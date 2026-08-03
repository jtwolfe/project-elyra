"""EncodeQueue, encode path, write hooks, idle drain (Phase 2 PR2)."""

from __future__ import annotations

from dataclasses import replace
from threading import Event, Thread
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
from elyra.memory.embed.queue import (
    EncodePriority,
    EncodeQueue,
    catchup_none_atoms_for_encode,
    scan_pending_into_queue,
)
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
    # Same priority re-enqueue is still a no-op.
    assert q.enqueue("a1", priority=EncodePriority.CATCHUP) is False
    assert q.depth_by_priority() == {
        EncodePriority.ATOM_CREATE.value: 1,
        EncodePriority.CATCHUP.value: 0,
    }


def test_enqueue_promote_catchup_to_atom_create():
    """Already-queued P2 item is promoted to P1 on atom_create enqueue."""
    q = EncodeQueue(maxsize=16)
    assert q.enqueue("a1", priority=EncodePriority.CATCHUP) is True
    assert q.depth_by_priority()[EncodePriority.CATCHUP.value] == 1
    # Promote P2 → P1
    assert q.enqueue("a1", priority=EncodePriority.ATOM_CREATE) is True
    assert q.contains("a1")
    assert len(q) == 1
    depths = q.depth_by_priority()
    assert depths[EncodePriority.ATOM_CREATE.value] == 1
    assert depths[EncodePriority.CATCHUP.value] == 0
    # Second promote / same-lane enqueue is no-op
    assert q.enqueue("a1", priority=EncodePriority.ATOM_CREATE) is False
    item = q.pop_next_bulk()
    assert item == ("a1", EncodePriority.ATOM_CREATE)


def test_pop_order_p1_before_p2():
    q = EncodeQueue(maxsize=16)
    q.enqueue("c1", priority=EncodePriority.CATCHUP)
    q.enqueue("c2", priority=EncodePriority.CATCHUP)
    q.enqueue("n1", priority=EncodePriority.ATOM_CREATE)
    assert q.pop_next() == "n1"
    assert q.pop_next_bulk() == ("c1", EncodePriority.CATCHUP)
    assert q.pop_next() == "c2"
    assert q.pop_next() is None


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
    # Overflow (all P1): drop oldest P1 a_old → skipped
    assert q.enqueue("a_new", store=store)
    assert not q.contains("a_old")
    assert q.contains("a_mid")
    assert q.contains("a_new")
    assert q.dropped_total() == 1

    old = store.get_atom("a_old")
    assert old is not None
    assert old.embedding_status == "skipped"
    assert old.meta.get("embed_error") == "queue_overflow"


def test_overflow_drops_p2_before_p1(store):
    """At capacity, oldest catchup is dropped before any atom_create."""
    q = EncodeQueue(maxsize=2)
    for aid, text in (
        ("c_old", "catchup old"),
        ("n1", "create 1"),
        ("c_new", "catchup new"),
    ):
        store.put_atom(_atom(atom_id=aid, text=text, status="pending"))

    assert q.enqueue("c_old", priority=EncodePriority.CATCHUP, store=store)
    assert q.enqueue("n1", priority=EncodePriority.ATOM_CREATE, store=store)
    # Full: P2 has c_old, P1 has n1. Enqueue another catchup → drop c_old (P2).
    assert q.enqueue("c_new", priority=EncodePriority.CATCHUP, store=store)
    assert not q.contains("c_old")
    assert q.contains("n1")
    assert q.contains("c_new")
    assert q.dropped_total() == 1
    dropped = store.get_atom("c_old")
    assert dropped is not None
    assert dropped.embedding_status == "skipped"
    assert dropped.meta.get("embed_error") == "queue_overflow"
    # n1 (P1) must survive; next overflow with only P1+P2 present drops P2 again.
    store.put_atom(_atom(atom_id="n2", text="create 2", status="pending"))
    assert q.enqueue("n2", priority=EncodePriority.ATOM_CREATE, store=store)
    assert not q.contains("c_new")  # P2 dropped before n1
    assert q.contains("n1")
    assert q.contains("n2")
    assert q.dropped_total() == 2


