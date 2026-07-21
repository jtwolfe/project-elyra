"""Sanitize provider completion text against channel-protocol marker floods.

Scope: pure strip/detect for Gemma/llama-style ``<|channel>…`` leaks and
related repetition tails. Applied at step completion ingress so dual-write
inner monologue and cycle monologue fuel never re-enter protocol markers.

Why this exists
---------------
Live + hermetic evidence (project host, Gemma / llama-server): under product
monologue knobs the model occasionally emits free-prose reasoning, then
falls into a pure ``<|channel>thought`` repetition loop until ``length`` stop.
That text was dual-written / re-fed via reasoning_content — a contamination
loop, not a prompt typo.

This module is **defense at the product boundary** (store + fuel), not a claim
that generation is cured. Generation risk remains stochastic.

Patterns observed in floods (not exhaustive):
- ``<|channel>thought`` (primary; no second pipe before ``>``)
- ``<channel|>`` interleave garbage
- pure-tag runs after good prose (prose prefix should be retained)

Out of scope: do-loop wire (later PR), sampling knobs, model weights,
or durable wipe of already-stored pollution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from elyra.llm.client import ChatCompletionResult

# Primary open tag: <|channel>NAME  (live floods use name "thought")
# Also tolerate <|channel|>NAME (pipe-before-close variant) and bare <channel|>.
_CHANNEL_MARKER_RE = re.compile(
    r"<\|channel\|>\w*"  # <|channel|>thought (pipe-before-close)
    r"|<\|channel>\w*"  # <|channel>thought  (observed primary live form)
    r"|</?channel\b[^>]*>",  # <channel|>, </channel>, <channel ...>
    re.IGNORECASE,
)

# After stripping tags, collapse blank runs left by tag-only lines.
_MULTI_BLANK_RE = re.compile(r"\n{3,}")

# Flood threshold: a few markers can be a stray trailer; many ⇒ loop.
CHANNEL_FLOOD_MIN_MARKERS = 5


@dataclass(frozen=True)
class ChannelHygieneReport:
    """Diagnostics for one sanitize pass (tests / anomaly glass)."""

    original_content_markers: int
    original_reasoning_markers: int
    content_changed: bool
    reasoning_changed: bool
    content_flood: bool
    reasoning_flood: bool

    @property
    def any_markers(self) -> bool:
        return (
            self.original_content_markers > 0 or self.original_reasoning_markers > 0
        )

    @property
    def any_flood(self) -> bool:
        return self.content_flood or self.reasoning_flood

    @property
    def any_change(self) -> bool:
        return self.content_changed or self.reasoning_changed


def channel_marker_count(text: str | None) -> int:
    """Count channel-protocol markers in text."""
    if not text:
        return 0
    return len(_CHANNEL_MARKER_RE.findall(text))


def is_channel_flood(
    text: str | None,
    *,
    min_markers: int = CHANNEL_FLOOD_MIN_MARKERS,
) -> bool:
    """True when marker count indicates a repetition flood (not a single trailer)."""
    return channel_marker_count(text) >= min_markers


def strip_channel_markers(text: str | None) -> str:
    """Remove channel-protocol markers; keep surrounding prose; collapse blanks.

    Empty / None → ``\"\"``. Pure-tag floods → ``\"\"`` (fail-closed for fuel/store).
    """
    if not text:
        return ""
    cleaned = _CHANNEL_MARKER_RE.sub("", text)
    cleaned = _MULTI_BLANK_RE.sub("\n\n", cleaned)
    # Tag-only lines often leave trailing whitespace per former tag line.
    lines = [ln.rstrip() for ln in cleaned.splitlines()]
    # Drop fully empty lines that were pure-tag rows (keep single blank max).
    compact: list[str] = []
    blank_run = 0
    for ln in lines:
        if not ln.strip():
            blank_run += 1
            if blank_run <= 1 and compact:
                compact.append("")
            continue
        blank_run = 0
        compact.append(ln)
    return "\n".join(compact).strip()


def sanitize_completion(
    result: ChatCompletionResult,
) -> tuple[ChatCompletionResult, ChannelHygieneReport]:
    """Strip markers from content and reasoning_content; report what changed.

    Greenfield has no error-sentinel results (HTTP failures raise). Does not
    mutate ``result`` in place — returns a ``dataclasses.replace`` copy when
    either field changes.
    """
    c_raw = result.content or ""
    r_raw = result.reasoning_content or ""
    c_n = channel_marker_count(c_raw)
    r_n = channel_marker_count(r_raw)
    c_clean = strip_channel_markers(c_raw) if c_n else c_raw
    r_clean = strip_channel_markers(r_raw) if r_n else r_raw

    report = ChannelHygieneReport(
        original_content_markers=c_n,
        original_reasoning_markers=r_n,
        content_changed=c_clean != c_raw,
        reasoning_changed=r_clean != r_raw,
        content_flood=is_channel_flood(c_raw),
        reasoning_flood=is_channel_flood(r_raw),
    )
    if not report.any_change:
        return result, report
    return (
        replace(result, content=c_clean, reasoning_content=r_clean),
        report,
    )
