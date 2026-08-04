"""Tests for pure orient-slice formatters (skill catalog, bias, goals)."""

from __future__ import annotations

import pytest

from elyra.loop.context import estimate_tokens
from elyra.loop.orient_slice import (
    BIAS_BACKGROUND,
    BIAS_DO_WORK,
    BIAS_MOMENT_CONTINUE,
    BIAS_PLAN_WORK,
    BIAS_REST,
    BIAS_TALK,
    BIAS_TIMER_GENERIC,
    BIAS_TIMER_LINKED,
    BIAS_WAIT_TIMEOUT,
    format_goals_slice,
    format_skill_bias,
    format_skill_catalog,
)


# ---------------------------------------------------------------------------
# Fixtures for ledger-aware bias (mapping dicts, list_goals shape)
# ---------------------------------------------------------------------------


def _goal(
    *,
    gid: str = "g1",
    status: str = "open",
    tasks: list | None = None,
) -> dict:
    return {
        "id": gid,
        "title": f"Goal {gid}",
        "status": status,
        "tasks": tasks if tasks is not None else [],
    }


def _task(tid: str, status: str) -> dict:
    return {"id": tid, "title": f"Task {tid}", "status": status}


READY_ON_OPEN = [
    _goal(tasks=[_task("t_ready", "ready")]),
]
IN_PROGRESS_ON_OPEN = [
    _goal(tasks=[_task("t_prog", "in_progress")]),
]
OPEN_NO_TASKS = [
    _goal(tasks=[]),
]
OPEN_PENDING_BLOCKED = [
    _goal(
        tasks=[
            _task("t_pend", "pending"),
            _task("t_block", "blocked"),
        ]
    ),
]
REVIEW_NO_READY = [
    _goal(status="review", tasks=[_task("t_done", "done")]),
]
CLOSED_ONLY = [
    _goal(status="closed", tasks=[_task("t_ready", "ready")]),
]
READY_ON_REVIEW = [
    _goal(status="review", tasks=[_task("t_ready", "ready")]),
]


# ---------------------------------------------------------------------------
# Catalog (unchanged)
# ---------------------------------------------------------------------------


def test_format_skill_catalog_name_and_description_only():
    catalog = [
        {"name": "do-work", "description": "Execute the next ready task."},
        {"name": "talk", "description": "Social presence."},
    ]
    text = format_skill_catalog(catalog)
    assert "- do-work: Execute the next ready task." in text
    assert "- talk: Social presence." in text
    assert "source" not in text
    assert "body" not in text


def test_format_skill_catalog_empty():
    assert format_skill_catalog(None) == ""
    assert format_skill_catalog([]) == ""


def test_format_skill_catalog_trailing_drop_when_over_budget():
    """YAGNI: simple trailing drop only — no bias-aware ranking."""
    catalog = [
        {"name": f"skill-{i:02d}", "description": "x" * 80} for i in range(20)
    ]
    full = format_skill_catalog(catalog)
    assert estimate_tokens(full) > 50
    capped = format_skill_catalog(catalog, max_tokens=50)
    assert estimate_tokens(capped) <= 50
    # Alphabetically first skills kept; last dropped.
    assert "skill-00" in capped
    assert "skill-19" not in capped


def test_format_skill_catalog_nonpositive_budget_is_empty():
    """max_tokens <= 0 must not silently mean unlimited."""
    catalog = [{"name": "talk", "description": "Social presence."}]
    assert format_skill_catalog(catalog, max_tokens=0) == ""
    assert format_skill_catalog(catalog, max_tokens=-1) == ""
    # None remains uncapped.
    assert "talk" in format_skill_catalog(catalog, max_tokens=None)


def test_bundled_skill_catalog_size_bands() -> None:
    """Uncapped bundled catalog stays under meal soft/hard token bands.

    Historical soft cap was 400 tokens (dropped late-alpha skills like talk).
    Bands: soft/warn = 800 (2×), hard/error = 1600 (4×). Meal-content review
    will re-tune before v0.1; hard fail should never hit in practice.
    """
    import warnings

    from elyra.skills.catalog import SkillCatalog

    catalog = SkillCatalog().catalog()
    full = format_skill_catalog(catalog, max_tokens=None)
    tokens = estimate_tokens(full)
    soft_warn = 800
    hard_fail = 1600
    assert "- talk:" in full, "full catalog must include talk (late-alpha skill)"
    assert tokens < hard_fail, (
        f"bundled skill catalog is {tokens} tokens (hard budget {hard_fail}); "
        "review meal content before this ceiling"
    )
    if tokens >= soft_warn:
        warnings.warn(
            f"bundled skill catalog is {tokens} tokens (soft budget {soft_warn}); "
            "review meal content before further growth",
            UserWarning,
            stacklevel=1,
        )


