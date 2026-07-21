"""Social tool builtins — speak only in PR8a.

Scope: thin speak wrapper that delegates glass delivery to SpeakTransport.
In scope: parse args, resolve transport/user, map SpeakDelivery → ToolResult.
Out of scope: wait_user, schedule_wake (PR8b).

ONLY speak (via transport) writes assistant glass rows — never bare content.
"""

from __future__ import annotations

from typing import Any

from elyra.speak import SpeakTransport
from elyra.tools.types import ToolContext, ToolResult


def speak(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Address a user via glass transport.

    Args (schema): ``text`` (required), optional ``user_id`` (defaults to
    ``ctx.user_id`` or ``operator``).

    Success → ``ok=True``, ``counts_as_speak=True``, payload with transport_ok.
    Transport failure → ``ok=False``, reason in payload (and error_reason).
    """
    raw_text = args.get("text")
    if not isinstance(raw_text, str):
        # Missing or wrong type — fail closed without writing glass.
        return ToolResult(
            ok=False,
            payload={
                "transport_ok": False,
                "reason": "missing_text",
                "user_id": _resolve_user_id(args, ctx),
            },
            error_reason="missing_text",
            counts_as_speak=False,
        )

    transport = _resolve_transport(ctx)
    user_id = _resolve_user_id(args, ctx)
    moment_id = ctx.moment_id or None

    delivery = transport.deliver(
        raw_text,
        user_id=user_id,
        moment_id=moment_id if moment_id else None,
    )

    if delivery.ok:
        return ToolResult(
            ok=True,
            payload=delivery.as_payload(),
            counts_as_speak=True,
        )

    reason = delivery.reason or "transport_failed"
    return ToolResult(
        ok=False,
        payload=delivery.as_payload(),
        error_reason=reason,
        counts_as_speak=False,
    )


def _resolve_transport(ctx: ToolContext) -> SpeakTransport:
    """Prefer injected ctx.speak; else construct from paths (or extras)."""
    if ctx.speak is not None:
        return ctx.speak
    extra = ctx.extras.get("speak")
    if isinstance(extra, SpeakTransport):
        return extra
    return SpeakTransport(ctx.paths)


def _resolve_user_id(args: dict[str, Any], ctx: ToolContext) -> str:
    """Args user_id wins when non-blank; else ctx.user_id; else operator."""
    raw = args.get("user_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if ctx.user_id is not None and str(ctx.user_id).strip():
        return str(ctx.user_id).strip()
    return "operator"
