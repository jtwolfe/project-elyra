"""Tests for ledger tools: create/list/get/update goal and task."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.goals import GoalsStore, SOFT_CLOSE_WARNING
from elyra.tools import ToolContext, ToolRegistry, resolve_bundled_tools_root
from elyra.tools.builtin.ledger import (
    create_goal,
    create_task,
    get_goal,
    get_task,
    list_goals,
    update_goal,
    update_task,
)


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
    user_id: str | None = None,
    moment_id: str = "",
    extras: dict[str, Any] | None = None,
) -> ToolContext:
    return ToolContext(
        paths=paths,
        goals=store,
        enqueue_wake=enqueue_wake,
        mark_task_changed=mark_task_changed,
        user_id=user_id,
        moment_id=moment_id,
        extras=extras or {},
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


_LEDGER_MUTATE = (
    "create_goal",
    "create_task",
    "update_task",
    "update_goal",
)
_LEDGER_READ = ("list_goals", "get_goal", "get_task")
_LEDGER_ALL = _LEDGER_MUTATE + _LEDGER_READ


def test_discover_ledger_packages(registry: ToolRegistry) -> None:
    for name in _LEDGER_ALL:
        assert registry.has(name), name
        pkg = registry.get(name)
        assert pkg is not None and pkg.handler is not None
    for name in _LEDGER_MUTATE:
        assert registry.get(name).meta.kind == "mutate"  # type: ignore[union-attr]
    for name in _LEDGER_READ:
        assert registry.get(name).meta.kind == "read"  # type: ignore[union-attr]
    names = registry.names()
    for name in _LEDGER_ALL:
        assert name in names


def test_openai_tools_include_ledger(registry: ToolRegistry) -> None:
    tools = {t["function"]["name"]: t for t in registry.openai_tools()}
    for name in _LEDGER_ALL:
        assert name in tools, name
    assert "task_id" in tools["update_task"]["function"]["parameters"]["properties"]
    assert "goal_id" in tools["update_goal"]["function"]["parameters"]["properties"]
    assert "force" in tools["update_goal"]["function"]["parameters"]["properties"]
    assert "title" in tools["create_goal"]["function"]["parameters"]["properties"]
    assert (
        "created_in_context"
        in tools["create_goal"]["function"]["parameters"]["properties"]
    )
    assert "goal_id" in tools["create_task"]["function"]["parameters"]["properties"]
    assert "title" in tools["create_task"]["function"]["parameters"]["properties"]
    assert (
        "created_in_context"
        in tools["create_task"]["function"]["parameters"]["properties"]
    )
    assert "goal_id" in tools["get_goal"]["function"]["parameters"]["properties"]
    assert "task_id" in tools["get_task"]["function"]["parameters"]["properties"]


# ---------------------------------------------------------------------------
# create_goal / create_task
# ---------------------------------------------------------------------------


def test_create_goal_works_and_marks_changed(paths, store: GoalsStore) -> None:
    changed: list[bool] = []
    result = create_goal(
        {"title": "Inventory sandbox", "acceptance": "list exists"},
        _ctx(paths, store, mark_task_changed=lambda: changed.append(True)),
    )
    assert result.ok is True
    assert result.error_reason is None
    goal = result.payload["goal"]
    assert goal["title"] == "Inventory sandbox"
    assert goal["status"] == "open"
    assert goal["acceptance"] == "list exists"
    assert goal["id"].startswith("g_")
    # Continuous / null user_id → no provenance (K6).
    assert "created_in_context" not in goal
    assert store.get_goal(goal["id"]) is not None
    assert changed == [True]


class _LabelUsers:
    def display_label(self, user_id: str) -> str:
        return {"jim": "Jim", "sam": "Sam"}.get(user_id, user_id)


def test_create_goal_social_populates_created_in_context(
    paths, store: GoalsStore
) -> None:
    result = create_goal(
        {"title": "Jim asked for this"},
        _ctx(
            paths,
            store,
            user_id="jim",
            moment_id="m_social1",
            extras={"users": _LabelUsers()},
        ),
    )
    assert result.ok is True
    goal = result.payload["goal"]
    ctx = goal["created_in_context"]
    assert ctx["user_id"] == "jim"
    assert ctx["goes_by"] == "Jim"
    assert ctx["moment_id"] == "m_social1"
    assert ctx["source"] == "tool"
    loaded = store.get_goal(goal["id"])
    assert loaded is not None
    assert loaded["created_in_context"]["user_id"] == "jim"


def test_create_goal_continuous_null_user_id_no_context(
    paths, store: GoalsStore
) -> None:
    result = create_goal(
        {"title": "Self-drive work"},
        _ctx(paths, store, user_id=None, moment_id="m_cont"),
    )
    assert result.ok is True
    assert "created_in_context" not in result.payload["goal"]


def test_create_goal_explicit_created_in_context(paths, store: GoalsStore) -> None:
    result = create_goal(
        {
            "title": "Override",
            "created_in_context": {"user_id": "sam", "goes_by": "Sam"},
        },
        _ctx(
            paths,
            store,
            user_id="jim",
            extras={"users": _LabelUsers()},
        ),
    )
    assert result.ok is True
    ctx = result.payload["goal"]["created_in_context"]
    assert ctx["user_id"] == "sam"
    assert ctx["goes_by"] == "Sam"


def test_create_task_social_populates_created_in_context(
    paths, store: GoalsStore
) -> None:
    g = store.create_goal("Parent")
    result = create_task(
        {"goal_id": g["id"], "title": "Do it"},
        _ctx(
            paths,
            store,
            user_id="jim",
            moment_id="m2",
            extras={"users": _LabelUsers()},
        ),
    )
    assert result.ok is True
    task = result.payload["task"]
    assert task["created_in_context"]["user_id"] == "jim"
    assert task["created_in_context"]["goes_by"] == "Jim"
    assert task["created_in_context"]["moment_id"] == "m2"


def test_create_task_continuous_null_user_id_no_context(
    paths, store: GoalsStore
) -> None:
    g = store.create_goal("Parent")
    result = create_task(
        {"goal_id": g["id"], "title": "Solo step"},
        _ctx(paths, store, user_id=None),
    )
    assert result.ok is True
    assert "created_in_context" not in result.payload["task"]


def test_create_goal_via_registry(
    registry: ToolRegistry, paths, store: GoalsStore
) -> None:
    changed: list[bool] = []
    result = registry.execute(
        "create_goal",
        {"title": "Via reg"},
        ToolContext(
            paths=paths,
            goals=store,
            mark_task_changed=lambda: changed.append(True),
        ),
    )
    assert result.ok is True
    assert result.payload["goal"]["title"] == "Via reg"
    assert changed == [True]
    assert result.ends_moment is False
    assert result.counts_as_speak is False


def test_create_goal_missing_title(paths, store: GoalsStore) -> None:
    result = create_goal({}, _ctx(paths, store))
    assert result.ok is False
    assert result.error_reason == "missing_title"


def test_create_goal_closed_rejected(paths, store: GoalsStore) -> None:
    result = create_goal(
        {"title": "X", "status": "closed"},
        _ctx(paths, store),
    )
    assert result.ok is False
    assert result.error_reason is not None
    assert result.error_reason.startswith("invalid_args:")


def test_create_goal_missing_goals(paths) -> None:
    result = create_goal({"title": "X"}, _ctx(paths, None))
    assert result.ok is False
    assert result.error_reason == "goals_not_configured"


def test_create_task_works_and_marks_changed(paths, store: GoalsStore) -> None:
    changed: list[bool] = []
    g = store.create_goal("Parent")
    result = create_task(
        {"goal_id": g["id"], "title": "First step", "notes": "n1"},
        _ctx(paths, store, mark_task_changed=lambda: changed.append(True)),
    )
    assert result.ok is True
    task = result.payload["task"]
    assert task["title"] == "First step"
    assert task["status"] == "pending"
    assert task["goal_id"] == g["id"]
    assert task["notes"] == "n1"
    assert result.payload.get("became_ready") is False
    assert store.get_task(task["id"]) is not None
    assert changed == [True]


def test_create_task_ready_enqueues_and_marks(paths, store: GoalsStore) -> None:
    wakes: list[dict[str, Any]] = []
    changed: list[bool] = []
    g = store.create_goal("G")
    result = create_task(
        {"goal_id": g["id"], "title": "Ready now", "status": "ready"},
        _ctx(
            paths,
            store,
            enqueue_wake=lambda **kw: wakes.append(kw) or "w1",
            mark_task_changed=lambda: changed.append(True),
        ),
    )
    assert result.ok is True
    assert result.payload["task"]["status"] == "ready"
    assert result.payload["became_ready"] is True
    assert len(wakes) == 1
    assert wakes[0]["kind"] == "task_ready"
    assert wakes[0]["task_id"] == result.payload["task"]["id"]
    assert wakes[0]["goal_id"] == g["id"]
    assert changed == [True]


def test_create_task_enqueue_wake_failure_after_ready_still_ok(
    paths, store: GoalsStore
) -> None:
    """Enqueue raise after durable create-as-ready must not fail the tool."""

    def boom(**_kwargs: Any) -> str:
        raise RuntimeError("wake queue down")

    g = store.create_goal("G")
    result = create_task(
        {"goal_id": g["id"], "title": "Ready now", "status": "ready"},
        _ctx(paths, store, enqueue_wake=boom),
    )
    assert result.ok is True
    assert result.payload["task"]["status"] == "ready"
    assert result.payload["became_ready"] is True
    assert result.payload.get("warning", "").startswith("task_ready_enqueue_failed:")
    assert store.get_task(result.payload["task"]["id"])["status"] == "ready"


def test_create_task_dual_path_store_hook_and_tool_enqueue_both_fire(
    tmp_path: Path,
) -> None:
    """Documented dual path on create-as-ready: both may fire; host must dedupe."""
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store_calls: list[tuple[str, str]] = []
    tool_wakes: list[dict[str, Any]] = []

    store = GoalsStore(
        paths,
        on_task_ready=lambda tid, gid: store_calls.append((tid, gid)),
    )
    g = store.create_goal("G")
    result = create_task(
        {"goal_id": g["id"], "title": "Ready now", "status": "ready"},
        _ctx(
            paths,
            store,
            enqueue_wake=lambda **kw: tool_wakes.append(kw) or "w1",
        ),
    )
    assert result.ok is True
    tid = result.payload["task"]["id"]
    assert store_calls == [(tid, g["id"])]
    assert len(tool_wakes) == 1
    assert tool_wakes[0]["kind"] == "task_ready"
    assert tool_wakes[0]["task_id"] == tid
    assert tool_wakes[0]["goal_id"] == g["id"]


def test_create_task_goal_not_found(paths, store: GoalsStore) -> None:
    result = create_task(
        {"goal_id": "g_missing", "title": "Orphan"},
        _ctx(paths, store),
    )
    assert result.ok is False
    assert result.error_reason == "goal_not_found"


def test_create_task_missing_fields(paths, store: GoalsStore) -> None:
    g = store.create_goal("G")
    r1 = create_task({"title": "No goal"}, _ctx(paths, store))
    assert r1.ok is False
    assert r1.error_reason == "missing_goal_id"
    r2 = create_task({"goal_id": g["id"]}, _ctx(paths, store))
    assert r2.ok is False
    assert r2.error_reason == "missing_title"


# ---------------------------------------------------------------------------
# list_goals / get_goal / get_task
# ---------------------------------------------------------------------------


def test_list_goals_compact_and_no_mark(paths, store: GoalsStore) -> None:
    changed: list[bool] = []
    g = store.create_goal("Alpha")
    store.create_task(g["id"], "T1", status="ready")
    store.create_task(g["id"], "T2")
    store.create_goal("Beta", status="review")

    result = list_goals(
        {},
        _ctx(paths, store, mark_task_changed=lambda: changed.append(True)),
    )
    assert result.ok is True
    assert changed == []  # read-only
    goals = result.payload["goals"]
    assert len(goals) == 2
    by_title = {x["title"]: x for x in goals}
    assert by_title["Alpha"]["task_count"] == 2
    assert len(by_title["Alpha"]["tasks"]) == 2
    assert set(by_title["Alpha"]["tasks"][0].keys()) <= {
        "id",
        "title",
        "status",
    }
    # Compact: no acceptance/created_at at top level of compact entry shape
    assert "acceptance" not in by_title["Alpha"]


def test_list_goals_status_filter(paths, store: GoalsStore) -> None:
    store.create_goal("Open one")
    store.create_goal("Review one", status="review")
    result = list_goals({"status": "review"}, _ctx(paths, store))
    assert result.ok is True
    goals = result.payload["goals"]
    assert len(goals) == 1
    assert goals[0]["title"] == "Review one"
    assert goals[0]["status"] == "review"


def test_list_goals_invalid_status(paths, store: GoalsStore) -> None:
    result = list_goals({"status": "bogus"}, _ctx(paths, store))
    assert result.ok is False
    assert result.error_reason is not None
    assert result.error_reason.startswith("invalid_args:")


def test_get_goal_full_and_no_mark(paths, store: GoalsStore) -> None:
    changed: list[bool] = []
    g = store.create_goal("Detail", acceptance="done when listed")
    t = store.create_task(g["id"], "Sub", notes="n")
    result = get_goal(
        {"goal_id": g["id"]},
        _ctx(paths, store, mark_task_changed=lambda: changed.append(True)),
    )
    assert result.ok is True
    assert changed == []
    goal = result.payload["goal"]
    assert goal["acceptance"] == "done when listed"
    assert len(goal["tasks"]) == 1
    assert goal["tasks"][0]["id"] == t["id"]
    assert goal["tasks"][0]["notes"] == "n"


def test_get_goal_not_found(paths, store: GoalsStore) -> None:
    result = get_goal({"goal_id": "g_missing"}, _ctx(paths, store))
    assert result.ok is False
    assert result.error_reason == "goal_not_found"


def test_get_task_and_no_mark(paths, store: GoalsStore) -> None:
    changed: list[bool] = []
    g = store.create_goal("G")
    t = store.create_task(g["id"], "Work", notes="hello")
    result = get_task(
        {"task_id": t["id"]},
        _ctx(paths, store, mark_task_changed=lambda: changed.append(True)),
    )
    assert result.ok is True
    assert changed == []
    task = result.payload["task"]
    assert task["id"] == t["id"]
    assert task["goal_id"] == g["id"]
    assert task["notes"] == "hello"


def test_get_task_not_found(paths, store: GoalsStore) -> None:
    result = get_task({"task_id": "t_missing"}, _ctx(paths, store))
    assert result.ok is False
    assert result.error_reason == "task_not_found"


def test_read_tools_via_registry(
    registry: ToolRegistry, paths, store: GoalsStore
) -> None:
    g = store.create_goal("R")
    t = store.create_task(g["id"], "T")
    lg = registry.execute(
        "list_goals", {}, ToolContext(paths=paths, goals=store)
    )
    assert lg.ok is True
    assert any(x["id"] == g["id"] for x in lg.payload["goals"])
    gg = registry.execute(
        "get_goal",
        {"goal_id": g["id"]},
        ToolContext(paths=paths, goals=store),
    )
    assert gg.ok is True
    assert gg.payload["goal"]["id"] == g["id"]
    gt = registry.execute(
        "get_task",
        {"task_id": t["id"]},
        ToolContext(paths=paths, goals=store),
    )
    assert gt.ok is True
    assert gt.payload["task"]["id"] == t["id"]


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
    assert result.payload.get("became_ready") is True
    assert wakes and wakes[0]["kind"] == "task_ready"
    # Ledger tools are mutate, not control — must not end the moment.
    assert result.ends_moment is False
    assert result.counts_as_speak is False


def test_update_task_uses_store_became_ready(paths, store: GoalsStore) -> None:
    """Tool keys wake on store-returned became_ready, not a separate pre-read."""
    g = store.create_goal("G")
    t = store.create_task(g["id"], "T")
    wakes: list[dict[str, Any]] = []
    result = update_task(
        {"task_id": t["id"], "status": "ready"},
        _ctx(paths, store, enqueue_wake=lambda **kw: wakes.append(kw) or "w"),
    )
    assert result.ok is True
    assert result.payload["became_ready"] is True
    assert len(wakes) == 1

    # Already ready: store reports became_ready=False → no tool enqueue.
    result2 = update_task(
        {"task_id": t["id"], "status": "ready", "notes": "noop"},
        _ctx(paths, store, enqueue_wake=lambda **kw: wakes.append(kw) or "w"),
    )
    assert result2.ok is True
    assert result2.payload["became_ready"] is False
    assert len(wakes) == 1


def test_enqueue_wake_failure_after_ready_still_ok(
    paths, store: GoalsStore
) -> None:
    """Enqueue raise after durable ready must not fail the tool (strand risk)."""

    def boom(**_kwargs: Any) -> str:
        raise RuntimeError("wake queue down")

    g = store.create_goal("G")
    t = store.create_task(g["id"], "T")
    result = update_task(
        {"task_id": t["id"], "status": "ready"},
        _ctx(paths, store, enqueue_wake=boom),
    )
    assert result.ok is True
    assert result.payload["task"]["status"] == "ready"
    assert result.payload["became_ready"] is True
    assert result.payload.get("warning", "").startswith("task_ready_enqueue_failed:")
    # Durable commit retained even though wake failed.
    assert store.get_task(t["id"])["status"] == "ready"


def test_mark_task_changed_failure_still_ok(paths, store: GoalsStore) -> None:
    def boom() -> None:
        raise RuntimeError("continue clock down")

    g = store.create_goal("G")
    t = store.create_task(g["id"], "T")
    result = update_task(
        {"task_id": t["id"], "title": "Still saved"},
        _ctx(paths, store, mark_task_changed=boom),
    )
    assert result.ok is True
    assert result.payload["task"]["title"] == "Still saved"
    assert store.get_task(t["id"])["title"] == "Still saved"


def test_dual_path_store_hook_and_tool_enqueue_both_fire(tmp_path: Path) -> None:
    """Documented dual path: both may fire; host must dedupe if both wired."""
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    store_calls: list[tuple[str, str]] = []
    tool_wakes: list[dict[str, Any]] = []

    store = GoalsStore(
        paths,
        on_task_ready=lambda tid, gid: store_calls.append((tid, gid)),
    )
    g = store.create_goal("G")
    t = store.create_task(g["id"], "T")
    result = update_task(
        {"task_id": t["id"], "status": "ready"},
        _ctx(
            paths,
            store,
            enqueue_wake=lambda **kw: tool_wakes.append(kw) or "w1",
        ),
    )
    assert result.ok is True
    assert store_calls == [(t["id"], g["id"])]
    assert len(tool_wakes) == 1
    assert tool_wakes[0]["kind"] == "task_ready"


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


def test_update_goal_mark_changed_on_success(paths, store: GoalsStore) -> None:
    """Normative: update_goal must call mark_task_changed (like update_task)."""
    changed: list[bool] = []
    g = store.create_goal("G")
    result = update_goal(
        {"goal_id": g["id"], "title": "Renamed goal"},
        _ctx(paths, store, mark_task_changed=lambda: changed.append(True)),
    )
    assert result.ok is True
    assert result.payload["goal"]["title"] == "Renamed goal"
    assert changed == [True]


def test_update_goal_mark_changed_failure_still_ok(
    paths, store: GoalsStore
) -> None:
    def boom() -> None:
        raise RuntimeError("continue clock down")

    g = store.create_goal("G")
    result = update_goal(
        {"goal_id": g["id"], "title": "Still saved"},
        _ctx(paths, store, mark_task_changed=boom),
    )
    assert result.ok is True
    assert result.payload["goal"]["title"] == "Still saved"
    assert store.get_goal(g["id"])["title"] == "Still saved"


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


def test_update_goal_force_alone_rejected(paths, store: GoalsStore) -> None:
    """force is only meaningful with a field change; alone → no_fields_to_update."""
    g = store.create_goal("G")
    result = update_goal(
        {"goal_id": g["id"], "force": True},
        _ctx(paths, store),
    )
    assert result.ok is False
    assert result.error_reason == "no_fields_to_update"
    assert store.get_goal(g["id"])["status"] == "open"
    assert g["updated_at"] == store.get_goal(g["id"])["updated_at"]


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
