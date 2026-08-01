"""Redact known secret values from grok_build result payloads and logs.

Scope: thin wrappers over elyra.secrets.inject redact helpers for instrument
results; merge call-local access tokens into known_values.
In scope: string/payload scrub, known_values union helper.
Out of scope: OAuth refresh, store I/O, subprocess, registry secret_env.

Reuses inject helpers without cycles (inject does not import instrument).
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from elyra.secrets.inject import (
    redact_payload as _redact_payload,
    redact_string as _redact_string,
    redact_tool_result_payload as _redact_tool_result_payload,
)
from elyra.secrets.policy import REDACT_PLACEHOLDER

# Re-export placeholder for callers that only touch instrument.redact.
PLACEHOLDER = REDACT_PLACEHOLDER


def merge_known_values(
    *groups: Iterable[str] | None,
) -> list[str]:
    """Union secret strings for redaction (longest-first applied by inject).

    Drop empties/duplicates while preserving a stable longest-first sort via
    inject's own sort. Order of groups does not matter.
    """
    seen: set[str] = set()
    out: list[str] = []
    for group in groups:
        if not group:
            continue
        for v in group:
            if not isinstance(v, str) or not v:
                continue
            if v not in seen:
                seen.add(v)
                out.append(v)
    return out


def redact_string(text: str, known_values: Sequence[str] | None) -> str:
    """Replace known secret substrings in text."""
    return _redact_string(text, list(known_values) if known_values else None)


def redact_payload(payload: Any, known_values: Sequence[str] | None) -> Any:
    """Recursively redact known secret values from a JSON-like payload."""
    return _redact_payload(payload, list(known_values) if known_values else None)


def redact_result_payload(
    payload: dict[str, Any] | None,
    known_values: Sequence[str] | None,
) -> dict[str, Any]:
    """Redact a ToolResult.payload dict (always returns a dict)."""
    return _redact_tool_result_payload(
        payload, list(known_values) if known_values else None
    )


def redact_instrument_result(
    result: dict[str, Any],
    known_values: Sequence[str] | None,
    *,
    access_tokens: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Redact a ToolResult-shaped dict (ok/payload/error_reason).

    Call-local access tokens are merged into known_values (KD auth law #4).
    """
    values = merge_known_values(known_values, access_tokens)
    if not isinstance(result, dict):
        return {}
    out = dict(result)
    if "payload" in out:
        out["payload"] = redact_result_payload(
            out.get("payload") if isinstance(out.get("payload"), dict) else {},
            values,
        )
    if isinstance(out.get("error_reason"), str) and values:
        out["error_reason"] = redact_string(out["error_reason"], values)
    # Also scrub top-level string fields if any leak
    for key in ("summary", "hint"):
        if isinstance(out.get(key), str):
            out[key] = redact_string(out[key], values)
    return out


__all__ = [
    "PLACEHOLDER",
    "merge_known_values",
    "redact_instrument_result",
    "redact_payload",
    "redact_result_payload",
    "redact_string",
]
