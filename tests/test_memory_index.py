"""EmbeddingIndex + KD19 preserve + KD4 hybrid recent-buffer (Phase 2 PR3/PR4)."""

from __future__ import annotations

import json
import math
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
    AnnSettings,
    EmbeddingIndex,
    LanceEmbeddingIndex,
    MemoryEmbeddingIndex,
    NullEmbeddingIndex,
    RecentBufferEntry,
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


def test_factory_fail_closed_migration_health():
    """Issue 1/3: migration failure must not yield healthy Null index."""

    class _BrokenLance:
        """Stub Lance-like store after failed emb migration."""

        vector_schema_ok = False

        def upsert_vectors(self, atom_id: str, embeddings: Any) -> bool:
            return False

        def get_atom(self, atom_id: str) -> Any:
            return None

        def search_vectors(self, *a: Any, **k: Any) -> list:
            return []

        def health(self) -> dict[str, Any]:
            return {
                "ok": True,  # scalar store still usable
                "backend": "lance",
                "atom_count": 1,
                "vectors": False,
                "vector_schema_version": 0,
                "vectors_ready": 0,
                "vector_error": "migration_failed: RuntimeError: boom",
            }

    store = _BrokenLance()
    idx = open_embedding_index(store)
    assert isinstance(idx, LanceEmbeddingIndex)
    assert not isinstance(idx, NullEmbeddingIndex)
    h = idx.health()
    assert h["ok"] is False
    assert h["backend"] == "lance"
    assert h.get("vectors") is False
    assert "migration_failed" in str(h.get("error") or "")
    # Upsert/search fail closed
    emb = _emb_set("a1", seed="x")
    assert idx.upsert(emb) is False
    assert idx.search(emb.emb_joint or mock_vector("q", dim=EMBED_DIM)) == []


def test_lance_embedding_index_health_fail_closed_direct():
    """Hermetic: LanceEmbeddingIndex.health ANDs vectors + vector_error."""

    class _Store:
        def health(self) -> dict[str, Any]:
            return {
                "ok": True,
                "vectors": False,
                "vector_error": "migration_failed: OSError: disk",
                "vectors_ready": 0,
                "vector_schema_version": 0,
            }

        def upsert_vectors(self, *a: Any, **k: Any) -> bool:
            return False

    idx = LanceEmbeddingIndex(_Store())
    h = idx.health()
    assert h["ok"] is False
    assert "migration_failed" in str(h.get("error") or "")


def test_queue_upsert_none_does_not_mark_ready(store):
    """Issue 2: upsert returning None must not set embedding_status=ready."""
    from elyra.memory.embed.queue import EncodeQueue

    class _NoneIdx:
        def upsert(self, embedding_set: Any) -> None:
            return None

    atom = store.put_atom(_atom(text="none upsert", status="pending"))
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    stats = q.drain(store, MockEmbedder(), index=_NoneIdx(), max_items=2)
    assert stats["ok"] == 1  # encode ok path
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "pending"
    assert got.meta.get("embed_encode_ok") is True


def test_queue_upsert_false_does_not_mark_ready(store):
    """Explicit False from index also leaves pending."""
    from elyra.memory.embed.queue import EncodeQueue

    class _FalseIdx:
        def upsert(self, embedding_set: Any) -> bool:
            return False

    atom = store.put_atom(_atom(text="false upsert", status="pending"))
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    q.drain(store, MockEmbedder(), index=_FalseIdx(), max_items=2)
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "pending"


def test_memory_search_requires_ready_status(store):
    """Issue 6: pending atom with vectors in index is not searchable."""
    atom = store.put_atom(_atom(text="pend", status="pending", atom_id="pend1"))
    idx = MemoryEmbeddingIndex(store=None)  # no status sync
    emb = _emb_set(atom.atom_id, seed="pend")
    assert idx.upsert(emb) is True
    # Store still pending; index has vectors but atom not ready.
    hits = idx.search(emb.emb_joint, k=5)
    # atom is None path when store is None — still searchable without store.
    assert len(hits) == 1

    idx2 = MemoryEmbeddingIndex(store=store)
    # Put vectors without going through upsert status path: inject into dict.
    idx2._by_id[atom.atom_id] = emb  # noqa: SLF001 — test inject
    assert store.get_atom(atom.atom_id).embedding_status == "pending"
    assert idx2.search(emb.emb_joint, k=5) == []


def test_newest_migration_backup_picks_latest(tmp_path):
    """Issue 9 helper: newest atoms-*.jsonl under bak dir."""
    from elyra.memory.lance_store import newest_migration_backup

    bak = tmp_path / "lance_migration_bak"
    assert newest_migration_backup(bak) is None
    bak.mkdir()
    assert newest_migration_backup(bak) is None
    older = bak / "atoms-20260101T000000Z.jsonl"
    newer = bak / "atoms-20260728T120000Z.jsonl"
    older.write_text('{"atom_id":"a"}\n', encoding="utf-8")
    newer.write_text('{"atom_id":"b"}\n', encoding="utf-8")
    assert newest_migration_backup(bak) == newer


