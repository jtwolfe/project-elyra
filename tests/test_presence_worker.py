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
    spoke: bool | None = None,
    tools_ran: bool = False,
    ledger_mutated: bool = False,
    model_beats: int = 1,
    channel_flood_beats: int = 0,
    last_stop_hop_was_flood: bool = False,
    work_continue_injects: int = 0,
    results: list[DoLoopResult] | None = None,
) -> Any:
    """Build a run_do_loop stand-in that records calls.

    When ``results`` is provided, each call pops the next result (last repeats).
    """

    calls: list[dict[str, Any]] = []
    result_idx = {"i": 0}

    def _fn(**kwargs: Any) -> DoLoopResult:
        calls.append(kwargs)
        if on_call is not None:
            on_call(kwargs)
        if delay_s:
            time.sleep(delay_s)
        ctx = kwargs.get("ctx")
        mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
        if results is not None and results:
            i = min(result_idx["i"], len(results) - 1)
            result_idx["i"] += 1
            base = results[i]
            return DoLoopResult(
                stop_reason=base.stop_reason,
                hop_count=base.hop_count,
                arm_wait=base.arm_wait,
                spoke=base.spoke,
                moment_id=mid or base.moment_id,
                reouter_count=base.reouter_count,
                continue_injects=base.continue_injects,
                work_continue_injects=base.work_continue_injects,
                tools_ran=base.tools_ran,
                ledger_mutated=base.ledger_mutated,
                model_beats=base.model_beats,
                channel_flood_beats=base.channel_flood_beats,
                last_stop_hop_was_flood=base.last_stop_hop_was_flood,
                error=base.error,
            )
        spoke_v = spoke if spoke is not None else (stop_reason != "no_tools")
        return DoLoopResult(
            stop_reason=stop_reason,
            hop_count=hop_count,
            arm_wait=arm_wait,
            spoke=spoke_v,
            moment_id=mid,
            continue_injects=continue_injects,
            work_continue_injects=work_continue_injects,
            tools_ran=tools_ran,
            ledger_mutated=ledger_mutated,
            model_beats=model_beats,
            channel_flood_beats=channel_flood_beats,
            last_stop_hop_was_flood=last_stop_hop_was_flood,
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
    settings=None,
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
        settings=settings or default_settings(),
        queue=queue,
        timers=timers,
        moments=moments,
        registry=_fake_registry(),
        run_do_loop_fn=run_do_loop_fn or _stub_loop(),
    )
    return worker, stop


def _open_goal(worker: PresenceWorker, title: str = "work item") -> dict:
    return worker._ensure_goals().create_goal(title)  # noqa: SLF001


def _progress_result(**overrides: Any) -> DoLoopResult:
    """DoLoopResult with non-speak progress (outer continue eligible)."""
    base = dict(
        stop_reason="no_tools",
        hop_count=1,
        spoke=False,
        tools_ran=True,
        ledger_mutated=False,
        model_beats=2,
        channel_flood_beats=0,
        last_stop_hop_was_flood=False,
    )
    base.update(overrides)
    return DoLoopResult(**base)


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
        assert overflow["routed"] == ROUTE_INTERJECT
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


def test_status_snapshot_continuous_defaults(paths):
    """Additive continuous status block (PR4 stub; default OFF)."""
    worker, _stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    snap = worker.status_snapshot()
    cont = snap["continuous"]
    assert cont["enabled"] is False
    assert cont["streak"] == 0
    assert cont["max_streak"] == 8
    assert cont["cooldown_seconds"] == 30
    assert cont["last_enqueue_at"] is None
    assert cont["last_skip_reason"] is None
    assert cont["pending_moment_continues"] == 0
    assert set(cont) >= {
        "enabled",
        "streak",
        "max_streak",
        "cooldown_seconds",
        "last_enqueue_at",
        "last_skip_reason",
        "pending_moment_continues",
    }


def test_status_snapshot_semantic_wait_defaults(paths):
    """Semantic wait block defaults ON with snappy budget from settings."""
    worker, _stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    snap = worker.status_snapshot()
    sw = snap["semantic_wait"]
    assert sw["enabled"] is True
    assert sw["max_ms"] == 15_000
    assert sw["snappy_select_max_ms"] == worker.settings.memory.semantic_select_max_ms
    assert sw["effective_select_max_ms"] == 15_000


def test_set_semantic_wait_persist_and_status(paths):
    """set_semantic_wait mutates status and writes runtime JSON."""
    from elyra.runtime.semantic_wait import (
        load_semantic_wait_runtime,
        semantic_wait_runtime_path,
    )

    worker, _stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    result = worker.set_semantic_wait(enabled=False, max_ms=8_000)
    assert result["ok"] is True
    assert result["changed"] is True
    assert result["semantic_wait"]["enabled"] is False
    assert result["semantic_wait"]["max_ms"] == 8_000
    assert result["semantic_wait"]["effective_select_max_ms"] == (
        worker.settings.memory.semantic_select_max_ms
    )

    path = semantic_wait_runtime_path(paths.data_dir)
    assert path.is_file()
    loaded = load_semantic_wait_runtime(paths.data_dir)
    assert loaded.enabled is False
    assert loaded.max_ms == 8_000

    snap = worker.status_snapshot()
    assert snap["semantic_wait"]["enabled"] is False
    assert snap["semantic_wait"]["max_ms"] == 8_000


def test_semantic_wait_seeds_from_settings_when_json_missing(paths, tmp_path):
    """Missing semantic_wait.json → MemorySettings / elyra.toml knobs."""
    from dataclasses import replace

    from elyra.settings import default_settings

    settings = replace(
        default_settings(),
        memory=replace(
            default_settings().memory,
            semantic_wait_for_select=False,
            semantic_wait_max_ms=12_000,
            semantic_select_max_ms=40,
        ),
    )
    worker, _stop = _make_worker(
        paths, run_do_loop_fn=_stub_loop(), settings=settings
    )
    sw = worker.status_snapshot()["semantic_wait"]
    assert sw["enabled"] is False
    assert sw["max_ms"] == 12_000
    assert sw["snappy_select_max_ms"] == 40
    assert sw["effective_select_max_ms"] == 40


def test_semantic_wait_rebuild_outer_overlay_contract(paths):
    """rebuild_outer replace(mem_cfg, wait from runtime) matches set_semantic_wait."""
    from dataclasses import replace

    worker, _stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_semantic_wait(enabled=False, max_ms=9_000)
    # Same overlay rebuild_outer applies under lock before compose_meal.
    with worker._lock:  # noqa: SLF001
        sw = worker._semantic_wait  # noqa: SLF001
        mem_cfg = replace(
            worker.settings.memory,
            semantic_wait_for_select=bool(sw.enabled),
            semantic_wait_max_ms=int(sw.max_ms),
        )
    assert mem_cfg.semantic_wait_for_select is False
    assert mem_cfg.semantic_wait_max_ms == 9_000
    # Settings library defaults stay unchanged until overlay.
    assert worker.settings.memory.semantic_wait_for_select is True


def test_memory_settings_with_wait_helper(paths):
    """_memory_settings_with_wait overlays runtime wait for graph/meal/tools."""
    worker, _stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_semantic_wait(enabled=False, max_ms=11_000)
    overlaid = worker._memory_settings_with_wait()  # noqa: SLF001
    assert overlaid.semantic_wait_for_select is False
    assert overlaid.semantic_wait_max_ms == 11_000
    # Bare settings unchanged.
    assert worker.settings.memory.semantic_wait_for_select is True
    assert worker.settings.memory.semantic_wait_max_ms == 15_000
    # Status applies_to lists long-path sites including deferred recalls (PR1b).
    sw = worker.status_snapshot()["semantic_wait"]
    assert "meal_select" in sw["applies_to"]
    assert "traverse_start" in sw["applies_to"]
    assert "traverse_step_semantic" in sw["applies_to"]
    assert "http_neighbors_opt_in" in sw["applies_to"]
    assert "speak_recalls_deferred" in sw["applies_to"]


