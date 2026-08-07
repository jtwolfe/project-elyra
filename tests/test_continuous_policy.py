"""Continuous policy pure gates (moment_continue + in-moment work nudge)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from elyra.loop.continuous_policy import (
    LEDGER_AUDIT_TOOLS,
    MOMENT_CONTINUE_STOP_ALLOWLIST,
    WORK_CONTINUE_HOST,
    ContinuousRuntimeState,
    MomentContinueDecision,
    continuous_status_block,
    flood_majority_or_last_stop,
    in_moment_work_context,
    load_continuous_runtime,
    save_continuous_enabled,
    should_enqueue_moment_continue,
    should_in_moment_work_nudge,
    work_continue_host_message,
)
from elyra.loop.doloop import _is_host_inject
from elyra.settings import ContinuousSettings, default_settings, load_settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_kwargs(**overrides):
    """Baseline kwargs that pass every outer gate (enqueue=True)."""
    base = dict(
        continuous_enabled=True,
        stop_reason="no_tools",
        wake_kind="task_ready",
        tools_ran=True,
        ledger_mutated=False,
        has_pending_wait=False,
        pending_task_ready_count=0,
        has_open_work=True,
        pending_moment_continues=0,
        streak=0,
        max_streak=8,
        seconds_since_last_enqueue=None,
        cooldown_seconds=30,
        model_beats=4,
        flood_beats=0,
        last_stop_hop_was_flood=False,
        require_progress=True,
        skip_pure_social=True,
        max_pending_continues=1,
    )
    base.update(overrides)
    return base


def _decide(**overrides) -> MomentContinueDecision:
    return should_enqueue_moment_continue(**_ok_kwargs(**overrides))


# ---------------------------------------------------------------------------
# HOST constant / settings
# ---------------------------------------------------------------------------


def test_work_continue_host_starts_with_host():
    text = work_continue_host_message()
    assert text == WORK_CONTINUE_HOST
    assert text.startswith("HOST:")
    assert "load_skill" in text
    assert "ledger" in text
    # Option A HOST contract: audit-then-idle + bare stop may re-wake
    assert "list_goals" in text
    assert "get_goal" in text or "get_task" in text
    assert "re-wake" in text or "may re-wake" in text
    assert "wait_user" in text
    # Do-loop host-inject classifier (design D)
    assert _is_host_inject({"role": "user", "content": WORK_CONTINUE_HOST})
    assert not _is_host_inject({"role": "assistant", "content": WORK_CONTINUE_HOST})


def test_ledger_audit_tools_closed_set():
    """Single source of truth for Option A audit tool names."""
    assert LEDGER_AUDIT_TOOLS == frozenset({"list_goals", "get_goal", "get_task"})


def test_continuous_settings_defaults():
    s = default_settings().continuous
    assert s.enabled is False
    assert s.require_open_work is True
    assert s.require_progress is True
    assert s.max_continue_streak == 8
    assert s.cooldown_seconds == 30
    assert s.max_pending_continues == 1
    assert s.in_moment_work_nudge_max == 1
    assert s.skip_pure_social is True


def test_continuous_settings_from_toml(tmp_path: Path):
    (tmp_path / "elyra.toml").write_text(
        """
