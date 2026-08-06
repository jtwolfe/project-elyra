"""Tests for goals/tasks ledger store."""

from __future__ import annotations

import json
import threading

import pytest

from elyra.config import resolve_paths
from elyra.goals import GoalsStore, SOFT_CLOSE_WARNING


@pytest.fixture
def store(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return GoalsStore(paths)


def test_create_and_get_goal(store):
    g = store.create_goal("Ship ledger", acceptance="CRUD + soft close")
    assert g["id"].startswith("g_")
    assert g["title"] == "Ship ledger"
    assert g["status"] == "open"
    assert g["acceptance"] == "CRUD + soft close"
    assert g["tasks"] == []
    assert g["created_at"]
    assert g["updated_at"]

    loaded = store.get_goal(g["id"])
    assert loaded is not None
    assert loaded["title"] == "Ship ledger"
    assert store.get_goal("missing") is None


def test_list_goals_and_status_filter(store):
    a = store.create_goal("A")
    b = store.create_goal("B")
    store.update_goal(b["id"], status="review")
    store.create_goal("C", status="cancelled")

    all_g = store.list_goals()
    assert len(all_g) == 3
    # All statuses still returned when unfiltered (KD-G1).
    assert {g["status"] for g in all_g} == {"open", "review", "cancelled"}
    open_only = store.list_goals(status="open")
    assert len(open_only) == 1
    assert open_only[0]["id"] == a["id"]
    review_only = store.list_goals(status="review")
    assert len(review_only) == 1
    assert review_only[0]["id"] == b["id"]


def test_list_goals_newest_first_by_updated_at(store):
    """KD-G1: list order is newest-updated first (not create/append order)."""
    a = store.create_goal("A")
    b = store.create_goal("B")
    # Touch A so its updated_at is strictly after B's create stamp.
    store.update_goal(a["id"], title="A touched")

    ordered = store.list_goals()
    assert [g["id"] for g in ordered] == [a["id"], b["id"]]
    assert ordered[0]["updated_at"] >= ordered[1]["updated_at"]


def test_list_goals_id_tie_break_when_stamps_equal(store, tmp_path):
    """KD-G1: equal (updated_at|created_at) → stable id desc tie-break."""
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    s = GoalsStore(paths)
    stamp = "2026-01-01T00:00:00+00:00"
    doc = {
        "goals": [
            {
                "id": "g_aaa",
                "title": "older id",
                "status": "open",
                "acceptance": None,
                "created_at": stamp,
                "updated_at": stamp,
                "tasks": [],
            },
            {
                "id": "g_zzz",
                "title": "newer id",
                "status": "closed",
                "acceptance": None,
                "created_at": stamp,
                "updated_at": stamp,
                "tasks": [],
            },
            {
                "id": "g_mmm",
                "title": "mid id",
                "status": "review",
                "acceptance": None,
                "created_at": stamp,
                "updated_at": stamp,
                "tasks": [],
            },
        ]
    }
    s.store_path.parent.mkdir(parents=True, exist_ok=True)
    s.store_path.write_text(json.dumps(doc), encoding="utf-8")

    ordered = s.list_goals()
    assert [g["id"] for g in ordered] == ["g_zzz", "g_mmm", "g_aaa"]
    # Unfiltered still includes all statuses (closed/review/open).
    assert {g["status"] for g in ordered} == {"open", "review", "closed"}


def test_soft_close_warning_open_to_closed(store):
    g = store.create_goal("Close me")
    result = store.update_goal(g["id"], status="closed")
    assert result["ok"] is True
    assert result["warning"] == SOFT_CLOSE_WARNING
    assert (
        result["warning"]
        == "prefer review-work before close; set status=review first or pass force=true"
    )
    assert result["goal"]["status"] == "closed"
    assert store.goal_close_without_review == 1
    assert store.get_goal(g["id"])["status"] == "closed"


def test_force_close_bypasses_warning(store):
    g = store.create_goal("Force close")
    result = store.update_goal(g["id"], status="closed", force=True)
    assert result["ok"] is True
    assert "warning" not in result
    assert result["goal"]["status"] == "closed"
    # Still a close-without-review event (force only skips the warning key).
    assert store.goal_close_without_review == 1


def test_close_after_review_no_warning(store):
    g = store.create_goal("Review first")
    store.update_goal(g["id"], status="review")
    result = store.update_goal(g["id"], status="closed")
    assert result["ok"] is True
    assert "warning" not in result
    assert result["goal"]["status"] == "closed"
    assert store.goal_close_without_review == 0


def test_create_and_update_task(store):
    g = store.create_goal("Parent")
    t = store.create_task(g["id"], "Do thing", notes="n1")
    assert t["id"].startswith("t_")
    assert t["goal_id"] == g["id"]
    assert t["status"] == "pending"
    assert t["notes"] == "n1"

    got = store.get_task(t["id"])
    assert got is not None
    assert got["title"] == "Do thing"

    updated = store.update_task(t["id"], status="in_progress", notes="n2")
    assert updated["ok"] is True
    assert updated["task"]["status"] == "in_progress"
    assert updated["task"]["notes"] == "n2"
    assert updated["became_ready"] is False

    parent = store.get_goal(g["id"])
    assert len(parent["tasks"]) == 1
    assert parent["tasks"][0]["status"] == "in_progress"


def test_task_ready_hook_fires_on_ready_transition(store, tmp_path):
    calls: list[tuple[str, str]] = []

    paths = resolve_paths(tmp_path)
    s = GoalsStore(paths, on_task_ready=lambda tid, gid: calls.append((tid, gid)))

    g = s.create_goal("G")
    t = s.create_task(g["id"], "T")
    assert calls == []

    r1 = s.update_task(t["id"], status="ready")
    assert r1["became_ready"] is True
    assert calls == [(t["id"], g["id"])]

    # Already ready: no re-fire
    r2 = s.update_task(t["id"], status="ready")
    assert r2["became_ready"] is False
    assert calls == [(t["id"], g["id"])]

    # Leave ready and re-enter: fires again (dedupe is hook-side)
    s.update_task(t["id"], status="in_progress")
    r3 = s.update_task(t["id"], status="ready")
    assert r3["became_ready"] is True
    assert calls == [(t["id"], g["id"]), (t["id"], g["id"])]


def test_task_ready_hook_not_required(store):
    """Hook may be None; ready transition still succeeds."""
    assert store._on_task_ready is None
    g = store.create_goal("G")
    t = store.create_task(g["id"], "T")
    result = store.update_task(t["id"], status="ready")
    assert result["ok"] is True
    assert result["task"]["status"] == "ready"


def test_create_task_as_ready_fires_hook(tmp_path):
    calls: list[tuple[str, str]] = []
    paths = resolve_paths(tmp_path)
    s = GoalsStore(paths, on_task_ready=lambda tid, gid: calls.append((tid, gid)))
    g = s.create_goal("G")
    t = s.create_task(g["id"], "T", status="ready")
    assert calls == [(t["id"], g["id"])]


def test_create_goal_with_created_in_context(store):
    ctx = {
        "user_id": "jim",
        "goes_by": "Jim",
        "moment_id": "m_abc",
        "source": "tool",
    }
    g = store.create_goal("Ship with Jim", created_in_context=ctx)
    assert g["created_in_context"] == ctx
    loaded = store.get_goal(g["id"])
    assert loaded is not None
    assert loaded["created_in_context"]["user_id"] == "jim"
    assert loaded["created_in_context"]["goes_by"] == "Jim"


def test_create_goal_without_context_omits_field(store):
    g = store.create_goal("Autonomous")
    assert "created_in_context" not in g
    loaded = store.get_goal(g["id"])
    assert loaded is not None
    assert "created_in_context" not in loaded


def test_create_task_with_created_in_context_does_not_inherit(store):
    g = store.create_goal(
        "Parent",
        created_in_context={"user_id": "jim", "goes_by": "Jim"},
    )
    t = store.create_task(g["id"], "Step", notes="n")
    assert "created_in_context" not in t
    t2 = store.create_task(
        g["id"],
        "Step2",
        created_in_context={"user_id": "sam", "goes_by": "Sam", "source": "tool"},
    )
    assert t2["created_in_context"]["user_id"] == "sam"
    # Parent goal context unchanged.
    assert store.get_goal(g["id"])["created_in_context"]["user_id"] == "jim"


def test_find_task_returns_goal_and_task(store):
    g = store.create_goal(
        "G",
        created_in_context={"user_id": "jim", "goes_by": "Jim"},
    )
    t = store.create_task(g["id"], "T", status="ready")
    found = store.find_task(t["id"])
    assert found is not None
    goal, task = found
    assert goal["id"] == g["id"]
    assert goal["created_in_context"]["user_id"] == "jim"
    assert task["id"] == t["id"]
    assert store.find_task("t_missing") is None


def test_persistence_roundtrip(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    s1 = GoalsStore(paths)
    g = s1.create_goal("Persist")
    t = s1.create_task(g["id"], "Task A")

    s2 = GoalsStore(paths)
    assert s2.get_goal(g["id"])["title"] == "Persist"
    assert s2.get_task(t["id"])["title"] == "Task A"

    raw = json.loads((paths.data_dir / "goals" / "goals.json").read_text())
    assert "goals" in raw
    assert raw["goals"][0]["tasks"][0]["id"] == t["id"]


def test_update_missing_raises(store):
    with pytest.raises(KeyError, match="goal not found"):
        store.update_goal("g_missing", status="closed")
    with pytest.raises(KeyError, match="task not found"):
        store.update_task("t_missing", status="ready")
    g = store.create_goal("G")
    with pytest.raises(KeyError, match="goal not found"):
        store.create_task("g_nope", "T")


def test_invalid_status_rejected(store):
    with pytest.raises(ValueError, match="invalid goal status"):
        store.create_goal("X", status="bogus")
    g = store.create_goal("G")
    with pytest.raises(ValueError, match="invalid goal status"):
        store.update_goal(g["id"], status="nope")
    with pytest.raises(ValueError, match="invalid task status"):
        store.create_task(g["id"], "T", status="nope")


def test_create_goal_rejects_closed(store):
    """Close must go through update_goal so soft-close path always applies."""
    with pytest.raises(ValueError, match="cannot create goal with status=closed"):
        store.create_goal("Already done", status="closed")
    assert store.list_goals() == []
    assert store.goal_close_without_review == 0


def test_force_must_be_bool(store):
    g = store.create_goal("G")
    with pytest.raises(TypeError, match="force must be bool"):
        store.update_goal(g["id"], status="closed", force="yes")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="force must be bool"):
        store.update_goal(g["id"], status="closed", force=1)  # type: ignore[arg-type]
    # Goal still open — no partial close on bad force.
    assert store.get_goal(g["id"])["status"] == "open"
    assert store.goal_close_without_review == 0


def test_concurrent_creates_same_store(tmp_path):
    """RLock + unique temp: concurrent creates on one store retain all goals."""
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    s = GoalsStore(paths)
    n = 40
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            s.create_goal(f"G{i}")
        except BaseException as exc:  # noqa: BLE001 — collect for assert
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(s.list_goals()) == n
