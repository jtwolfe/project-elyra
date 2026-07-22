"""Wake queue: event fold, claim, crash recovery, task_ready dedupe."""

from __future__ import annotations

import json
from pathlib import Path

from elyra.config import resolve_paths
from elyra.presence.queue import (
    KIND_PRIORITY,
    RE_ENQUEUE_ON_RECOVER,
    REASON_CONTINUOUS_DISABLED,
    REASON_INTERRUPTED,
    REASON_REPLACED,
    WakeItem,
    WakeQueue,
    fold_events,
    priority_for_kind,
)


def _queue(tmp_path) -> WakeQueue:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return WakeQueue(paths)


def test_priority_bands():
    assert priority_for_kind("user_message") == 0
    assert priority_for_kind("wait_reply") == 0
    assert priority_for_kind("wait_timeout") == 1
    assert priority_for_kind("timer") == 2
    assert priority_for_kind("task_ready") == 3
    assert priority_for_kind("moment_continue") == 3  # same band as task_ready
    assert priority_for_kind("background") == 4
    assert KIND_PRIORITY["user_message"] == 0
    assert "moment_continue" in RE_ENQUEUE_ON_RECOVER


def test_fold_events_latest_op_and_item_body():
    events = [
        {
            "ts": "2026-01-01T00:00:00Z",
            "wake_id": "W1",
            "op": "enqueue",
            "item": {
                "id": "W1",
                "kind": "user_message",
                "priority": 0,
                "created_at": "2026-01-01T00:00:00Z",
                "payload": {"content": "hi"},
            },
        },
        {
            "ts": "2026-01-01T00:00:01Z",
            "wake_id": "W1",
            "op": "claimed",
            "moment_id": "M1",
        },
        {
            "ts": "2026-01-01T00:00:02Z",
            "wake_id": "W1",
            "op": "done",
        },
        {
            "ts": "2026-01-01T00:00:03Z",
            "wake_id": "W2",
            "op": "enqueue",
            "item": {
                "id": "W2",
                "kind": "timer",
                "priority": 2,
                "created_at": "2026-01-01T00:00:03Z",
                "payload": {"reason": "ping"},
            },
        },
        {
            "ts": "2026-01-01T00:00:04Z",
            "wake_id": "W2",
            "op": "cancelled",
            "reason": "test",
        },
    ]
    folded = fold_events(events)
    assert folded["W1"].op == "done"
    assert folded["W1"].item is not None
    assert folded["W1"].item.payload["content"] == "hi"
    assert folded["W1"].moment_id == "M1"
    assert folded["W2"].op == "cancelled"
    assert folded["W2"].reason == "test"
    assert folded["W2"].item.kind == "timer"


def test_enqueue_persists_and_pending_order(tmp_path):
    q = _queue(tmp_path)
    bg = q.enqueue("background", {"n": 1})
    user = q.enqueue("user_message", {"content": "hello"})
    timer = q.enqueue("timer", {"reason": "t"})
    pending = q.pending()
    assert [p.id for p in pending] == [user.id, timer.id, bg.id]
    assert pending[0].kind == "user_message"
    assert pending[0].priority == 0

    # Events on disk
    path = q.events_path
    assert path.is_file()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3
    ops = [json.loads(ln)["op"] for ln in lines]
    assert ops == ["enqueue", "enqueue", "enqueue"]


def test_claim_sequential_and_done(tmp_path):
    q = _queue(tmp_path)
    a = q.enqueue("user_message", {"content": "a"})
    b = q.enqueue("wait_timeout", {"wait_id": "w"})
    c = q.enqueue("background", {})

    first = q.claim("M1")
    assert first is not None
    assert first.id == a.id
    assert q.status(a.id) == "claimed"
    assert q.peek() is not None
    assert q.peek().id == b.id

    second = q.claim("M2")
    assert second is not None
    assert second.id == b.id

    q.mark_done(a.id)
    assert q.status(a.id) == "done"
    q.mark_done(b.id)

    third = q.claim("M3")
    assert third is not None
    assert third.id == c.id
    q.cancel(c.id, "host")
    assert q.status(c.id) == "cancelled"

    assert q.claim("M4") is None
    assert q.pending() == []


def test_claim_under_lock_reload_fold(tmp_path):
    """Claim + done survive re-fold from disk (new queue instance)."""
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    q1 = WakeQueue(paths)
    item = q1.enqueue("task_ready", {"task_id": "T1"})
    claimed = q1.claim("moment-1")
    assert claimed is not None
    assert claimed.id == item.id

    q2 = WakeQueue(paths)
    assert q2.status(item.id) == "claimed"
    assert q2.pending() == []
    assert q2.get(item.id) is not None
    assert q2.get(item.id).payload["task_id"] == "T1"

    q2.mark_done(item.id)
    q3 = WakeQueue(paths)
    assert q3.status(item.id) == "done"


