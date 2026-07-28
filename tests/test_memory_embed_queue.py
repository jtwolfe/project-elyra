"""EncodeQueue, encode path, write hooks, idle drain (Phase 2 PR2)."""

from __future__ import annotations

from dataclasses import replace
from threading import Event
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.llm.client import StubChatClient
from elyra.memory.config import MemorySettings
from elyra.memory.embed.encode import (
    content_fingerprint,
    encode_atom,
    is_embeddable,
)
from elyra.memory.embed.mock import MockEmbedder
from elyra.memory.embed.queue import EncodeQueue, scan_pending_into_queue
from elyra.memory.promote import promote_beat, promote_wake_observation
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
    kind: str = "observation",
    atom_id: str | None = None,
    media_ids: tuple[str, ...] = (),
    **kwargs: Any,
) -> Atom:
    return Atom(
        atom_id=atom_id or new_atom_id(),
        t_start=kwargs.pop("t_start", "2026-07-28T10:00:00Z"),
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=kwargs.pop("moment_id", "m1"),
        media_ids=media_ids,
        embedding_status=status,
        **kwargs,
    )


# ── EncodeQueue basics ─────────────────────────────────────────────────────


def test_enqueue_dedupe():
    q = EncodeQueue(maxsize=16)
    assert q.enqueue("a1") is True
    assert q.enqueue("a1") is False
    assert len(q) == 1
    assert q.contains("a1")


def test_enqueue_drop_oldest_marks_skipped(store):
    q = EncodeQueue(maxsize=2)
    a0 = _atom(atom_id="a_old", text="oldest", status="pending")
    a1 = _atom(atom_id="a_mid", text="mid", status="pending")
    a2 = _atom(atom_id="a_new", text="newest", status="pending")
    store.put_atom(a0)
    store.put_atom(a1)
    store.put_atom(a2)

    assert q.enqueue("a_old", store=store)
    assert q.enqueue("a_mid", store=store)
    # Overflow: drop a_old → skipped
    assert q.enqueue("a_new", store=store)
    assert not q.contains("a_old")
    assert q.contains("a_mid")
    assert q.contains("a_new")
    assert q.dropped_total() == 1

    old = store.get_atom("a_old")
    assert old is not None
    assert old.embedding_status == "skipped"
    assert old.meta.get("embed_error") == "queue_overflow"


def test_enqueue_drop_oldest_without_store():
    q = EncodeQueue(maxsize=1)
    assert q.enqueue("x")
    assert q.enqueue("y")  # drops x, no store → no crash
    assert q.contains("y")
    assert not q.contains("x")
    assert q.dropped_total() == 1