def test_enqueue_drop_oldest_without_store():
    q = EncodeQueue(maxsize=1)
    assert q.enqueue("x")
    assert q.enqueue("y")  # drops x, no store → no crash
    assert q.contains("y")
    assert not q.contains("x")
    assert q.dropped_total() == 1


def test_concurrent_enqueue_and_drain(store):
    """RLock correctness: concurrent enqueue + drain loses no double-membership."""
    n = 80
    ids = []
    for i in range(n):
        a = store.put_atom(
            _atom(
                text=f"concurrent {i}",
                status="pending",
                t_start=f"2026-07-28T10:{i % 60:02d}:00Z",
                atom_id=f"conc_{i:04d}",
            )
        )
        ids.append(a.atom_id)

    q = EncodeQueue(maxsize=n + 8)
    emb = MockEmbedder()
    errors: list[BaseException] = []

    def enqueuer(chunk: list[str], priority: EncodePriority) -> None:
        try:
            for aid in chunk:
                q.enqueue(aid, priority=priority, store=store)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def drainer() -> int:
        total = 0
        try:
            # Several short drains interleaved with enqueues.
            for _ in range(20):
                stats = q.drain(
                    store, emb, index=None, max_ms=2000, max_items=8
                )
                total += stats["processed"]
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        return total

    mid = n // 2
    threads = [
        Thread(
            target=enqueuer,
            args=(ids[:mid], EncodePriority.ATOM_CREATE),
            name="enq-p1",
        ),
        Thread(
            target=enqueuer,
            args=(ids[mid:], EncodePriority.CATCHUP),
            name="enq-p2",
        ),
        Thread(target=drainer, name="drain-a"),
        Thread(target=drainer, name="drain-b"),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), f"thread hung: {t.name}"

    assert not errors, f"worker errors: {errors}"
    # Finish remaining items single-threaded.
    while len(q) > 0:
        stats = q.drain(store, emb, index=None, max_ms=5000, max_items=32)
        if stats["processed"] == 0:
            break

    assert len(q) == 0
    assert q.depth_by_priority() == {
        EncodePriority.ATOM_CREATE.value: 0,
        EncodePriority.CATCHUP.value: 0,
    }
    # Every atom was processed at least to encode_ok pending (no lost ids).
    ok_count = 0
    for aid in ids:
        got = store.get_atom(aid)
        assert got is not None
        assert got.embedding_status in ("pending", "skipped", "failed", "ready")
        if got.meta.get("embed_encode_ok"):
            ok_count += 1
    assert ok_count == n


def test_concurrent_enqueue_pop_no_double_membership():
    """Hammer enqueue + pop_next_bulk; each id leaves the queue at most once."""
    q = EncodeQueue(maxsize=256)
    n = 200
    popped: list[str] = []
    stop = Event()

    def producer() -> None:
        for i in range(n):
            q.enqueue(
                f"id_{i}",
                priority=(
                    EncodePriority.ATOM_CREATE
                    if i % 3 == 0
                    else EncodePriority.CATCHUP
                ),
            )
            # Also re-enqueue some for promote / dedupe coverage.
            if i % 5 == 0:
                q.enqueue(f"id_{i}", priority=EncodePriority.ATOM_CREATE)

    def consumer() -> None:
        while not stop.is_set() or len(q) > 0:
            item = q.pop_next_bulk()
            if item is None:
                if stop.is_set():
                    break
                continue
            popped.append(item[0])

    pt = Thread(target=producer)
    ct = Thread(target=consumer)
    ct.start()
    pt.start()
    pt.join(timeout=15)
    stop.set()
    ct.join(timeout=15)
    assert not pt.is_alive() and not ct.is_alive()

    # Drain any leftover single-threaded
    while True:
        item = q.pop_next_bulk()
        if item is None:
            break
        popped.append(item[0])

    assert len(q) == 0
    # Each id appears exactly once in pops (dedupe + promote never double-queue).
    assert len(popped) == n
    assert set(popped) == {f"id_{i}" for i in range(n)}


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


