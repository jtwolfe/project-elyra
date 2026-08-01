"""Headless Messages-style usage → TokenUsage / UsageMeter adapter (KD10).

Scope: map grok --output-format json usage fields (input_tokens, output_tokens,
cache_*, total_tokens, reasoning_tokens, usage_is_incomplete) into TokenUsage
compatible with parse_token_usage / meter.record; optional meter pre-check hook.
In scope: pure field adapter, record_instrument_usage, usage_hard_stop pre-check.
Out of scope: subprocess, jobs reaper, OAuth, presence.

Headless JSON uses Messages-style buckets, **not** OpenAI-only names.
Naive ``parse_token_usage(data["usage"])`` returns None for real payloads —
this adapter is required so instrument spend is not silently under-counted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping

from elyra.llm.usage import TokenUsage, UsageMeter, parse_token_usage

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UsageBridgeResult:
    """Outcome of adapting + optionally recording instrument usage."""

    usage: TokenUsage | None
    usage_recorded: bool
    usage_incomplete: bool
    raw_present: bool
    # Model-visible usage dict (no secrets).
    payload_usage: dict[str, Any]


def _as_nonneg_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, n)


def extract_usage_dict(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Pull a usage object from a headless JSON root (or nested result).

    Accepts:
    - ``{"usage": {...}, "usage_is_incomplete": bool}``
    - bare usage dict with input_tokens / prompt_tokens keys
    """
    if not isinstance(payload, dict):
        return None
    if "usage" in payload and isinstance(payload.get("usage"), dict):
        return dict(payload["usage"])
    # Bare usage-shaped dict.
    keys = (
        "input_tokens",
        "output_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "cached_tokens",
        "reasoning_tokens",
    )
    if any(k in payload for k in keys):
        return dict(payload)
    return None


def messages_usage_to_token_usage(
    raw: Mapping[str, Any] | None,
) -> TokenUsage | None:
    """Map Messages-style (or OpenAI-style) usage dict → TokenUsage.

    Adapter table (design):
    - input_tokens → prompt_tokens (uncached)
    - output_tokens → completion_tokens
    - cache_read_input_tokens (+ creation if present) → cached_tokens
    - total_tokens → total_tokens / billable when > 0
    - reasoning_tokens → reasoning_tokens

    Returns None when raw is missing/unusable (do **not** invent zeros as
    a complete usage record).
    """
    if not isinstance(raw, dict) or not raw:
        return None

    # Prefer OpenAI names when already present (pass-through via parse_token_usage).
    openaiish = parse_token_usage(raw)
    has_messages = any(
        k in raw
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )
    if openaiish is not None and not has_messages:
        return openaiish

    if not has_messages and openaiish is None:
        # Unknown shape.
        if any(k in raw for k in ("total_tokens", "reasoning_tokens", "cached_tokens")):
            # Partial OpenAI-ish without classic trio — still try parse.
            return openaiish
        return None

    prompt = _as_nonneg_int(raw.get("input_tokens", raw.get("prompt_tokens", 0)))
    completion = _as_nonneg_int(
        raw.get("output_tokens", raw.get("completion_tokens", 0))
    )
    total = _as_nonneg_int(raw.get("total_tokens", 0))

    cache_read = _as_nonneg_int(raw.get("cache_read_input_tokens", 0))
    cache_create = _as_nonneg_int(raw.get("cache_creation_input_tokens", 0))
    cached = cache_read + cache_create
    if cached <= 0:
        cached = _as_nonneg_int(raw.get("cached_tokens", 0))
        # Nested OpenAI-style details.
        prompt_details = raw.get("prompt_tokens_details")
        if isinstance(prompt_details, dict) and "cached_tokens" in prompt_details:
            cached = max(cached, _as_nonneg_int(prompt_details.get("cached_tokens")))

    reasoning = _as_nonneg_int(raw.get("reasoning_tokens", 0))
    details = raw.get("completion_tokens_details")
    if isinstance(details, dict) and "reasoning_tokens" in details:
        reasoning = max(reasoning, _as_nonneg_int(details.get("reasoning_tokens")))

    # If everything is zero and no recognized keys had values, treat as missing.
    if prompt == 0 and completion == 0 and total == 0 and cached == 0 and reasoning == 0:
        # Still accept explicit zeros only when at least one key was present
        # with a numeric zero (caller wants recorded empty). Prefer None when
        # keys exist but are all null/non-numeric — already zeroed above.
        has_numeric = any(
            isinstance(raw.get(k), (int, float)) and not isinstance(raw.get(k), bool)
            for k in (
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "prompt_tokens",
                "completion_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            )
        )
        if not has_numeric:
            return None

    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        reasoning_tokens=reasoning,
        cached_tokens=cached,
    )


