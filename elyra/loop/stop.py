"""Stop-reason decision helpers for the do-loop host.

Scope: map host / tool conditions to the Stretch 1 stop vocabulary.
In scope: pure helpers matching the stop decision table; re-export STOP_REASONS.
Out of scope: running the loop, tool registry, moment persistence.
"""

from __future__ import annotations

from typing import Literal

# Single source of truth lives on moment types (persistence edge).
from elyra.moment.types import STOP_REASONS, StopReason

# Named constants for call sites (mirrors STOP_REASONS / design table).
STOP_NO_TOOLS: Literal["no_tools"] = "no_tools"
STOP_WAIT: Literal["wait"] = "wait"
STOP_BLOCKED: Literal["blocked"] = "blocked"
STOP_POLICY: Literal["policy"] = "policy"
STOP_TIME_CONTINUE_DECLINED: Literal["time_continue_declined"] = (
    "time_continue_declined"
)
STOP_WALL_CLOCK: Literal["wall_clock"] = "wall_clock"
STOP_INTERRUPTED: Literal["interrupted"] = "interrupted"
STOP_ERROR: Literal["error"] = "error"
STOP_MAX_HOPS: Literal["max_hops"] = "max_hops"


def is_valid_stop_reason(value: str) -> bool:
    """True if ``value`` is an allowed moment stop_reason."""
    return isinstance(value, str) and value in STOP_REASONS


def stop_for_no_tools() -> StopReason:
    """Model returned no tool_calls (after optional no-speak nudge)."""
    return STOP_NO_TOOLS


def stop_for_wait() -> StopReason:
    """``wait_user`` (or control tool) armed wait and ends the moment."""
    return STOP_WAIT


def stop_for_blocked() -> StopReason:
    """Control tool requested blocked stop (ledger blocked ≠ this by default)."""
    return STOP_BLOCKED


def stop_for_policy() -> StopReason:
    """Host policy abort (disallowed tool, draft call, fatal sandbox escape)."""
    return STOP_POLICY


def stop_for_time_continue_declined() -> StopReason:
    """Continue max injects exhausted while still idle."""
    return STOP_TIME_CONTINUE_DECLINED


def stop_for_wall_clock() -> StopReason:
    """Moment absolute wall-clock budget exceeded."""
    return STOP_WALL_CLOCK


def stop_for_interrupted() -> StopReason:
    """Process restart / open moment cleanup."""
    return STOP_INTERRUPTED


def stop_for_error() -> StopReason:
    """Uncaught exception in the do-loop."""
    return STOP_ERROR


def stop_for_max_hops() -> StopReason:
    """``hop >= max_tool_hops`` thrash backstop."""
    return STOP_MAX_HOPS


def stop_from_tool_result(
    *,
    ends_moment: bool,
    stop_reason: str | None,
) -> StopReason | None:
    """Map a control ToolResult to a stop reason, or None if loop continues.

    Trusts only ``ends_moment`` + ``stop_reason`` from execute() (design).
    Invalid / missing stop_reason with ends_moment falls back to ``policy``.
    """
    if not ends_moment:
        return None
    if stop_reason and is_valid_stop_reason(stop_reason):
        return stop_reason  # type: ignore[return-value]
    return STOP_POLICY


def resolve_host_precheck_stop(
    *,
    wall_clock_exceeded: bool = False,
    hop: int = 0,
    max_tool_hops: int = 200,
    time_continue_declined: bool = False,
) -> StopReason | None:
    """Host pre-model checks in multi-hop order.

    Priority matches the do-loop algorithm:
    1. wall_clock
    2. max_hops
    3. time_continue_declined

    Returns a stop_reason string or None if the loop may proceed
    (caller may still inject continue before the model call).
    """
    if wall_clock_exceeded:
        return STOP_WALL_CLOCK
    if hop >= max_tool_hops:
        return STOP_MAX_HOPS
    if time_continue_declined:
        return STOP_TIME_CONTINUE_DECLINED
    return None


__all__ = [
    "STOP_REASONS",
    "StopReason",
    "STOP_NO_TOOLS",
    "STOP_WAIT",
    "STOP_BLOCKED",
    "STOP_POLICY",
    "STOP_TIME_CONTINUE_DECLINED",
    "STOP_WALL_CLOCK",
    "STOP_INTERRUPTED",
    "STOP_ERROR",
    "STOP_MAX_HOPS",
    "is_valid_stop_reason",
    "stop_for_no_tools",
    "stop_for_wait",
    "stop_for_blocked",
    "stop_for_policy",
    "stop_for_time_continue_declined",
    "stop_for_wall_clock",
    "stop_for_interrupted",
    "stop_for_error",
    "stop_for_max_hops",
    "stop_from_tool_result",
    "resolve_host_precheck_stop",
]
