"""EncodeWorker continuous drain, EmbedderGate, owner, death recovery (PR2)."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from threading import Event
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.llm.client import StubChatClient
from elyra.memory.config import MemorySettings
from elyra.memory.embed.gate import EmbedderGate
from elyra.memory.embed.mock import MockEmbedder
from elyra.memory.embed.queue import EncodePriority, EncodeQueue
from elyra.memory.embed.worker import EncodeWorker
from elyra.memory.promote import promote_beat
from elyra.memory.store import open_memory_store
from elyra.memory.types import Atom, new_atom_id
from elyra.presence.worker import PresenceWorker
from elyra.settings import default_settings


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    return open_memory_store(paths, MemorySettings(write_atoms=True, backend="jsonl"))


def _atom(
    *,
    text: str = "hello embed me",
    status: str = "pending",
    atom_id: str | None = None,
    **kwargs: Any,
) -> Atom:
    return Atom(
        atom_id=atom_id or new_atom_id(),
        t_start=kwargs.pop("t_start", "2026-07-28T10:00:00Z"),
        kind=kwargs.pop("kind", "observation"),
        content_text=text,
        content_ref="inline",
        moment_id=kwargs.pop("moment_id", "m1"),
        embedding_status=status,
        **kwargs,
    )


def _sem_embed_settings(**kwargs: Any) -> Any:
    base = dict(
        write_atoms=True,
        enabled=True,
        backend="jsonl",
        semantic_enabled=True,
        embed_enabled=True,
        embed_backend="mock",
        encode_worker_enabled=True,
        encode_worker_poll_s=0.05,
        encode_max_items_per_tick=8,
        encode_max_ms_per_tick=2000,
    )
    base.update(kwargs)
    return replace(default_settings(), memory=MemorySettings(**base))


# ── EmbedderGate ────────────────────────────────────────────────────────────


def test_gate_exclusive_and_lookup_priority():
    gate = EmbedderGate()
    assert gate.acquire("bulk", timeout=0.2) is True
    assert gate.holder == "bulk"

    # Lookup cannot take while bulk holds (timeout).
    assert gate.acquire("lookup", timeout=0.05) is False
    gate.release()

    assert gate.acquire("lookup", timeout=0.2) is True
    assert gate.holder == "lookup"
    # Bulk yields while lookup holds.
    assert gate.acquire("bulk", timeout=0.05) is False
    assert gate.gate_bulk_yields >= 1
    gate.release()


def test_gate_lookup_waiter_blocks_new_bulk():
    gate = EmbedderGate()
    held = Event()
    done = Event()

    def _lookup() -> None:
        assert gate.acquire("lookup", timeout=2.0) is True
        held.set()
        time.sleep(0.15)
        gate.release()
        done.set()

    t = threading.Thread(target=_lookup, daemon=True)
    t.start()
    assert held.wait(timeout=1.0)
    # While lookup holds, bulk cannot acquire.
    assert gate.acquire("bulk", timeout=0.05) is False
    assert done.wait(timeout=1.0)
    assert gate.acquire("bulk", timeout=0.5) is True
    gate.release()
    t.join(timeout=1.0)


# ── EncodeWorker unit ───────────────────────────────────────────────────────


def test_encode_worker_ticks_on_wake():
    hits: list[int] = []
    lock = threading.Lock()

    def poll() -> dict[str, int]:
        with lock:
            hits.append(1)
        return {"ok": 1, "processed": 1, "remaining": 0}

    wake = Event()
    w = EncodeWorker(poll_once=poll, poll_s=0.5, wake_event=wake)
    w.start()
    try:
        wake.set()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with lock:
                if len(hits) >= 1:
                    break
            time.sleep(0.02)
        with lock:
            assert len(hits) >= 1
    finally:
        w.stop(join_timeout_s=1.0)
    assert not w.is_alive()


def test_encode_worker_tick_once_soft_fail():
    def boom() -> dict[str, int]:
        raise RuntimeError("tick boom")

    w = EncodeWorker(poll_once=boom, poll_s=0.1)
    assert w.tick_once() is None  # never raises


# ── Drain deferred retry ────────────────────────────────────────────────────


def test_drain_deferred_retry_once_per_tick(store):
    """Retryable fail re-enqueues after drain; attempts +1 per drain call only."""

    class Boom:
        def health(self):
            return {"ok": True, "dim": 8, "model_id": "x", "backend": "mock"}

        def encode_text(self, text: str):
            raise RuntimeError("boom")

        def encode_image(self, x):
            raise RuntimeError("boom")

        def encode_audio(self, x):
            raise RuntimeError("boom")

        def encode_video(self, x):
            raise RuntimeError("boom")

        def encode_joint(self, parts):
            raise RuntimeError("boom")

    atom = store.put_atom(_atom(text="will fail once per tick", status="pending"))
    q = EncodeQueue(maxsize=8)
    emb = Boom()
    q.enqueue(atom.atom_id, priority=EncodePriority.ATOM_CREATE)

    # One drain tick with high max_items must only burn attempts once even
    # though deferred re-enqueue puts the id back after the call.
    stats = q.drain(store, emb, max_items=8, max_attempts=3, max_ms=5000)
    assert stats["failed"] == 1
    assert stats["processed"] == 1
    assert stats["requeued"] >= 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert int(got.meta.get("embed_attempts") or 0) == 1
    assert got.embedding_status == "pending"
    # Deferred flush left it in the queue for the *next* tick.
    assert q.contains(atom.atom_id)

    stats2 = q.drain(store, emb, max_items=8, max_attempts=3, max_ms=5000)
    assert stats2["failed"] == 1
    got2 = store.get_atom(atom.atom_id)
    assert int(got2.meta.get("embed_attempts") or 0) == 2


def test_drain_gate_yield_requeues(store):
    gate = EmbedderGate()
    # Hold lookup so bulk cannot acquire.
    assert gate.acquire("lookup", timeout=1.0) is True
    atom = store.put_atom(_atom(text="yield me", status="pending"))
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    stats = q.drain(
        store,
        MockEmbedder(),
        max_items=2,
        max_ms=500,
        gate=gate,
        gate_bulk_timeout_s=0.05,
    )
    assert stats["yielded"] >= 1
    assert q.contains(atom.atom_id)  # deferred requeue
    gate.release()


# ── Presence continuous path ────────────────────────────────────────────────


def test_continuous_drain_without_idle_claim(paths):
    """G-B: drain progress with owner=worker without calling idle encode."""
    settings = _sem_embed_settings()
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    store = worker._ensure_memory_store()  # noqa: SLF001
    assert store is not None
    worker._start_encode_worker_if_needed()  # noqa: SLF001
    assert worker._encode_owner == "worker"  # noqa: SLF001

    atom = promote_beat(
        store,
        "m1",
        {
            "type": "model",
            "content": "continuous encode body long enough " * 4,
            "ts": "2026-07-28T11:00:00Z",
        },
        settings=settings.memory,
    )
    assert atom is not None
    assert atom.embedding_status == "pending"
    assert worker._encode_queue.contains(atom.atom_id)  # noqa: SLF001

    # Idle path must not drain while owner=worker.
    worker._idle_memory_encode()  # noqa: SLF001
    # Worker / poll_once drains without idle claim.
    deadline = time.monotonic() + 3.0
    got = None
    while time.monotonic() < deadline:
        got = store.get_atom(atom.atom_id)
        if got is not None and got.meta.get("embed_encode_ok"):
            break
        # Also drive poll in case wake/poll races in CI
        worker._encode_poll_once()  # noqa: SLF001
        time.sleep(0.02)
    assert got is not None
    assert got.meta.get("embed_encode_ok") is True
    assert worker._encode_drain_ok_total >= 1  # noqa: SLF001

    worker._shutdown_encode()  # noqa: SLF001
    assert worker._encode_owner == "none"  # noqa: SLF001


def test_idle_owner_drains_when_worker_disabled(paths):
    settings = _sem_embed_settings(encode_worker_enabled=False)
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    store = worker._ensure_memory_store()  # noqa: SLF001
    worker._start_encode_worker_if_needed()  # noqa: SLF001
    assert worker._encode_owner == "idle"  # noqa: SLF001
    assert worker._encode_worker is None or not worker._encode_worker.is_alive()  # noqa: SLF001

    atom = promote_beat(
        store,
        "m1",
        {
            "type": "model",
            "content": "idle rollback body long enough " * 4,
            "ts": "2026-07-28T11:00:00Z",
        },
        settings=settings.memory,
    )
    assert atom is not None
    worker._idle_memory_encode()  # noqa: SLF001
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.meta.get("embed_encode_ok") is True


def test_consumer_ensure_nonblocking_while_loading(paths):
    settings = _sem_embed_settings()
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    # Simulate loader in flight without completing open.
    worker._embedder_state = "loading"  # noqa: SLF001
    t0 = time.monotonic()
    emb = worker._ensure_embedder(role="consumer")  # noqa: SLF001
    elapsed = time.monotonic() - t0
    assert emb is None
    assert elapsed < 0.5  # must not wait on cold load


def test_death_recovery_gap_drain(paths):
    """Dead worker + owner=worker still makes progress via gap drain."""
    settings = _sem_embed_settings()
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    store = worker._ensure_memory_store()  # noqa: SLF001
    worker._start_encode_worker_if_needed()  # noqa: SLF001
    assert worker._encode_owner == "worker"  # noqa: SLF001

    # Kill the worker thread without flipping owner to idle.
    worker._stop_encode_worker()  # noqa: SLF001
    # Force owner back to worker (stop doesn't change desired owner).
    worker._encode_owner = "worker"  # noqa: SLF001
    # Prevent immediate restart so gap path is exercised.
    worker._encode_worker_next_restart_at = time.monotonic() + 60.0  # noqa: SLF001

    atom = promote_beat(
        store,
        "m1",
        {
            "type": "model",
            "content": "gap drain body long enough here " * 4,
            "ts": "2026-07-28T12:00:00Z",
        },
        settings=settings.memory,
    )
    assert atom is not None

    worker._gap_drain_if_needed()  # noqa: SLF001
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.meta.get("embed_encode_ok") is True
    assert worker._encode_owner == "worker"  # noqa: SLF001 — never flipped to idle


def test_maybe_restart_encode_worker(paths):
    settings = _sem_embed_settings(
        encode_worker_restart_window_s=60.0,
        encode_worker_max_restarts=5,
    )
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    worker._ensure_memory_store()  # noqa: SLF001
    worker._start_encode_worker_if_needed()  # noqa: SLF001
    assert worker._encode_worker is not None  # noqa: SLF001
    assert worker._encode_worker.is_alive()  # noqa: SLF001

    worker._stop_encode_worker()  # noqa: SLF001
    worker._encode_owner = "worker"  # noqa: SLF001
    worker._encode_worker_next_restart_at = 0.0  # noqa: SLF001
    worker._maybe_restart_encode_worker()  # noqa: SLF001
    assert worker._encode_owner == "worker"  # noqa: SLF001
    assert worker._encode_worker is not None  # noqa: SLF001
    assert worker._encode_worker.is_alive()  # noqa: SLF001
    assert worker._encode_worker_restarts >= 1  # noqa: SLF001
    worker._shutdown_encode()  # noqa: SLF001


def test_write_hook_wakes_worker(paths):
    settings = _sem_embed_settings()
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    store = worker._ensure_memory_store()  # noqa: SLF001
    worker._encode_wake.clear()  # noqa: SLF001
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "model",
            "content": "wake event body long enough text " * 4,
            "ts": "2026-07-28T13:00:00Z",
        },
        settings=settings.memory,
    )
    assert atom is not None
    assert worker._encode_wake.is_set()  # noqa: SLF001
    assert worker._encode_queue.contains(atom.atom_id)  # noqa: SLF001


# ── Review fixes: zombie join, thrash backoff, gate singleton ───────────────


def test_stop_join_timeout_retains_alive_handle():
    """Issue 1: stop must not null thread while poll_once still running."""
    entered = Event()
    release = Event()
    calls: list[str] = []

    def slow_poll() -> dict[str, int]:
        calls.append("enter")
        entered.set()
        release.wait(timeout=5.0)
        calls.append("exit")
        return {"ok": 0, "processed": 0, "remaining": 0}

    w = EncodeWorker(poll_once=slow_poll, poll_s=0.5)
    w.start()
    assert entered.wait(timeout=2.0)
    # Join timeout while still inside poll_once.
    assert w.stop(join_timeout_s=0.15) is False
    assert w.is_alive() is True
    assert "enter" in calls
    assert "exit" not in calls
    release.set()
    deadline = time.monotonic() + 2.0
    while w.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not w.is_alive()
    assert "exit" in calls


def test_zombie_epoch_noop_and_no_gap_dual(paths):
    """Issue 1: stop bumps epoch; zombie poll no-ops; gap waits until dead."""
    settings = _sem_embed_settings()
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    worker._ensure_memory_store()  # noqa: SLF001

    entered = Event()
    release = Event()
    poll_calls: list[str] = []

    def slow_bound() -> dict[str, Any] | None:
        # Capture epoch at call time via worker's bound path.
        poll_calls.append("enter")
        entered.set()
        release.wait(timeout=5.0)
        # After stop, epoch should make this a no-op if re-entered; this
        # invocation may complete once if already past the epoch check.
        return worker._encode_poll_once(epoch=epoch)  # noqa: SLF001

    epoch = int(worker._encode_epoch)  # noqa: SLF001
    from elyra.memory.embed.worker import EncodeWorker as EW

    ew = EW(
        poll_once=slow_bound,
        poll_s=0.5,
        wake_event=worker._encode_wake,  # noqa: SLF001
        generation=epoch,
    )
    worker._encode_worker = ew  # noqa: SLF001
    worker._encode_owner = "worker"  # noqa: SLF001
    ew.start()
    assert entered.wait(timeout=2.0)

    dead = worker._stop_encode_worker(join_timeout_s=0.15)  # noqa: SLF001
    assert dead is False
    assert worker._encode_worker is not None  # noqa: SLF001 — zombie retained
    assert worker._encode_worker.is_alive()  # noqa: SLF001
    # Gap must not dual-drain while zombie is alive.
    worker._gap_drain_if_needed()  # noqa: SLF001
    assert worker._gap_drain_active is False  # noqa: SLF001

    # Stale epoch tick is a soft no-op.
    stale = worker._encode_poll_once(epoch=epoch)  # noqa: SLF001
    assert stale is not None
    assert stale.get("reason") == "stale_epoch"

    release.set()
    deadline = time.monotonic() + 2.0
    while worker._encode_worker is not None and worker._encode_worker.is_alive():  # noqa: SLF001
        if time.monotonic() >= deadline:
            break
        time.sleep(0.02)
    # Once dead, restart monitor may clear handle; force gap path.
    if worker._encode_worker is not None and not worker._encode_worker.is_alive():  # noqa: SLF001
        worker._encode_worker = None  # noqa: SLF001
    worker._encode_owner = "worker"  # noqa: SLF001
    # Gap allowed when no live thread.
    worker._gap_drain_if_needed()  # noqa: SLF001


def test_loader_publish_aborted_on_close(paths, monkeypatch):
    """Issue 1: mid-load close must not leave warm embedder resurrected."""
    settings = _sem_embed_settings()
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    opened = Event()
    release = Event()
    closed: list[Any] = []

    class SlowEmb:
        def health(self):
            return {"ok": True, "dim": 8, "model_id": "x", "backend": "mock"}

        def close(self):
            closed.append(self)

    def slow_open(_cfg):
        opened.set()
        release.wait(timeout=5.0)
        return SlowEmb()

    monkeypatch.setattr(
        "elyra.memory.embed.runtime.open_encoder",
        slow_open,
    )

    result_box: list[Any] = []

    def loader() -> None:
        result_box.append(worker._ensure_embedder(role="loader"))  # noqa: SLF001

    t = threading.Thread(target=loader, daemon=True)
    t.start()
    assert opened.wait(timeout=2.0)
    # Close while loader is blocked in open_encoder.
    worker._close_embedder()  # noqa: SLF001
    worker._encode_shutting_down = True  # noqa: SLF001
    release.set()
    t.join(timeout=2.0)
    assert result_box and result_box[0] is None
    assert worker._embedder is None  # noqa: SLF001
    assert worker._embedder_state == "absent"  # noqa: SLF001
    assert closed  # orphan closed


def test_restart_thrash_backoff_delays(paths):
    """Issue 2: after max_restarts in window, next restart is delayed."""
    settings = _sem_embed_settings(
        encode_worker_max_restarts=2,
        encode_worker_restart_window_s=60.0,
        encode_worker_restart_backoff_max_s=30.0,
    )
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    worker._ensure_memory_store()  # noqa: SLF001
    worker._start_encode_worker_if_needed()  # noqa: SLF001
    assert worker._encode_worker is not None  # noqa: SLF001

    for _ in range(2):
        worker._stop_encode_worker()  # noqa: SLF001
        worker._encode_owner = "worker"  # noqa: SLF001
        worker._encode_worker_next_restart_at = 0.0  # noqa: SLF001
        worker._maybe_restart_encode_worker()  # noqa: SLF001
        assert worker._encode_worker is not None  # noqa: SLF001
        assert worker._encode_worker.is_alive()  # noqa: SLF001

    assert worker._encode_worker_restarts == 2  # noqa: SLF001
    # Third death inside window → throttled, no immediate restart.
    worker._stop_encode_worker()  # noqa: SLF001
    worker._encode_owner = "worker"  # noqa: SLF001
    worker._encode_worker_next_restart_at = 0.0  # noqa: SLF001
    worker._maybe_restart_encode_worker()  # noqa: SLF001
    assert worker._encode_worker_restart_throttled is True  # noqa: SLF001
    assert worker._encode_worker_next_restart_at > time.monotonic()  # noqa: SLF001
    assert worker._encode_worker is None  # noqa: SLF001 — not restarted
    assert worker._encode_owner == "worker"  # noqa: SLF001 — never idle
    worker._shutdown_encode()  # noqa: SLF001


def test_embedder_gate_is_singleton(paths):
    """Issue 3: gate is create-once (no lazy TOCTOU)."""
    settings = _sem_embed_settings()
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    g1 = worker._get_embedder_gate()  # noqa: SLF001
    g2 = worker._get_embedder_gate()  # noqa: SLF001
    assert g1 is g2
    assert g1 is worker._embedder_gate  # noqa: SLF001
