"""EmbeddingIndex + KD19 preserve + ready rule (Phase 2 PR3)."""

from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings, memory_meta_path
from elyra.memory.embed.mock import MockEmbedder, mock_vector
from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet, embeddings_are_ready
from elyra.memory.index import (
    EmbeddingIndex,
    LanceEmbeddingIndex,
    MemoryEmbeddingIndex,
    NullEmbeddingIndex,
    ScoredAtom,
    open_embedding_index,
)
from elyra.memory.store import open_memory_store
from elyra.memory.types import Atom, new_atom_id


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
    text: str = "hello",
    status: str = "pending",
    atom_id: str | None = None,
    t_start: str = "2026-07-28T10:00:00Z",
    moment_id: str | None = "m1",
    kind: str = "observation",
    **kwargs: Any,
) -> Atom:
    return Atom(
        atom_id=atom_id or new_atom_id(),
        t_start=t_start,
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=moment_id,
        embedding_status=status,
        **kwargs,
    )


def _emb_set(
    atom_id: str,
    *,
    seed: str = "x",
    joint: bool = True,
    text: bool = True,
) -> EmbeddingSet:
    tv = mock_vector(f"text:{seed}", dim=EMBED_DIM) if text else None
    jv = mock_vector(f"joint:{seed}", dim=EMBED_DIM) if joint else tv
    return EmbeddingSet(
        atom_id=atom_id,
        dim=EMBED_DIM,
        emb_text=tv,
        emb_joint=jv if joint or text else None,
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )


# ── KD20 ready helper ─────────────────────────────────────────────────────


def test_embeddings_are_ready_joint():
    e = _emb_set("a1", joint=True)
    assert embeddings_are_ready(e)
    assert e.is_ready()


