"""GET /api/schedule — timer/wait inspect (#126 / PR2).

Hermetic: real TimerService via _ApiHarness (production store paths).
Presence poll is not auto-running — terminal transitions invoked explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.loop.continuous_policy import continuous_status_block
from tests.test_api_glass import _ApiHarness


CONTINUOUS_KEYS = frozenset(
    {
        "enabled",
        "streak",
        "max_streak",
        "cooldown_seconds",
        "last_enqueue_at",
        "last_skip_reason",
        "pending_moment_continues",
    }
)

COUNTS_KEYS = frozenset(
    {
        "timers_scheduled",
        "timers_fired",
        "timers_cancelled",
        "timers_total",
        "waits_pending",
        "waits_answered",
        "waits_timed_out",
        "waits_cancelled",
        "waits_total",
    }
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


def _future(hours: float = 24) -> str:
    return (
        datetime.now(UTC) + timedelta(hours=hours)
    ).isoformat().replace("+00:00", "Z")


def _past(hours: float = 1) -> str:
    return (
        datetime.now(UTC) - timedelta(hours=hours)
    ).isoformat().replace("+00:00", "Z")


def _zero_counts() -> dict[str, int]:
    return {k: 0 for k in COUNTS_KEYS}


@pytest.fixture
def harness(paths):
    h = _ApiHarness(paths)
    try:
        yield h
    finally:
        h.close()


def test_schedule_empty(harness: _ApiHarness) -> None:
    code, body = harness.get("/api/schedule")
    assert code == 200
    assert body["ok"] is True
    assert body["timers"] == []
    assert body["waits"] == []
    assert body["history_timers"] == []
    assert body["history_waits"] == []
    assert set(body["counts"]) == COUNTS_KEYS
    assert body["counts"] == _zero_counts()
    assert set(body["continuous"]) == CONTINUOUS_KEYS
    assert isinstance(body["server_time"], str)
    assert body["server_time"].endswith("Z")
    # Parseable UTC ISO
    datetime.fromisoformat(body["server_time"].replace("Z", "+00:00"))


def test_schedule_active_timer(harness: _ApiHarness) -> None:
    svc = harness.worker.timers
    wake = _future(48)
    t = svc.schedule_timer(wake, reason="weekly self-review", goal_id="g_test1")
    code, body = harness.get("/api/schedule")
    assert code == 200
    assert body["ok"] is True
    assert len(body["timers"]) == 1
    row = body["timers"][0]
    assert row["id"] == t.id
    assert row["status"] == "scheduled"
    assert row["reason"] == "weekly self-review"
    assert row["goal_id"] == "g_test1"
    assert row["wake_at"] == wake
    assert body["waits"] == []
    assert body["history_timers"] == []
    assert body["counts"]["timers_scheduled"] == 1
    assert body["counts"]["timers_total"] == 1


def test_schedule_arm_wait(harness: _ApiHarness) -> None:
    svc = harness.worker.timers
    w = svc.arm_wait(
        prompt="What next?",
        user_id="operator",
        moment_id="m_test",
        timeout=300,
        choices=["A", "B"],
    )
    code, body = harness.get("/api/schedule")
    assert code == 200
    assert len(body["waits"]) == 1
    row = body["waits"][0]
    assert row["id"] == w.id
    assert row["status"] == "pending"
    assert row["prompt"] == "What next?"
    assert row["user_id"] == "operator"
    assert row["moment_id"] == "m_test"
    assert row["choices"] == ["A", "B"]
    assert body["timers"] == []
    assert body["counts"]["waits_pending"] == 1
    assert body["counts"]["waits_total"] == 1


def test_schedule_fire_then_history(harness: _ApiHarness) -> None:
    svc = harness.worker.timers
    past = _past(2)
    t = svc.schedule_timer(past, reason="due soon")
    now = datetime.now(UTC)
    fired = svc.schedule_due(now=now)
    assert len(fired) == 1

    code, body = harness.get("/api/schedule")
    assert code == 200
    assert body["timers"] == []
    assert body["history_timers"] == []
    assert body["counts"]["timers_fired"] == 1
    assert body["counts"]["timers_scheduled"] == 0

    code, hist = harness.get("/api/schedule?include_history=1")
    assert code == 200
    assert len(hist["history_timers"]) == 1
    assert hist["history_timers"][0]["id"] == t.id
    assert hist["history_timers"][0]["status"] == "fired"
    assert hist["timers"] == []


def test_schedule_answer_wait_history(harness: _ApiHarness) -> None:
    svc = harness.worker.timers
    w = svc.arm_wait(
        prompt="pick",
        user_id="operator",
        moment_id="m1",
        timeout=600,
    )
    svc.mark_wait_answered(w.id)

    code, body = harness.get("/api/schedule")
    assert code == 200
    assert body["waits"] == []
    assert body["counts"]["waits_answered"] == 1
    assert body["counts"]["waits_pending"] == 0

    code, hist = harness.get("/api/schedule?include_history=1")
    assert code == 200
    assert len(hist["history_waits"]) == 1
    assert hist["history_waits"][0]["id"] == w.id
    assert hist["history_waits"][0]["status"] == "answered"


def test_schedule_timeout_wait_history(harness: _ApiHarness) -> None:
    svc = harness.worker.timers
    past = _past(1)
    w = svc.arm_wait(
        prompt="expired",
        user_id="operator",
        moment_id="m_to",
        expires_at=past,
        timeout=1.0,
    )
    timed = svc.check_timeouts(now=datetime.now(UTC))
    assert len(timed) == 1

    code, body = harness.get("/api/schedule")
    assert code == 200
    assert body["waits"] == []
    assert body["counts"]["waits_timed_out"] == 1

    code, hist = harness.get("/api/schedule?include_history=1")
    assert code == 200
    ids = [r["id"] for r in hist["history_waits"]]
    assert w.id in ids
    row = next(r for r in hist["history_waits"] if r["id"] == w.id)
    assert row["status"] == "timed_out"


def test_schedule_cancel_timer_history(harness: _ApiHarness) -> None:
    svc = harness.worker.timers
    t = svc.schedule_timer(_future(10), reason="cancel me")
    svc.cancel_timer(t.id)

    code, body = harness.get("/api/schedule")
    assert code == 200
    assert body["timers"] == []
    assert body["counts"]["timers_cancelled"] == 1

    code, hist = harness.get("/api/schedule?include_history=1")
    assert code == 200
    assert len(hist["history_timers"]) == 1
    assert hist["history_timers"][0]["id"] == t.id
    assert hist["history_timers"][0]["status"] == "cancelled"


def test_schedule_history_limit_and_sort(harness: _ApiHarness) -> None:
    svc = harness.worker.timers
    # Three terminal timers with different wake_at; history sorted DESC by wake_at
    early = (
        datetime.now(UTC) - timedelta(hours=3)
    ).isoformat().replace("+00:00", "Z")
    mid = (
        datetime.now(UTC) - timedelta(hours=2)
    ).isoformat().replace("+00:00", "Z")
    late = (
        datetime.now(UTC) - timedelta(hours=1)
    ).isoformat().replace("+00:00", "Z")
    t_early = svc.schedule_timer(early, reason="early")
    t_mid = svc.schedule_timer(mid, reason="mid")
    t_late = svc.schedule_timer(late, reason="late")
    svc.schedule_due(now=datetime.now(UTC))

    code, body = harness.get(
        "/api/schedule?include_history=1&history_limit=1"
    )
    assert code == 200
    assert len(body["history_timers"]) == 1
    # Latest wake_at first
    assert body["history_timers"][0]["id"] == t_late.id

    code, body2 = harness.get(
        "/api/schedule?include_history=1&history_limit=2"
    )
    assert code == 200
    assert len(body2["history_timers"]) == 2
    assert [r["id"] for r in body2["history_timers"]] == [t_late.id, t_mid.id]
    # early still counted
    assert body2["counts"]["timers_fired"] == 3
    assert t_early.id not in [r["id"] for r in body2["history_timers"]]


def test_schedule_primary_sort_wake_at_asc(harness: _ApiHarness) -> None:
    svc = harness.worker.timers
    later = _future(48)
    sooner = _future(12)
    t_later = svc.schedule_timer(later, reason="later")
    t_sooner = svc.schedule_timer(sooner, reason="sooner")

    code, body = harness.get("/api/schedule")
    assert code == 200
    assert len(body["timers"]) == 2
    assert body["timers"][0]["id"] == t_sooner.id
    assert body["timers"][1]["id"] == t_later.id


def test_schedule_view_all_and_status_override(harness: _ApiHarness) -> None:
    svc = harness.worker.timers
    t_sched = svc.schedule_timer(_future(5), reason="live")
    t_done = svc.schedule_timer(_past(1), reason="done")
    svc.schedule_due(now=datetime.now(UTC))
    w = svc.arm_wait(
        prompt="q", user_id="op", moment_id="m", timeout=120
    )
    svc.mark_wait_answered(w.id)

    # view=all includes all statuses (capped)
    code, all_body = harness.get("/api/schedule?view=all")
    assert code == 200
    timer_ids = {r["id"] for r in all_body["timers"]}
    assert t_sched.id in timer_ids
    assert t_done.id in timer_ids
    wait_ids = {r["id"] for r in all_body["waits"]}
    assert w.id in wait_ids

    # timer_status overrides view for timers only
    code, fired_only = harness.get(
        "/api/schedule?view=active&timer_status=fired"
    )
    assert code == 200
    assert len(fired_only["timers"]) == 1
    assert fired_only["timers"][0]["id"] == t_done.id
    # waits still follow view=active → pending only (none)
    assert fired_only["waits"] == []

    code, answered = harness.get(
        "/api/schedule?timer_status=scheduled&wait_status=answered"
    )
    assert code == 200
    assert len(answered["timers"]) == 1
    assert answered["timers"][0]["id"] == t_sched.id
    assert len(answered["waits"]) == 1
    assert answered["waits"][0]["status"] == "answered"


def test_schedule_invalid_params(harness: _ApiHarness) -> None:
    cases = [
        ("/api/schedule?view=nope", "invalid view"),
        ("/api/schedule?history_limit=x", "invalid history_limit"),
        ("/api/schedule?timer_status=bogus", "invalid timer_status"),
        ("/api/schedule?wait_status=bogus", "invalid wait_status"),
        ("/api/schedule?timer_status=pending", "invalid timer_status"),
        ("/api/schedule?wait_status=scheduled", "invalid wait_status"),
    ]
    for path, err in cases:
        code, body = harness.get(path)
        assert code == 400, path
        assert body["ok"] is False
        assert body["error"] == err


def test_schedule_continuous_shape_matches_block(harness: _ApiHarness) -> None:
    code, body = harness.get("/api/schedule")
    assert code == 200
    cont = body["continuous"]
    assert set(cont) == CONTINUOUS_KEYS
    # Same construction as status continuous (lightweight worker helper)
    expected = harness.worker.continuous_status()
    assert cont == expected
    # Keys match continuous_status_block contract
    from elyra.loop.continuous_policy import ContinuousRuntimeState
    from elyra.settings import default_settings

    sample = continuous_status_block(
        ContinuousRuntimeState(),
        default_settings().continuous,
        pending_moment_continues=0,
    )
    assert set(sample) == CONTINUOUS_KEYS
    # status_snapshot still works independently (schedule must not depend on it)
    snap = harness.worker.status_snapshot()
    assert "continuous" in snap
    assert set(snap["continuous"]) == CONTINUOUS_KEYS


def test_schedule_history_include_false_stable_empty(harness: _ApiHarness) -> None:
    svc = harness.worker.timers
    t = svc.schedule_timer(_past(1), reason="fire")
    svc.schedule_due(now=datetime.now(UTC))
    code, body = harness.get("/api/schedule?include_history=0")
    assert code == 200
    assert body["history_timers"] == []
    assert body["history_waits"] == []
    # still in counts
    assert body["counts"]["timers_fired"] >= 1
    assert t.id not in [r["id"] for r in body["timers"]]


def test_schedule_history_limit_clamped(harness: _ApiHarness) -> None:
    # history_limit > 100 clamps to 100; still 200 OK with empty history
    code, body = harness.get(
        "/api/schedule?include_history=1&history_limit=500"
    )
    assert code == 200
    assert body["history_timers"] == []
    # negative clamps to 0
    code, body2 = harness.get(
        "/api/schedule?include_history=1&history_limit=-3"
    )
    assert code == 200
    assert body2["history_timers"] == []


def test_worker_timers_property(paths) -> None:
    """KD6: public timers property mirrors queue pattern."""
    h = _ApiHarness(paths)
    try:
        assert h.worker.timers is h.worker._timers
        t = h.worker.timers.schedule_timer(_future(1), reason="prop")
        listed = h.worker.timers.list_timers()
        assert any(x.id == t.id for x in listed)
    finally:
        h.close()