def test_deferred_recalls_enqueue_drop_new_and_idle_drain(paths, tmp_path):
    """PR1b: queue depth 32 drop-new; idle drain uses wait ceiling + metrics."""
    from dataclasses import replace

    from elyra.memory.config import (
        EDGE_RECALLS_DEFERRED_QUEUE_DEPTH_DEFAULT,
        MemorySettings,
        semantic_ann_deadline_ms,
    )
    from elyra.memory.edges import open_edge_store
    from elyra.memory.embed.mock import MockEmbedder
    from elyra.memory.index import ScoredAtom
    from elyra.memory.store import open_memory_store
    from elyra.memory.types import Atom, new_atom_id
    from elyra.memory.weights import EDGE_RECALLS

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            backend="jsonl",
            durable_edges_enabled=True,
            semantic_enabled=True,
            embed_enabled=True,
            semantic_wait_for_select=True,
            semantic_wait_max_ms=15_000,
            edge_recalls_inline=False,
        ),
    )
    worker, _stop = _make_worker(paths, run_do_loop_fn=_stub_loop(), settings=settings)

    # Open stores on worker and seed a past speak for ANN hits.
    store = worker._ensure_memory_store()  # noqa: SLF001
    assert store is not None
    past = Atom(
        atom_id="a_past_recall",
        t_start="2026-07-01T00:00:00Z",
        kind="speak",
        content_text="alpha memory",
        content_ref="inline",
        moment_id="m_past",
        embedding_status="ready",
    )
    store.put_atom(past)

    # Enqueue gates: durable + semantic on → queued.
    assert worker.enqueue_deferred_recalls(
        src_atom_id="a_src1", spoken_text="remember alpha"
    )
    assert worker._recalls_deferred_queued == 1  # noqa: SLF001
    assert len(worker._deferred_recalls_jobs) == 1  # noqa: SLF001

    # Drop-new at depth 32.
    worker._deferred_recalls_jobs.clear()  # noqa: SLF001
    worker._recalls_deferred_queued = 0  # noqa: SLF001
    worker._deferred_recalls_depth = EDGE_RECALLS_DEFERRED_QUEUE_DEPTH_DEFAULT  # noqa: SLF001
    for i in range(EDGE_RECALLS_DEFERRED_QUEUE_DEPTH_DEFAULT):
        assert worker.enqueue_deferred_recalls(
            src_atom_id=f"a_{i}", spoken_text=f"text {i}"
        )
    assert (
        worker.enqueue_deferred_recalls(src_atom_id="a_overflow", spoken_text="drop me")
        is False
    )
    assert worker._recalls_deferred_dropped == 1  # noqa: SLF001
    assert len(worker._deferred_recalls_jobs) == EDGE_RECALLS_DEFERRED_QUEUE_DEPTH_DEFAULT  # noqa: SLF001

    # Idle drain one job with mocked index/embedder under wait ceiling.
    worker._deferred_recalls_jobs.clear()  # noqa: SLF001
    worker._recalls_deferred_dropped = 0  # noqa: SLF001
    worker.enqueue_deferred_recalls(src_atom_id="a_src_drain", spoken_text="recall past")

    class _FakeIndex:
        def search(self, query, **kwargs):
            return [
                ScoredAtom(atom_id=past.atom_id, score=0.91, atom=past),
            ]

    worker._embedding_index = _FakeIndex()  # noqa: SLF001
    worker._embedder = MockEmbedder()  # noqa: SLF001
    worker._embedder_state = "warm"  # noqa: SLF001
    # edge store already open via ensure
    assert worker._ensure_edge_store() is not None  # noqa: SLF001

    mem_cfg = worker._memory_settings_with_wait()  # noqa: SLF001
    assert semantic_ann_deadline_ms(mem_cfg, "recalls") == 15_000

    worker._idle_deferred_recalls()  # noqa: SLF001
    assert worker._recalls_deferred_ok == 1  # noqa: SLF001
    assert len(worker._deferred_recalls_jobs) == 0  # noqa: SLF001
    es = worker._edge_store  # noqa: SLF001
    edges = es.list_edges_from("a_src_drain", kinds=[EDGE_RECALLS])
    assert len(edges) == 1
    assert edges[0].dst_atom_id == past.atom_id

    # Status surfaces deferred metrics.
    mem_block = worker.status_snapshot()["memory"]
    rd = mem_block["recalls_deferred"]
    assert rd["ok"] >= 1
    assert rd["queue_depth_max"] == EDGE_RECALLS_DEFERRED_QUEUE_DEPTH_DEFAULT
    assert "pending" in rd


def test_deferred_recalls_soft_skip_cold_on_drain(paths):
    """Idle drain soft-skips cold encoder; metric recalls_skipped{encoder_cold}."""
    from dataclasses import replace

    from elyra.memory.config import MemorySettings

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            backend="jsonl",
            durable_edges_enabled=True,
            semantic_enabled=True,
            semantic_wait_for_select=True,
            semantic_wait_max_ms=15_000,
        ),
    )
    worker, _stop = _make_worker(paths, run_do_loop_fn=_stub_loop(), settings=settings)
    worker._ensure_memory_store()  # noqa: SLF001
    worker._ensure_edge_store()  # noqa: SLF001
    worker.enqueue_deferred_recalls(src_atom_id="a_c", spoken_text="hello cold")

    class _Cold:
        def health(self):
            return {"ok": False, "reason": "cold"}

        def encode_text(self, text: str):
            raise AssertionError("must not encode when cold")

    class _Idx:
        def search(self, *a, **k):
            return []

    worker._embedder = _Cold()  # noqa: SLF001
    worker._embedder_state = "absent"  # noqa: SLF001
    worker._embedding_index = _Idx()  # noqa: SLF001
    worker._idle_deferred_recalls()  # noqa: SLF001
    assert worker._recalls_deferred_ok == 0  # noqa: SLF001
    assert worker._recalls_skipped.get("encoder_cold", 0) >= 1  # noqa: SLF001


def test_why_now_moment_continue():
    from elyra.presence.queue import WakeItem
    from elyra.presence.worker import _why_now

    wake = WakeItem(
        id="W1",
        kind="moment_continue",
        priority=3,
        created_at="2026-01-01T00:00:00Z",
        payload={"source_moment_id": "M-abc", "source_stop_reason": "no_tools"},
    )
    assert _why_now(wake) == "continue work (from moment M-abc)"
    wake2 = WakeItem(
        id="W2",
        kind="moment_continue",
        priority=3,
        created_at="2026-01-01T00:00:00Z",
        payload={},
    )
    assert _why_now(wake2) == "continue work (from moment ?)"


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


# ---------------------------------------------------------------------------
# Continuous finalize: moment_continue enqueue (PR6)
# ---------------------------------------------------------------------------


def _finalize_direct(
    worker: PresenceWorker,
    *,
    wake_kind: str = "user_message",
    payload: dict | None = None,
    result: DoLoopResult | None = None,
) -> tuple[str, Any]:
    """Open a synthetic moment, finalize with crafted DoLoopResult (no thread)."""
    import uuid as uuid_mod

    from elyra.presence.queue import WakeItem

    mid = "m-test-" + uuid_mod.uuid4().hex[:10]
    wake_id = "wake-" + uuid_mod.uuid4().hex[:10]
    wake = WakeItem(
        id=wake_id,
        kind=wake_kind,
        priority=0 if wake_kind in ("user_message", "wait_reply") else 3,
        created_at="2026-01-01T00:00:00Z",
        payload=payload or {"content": "hi", "user_id": "operator"},
    )
    # Seed queue so mark_done succeeds.
    worker._queue.enqueue(  # noqa: SLF001
        wake.kind, dict(wake.payload), wake_id=wake.id, created_at=wake.created_at
    )
    worker._queue.claim(mid)  # noqa: SLF001
    worker._moments.open_moment(  # noqa: SLF001
        why_now="test",
        user_id="operator",
        wake_id=wake.id,
        moment_id=mid,
    )
    res = result or _progress_result()
    worker._finalize_moment(wake, mid, res)  # noqa: SLF001
    return mid, wake


