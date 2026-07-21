"""Timers and wait snapshot: schedule_due, arm_wait, timeouts, rehydrate."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from elyra.config import resolve_paths
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import (
    STATUS_FIRED,
    STATUS_PENDING,
    STATUS_SCHEDULED,
    STATUS_TIMED_OUT,
    TimerService,
    parse_utc,
)


def _svc(tmp_path) -> tuple[TimerService, WakeQueue]:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    q = WakeQueue(paths)
    return TimerService(paths, q), q


def test_parse_utc_z_and_offset():
    a = parse_utc("2026-01-01T12:00:00Z")
    b = parse_utc("2026-01-01T12:00:00+00:00")
    assert a == b
    assert a.tzinfo is not None


def test_schedule_timer_and_due_fires(tmp_path):
    svc, q = _svc(tmp_path)
    past = "2020-01-01T00:00:00Z"
    future = (datetime.now(UTC) + timedelta(days=365)).isoformat().replace(
        "+00:00", "Z"
    )

    t_past = svc.schedule_timer(past, reason="due-now", goal_id="G1")
    t_future = svc.schedule_timer(future, reason="later")
    assert t_past.status == STATUS_SCHEDULED
    assert svc.timers_path.is_file()

    fired = svc.schedule_due(now="2021-01-01T00:00:00Z")
    assert len(fired) == 1
    assert fired[0].kind == "timer"
    assert fired[0].priority == 2
    assert fired[0].payload["reason"] == "due-now"
    assert fired[0].payload["timer_id"] == t_past.id
    assert fired[0].payload["goal_id"] == "G1"

    # Future not fired
    still = svc.list_timers(status=STATUS_SCHEDULED)
    assert len(still) == 1
    assert still[0].id == t_future.id

    fired_again = svc.schedule_due(now="2021-01-01T00:00:00Z")
    assert fired_again == []

    # Wake is pending on queue
    pending = q.pending()
    assert len(pending) == 1
    assert pending[0].id == fired[0].id

    # Persist fired status
    raw = json.loads(svc.timers_path.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in raw}
    assert by_id[t_past.id]["status"] == STATUS_FIRED
    assert by_id[t_past.id]["wake_id"] == fired[0].id


def test_schedule_due_with_datetime_now(tmp_path):
    svc, q = _svc(tmp_path)
    wake_at = datetime(2020, 6, 1, tzinfo=UTC)
    svc.schedule_timer(wake_at, reason="dt")
    fired = svc.schedule_due(now=datetime(2020, 6, 2, tzinfo=UTC))
    assert len(fired) == 1
    assert q.peek().kind == "timer"


def test_cancel_timer_skips_due(tmp_path):
    svc, q = _svc(tmp_path)
    t = svc.schedule_timer("2020-01-01T00:00:00Z", reason="nope")
    svc.cancel_timer(t.id)
    assert svc.schedule_due(now="2021-01-01T00:00:00Z") == []
    assert q.pending() == []


def test_arm_wait_and_check_timeouts(tmp_path):
    svc, q = _svc(tmp_path)
    wait = svc.arm_wait(
        prompt="Pick one",
        choices=["A", "B"],
        user_id="operator",
        moment_id="M1",
        expires_at="2020-01-01T00:00:00Z",
        timeout=30.0,
    )
    assert wait.status == STATUS_PENDING
    assert svc.waits_path.is_file()
    assert svc.get_wait(wait.id) is not None

    # Not yet due relative to past? expires is 2020, now 2021 → due
    fired = svc.check_timeouts(now="2021-06-01T00:00:00Z")
    assert len(fired) == 1
    assert fired[0].kind == "wait_timeout"
    assert fired[0].priority == 1
    assert fired[0].payload["wait_id"] == wait.id
    assert fired[0].payload["moment_id"] == "M1"
    assert fired[0].payload["choices_offered"] == ["A", "B"]
    assert fired[0].payload["wait_elapsed_s"] == 30.0

    updated = svc.get_wait(wait.id)
    assert updated is not None
    assert updated.status == STATUS_TIMED_OUT
    assert updated.wake_id == fired[0].id

    # Idempotent
    assert svc.check_timeouts(now="2021-06-01T00:00:00Z") == []
    assert len(q.pending()) == 1


def test_arm_wait_with_timeout_seconds(tmp_path):
    svc, _q = _svc(tmp_path)
    wait = svc.arm_wait(
        prompt="?",
        user_id="operator",
        moment_id="M",
        timeout=0.0,
        choices=[],
    )
    # expires_at ~ now; check with future now
    future = datetime.now(UTC) + timedelta(seconds=5)
    fired = svc.check_timeouts(now=future)
    assert len(fired) == 1
    assert wait.id == fired[0].payload["wait_id"]


def test_mark_answered_and_cancel_wait(tmp_path):
    svc, q = _svc(tmp_path)
    w1 = svc.arm_wait(
        prompt="a",
        user_id="operator",
        moment_id="M",
        expires_at="2020-01-01T00:00:00Z",
    )
    w2 = svc.arm_wait(
        prompt="b",
        user_id="operator",
        moment_id="M",
        expires_at="2020-01-01T00:00:00Z",
    )
    svc.mark_wait_answered(w1.id)
    svc.cancel_wait(w2.id)
    assert svc.check_timeouts(now="2021-01-01T00:00:00Z") == []
    assert q.pending() == []


def test_rehydrate_waits_fires_expired(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    q1 = WakeQueue(paths)
    svc1 = TimerService(paths, q1)
    wait = svc1.arm_wait(
        prompt="stale",
        user_id="operator",
        moment_id="M9",
        expires_at="2019-01-01T00:00:00Z",
        choices=["x"],
    )
    # New process
    q2 = WakeQueue(paths)
    svc2 = TimerService(paths, q2)
    pending_before = svc2.list_waits(status=STATUS_PENDING)
    assert any(w.id == wait.id for w in pending_before)

    fired = svc2.rehydrate_waits(now="2020-01-01T00:00:00Z")
    assert len(fired) == 1
    assert fired[0].kind == "wait_timeout"
    assert fired[0].payload["wait_id"] == wait.id
    assert svc2.get_wait(wait.id).status == STATUS_TIMED_OUT


def test_future_wait_survives_rehydrate(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    q = WakeQueue(paths)
    svc = TimerService(paths, q)
    future = (datetime.now(UTC) + timedelta(hours=2)).isoformat().replace(
        "+00:00", "Z"
    )
    wait = svc.arm_wait(
        prompt="later",
        user_id="alice",
        moment_id="M",
        expires_at=future,
    )
    fired = svc.rehydrate_waits(now=datetime.now(UTC))
    assert fired == []
    assert svc.get_wait(wait.id).status == STATUS_PENDING