# ---------------------------------------------------------------------------
# A. Social override (ledger ignored)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wake_kind,goals,payload",
    [
        ("user_message", None, None),  # A1
        ("wait_reply", None, None),  # A2
        ("user_message", READY_ON_OPEN, None),  # A3
        ("wait_reply", OPEN_NO_TASKS, None),  # A4
        ("user_message", [], None),  # A5
        ("user_message", READY_ON_OPEN, {"task_id": "t_ready"}),
        ("wait_reply", CLOSED_ONLY, None),
    ],
    ids=[
        "A1_user_message_goals_None",
        "A2_wait_reply_goals_None",
        "A3_user_message_ready_ledger",
        "A4_wait_reply_open_no_tasks",
        "A5_user_message_empty_list",
        "user_message_ready_with_payload",
        "wait_reply_closed_only",
    ],
)
def test_social_override_always_talk(wake_kind, goals, payload):
    assert format_skill_bias(wake_kind, payload, goals) == BIAS_TALK


# ---------------------------------------------------------------------------
# B. Ledger preference (non-social, goals provided)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wake_kind,goals,payload,expected",
    [
        # B1
        ("background", READY_ON_OPEN, None, BIAS_DO_WORK),
        # B2
        ("timer", IN_PROGRESS_ON_OPEN, None, BIAS_DO_WORK),
        # B3
        ("task_ready", READY_ON_OPEN, {"task_id": "t_ready"}, BIAS_DO_WORK),
        # B4
        ("background", OPEN_NO_TASKS, None, BIAS_PLAN_WORK),
        # B5
        ("timer", OPEN_PENDING_BLOCKED, None, BIAS_PLAN_WORK),
        # B6
        ("moment_continue", REVIEW_NO_READY, None, BIAS_PLAN_WORK),
        # B7
        ("background", [], None, BIAS_REST),
        # B8
        ("timer", CLOSED_ONLY, None, BIAS_REST),
        # B9
        ("wait_timeout", CLOSED_ONLY, None, BIAS_REST),
        # B10 — empty ledger + task_ready payload → REST (not wake-kind DO_WORK)
        (
            "task_ready",
            [],
            {"task_id": "t_stale", "goal_id": "g_stale"},
            BIAS_REST,
        ),
        # B11a — open goals, no ready/in_progress → PLAN_WORK
        ("task_ready", OPEN_PENDING_BLOCKED, {"task_id": "t_block"}, BIAS_PLAN_WORK),
        # B11b — no open/review remaining → REST
        ("task_ready", CLOSED_ONLY, {"task_id": "t_ready"}, BIAS_REST),
        # Extra edge: review + ready → DO_WORK
        ("background", READY_ON_REVIEW, None, BIAS_DO_WORK),
        # Extra: open goal zero tasks already B4; moment_continue empty → REST
        ("moment_continue", [], None, BIAS_REST),
        ("timer", [], {"task_id": "t1"}, BIAS_REST),
        ("wait_timeout", [], None, BIAS_REST),
        # blocked-only does NOT count as ready (plan-work, not do-work)
        (
            "background",
            [_goal(tasks=[_task("t_b", "blocked")])],
            None,
            BIAS_PLAN_WORK,
        ),
        # ready on closed goal is filtered out → REST
        (
            "background",
            [_goal(status="closed", tasks=[_task("t_r", "ready")])],
            None,
            BIAS_REST,
        ),
        # cancelled goals only → REST
        (
            "background",
            [_goal(status="cancelled", tasks=[_task("t_r", "ready")])],
            None,
            BIAS_REST,
        ),
    ],
    ids=[
        "B1_background_ready",
        "B2_timer_in_progress",
        "B3_task_ready_ready_on_ledger",
        "B4_background_open_no_tasks",
        "B5_timer_pending_blocked",
        "B6_moment_continue_review_no_ready",
        "B7_background_empty",
        "B8_timer_closed_only",
        "B9_wait_timeout_no_open",
        "B10_task_ready_empty_ledger_REST",
        "B11a_task_ready_open_no_ready_PLAN",
        "B11b_task_ready_closed_only_REST",
        "review_with_ready_DO_WORK",
        "moment_continue_empty_REST",
        "timer_empty_with_payload_REST",
        "wait_timeout_empty_REST",
        "blocked_only_PLAN_WORK",
        "ready_on_closed_goal_REST",
        "cancelled_only_REST",
    ],
)
def test_ledger_preference_when_goals_provided(wake_kind, goals, payload, expected):
    assert format_skill_bias(wake_kind, payload, goals) == expected