def test_finalize_enqueues_moment_continue_with_progress(paths):
    """Continuous ON + tools_ran + open work → moment_continue (not task_ready)."""
    worker, _ = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_continuous_enabled(True)
    _open_goal(worker)
    # Avoid cooldown from prior enqueues; zero cooldown for unit speed.
    worker.settings = default_settings()  # frozen continuous.cooldown still 30
    # Force cooldown elapsed by leaving last_enqueue_at None.
    assert worker._continuous.last_enqueue_at is None  # noqa: SLF001

    mid, _wake = _finalize_direct(
        worker,
        wake_kind="task_ready",
        payload={"task_id": "t1", "goal_id": "g1"},
        result=_progress_result(tools_ran=True),
    )
    pending = worker._queue.pending()  # noqa: SLF001
    kinds = [p.kind for p in pending]
    assert "moment_continue" in kinds
    assert "task_ready" not in kinds  # never re-arm from continuous
    mc = next(p for p in pending if p.kind == "moment_continue")
    assert mc.payload["source_moment_id"] == mid
    assert mc.payload["source_wake_kind"] == "task_ready"
    assert mc.payload["source_stop_reason"] == "no_tools"
    assert worker._continuous.last_continue_wake_id == mc.id  # noqa: SLF001
    assert worker._continuous.last_enqueue_at is not None  # noqa: SLF001
    snap = worker.status_snapshot()["continuous"]
    assert snap["enabled"] is True
    assert snap["pending_moment_continues"] == 1


def test_finalize_honest_exit_when_ledger_audited(paths):
    """Option A: continuous ON + open work + tools_ran + ledger_audited + no_tools → no MC."""
    worker, _ = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_continuous_enabled(True)
    _open_goal(worker)
    assert worker._continuous.last_enqueue_at is None  # noqa: SLF001

    _finalize_direct(
        worker,
        wake_kind="task_ready",
        payload={"task_id": "t1", "goal_id": "g1"},
        result=_progress_result(tools_ran=True, ledger_audited=True),
    )
    assert worker._queue.pending_of_kind("moment_continue") == []  # noqa: SLF001
    assert worker._continuous.last_skip_reason == "honest_exit"  # noqa: SLF001
    assert worker._continuous.last_enqueue_at is None  # noqa: SLF001
    snap = worker.status_snapshot()["continuous"]
    assert snap["last_skip_reason"] == "honest_exit"
    assert snap["pending_moment_continues"] == 0


def test_finalize_still_enqueues_when_not_ledger_audited(paths):
    """ledger_audited=False + progress + open work → still enqueues moment_continue."""
    worker, _ = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_continuous_enabled(True)
    _open_goal(worker)

    _finalize_direct(
        worker,
        wake_kind="task_ready",
        payload={"task_id": "t1", "goal_id": "g1"},
        result=_progress_result(tools_ran=True, ledger_audited=False),
    )
    assert len(worker._queue.pending_of_kind("moment_continue")) == 1  # noqa: SLF001
    assert worker._continuous.last_skip_reason is None  # noqa: SLF001


def test_finalize_speak_only_no_enqueue(paths):
    """K15: spoke-only (tools_ran False) never outer-continues."""
    worker, _ = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_continuous_enabled(True)
    _open_goal(worker)
    _finalize_direct(
        worker,
        wake_kind="user_message",
        result=_progress_result(
            tools_ran=False,
            ledger_mutated=False,
            spoke=True,
        ),
    )
    kinds = [p.kind for p in worker._queue.pending()]  # noqa: SLF001
    assert "moment_continue" not in kinds
    assert worker._continuous.last_skip_reason in {  # noqa: SLF001
        "no_progress",
        "pure_social",
    }


def test_finalize_pending_task_ready_skips_moment_continue(paths):
    """Prefer *pending* task_ready only — skip moment_continue, never re-arm."""
    worker, _ = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_continuous_enabled(True)
    _open_goal(worker)
    # Pre-seed a pending task_ready (as ledger hook would).
    worker._queue.enqueue_task_ready("t_ready", goal_id="g1")  # noqa: SLF001
    before = [
        p.id for p in worker._queue.pending_of_kind("task_ready")  # noqa: SLF001
    ]
    assert len(before) == 1

    _finalize_direct(
        worker,
        wake_kind="user_message",
        result=_progress_result(tools_ran=True, ledger_mutated=True),
    )
    after_tr = worker._queue.pending_of_kind("task_ready")  # noqa: SLF001
    after_mc = worker._queue.pending_of_kind("moment_continue")  # noqa: SLF001
    assert len(after_tr) == 1
    assert after_tr[0].id == before[0]  # same wake — not replaced/re-armed
    assert after_mc == []
    assert worker._continuous.last_skip_reason == "pending_task_ready"  # noqa: SLF001


def test_finalize_never_calls_enqueue_task_ready(paths):
    """K4/K16: continuous finalize must not invent task_ready wakes."""
    worker, _ = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_continuous_enabled(True)
    # Open goal with a ready task still in ledger but NO pending wake.
    g = _open_goal(worker)
    worker._ensure_goals().create_task(  # noqa: SLF001
        g["id"], "do it", status="ready"
    )
    # Drain any task_ready the store hook may have enqueued so the ledger
    # has ready work but continuous must NOT backstop re-arm.
    for item in list(worker._queue.pending_of_kind("task_ready")):  # noqa: SLF001
        worker._queue.cancel(item.id, "test_drain")  # noqa: SLF001

    _finalize_direct(
        worker,
        wake_kind="task_ready",
        payload={"task_id": "x", "goal_id": g["id"]},
        result=_progress_result(tools_ran=True),
    )
    # Progress gates pass → moment_continue OK; task_ready must stay empty.
    assert worker._queue.pending_of_kind("task_ready") == []  # noqa: SLF001
    assert len(worker._queue.pending_of_kind("moment_continue")) == 1  # noqa: SLF001


def test_finalize_flood_skips_and_starts_cooldown(paths):
    """Flood thrash → no enqueue; start_cooldown advances last_enqueue_at."""
    worker, _ = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_continuous_enabled(True)
    _open_goal(worker)
    assert worker._continuous.last_enqueue_at is None  # noqa: SLF001

    _finalize_direct(
        worker,
        wake_kind="task_ready",
        payload={"task_id": "t1"},
        result=_progress_result(
            tools_ran=True,
            model_beats=2,
            channel_flood_beats=2,  # majority flood
            last_stop_hop_was_flood=False,
        ),
    )
    assert worker._queue.pending_of_kind("moment_continue") == []  # noqa: SLF001
    assert worker._continuous.last_skip_reason == "flood"  # noqa: SLF001
    assert worker._continuous.last_enqueue_at is not None  # noqa: SLF001


def test_finalize_cooldown_blocks_second_enqueue(paths):
    """Second finalize within cooldown_seconds does not stack continues."""
    worker, _ = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_continuous_enabled(True)
    _open_goal(worker)

    _finalize_direct(
        worker,
        wake_kind="task_ready",
        payload={"task_id": "t1"},
        result=_progress_result(tools_ran=True),
    )
    assert len(worker._queue.pending_of_kind("moment_continue")) == 1  # noqa: SLF001

    # Cancel pending so dedupe gate is not the blocker — cooldown is.
    for item in worker._queue.pending_of_kind("moment_continue"):  # noqa: SLF001
        worker._queue.cancel(item.id, "test")  # noqa: SLF001

    _finalize_direct(
        worker,
        wake_kind="task_ready",
        payload={"task_id": "t2"},
        result=_progress_result(tools_ran=True),
    )
    assert worker._queue.pending_of_kind("moment_continue") == []  # noqa: SLF001
    assert worker._continuous.last_skip_reason == "cooldown"  # noqa: SLF001