[continuous]
enabled = true
max_continue_streak = 3
cooldown_seconds = 10
require_open_work = true
""".strip()
        + "\n",
        encoding="utf-8",
    )
    s = load_settings(tmp_path)
    assert s.continuous.enabled is True
    assert s.continuous.max_continue_streak == 3
    assert s.continuous.cooldown_seconds == 10


def test_require_open_work_false_rejected_in_toml(tmp_path: Path):
    """K18: product load path cannot opt out of require_open_work."""
    (tmp_path / "elyra.toml").write_text(
        "[continuous]\nrequire_open_work = false\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="require_open_work"):
        load_settings(tmp_path)


# ---------------------------------------------------------------------------
# Outer gate table
# ---------------------------------------------------------------------------


def test_happy_path_enqueues():
    d = _decide()
    assert d.enqueue is True
    assert d.reason == "enqueued"
    assert d.skip_for_pending_task_ready is False
    assert d.start_cooldown is True  # PR6 must tick last_enqueue_at


@pytest.mark.parametrize(
    "stop_reason",
    sorted(MOMENT_CONTINUE_STOP_ALLOWLIST),
)
def test_allowlisted_stop_reasons_pass(stop_reason: str):
    d = _decide(stop_reason=stop_reason)
    assert d.enqueue is True, stop_reason


@pytest.mark.parametrize(
    "stop_reason",
    ["wait", "error", "wall_clock", "blocked", "interrupted", "policy", "unknown"],
)
def test_denied_stop_reasons(stop_reason: str):
    d = _decide(stop_reason=stop_reason)
    assert d.enqueue is False
    assert d.reason == "stop_reason"


def test_disabled_toggle():
    d = _decide(continuous_enabled=False)
    assert d.enqueue is False
    assert d.reason == "disabled"


def test_pending_wait_denies():
    d = _decide(has_pending_wait=True)
    assert d.enqueue is False
    assert d.reason == "pending_wait"


def test_dedupe_pending_moment_continue():
    d = _decide(pending_moment_continues=1)
    assert d.enqueue is False
    assert d.reason == "dedupe"


def test_streak_exhausted():
    d = _decide(streak=8, max_streak=8)
    assert d.enqueue is False
    assert d.reason == "streak"
    d2 = _decide(streak=7, max_streak=8)
    assert d2.enqueue is True


def test_cooldown_not_elapsed():
    d = _decide(seconds_since_last_enqueue=10.0, cooldown_seconds=30)
    assert d.enqueue is False
    assert d.reason == "cooldown"
    d2 = _decide(seconds_since_last_enqueue=30.0, cooldown_seconds=30)
    assert d2.enqueue is True
    d3 = _decide(seconds_since_last_enqueue=None, cooldown_seconds=30)
    assert d3.enqueue is True


def test_speak_only_no_progress_no_enqueue():
    """spoke alone must never qualify — tools_ran False, ledger False."""
    d = _decide(tools_ran=False, ledger_mutated=False, wake_kind="user_message")
    assert d.enqueue is False
    assert d.reason == "no_progress"


def test_pure_social_hello_no_enqueue():
    """Social wake + no tools/ledger → no outer continue (even after progress gate)."""
    # With require_progress=True we hit no_progress first.
    d = _decide(
        tools_ran=False,
        ledger_mutated=False,
        wake_kind="user_message",
        require_progress=True,
    )
    assert d.enqueue is False
    assert d.reason == "no_progress"
    # With require_progress=False, pure_social still blocks.
    d2 = _decide(
        tools_ran=False,
        ledger_mutated=False,
        wake_kind="user_message",
        require_progress=False,
    )
    assert d2.enqueue is False
    assert d2.reason == "pure_social"
    d3 = _decide(
        tools_ran=False,
        ledger_mutated=False,
        wake_kind="wait_reply",
        require_progress=False,
    )
    assert d3.enqueue is False
    assert d3.reason == "pure_social"


def test_ledger_mutated_counts_as_progress():
    d = _decide(tools_ran=False, ledger_mutated=True, wake_kind="user_message")
    assert d.enqueue is True


def test_pending_task_ready_skips_without_synthesize():
    d = _decide(pending_task_ready_count=1)
    assert d.enqueue is False
    assert d.reason == "pending_task_ready"
    assert d.skip_for_pending_task_ready is True
    # Policy never invents task_ready — flag is advisory for finalize (PR6).


def test_no_open_work_always_required_k18():
    """K18: open work is always required — no require_open_work opt-out param."""
    d = _decide(has_open_work=False)
    assert d.enqueue is False
    assert d.reason == "no_open_work"
    assert d.start_cooldown is False


def test_flood_majority_skips():
    # 3 flood / 4 model → majority (3*2 >= 4)
    d = _decide(model_beats=4, flood_beats=3, last_stop_hop_was_flood=False)
    assert d.enqueue is False
    assert d.reason == "flood"
    assert d.start_cooldown is True  # gate 11: flood skip ticks cooldown
    # Exactly half: 2*2 >= 4 → thrash
    d2 = _decide(model_beats=4, flood_beats=2)
    assert d2.enqueue is False
    assert d2.reason == "flood"
    assert d2.start_cooldown is True
    # Minority flood ok
    d3 = _decide(model_beats=5, flood_beats=2)
    assert d3.enqueue is True
    assert d3.start_cooldown is True


def test_last_stop_hop_flood_skips():
    d = _decide(
        model_beats=10,
        flood_beats=0,
        last_stop_hop_was_flood=True,
    )
    assert d.enqueue is False
    assert d.reason == "flood"
    assert d.start_cooldown is True


def test_flood_formula_helper():
    assert flood_majority_or_last_stop(
        model_beats=4, flood_beats=2, last_stop_hop_was_flood=False
    )
    assert not flood_majority_or_last_stop(
        model_beats=5, flood_beats=2, last_stop_hop_was_flood=False
    )
    assert flood_majority_or_last_stop(
        model_beats=100, flood_beats=0, last_stop_hop_was_flood=True
    )
    assert not flood_majority_or_last_stop(
        model_beats=1, flood_beats=0, last_stop_hop_was_flood=False
    )


def test_gate_order_disabled_before_stop_reason():
    d = _decide(continuous_enabled=False, stop_reason="wait")
    assert d.reason == "disabled"


def test_gate_order_pending_task_ready_after_progress():
    # Progress fails first when no tools.
    d = _decide(
        tools_ran=False,
        ledger_mutated=False,
        pending_task_ready_count=2,
    )
    assert d.reason == "no_progress"
    # With progress, pending task_ready wins.
    d2 = _decide(tools_ran=True, pending_task_ready_count=2)
    assert d2.reason == "pending_task_ready"
    assert d2.skip_for_pending_task_ready is True


# ---------------------------------------------------------------------------
# Option A: honest_exit (gate 7b after progress, before pure_social)
# ---------------------------------------------------------------------------


def test_honest_exit_when_ledger_audited_and_no_tools():
    """Audit + no_tools + progress + open work → no enqueue, reason honest_exit."""
    d = _decide(
        tools_ran=True,
        ledger_mutated=False,
        ledger_audited=True,
        stop_reason="no_tools",
        has_open_work=True,
    )
    assert d.enqueue is False
    assert d.reason == "honest_exit"
    assert d.start_cooldown is False


def test_no_honest_exit_when_not_audited():
    """ledger_audited=False + progress + open work + no_tools → still enqueued."""
    d = _decide(
        tools_ran=True,
        ledger_mutated=False,
        ledger_audited=False,
        stop_reason="no_tools",
        has_open_work=True,
    )
    assert d.enqueue is True
    assert d.reason == "enqueued"


def test_ledger_audited_false_default_still_enqueues():
    """Default ledger_audited=False path (kwargs omit) matches pre-Option-A."""
    d = _decide(tools_ran=True, has_open_work=True, stop_reason="no_tools")
    assert d.enqueue is True
    assert d.reason == "enqueued"


def test_audit_plus_time_continue_declined_does_not_honest_exit():
    """Option A applies only to no_tools — allowlisted non-idle stops stay progress-gated."""
    d = _decide(
        tools_ran=True,
        ledger_audited=True,
        stop_reason="time_continue_declined",
        has_open_work=True,
    )
    assert d.enqueue is True
    assert d.reason == "enqueued"
    d2 = _decide(
        tools_ran=True,
        ledger_audited=True,
        stop_reason="max_hops",
        has_open_work=True,
    )
    assert d2.enqueue is True
    assert d2.reason == "enqueued"


def test_honest_exit_after_progress_before_pure_social_and_open_work():
    """Gate order: progress then 7b; audited idle is honest_exit not pure_social/no_open_work."""
    # Would have been pure_social under require_progress=False without tools;
    # with tools_ran + audited, 7b fires before pure_social.
    d = _decide(
        tools_ran=True,
        ledger_mutated=False,
        ledger_audited=True,
        wake_kind="user_message",
        stop_reason="no_tools",
        has_open_work=True,
    )
    assert d.reason == "honest_exit"
    # Open work would fail later, but honest_exit wins first when audited.
    d2 = _decide(
        tools_ran=True,
        ledger_audited=True,
        stop_reason="no_tools",
        has_open_work=False,
    )
    assert d2.reason == "honest_exit"


def test_wait_and_non_allowlist_unchanged_with_audit():
    """wait / non-allowlist still deny via stop_reason even if audited."""
    d = _decide(
        stop_reason="wait",
        ledger_audited=True,
        tools_ran=True,
    )
    assert d.enqueue is False
    assert d.reason == "stop_reason"
    d2 = _decide(
        stop_reason="error",
        ledger_audited=True,
        tools_ran=True,
    )
    assert d2.enqueue is False
    assert d2.reason == "stop_reason"


def test_no_open_work_unchanged_without_audit():
    """K18 no_open_work still fires when not audited."""
    d = _decide(has_open_work=False, ledger_audited=False, tools_ran=True)
    assert d.enqueue is False
    assert d.reason == "no_open_work"


def test_honest_exit_masked_by_streak_cooldown_dedupe():
    """v1 masking: gates 1–6 fire before 7b; product outcome is still no enqueue."""
    d = _decide(streak=8, max_streak=8, ledger_audited=True, tools_ran=True)
    assert d.enqueue is False
    assert d.reason == "streak"
    d2 = _decide(
        seconds_since_last_enqueue=5.0,
        cooldown_seconds=30,
        ledger_audited=True,
        tools_ran=True,
    )
    assert d2.enqueue is False
    assert d2.reason == "cooldown"
    d3 = _decide(
        pending_moment_continues=1,
        ledger_audited=True,
        tools_ran=True,
    )
    assert d3.enqueue is False
    assert d3.reason == "dedupe"


# ---------------------------------------------------------------------------
# In-moment work nudge
# ---------------------------------------------------------------------------


def test_in_moment_nudge_injects_when_workish():
    d = should_in_moment_work_nudge(
        continuous_enabled=True,
        social_wake=False,
        spoke=False,
        no_speak_nudge_pending_or_needed=False,
        work_nudge_sent=0,
        max_nudges=1,
        work_context=True,
        last_hop_was_flood=False,
    )
    assert d.inject is True
    assert d.reason == "injected"


def test_in_moment_nudge_disabled():
    d = should_in_moment_work_nudge(
        continuous_enabled=False,
        social_wake=False,
        spoke=False,
        no_speak_nudge_pending_or_needed=False,
        work_nudge_sent=0,
        max_nudges=1,
        work_context=True,
        last_hop_was_flood=False,
    )
    assert d.inject is False
    assert d.reason == "disabled"


def test_in_moment_nudge_flood_hard_stop():
    d = should_in_moment_work_nudge(
        continuous_enabled=True,
        social_wake=False,
        spoke=False,
        no_speak_nudge_pending_or_needed=False,
        work_nudge_sent=0,
        max_nudges=1,
        work_context=True,
        last_hop_was_flood=True,
    )
    assert d.inject is False
    assert d.reason == "flood"


def test_in_moment_nudge_social_nudge_first():
    d = should_in_moment_work_nudge(
        continuous_enabled=True,
        social_wake=True,
        spoke=False,
        no_speak_nudge_pending_or_needed=True,
        work_nudge_sent=0,
        max_nudges=1,
        work_context=True,
        last_hop_was_flood=False,
    )
    assert d.inject is False
    assert d.reason == "social_nudge_first"


def test_in_moment_nudge_social_need_spoke_after_no_speak_spent():
    """K8/§D: after no-speak budget spent without speak, no work-continue on social."""
    d = should_in_moment_work_nudge(
        continuous_enabled=True,
        social_wake=True,
        spoke=False,
        no_speak_nudge_pending_or_needed=False,
        work_nudge_sent=0,
        max_nudges=1,
        work_context=True,  # tools ran does not override need_spoke
        last_hop_was_flood=False,
    )
    assert d.inject is False
    assert d.reason == "need_spoke"


def test_in_moment_nudge_social_injects_after_spoke():
    """Social work-continue only when spoke + work_context."""
    d = should_in_moment_work_nudge(
        continuous_enabled=True,
        social_wake=True,
        spoke=True,
        no_speak_nudge_pending_or_needed=False,
        work_nudge_sent=0,
        max_nudges=1,
        work_context=True,
        last_hop_was_flood=False,
    )
    assert d.inject is True
    assert d.reason == "injected"


def test_in_moment_nudge_budget():
    d = should_in_moment_work_nudge(
        continuous_enabled=True,
        social_wake=False,
        spoke=False,
        no_speak_nudge_pending_or_needed=False,
        work_nudge_sent=1,
        max_nudges=1,
        work_context=True,
        last_hop_was_flood=False,
    )
    assert d.inject is False
    assert d.reason == "budget"


def test_in_moment_nudge_not_workish():
    d = should_in_moment_work_nudge(
        continuous_enabled=True,
        social_wake=True,
        spoke=True,
        no_speak_nudge_pending_or_needed=False,
        work_nudge_sent=0,
        max_nudges=1,
        work_context=False,
        last_hop_was_flood=False,
    )
    assert d.inject is False
    assert d.reason == "not_workish"


def test_in_moment_nudge_thrash_recovery():
    """K15: thrash_host_sent > 0 suppresses work_continue (thrash_recovery)."""
    d = should_in_moment_work_nudge(
        continuous_enabled=True,
        social_wake=False,
        spoke=False,
        no_speak_nudge_pending_or_needed=False,
        work_nudge_sent=0,
        max_nudges=1,
        work_context=True,
        last_hop_was_flood=False,
        thrash_host_sent=1,
    )
    assert d.inject is False
    assert d.reason == "thrash_recovery"


def test_in_moment_work_context_social_ignores_open_goals_alone():
    assert (
        in_moment_work_context(
            social_wake=True,
            tools_ran=False,
            ledger_mutated=False,
            wake_kind="user_message",
            has_open_goals_slice=True,
        )
        is False
    )
    assert (
        in_moment_work_context(
            social_wake=True,
            tools_ran=True,
            ledger_mutated=False,
            wake_kind="user_message",
            has_open_goals_slice=False,
        )
        is True
    )


def test_in_moment_work_context_non_social_kinds_and_open_goals():
    assert in_moment_work_context(
        social_wake=False,
        tools_ran=False,
        ledger_mutated=False,
        wake_kind="moment_continue",
        has_open_goals_slice=False,
    )
    assert in_moment_work_context(
        social_wake=False,
        tools_ran=False,
        ledger_mutated=False,
        wake_kind="timer",
        has_open_goals_slice=False,
    )
    assert in_moment_work_context(
        social_wake=False,
        tools_ran=False,
        ledger_mutated=False,
        wake_kind="background",
        has_open_goals_slice=True,
    )
    assert not in_moment_work_context(
        social_wake=False,
        tools_ran=False,
        ledger_mutated=False,
        wake_kind="background",
        has_open_goals_slice=False,
    )


# ---------------------------------------------------------------------------
# Runtime load / status block
# ---------------------------------------------------------------------------


def test_load_continuous_runtime_defaults(tmp_path: Path):
    state = load_continuous_runtime(tmp_path / "data", defaults=ContinuousSettings())
    assert state.enabled is False
    assert state.streak == 0


def test_load_continuous_runtime_json_override(tmp_path: Path):
    data = tmp_path / "data"
    save_continuous_enabled(data, True)
    state = load_continuous_runtime(data, defaults=ContinuousSettings(enabled=False))
    assert state.enabled is True


def test_continuous_status_block_shape():
    state = ContinuousRuntimeState(enabled=True, streak=2, last_skip_reason="flood")
    state.last_enqueue_at = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
    block = continuous_status_block(
        state,
        ContinuousSettings(max_continue_streak=8, cooldown_seconds=30),
        pending_moment_continues=1,
    )
    assert block["enabled"] is True
    assert block["streak"] == 2
    assert block["max_streak"] == 8
    assert block["cooldown_seconds"] == 30
    assert block["last_skip_reason"] == "flood"
    assert block["pending_moment_continues"] == 1
    assert block["last_enqueue_at"].startswith("2026-07-22")