# ---------------------------------------------------------------------------
# C. Wake-kind table when goals is None (unit-compat only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args,expected",
    [
        (("task_ready", {"task_id": "t1"}), BIAS_DO_WORK),  # C1
        (("timer", {"task_id": "t1"}), BIAS_TIMER_LINKED),  # C2
        (("timer", {"goal_id": "g1"}), BIAS_TIMER_LINKED),  # C3
        (("timer", {"reason": "ping"}), BIAS_TIMER_GENERIC),  # C4
        (("timer",), BIAS_TIMER_GENERIC),  # C5
        (("moment_continue",), BIAS_MOMENT_CONTINUE),  # C6
        (("background",), BIAS_BACKGROUND),  # C7
        (("wait_timeout",), BIAS_WAIT_TIMEOUT),  # C8
        (("unknown_kind",), ""),  # C9
        (("",), ""),  # C10
    ],
    ids=[
        "C1_task_ready",
        "C2_timer_task_id",
        "C3_timer_goal_id",
        "C4_timer_reason",
        "C5_timer_bare",
        "C6_moment_continue",
        "C7_background",
        "C8_wait_timeout",
        "C9_unknown",
        "C10_empty",
    ],
)
def test_wake_kind_table_when_goals_none(args, expected):
    assert format_skill_bias(*args) == expected


def test_format_skill_bias_table_compat():
    """Legacy smoke: goals=None path still matches pre-Stage-B table."""
    assert format_skill_bias("user_message") == BIAS_TALK
    assert format_skill_bias("wait_reply") == BIAS_TALK
    assert format_skill_bias("task_ready", {"task_id": "t1"}) == BIAS_DO_WORK
    assert format_skill_bias("timer", {"task_id": "t1"}) == BIAS_TIMER_LINKED
    assert format_skill_bias("timer", {"goal_id": "g1"}) == BIAS_TIMER_LINKED
    assert format_skill_bias("timer", {"reason": "ping"}) == BIAS_TIMER_GENERIC
    assert format_skill_bias("timer") == BIAS_TIMER_GENERIC
    assert format_skill_bias("moment_continue") == BIAS_MOMENT_CONTINUE
    assert format_skill_bias("background") == BIAS_BACKGROUND
    assert format_skill_bias("wait_timeout") == BIAS_WAIT_TIMEOUT
    assert format_skill_bias("unknown_kind") == ""


def test_moment_continue_bias_string_exact():
    """Dead path until continuous PRs; string lands early and must stay exact."""
    assert format_skill_bias("moment_continue") == (
        "Prefer skill: do-work or plan-work; use create-tool/create-skill only if "
        "capability is the bottleneck. Load rest if nothing honest remains."
    )


# ---------------------------------------------------------------------------
# D. Shape / purity / edge cases
# ---------------------------------------------------------------------------


def test_bias_strings_are_single_line():
    """D1: return is single line (no newline in bias constants)."""
    constants = [
        BIAS_TALK,
        BIAS_DO_WORK,
        BIAS_TIMER_LINKED,
        BIAS_TIMER_GENERIC,
        BIAS_MOMENT_CONTINUE,
        BIAS_BACKGROUND,
        BIAS_WAIT_TIMEOUT,
        BIAS_PLAN_WORK,
        BIAS_REST,
    ]
    for s in constants:
        assert "\n" not in s, repr(s)
    # And live returns from both paths
    assert "\n" not in format_skill_bias("background", goals=[])
    assert "\n" not in format_skill_bias("task_ready", {"task_id": "t1"})
    assert "\n" not in format_skill_bias("background", goals=READY_ON_OPEN)
    assert "\n" not in format_skill_bias("background", goals=OPEN_NO_TASKS)


def test_bias_new_constants_exact_strings():
    assert BIAS_PLAN_WORK == (
        "Prefer skill: plan-work (open goal needs tasks before execution)."
    )
    assert BIAS_REST == "Prefer skill: rest (nothing honest open on the ledger)."