def test_embeddings_are_ready_single_modality_text_only():
    e = EmbeddingSet(
        atom_id="a1",
        emb_text=mock_vector("t", dim=EMBED_DIM),
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    assert embeddings_are_ready(e)


def test_embeddings_are_ready_rejects_empty():
    e = EmbeddingSet(atom_id="a1")
    assert not embeddings_are_ready(e)


# ── NullEmbeddingIndex ────────────────────────────────────────────────────


def test_null_index_no_vectors():
    idx = NullEmbeddingIndex()
    assert isinstance(idx, EmbeddingIndex)
    e = _emb_set("a1")
    assert idx.upsert(e) is False
    assert idx.search(mock_vector("q", dim=EMBED_DIM)) == []
    h = idx.health()
    assert h["ok"] is True
    assert h["vectors"] is False
    assert h["vectors_ready"] == 0
    assert idx.optimize()["optimized"] is False


# ── MemoryEmbeddingIndex ──────────────────────────────────────────────────


def test_memory_index_upsert_search_and_ready(store):
    atom = store.put_atom(_atom(text="alpha", status="pending"))
    idx = MemoryEmbeddingIndex(store=store)
    emb = _emb_set(atom.atom_id, seed="alpha")
    assert idx.upsert(emb) is True
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    assert idx.get(atom.atom_id) is not None

    hits = idx.search(emb.emb_joint or emb.emb_text, k=5, channel="joint")
    assert len(hits) == 1
    assert isinstance(hits[0], ScoredAtom)
    assert hits[0].atom_id == atom.atom_id
    assert hits[0].score > 0.99
    h = idx.health()
    assert h["ok"] is True
    assert h["vectors_ready"] == 1
    assert h["backend"] == "memory"


def test_memory_index_filters_moment_kind_time(store):
    a1 = store.put_atom(
        _atom(text="in", t_start="2026-07-28T10:00:00Z", moment_id="mA", kind="observation")
    )
    a2 = store.put_atom(
        _atom(text="out", t_start="2026-07-28T12:00:00Z", moment_id="mB", kind="speak")
    )
    idx = MemoryEmbeddingIndex(store=store)
    idx.upsert(_emb_set(a1.atom_id, seed="in"))
    idx.upsert(_emb_set(a2.atom_id, seed="out"))
    q = mock_vector("joint:in", dim=EMBED_DIM)

    only_mA = idx.search(q, k=10, moment_id="mA")
    assert [h.atom_id for h in only_mA] == [a1.atom_id]

    only_obs = idx.search(q, k=10, kinds=["observation"])
    assert a1.atom_id in {h.atom_id for h in only_obs}
    assert a2.atom_id not in {h.atom_id for h in only_obs}

    window = idx.search(
        q, k=10, t_start="2026-07-28T09:00:00Z", t_end="2026-07-28T11:00:00Z"
    )
    assert [h.atom_id for h in window] == [a1.atom_id]

    excl = idx.search(q, k=10, exclude_atom_ids={a1.atom_id})
    assert a1.atom_id not in {h.atom_id for h in excl}


def test_memory_index_preserve_after_scalar_put_and_links(store):
    """Acceptance (CI): vectors survive put_atom B + update_links on A."""
    a = store.put_atom(_atom(text="atom A", status="pending", atom_id="atom_a"))
    idx = MemoryEmbeddingIndex(store=store)
    emb = _emb_set(a.atom_id, seed="A")
    assert idx.upsert(emb) is True
    assert store.get_atom(a.atom_id).embedding_status == "ready"

    b = store.put_atom(
        _atom(
            text="atom B",
            status="pending",
            atom_id="atom_b",
            t_start="2026-07-28T10:01:00Z",
            prev_atom_id=a.atom_id,
        )
    )
    store.update_links(a.atom_id, next_atom_id=b.atom_id)

    # Index still holds vectors; atom A still ready.
    assert idx.get(a.atom_id) is not None
    assert idx.get(a.atom_id).emb_joint is not None
    a2 = store.get_atom(a.atom_id)
    assert a2 is not None
    assert a2.embedding_status == "ready"
    assert a2.next_atom_id == b.atom_id
    hits = idx.search(emb.emb_joint, k=3)
    assert any(h.atom_id == a.atom_id for h in hits)


def test_memory_index_upsert_rejects_not_ready():
    idx = MemoryEmbeddingIndex()
    empty = EmbeddingSet(atom_id="x")
    assert idx.upsert(empty) is False


def test_open_embedding_index_jsonl_is_null(store):
    idx = open_embedding_index(store)
    assert isinstance(idx, NullEmbeddingIndex)


def test_queue_drain_ready_with_memory_index(store):
    """PR3: EncodeQueue + MemoryEmbeddingIndex → ready."""
    from elyra.memory.embed.queue import EncodeQueue

    atom = store.put_atom(_atom(text="queue me", status="pending"))
    q = EncodeQueue(maxsize=8)
    q.enqueue(atom.atom_id)
    idx = MemoryEmbeddingIndex(store=store)
    stats = q.drain(store, MockEmbedder(), index=idx, max_items=2)
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    assert idx.get(atom.atom_id) is not None


def test_jsonl_health_vectors_false(store):
    h = store.health()
    assert h["ok"] is True
    assert h.get("vectors") is False


# ── Lance path (skip when dep missing or connect unusable) ────────────────


@lru_cache(maxsize=1)
def _lancedb_connect_works() -> bool:
    try:
        import lancedb  # noqa: F401, PLC0415
    except ImportError:
        return False
    code = (
        "import tempfile, lancedb\n"
        "d = tempfile.mkdtemp()\n"
        "db = lancedb.connect(d)\n"
        "db.table_names()\n"
        "print('ok')\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and "ok" in (proc.stdout or "")


lance_required = pytest.mark.skipif(
    not _lancedb_connect_works(),
    reason="lancedb connect unusable on this Python/runtime",
)


@lance_required
def test_lance_upsert_vectors_and_preserve_on_links(paths):
    """Acceptance: upsert A vectors → put B linking A → A vectors intact."""
    from elyra.memory.lance_store import LanceMemoryStore

    store = open_memory_store(paths, MemorySettings(backend="lance", write_atoms=True))
    assert isinstance(store, LanceMemoryStore)
    try:
        assert store.vector_schema_ok is True
        a = store.put_atom(_atom(text="A body", atom_id="lance_a", status="pending"))
        emb = _emb_set(a.atom_id, seed="lanceA")
        assert store.upsert_vectors(a.atom_id, emb) is True
        assert store.get_atom(a.atom_id).embedding_status == "ready"
        got_v = store.get_vectors(a.atom_id)
        assert got_v is not None
        assert got_v.emb_joint is not None
        assert len(got_v.emb_joint) == EMBED_DIM

        b = store.put_atom(
            _atom(
                text="B body",
                atom_id="lance_b",
                t_start="2026-07-28T10:01:00Z",
                prev_atom_id=a.atom_id,
                status="pending",
            )
        )
        store.update_links(a.atom_id, next_atom_id=b.atom_id)

        a2 = store.get_atom(a.atom_id)
        assert a2 is not None
        assert a2.embedding_status == "ready"
        assert a2.next_atom_id == b.atom_id
        still = store.get_vectors(a.atom_id)
        assert still is not None
        assert still.emb_joint is not None
        # Bit-stable enough for mock vectors
        assert still.emb_joint == got_v.emb_joint

        # meta.json epoch
        meta = json.loads(memory_meta_path(paths).read_text(encoding="utf-8"))
        assert meta.get("vector_schema_version") == 1
        assert meta.get("emb_dim") == EMBED_DIM
    finally:
        store.close()


@lance_required
def test_lance_vectors_survive_reopen(paths):
    from elyra.memory.lance_store import LanceMemoryStore

    store = open_memory_store(paths, MemorySettings(backend="lance"))
    try:
        a = store.put_atom(_atom(text="persist vec", atom_id="p1", status="pending"))
        emb = _emb_set(a.atom_id, seed="persist")
        assert store.upsert_vectors(a.atom_id, emb) is True
    finally:
        store.close()

    store2 = open_memory_store(paths, MemorySettings(backend="lance"))
    try:
        assert isinstance(store2, LanceMemoryStore)
        v = store2.get_vectors("p1")
        assert v is not None
        assert v.emb_joint is not None
        assert store2.get_atom("p1").embedding_status == "ready"
    finally:
        store2.close()


@lance_required
def test_lance_embedding_index_search_filters(paths):
    store = open_memory_store(paths, MemorySettings(backend="lance"))
    try:
        a1 = store.put_atom(
            _atom(text="s1", atom_id="s1", t_start="2026-07-28T10:00:00Z", moment_id="m1")
        )
        a2 = store.put_atom(
            _atom(text="s2", atom_id="s2", t_start="2026-07-28T11:00:00Z", moment_id="m2")
        )
        idx = LanceEmbeddingIndex(store)
        assert isinstance(idx, EmbeddingIndex)
        e1 = _emb_set(a1.atom_id, seed="s1")
        e2 = _emb_set(a2.atom_id, seed="s2")
        assert idx.upsert(e1) is True
        assert idx.upsert(e2) is True
        hits = idx.search(e1.emb_joint, k=5, moment_id="m1")
        assert [h.atom_id for h in hits] == [a1.atom_id]
        h = idx.health()
        assert h["ok"] is True
        assert h["backend"] == "lance"
        assert h["vectors_ready"] >= 2
        assert idx.optimize()["optimized"] is False
    finally:
        store.close()


@lance_required
def test_lance_phase1_scalar_table_migrates(paths, tmp_path):
    """Fixture Phase 1 scalar-only table → open Phase 2 store migrates."""
    import pyarrow as pa
    import lancedb

    from elyra.memory.config import lance_root
    from elyra.memory.lance_store import LanceMemoryStore, _STRING_COLS

    root = lance_root(paths)
    root.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(root))
    fields = [pa.field(n, pa.utf8()) for n in _STRING_COLS]
    fields.append(pa.field("schema_version", pa.int64()))
    schema = pa.schema(fields)
    row = {n: None for n in _STRING_COLS}
    row.update(
        {
            "atom_id": "legacy1",
            "t_start": "2026-07-28T10:00:00Z",
            "kind": "observation",
            "content_text": "legacy body",
            "content_ref": "inline",
            "embedding_status": "none",
            "media_ids_json": "[]",
            "meta_json": "{}",
            "moment_id": "m1",
            "schema_version": 1,
        }
    )
    db.create_table("atoms", pa.Table.from_pylist([row], schema=schema))

    # meta without vector epoch
    meta_path = memory_meta_path(paths)
    meta_path.write_text(
        json.dumps({"schema_version": 1, "backend": "lance", "created_at": "x"}),
        encoding="utf-8",
    )

    store = LanceMemoryStore(paths, MemorySettings(backend="lance"))
    try:
        assert store.vector_schema_ok is True
        got = store.get_atom("legacy1")
        assert got is not None
        assert got.content_text == "legacy body"
        assert got.embedding_status == "none"
        emb = _emb_set("legacy1", seed="legacy")
        assert store.upsert_vectors("legacy1", emb) is True
        assert store.get_vectors("legacy1") is not None
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assert meta["vector_schema_version"] == 1
        assert meta["emb_dim"] == EMBED_DIM
    finally:
        store.close()


@lance_required
def test_lance_health_reports_vectors(paths):
    store = open_memory_store(paths, MemorySettings(backend="lance"))
    try:
        h = store.health()
        assert h["ok"] is True
        assert h["vectors"] is True
        assert h.get("vector_schema_version") == 1
    finally:
        store.close()