def test_streak_increments_on_moment_continue_and_resets_on_user(paths):
    """Streak +1 on moment_continue finalize; user claim resets to 0."""
    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_continuous_enabled(True)
    _open_goal(worker)

    # Increment: finalize a moment_continue wake with progress.
    worker._continuous.streak = 0  # noqa: SLF001
    worker._continuous.last_enqueue_at = None  # noqa: SLF001
    _finalize_direct(
        worker,
        wake_kind="moment_continue",
        payload={"source_moment_id": "prior", "streak": 0},
        result=_progress_result(tools_ran=True),
    )
    assert worker._continuous.streak == 1  # noqa: SLF001
    # Drain any pending continue so it cannot re-run under the live worker.
    for item in worker._queue.pending_of_kind("moment_continue"):  # noqa: SLF001
        worker._queue.cancel(item.id, "test")  # noqa: SLF001

    # Speak-only stub: no outer re-enqueue after user moment (keeps streak stable).
    stub = _stub_loop(tools_ran=False, spoke=True, ledger_mutated=False)
    worker._run_do_loop = stub  # noqa: SLF001
    worker._continuous.streak = 4  # noqa: SLF001
    worker._continuous.last_enqueue_at = None  # noqa: SLF001

    t = _start(worker)
    try:
        worker.enqueue_user_message("hello again")
        assert _wait_until(lambda: len(stub.calls) >= 1, timeout=2.0)
        assert _wait_until(lambda: not worker.busy, timeout=2.0)
        # User-band claim resets streak (must stay 0 — no moment_continue ran).
        assert worker._continuous.streak == 0  # noqa: SLF001
        assert worker._queue.pending_of_kind("moment_continue") == []  # noqa: SLF001
        assert stub.calls[0].get("continuous_enabled") is True
        assert stub.calls[0].get("wake_kind") == "user_message"
        assert stub.calls[0].get("has_open_goals_slice") is True
    finally:
        _stop_join(worker, stop, t)


def test_streak_not_incremented_when_continuous_disabled_mid_flight(paths):
    """Issue 1: OFF mid-flight then moment_continue finalize must leave streak 0."""
    worker, _ = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_continuous_enabled(True)
    _open_goal(worker)
    worker._continuous.streak = 0  # noqa: SLF001

    # Simulate toggle OFF while a moment_continue is in flight (after claim).
    worker.set_continuous_enabled(False)
    assert worker._continuous.streak == 0  # noqa: SLF001
    assert worker._continuous.enabled is False  # noqa: SLF001

    _finalize_direct(
        worker,
        wake_kind="moment_continue",
        payload={"source_moment_id": "midflight", "streak": 0},
        result=_progress_result(tools_ran=True),
    )
    assert worker._continuous.streak == 0  # noqa: SLF001
    assert worker._queue.pending_of_kind("moment_continue") == []  # noqa: SLF001


def test_set_continuous_off_cancels_pending_moment_continues(paths):
    """Toggle OFF cancels only moment_continue; leaves task_ready untouched."""
    worker, _ = _make_worker(paths, run_do_loop_fn=_stub_loop())
    worker.set_continuous_enabled(True)
    worker._queue.enqueue(  # noqa: SLF001
        "moment_continue",
        {"source_moment_id": "A", "streak": 0},
    )
    worker._queue.enqueue(  # noqa: SLF001
        "moment_continue",
        {"source_moment_id": "B", "streak": 1},
    )
    tr = worker._queue.enqueue_task_ready("t_keep", goal_id="g1")  # noqa: SLF001
    worker._continuous.streak = 3  # noqa: SLF001

    out = worker.set_continuous_enabled(False)
    assert out["ok"] is True
    assert out["enabled"] is False
    assert len(out["cancelled_moment_continues"]) == 2
    assert worker._queue.pending_of_kind("moment_continue") == []  # noqa: SLF001
    assert worker._queue.status(tr.id) == "enqueue"  # noqa: SLF001
    assert worker._continuous.streak == 0  # noqa: SLF001
    assert worker._continuous.enabled is False  # noqa: SLF001
    # Persistence for PR7 path
    from elyra.loop.continuous_policy import load_continuous_runtime

    reloaded = load_continuous_runtime(
        paths.data_dir, defaults=default_settings().continuous
    )
    assert reloaded.enabled is False


def test_run_do_loop_wired_with_continuous_and_wake_kind(paths):
    """Presence passes wake_kind / continuous_enabled / has_open_goals_slice."""
    stub = _stub_loop(tools_ran=True)
    worker, stop = _make_worker(paths, run_do_loop_fn=stub)
    worker.set_continuous_enabled(True)
    _open_goal(worker)
    # Prevent outer chain from racing extra moments during assert.
    worker.settings = __import__("dataclasses").replace(
        worker.settings,
        continuous=__import__("dataclasses").replace(
            worker.settings.continuous, cooldown_seconds=0, enabled=True
        ),
    )
    # Re-sync runtime flag after settings replace (set_continuous already True).
    worker._continuous.enabled = True  # noqa: SLF001
    worker._continuous.last_enqueue_at = None  # noqa: SLF001

    t = _start(worker)
    try:
        worker.enqueue_user_message("work please")
        assert _wait_until(lambda: len(stub.calls) >= 1, timeout=2.0)
        call = stub.calls[0]
        assert call["wake_kind"] == "user_message"
        assert call["continuous_enabled"] is True
        assert call["has_open_goals_slice"] is True
        assert call["social_wake"] is True
        # Wait until first moment finishes; may claim moment_continue next.
        assert _wait_until(lambda: not worker.busy or len(stub.calls) >= 2, timeout=2.0)
    finally:
        _stop_join(worker, stop, t)


def test_continuous_off_never_enqueues(paths):
    """Default continuous OFF: progress + open work still no moment_continue."""
    worker, _ = _make_worker(paths, run_do_loop_fn=_stub_loop())
    assert worker._continuous.enabled is False  # noqa: SLF001
    _open_goal(worker)
    _finalize_direct(
        worker,
        wake_kind="task_ready",
        payload={"task_id": "t1"},
        result=_progress_result(tools_ran=True),
    )
    assert worker._queue.pending_of_kind("moment_continue") == []  # noqa: SLF001

def test_rebuild_outer_injects_goals_catalog_and_bias(paths):
    """rebuild_outer passes skill catalog, bias, and goals into assemble_outer_meal."""
    from elyra.goals import GoalsStore
    from elyra.loop.orient_slice import BIAS_TALK

    store = GoalsStore(paths)
    goal = store.create_goal("Ship orient slices", acceptance="meal has goals")
    store.create_task(goal["id"], "Wire rebuild_outer", status="ready")

    meals: list[list[dict[str, Any]]] = []

    def capture(**kwargs: Any) -> DoLoopResult:
        rebuild = kwargs["rebuild_outer"]
        meal = rebuild()
        meals.append(meal)
        ctx = kwargs["ctx"]
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            moment_id=ctx.moment_id,
        )

    worker, stop = _make_worker(paths, run_do_loop_fn=capture)
    # Inject goals store so create above is visible (same paths; re-read ok).
    worker._goals = store  # noqa: SLF001
    t = _start(worker)
    try:
        worker.enqueue_user_message("hello orient")
        assert _wait_until(lambda: len(meals) >= 1, timeout=2.0)
        assert _wait_until(lambda: not worker.busy, timeout=2.0)
        orient_body = meals[0][-1]["content"]
        assert "Ship orient slices" in orient_body
        assert "Wire rebuild_outer" in orient_body
        assert BIAS_TALK in orient_body
        # Catalog from bundled skills — exact bullet shape, not incidental text.
        # talk is alphabetically late; soft budget (800) must still fit the full list.
        assert "- talk:" in orient_body
        assert "{{GOALS}}" not in orient_body
        assert "{{SKILL_CATALOG}}" not in orient_body
        assert "{{SKILL_BIAS}}" not in orient_body
        # Held SkillCatalog is reused and injected into tool context extras.
        assert worker._ensure_skills() is worker._ensure_skills()  # noqa: SLF001
    finally:
        _stop_join(worker, stop, t)

