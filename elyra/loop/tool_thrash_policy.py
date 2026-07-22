"""Tool thrash policy (pure decisions).

Scope: fingerprints, streak updates, thrash HOST decision, HOST builder.
Peer to skill_commit_policy / continuous_policy. No I/O. No glass.

Phase B only: no lesson ceremony, no skip-identical re-exec.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

# Defaults (thin lattice; constants over flag forest).
FAIL_STREAK_THRESHOLD = 3
OK_STREAK_THRESHOLD = 5
MAX_THRASH_HOSTS = 1
THRASH_TRIED_CAP = 8

# Large string values hashed above this length (fingerprint only).
_LARGE_STR_THRESHOLD = 64

# Normative thrash HOST — must NOT echo WORK_CONTINUE_HOST wording.
THRASH_HOST = (
    "HOST: tool thrash — repeated {tool_name} ×{streak} with same args "
    "({detail}). Do not repeat that call. Change tool or arguments, "
    "or stop with free-text (no tools). Rest means load_skill(\"rest\") "
    "or honest no-tool stop — rest is not a tool name."
)

_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ThrashUpdate:
    """Result of update_thrash_streak — next fingerprint streak + metadata."""

    fingerprint: str
    streak: int
    repeated: bool  # streak >= 2
    ok: bool
    error_reason: str | None
    tool_name: str


@dataclass(frozen=True)
class ThrashHostDecision:
    """Result of should_inject_thrash_host."""

    inject: bool
    reason: str  # injected | below_threshold | budget | no_tool | ...
    kind: str  # thrash_fail_streak | thrash_speak_repeat | thrash_repeat | ""


def _normalize_tool_name(tool_name: str) -> str:
    return (tool_name or "").strip().casefold()


def _normalize_pathish(value: str) -> str:
    """Collapse path separators / whitespace for stable fingerprints."""
    s = value.strip().replace("\\", "/")
    while "//" in s:
        s = s.replace("//", "/")
    return s


def _hash16(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _canonicalize_value(key: str, value: Any) -> Any:
    """Normalize one arg value for fingerprint JSON."""
    if isinstance(value, Mapping):
        # install_tool_draft ``files`` map: path → content body → len + hash
        if key == "files":
            out: dict[str, Any] = {}
            for path_key in sorted(value.keys(), key=lambda k: str(k)):
                body = value[path_key]
                if isinstance(body, str):
                    out[str(path_key)] = {
                        "len": len(body),
                        "sha256_16": _hash16(body),
                    }
                else:
                    out[str(path_key)] = _canonicalize_value("", body)
            return out
        return {
            str(k): _canonicalize_value(str(k), value[k])
            for k in sorted(value.keys(), key=lambda k: str(k))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize_value(key, v) for v in value]
    if isinstance(value, str):
        # Speak / free text: collapse whitespace so identical greets streak.
        if key in {"text", "content", "message"}:
            return _WS_RE.sub(" ", value.strip())
        # Path-ish keys: normalize separators.
        if key in {"path", "file", "dir", "directory", "target", "src", "dst"}:
            return _normalize_pathish(value)
        if len(value) > _LARGE_STR_THRESHOLD:
            return {"len": len(value), "sha256_16": _hash16(value)}
        return value
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    # Fallback: stable string form
    return str(value)


def canonical_args(args: Mapping[str, Any] | None) -> str:
    """Stable JSON: sort keys, normalize paths, redact/truncate large strings.

    File bodies under ``files`` fingerprint as
    ``{path: {"len": N, "sha256_16": "..."}}`` so content edits break the streak
    without hashing megabytes into the message itself.
    """
    if not args:
        return "{}"
    canon = {
        str(k): _canonicalize_value(str(k), args[k])
        for k in sorted(args.keys(), key=lambda k: str(k))
    }
    return json.dumps(canon, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def tool_fingerprint(tool_name: str, args: Mapping[str, Any] | None) -> str:
    """``tool_name|canonical_args`` with casefold/strip on the name."""
    name = _normalize_tool_name(tool_name)
    return f"{name}|{canonical_args(args)}"


def update_thrash_streak(
    *,
    prev_fp: str | None,
    prev_streak: int,
    tool_name: str,
    args: Mapping[str, Any] | None,
    ok: bool,
    error_reason: str | None,
) -> ThrashUpdate:
    """Compute next fingerprint streak and echo ok/error for state wiring.

    Same fingerprint → streak += 1; different fingerprint → streak = 1.
    ``repeated`` is True when streak >= 2.
    """
    name = _normalize_tool_name(tool_name) or (tool_name or "").strip()
    fp = tool_fingerprint(tool_name, args)
    if prev_fp is not None and fp == prev_fp:
        streak = max(int(prev_streak), 0) + 1
    else:
        streak = 1
    return ThrashUpdate(
        fingerprint=fp,
        streak=streak,
        repeated=streak >= 2,
        ok=bool(ok),
        error_reason=error_reason,
        tool_name=name or (tool_name or ""),
    )


def should_inject_thrash_host(
    *,
    streak: int,
    last_ok: bool | None,
    thrash_host_sent: int,
    tool_name: str | None,
    max_thrash_hosts: int = MAX_THRASH_HOSTS,
    fail_streak_threshold: int = FAIL_STREAK_THRESHOLD,
    ok_streak_threshold: int = OK_STREAK_THRESHOLD,
) -> ThrashHostDecision:
    """Decide whether to inject a post-batch thrash HOST (end-of-batch last-fp).

    ``tool_name`` None or blank → inject=False, reason=no_tool.
    Builders require non-empty tool_name when inject=True.
    """
    if tool_name is None or not str(tool_name).strip():
        return ThrashHostDecision(inject=False, reason="no_tool", kind="")
    if max_thrash_hosts <= 0 or thrash_host_sent >= max_thrash_hosts:
        return ThrashHostDecision(inject=False, reason="budget", kind="")
    if last_ok is False:
        if streak >= fail_streak_threshold:
            return ThrashHostDecision(
                inject=True, reason="injected", kind="thrash_fail_streak"
            )
        return ThrashHostDecision(inject=False, reason="below_threshold", kind="")
    if last_ok is True:
        if streak >= ok_streak_threshold:
            name = _normalize_tool_name(tool_name)
            kind = "thrash_speak_repeat" if name == "speak" else "thrash_repeat"
            return ThrashHostDecision(inject=True, reason="injected", kind=kind)
        return ThrashHostDecision(inject=False, reason="below_threshold", kind="")
    # last_ok is None — no tool result yet
    return ThrashHostDecision(inject=False, reason="below_threshold", kind="")


def thrash_host_message(*, tool_name: str, streak: int, detail: str) -> str:
    """HOST thrash line injected into the in-turn chain (obs / user).

    ``tool_name`` must be non-empty (builder invariant when inject=True).
    """
    return THRASH_HOST.format(
        tool_name=tool_name,
        streak=int(streak),
        detail=detail if detail else "unknown",
    )


def thrash_detail(*, last_ok: bool | None, last_error: str | None) -> str:
    """Short detail fragment for thrash HOST ({summary_error_or_ok})."""
    if last_ok is True:
        return "ok"
    if last_error:
        return str(last_error)
    if last_ok is False:
        return "error"
    return "unknown"


__all__ = [
    "FAIL_STREAK_THRESHOLD",
    "MAX_THRASH_HOSTS",
    "OK_STREAK_THRESHOLD",
    "THRASH_HOST",
    "THRASH_TRIED_CAP",
    "ThrashHostDecision",
    "ThrashUpdate",
    "canonical_args",
    "should_inject_thrash_host",
    "thrash_detail",
    "thrash_host_message",
    "tool_fingerprint",
    "update_thrash_streak",
]