def test_malformed_goals_do_not_raise():
    """D3: malformed entries do not raise; non-list tasks → no ready signal."""
    # Non-dict in goals list
    assert (
        format_skill_bias("background", goals=["not-a-dict", 42, None])  # type: ignore[list-item]
        == BIAS_REST
    )
    # Goal without tasks key
    assert (
        format_skill_bias(
            "background",
            goals=[{"id": "g1", "status": "open", "title": "no tasks key"}],
        )
        == BIAS_PLAN_WORK
    )
    # tasks is None
    assert (
        format_skill_bias(
            "background",
            goals=[{"id": "g1", "status": "open", "tasks": None}],
        )
        == BIAS_PLAN_WORK
    )
    # tasks is not a list
    assert (
        format_skill_bias(
            "background",
            goals=[{"id": "g1", "status": "open", "tasks": "not-a-list"}],
        )
        == BIAS_PLAN_WORK
    )
    # task entry non-mapping mixed with a real ready task
    goals_mixed = [
        {
            "id": "g1",
            "status": "open",
            "tasks": [
                "bad-task",
                None,
                99,
                {"id": "t_ok", "status": "ready", "title": "ok"},
            ],
        }
    ]
    assert format_skill_bias("background", goals=goals_mixed) == BIAS_DO_WORK
    # Only malformed tasks on open goal → plan-work (no ready signal)
    goals_only_bad_tasks = [
        {
            "id": "g1",
            "status": "open",
            "tasks": ["bad", None, 3],
        }
    ]
    assert format_skill_bias("background", goals=goals_only_bad_tasks) == BIAS_PLAN_WORK
    # tasks as tuple of mappings still works (list or tuple accepted)
    goals_tuple_tasks = [
        {
            "id": "g1",
            "status": "open",
            "tasks": ({"id": "t1", "status": "in_progress", "title": "x"},),
        }
    ]
    assert format_skill_bias("background", goals=goals_tuple_tasks) == BIAS_DO_WORK
    # Non-mapping goal mixed with valid open goal (no ready) → plan-work
    mixed_goals = [
        "skip-me",
        {"id": "g2", "status": "open", "tasks": []},
    ]
    assert format_skill_bias("timer", goals=mixed_goals) == BIAS_PLAN_WORK


def test_goals_none_vs_empty_list_semantics():
    """goals=None → wake table; goals=[] → REST for non-social."""
    assert format_skill_bias("task_ready", {"task_id": "t1"}, goals=None) == BIAS_DO_WORK
    assert format_skill_bias("task_ready", {"task_id": "t1"}, goals=[]) == BIAS_REST
    assert format_skill_bias("background", goals=None) == BIAS_BACKGROUND
    assert format_skill_bias("background", goals=[]) == BIAS_REST
    assert format_skill_bias("timer", {"task_id": "t1"}, goals=None) == BIAS_TIMER_LINKED
    assert format_skill_bias("timer", {"task_id": "t1"}, goals=[]) == BIAS_REST


# ---------------------------------------------------------------------------
# Goals slice (unchanged / D4)
# ---------------------------------------------------------------------------


def test_format_goals_slice_empty():
    assert format_goals_slice(None) == "(no open goals)"
    assert format_goals_slice([]) == "(no open goals)"
    assert format_goals_slice(
        [{"id": "g1", "title": "done", "status": "closed", "tasks": []}]
    ) == "(no open goals)"


def test_format_goals_slice_open_and_tasks():
    goals = [
        {
            "id": "g_abc",
            "title": "Organize sandbox docs",
            "status": "open",
            "acceptance": "README lists layout",
            "updated_at": "2026-07-22T12:00:00+00:00",
            "tasks": [
                {
                    "id": "t_ready",
                    "title": "Draft README outline",
                    "status": "ready",
                },
                {
                    "id": "t_prog",
                    "title": "Move notes",
                    "status": "in_progress",
                },
                {
                    "id": "t_pend",
                    "title": "Later polish",
                    "status": "pending",
                },
                {
                    "id": "t_done",
                    "title": "Old step",
                    "status": "done",
                },
            ],
        }
    ]
    text = format_goals_slice(goals, max_tokens=600)
    assert "Goal g_abc [open]: Organize sandbox docs" in text
    assert "acceptance: README lists layout" in text
    assert "t_ready" in text and "[ready]" in text
    assert "t_prog" in text and "[in_progress]" in text
    # pending/done omitted by default
    assert "t_pend" not in text
    assert "t_done" not in text