def test_tool_context_extras_skills_is_worker_catalog(paths):
    """install_skill reloads the same SkillCatalog rebuild_outer formats."""
    seen: list[Any] = []

    def capture(**kwargs: Any) -> DoLoopResult:
        ctx = kwargs["ctx"]
        seen.append(ctx.extras.get("skills"))
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            moment_id=ctx.moment_id,
        )

    worker, stop = _make_worker(paths, run_do_loop_fn=capture)
    t = _start(worker)
    try:
        worker.enqueue_user_message("check skills extras")
        assert _wait_until(lambda: len(seen) >= 1, timeout=2.0)
        assert seen[0] is not None
        assert seen[0] is worker._ensure_skills()  # noqa: SLF001
    finally:
        _stop_join(worker, stop, t)

def test_rebuild_outer_rereads_goals_each_call(paths):
    """Every rebuild_outer re-reads goals (fresh slice, not cached at open)."""
    from elyra.goals import GoalsStore

    store = GoalsStore(paths)
    meals: list[str] = []

    def capture(**kwargs: Any) -> DoLoopResult:
        rebuild = kwargs["rebuild_outer"]
        first = rebuild()[-1]["content"]
        # Mutate ledger mid-moment, then rebuild again.
        store.create_goal("Mid-moment goal")
        second = rebuild()[-1]["content"]
        meals.extend([first, second])
        ctx = kwargs["ctx"]
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            moment_id=ctx.moment_id,
        )

    worker, stop = _make_worker(paths, run_do_loop_fn=capture)
    worker._goals = store  # noqa: SLF001
    t = _start(worker)
    try:
        worker.enqueue_user_message("check reread")
        assert _wait_until(lambda: len(meals) >= 2, timeout=2.0)
        assert "Mid-moment goal" not in meals[0]
        assert "Mid-moment goal" in meals[1]
    finally:
        _stop_join(worker, stop, t)

def test_rebuild_outer_task_ready_bias(paths):
    """E2a: task_ready + empty ledger → BIAS_REST (payload ids do not force do-work)."""
    from elyra.loop.orient_slice import BIAS_DO_WORK, BIAS_REST

    meals: list[str] = []

    def capture(**kwargs: Any) -> DoLoopResult:
        rebuild = kwargs["rebuild_outer"]
        meals.append(rebuild()[-1]["content"])
        ctx = kwargs["ctx"]
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            moment_id=ctx.moment_id,
        )

    worker, stop = _make_worker(paths, run_do_loop_fn=capture)
    t = _start(worker)
    try:
        worker.enqueue_wake(
            "task_ready",
            {"task_id": "t_xyz", "goal_id": "g_xyz"},
        )
        assert _wait_until(lambda: len(meals) >= 1, timeout=2.0)
        assert BIAS_REST in meals[0]
        assert BIAS_DO_WORK not in meals[0]
    finally:
        _stop_join(worker, stop, t)


def test_rebuild_outer_task_ready_seeded_ready_task_bias(paths):
    """E2b: task_ready + open goal with ready task → BIAS_DO_WORK."""
    from elyra.goals import GoalsStore
    from elyra.loop.orient_slice import BIAS_DO_WORK

    store = GoalsStore(paths)
    goal = store.create_goal("Ship ready work", acceptance="task done")
    task = store.create_task(goal["id"], "Act on ready task", status="ready")

    meals: list[str] = []

    def capture(**kwargs: Any) -> DoLoopResult:
        rebuild = kwargs["rebuild_outer"]
        meals.append(rebuild()[-1]["content"])
        ctx = kwargs["ctx"]
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            moment_id=ctx.moment_id,
        )

    worker, stop = _make_worker(paths, run_do_loop_fn=capture)
    worker._goals = store  # noqa: SLF001
    t = _start(worker)
    try:
        worker.enqueue_wake(
            "task_ready",
            {"task_id": task["id"], "goal_id": goal["id"]},
        )
        assert _wait_until(lambda: len(meals) >= 1, timeout=2.0)
        assert BIAS_DO_WORK in meals[0]
    finally:
        _stop_join(worker, stop, t)


def test_rebuild_outer_background_ready_task_bias(paths):
    """E3: background wake + ready task on ledger → BIAS_DO_WORK (ledger-aware)."""
    from elyra.goals import GoalsStore
    from elyra.loop.orient_slice import BIAS_BACKGROUND, BIAS_DO_WORK

    store = GoalsStore(paths)
    goal = store.create_goal("Background ready work", acceptance="done")
    store.create_task(goal["id"], "Ready under background", status="ready")

    meals: list[str] = []

    def capture(**kwargs: Any) -> DoLoopResult:
        rebuild = kwargs["rebuild_outer"]
        meals.append(rebuild()[-1]["content"])
        ctx = kwargs["ctx"]
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            moment_id=ctx.moment_id,
        )

    worker, stop = _make_worker(paths, run_do_loop_fn=capture)
    worker._goals = store  # noqa: SLF001
    t = _start(worker)
    try:
        worker.enqueue_wake("background", {"reason": "housekeeping"})
        assert _wait_until(lambda: len(meals) >= 1, timeout=2.0)
        assert BIAS_DO_WORK in meals[0]
        assert BIAS_BACKGROUND not in meals[0]
    finally:
        _stop_join(worker, stop, t)


def test_rebuild_outer_background_empty_ledger_rest(paths):
    """E4: background + empty ledger → BIAS_REST (not BIAS_BACKGROUND)."""
    from elyra.loop.orient_slice import BIAS_BACKGROUND, BIAS_REST

    meals: list[str] = []

    def capture(**kwargs: Any) -> DoLoopResult:
        rebuild = kwargs["rebuild_outer"]
        meals.append(rebuild()[-1]["content"])
        ctx = kwargs["ctx"]
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            moment_id=ctx.moment_id,
        )

    worker, stop = _make_worker(paths, run_do_loop_fn=capture)
    t = _start(worker)
    try:
        worker.enqueue_wake("background", {"reason": "housekeeping"})
        assert _wait_until(lambda: len(meals) >= 1, timeout=2.0)
        assert BIAS_REST in meals[0]
        assert BIAS_BACKGROUND not in meals[0]
    finally:
        _stop_join(worker, stop, t)


def test_rebuild_outer_timer_empty_ledger_rest(paths):
    """E6: timer + empty ledger → BIAS_REST (production death of BIAS_TIMER_*)."""
    from elyra.loop.orient_slice import (
        BIAS_REST,
        BIAS_TIMER_GENERIC,
        BIAS_TIMER_LINKED,
    )

    meals: list[str] = []

    def capture(**kwargs: Any) -> DoLoopResult:
        rebuild = kwargs["rebuild_outer"]
        meals.append(rebuild()[-1]["content"])
        ctx = kwargs["ctx"]
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            moment_id=ctx.moment_id,
        )

    worker, stop = _make_worker(paths, run_do_loop_fn=capture)
    t = _start(worker)
    try:
        worker.enqueue_wake(
            "timer",
            {"reason": "ping", "wake_at": "2099-01-01T00:00:00Z"},
        )
        assert _wait_until(lambda: len(meals) >= 1, timeout=2.0)
        assert BIAS_REST in meals[0]
        assert BIAS_TIMER_GENERIC not in meals[0]
        assert BIAS_TIMER_LINKED not in meals[0]
    finally:
        _stop_join(worker, stop, t)


def test_policy_a_run_do_loop_gets_matching_sliding_and_in_turn(paths):
    """BUG-meal-01 Policy A: fraction 0.4 → both caps 200k into run_do_loop."""
    seen: list[Any] = []

    def capture(**kwargs: Any) -> DoLoopResult:
        seen.append(kwargs.get("settings"))
        ctx = kwargs["ctx"]
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            moment_id=ctx.moment_id,
        )

    worker, stop = _make_worker(paths, run_do_loop_fn=capture)
    t = _start(worker)
    try:
        result = worker.set_meal_budget(fraction=0.4)
        assert result["ok"] is True
        assert result["meal_budget"]["meal_budget_tokens"] == 200_000
        worker.enqueue_user_message("policy a meal budget")
        assert _wait_until(lambda: len(seen) >= 1, timeout=2.0)
        assert _wait_until(lambda: not worker.busy, timeout=2.0)
        settings = seen[0]
        assert settings is not None
        assert settings.loop.sliding_input_tokens == 200_000
        assert settings.loop.in_turn_max_tokens == 200_000
        assert (
            settings.loop.sliding_input_tokens
            == settings.loop.in_turn_max_tokens
            == 200_000
        )
    finally:
        _stop_join(worker, stop, t)