def usage_is_incomplete_flag(payload: Mapping[str, Any] | None) -> bool:
    """True when headless reports ``usage_is_incomplete: true`` (root or usage)."""
    if not isinstance(payload, dict):
        return False
    if payload.get("usage_is_incomplete") is True:
        return True
    usage = payload.get("usage")
    if isinstance(usage, dict) and usage.get("usage_is_incomplete") is True:
        return True
    return False


def adapt_headless_usage(
    payload: Mapping[str, Any] | None,
) -> UsageBridgeResult:
    """Full adapt of a headless JSON object → UsageBridgeResult (no meter I/O)."""
    incomplete = usage_is_incomplete_flag(payload)
    usage_raw = extract_usage_dict(payload)
    raw_present = usage_raw is not None
    usage = messages_usage_to_token_usage(usage_raw) if raw_present else None

    if usage is None:
        payload_usage: dict[str, Any] = {
            "total_tokens": 0,
            "recorded": False,
        }
        if incomplete:
            payload_usage["usage_incomplete"] = True
        return UsageBridgeResult(
            usage=None,
            usage_recorded=False,
            usage_incomplete=incomplete,
            raw_present=raw_present,
            payload_usage=payload_usage,
        )

    payload_usage = {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "cached_tokens": usage.cached_tokens,
        "billable_tokens": usage.billable_tokens,
        "recorded": False,  # flipped by record_instrument_usage
    }
    if incomplete:
        payload_usage["usage_incomplete"] = True

    return UsageBridgeResult(
        usage=usage,
        usage_recorded=False,
        usage_incomplete=incomplete,
        raw_present=True,
        payload_usage=payload_usage,
    )


def record_instrument_usage(
    meter: UsageMeter | None,
    payload: Mapping[str, Any] | None,
    *,
    estimated_if_missing: int = 0,
) -> UsageBridgeResult:
    """Adapt headless usage and record into meter when tokens are known.

    - missing usage → usage_recorded=false; do **not** invent
    - usage_is_incomplete → still record known tokens; flag payload
    - meter is None → adapt only (no record)
    """
    result = adapt_headless_usage(payload)
    if result.usage is None:
        # Do not invent zeros as complete; optional estimated only when caller
        # explicitly wants a missing-usage estimate (default 0 = no-op record).
        if meter is not None and estimated_if_missing > 0:
            try:
                meter.record(None, estimated_if_missing=estimated_if_missing)
            except Exception as exc:  # noqa: BLE001
                logger.warning("usage_bridge record estimated failed: %s", exc)
        return result

    if meter is None:
        return result

    try:
        meter.record(result.usage)
        payload_usage = dict(result.payload_usage)
        payload_usage["recorded"] = True
        return UsageBridgeResult(
            usage=result.usage,
            usage_recorded=True,
            usage_incomplete=result.usage_incomplete,
            raw_present=result.raw_present,
            payload_usage=payload_usage,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("usage_bridge meter.record failed: %s", exc)
        return result


def meter_allows_call(meter: UsageMeter | None) -> bool:
    """Pre-check: True when meter is None/disabled or can_call().

    On refuse, callers should return ``error_reason=usage_hard_stop``.
    """
    if meter is None:
        return True
    try:
        return bool(meter.can_call())
    except Exception:  # noqa: BLE001
        # Fail open on meter errors would under-enforce; fail closed for safety.
        logger.warning("usage_bridge can_call raised; treating as hard-stop", exc_info=True)
        return False


__all__ = [
    "UsageBridgeResult",
    "adapt_headless_usage",
    "extract_usage_dict",
    "messages_usage_to_token_usage",
    "meter_allows_call",
    "record_instrument_usage",
    "usage_is_incomplete_flag",
]
