"""Social tool builtins — speak, wait_user, schedule_wake.

Scope: thin wrappers that map tool args → ToolResult / presence ports.
In scope: speak transport, WaitArm + ends_moment, timer enqueue via TimerService.
Out of scope: do-loop ends_moment batch abort (PR11), phase machine, glass for wait.

ONLY speak (via transport) writes assistant glass rows — never bare content.
wait_user ends the moment (loop trusts ends_moment); later batch calls are
loop responsibility. schedule_wake records a durable timer only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from elyra.presence.timers import TimerService, parse_utc
from elyra.speak import SpeakTransport
from elyra.tools.types import ToolContext, ToolResult, WaitArm

# Fallback when ctx.settings is unset (matches WaitSettings.default_timeout_seconds).
_DEFAULT_WAIT_TIMEOUT_S = 120


def speak(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Address a user via glass transport.

    Args (schema): ``text`` (required), optional ``user_id`` (defaults to
    ``ctx.user_id`` or ``operator``).

    Success → ``ok=True``, ``counts_as_speak=True``, payload with transport_ok.
    Transport failure → ``ok=False``, reason in payload (and error_reason).
    """
    raw_text = args.get("text")
    if raw_text is None and "text" not in args:
        # Key absent — fail closed without writing glass.
        return _text_error("missing_text", args, ctx)
    if not isinstance(raw_text, str):
        # Key present but not a string (incl. explicit null) — invalid_text.
        return _text_error("invalid_text", args, ctx)

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