def test_set_meal_budget_persist_failure_leaves_live_state(paths, monkeypatch):
    """Fail-clean: OSError on save does not mutate live fraction or claim ok."""
    from elyra.runtime import meal_budget as meal_budget_mod

    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    t = _start(worker)
    try:
        assert worker.set_meal_budget(fraction=0.5)["ok"] is True
        assert worker._meal_budget.fraction == 0.5  # noqa: SLF001

        def boom(*_a: Any, **_k: Any) -> Path:
            raise OSError("disk full")

        monkeypatch.setattr(meal_budget_mod, "save_meal_budget_runtime", boom)
        # Worker imports save at module level — patch the bound name on worker module.
        import elyra.presence.worker as worker_mod

        monkeypatch.setattr(worker_mod, "save_meal_budget_runtime", boom)

        result = worker.set_meal_budget(fraction=0.4)
        assert result["ok"] is False
        assert result["error"] == "persist_failed"
        assert result["meal_budget"]["fraction"] == 0.5
        assert worker._meal_budget.fraction == 0.5  # noqa: SLF001
        # Status still reports previous durable/live value.
        snap = worker.status_snapshot()
        assert snap["meal_budget"]["fraction"] == 0.5
        assert snap["meal_budget"]["meal_budget_tokens"] == 250_000
    finally:
        _stop_join(worker, stop, t)


# ── P2 warm-on-start: EdgeStore open SM + single-flight + retry ─────────────


def test_edge_store_open_retry_clears_unavailable(paths, monkeypatch):
    """Soft-fail Unavailable must not stick forever — null handle and retry.

    Real sticky path: fail_soft returns UnavailableEdgeStore which is not None;
    old ensure returned it forever. On transient retry we clear and re-open.
    """
    from dataclasses import replace

    from elyra.memory.config import MemorySettings
    from elyra.memory.edges import JsonlEdgeStore, UnavailableEdgeStore
    import elyra.memory.edges as edges_mod

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            durable_edges_enabled=True,
        ),
    )
    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop(), settings=settings)

    calls = {"n": 0}

    def flaky_open(paths_arg, settings_arg=None, *, fail_soft=True):
        calls["n"] += 1
        if calls["n"] == 1:
            return UnavailableEdgeStore("edge_backend_open_failed:SimulatedIO")
        return JsonlEdgeStore(paths_arg, settings_arg or MemorySettings())

    monkeypatch.setattr(edges_mod, "open_edge_store", flaky_open)
    # Worker imports open_edge_store inside the method — patch module used there.
    import elyra.presence.worker as worker_mod

    # open_edge_store is imported inside _ensure_edge_store from elyra.memory.edges
    monkeypatch.setattr(
        "elyra.memory.edges.open_edge_store", flaky_open
    )

    first = worker._ensure_edge_store()  # noqa: SLF001
    assert isinstance(first, UnavailableEdgeStore)
    assert worker._edge_store_state == "unavailable"  # noqa: SLF001
    assert worker._edge_store_open_failed is False  # noqa: SLF001 — transient
    assert calls["n"] == 1

    # Immediate re-ensure still returns Unavailable (backoff not elapsed).
    second = worker._ensure_edge_store()  # noqa: SLF001
    assert isinstance(second, UnavailableEdgeStore)
    assert calls["n"] == 1  # no re-open yet

    # Advance past backoff → retry nulls Unavailable and opens real store.
    worker._edge_store_next_retry_at = 0.0  # noqa: SLF001
    third = worker._ensure_edge_store()  # noqa: SLF001
    assert not isinstance(third, UnavailableEdgeStore)
    assert worker._edge_store_state == "ready"  # noqa: SLF001
    assert calls["n"] == 2
    assert third is worker._edge_store  # noqa: SLF001

    st = worker.edge_store_open_status()
    assert st["state"] == "ready"
    assert st["attempts"] == 2


def test_edge_store_permanent_import_error_no_retry(paths, monkeypatch):
    """ImportError-class reason is permanent — no auto-retry loop."""
    from dataclasses import replace

    from elyra.memory.config import MemorySettings
    from elyra.memory.edges import UnavailableEdgeStore

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            durable_edges_enabled=True,
        ),
    )
    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop(), settings=settings)

    calls = {"n": 0}

    def always_import_fail(*_a, **_k):
        calls["n"] += 1
        return UnavailableEdgeStore("edge_backend_unavailable")

    monkeypatch.setattr("elyra.memory.edges.open_edge_store", always_import_fail)

    first = worker._ensure_edge_store()  # noqa: SLF001
    assert isinstance(first, UnavailableEdgeStore)
    assert worker._edge_store_open_failed is True  # noqa: SLF001
    worker._edge_store_next_retry_at = 0.0  # noqa: SLF001
    second = worker._ensure_edge_store()  # noqa: SLF001
    assert isinstance(second, UnavailableEdgeStore)
    assert calls["n"] == 1  # no second open


def test_edge_store_single_flight_open(paths, monkeypatch):
    """Two concurrent ensures share one open (single-flight lock)."""
    from dataclasses import replace

    from elyra.memory.config import MemorySettings
    from elyra.memory.edges import JsonlEdgeStore

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            durable_edges_enabled=True,
        ),
    )
    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop(), settings=settings)

    barrier = threading.Barrier(2)
    calls = {"n": 0}
    lock = threading.Lock()

    def slow_open(paths_arg, settings_arg=None, *, fail_soft=True):
        with lock:
            calls["n"] += 1
        time.sleep(0.15)
        return JsonlEdgeStore(paths_arg, settings_arg or MemorySettings())

    monkeypatch.setattr("elyra.memory.edges.open_edge_store", slow_open)

    results: list[Any] = [None, None]
    errors: list[BaseException | None] = [None, None]

    def target(idx: int) -> None:
        try:
            barrier.wait(timeout=5)
            results[idx] = worker._ensure_edge_store()  # noqa: SLF001
        except BaseException as exc:  # noqa: BLE001
            errors[idx] = exc

    t0 = threading.Thread(target=target, args=(0,))
    t1 = threading.Thread(target=target, args=(1,))
    t0.start()
    t1.start()
    t0.join(timeout=10)
    t1.join(timeout=10)
    assert errors[0] is None and errors[1] is None
    assert results[0] is not None and results[1] is not None
    assert results[0] is results[1]
    assert calls["n"] == 1
    assert worker._edge_store_state == "ready"  # noqa: SLF001


# ── P1 warm-on-start: eager core + side-thread embedder loader ──────────────


