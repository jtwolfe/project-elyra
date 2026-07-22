"""Tests for pure orient-slice formatters (skill catalog, bias, goals)."""

from __future__ import annotations

from elyra.loop.context import estimate_tokens
from elyra.loop.orient_slice import (
    BIAS_BACKGROUND,
    BIAS_DO_WORK,
    BIAS_MOMENT_CONTINUE,
    BIAS_TALK,
    BIAS_TIMER_GENERIC,
    BIAS_TIMER_LINKED,
    BIAS_WAIT_TIMEOUT,
    format_goals_slice,
    format_skill_bias,
    format_skill_catalog,
)


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


def test_format_skill_bias_table():
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