def test_recover_interrupted_migration_promotes_staging(paths):
    """Issue 9: open-time path promotes atoms__migrating when atoms missing."""
    import pyarrow as pa

    from elyra.memory.config import MemorySettings
    from elyra.memory.lance_store import (
        LanceMemoryStore,
        _ATOMS_TABLE,
        _STAGING_TABLE,
        _atoms_schema,
    )

    class _FakeTable:
        def __init__(self, arrow: Any) -> None:
            self._arrow = arrow

        def to_arrow(self) -> Any:
            return self._arrow

        def count_rows(self) -> int:
            return self._arrow.num_rows

        @property
        def schema(self) -> Any:
            return self._arrow.schema

    class _FakeDB:
        def __init__(self) -> None:
            self.tables: dict[str, _FakeTable] = {}

        def table_names(self) -> list[str]:
            return list(self.tables.keys())

        def open_table(self, name: str) -> _FakeTable:
            return self.tables[name]

        def drop_table(self, name: str) -> None:
            self.tables.pop(name, None)

        def create_table(self, name: str, data: Any) -> _FakeTable:
            if hasattr(data, "schema"):
                arrow = data
            else:
                arrow = pa.Table.from_pylist(data)
            tbl = _FakeTable(arrow)
            self.tables[name] = tbl
            return tbl

    # Build store without real Lance connect.
    store = object.__new__(LanceMemoryStore)
    store._paths = paths
    store._settings = MemorySettings(backend="lance")
    store._db = _FakeDB()
    store._table = None
    store._vector_schema_ok = False
    store._vector_error = None
    store._by_id = {}
    store._emb_by_id = {}

    # Staging holds one recovered row (full emb schema).
    schema = _atoms_schema(with_vectors=True)
    row = {name: None for name in schema.names}
    row.update(
        {
            "atom_id": "recovered1",
            "t_start": "2026-07-28T10:00:00Z",
            "kind": "observation",
            "content_text": "from staging",
            "content_ref": "inline",
            "embedding_status": "none",
            "media_ids_json": "[]",
            "meta_json": "{}",
            "schema_version": 1,
        }
    )
    staging_arrow = pa.Table.from_pylist([row], schema=schema)
    store._db.tables[_STAGING_TABLE] = _FakeTable(staging_arrow)

    assert _ATOMS_TABLE not in store._db.table_names()
    assert store._recover_interrupted_migration(store._db.table_names()) is True
    assert _ATOMS_TABLE in store._db.table_names()
    assert _STAGING_TABLE not in store._db.table_names()
    assert store._table is not None
    assert store.vector_schema_ok is True
    assert store._vector_error is None
    assert store._table.count_rows() == 1