def test_warm_core_allows_claim_before_embedder(paths, monkeypatch):
    """After core warm, claim/start runs while embedder loader still loading.

    KD-WARM-UX: presence must not join Nemotron before ``_started`` / claim loop.
    Mock open_encoder blocks until release; presence loop must set ``_started``
    and poll while embedder_state is still ``loading``.
    """
    from dataclasses import replace

    from elyra.memory.config import MemorySettings
    from elyra.memory.embed.mock import MockEmbedder

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            durable_edges_enabled=True,
            semantic_enabled=True,
            embed_enabled=True,
            embed_backend="mock",
            embed_preload=True,
            encode_worker_enabled=True,
            encode_worker_poll_s=0.05,
        ),
    )
    worker, stop = _make_worker(
        paths, run_do_loop_fn=_stub_loop(), settings=settings, poll_seconds=0.02
    )

    opened = threading.Event()
    release = threading.Event()
    load_calls = {"n": 0}

    def slow_open(_cfg):
        load_calls["n"] += 1
        opened.set()
        # Hold cold load until test releases — presence must not wait.
        if not release.wait(timeout=10.0):
            raise RuntimeError("loader release timeout")
        return MockEmbedder()

    monkeypatch.setattr(
        "elyra.memory.embed.runtime.open_encoder",
        slow_open,
    )

    t = threading.Thread(target=worker.run, name="presence-warm-test", daemon=True)
    t.start()
    try:
        # Wait until start-path loader has entered open_encoder.
        assert opened.wait(timeout=5.0), "loader never entered open_encoder"
        # Presence must already have finished core + set _started without joining.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not worker._started:  # noqa: SLF001
            time.sleep(0.01)
        assert worker._started is True  # noqa: SLF001
        assert worker._embedder_state == "loading"  # noqa: SLF001
        # Core fabric open (store + edges) while embedder still loading.
        assert worker._memory is not None  # noqa: SLF001
        assert worker._edge_store_state in ("ready", "unavailable")  # noqa: SLF001
        # Consumer gate: non-blocking None while loading (KD-GATE / KD-E18).
        t0 = time.monotonic()
        emb = worker._ensure_embedder(role="consumer")  # noqa: SLF001
        assert emb is None
        assert (time.monotonic() - t0) < 0.5
        # Encode worker deferred until loader terminal.
        assert (
            worker._encode_worker is None
            or not worker._encode_worker.is_alive()  # noqa: SLF001
        )
        # Status surfaces honest warming + loading; aggregate not ready yet.
        mem = worker.status_snapshot()["memory"]
        assert mem["embedder_state"] == "loading"
        assert mem["memory_warming"] is True
        assert mem["warming"] is True
        assert mem["embedder_ready"] is False
        assert mem["memory_ready"] is False  # need_embed while loading
        assert "edges_open" in mem
        assert mem["edges_open"]["state"] in (
            "ready",
            "unavailable",
            "opening",
            "absent",
        )
    finally:
        release.set()
        stop.set()
        t.join(timeout=15.0)


def test_warm_loader_terminal_starts_encode_worker(paths, monkeypatch):
    """On loader terminal warm, presence applies encode worker start once."""
    from dataclasses import replace

    from elyra.memory.config import MemorySettings
    from elyra.memory.embed.mock import MockEmbedder

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            durable_edges_enabled=True,
            semantic_enabled=True,
            embed_enabled=True,
            embed_backend="mock",
            embed_preload=True,
            encode_worker_enabled=True,
            encode_worker_poll_s=0.05,
        ),
    )
    worker, stop = _make_worker(
        paths, run_do_loop_fn=_stub_loop(), settings=settings, poll_seconds=0.02
    )
    monkeypatch.setattr(
        "elyra.memory.embed.runtime.open_encoder",
        lambda _cfg: MockEmbedder(),
    )
    t = threading.Thread(target=worker.run, name="presence-warm-term", daemon=True)
    t.start()
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if (
                worker._embedder_loader_applied  # noqa: SLF001
                and worker._embedder_state == "warm"  # noqa: SLF001
            ):
                break
            time.sleep(0.02)
        assert worker._embedder_state == "warm"  # noqa: SLF001
        assert worker._embedder_loader_applied is True  # noqa: SLF001
        assert worker._memory_warming is False  # noqa: SLF001
        # Encode worker should be live after terminal apply.
        deadline2 = time.monotonic() + 3.0
        while time.monotonic() < deadline2:
            w = worker._encode_worker  # noqa: SLF001
            if w is not None and w.is_alive():
                break
            time.sleep(0.02)
        assert worker._encode_worker is not None  # noqa: SLF001
        assert worker._encode_worker.is_alive()  # noqa: SLF001
        emb = worker._ensure_embedder(role="consumer")  # noqa: SLF001
        assert emb is not None
        mem = worker.status_snapshot()["memory"]
        assert mem["embedder_state"] == "warm"
        assert mem["memory_warming"] is False
        assert mem["embedder_ready"] is True
        # Full fabric ready once embedder terminal warm + edges ok.
        if mem["edges_ready"] and mem["atom_store_ready"] and mem["index_ready"]:
            assert mem["memory_ready"] is True
    finally:
        stop.set()
        t.join(timeout=10.0)


def test_memory_status_component_fields_present(paths):
    """Status memory block exposes embedder_state / edges_open / memory_warming / ready."""
    from dataclasses import replace

    from elyra.memory.config import MemorySettings

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            durable_edges_enabled=True,
        ),
    )
    worker, _stop = _make_worker(paths, run_do_loop_fn=_stub_loop(), settings=settings)
    mem = worker.status_snapshot()["memory"]
    assert mem["embedder_state"] in ("absent", "loading", "warm", "failed")
    assert isinstance(mem["edges_open"], dict)
    assert "state" in mem["edges_open"]
    assert "memory_warming" in mem
    assert mem["memory_warming"] is False  # not yet on warm path
    # P4 aggregate + component flags always present.
    assert "memory_ready" in mem
    assert mem["memory_ready"] is False  # store not open yet
    assert "edges_ready" in mem
    assert "embedder_ready" in mem
    assert "index_ready" in mem
    assert "atom_store_ready" in mem
    assert isinstance(mem.get("edges"), dict)
    assert isinstance(mem.get("embedder"), dict)
    assert isinstance(mem.get("index"), dict)

    # After eager core (no run loop), edges open + fields still honest.
    worker._warm_memory_core()  # noqa: SLF001
    mem2 = worker.status_snapshot()["memory"]
    assert mem2["store_open"] is True
    assert mem2["edges_open"]["state"] in ("ready", "unavailable")
    assert mem2["embedder_state"] == "absent"  # core does not load embedder
    assert mem2["atom_store_ready"] is True
    assert mem2["index_ready"] is True
    if mem2["edges_open"]["state"] == "ready":
        assert mem2["edges_ready"] is True
        # embed not enabled by default in this settings → memory_ready true
        assert mem2["need_embed"] is False
        assert mem2["memory_ready"] is True


def test_warm_core_optional_tray_soft_fail(paths):
    """W5: warm core best-effort ensure_tray; soft fail; never gates memory_ready.

    - Happy path: tray loaded after ``_warm_memory_core`` (was lazy before).
    - Failure path: ensure_tray raises → core still completes; memory_ready
      still driven only by store/index/edges/embedder components.
    """
    from dataclasses import replace
    from unittest.mock import patch

    from elyra.memory.config import MemorySettings
    from elyra.memory.keep_tray import (
        DirectedKeepTray,
        merge_confirm,
        save_directed_keep_tray,
        tray_runtime_path,
    )

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            durable_edges_enabled=True,
        ),
    )
    # Seed a sticky tray on disk so warm path has something to load.
    tray = DirectedKeepTray()
    merge_confirm(tray, ["warm-pin-a"], now="2026-08-06T00:00:00Z")
    save_directed_keep_tray(tray, paths=paths)
    assert tray_runtime_path(paths.data_dir).is_file()

    worker, _stop = _make_worker(
        paths, run_do_loop_fn=_stub_loop(), settings=settings
    )
    assert worker.traversal.directed_keep_tray is None  # lazy until warm

    worker._warm_memory_core()  # noqa: SLF001

    loaded = worker.traversal.directed_keep_tray
    assert loaded is not None
    assert "warm-pin-a" in loaded.atom_ids()
    mem = worker.status_snapshot()["memory"]
    # Tray is not a readiness component (KD-TRAY / KD-GATE).
    assert "tray_ready" not in mem
    if mem["edges_ready"] and mem["atom_store_ready"]:
        assert mem["memory_ready"] is True

    # Soft fail: ensure_tray boom must not kill warm core or flip ready falsely.
    worker2, _ = _make_worker(
        paths, run_do_loop_fn=_stub_loop(), settings=settings
    )
    with patch.object(
        worker2.traversal,
        "ensure_tray",
        side_effect=RuntimeError("tray boom"),
    ):
        worker2._warm_memory_core()  # noqa: SLF001 — must not raise
    assert worker2.traversal.directed_keep_tray is None
    mem2 = worker2.status_snapshot()["memory"]
    # Store/edges still decide readiness — tray failure is invisible to aggregate.
    if mem2["edges_ready"] and mem2["atom_store_ready"]:
        assert mem2["memory_ready"] is True


