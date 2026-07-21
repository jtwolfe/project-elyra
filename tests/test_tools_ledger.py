"""Tests for ledger tools: update_task and update_goal."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.goals import GoalsStore, SOFT_CLOSE_WARNING
from elyra.tools import ToolContext, ToolRegistry, resolve_bundled_tools_root
from elyra.tools.builtin.ledger import update_goal, update_task


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


@pytest.fixture
def store(paths) -> GoalsStore:
    return GoalsStore(paths)


@pytest.fixture
def registry(paths) -> ToolRegistry:
    return ToolRegistry(paths, bundled_root=resolve_bundled_tools_root())


def _ctx(
    paths,
    store: GoalsStore | None,
    *,
    enqueue_wake=None,
    mark_task_changed=None,
) -> ToolContext:
    return ToolContext(
        paths=paths,
        goals=store,
        enqueue_wake=enqueue_wake,
        mark_task_changed=mark_task_changed,
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_ledger_packages(registry: ToolRegistry) -> None:
    assert registry.has("update_task")
    assert registry.has("update_goal")
    task_pkg = registry.get("update_task")
    goal_pkg = registry.get("update_goal")
    assert task_pkg is not None and task_pkg.meta.kind == "mutate"
    assert goal_pkg is not None and goal_pkg.meta.kind == "mutate"
    assert task_pkg.handler is not None
    assert goal_pkg.handler is not None
    names = registry.names()
    assert "update_task" in names
    assert "update_goal" in names


def test_openai_tools_include_ledger(registry: ToolRegistry) -> None:
    tools = {t["function"]["name"]: t for t in registry.openai_tools()}
    assert "update_task" in tools
    assert "update_goal" in tools
    assert "task_id" in tools["update_task"]["function"]["parameters"]["properties"]
    assert "goal_id" in tools["update_goal"]["function"]["parameters"]["properties"]
    assert "force" in tools["update_goal"]["function"]["parameters"]["properties"]


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------


def test_update_task_status_and_notes(paths, store: GoalsStore) -> None:
    g = store.create_goal("Parent")
    t = store.create_task(g["id"], "Work item")
    result = update_task(
        {"task_id": t["id"], "status": "in_progress", "notes": "started"},
        _ctx(paths, store),
    )
    assert result.ok is True
    assert result.error_reason is None
    assert result.payload["ok"] is True
    assert result.payload["task"]["status"] == "in_progress"
    assert result.payload["task"]["notes"] == "started"
    assert store.get_task(t["id"])["status"] == "in_progress"


def test_update_task_to_ready_calls_enqueue_wake(paths, store: GoalsStore) -> None:
    wakes: list[dict[str, Any]] = []
    changed: list[bool] = []

    def enqueue_wake(**kwargs: Any) -> str:
        wakes.append(kwargs)
        return "wake_1"

    def mark_task_changed() -> None:
        changed.append(True)

    g = store.create_goal("G")
    t = store.create_task(g["id"], "T")
    result = update_task(
        {"task_id": t["id"], "status": "ready"},
        _ctx(
            paths,
            store,
            enqueue_wake=enqueue_wake,
            mark_task_changed=mark_task_changed,
        ),
    )
    assert result.ok is True
    assert result.payload["task"]["status"] == "ready"
    assert len(wakes) == 1
    assert wakes[0]["kind"] == "task_ready"
    assert wakes[0]["task_id"] == t["id"]
    assert wakes[0]["goal_id"] == g["id"]
    assert changed == [True]


def test_update_task_already_ready_does_not_reenqueue(
    paths, store: GoalsStore
) -> None:
    wakes: list[dict[str, Any]] = []

    def enqueue_wake(**kwargs: Any) -> str:
        wakes.append(kwargs)
        return f"wake_{len(wakes)}"

    g = store.create_goal("G")
    t = store.create_task(g["id"], "T", status="ready")
    # First call: already ready → no transition, no enqueue from tool layer
    result = update_task(
        {"task_id": t["id"], "status": "ready", "notes": "still ready"},
        _ctx(paths, store, enqueue_wake=enqueue_wake),
    )
    assert result.ok is True
    assert wakes == []

    # Leave and re-enter ready → enqueues once
    store.update_task(t["id"], status="in_progress")
    result2 = update_task(
        {"task_id": t["id"], "status": "ready"},
        _ctx(paths, store, enqueue_wake=enqueue_wake),
    )
    assert result2.ok is True
    assert len(wakes) == 1
    assert wakes[0]["kind"] == "task_ready"


def test_update_task_mark_changed_on_any_success(paths, store: GoalsStore) -> None:
    changed: list[bool] = []
    g = store.create_goal("G")
    t = store.create_task(g["id"], "T")
    result = update_task(
        {"task_id": t["id"], "title": "Renamed"},
        _ctx(paths, store, mark_task_changed=lambda: changed.append(True)),
    )
    assert result.ok is True
    assert changed == [True]
    assert result.payload["task"]["title"] == "Renamed"


def test_update_task_without_enqueue_port_still_ok(paths, store: GoalsStore) -> None:
    """enqueue_wake optional; store may still fire on_task_ready if wired."""
    g = store.create_goal("G")
    t = store.create_task(g["id"], "T")
    result = update_task(
        {"task_id": t["id"], "status": "ready"},
        _ctx(paths, store),
    )
    assert result.ok is True
    assert result.payload["task"]["status"] == "ready"


def test_update_task_missing_goals(paths) -> None:
    result = update_task(
        {"task_id": "t_x", "status": "ready"},
        _ctx(paths, None),
    )
    assert result.ok is False
    assert result.error_reason == "goals_not_configured"


def test_update_task_not_found(paths, store: GoalsStore) -> None:
    result = update_task(
        {"task_id": "t_missing", "status": "ready"},
        _ctx(paths, store),
    )
    assert result.ok is False
    assert result.error_reason == "task_not_found"


def test_update_task_missing_id(paths, store: GoalsStore) -> None:
    result = update_task({"status": "ready"}, _ctx(paths, store))
    assert result.ok is False
    assert result.error_reason == "missing_task_id"


def test_update_task_no_fields(paths, store: GoalsStore) -> None:
    g = store.create_goal("G")
    t = store.create_task(g["id"], "T")
    result = update_task({"task_id": t["id"]}, _ctx(paths, store))
    assert result.ok is False
    assert result.error_reason == "no_fields_to_update"


def test_update_task_invalid_status(paths, store: GoalsStore) -> None:
    g = store.create_goal("G")
    t = store.create_task(g["id"], "T")
    result = update_task(
        {"task_id": t["id"], "status": "bogus"},
        _ctx(paths, store),
    )
    assert result.ok is False
    assert result.error_reason is not None
    assert result.error_reason.startswith("invalid_args:")


def test_update_task_via_registry(registry: ToolRegistry, paths, store: GoalsStore) -> None:
    g = store.create_goal("G")
    t = store.create_task(g["id"], "T")
    wakes: list[dict[str, Any]] = []
    result = registry.execute(
        "update_task",
        {"task_id": t["id"], "status": "ready"},
        ToolContext(
            paths=paths,
            goals=store,
            enqueue_wake=lambda **kw: wakes.append(kw) or "w1",
        ),
    )
    assert result.ok is True
    assert result.payload["task"]["status"] == "ready"
    assert wakes and wakes[0]["kind"] == "task_ready"
    # Ledger tools are mutate, not control — must not end the moment.
    assert result.ends_moment is False
    assert result.counts_as_speak is False


# ---------------------------------------------------------------------------
# update_goal
# ---------------------------------------------------------------------------


def test_update_goal_soft_close_warning(paths, store: GoalsStore) -> None:
    g = store.create_goal("Close soft")
    result = update_goal(
        {"goal_id": g["id"], "status": "closed"},
        _ctx(paths, store),
    )
    assert result.ok is True
    assert result.payload["ok"] is True
    assert result.payload["warning"] == SOFT_CLOSE_WARNING
    assert (
        result.payload["warning"]
        == "prefer review-work before close; set status=review first or pass force=true"
    )
    assert result.payload["goal"]["status"] == "closed"
    assert store.goal_close_without_review == 1


def test_update_goal_force_close_no_warning(paths, store: GoalsStore) -> None:
    g = store.create_goal("Force")
    result = update_goal(
        {"goal_id": g["id"], "status": "closed", "force": True},
        _ctx(paths, store),
    )
    assert result.ok is True
    assert "warning" not in result.payload
    assert result.payload["goal"]["status"] == "closed"
    assert store.goal_close_without_review == 1


def test_update_goal_review_then_close_clean(paths, store: GoalsStore) -> None:
    g = store.create_goal("Review first")
    r1 = update_goal(
        {"goal_id": g["id"], "status": "review"},
        _ctx(paths, store),
    )
    assert r1.ok is True
    assert "warning" not in r1.payload
    r2 = update_goal(
        {"goal_id": g["id"], "status": "closed"},
        _ctx(paths, store),
    )
    assert r2.ok is True
    assert "warning" not in r2.payload
    assert store.goal_close_without_review == 0


def test_update_goal_title_and_acceptance(paths, store: GoalsStore) -> None:
    g = store.create_goal("Old", acceptance="a1")
    result = update_goal(
        {
            "goal_id": g["id"],
            "title": "New title",
            "acceptance": "done when green",
        },
        _ctx(paths, store),
    )
    assert result.ok is True
    assert result.payload["goal"]["title"] == "New title"
    assert result.payload["goal"]["acceptance"] == "done when green"


def test_update_goal_force_must_be_bool(paths, store: GoalsStore) -> None:
    g = store.create_goal("G")
    result = update_goal(
        {"goal_id": g["id"], "status": "closed", "force": "yes"},
        _ctx(paths, store),
    )
    assert result.ok is False
    assert result.error_reason == "force_must_be_bool"
    assert store.get_goal(g["id"])["status"] == "open"


def test_update_goal_not_found(paths, store: GoalsStore) -> None:
    result = update_goal(
        {"goal_id": "g_missing", "status": "closed"},
        _ctx(paths, store),
    )
    assert result.ok is False
    assert result.error_reason == "goal_not_found"


def test_update_goal_missing_goals(paths) -> None:
    result = update_goal(
        {"goal_id": "g_x", "status": "closed"},
        _ctx(paths, None),
    )
    assert result.ok is False
    assert result.error_reason == "goals_not_configured"


def test_update_goal_via_registry_soft_close(
    registry: ToolRegistry, paths, store: GoalsStore
) -> None:
    g = store.create_goal("Via reg")
    result = registry.execute(
        "update_goal",
        {"goal_id": g["id"], "status": "closed"},
        ToolContext(paths=paths, goals=store),
    )
    assert result.ok is True
    assert result.payload.get("warning") == SOFT_CLOSE_WARNING
    assert result.ends_moment is False


def test_update_goal_invalid_status(paths, store: GoalsStore) -> None:
    g = store.create_goal("G")
    result = update_goal(
        {"goal_id": g["id"], "status": "nope"},
        _ctx(paths, store),
    )
    assert result.ok is False
    assert result.error_reason is not None
    assert result.error_reason.startswith("invalid_args:")