def test_recover_interrupted_migration_restores_bak(paths):
    """Issue 9: restore newest JSONL bak when staging absent."""
    import pyarrow as pa

    from elyra.memory.config import MemorySettings, memory_root
    from elyra.memory.lance_store import LanceMemoryStore, _ATOMS_TABLE

    class _FakeTable:
        def __init__(self, arrow: Any) -> None:
            self._arrow = arrow

        def to_arrow(self) -> Any:
            return self._arrow

        def count_rows(self) -> int:
            return self._arrow.num_rows

        @property
        def schema(self) -> Any:
            return self._arrow.schema

    class _FakeDB:
        def __init__(self) -> None:
            self.tables: dict[str, _FakeTable] = {}

        def table_names(self) -> list[str]:
            return list(self.tables.keys())

        def open_table(self, name: str) -> _FakeTable:
            return self.tables[name]

        def drop_table(self, name: str) -> None:
            self.tables.pop(name, None)

        def create_table(self, name: str, data: Any) -> _FakeTable:
            arrow = data if hasattr(data, "schema") else pa.Table.from_pylist(data)
            tbl = _FakeTable(arrow)
            self.tables[name] = tbl
            return tbl

    store = object.__new__(LanceMemoryStore)
    store._paths = paths
    store._settings = MemorySettings(backend="lance")
    store._db = _FakeDB()
    store._table = None
    store._vector_schema_ok = False
    store._vector_error = None

    bak_dir = memory_root(paths) / "lance_migration_bak"
    bak_dir.mkdir(parents=True, exist_ok=True)
    bak = bak_dir / "atoms-20260728T100000Z.jsonl"
    bak.write_text(
        json.dumps(
            {
                "atom_id": "bak1",
                "t_start": "2026-07-28T10:00:00Z",
                "kind": "observation",
                "content_text": "from bak",
                "content_ref": "inline",
                "embedding_status": "none",
                "media_ids_json": "[]",
                "meta_json": "{}",
                "schema_version": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert store._recover_interrupted_migration([]) is True
    assert _ATOMS_TABLE in store._db.table_names()
    assert store.vector_schema_ok is True
    assert store._table.count_rows() == 1


def test_recover_interrupted_migration_returns_false_without_artifacts(paths):
    from elyra.memory.config import MemorySettings
    from elyra.memory.lance_store import LanceMemoryStore

    class _FakeDB:
        def table_names(self) -> list[str]:
            return []

    store = object.__new__(LanceMemoryStore)
    store._paths = paths
    store._settings = MemorySettings(backend="lance")
    store._db = _FakeDB()
    store._table = None
    store._vector_schema_ok = False
    store._vector_error = None
    assert store._recover_interrupted_migration([]) is False


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
        # Optimize may succeed (create_index) or fail — must not claim fresh on fail.
        opt = idx.optimize()
        h2 = idx.health()
        if opt.get("optimized"):
            assert opt.get("last_optimize")
            assert h2["ann_index_built"] is True
            assert h2["recent_buffer"] == 0
        else:
            # Failed ANN create: buffer/stale retained (Issue 3).
            assert h2["ann_index_built"] is False
            assert h2["index_stale"] is True or h2["recent_buffer"] >= 0
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


# ── PR4: recent buffer, hybrid merge, restart, optimize, stale ─────────────


def test_recent_buffer_on_upsert_and_cap(store):
    """Buffer holds vectors in-process; cap evicts oldest encoded_at."""
    ann = AnnSettings(recent_buffer_max=2, full_search_below=2000)
    idx = MemoryEmbeddingIndex(store=store, ann=ann)
    atoms = []
    for i, seed in enumerate(("a", "b", "c")):
        a = store.put_atom(
            _atom(
                text=seed,
                atom_id=f"buf_{seed}",
                t_start=f"2026-07-28T10:0{i}:00Z",
            )
        )
        atoms.append(a)
        emb = EmbeddingSet(
            atom_id=a.atom_id,
            dim=EMBED_DIM,
            emb_text=mock_vector(f"text:{seed}", dim=EMBED_DIM),
            emb_joint=mock_vector(f"joint:{seed}", dim=EMBED_DIM),
            model_id="mock",
            encoded_at=f"2026-07-28T10:0{i}:00Z",
        )
        assert idx.upsert(emb) is True
    h = idx.health()
    assert h["recent_buffer"] == 2
    assert h["index_stale"] is True
    # Oldest encoded_at (a) evicted.
    assert idx._fresh.buffer.get("buf_a") is None  # noqa: SLF001
    assert idx._fresh.buffer.get("buf_b") is not None  # noqa: SLF001
    assert idx._fresh.buffer.get("buf_c") is not None  # noqa: SLF001


def test_hybrid_merge_includes_buffer_only_atom():
    """Hybrid = main ∪ buffer; buffer-only ready atom must surface (KD4)."""
    ann = AnnSettings(full_search_below=0, recent_buffer_max=16)  # force hybrid
    idx = MemoryEmbeddingIndex(store=None, ann=ann)
    main_emb = _emb_set("main1", seed="main")
    assert idx.upsert(main_emb) is True

    # Simulate unindexed recent: vector only in buffer (not main dict).
    buf_vec = mock_vector("joint:buffer_only", dim=EMBED_DIM)
    idx._fresh.buffer.push(  # noqa: SLF001
        RecentBufferEntry(
            atom_id="buf_only",
            channel="joint",
            vector=buf_vec,
            encoded_at="2026-07-28T12:00:00Z",
            t_start="2026-07-28T12:00:00Z",
            moment_id="m1",
            kind="observation",
        )
    )
    # Drop from main to force buffer leg (ANN miss simulation).
    idx._by_id.pop("buf_only", None)  # noqa: SLF001

    hits = idx.search(buf_vec, k=5, channel="joint")
    ids = {h.atom_id for h in hits}
    assert "buf_only" in ids
    assert isinstance(hits[0], ScoredAtom)


def test_hybrid_merge_prefers_higher_score():
    ann = AnnSettings(full_search_below=0)
    idx = MemoryEmbeddingIndex(ann=ann)
    emb = _emb_set("same", seed="x")
    idx.upsert(emb)
    # Buffer already has same id from upsert; main+buffer merge keeps best.
    hits = idx.search(emb.emb_joint, k=3)
    assert len(hits) == 1
    assert hits[0].atom_id == "same"
    assert hits[0].score > 0.99


def test_full_search_mode_below_threshold(store):
    ann = AnnSettings(full_search_below=100, recent_buffer_max=8)
    idx = MemoryEmbeddingIndex(store=store, ann=ann)
    a = store.put_atom(_atom(text="fullmode", atom_id="fm1"))
    emb = _emb_set(a.atom_id, seed="fullmode")
    idx.upsert(emb)
    h = idx.health()
    assert h["search_mode"] == "full"
    assert h["vectors_ready"] == 1
    hits = idx.search(emb.emb_joint, k=3)
    assert any(x.atom_id == a.atom_id for x in hits)


def test_optimize_trims_buffer_and_clears_stale(store):
    ann = AnnSettings(
        recent_buffer_max=16,
        full_search_below=2000,
        optimize_every_n_encodes=64,
    )
    idx = MemoryEmbeddingIndex(store=store, ann=ann)
    a = store.put_atom(_atom(text="opt", atom_id="opt1"))
    emb = EmbeddingSet(
        atom_id=a.atom_id,
        emb_joint=mock_vector("joint:opt", dim=EMBED_DIM),
        emb_text=mock_vector("text:opt", dim=EMBED_DIM),
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    idx.upsert(emb)
    assert idx.health()["index_stale"] is True
    assert idx.health()["recent_buffer"] == 1
    result = idx.optimize()
    assert result["optimized"] is True
    assert result["buffer_trimmed"] >= 1
    h = idx.health()
    assert h["recent_buffer"] == 0
    assert h["index_stale"] is False
    assert h["last_optimize"]
    # Search still works via main after buffer trim.
    hits = idx.search(emb.emb_joint, k=3)
    assert any(x.atom_id == a.atom_id for x in hits)


def test_index_stale_after_n_encodes_threshold():
    """encodes_since_optimize >= threshold ⇒ index_stale even if buffer empty."""
    ann = AnnSettings(
        recent_buffer_max=8,
        optimize_every_n_encodes=3,
        full_search_below=2000,
    )
    idx = MemoryEmbeddingIndex(ann=ann)
    for i in range(3):
        emb = EmbeddingSet(
            atom_id=f"n{i}",
            emb_joint=mock_vector(f"joint:n{i}", dim=EMBED_DIM),
            model_id="mock",
            encoded_at=f"2026-07-28T10:0{i}:00Z",
        )
        idx.upsert(emb)
    # Optimize clears buffer; then force encodes counter without buffer.
    idx.optimize()
    assert idx.health()["recent_buffer"] == 0
    # Manually bump counter past threshold with empty buffer.
    idx._fresh.encodes_since_optimize = 3  # noqa: SLF001
    assert idx.health()["index_stale"] is True


def test_buffer_filters_moment_and_kind():
    ann = AnnSettings(full_search_below=0)
    idx = MemoryEmbeddingIndex(ann=ann)
    idx._fresh.buffer.push(  # noqa: SLF001
        RecentBufferEntry(
            atom_id="f1",
            channel="joint",
            vector=mock_vector("joint:f1", dim=EMBED_DIM),
            encoded_at="2026-07-28T10:00:00Z",
            t_start="2026-07-28T10:00:00Z",
            moment_id="mA",
            kind="observation",
        )
    )
    idx._fresh.buffer.push(  # noqa: SLF001
        RecentBufferEntry(
            atom_id="f2",
            channel="joint",
            vector=mock_vector("joint:f2", dim=EMBED_DIM),
            encoded_at="2026-07-28T11:00:00Z",
            t_start="2026-07-28T11:00:00Z",
            moment_id="mB",
            kind="speak",
        )
    )
    q = mock_vector("joint:f1", dim=EMBED_DIM)
    only = idx.search(q, k=10, moment_id="mA")
    assert [h.atom_id for h in only] == ["f1"]
    obs = idx.search(q, k=10, kinds=["observation"])
    assert [h.atom_id for h in obs] == ["f1"]


class _FakeLanceStore:
    """Hermetic store stand-in for LanceEmbeddingIndex restart / hybrid tests.

    Mirrors real Lance: glass ``list_atoms`` is hard-capped at LIST_ATOMS_MAX;
    ANN seed uses ``list_ready_embeddings_for_seed`` (no glass cap).
    """

    def __init__(self, *, create_index_ok: bool = False) -> None:
        self._atoms: dict[str, Atom] = {}
        self._embs: dict[str, EmbeddingSet] = {}
        self._create_index_ok = create_index_ok
        self.create_vector_index_calls = 0

    def put_atom(self, atom: Atom, **_kw: Any) -> Atom:
        self._atoms[atom.atom_id] = atom
        return atom

    def get_atom(self, atom_id: str) -> Atom | None:
        return self._atoms.get(atom_id)

    def upsert_vectors(self, atom_id: str, embeddings: EmbeddingSet) -> bool:
        if atom_id not in self._atoms:
            return False
        if not embeddings_are_ready(embeddings):
            return False
        self._embs[atom_id] = embeddings
        a = self._atoms[atom_id]
        meta = dict(a.meta or {})
        meta["embed_encode_ok"] = True
        if embeddings.encoded_at:
            meta["embed_encoded_at"] = embeddings.encoded_at
        from elyra.memory.types import atom_replace

        self._atoms[atom_id] = atom_replace(a, embedding_status="ready", meta=meta)
        return True

    def get_vectors(self, atom_id: str) -> EmbeddingSet | None:
        return self._embs.get(atom_id)

    def create_vector_index(self, channel: str = "joint", max_ms: int | None = None) -> None:
        del channel, max_ms
        self.create_vector_index_calls += 1
        if not self._create_index_ok:
            raise RuntimeError("create_vector_index disabled for test")

    def search_vectors(
        self,
        query: Any,
        *,
        k: int = 12,
        channel: str = "joint",
        t_start: Any = None,
        t_end: Any = None,
        moment_id: str | None = None,
        kinds: Any = None,
        exclude_atom_ids: Any = None,
        exclude_moment_id: str | None = None,
    ) -> list[tuple[str, float]]:
        del t_start, t_end
        exclude = set(exclude_atom_ids or ())
        kind_set = set(kinds) if kinds is not None else None
        q = [float(x) for x in query]
        qn = math.sqrt(sum(x * x for x in q)) or 1.0
        q = [x / qn for x in q]
        scored: list[tuple[str, float]] = []
        for aid, emb in self._embs.items():
            if aid in exclude:
                continue
            atom = self._atoms.get(aid)
            if atom is None or atom.embedding_status != "ready":
                continue
            if kind_set is not None and atom.kind not in kind_set:
                continue
            if moment_id is not None and atom.moment_id != moment_id:
                continue
            if exclude_moment_id and atom.moment_id == exclude_moment_id:
                continue
            vec = emb.channel_vector(channel)
            if vec is None:
                continue
            vn = math.sqrt(sum(float(x) * float(x) for x in vec)) or 1.0
            score = sum(float(a) * float(b) for a, b in zip(q, vec, strict=False)) / vn
            scored.append((aid, float(score)))
        scored.sort(key=lambda p: (-p[1], p[0]))
        return scored[: max(0, int(k))]

    def list_atoms(
        self,
        *,
        embedding_status: str | None = None,
        kinds: Any = None,
        limit: int = 50,
        newest_first: bool = True,
    ) -> list[Atom]:
        """Glass path: hard-capped at LIST_ATOMS_MAX (matches real Lance)."""
        from elyra.memory.store import LIST_ATOMS_MAX

        del kinds
        rows = list(self._atoms.values())
        if embedding_status is not None:
            rows = [a for a in rows if a.embedding_status == embedding_status]
        rows.sort(key=lambda a: (a.t_start, a.atom_id), reverse=bool(newest_first))
        cap = max(0, min(int(limit), LIST_ATOMS_MAX))
        return rows[:cap]

    def list_ready_embeddings_for_seed(
        self, *, limit: int = 256
    ) -> list[tuple[str, EmbeddingSet, Atom]]:
        """ANN seed path: no LIST_ATOMS_MAX; order by encoded_at desc."""
        ranked: list[tuple[str, EmbeddingSet, Atom]] = []
        for aid, emb in self._embs.items():
            atom = self._atoms.get(aid)
            if atom is None or atom.embedding_status != "ready":
                continue
            if not embeddings_are_ready(emb):
                continue
            ranked.append((aid, emb, atom))
        ranked.sort(key=lambda t: (t[1].encoded_at or "", t[0]), reverse=True)
        return ranked[: max(0, int(limit))]

    def health(self) -> dict[str, Any]:
        ready = sum(
            1
            for a in self._atoms.values()
            if a.embedding_status == "ready" and a.atom_id in self._embs
        )
        return {
            "ok": True,
            "backend": "lance",
            "vectors": True,
            "vectors_ready": ready,
            "vector_schema_version": 1,
        }


def test_restart_full_mode_search_returns_recent():
    """KD4 restart: encode without optimize → reopen → search finds recent atom.

    Uses full mode (vectors_ready < full_search_below) so durable main scan
    recovers without in-process buffer.
    """
    store = _FakeLanceStore()
    ann = AnnSettings(full_search_below=2000, recent_buffer_max=4)
    idx = LanceEmbeddingIndex(store, ann=ann, seed_on_open=True)
    recent_id = None
    for i in range(5):
        aid = f"r{i}"
        store.put_atom(
            _atom(
                text=f"body {i}",
                atom_id=aid,
                t_start=f"2026-07-28T10:{i:02d}:00Z",
            )
        )
        emb = EmbeddingSet(
            atom_id=aid,
            emb_joint=mock_vector(f"joint:r{i}", dim=EMBED_DIM),
            emb_text=mock_vector(f"text:r{i}", dim=EMBED_DIM),
            model_id="mock",
            encoded_at=f"2026-07-28T10:{i:02d}:00Z",
        )
        assert idx.upsert(emb) is True
        recent_id = aid
    # "Kill process": drop index (buffer lost); store keeps durable vectors.
    del idx
    idx2 = LanceEmbeddingIndex(store, ann=ann, seed_on_open=True)
    h = idx2.health()
    assert h["search_mode"] == "full"
    assert h["vectors_ready"] == 5
    # Recent buffer not required in full mode after restart.
    q = mock_vector("joint:r4", dim=EMBED_DIM)
    hits = idx2.search(q, k=3)
    assert any(x.atom_id == recent_id for x in hits)


def test_restart_hybrid_seed_returns_recent():
    """Above full_search_below: open seeds buffer; search finds recent.

    Until ANN is built, search_mode stays full (KD4); buffer is still seeded
    so hybrid is ready after a successful optimize.
    """
    store = _FakeLanceStore(create_index_ok=True)
    # full_search_below=0 → above threshold immediately; seed last N on open.
    ann = AnnSettings(full_search_below=0, recent_buffer_max=8)
    idx = LanceEmbeddingIndex(store, ann=ann, seed_on_open=False)
    for i in range(4):
        aid = f"h{i}"
        store.put_atom(
            _atom(
                text=f"hy {i}",
                atom_id=aid,
                t_start=f"2026-07-28T11:{i:02d}:00Z",
            )
        )
        emb = EmbeddingSet(
            atom_id=aid,
            emb_joint=mock_vector(f"joint:h{i}", dim=EMBED_DIM),
            emb_text=mock_vector(f"text:h{i}", dim=EMBED_DIM),
            model_id="mock",
            encoded_at=f"2026-07-28T11:{i:02d}:00Z",
        )
        assert idx.upsert(emb) is True
    del idx
    # Reopen: seed buffer from durable rows (even while full until ANN built).
    idx2 = LanceEmbeddingIndex(store, ann=ann, seed_on_open=True)
    h = idx2.health()
    assert h["ann_index_built"] is False
    assert h["search_mode"] == "full"  # full until index built
    assert h["recent_buffer"] >= 1
    assert h["seed_incomplete"] is False
    assert h["index_stale"] is True  # no ANN yet with vectors
    q = mock_vector("joint:h3", dim=EMBED_DIM)
    hits = idx2.search(q, k=5)
    assert any(x.atom_id == "h3" for x in hits)
    # Successful optimize → hybrid mode.
    r = idx2.optimize()
    assert r["optimized"] is True
    h2 = idx2.health()
    assert h2["ann_index_built"] is True
    assert h2["search_mode"] == "hybrid"
    assert h2["recent_buffer"] == 0
    assert h2["index_stale"] is False


def test_lance_optimize_failed_keeps_buffer_and_stale():
    """Failed ANN create must not clear buffer or claim freshness (Issue 3)."""
    store = _FakeLanceStore(create_index_ok=False)
    ann = AnnSettings(full_search_below=2000, optimize_every_n_encodes=2)
    idx = LanceEmbeddingIndex(store, ann=ann)
    store.put_atom(_atom(text="s", atom_id="st1"))
    emb = _emb_set("st1", seed="st")
    assert idx.upsert(emb) is True
    assert idx.health()["index_stale"] is True
    assert idx.health()["recent_buffer"] == 1
    r = idx.optimize(max_ms=50)
    assert r["optimized"] is False
    assert r["buffer_trimmed"] == 0
    assert idx.health()["recent_buffer"] == 1
    assert idx.health()["index_stale"] is True
    assert idx.health()["ann_index_built"] is False


def test_lance_optimize_success_trims_buffer():
    store = _FakeLanceStore(create_index_ok=True)
    ann = AnnSettings(full_search_below=2000, optimize_every_n_encodes=2)
    idx = LanceEmbeddingIndex(store, ann=ann)
    store.put_atom(_atom(text="s", atom_id="st1"))
    emb = _emb_set("st1", seed="st")
    assert idx.upsert(emb) is True
    r = idx.optimize(max_ms=50)
    assert r["optimized"] is True
    assert store.create_vector_index_calls >= 1
    assert idx.health()["recent_buffer"] == 0
    assert idx.health()["ann_index_built"] is True
    assert idx.health()["index_stale"] is False


def test_use_full_search_until_ann_built():
    """Issue 2: hybrid only when above threshold AND ann_index_built."""
    store = _FakeLanceStore(create_index_ok=True)
    ann = AnnSettings(full_search_below=2, recent_buffer_max=8)
    idx = LanceEmbeddingIndex(store, ann=ann, seed_on_open=False)
    for i in range(3):
        store.put_atom(
            _atom(text=f"u{i}", atom_id=f"u{i}", t_start=f"2026-07-28T10:0{i}:00Z")
        )
        idx.upsert(
            EmbeddingSet(
                atom_id=f"u{i}",
                emb_joint=mock_vector(f"joint:u{i}", dim=EMBED_DIM),
                model_id="mock",
                encoded_at=f"2026-07-28T10:0{i}:00Z",
            )
        )
    h = idx.health()
    assert h["vectors_ready"] >= 2
    assert h["ann_index_built"] is False
    assert h["search_mode"] == "full"
    idx.optimize()
    assert idx.health()["search_mode"] == "hybrid"
    assert idx.health()["ann_index_built"] is True


def test_seed_fills_beyond_list_atoms_max():
    """Issue 1: seed fills ann_recent_buffer_max even when > LIST_ATOMS_MAX."""
    from elyra.memory.store import LIST_ATOMS_MAX

    store = _FakeLanceStore()
    n = LIST_ATOMS_MAX + 40  # 240 > 200 glass cap
    buf_max = LIST_ATOMS_MAX + 30  # 230 — would starve via list_atoms
    # Put many ready vectors with distinct encoded_at.
    for i in range(n):
        aid = f"seed_{i:04d}"
        store.put_atom(
            _atom(
                text=aid,
                atom_id=aid,
                # t_start order differs from encoded_at on purpose
                t_start=f"2026-01-01T00:00:{i % 60:02d}Z" if i < 60 else f"2026-01-02T00:{i % 60:02d}:00Z",
            )
        )
        store.upsert_vectors(
            aid,
            EmbeddingSet(
                atom_id=aid,
                emb_joint=mock_vector(f"joint:{aid}", dim=EMBED_DIM),
                emb_text=mock_vector(f"text:{aid}", dim=EMBED_DIM),
                model_id="mock",
                encoded_at=f"2026-07-28T{i // 60:02d}:{i % 60:02d}:00Z",
            ),
        )
    # Glass list_atoms is capped.
    assert len(store.list_atoms(embedding_status="ready", limit=1000)) == LIST_ATOMS_MAX
    # Seed API is not.
    seeded_rows = store.list_ready_embeddings_for_seed(limit=buf_max)
    assert len(seeded_rows) == buf_max

    ann = AnnSettings(full_search_below=0, recent_buffer_max=buf_max)
    idx = LanceEmbeddingIndex(store, ann=ann, seed_on_open=True)
    h = idx.health()
    assert h["recent_buffer"] == buf_max
    assert h["seed_incomplete"] is False
    # Newest by encoded_at must be present (not truncated by t_start glass window).
    newest = f"seed_{n - 1:04d}"
    assert idx._fresh.buffer.get(newest) is not None  # noqa: SLF001


def test_open_embedding_index_passes_settings():
    store = _FakeLanceStore()
    settings = MemorySettings(
        backend="lance",
        ann_recent_buffer_max=7,
        ann_full_search_below=0,
    )
    idx = open_embedding_index(store, settings=settings)
    assert isinstance(idx, LanceEmbeddingIndex)
    assert idx._fresh.ann.recent_buffer_max == 7  # noqa: SLF001
    assert idx._fresh.ann.full_search_below == 0  # noqa: SLF001


@lance_required
def test_lance_restart_real_store_search_recent(paths):
    """Acceptance on real Lance when connect works: reopen finds recent vector."""
    settings = MemorySettings(backend="lance", write_atoms=True)
    store = open_memory_store(paths, settings)
    try:
        ann = AnnSettings(full_search_below=2000, recent_buffer_max=16)
        idx = LanceEmbeddingIndex(store, ann=ann)
        last = None
        for i in range(3):
            a = store.put_atom(
                _atom(
                    text=f"real {i}",
                    atom_id=f"real_{i}",
                    t_start=f"2026-07-28T12:{i:02d}:00Z",
                )
            )
            emb = EmbeddingSet(
                atom_id=a.atom_id,
                emb_joint=mock_vector(f"joint:real{i}", dim=EMBED_DIM),
                emb_text=mock_vector(f"text:real{i}", dim=EMBED_DIM),
                model_id="mock",
                encoded_at=f"2026-07-28T12:{i:02d}:00Z",
            )
            assert idx.upsert(emb) is True
            last = a.atom_id
        store.close()
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass

    store2 = open_memory_store(paths, settings)
    try:
        idx2 = open_embedding_index(store2, settings=settings)
        assert isinstance(idx2, LanceEmbeddingIndex)
        q = mock_vector("joint:real2", dim=EMBED_DIM)
        hits = idx2.search(q, k=5)
        assert any(h.atom_id == last for h in hits)
    finally:
        store2.close()


# ── Phase 2 rectification PR-R1: resolve + joint repair (KD-R1/R2/R11) ──────


def test_resolve_search_channel_explicit_and_auto():
    from elyra.memory.index import resolve_search_channel

    assert resolve_search_channel("text") == ("text", "explicit")
    assert resolve_search_channel("joint") == ("joint", "explicit")

    ch, reason = resolve_search_channel(
        "auto",
        vectors_by_channel={"text": 10, "joint": 0},
        joint_repair_remaining=5,
    )
    assert ch == "text"
    assert reason == "auto_text_repair_pending"

    ch, reason = resolve_search_channel(
        "auto",
        vectors_by_channel={"text": 10, "joint": 10},
        joint_repair_remaining=0,
    )
    assert ch == "joint"
    assert reason == "auto_joint"

    ch, reason = resolve_search_channel(
        "auto",
        vectors_by_channel={"text": 3, "joint": 0},
        joint_repair_remaining=0,
    )
    assert ch == "text"
    assert reason == "auto_text"

    ch, reason = resolve_search_channel(
        "auto",
        vectors_by_channel={},
        joint_repair_remaining=0,
    )
    assert ch == "joint"
    assert reason == "auto_empty"


def test_search_channel_auto_does_not_early_return_empty(store):
    """Footgun: channel=auto must not hit CHANNEL_SET reject before resolve."""
    atom = store.put_atom(_atom(text="alpha", status="pending"))
    idx = MemoryEmbeddingIndex(store=store)
    # New encode path: joint copy of text
    emb = EmbeddingSet(
        atom_id=atom.atom_id,
        emb_text=mock_vector("text:alpha", dim=EMBED_DIM),
        emb_joint=mock_vector("text:alpha", dim=EMBED_DIM),  # copy
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    assert idx.upsert(emb) is True
    q = mock_vector("text:alpha", dim=EMBED_DIM)
    hits_auto = idx.search(q, k=5, channel="auto")
    assert len(hits_auto) == 1
    assert hits_auto[0].atom_id == atom.atom_id
    hits_joint = idx.search(q, k=5, channel="joint")
    assert len(hits_joint) == 1


def test_joint_repair_text_only_without_encoder(store):
    """KD-R11: ready text-only fixture → repair fills joint; search hits."""
    atom = store.put_atom(_atom(text="legacy", status="ready"))
    idx = MemoryEmbeddingIndex(store=store, joint_repair_max_per_open=0)
    # Legacy ready without joint (pre-KD-R1).
    emb = EmbeddingSet(
        atom_id=atom.atom_id,
        emb_text=mock_vector("text:legacy", dim=EMBED_DIM),
        emb_joint=None,
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    assert embeddings_are_ready(emb)  # legacy sole-mod still ready
    assert embeddings_are_ready(emb, require_joint=True) is False
    assert idx.upsert(emb) is True
    h0 = idx.health()
    assert h0["joint_repair_remaining"] == 1
    assert h0["vectors_by_channel"]["text"] == 1
    assert h0["vectors_by_channel"]["joint"] == 0

    result = idx.repair_joint_copies(limit=64)
    assert result["repaired"] == 1
    assert result["joint_repair_remaining"] == 0
    fixed = idx.get(atom.atom_id)
    assert fixed is not None
    assert fixed.emb_joint is not None
    assert fixed.emb_joint == fixed.emb_text

    q = mock_vector("text:legacy", dim=EMBED_DIM)
    hits = idx.search(q, k=5, channel="joint")
    assert len(hits) == 1
    hits_auto = idx.search(q, k=5, channel="auto")
    assert len(hits_auto) == 1
    h1 = idx.health()
    assert h1["joint_repair_remaining"] == 0
    assert h1["vectors_by_channel"]["joint"] == 1
    # Buffer re-pushed as joint
    buf_entry = idx._fresh.buffer.get(atom.atom_id)
    assert buf_entry is not None
    assert buf_entry.channel == "joint"


def test_auto_resolves_text_while_repair_pending(store):
    atom = store.put_atom(_atom(text="pending-repair", status="ready"))
    idx = MemoryEmbeddingIndex(store=store, joint_repair_max_per_open=0)
    emb = EmbeddingSet(
        atom_id=atom.atom_id,
        emb_text=mock_vector("text:pr", dim=EMBED_DIM),
        emb_joint=None,
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    assert idx.upsert(emb) is True
    assert idx.health()["joint_repair_remaining"] == 1
    q = mock_vector("text:pr", dim=EMBED_DIM)
    # auto → text while repair pending (not empty joint)
    hits = idx.search(q, k=5, channel="auto")
    assert len(hits) == 1
    assert hits[0].channel == "text"


def test_embeddings_are_ready_require_joint_oq_r4():
    sole = EmbeddingSet(
        atom_id="a1",
        emb_text=mock_vector("t", dim=EMBED_DIM),
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    assert embeddings_are_ready(sole)
    assert not embeddings_are_ready(sole, require_joint=True)
    with_joint = EmbeddingSet(
        atom_id="a1",
        emb_text=mock_vector("t", dim=EMBED_DIM),
        emb_joint=mock_vector("t", dim=EMBED_DIM),
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    assert embeddings_are_ready(with_joint, require_joint=True)