def test_consumer_embedder_nonblocking_while_loading_status(paths):
    """Document KD-GATE: consumers stay non-blocking while loader in flight."""
    from dataclasses import replace

    from elyra.memory.config import MemorySettings

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            embed_enabled=True,
            embed_backend="mock",
            embed_preload=True,
        ),
    )
    worker, _stop = _make_worker(paths, run_do_loop_fn=_stub_loop(), settings=settings)
    worker._embedder_state = "loading"  # noqa: SLF001
    worker._memory_warming = True  # noqa: SLF001
    t0 = time.monotonic()
    emb = worker._ensure_embedder(role="consumer")  # noqa: SLF001
    elapsed = time.monotonic() - t0
    assert emb is None
    assert elapsed < 0.5
    mem = worker.status_snapshot()["memory"]
    assert mem["embedder_state"] == "loading"
    assert mem["memory_warming"] is True


# ---------------------------------------------------------------------------
# PR3b — conversation_id + social_kind propagation
# ---------------------------------------------------------------------------


def test_interject_overflow_retains_conversation_and_social_kind(paths):
    """Interject overflow wake retains conversation_id + social_kind (§3.6)."""
    entered = threading.Event()
    release = threading.Event()

    def on_call(_kwargs: Any) -> None:
        entered.set()
        release.wait(timeout=3.0)

    stub = _stub_loop(on_call=on_call)
    worker, stop = _make_worker(paths, run_do_loop_fn=stub)
    t = _start(worker)
    try:
        worker.enqueue_user_message(
            "busy",
            conversation_id="group:g-overflow",
            social_kind="group",
        )
        assert entered.wait(timeout=2.0)

        for i in range(8):
            r = worker.interject(
                f"note-{i}",
                conversation_id="group:g-overflow",
                social_kind="group",
            )
            assert r["ok"] is True

        overflow = worker.interject(
            "too-many",
            user_id="jim",
            conversation_id="group:g-overflow",
            social_kind="group",
        )
        assert overflow["ok"] is False
        assert overflow["reason"] == "interjection_buffer_full"
        wake_id = overflow.get("wake_id")
        assert wake_id
        item = worker._queue.get(wake_id)  # noqa: SLF001
        assert item is not None
        payload = item.payload or {}
        assert payload.get("conversation_id") == "group:g-overflow"
        assert payload.get("social_kind") == "group"
        assert payload.get("from_interject_overflow") is True
        assert payload.get("user_id") == "jim"
    finally:
        release.set()
        _stop_join(worker, stop, t)


def test_build_tool_context_stamps_social_kind_from_payload(paths):
    """_build_tool_context copies conversation_id + extras social_kind from wake."""
    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    try:
        from elyra.presence.queue import WakeItem, priority_for_kind

        social_wake = WakeItem(
            id="w-social",
            kind="user_message",
            priority=priority_for_kind("user_message"),
            created_at="2026-01-01T00:00:00Z",
            payload={
                "content": "hi",
                "user_id": "jim",
                "conversation_id": "group:g1",
                "social_kind": "group",
            },
        )
        ctx = worker._build_tool_context(social_wake, "m1")  # noqa: SLF001
        assert ctx.user_id == "jim"
        assert ctx.conversation_id == "group:g1"
        assert ctx.extras.get("social_kind") == "group"

        pure_wake = WakeItem(
            id="w-timer",
            kind="timer",
            priority=priority_for_kind("timer"),
            created_at="2026-01-01T00:00:00Z",
            payload={"reason": "tick"},
        )
        ctx2 = worker._build_tool_context(pure_wake, "m2")  # noqa: SLF001
        assert ctx2.conversation_id is None
        assert ctx2.extras.get("social_kind") == "none"
    finally:
        stop.set()


def test_continuous_and_timer_wakes_have_no_conversation_id(paths):
    """Continuous/timer enqueue must not invent conversation_id from any session."""
    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    try:
        # Simulate "client has dm" existing only as session state elsewhere —
        # pure work paths must still omit conversation_id.
        wid = worker.enqueue_wake("timer", {"reason": "solo work"})
        item = worker._queue.get(wid)  # noqa: SLF001
        assert item is not None
        payload = item.payload or {}
        assert payload.get("conversation_id") in (None, "")
        assert payload.get("social_kind") in (None, "", "none")

        # moment_continue payload shape (same as finalize path) has no social.
        cont_id = worker.enqueue_wake(
            "moment_continue",
            {
                "source_moment_id": "m-src",
                "source_wake_kind": "timer",
                "source_stop_reason": "no_tools",
                "streak": 1,
            },
        )
        cont = worker._queue.get(cont_id)  # noqa: SLF001
        assert cont is not None
        cp = cont.payload or {}
        assert "conversation_id" not in cp or not cp.get("conversation_id")
        assert cp.get("social_kind") in (None, "", "none")

        # open_moment soft field stays unset for pure work.
        mid = worker._moments.open_moment(  # noqa: SLF001
            why_now="timer due",
            user_id=None,
            wake_id=wid,
        )
        meta = worker._moments.get_moment(mid)  # noqa: SLF001
        assert meta is not None
        assert not meta.get("conversation_id")
    finally:
        stop.set()


def test_enqueue_user_message_stamps_social_fields(paths):
    """enqueue_user_message / resolve_user_input stamp conversation + social_kind."""
    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    try:
        wid = worker.enqueue_user_message(
            "group hi",
            user_id="jim",
            conversation_id="group:g2",
        )
        item = worker._queue.get(wid)  # noqa: SLF001
        assert item is not None
        payload = item.payload or {}
        assert payload["conversation_id"] == "group:g2"
        assert payload["social_kind"] == "group"
        assert payload["user_id"] == "jim"

        r = worker.resolve_user_input(
            "dm hi",
            user_id="sam",
            conversation_id="dm:sam",
        )
        assert r.get("ok") is True
        wake_id = r.get("wake_id")
        assert wake_id
        w = worker._queue.get(wake_id)  # noqa: SLF001
        assert w is not None
        assert (w.payload or {}).get("conversation_id") == "dm:sam"
        assert (w.payload or {}).get("social_kind") == "dm"
    finally:
        stop.set()


def test_open_moment_soft_conversation_id_from_wake(paths):
    """open_moment persists soft conversation_id only when wake payload has it."""
    entered = threading.Event()
    release = threading.Event()
    opened: list[str] = []

    def on_call(kwargs: Any) -> None:
        ctx = kwargs.get("ctx")
        if ctx is not None:
            opened.append(getattr(ctx, "conversation_id", None) or "")
        entered.set()
        release.wait(timeout=2.0)

    stub = _stub_loop(on_call=on_call)
    worker, stop = _make_worker(paths, run_do_loop_fn=stub)
    t = _start(worker)
    try:
        worker.enqueue_user_message(
            "social open",
            user_id="jim",
            conversation_id="dm:jim",
            social_kind="dm",
        )
        assert entered.wait(timeout=2.0)
        assert worker.active_moment_id is not None
        meta = worker._moments.get_moment(worker.active_moment_id)  # noqa: SLF001
        assert meta is not None
        assert meta.get("conversation_id") == "dm:jim"
        assert opened and opened[0] == "dm:jim"
    finally:
        release.set()
        _stop_join(worker, stop, t)


def test_append_message_if_allowed_conversation_id(paths):
    """append_message_if_allowed forwards conversation_id to the row."""
    from elyra.messages import list_messages

    worker, stop = _make_worker(paths, run_do_loop_fn=_stub_loop())
    try:
        msg, err = worker.append_message_if_allowed(
            "user",
            "stamped",
            user_id="jim",
            conversation_id="group:g3",
        )
        assert err is None
        assert msg is not None
        assert msg.conversation_id == "group:g3"
        rows = list_messages(paths=paths, limit=10, conversation_id="group:g3")
        assert any(r.get("content") == "stamped" for r in rows)
    finally:
        stop.set()
