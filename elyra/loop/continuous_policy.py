"""Continuous work policy (pure decisions).

Scope: gates for in-moment work-continue HOST nudge and outer moment_continue
re-wake; HOST string builders; decision dataclasses; runtime state shape.
In scope: ContinuousSettings knobs, flood thrash formula, progress definitions
(tools_ran = non-speak only; spoke alone never qualifies); ContinuousRuntimeState
load/save helpers used by PresenceWorker finalize and toggle.
Out of scope: wake enqueue I/O, do-loop scheduling, time-idle continue_policy
(see continue_policy.py — different concept). Finalize I/O lives in presence.worker.

Do not put continuous gates in continue_policy.py (name collision with 8-min idle).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyra.settings import ContinuousSettings, default_settings

logger = logging.getLogger(__name__)

# In-moment work-continue HOST (chain-only; never SpeakTransport).
WORK_CONTINUE_HOST = (
    "HOST: work still open — call tools to continue "
    "(load_skill / ledger / sandbox), speak if the user needs an update, "
    "or stop if truly done."
)

# Outer stop_reason allowlist (v1 closed). Deny wait/error/wall_clock/blocked/…
MOMENT_CONTINUE_STOP_ALLOWLIST = frozenset(
    {"no_tools", "time_continue_declined", "max_hops"}
)

# Social wakes for pure-social skip (gate 8) and in-moment social path.
SOCIAL_WAKE_KINDS = frozenset({"user_message", "wait_reply"})

# Non-social work-context kinds for in-moment nudge (design C).
NON_SOCIAL_WORK_KINDS = frozenset({"task_ready", "moment_continue", "timer"})

CONTINUOUS_RUNTIME_REL = Path("runtime") / "continuous.json"


@dataclass(frozen=True)
class InMomentNudgeDecision:
    """Result of should_in_moment_work_nudge."""

    inject: bool
    reason: str  # injected | disabled | budget | not_workish | social_nudge_first | need_spoke | flood | …


@dataclass(frozen=True)
class MomentContinueDecision:
    """Result of should_enqueue_moment_continue.

    ``skip_for_pending_task_ready`` is True only when a *pending* task_ready
    already exists — host must NOT synthesize / re-arm one (K4/K16).

    ``start_cooldown`` is True when finalize (PR6) must advance
    ``last_enqueue_at`` as if an enqueue attempt occurred: successful enqueue
    **and** flood thrash skip (design gate 11 rate-limits flood loops). Other
    denies leave cooldown untouched.
    """

    enqueue: bool
    reason: str
    skip_for_pending_task_ready: bool = False
    start_cooldown: bool = False


@dataclass
class ContinuousRuntimeState:
    """Worker-owned mutable continuous flag + outer-chain counters.

    Defaults for enabled come from ContinuousSettings / continuous.json.
    PresenceWorker finalize updates streak/cooldown/last_* on outer continue;
    ``set_continuous_enabled`` persists enabled and resets streak on OFF.
    """

    enabled: bool = False
    streak: int = 0
    last_enqueue_at: datetime | None = None
    last_continue_wake_id: str | None = None
    last_source_moment_id: str | None = None
    last_skip_reason: str | None = None
    resetting: bool = False


def work_continue_host_message() -> str:
    """HOST work-continue line injected into the in-turn chain (obs / user)."""
    return WORK_CONTINUE_HOST


def flood_majority_or_last_stop(
    *,
    model_beats: int,
    flood_beats: int,
    last_stop_hop_was_flood: bool,
) -> bool:
    """Single normative flood thrash formula (outer + hard-stop sibling).

    Exactly one expression (design C)::

        (flood_beats >= 1 and flood_beats * 2 >= model_beats)
        OR last_stop_hop_was_flood
    """
    return bool(last_stop_hop_was_flood) or (
        flood_beats >= 1 and flood_beats * 2 >= model_beats
    )


def should_in_moment_work_nudge(
    *,
    continuous_enabled: bool,
    social_wake: bool,
    spoke: bool,
    no_speak_nudge_pending_or_needed: bool,
    work_nudge_sent: int,
    max_nudges: int,
    work_context: bool,
    last_hop_was_flood: bool,
) -> InMomentNudgeDecision:
    """Decide whether to inject the work-continue HOST before accepting no_tools.

    Social path (K8 / design §D):
    1. No-speak nudge wins first when still needed (``social_nudge_first``).
    2. Work-continue on social requires ``spoke=True`` — after no-speak is spent
       without a speak tool, accept ``no_tools`` (``need_spoke``). Never a second
       HOST that pushes tools without glass speech on a social wake.

    Non-social: ``spoke`` is not required; ``work_context`` alone can inject.

    Progress inputs are folded into ``work_context`` by the caller via
    ``in_moment_work_context`` (tools_ran / ledger_mutated not re-checked here).
    """
    if not continuous_enabled:
        return InMomentNudgeDecision(inject=False, reason="disabled")
    if last_hop_was_flood:
        return InMomentNudgeDecision(inject=False, reason="flood")
    # K8: social no-speak first, then work-continue only after spoke.
    if social_wake and not spoke:
        if no_speak_nudge_pending_or_needed:
            return InMomentNudgeDecision(inject=False, reason="social_nudge_first")
        return InMomentNudgeDecision(inject=False, reason="need_spoke")
    if max_nudges <= 0 or work_nudge_sent >= max_nudges:
        return InMomentNudgeDecision(inject=False, reason="budget")
    if not work_context:
        return InMomentNudgeDecision(inject=False, reason="not_workish")
    return InMomentNudgeDecision(inject=True, reason="injected")


def in_moment_work_context(
    *,
    social_wake: bool,
    tools_ran: bool,
    ledger_mutated: bool,
    wake_kind: str,
    has_open_goals_slice: bool,
) -> bool:
    """Compute work_context for should_in_moment_work_nudge (design C).

    Social: tools_ran OR ledger_mutated only (not pre-existing open goals).
    Non-social: tools_ran OR ledger_mutated OR work kinds OR open goals slice.
    """
    if tools_ran or ledger_mutated:
        return True
    if social_wake:
        return False
    if wake_kind in NON_SOCIAL_WORK_KINDS:
        return True
    return bool(has_open_goals_slice)


def _moment_continue_decision(
    enqueue: bool,
    reason: str,
    *,
    skip_for_pending_task_ready: bool = False,
) -> MomentContinueDecision:
    """Build a decision; flood deny and successful enqueue tick cooldown (gate 11)."""
    start_cooldown = reason in {"flood", "enqueued"}
    return MomentContinueDecision(
        enqueue=enqueue,
        reason=reason,
        skip_for_pending_task_ready=skip_for_pending_task_ready,
        start_cooldown=start_cooldown,
    )


def should_enqueue_moment_continue(
    *,
    continuous_enabled: bool,
    stop_reason: str,
    wake_kind: str,
    tools_ran: bool,
    ledger_mutated: bool,
    has_pending_wait: bool,
    pending_task_ready_count: int,
    has_open_work: bool,
    pending_moment_continues: int,
    streak: int,
    max_streak: int,
    seconds_since_last_enqueue: float | None,
    cooldown_seconds: int,
    model_beats: int,
    flood_beats: int,
    last_stop_hop_was_flood: bool,
    require_progress: bool = True,
    skip_pure_social: bool = True,
    max_pending_continues: int = 1,
) -> MomentContinueDecision:
    """Gates for outer ``moment_continue`` enqueue (pure; no I/O).

    Normative order (design C gates 1–11). Never synthesizes task_ready (K4/K16).
    ``tools_ran`` must mean ≥1 successful non-speak tool (counts_as_speak False).

    Open work is **always** required (K18) — no empty-ledger outer continue and
    no ``require_open_work`` opt-out parameter. Product settings reject False.
    """
    # 1. Toggle
    if not continuous_enabled:
        return _moment_continue_decision(False, "disabled")

    # 2. stop_reason allowlist
    if stop_reason not in MOMENT_CONTINUE_STOP_ALLOWLIST:
        return _moment_continue_decision(False, "stop_reason")

    # 3. Not while pending wait
    if has_pending_wait:
        return _moment_continue_decision(False, "pending_wait")

    # 4. At most max_pending_continues pending moment_continue
    if pending_moment_continues >= max_pending_continues:
        return _moment_continue_decision(False, "dedupe")

    # 5. Streak budget
    if streak >= max_streak:
        return _moment_continue_decision(False, "streak")

    # 6. Cooldown (None = never enqueued → elapsed)
    if (
        seconds_since_last_enqueue is not None
        and cooldown_seconds > 0
        and seconds_since_last_enqueue < cooldown_seconds
    ):
        return _moment_continue_decision(False, "cooldown")

    # 7. Non-speak progress (tools_ran OR ledger_mutated); speak alone fails
    if require_progress and not (tools_ran or ledger_mutated):
        return _moment_continue_decision(False, "no_progress")

    # 8. Pure social (social wake + no tools/ledger) — even if require_progress off
    if (
        skip_pure_social
        and wake_kind in SOCIAL_WAKE_KINDS
        and not tools_ran
        and not ledger_mutated
    ):
        return _moment_continue_decision(False, "pure_social")

    # 9. Prefer *pending* task_ready only — never synthesize
    if pending_task_ready_count > 0:
        return _moment_continue_decision(
            False,
            "pending_task_ready",
            skip_for_pending_task_ready=True,
        )

    # 10. Open work always required (K18 — no empty-ledger outer continue)
    if not has_open_work:
        return _moment_continue_decision(False, "no_open_work")

    # 11. Flood thrash (single formula); start_cooldown on deny
    if flood_majority_or_last_stop(
        model_beats=model_beats,
        flood_beats=flood_beats,
        last_stop_hop_was_flood=last_stop_hop_was_flood,
    ):
        return _moment_continue_decision(False, "flood")

    return _moment_continue_decision(True, "enqueued")


def continuous_status_block(
    state: ContinuousRuntimeState,
    settings: ContinuousSettings | None = None,
    *,
    pending_moment_continues: int = 0,
) -> dict[str, Any]:
    """Build the ``continuous`` object for status_snapshot /api/status."""
    cfg = settings if settings is not None else default_settings().continuous
    last_at = state.last_enqueue_at
    return {
        "enabled": bool(state.enabled),
        "streak": int(state.streak),
        "max_streak": int(cfg.max_continue_streak),
        "cooldown_seconds": int(cfg.cooldown_seconds),
        "last_enqueue_at": (
            last_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
            if isinstance(last_at, datetime)
            else None
        ),
        "last_skip_reason": state.last_skip_reason,
        "pending_moment_continues": int(pending_moment_continues),
    }


def continuous_runtime_path(data_dir: Path) -> Path:
    return Path(data_dir) / CONTINUOUS_RUNTIME_REL


def load_continuous_runtime(
    data_dir: Path,
    *,
    defaults: ContinuousSettings | None = None,
) -> ContinuousRuntimeState:
    """Load ContinuousRuntimeState: defaults then data/runtime/continuous.json.

    Missing or corrupt JSON → defaults only (enabled from ContinuousSettings).
    Does not invent wakes. Worker ``set_continuous_enabled`` / PR7 API write
    the file via ``save_continuous_enabled``.
    """
    cfg = defaults if defaults is not None else default_settings().continuous
    state = ContinuousRuntimeState(enabled=bool(cfg.enabled))
    path = continuous_runtime_path(data_dir)
    if not path.is_file():
        return state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("continuous runtime load failed (%s): %s", path, exc)
        return state
    if not isinstance(raw, dict):
        return state
    if "enabled" in raw:
        state.enabled = bool(raw["enabled"])
    return state


def save_continuous_enabled(data_dir: Path, enabled: bool) -> Path:
    """Persist enabled flag to data/runtime/continuous.json (creates parents)."""
    path = continuous_runtime_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "enabled": bool(enabled),
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(body, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