def wait_user(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """End the moment and arm a durable user wait (multi-choice + free text).

    Args (schema):
      - ``prompt`` (required str) — question / wait prompt shown to the user
      - ``choices`` (optional list[str]) — multi-choice options; empty = free text
      - ``timeout_seconds`` (optional int) — default from settings.wait (120)
      - ``user_id`` (optional) — defaults to ctx.user_id / operator

    Success → ``ok=True``, ``ends_moment=True``, ``stop_reason="wait"``,
    ``arm_wait=WaitArm(...)``, ``counts_as_speak=False``.

    When ``ctx.timers`` (or extras) is present, also persists the wait via
    ``TimerService.arm_wait`` so presence can fire ``wait_timeout`` on expiry.
    Loop still owns batch abort after ends_moment (PR11).
    """
    prompt = args.get("prompt")
    if prompt is None and "prompt" not in args:
        return ToolResult(
            ok=False,
            payload={"reason": "missing_prompt"},
            error_reason="missing_prompt",
            counts_as_speak=False,
        )
    if not isinstance(prompt, str):
        return ToolResult(
            ok=False,
            payload={"reason": "invalid_prompt"},
            error_reason="invalid_prompt",
            counts_as_speak=False,
        )
    prompt = prompt.strip()
    if not prompt:
        return ToolResult(
            ok=False,
            payload={"reason": "empty_prompt"},
            error_reason="empty_prompt",
            counts_as_speak=False,
        )

    choices, choices_err = _parse_choices(args.get("choices"))
    if choices_err is not None:
        return ToolResult(
            ok=False,
            payload={"reason": choices_err},
            error_reason=choices_err,
            counts_as_speak=False,
        )

    timeout_seconds, timeout_err = _parse_timeout_seconds(args, ctx)
    if timeout_err is not None:
        return ToolResult(
            ok=False,
            payload={"reason": timeout_err},
            error_reason=timeout_err,
            counts_as_speak=False,
        )

    user_id = _resolve_user_id(args, ctx)
    wait_id = str(uuid.uuid4())
    arm = WaitArm(
        wait_id=wait_id,
        timeout_seconds=timeout_seconds,
        prompt=prompt,
        choices=choices,
        user_id=user_id,
    )

    # Durable snapshot for presence timers when host injected TimerService.
    timers = _resolve_timers(ctx)
    if timers is not None:
        try:
            timers.arm_wait(
                wait_id=arm.wait_id,
                prompt=arm.prompt,
                choices=list(arm.choices),
                user_id=arm.user_id,
                moment_id=ctx.moment_id or "",
                timeout=float(arm.timeout_seconds),
            )
        except (ValueError, TypeError, OSError) as exc:
            return ToolResult(
                ok=False,
                payload={
                    "reason": f"arm_wait_failed:{type(exc).__name__}",
                    "detail": str(exc),
                },
                error_reason=f"arm_wait_failed:{type(exc).__name__}",
                counts_as_speak=False,
            )

    payload: dict[str, Any] = {
        "wait_id": arm.wait_id,
        "timeout_seconds": arm.timeout_seconds,
        "prompt": arm.prompt,
        "choices": list(arm.choices),
        "user_id": arm.user_id,
        "moment_id": ctx.moment_id or "",
        "armed": timers is not None,
    }
    return ToolResult(
        ok=True,
        payload=payload,
        ends_moment=True,
        stop_reason="wait",
        arm_wait=arm,
        counts_as_speak=False,
    )


def schedule_wake(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Record a durable timer that will enqueue a ``timer`` wake when due.

    Args (schema):
      - ``wake_at`` (optional ISO UTC) — absolute fire time
      - ``delay_seconds`` (optional number) — relative from now (alternative to wake_at)
      - ``reason`` (optional str)
      - ``goal_id`` / ``task_id`` (optional str)

    Exactly one of ``wake_at`` or ``delay_seconds`` must be provided.
    Requires ``ctx.timers`` (or extras timers) — does not invent a WakeQueue.
    Does **not** end the moment (``ends_moment=False``).
    """
    timers = _resolve_timers(ctx)
    if timers is None:
        return ToolResult(
            ok=False,
            payload={"reason": "timers_unavailable"},
            error_reason="timers_unavailable",
        )

    wake_at, when_err = _resolve_wake_at(args)
    if when_err is not None:
        return ToolResult(
            ok=False,
            payload={"reason": when_err},
            error_reason=when_err,
        )

    reason = args.get("reason")
    if reason is None:
        reason_s = ""
    elif isinstance(reason, str):
        reason_s = reason
    else:
        return ToolResult(
            ok=False,
            payload={"reason": "invalid_reason"},
            error_reason="invalid_reason",
        )

    goal_id = _optional_str_id(args, "goal_id")
    if goal_id is False:
        return ToolResult(
            ok=False,
            payload={"reason": "invalid_goal_id"},
            error_reason="invalid_goal_id",
        )
    task_id = _optional_str_id(args, "task_id")
    if task_id is False:
        return ToolResult(
            ok=False,
            payload={"reason": "invalid_task_id"},
            error_reason="invalid_task_id",
        )

    try:
        timer = timers.schedule_timer(
            wake_at,
            reason=reason_s,
            goal_id=goal_id if isinstance(goal_id, str) else None,
            task_id=task_id if isinstance(task_id, str) else None,
        )
    except (ValueError, TypeError, OSError) as exc:
        return ToolResult(
            ok=False,
            payload={
                "reason": f"schedule_failed:{type(exc).__name__}",
                "detail": str(exc),
            },
            error_reason=f"schedule_failed:{type(exc).__name__}",
        )

    return ToolResult(
        ok=True,
        payload={
            "timer_id": timer.id,
            "wake_at": timer.wake_at,
            "reason": timer.reason,
            "goal_id": timer.goal_id,
            "task_id": timer.task_id,
            "status": timer.status,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_error(
    reason: str, args: dict[str, Any], ctx: ToolContext
) -> ToolResult:
    return ToolResult(
        ok=False,
        payload={
            "transport_ok": False,
            "reason": reason,
            "user_id": _resolve_user_id(args, ctx),
        },
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


def _resolve_timers(ctx: ToolContext) -> TimerService | None:
    """Prefer injected ctx.timers; else extras['timers'] when TimerService."""
    if ctx.timers is not None:
        return ctx.timers
    extra = ctx.extras.get("timers")
    if isinstance(extra, TimerService):
        return extra
    return None


def _resolve_user_id(args: dict[str, Any], ctx: ToolContext) -> str:
    """Args user_id wins when non-blank; else ctx.user_id; else operator."""
    raw = args.get("user_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if ctx.user_id is not None and str(ctx.user_id).strip():
        return str(ctx.user_id).strip()
    return "operator"


def _parse_choices(raw: Any) -> tuple[list[str], str | None]:
    """Return (choices, error_reason). Absent / null → empty list."""
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return [], "invalid_choices"
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            return [], "invalid_choices"
        text = item.strip()
        if text:
            out.append(text)
    return out, None


def _parse_timeout_seconds(
    args: dict[str, Any], ctx: ToolContext
) -> tuple[int, str | None]:
    """Resolve timeout_seconds from args or settings (default 120)."""
    if "timeout_seconds" not in args or args.get("timeout_seconds") is None:
        return _default_timeout_seconds(ctx), None
    raw = args["timeout_seconds"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0, "invalid_timeout"
    if isinstance(raw, float) and not raw.is_integer():
        return 0, "invalid_timeout"
    value = int(raw)
    if value < 0:
        return 0, "invalid_timeout"
    return value, None


def _default_timeout_seconds(ctx: ToolContext) -> int:
    if ctx.settings is not None:
        try:
            return int(ctx.settings.wait.default_timeout_seconds)
        except (AttributeError, TypeError, ValueError):
            pass
    return _DEFAULT_WAIT_TIMEOUT_S


def _resolve_wake_at(args: dict[str, Any]) -> tuple[str | None, str | None]:
    """Resolve absolute wake_at ISO from wake_at or delay_seconds.

    Returns (wake_at_iso, error_reason). Exactly one of the two args required.
    """
    has_wake = "wake_at" in args and args.get("wake_at") is not None
    has_delay = "delay_seconds" in args and args.get("delay_seconds") is not None

    if has_wake and has_delay:
        return None, "ambiguous_when"
    if not has_wake and not has_delay:
        return None, "missing_when"

    if has_wake:
        raw = args["wake_at"]
        if not isinstance(raw, str) or not raw.strip():
            return None, "invalid_wake_at"
        # Validate parseable ISO (TimerService also validates).
        try:
            parse_utc(raw.strip())
        except (ValueError, TypeError):
            return None, "invalid_wake_at"
        return raw.strip(), None

    delay = args["delay_seconds"]
    if isinstance(delay, bool) or not isinstance(delay, (int, float)):
        return None, "invalid_delay_seconds"
    if float(delay) < 0:
        return None, "invalid_delay_seconds"
    wake_dt = datetime.now(UTC) + timedelta(seconds=float(delay))
    wake_at = wake_dt.isoformat().replace("+00:00", "Z")
    return wake_at, None


def _optional_str_id(args: dict[str, Any], key: str) -> str | None | bool:
    """Return optional non-blank str id, None if absent, False if invalid type."""
    if key not in args or args.get(key) is None:
        return None
    raw = args[key]
    if not isinstance(raw, str):
        return False
    text = raw.strip()
    if not text:
        return None
    return text