def test_drain_without_index_then_with_index_becomes_ready(store):
    """Issue 1: encode_ok short-circuit must not block a later index upsert."""

    class _Idx:
        def __init__(self) -> None:
            self.seen: dict[str, Any] = {}

        def upsert(self, atom_id: str, embeddings: Any) -> bool:
            self.seen[atom_id] = embeddings
            return True

    atom = store.put_atom(_atom(text="later index", status="pending"))
    q = EncodeQueue(maxsize=4)
    emb = MockEmbedder()
    q.enqueue(atom.atom_id)
    q.drain(store, emb, index=None, max_items=2)
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "pending"
    assert got.meta.get("embed_encode_ok") is True

    # Second drain with index must re-encode and upsert → ready.
    q.enqueue(atom.atom_id)
    idx = _Idx()
    stats = q.drain(store, emb, index=idx, max_items=2)
    assert stats["ok"] == 1
    assert atom.atom_id in idx.seen
    got2 = store.get_atom(atom.atom_id)
    assert got2 is not None
    assert got2.embedding_status == "ready"


def test_media_only_unresolved_stays_pending(store):
    """Issue 3: media-only + no MediaStore must not permanent-skip."""
    atom = store.put_atom(
        _atom(text="", status="pending", media_ids=("att_missing_1",))
    )
    # Ensure empty text persisted (prepare may leave "").
    from elyra.memory.types import atom_replace

    atom = store.put_atom(
        atom_replace(
            atom,
            content_text="",
            media_ids=("att_missing_1",),
            embedding_status="pending",
        ),
        notify=False,
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    stats = q.drain(store, MockEmbedder(), media_store=None, max_items=2)
    assert stats["skipped"] == 1  # not encoded this tick
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "pending"
    assert got.meta.get("embed_error") == "media_unresolved"
    # Attempts not burned
    assert int(got.meta.get("embed_attempts") or 0) == 0


def test_encode_atom_media_unresolved_error():
    result = encode_atom(
        MockEmbedder(),
        _atom(text="", media_ids=("m1",)),
        media_store=None,
    )
    assert result.error == "media_unresolved"
    assert result.embeddings is None


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
    # Scan always enqueues at catchup (P2) priority.
    assert q.depth_by_priority() == {
        EncodePriority.ATOM_CREATE.value: 0,
        EncodePriority.CATCHUP.value: 2,
    }
    # Second scan dedupes
    assert scan_pending_into_queue(store, q, limit=10) == 0


def test_catchup_none_atoms_for_encode(store):
    """OQ4: historical none experience atoms become pending; summaries skipped."""
    exp = store.put_atom(
        _atom(text="old chat", status="none", t_start="2026-07-28T10:00:00Z")
    )
    store.put_atom(
        _atom(
            text="ladder body",
            status="none",
            kind="summary",
            t_start="2026-07-28T10:01:00Z",
        )
    )
    store.put_atom(
        _atom(text="already", status="pending", t_start="2026-07-28T10:02:00Z")
    )
    # Outside horizon
    store.put_atom(
        _atom(text="ancient", status="none", t_start="2020-01-01T00:00:00Z")
    )
    n = catchup_none_atoms_for_encode(
        store,
        limit=10,
        horizon_hours=168.0,
        now_iso="2026-07-28T12:00:00Z",
    )
    assert n == 1
    assert store.get_atom(exp.atom_id).embedding_status == "pending"
    # Second call: no more none experience in horizon
    assert (
        catchup_none_atoms_for_encode(
            store, limit=10, horizon_hours=168.0, now_iso="2026-07-28T12:00:00Z"
        )
        == 0
    )


def test_scan_includes_encode_ok_pending(store):
    """Issue 9: scan re-queues encode_ok pending so a later index can upsert."""
    a = store.put_atom(_atom(text="done", status="pending"))
    q = EncodeQueue(maxsize=4)
    q.enqueue(a.atom_id)
    q.drain(store, MockEmbedder(), index=None, max_items=1)
    got = store.get_atom(a.atom_id)
    assert got is not None
    assert got.meta.get("embed_encode_ok") is True
    assert got.embedding_status == "pending"
    # Idle scan must re-enqueue (process_one short-circuits when index is None).
    n = scan_pending_into_queue(store, q, limit=10)
    assert n == 1
    assert q.contains(a.atom_id)
    stats = q.drain(store, MockEmbedder(), index=None, max_items=1)
    assert stats["ok"] == 1
    assert store.get_atom(a.atom_id).embedding_status == "pending"


def test_idle_scan_then_index_drain_becomes_ready(store):
    """Issue 9: no-index drain → scan+drain with index (no manual enqueue) → ready."""

    class _Idx:
        def __init__(self) -> None:
            self.seen: dict[str, Any] = {}

        def upsert(self, atom_id: str, embeddings: Any) -> bool:
            self.seen[atom_id] = embeddings
            return True

    a = store.put_atom(_atom(text="handoff via scan", status="pending"))
    q = EncodeQueue(maxsize=4)
    emb = MockEmbedder()
    q.enqueue(a.atom_id)
    q.drain(store, emb, index=None, max_items=1)
    assert store.get_atom(a.atom_id).embedding_status == "pending"
    assert store.get_atom(a.atom_id).meta.get("embed_encode_ok") is True
    assert len(q) == 0

    # Idle path: scan fills queue; drain with index promotes to ready.
    n = scan_pending_into_queue(store, q, limit=10)
    assert n == 1
    idx = _Idx()
    stats = q.drain(store, emb, index=idx, max_items=2)
    assert stats["ok"] == 1
    assert a.atom_id in idx.seen
    assert store.get_atom(a.atom_id).embedding_status == "ready"


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
            encode_queue_max=2,
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
    # Hook must not enqueue while embed off (Issue 2).
    assert worker._encode_queue is not None  # noqa: SLF001
    assert not worker._encode_queue.contains(atom.atom_id)  # noqa: SLF001
    # Drain should no-op (embed_enabled false).
    worker._idle_memory_encode()  # noqa: SLF001
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "pending"
    assert not got.meta.get("embed_encode_ok")


def test_semantic_on_embed_off_overflow_leaves_pending(paths):
    """Issue 2: many pending puts with embed off must not skip via overflow."""
    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            semantic_enabled=True,
            embed_enabled=False,
            encode_queue_max=2,
        ),
    )
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=Event(),
        settings=settings,
    )
    store = worker._ensure_memory_store()  # noqa: SLF001
    ids: list[str] = []
    for i in range(5):
        atom = promote_beat(
            store,
            f"m{i}",
            {
                "type": "model",
                "content": f"pending body number {i} long enough " * 4,
                "ts": f"2026-07-28T12:{i:02d}:00Z",
            },
            settings=settings.memory,
        )
        assert atom is not None
        ids.append(atom.atom_id)
        assert atom.embedding_status == "pending"

    # Queue empty (hook no-ops when embed off); all stay pending.
    assert worker._encode_queue is not None  # noqa: SLF001
    assert len(worker._encode_queue) == 0  # noqa: SLF001
    for aid in ids:
        got = store.get_atom(aid)
        assert got is not None
        assert got.embedding_status == "pending"
        assert got.meta.get("embed_error") != "queue_overflow"


def test_hook_skips_encode_ok_same_fingerprint(paths):
    """Issue 4: re-put of encode_ok pending with same content does not re-enqueue."""
    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            semantic_enabled=True,
            embed_enabled=True,
            embed_backend="mock",
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
            "content": "encode once then re-put " * 4,
            "ts": "2026-07-28T13:00:00Z",
        },
        settings=settings.memory,
    )
    assert atom is not None
    worker._idle_memory_encode()  # noqa: SLF001
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.meta.get("embed_encode_ok") is True
    # Clear queue, re-put same content with pending + encode_ok
    worker._encode_queue.clear()  # noqa: SLF001
    store.put_atom(got)  # notify=True → hook should no-op
    assert not worker._encode_queue.contains(got.atom_id)  # noqa: SLF001


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