def test_drain_leaves_pending_without_index(store):
    """KD8 / PR2: successful encode without index must NOT set ready."""
    atom = store.put_atom(_atom(text="encode me please", status="pending"))
    q = EncodeQueue(maxsize=8)
    q.enqueue(atom.atom_id, store=store)
    emb = MockEmbedder()
    stats = q.drain(store, emb, index=None, max_ms=500, max_items=4)
    assert stats["ok"] == 1
    assert stats["processed"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "pending"
    assert got.meta.get("embed_encode_ok") is True
    assert got.meta.get("embed_content_fp") == content_fingerprint(got)
    assert "text" in (got.meta.get("embed_channels") or [])


def test_drain_idempotent_after_encode_ok(store):
    atom = store.put_atom(_atom(text="once", status="pending"))
    q = EncodeQueue(maxsize=8)
    emb = MockEmbedder()
    q.enqueue(atom.atom_id)
    q.drain(store, emb, index=None, max_items=4)
    # Re-enqueue and drain again — should short-circuit, stay pending.
    q.enqueue(atom.atom_id)
    stats = q.drain(store, emb, index=None, max_items=4)
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "pending"
    assert int(got.meta.get("embed_attempts") or 0) == 1  # not re-bumped


def test_drain_ready_when_index_upserts(store):
    """In-memory index may mark ready (tests / PR3); production has no index."""

    class _Idx:
        def __init__(self) -> None:
            self.seen: dict[str, Any] = {}

        def upsert(self, atom_id: str, embeddings: Any) -> bool:
            self.seen[atom_id] = embeddings
            return True

    atom = store.put_atom(_atom(text="with index", status="pending"))
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    idx = _Idx()
    stats = q.drain(store, MockEmbedder(), index=idx, max_items=2)
    assert stats["ok"] == 1
    assert atom.atom_id in idx.seen
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"


def test_drain_skips_empty_content(store):
    atom = store.put_atom(
        _atom(text="", status="pending", media_ids=())
    )
    # Force empty body past put prepare.
    from elyra.memory.types import atom_replace

    atom = store.put_atom(
        atom_replace(atom, content_text="", embedding_status="pending"),
        notify=False,
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    stats = q.drain(store, MockEmbedder(), max_items=2)
    assert stats["skipped"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "skipped"


def test_drain_failed_then_max_attempts(store):
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

    atom = store.put_atom(_atom(text="will fail", status="pending"))
    q = EncodeQueue(maxsize=4)
    emb = Boom()

    # attempts 1..2 stay pending; attempt 3 → failed
    for expected_attempts in (1, 2, 3):
        q.enqueue(atom.atom_id)
        q.drain(store, emb, max_items=1, max_attempts=3)
        got = store.get_atom(atom.atom_id)
        assert got is not None
        assert int(got.meta.get("embed_attempts") or 0) == expected_attempts
        if expected_attempts < 3:
            assert got.embedding_status == "pending"
        else:
            assert got.embedding_status == "failed"
            assert "boom" in str(got.meta.get("embed_error") or "")


def test_drain_respects_max_items(store):
    q = EncodeQueue(maxsize=16)
    ids = []
    for i in range(5):
        a = store.put_atom(
            _atom(text=f"item {i}", status="pending", t_start=f"2026-07-28T10:0{i}:00Z")
        )
        ids.append(a.atom_id)
        q.enqueue(a.atom_id)
    stats = q.drain(store, MockEmbedder(), max_items=2, max_ms=5000)
    assert stats["processed"] == 2
    assert stats["remaining"] == 3


def test_drain_never_raises_on_store_errors():
    """Broken store methods must not propagate out of drain."""

    class BadStore:
        def get_atom(self, atom_id: str):
            raise RuntimeError("db down")

        def put_atom(self, atom, **kw):
            raise RuntimeError("db down")

    q = EncodeQueue(maxsize=4)
    q.enqueue("ghost")
    stats = q.drain(BadStore(), MockEmbedder(), max_items=2)
    assert stats["processed"] == 1
    assert stats["failed"] == 1


def test_scan_pending_into_queue(store):
    p1 = store.put_atom(_atom(text="p1", status="pending", t_start="2026-07-28T10:00:00Z"))
    store.put_atom(_atom(text="ready-ish", status="none", t_start="2026-07-28T10:01:00Z"))
    p2 = store.put_atom(_atom(text="p2", status="pending", t_start="2026-07-28T10:02:00Z"))
    q = EncodeQueue(maxsize=16)
    n = scan_pending_into_queue(store, q, limit=10)
    assert n == 2
    assert q.contains(p1.atom_id)
    assert q.contains(p2.atom_id)
    # Second scan dedupes
    assert scan_pending_into_queue(store, q, limit=10) == 0


def test_scan_skips_encode_ok_pending(store):
    a = store.put_atom(_atom(text="done", status="pending"))
    q = EncodeQueue(maxsize=4)
    q.enqueue(a.atom_id)
    q.drain(store, MockEmbedder(), max_items=1)
    # Atom still pending with encode_ok — scan must not re-enqueue.
    n = scan_pending_into_queue(store, q, limit=10)
    assert n == 0
    assert len(q) == 0


# ── encode_atom ────────────────────────────────────────────────────────────


def test_encode_atom_text_path():
    emb = MockEmbedder()
    atom = _atom(text="hello world")
    result = encode_atom(emb, atom)
    assert result.status == "ready"
    assert result.embeddings is not None
    assert result.embeddings.emb_text is not None
    assert "text" in result.embeddings.channels_present


def test_encode_atom_skips_moment_meta():
    atom = _atom(text="meta", kind="moment_meta")
    result = encode_atom(MockEmbedder(), atom)
    assert result.status == "skipped"
    assert result.error == "kind_skipped"


def test_encode_atom_never_raises():
    class Boom:
        def health(self):
            return {"ok": True}

        def encode_text(self, t):
            raise ValueError("nope")

        def encode_image(self, x):
            raise ValueError("nope")

        def encode_audio(self, x):
            raise ValueError("nope")

        def encode_video(self, x):
            raise ValueError("nope")

        def encode_joint(self, p):
            raise ValueError("nope")

    result = encode_atom(Boom(), _atom(text="x"))
    assert result.status == "failed"
    assert "nope" in (result.error or "")


def test_is_embeddable():
    assert is_embeddable(_atom(text="hi")) is True
    assert is_embeddable(_atom(text="", media_ids=("m1",))) is True
    assert is_embeddable(_atom(text="", media_ids=())) is False
    assert is_embeddable(_atom(text="x", kind="moment_meta")) is False


# ── write hooks ────────────────────────────────────────────────────────────


def test_write_hook_fires_after_put(store):
    seen: list[str] = []

    def hook(atom: Atom) -> None:
        seen.append(atom.atom_id)

    store.set_write_hook(hook)
    a = store.put_atom(_atom(text="hooked", status="pending"))
    assert a.atom_id in seen


def test_write_hook_exception_does_not_break_put(store):
    def bad_hook(atom: Atom) -> None:
        raise RuntimeError("hook boom")

    store.set_write_hook(bad_hook)
    a = store.put_atom(_atom(text="still stored"))
    assert store.get_atom(a.atom_id) is not None


def test_write_hook_notify_false_skips(store):
    seen: list[str] = []
    store.set_write_hook(lambda a: seen.append(a.atom_id))
    a = store.put_atom(_atom(text="silent"), notify=False)
    assert seen == []
    store.put_atom(a, notify=True)
    assert a.atom_id in seen


def test_hook_plus_queue_integration(store):
    q = EncodeQueue(maxsize=8)

    def on_written(atom: Atom) -> None:
        if atom.embedding_status == "pending":
            q.enqueue(atom.atom_id, store=store)

    store.set_write_hook(on_written)
    a = store.put_atom(_atom(text="via hook", status="pending"))
    assert q.contains(a.atom_id)
    stats = q.drain(store, MockEmbedder(), max_items=1)
    assert stats["ok"] == 1
    got = store.get_atom(a.atom_id)
    assert got is not None
    assert got.embedding_status == "pending"  # no false ready


# ── promote pending ────────────────────────────────────────────────────────


def test_promote_sets_pending_when_semantic_enabled(store):
    settings = MemorySettings(write_atoms=True, semantic_enabled=True)
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "model",
            "content": "memorable free text long enough to promote " * 2,
            "ts": "2026-07-28T10:00:00Z",
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.embedding_status == "pending"
    # Store write should have persisted pending.
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "pending"


def test_promote_none_when_semantic_off(store):
    settings = MemorySettings(write_atoms=True, semantic_enabled=False)
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "model",
            "content": "memorable free text long enough to promote " * 2,
            "ts": "2026-07-28T10:00:00Z",
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.embedding_status == "none"


def test_promote_wake_pending_when_semantic(store):
    settings = MemorySettings(write_atoms=True, semantic_enabled=True)
    atom = promote_wake_observation(
        store,
        "m1",
        content="hello from wake",
        message_id="msg-1",
        settings=settings,
    )
    assert atom is not None
    assert atom.embedding_status == "pending"


def test_promote_speak_pending_semantic(store):
    settings = MemorySettings(write_atoms=True, semantic_enabled=True)
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": '{"text": "spoken line for memory"}',
            "ts": "2026-07-28T10:00:00Z",
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.embedding_status == "pending"


# ── worker idle drain ──────────────────────────────────────────────────────


def test_worker_installs_hook_and_idle_drain(paths):
    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            semantic_enabled=True,
            embed_enabled=True,
            embed_backend="mock",
            encode_max_items_per_tick=8,
            encode_max_ms_per_tick=2000,
        ),
    )
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    store = worker._ensure_memory_store()  # noqa: SLF001
    assert store is not None
    assert worker._encode_queue is not None  # noqa: SLF001

    # Promote with semantic → pending + hook enqueue
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "model",
            "content": "worker encode path body " * 4,
            "ts": "2026-07-28T11:00:00Z",
        },
        settings=settings.memory,
    )
    assert atom is not None
    assert atom.embedding_status == "pending"
    assert worker._encode_queue.contains(atom.atom_id)  # noqa: SLF001

    worker._idle_memory_encode()  # noqa: SLF001
    got = store.get_atom(atom.atom_id)
    assert got is not None
    # No index → stay pending with encode_ok (KD8).
    assert got.embedding_status == "pending"
    assert got.meta.get("embed_encode_ok") is True


def test_worker_encode_noop_when_embed_disabled(paths):
    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            semantic_enabled=True,
            embed_enabled=False,
        ),
    )
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    store = worker._ensure_memory_store()  # noqa: SLF001
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "model",
            "content": "pending forever until embed on " * 3,
            "ts": "2026-07-28T11:00:00Z",
        },
        settings=settings.memory,
    )
    assert atom is not None
    assert atom.embedding_status == "pending"
    # Drain should no-op (embed_enabled false).
    worker._idle_memory_encode()  # noqa: SLF001
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "pending"
    assert not got.meta.get("embed_encode_ok")


def test_worker_encode_noop_when_semantic_off(paths):
    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            semantic_enabled=False,
            embed_enabled=True,
        ),
    )
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    store = worker._ensure_memory_store()  # noqa: SLF001
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "model",
            "content": "phase1 parity body long enough " * 3,
            "ts": "2026-07-28T11:00:00Z",
        },
        settings=settings.memory,
    )
    assert atom is not None
    assert atom.embedding_status == "none"
    worker._idle_memory_encode()  # noqa: SLF001 — no-op, no raise
