"""Tool call/result contracts shared by registry and do-loop.

Scope: ToolCall, ToolResult, WaitArm, ToolContext shapes.
In scope: frozen result flags (ends_moment, counts_as_speak), minimal ctx.
Out of scope: do-loop orchestration, promote/verify.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from elyra.config import ElyraPaths
    from elyra.goals import GoalsStore
    from elyra.presence.timers import TimerService
    from elyra.sandbox import Sandbox
    from elyra.settings import Settings
    from elyra.speak import SpeakTransport
    from elyra.tools.registry import ToolRegistry


@dataclass(frozen=True)
class ToolCall:
    """One model-requested tool invocation (arguments already parsed)."""

    id: str
    name: str
    arguments: dict[str, Any]  # {} if parse failed (flagged by caller)


@dataclass(frozen=True)
class WaitArm:
    """Durable wait request attached to a ToolResult that ends the moment."""

    wait_id: str
    timeout_seconds: int
    prompt: str
    choices: list[str]
    user_id: str


@dataclass(frozen=True)
class ToolResult:
    """Outcome of registry.execute — model-visible payload + loop control flags.

    Loop trusts ends_moment / counts_as_speak from execute only (not tool name).
    """

    ok: bool
    payload: dict[str, Any]
    error_reason: str | None = None
    # Loop control (host builtins that end the moment only):
    ends_moment: bool = False
    stop_reason: str | None = None  # wait | blocked | policy
    arm_wait: WaitArm | None = None
    # Social tracking — speak builtin only:
    counts_as_speak: bool = False


@dataclass
class ToolContext:
    """Per-invocation host context for tool handlers.

    Full ports (goals, skills, enqueue) land as later packages attach;
    handlers that need them read from attributes when present.
    """

    paths: ElyraPaths
    sandbox: Sandbox | None = None
    settings: Settings | None = None
    moment_id: str = ""
    user_id: str | None = None
    # Social address from wake.payload only (never client session on pure work).
    conversation_id: str | None = None
    registry: ToolRegistry | None = None
    # Glass delivery — speak builtin uses this; loop injects shared instance.
    speak: SpeakTransport | None = None
    # Timer/wait store — schedule_wake + wait_user durable arming.
    timers: TimerService | None = None
    # Ledger port (PR8c); host injects GoalsStore for update_task / update_goal.
    goals: GoalsStore | None = None
    # Mutable bags / ports filled by presence/do-loop wiring (later PRs):
    skills_used: list[str] = field(default_factory=list)
    mark_spoke: Callable[[], None] | None = None
    mark_task_changed: Callable[[], None] | None = None
    enqueue_wake: Callable[..., str] | None = None
    cancel_wait: Callable[[str], None] | None = None
    # Extension bag for ports not yet typed (skills loader, …)
    extras: dict[str, Any] = field(default_factory=dict)
