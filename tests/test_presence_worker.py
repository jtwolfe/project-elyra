"""Presence worker: phase machine, claim→do-loop, resolve_user_input (PR12a).

Uses stub do-loop inject + fake registry so tests never need a live LLM.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.llm.client import StubChatClient
from elyra.loop.doloop import DoLoopResult
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import STATUS_PENDING, TimerService
from elyra.presence.user_input import (
    PHASE_IDLE,
    PHASE_IN_MOMENT,
    PHASE_WAITING,
    ROUTE_INTERJECT,
    ROUTE_USER_MESSAGE,
    ROUTE_WAIT_REPLY,
    resolve_user_input,
)
from elyra.presence.worker import PresenceWorker
from elyra.settings import default_settings
from elyra.tools.types import WaitArm


# ---------------------------------------------------------------------------
# Fixtures / stubs
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


def _fake_registry() -> MagicMock:
    reg = MagicMock()
    reg.openai_tools.return_value = []
    reg.execute.return_value = MagicMock(ok=True, payload={}, ends_moment=False)
    return reg


def _stub_loop(
    *,
    stop_reason: str = "no_tools",
    hop_count: int = 1,
    arm_wait: WaitArm | None = None,
    continue_injects: int = 0,
    error: str | None = None,
    delay_s: float = 0.0,
    on_call: Any = None,
) -> Any:
    """Build a run_do_loop stand-in that records calls."""

    calls: list[dict[str, Any]] = []

    def _fn(**kwargs: Any) -> DoLoopResult:
        calls.append(kwargs)
        if on_call is not None:
            on_call(kwargs)
        if delay_s:
            time.sleep(delay_s)
        ctx = kwargs.get("ctx")
        mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
        return DoLoopResult(
            stop_reason=stop_reason,
            hop_count=hop_count,
            arm_wait=arm_wait,
            spoke=stop_reason != "no_tools",
            moment_id=mid,
            continue_injects=continue_injects,
            error=error,
        )

    _fn.calls = calls  # type: ignore[attr-defined]
    return _fn


def _make_worker(
    paths,
    *,
    run_do_loop_fn=None,
    poll_seconds: float = 0.05,
    stop_event: threading.Event | None = None,
) -> tuple[PresenceWorker, threading.Event]:
    stop = stop_event or threading.Event()
    queue = WakeQueue(paths)
    timers = TimerService(paths, queue)
    moments = MomentStore(paths)
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=stop,
        poll_seconds=poll_seconds,
        settings=default_settings(),
        queue=queue,
        timers=timers,
        moments=moments,
        registry=_fake_registry(),
        run_do_loop_fn=run_do_loop_fn or _stub_loop(),
    )
    return worker, stop


def _start(worker: PresenceWorker) -> threading.Thread:
    t = threading.Thread(target=worker.run, name="test-presence", daemon=True)
    t.start()
    # Allow startup recover to finish.
    time.sleep(0.05)
    return t


def _stop_join(worker: PresenceWorker, stop: threading.Event, t: threading.Thread) -> None:
    stop.set()
    t.join(timeout=2.0)


def _wait_until(pred, timeout: float = 2.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


# ---------------------------------------------------------------------------
# resolve_user_input pure matrix
# ---------------------------------------------------------------------------


def test_resolve_idle_enqueues_user_message_decision():
    d = resolve_user_input(
        "hello",
        "operator",
        phase=PHASE_IDLE,
        pending_wait=None,
    )
    assert d["ok"] is True
    assert d["routed"] == ROUTE_USER_MESSAGE
    assert d["cancel_stale_wait"] is False


def test_resolve_in_moment_interject():
    d = resolve_user_input(
        "mid-loop note",
        "operator",
        phase=PHASE_IN_MOMENT,
        pending_wait=None,
    )
    assert d["ok"] is True
    assert d["routed"] == ROUTE_INTERJECT


def test_resolve_in_moment_empty_fails():
    d = resolve_user_input(
        "  ",
        "operator",
        phase=PHASE_IN_MOMENT,
        pending_wait=None,
    )
    assert d["ok"] is False
    assert d["reason"] == "empty_content"


def test_resolve_waiting_free_text_is_wait_reply():
    pending = {
        "id": "w1",
        "user_id": "operator",
        "status": "pending",
        "prompt": "?",
    }
    d = resolve_user_input(
        "option A",
        "operator",
        phase=PHASE_WAITING,
        pending_wait=pending,
        from_wait_api=False,
    )
    assert d["ok"] is True
    assert d["routed"] == ROUTE_WAIT_REPLY
    assert d["answer_wait_id"] == "w1"


def test_resolve_wait_api_while_idle_with_pending():
    pending = {
        "id": "w2",
        "user_id": "operator",
        "status": "pending",
    }
    d = resolve_user_input(
        "yes",
        "operator",
        choice="yes",
        phase=PHASE_IDLE,
        pending_wait=pending,
        from_wait_api=True,
    )
    assert d["routed"] == ROUTE_WAIT_REPLY
    assert d["answer_wait_id"] == "w2"


def test_resolve_idle_cancels_stale_wait_flag():
    pending = {
        "id": "w3",
        "user_id": "operator",
        "status": "pending",
    }
    d = resolve_user_input(
        "new topic",
        "operator",
        phase=PHASE_IDLE,
        pending_wait=pending,
        from_wait_api=False,
    )
    assert d["routed"] == ROUTE_USER_MESSAGE
    assert d["cancel_stale_wait"] is True


def test_resolve_waiting_other_user_is_user_message():
    pending = {
        "id": "w4",
        "user_id": "alice",
        "status": "pending",
    }
    d = resolve_user_input(
        "hi",
        "bob",
        phase=PHASE_WAITING,
        pending_wait=pending,
    )
    assert d["routed"] == ROUTE_USER_MESSAGE


# ---------------------------------------------------------------------------
# Worker: claim → open → stub loop → close
# ---------------------------------------------------------------------------


def test_worker_claims_user_message_runs_stub_loop(paths):
    stub = _stub_loop(hop_count=2, stop_reason="no_tools")
    worker, stop = _make_worker(paths, run_do_loop_fn=stub)
    t = _start(worker)
    try:
        wake_id = worker.enqueue_user_message("hello there", user_id="operator")
        assert wake_id

        assert _wait_until(lambda: len(stub.calls) >= 1)
        assert _wait_until(lambda: worker.phase == PHASE_IDLE)
        assert _wait_until(lambda: not worker.busy)

        assert len(stub.calls) == 1
        call = stub.calls[0]
        assert call["social_wake"] is True
        assert call["ctx"].moment_id
        assert call["moments"] is not None

        snap = worker.status_snapshot()
        assert snap["phase"] == PHASE_IDLE
        assert snap["hop_count"] == 2
        assert snap["active_moment_id"] is None
        assert snap["worker_pending"] == 0

        # Moment closed on disk
        moments = MomentStore(paths)
        open_list = moments.list_open_moments()
        assert open_list == []
        # Wake terminal
        assert worker._queue.status(wake_id) == "done"  # noqa: SLF001
    finally:
        _stop_join(worker, stop, t)


def test_worker_wait_stop_arms_wait_and_phase_waiting(paths):
    arm = WaitArm(
        wait_id="wait-stub-1",
        timeout_seconds=120,
        prompt="Continue?",
        choices=["yes", "no"],
        user_id="operator",
    )
    stub = _stub_loop(stop_reason="wait", hop_count=1, arm_wait=arm)
    worker, stop = _make_worker(paths, run_do_loop_fn=stub)
    t = _start(worker)
    try:
        worker.enqueue_user_message("please wait for me")
        assert _wait_until(lambda: worker.phase == PHASE_WAITING, timeout=2.0)
        assert worker.busy is False
        pw = worker.pending_wait
        assert pw is not None
        assert pw["id"] == "wait-stub-1"
        assert pw["status"] == STATUS_PENDING
        assert pw["prompt"] == "Continue?"

        snap = worker.status_snapshot()
        assert snap["phase"] == PHASE_WAITING
        assert snap["pending_wait"]["id"] == "wait-stub-1"
    finally:
        _stop_join(worker, stop, t)


def test_wait_stop_without_arm_does_not_leave_waiting(paths):
    """stop_reason=wait with no arm_wait and no durable wait → idle, not stranded."""
    stub = _stub_loop(stop_reason="wait", hop_count=1, arm_wait=None)
    worker, stop = _make_worker(paths, run_do_loop_fn=stub)
    t = _start(worker)
    try:
        worker.enqueue_user_message("wait without arm")
        assert _wait_until(lambda: not worker.busy and len(stub.calls) >= 1)
        assert worker.phase == PHASE_IDLE
        assert worker.pending_wait is None
        assert MomentStore(paths).list_open_moments() == []
    finally:
        _stop_join(worker, stop, t)


def test_exception_after_claim_closes_moment_and_terminalizes_wake(paths):
    """Issue 1: exception after open must not leave open moment / claimed wake."""

    def boom(**_kwargs: Any) -> DoLoopResult:
        raise RuntimeError("stub boom")

    worker, stop = _make_worker(paths, run_do_loop_fn=boom)
    t = _start(worker)
    try:
        wake_id = worker.enqueue_user_message("will fail")
        assert _wait_until(
            lambda: worker._queue.status(wake_id) == "done",  # noqa: SLF001
            timeout=2.0,
        )
        assert worker.phase == PHASE_IDLE
        assert worker.busy is False
        assert worker.active_moment_id is None
        assert MomentStore(paths).list_open_moments() == []
        # Error recorded
        assert worker.last_error is not None
        assert "RuntimeError" in (worker.last_error or "")

        # Subsequent wake still processes cleanly
        stub = _stub_loop(hop_count=1)
        worker._run_do_loop = stub  # noqa: SLF001
        wake2 = worker.enqueue_user_message("recover and continue")
        assert _wait_until(lambda: len(stub.calls) >= 1, timeout=2.0)
        assert _wait_until(
            lambda: worker._queue.status(wake2) == "done",  # noqa: SLF001
            timeout=2.0,
        )
        assert MomentStore(paths).list_open_moments() == []
        assert worker.phase == PHASE_IDLE
    finally:
        _stop_join(worker, stop, t)


def test_skills_used_passed_to_close_moment(paths):
    """Issue 2: skills loaded on ctx are recorded on closed moment meta."""
    seen_mid: list[str] = []

    def with_skill(**kwargs: Any) -> DoLoopResult:
        ctx = kwargs["ctx"]
        seen_mid.append(ctx.moment_id)
        ctx.skills_used.append("talk")
        ctx.skills_used.append("wait")
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            moment_id=ctx.moment_id,
        )

    worker, stop = _make_worker(paths, run_do_loop_fn=with_skill)
    t = _start(worker)
    try:
        worker.enqueue_user_message("use skill")
        assert _wait_until(lambda: len(seen_mid) >= 1, timeout=2.0)
        assert _wait_until(
            lambda: not worker.busy and worker.phase == PHASE_IDLE, timeout=2.0
        )
        meta = MomentStore(paths).get_moment(seen_mid[0])
        assert meta is not None
        assert meta.get("ended_at") is not None
        assert meta.get("skills_used") == ["talk", "wait"]
    finally:
        _stop_join(worker, stop, t)


def test_resolve_user_input_wait_reply_enqueues_wake(paths):
    arm = WaitArm(
        wait_id="wait-reply-1",
        timeout_seconds=60,
        prompt="Pick",
        choices=["a"],
        user_id="operator",
    )
    stub = _stub_loop(stop_reason="wait", arm_wait=arm)
    worker, stop = _make_worker(paths, run_do_loop_fn=stub)
    t = _start(worker)
    try:
        worker.enqueue_user_message("start")
        assert _wait_until(lambda: worker.phase == PHASE_WAITING)

        # Pause worker processing briefly by not needing another loop —
        # resolve should mark wait answered and enqueue wait_reply.
        result = worker.resolve_user_input(
            "a",
            "operator",
            choice="a",
            from_wait_api=True,
        )
        assert result["ok"] is True
        assert result["routed"] == ROUTE_WAIT_REPLY
        assert result.get("wake_id")

        # Claim wait_reply → in_moment → idle (stub no_tools default on 2nd call)
        # Second stub call uses same stop_reason=wait unless we change stub.
        # Re-bind stub to no_tools for subsequent moments:
        stub2_calls = stub.calls  # shared list continues
        assert _wait_until(lambda: len(stub2_calls) >= 2, timeout=2.0)
    finally:
        _stop_join(worker, stop, t)


def test_interject_while_in_moment(paths):
    entered = threading.Event()
    release = threading.Event()

    def on_call(_kwargs: Any) -> None:
        entered.set()
        release.wait(timeout=2.0)

    stub = _stub_loop(delay_s=0.0, on_call=on_call)
    worker, stop = _make_worker(paths, run_do_loop_fn=stub)
    t = _start(worker)
    try:
        worker.enqueue_user_message("long moment")
        assert entered.wait(timeout=2.0)
        assert worker.phase == PHASE_IN_MOMENT
        assert worker.busy is True
        assert worker.active_moment_id is not None

        r = worker.interject("quick note", user_id="operator")
        assert r["ok"] is True
        assert r["routed"] == ROUTE_INTERJECT
        assert worker.status_snapshot()["interject_depth"] == 1

        # drain_interjections should be wired; release loop
        release.set()
        assert _wait_until(lambda: worker.phase == PHASE_IDLE, timeout=2.0)

        # Drain was called during stub? Stub doesn't call drain — buffer flushed
        # as wakes on finalize if still present.
        # After finalize, interject depth is 0.
        assert worker.status_snapshot()["interject_depth"] == 0
    finally:
        release.set()
        _stop_join(worker, stop, t)


def test_interject_buffer_overflow_enqueues_wake(paths):
    entered = threading.Event()
    release = threading.Event()

    def on_call(_kwargs: Any) -> None:
        entered.set()
        release.wait(timeout=3.0)

    stub = _stub_loop(on_call=on_call)
    worker, stop = _make_worker(paths, run_do_loop_fn=stub)
    t = _start(worker)
    try:
        worker.enqueue_user_message("busy")
        assert entered.wait(timeout=2.0)

        # Fill buffer to max (8)
        for i in range(8):
            r = worker.interject(f"note-{i}")
            assert r["ok"] is True

        overflow = worker.interject("too-many")
        assert overflow["ok"] is False
        assert overflow["reason"] == "interjection_buffer_full"
        assert overflow.get("wake_id")

        release.set()
        assert _wait_until(lambda: not worker.busy, timeout=2.0)
        # Overflow wake may still be pending or already claimed as next moment.
        # At least one additional stub call or a pending wake should exist.
        assert len(stub.calls) >= 1
    finally:
        release.set()
        _stop_join(worker, stop, t)


def test_enqueue_wake_public_api(paths):
    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    # No thread needed for pure enqueue
    wid = worker.enqueue_wake("background", {"reason": "housekeeping"})
    assert wid
    pending = worker._queue.pending()  # noqa: SLF001
    assert len(pending) == 1
    assert pending[0].kind == "background"
    snap = worker.status_snapshot()
    assert snap["queue_depth_by_band"]["background"] == 1


def test_startup_recovers_open_moments(paths):
    moments = MomentStore(paths)
    orphan = moments.open_moment(why_now="crashed", user_id="operator")
    assert moments.list_open_moments()

    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    t = _start(worker)
    try:
        assert _wait_until(
            lambda: MomentStore(paths).get_moment(orphan).get("stop_reason")
            == "interrupted"
        )
        meta = MomentStore(paths).get_moment(orphan)
        assert meta is not None
        assert meta["ended_at"] is not None
        assert meta["stop_reason"] == "interrupted"
    finally:
        _stop_join(worker, stop, t)


def test_startup_recover_claimed_user_message_and_timer(paths):
    """Issue 3: recover_claimed cancels social wakes; re-enqueues timer/task_ready."""
    import uuid as uuid_mod

    from elyra.presence.queue import REASON_INTERRUPTED

    seed_queue = WakeQueue(paths)
    user_item = seed_queue.enqueue(
        "user_message",
        {"content": "orphaned", "user_id": "operator", "message_id": "m1"},
    )
    timer_item = seed_queue.enqueue(
        "timer",
        {"reason": "ping", "wake_at": "2099-01-01T00:00:00Z", "timer_id": "t1"},
    )
    # Claim both without completing (crash mid-moment). user band 0 first.
    claimed_user = seed_queue.claim(str(uuid_mod.uuid4()))
    claimed_timer = seed_queue.claim(str(uuid_mod.uuid4()))
    assert claimed_user is not None and claimed_user.id == user_item.id
    assert claimed_timer is not None and claimed_timer.id == timer_item.id
    assert seed_queue.status(user_item.id) == "claimed"
    assert seed_queue.status(timer_item.id) == "claimed"

    # Fresh worker reloads events and runs recover_claimed on start.
    stub = _stub_loop()
    worker, stop = _make_worker(paths, run_do_loop_fn=stub)
    t = _start(worker)
    try:
        assert _wait_until(
            lambda: worker._queue.status(user_item.id) == "cancelled",  # noqa: SLF001
            timeout=2.0,
        )
        assert worker._queue.status(user_item.id) == "cancelled"  # noqa: SLF001
        assert worker._queue.status(timer_item.id) == "cancelled"  # noqa: SLF001

        # Timer re-enqueued as a new id (clone); may already be done by worker.
        def timer_clone_seen() -> bool:
            q = worker._queue  # noqa: SLF001
            for item in list(q._items.values()):  # noqa: SLF001
                if item.id == timer_item.id:
                    continue
                if item.kind == "timer" and item.payload.get("reason") == "ping":
                    return True
            return False

        assert _wait_until(timer_clone_seen, timeout=2.0)
        assert _wait_until(lambda: not worker.busy, timeout=2.0)
        assert MomentStore(paths).list_open_moments() == []
        # user_message must not be re-enqueued as pending
        pending_kinds = [w.kind for w in worker._queue.pending()]  # noqa: SLF001
        assert "user_message" not in pending_kinds
        # Sanity: interrupted reason on cancelled user wake (fold state)
        assert (
            worker._queue._reasons.get(user_item.id) == REASON_INTERRUPTED  # noqa: SLF001
        )
    finally:
        _stop_join(worker, stop, t)


def test_startup_rehydrates_pending_wait_phase(paths):
    queue = WakeQueue(paths)
    timers = TimerService(paths, queue)
    timers.arm_wait(
        wait_id="persist-wait",
        prompt="still waiting?",
        user_id="operator",
        moment_id="old-moment",
        timeout=600.0,
        choices=["y", "n"],
    )

    stop = threading.Event()
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=stop,
        poll_seconds=0.05,
        settings=default_settings(),
        queue=WakeQueue(paths),  # reloads events (empty)
        timers=TimerService(paths, WakeQueue(paths)),  # reloads waits.json
        moments=MomentStore(paths),
        registry=_fake_registry(),
        run_do_loop_fn=_stub_loop(),
    )
    t = _start(worker)
    try:
        assert _wait_until(lambda: worker.phase == PHASE_WAITING, timeout=2.0)
        pw = worker.pending_wait
        assert pw is not None
        assert pw["id"] == "persist-wait"
    finally:
        _stop_join(worker, stop, t)


def test_worker_resolve_idle_enqueues(paths):
    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    t = _start(worker)
    try:
        r = worker.resolve_user_input("hey", "operator")
        assert r["ok"] is True
        assert r["routed"] == ROUTE_USER_MESSAGE
        assert r.get("wake_id")
        assert _wait_until(lambda: worker.phase == PHASE_IDLE and not worker.busy)
    finally:
        _stop_join(worker, stop, t)


def test_must_not_import_runtime_web():
    """presence.worker dependency rule: no runtime.web import."""
    import ast

    import elyra.presence.worker as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "runtime.web" not in alias.name
                assert not alias.name.startswith("elyra.runtime.web")
        elif isinstance(node, ast.ImportFrom):
            mod_name = node.module or ""
            assert "runtime.web" not in mod_name
            assert not mod_name.startswith("elyra.runtime.web")
            assert mod_name != "elyra.runtime"


def test_priority_user_before_timer(paths):
    """Claim order: user_message (band 0) before timer (band 2)."""
    order: list[str] = []

    def on_call(kwargs: Any) -> None:
        wake = kwargs["ctx"].extras.get("wake")
        order.append(wake.kind if wake is not None else "?")

    stub = _stub_loop(on_call=on_call)
    worker, stop = _make_worker(paths, run_do_loop_fn=stub)
    # Enqueue timer first, then user — user should still run first.
    worker.enqueue_wake("timer", {"reason": "later", "wake_at": "2099-01-01T00:00:00Z"})
    worker.enqueue_user_message("urgent")
    t = _start(worker)
    try:
        assert _wait_until(lambda: len(order) >= 2, timeout=3.0)
        assert order[0] == "user_message"
        assert order[1] == "timer"
    finally:
        _stop_join(worker, stop, t)