def test_recover_claimed_cancels_user_reenqueues_timer(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    q = WakeQueue(paths)
    user = q.enqueue("user_message", {"content": "once"})
    timer = q.enqueue("timer", {"reason": "retry-me", "wake_at": "2026-01-01T00:00:00Z"})
    task = q.enqueue("task_ready", {"task_id": "T9", "goal_id": "G1"})
    wait_to = q.enqueue("wait_timeout", {"wait_id": "W"})

    assert q.claim("M-user").id == user.id
    assert q.claim("M-wait").id == wait_to.id
    assert q.claim("M-timer").id == timer.id
    assert q.claim("M-task").id == task.id
    assert q.pending() == []

    # Simulate crash: new process folds claimed-without-done
    q2 = WakeQueue(paths)
    claimed = {w.kind: w for w in q2.claimed()}
    assert set(claimed) == {"user_message", "wait_timeout", "timer", "task_ready"}

    reenqueued = q2.recover_claimed()
    assert q2.status(user.id) == "cancelled"
    assert q2.status(wait_to.id) == "cancelled"
    assert q2.status(timer.id) == "cancelled"
    assert q2.status(task.id) == "cancelled"

    # Social/wait: no re-enqueue
    pending = q2.pending()
    kinds = sorted(p.kind for p in pending)
    assert kinds == ["task_ready", "timer"]
    assert len(reenqueued) == 2
    assert {r.kind for r in reenqueued} == {"timer", "task_ready"}
    # New ids
    assert all(r.id not in {timer.id, task.id} for r in reenqueued)
    timer_clone = next(r for r in reenqueued if r.kind == "timer")
    assert timer_clone.payload["reason"] == "retry-me"
    task_clone = next(r for r in reenqueued if r.kind == "task_ready")
    assert task_clone.payload["task_id"] == "T9"

    # Events include interrupted_redelivery
    text = Path(q2.events_path).read_text(encoding="utf-8")
    assert REASON_INTERRUPTED in text


def test_task_ready_dedupe_cancels_old_pending(tmp_path):
    q = _queue(tmp_path)
    first = q.enqueue_task_ready("T1", goal_id="G", payload={"n": 1})
    assert first.kind == "task_ready"
    assert first.payload["task_id"] == "T1"
    assert len(q.pending()) == 1

    second = q.enqueue_task_ready("T1", goal_id="G", payload={"n": 2})
    assert second.id != first.id
    assert q.status(first.id) == "cancelled"
    # reason on disk
    lines = [
        json.loads(ln)
        for ln in q.events_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    cancel_ev = next(
        e for e in lines if e.get("wake_id") == first.id and e.get("op") == "cancelled"
    )
    assert cancel_ev["reason"] == REASON_REPLACED

    pending = q.pending()
    assert len(pending) == 1
    assert pending[0].id == second.id
    assert pending[0].payload["n"] == 2

    # Different task_id is independent
    other = q.enqueue_task_ready("T2")
    assert len(q.pending()) == 2
    assert {p.payload["task_id"] for p in q.pending()} == {"T1", "T2"}
    assert other.payload["task_id"] == "T2"


def test_wake_item_roundtrip_dict():
    item = WakeItem(
        id="W",
        kind="background",
        priority=4,
        created_at="2026-01-01T00:00:00Z",
        payload={"x": 1},
    )
    assert WakeItem.from_dict(item.to_dict()) == item


def test_poison_enqueue_line_does_not_abort_load(tmp_path):
    """A truncated/missing-field enqueue must not prevent loading valid wakes."""
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    q = WakeQueue(paths)
    good = q.enqueue("user_message", {"content": "ok"})
    path = q.events_path
    # Append poison line (missing kind/priority/created_at) then another good wake.
    poison = {
        "ts": "2026-01-01T00:00:00Z",
        "wake_id": "POISON",
        "op": "enqueue",
        "item": {"id": "POISON"},  # incomplete
    }
    other = {
        "ts": "2026-01-01T00:00:01Z",
        "wake_id": "W-good2",
        "op": "enqueue",
        "item": {
            "id": "W-good2",
            "kind": "background",
            "priority": 4,
            "created_at": "2026-01-01T00:00:01Z",
            "payload": {},
        },
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(poison) + "\n")
        handle.write(json.dumps(other) + "\n")

    q2 = WakeQueue(paths)  # must not raise
    pending_ids = {p.id for p in q2.pending()}
    assert good.id in pending_ids
    assert "W-good2" in pending_ids
    assert "POISON" not in pending_ids
    assert q2.get("POISON") is None


def test_fold_repairs_mismatched_item_id(tmp_path):
    """item.id != event wake_id is repaired so claim can pop the heap entry."""
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    q = WakeQueue(paths)
    path = q.events_path
    path.parent.mkdir(parents=True, exist_ok=True)
    ev = {
        "ts": "2026-01-01T00:00:00Z",
        "wake_id": "EVENT-ID",
        "op": "enqueue",
        "item": {
            "id": "ITEM-ID-DIFFERENT",
            "kind": "timer",
            "priority": 2,
            "created_at": "2026-01-01T00:00:00Z",
            "payload": {"reason": "mismatch"},
        },
    }
    path.write_text(json.dumps(ev) + "\n", encoding="utf-8")
    q2 = WakeQueue(paths)
    pending = q2.pending()
    assert len(pending) == 1
    assert pending[0].id == "EVENT-ID"
    claimed = q2.claim("M1")
    assert claimed is not None
    assert claimed.id == "EVENT-ID"


def test_task_ready_dedupe_leaves_claimed_alone(tmp_path):
    """Dedupe is enqueue-queue only; claimed same task_id is not replaced."""
    q = _queue(tmp_path)
    first = q.enqueue_task_ready("T1", payload={"n": 1})
    claimed = q.claim("M1")
    assert claimed is not None
    assert claimed.id == first.id
    assert q.status(first.id) == "claimed"

    second = q.enqueue_task_ready("T1", payload={"n": 2})
    assert second.id != first.id
    # Claimed original still claimed (not cancelled)
    assert q.status(first.id) == "claimed"
    assert q.status(second.id) == "enqueue"
    assert len(q.pending()) == 1
    assert q.pending()[0].id == second.id


def test_reject_reenqueue_nonterminal_same_id(tmp_path):
    q = _queue(tmp_path)
    item = q.enqueue("background", {})
    try:
        q.enqueue("background", {}, wake_id=item.id)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "non-terminal" in str(exc)


def test_recover_wait_reply_and_background_cancel_only(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    q = WakeQueue(paths)
    wr = q.enqueue("wait_reply", {"content": "ans"})
    bg = q.enqueue("background", {})
    assert q.claim("M1").id == wr.id
    assert q.claim("M2").id == bg.id
    q2 = WakeQueue(paths)
    re = q2.recover_claimed()
    assert re == []
    assert q2.status(wr.id) == "cancelled"
    assert q2.status(bg.id) == "cancelled"
    assert q2.pending() == []


def test_moment_continue_band_fifo_with_task_ready(tmp_path):
    """moment_continue and task_ready share band 3; FIFO by created_at."""
    q = _queue(tmp_path)
    mc = q.enqueue(
        "moment_continue",
        {"source_moment_id": "M0"},
        created_at="2026-01-01T00:00:00Z",
    )
    tr = q.enqueue(
        "task_ready",
        {"task_id": "T1"},
        created_at="2026-01-01T00:00:01Z",
    )
    pending = q.pending()
    assert [p.id for p in pending] == [mc.id, tr.id]
    assert mc.priority == tr.priority == 3
    first = q.claim("M1")
    assert first is not None and first.id == mc.id


def test_recover_claimed_reenqueues_moment_continue(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    q = WakeQueue(paths)
    mc = q.enqueue(
        "moment_continue",
        {"source_moment_id": "M-src", "source_stop_reason": "no_tools"},
    )
    assert q.claim("M1").id == mc.id
    q2 = WakeQueue(paths)
    re = q2.recover_claimed()
    assert q2.status(mc.id) == "cancelled"
    assert len(re) == 1
    assert re[0].kind == "moment_continue"
    assert re[0].id != mc.id
    assert re[0].payload["source_moment_id"] == "M-src"
    assert q2.pending()[0].id == re[0].id


def test_cancel_all_pending_of_kind_moment_continue(tmp_path):
    q = _queue(tmp_path)
    a = q.enqueue("moment_continue", {"source_moment_id": "A"})
    b = q.enqueue("moment_continue", {"source_moment_id": "B"})
    tr = q.enqueue_task_ready("T1")
    timer = q.enqueue("timer", {"reason": "keep"})
    cancelled = q.cancel_all_pending_of_kind(
        "moment_continue", REASON_CONTINUOUS_DISABLED
    )
    assert set(cancelled) == {a.id, b.id}
    assert q.status(a.id) == "cancelled"
    assert q.status(b.id) == "cancelled"
    # Other kinds untouched
    assert q.status(tr.id) == "enqueue"
    assert q.status(timer.id) == "enqueue"
    pending_kinds = {p.kind for p in q.pending()}
    assert pending_kinds == {"task_ready", "timer"}
    assert q.pending_of_kind("moment_continue") == []

    # Claimed moment_continue is not cancelled by helper (only enqueue-state).
    # Drain higher-priority leftovers so claim hits moment_continue.
    q.mark_done(tr.id)
    q.mark_done(timer.id)
    c = q.enqueue("moment_continue", {"source_moment_id": "C"})
    claimed = q.claim("M-c")
    assert claimed is not None and claimed.id == c.id
    assert q.cancel_all_pending_of_kind("moment_continue", "x") == []
    assert q.status(c.id) == "claimed"


def test_cancel_all_pending_of_kind_unknown_raises(tmp_path):
    q = _queue(tmp_path)
    try:
        q.cancel_all_pending_of_kind("not_a_kind", "x")
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "unknown wake kind" in str(exc)