def test_format_goals_slice_excludes_closed_cancelled():
    goals = [
        {
            "id": "g_open",
            "title": "Keep",
            "status": "open",
            "updated_at": "2026-07-22T10:00:00+00:00",
            "tasks": [],
        },
        {
            "id": "g_closed",
            "title": "Drop",
            "status": "closed",
            "updated_at": "2026-07-22T11:00:00+00:00",
            "tasks": [],
        },
        {
            "id": "g_cancel",
            "title": "Drop2",
            "status": "cancelled",
            "updated_at": "2026-07-22T12:00:00+00:00",
            "tasks": [],
        },
        {
            "id": "g_rev",
            "title": "Review me",
            "status": "review",
            "updated_at": "2026-07-22T09:00:00+00:00",
            "tasks": [{"id": "t1", "title": "check", "status": "blocked"}],
        },
    ]
    text = format_goals_slice(goals, max_tokens=600)
    assert "g_open" in text
    assert "g_rev" in text
    assert "g_closed" not in text
    assert "g_cancel" not in text
    assert "t1" in text and "[blocked]" in text


def test_format_goals_slice_budget_drops_oldest_updated():
    goals = [
        {
            "id": "g_old",
            "title": "Oldest",
            "status": "open",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "tasks": [
                {"id": "t_old", "title": "old task", "status": "ready"},
            ],
        },
        {
            "id": "g_new",
            "title": "Newest",
            "status": "open",
            "updated_at": "2026-07-22T00:00:00+00:00",
            "tasks": [
                {"id": "t_new", "title": "new task", "status": "ready"},
            ],
        },
    ]
    # Tiny budget: only one small goal block should fit.
    text = format_goals_slice(goals, max_tokens=25)
    assert "g_new" in text
    assert "g_old" not in text


def test_format_goals_slice_protects_wake_ids():
    goals = [
        {
            "id": "g_old",
            "title": "Protected old",
            "status": "open",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "tasks": [
                {"id": "t_prot", "title": "wake task", "status": "ready"},
            ],
        },
        {
            "id": "g_new",
            "title": "Newest filler " + ("x" * 40),
            "status": "open",
            "updated_at": "2026-07-22T00:00:00+00:00",
            "tasks": [
                {
                    "id": "t_new",
                    "title": "filler " + ("y" * 40),
                    "status": "ready",
                },
            ],
        },
    ]
    text = format_goals_slice(
        goals,
        max_tokens=40,
        protect_task_ids={"t_prot"},
    )
    # Protected goal/task must remain even if oldest-updated.
    assert "g_old" in text
    assert "t_prot" in text


def test_format_goals_slice_shows_goes_by_when_present():
    goals = [
        {
            "id": "g_jim",
            "title": "Ship feature",
            "status": "open",
            "updated_at": "2026-07-22T12:00:00+00:00",
            "created_in_context": {
                "user_id": "jim",
                "goes_by": "Jim",
                "source": "tool",
            },
            "tasks": [
                {"id": "t1", "title": "Do it", "status": "ready"},
            ],
        },
        {
            "id": "g_solo",
            "title": "Autonomous",
            "status": "open",
            "updated_at": "2026-07-22T11:00:00+00:00",
            "tasks": [],
        },
    ]
    text = format_goals_slice(goals, max_tokens=600)
    assert "Goal g_jim [open]: Ship feature · Jim" in text
    # No · annotation when created_in_context absent.
    assert "Goal g_solo [open]: Autonomous" in text
    assert "Autonomous ·" not in text


def test_format_goals_slice_skips_goes_by_when_missing_or_blank():
    goals = [
        {
            "id": "g1",
            "title": "Only user_id",
            "status": "open",
            "updated_at": "2026-07-22T12:00:00+00:00",
            "created_in_context": {"user_id": "jim"},
            "tasks": [],
        },
        {
            "id": "g2",
            "title": "Blank goes_by",
            "status": "open",
            "updated_at": "2026-07-22T11:00:00+00:00",
            "created_in_context": {"user_id": "jim", "goes_by": "  "},
            "tasks": [],
        },
    ]
    text = format_goals_slice(goals, max_tokens=600)
    assert "·" not in text
    assert "Only user_id" in text
    assert "Blank goes_by" in text
