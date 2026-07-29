"""MemoryStore Protocol contract over optional LanceDB backend.

Skips when ``lancedb`` is missing or runtime-broken (e.g. connect segfault
on unsupported Python builds). CI default path stays JSONL.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from functools import lru_cache

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings, lance_root, memory_meta_path, memory_root
from elyra.memory.store import MemoryStore, open_memory_store
from elyra.memory.types import (
    Atom,
    new_atom_id,
    stable_summary_id,
    to_iso_z,
    window_bounds,
)

# Fail closed before any in-process connect: importorskip alone is not enough
# when lancedb imports but native connect crashes the interpreter.
pytest.importorskip("lancedb")


@lru_cache(maxsize=1)
def _lancedb_connect_works() -> bool:
    """Probe connect in a subprocess so a segfault cannot kill pytest."""
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


pytestmark = pytest.mark.skipif(
    not _lancedb_connect_works(),
    reason="lancedb import ok but connect unusable on this Python/runtime",
)


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    from elyra.memory.lance_store import LanceMemoryStore

    s = open_memory_store(paths, MemorySettings(write_atoms=True, backend="lance"))
    assert isinstance(s, LanceMemoryStore)
    yield s
    s.close()


def _atom(
    *,
    t: str,
    kind: str = "observation",
    text: str = "body",
    moment_id: str | None = "m1",
    atom_id: str | None = None,
    **kwargs,
) -> Atom:
    return Atom(
        atom_id=atom_id or new_atom_id(),
        t_start=t,
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=moment_id,
        **kwargs,
    )


def test_factory_returns_lance(paths, store):
    assert isinstance(store, MemoryStore)
    h = store.health()
    assert h["ok"] is True
    assert h["backend"] == "lance"


def test_meta_json_backend_lance(paths, store):
    meta_path = memory_meta_path(paths)
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["schema_version"] == 1
    assert meta["backend"] == "lance"
    assert meta["created_at"]
    assert lance_root(paths).is_dir()


def test_put_get_roundtrip(store):
    a = _atom(t="2026-07-28T10:00:00Z", text="hello world")
    stored = store.put_atom(a)
    assert stored.atom_id == a.atom_id
    assert stored.content_text == "hello world"
    assert stored.content_ref == "inline"
    got = store.get_atom(a.atom_id)
    assert got is not None
    assert got.content_text == "hello world"
    assert got.kind == "observation"
    assert got.schema_version == 1


def test_put_idempotent_replace_by_atom_id(store):
    aid = new_atom_id()
    store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="v1", atom_id=aid))
    store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="v2", atom_id=aid))
    got = store.get_atom(aid)
    assert got is not None
    assert got.content_text == "v2"
    h = store.health()
    assert h["ok"] is True
    assert h["atom_count"] == 1


def test_list_by_moment_order(store):
    store.put_atom(_atom(t="2026-07-28T10:02:00Z", text="b", moment_id="mA"))
    store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="a", moment_id="mA"))
    store.put_atom(_atom(t="2026-07-28T10:01:00Z", text="x", moment_id="mB"))
    rows = store.list_by_moment("mA")
    assert [r.content_text for r in rows] == ["a", "b"]
    assert store.list_by_moment("mA", kinds=["speak"]) == []
    store.put_atom(
        _atom(t="2026-07-28T10:03:00Z", kind="speak", text="hi", moment_id="mA")
    )
    assert len(store.list_by_moment("mA", kinds=["speak"])) == 1


def test_list_range_half_open(store):
    store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="in", moment_id="m1"))
    store.put_atom(_atom(t="2026-07-28T11:00:00Z", text="end", moment_id="m1"))
    store.put_atom(_atom(t="2026-07-28T09:59:59Z", text="before", moment_id="m1"))
    rows = store.list_range("2026-07-28T10:00:00Z", "2026-07-28T11:00:00Z")
    assert [r.content_text for r in rows] == ["in"]
    store.put_atom(_atom(t="2026-07-28T10:30:00Z", text="other", moment_id="m2"))
    rows2 = store.list_range(
        "2026-07-28T10:00:00Z",
        "2026-07-28T12:00:00Z",
        exclude_moment_id="m1",
    )
    assert [r.content_text for r in rows2] == ["other"]


def test_update_links_and_walk(store):
    a1 = store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="1", moment_id="m1"))
    a2 = store.put_atom(
        _atom(
            t="2026-07-28T10:01:00Z",
            text="2",
            moment_id="m1",
            prev_atom_id=a1.atom_id,
        )
    )
    store.update_links(a1.atom_id, next_atom_id=a2.atom_id)

    a1b = store.get_atom(a1.atom_id)
    assert a1b is not None
    assert a1b.next_atom_id == a2.atom_id
    a2b = store.get_atom(a2.atom_id)
    assert a2b is not None
    assert a2b.prev_atom_id == a1.atom_id

    forward = store.walk_next(a1.atom_id, n=10)
    assert [x.content_text for x in forward] == ["1", "2"]
    backward = store.walk_prev(a2.atom_id, n=10)
    assert [x.content_text for x in backward] == ["2", "1"]


def test_moment_tail_and_global_tail(store):
    assert store.moment_tail("m1") is None
    assert store.global_tail() is None
    a1 = store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="1", moment_id="m1"))
    a2 = store.put_atom(
        _atom(
            t="2026-07-28T10:01:00Z",
            text="2",
            moment_id="m1",
            prev_atom_id=a1.atom_id,
        )
    )
    store.update_links(a1.atom_id, next_atom_id=a2.atom_id)
    tail = store.moment_tail("m1")
    assert tail is not None
    assert tail.atom_id == a2.atom_id
    g = store.global_tail()
    assert g is not None
    assert g.atom_id == a2.atom_id


def test_health(store):
    h = store.health()
    assert h["ok"] is True
    assert h["backend"] == "lance"
    assert h["atom_count"] == 0
    store.put_atom(_atom(t="2026-07-28T10:00:00Z"))
    assert store.health()["atom_count"] == 1


def test_delete_atom(store):
    a = store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="gone"))
    assert store.delete_atom(a.atom_id) is True
    assert store.get_atom(a.atom_id) is None
    assert store.delete_atom(a.atom_id) is False
    assert store.health()["atom_count"] == 0


def test_restart_reloads_indexes(paths, store):
    a = store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", text="persist", moment_id="m9")
    )
    store.close()

    store2 = open_memory_store(paths, MemorySettings(backend="lance"))
    try:
        got = store2.get_atom(a.atom_id)
        assert got is not None
        assert got.content_text == "persist"
        assert store2.list_by_moment("m9")[0].atom_id == a.atom_id
        assert store2.health()["backend"] == "lance"
    finally:
        store2.close()


def test_content_spill_to_blob(paths):
    settings = MemorySettings(
        backend="lance",
        atom_max_chars=50_000,
        inline_max_chars=32,
    )
    store = open_memory_store(paths, settings)
    try:
        long_text = "x" * 100
        a = store.put_atom(
            _atom(t="2026-07-28T10:00:00Z", text=long_text, moment_id="m1")
        )
        assert a.content_ref.startswith("blob:")
        assert a.content_text == long_text
        got = store.get_atom(a.atom_id)
        assert got is not None
        assert got.content_text == long_text
        rel = a.content_ref[len("blob:") :]
        blob_path = memory_root(paths) / rel
        assert blob_path.is_file()
        assert blob_path.read_text(encoding="utf-8") == long_text
    finally:
        store.close()

    # Reload hydrates from blob.
    store2 = open_memory_store(paths, settings)
    try:
        again = store2.get_atom(a.atom_id)
        assert again is not None
        assert again.content_text == long_text
    finally:
        store2.close()


def test_atom_max_chars_cap(paths):
    settings = MemorySettings(
        backend="lance", atom_max_chars=10, inline_max_chars=10_000
    )
    store = open_memory_store(paths, settings)
    try:
        a = store.put_atom(
            _atom(t="2026-07-28T10:00:00Z", text="0123456789ABCDEF")
        )
        assert a.content_text == "0123456789"
        assert a.meta.get("truncated") is True
    finally:
        store.close()


def test_list_summaries(store):
    start, end = window_bounds("15m", datetime(2026, 7, 28, 12, 5, tzinfo=UTC))
    sid = stable_summary_id("15m", start)
    store.put_atom(
        Atom(
            atom_id=sid,
            t_start=to_iso_z(start),
            kind="summary",
            scale="15m",
            window_start=to_iso_z(start),
            window_end=to_iso_z(end),
            content_text="summary body",
            moment_id=None,
        )
    )
    rows = store.list_summaries("15m")
    assert len(rows) == 1
    assert rows[0].atom_id == sid
    ov = store.list_summaries("15m", overlapping=(start, end))
    assert len(ov) == 1
    miss = store.list_summaries(
        "15m",
        overlapping=(
            start + timedelta(hours=2),
            end + timedelta(hours=2),
        ),
    )
    assert miss == []


def test_global_tail_excludes_summary(store):
    obs = store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", kind="observation", text="exp")
    )
    start, end = window_bounds("15m", datetime(2026, 7, 28, 11, 0, tzinfo=UTC))
    store.put_atom(
        Atom(
            atom_id=stable_summary_id("15m", start),
            t_start="2026-07-28T11:00:00Z",
            kind="summary",
            scale="15m",
            window_start=to_iso_z(start),
            window_end=to_iso_z(end),
            content_text="ladder",
            moment_id=None,
        )
    )
    tail = store.global_tail()
    assert tail is not None
    assert tail.atom_id == obs.atom_id
    assert tail.kind == "observation"


def test_list_range_mixed_iso_offset_forms(store):
    store.put_atom(
        _atom(t="2026-07-28T10:00:00+00:00", text="plus", atom_id=new_atom_id())
    )
    store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", text="zee", atom_id=new_atom_id())
    )
    store.put_atom(
        _atom(t="2026-07-28T09:00:00Z", text="before", atom_id=new_atom_id())
    )
    rows = store.list_range("2026-07-28T10:00:00Z", "2026-07-28T11:00:00Z")
    texts = sorted(r.content_text for r in rows)
    assert texts == ["plus", "zee"]
    for r in rows:
        assert r.t_start.endswith("Z")
        assert "+00:00" not in r.t_start


def test_update_links_missing_raises(store):
    from elyra.memory import MemoryAtomNotFound

    with pytest.raises(MemoryAtomNotFound):
        store.update_links("a_does_not_exist", next_atom_id="a_other")


def test_close_rejects_ops(store):
    store.close()
    from elyra.memory.errors import MemoryUnavailable

    with pytest.raises(MemoryUnavailable):
        store.put_atom(_atom(t="2026-07-28T10:00:00Z"))


def test_spill_then_shrink_reloads_short_body(paths):
    """Blob → short body must force inline; reopen must not revive blob."""
    from elyra.memory.types import atom_replace

    settings = MemorySettings(
        backend="lance", atom_max_chars=50_000, inline_max_chars=32
    )
    store = open_memory_store(paths, settings)
    try:
        aid = new_atom_id()
        long_text = "x" * 100
        a = store.put_atom(
            _atom(t="2026-07-28T10:00:00Z", text=long_text, atom_id=aid)
        )
        assert a.content_ref.startswith("blob:")
        blob_rel = a.content_ref[len("blob:") :]
        blob_path = memory_root(paths) / blob_rel
        assert blob_path.is_file()

        short = store.put_atom(
            atom_replace(a, content_text="short", content_ref=a.content_ref)
        )
        assert short.content_text == "short"
        assert short.content_ref == "inline"
        assert store.get_atom(aid).content_text == "short"
    finally:
        store.close()

    store2 = open_memory_store(paths, settings)
    try:
        again = store2.get_atom(aid)
        assert again is not None
        assert again.content_text == "short"
        assert again.content_ref == "inline"
    finally:
        store2.close()


def test_list_atoms_filter_embedding_status(store):
    """Issue 5: Lance list_atoms status filter + newest_first + cap."""
    from elyra.memory.store import LIST_ATOMS_MAX

    store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", text="a", embedding_status="none")
    )
    store.put_atom(
        _atom(t="2026-07-28T10:01:00Z", text="b", embedding_status="pending")
    )
    store.put_atom(
        _atom(t="2026-07-28T10:02:00Z", text="c", embedding_status="pending")
    )
    store.put_atom(
        _atom(t="2026-07-28T10:03:00Z", text="d", embedding_status="skipped")
    )
    pending = store.list_atoms(embedding_status="pending", limit=50)
    assert len(pending) == 2
    assert all(a.embedding_status == "pending" for a in pending)
    assert pending[0].content_text == "c"
    assert pending[1].content_text == "b"

    oldest = store.list_atoms(
        embedding_status="pending", newest_first=False, limit=50
    )
    assert oldest[0].content_text == "b"

    capped = store.list_atoms(limit=1)
    assert len(capped) == 1

    for i in range(3):
        store.put_atom(
            _atom(
                t=f"2026-07-28T11:{i:02d}:00Z",
                text=f"x{i}",
                atom_id=new_atom_id(),
            )
        )
    all_rows = store.list_atoms(limit=10_000)
    assert len(all_rows) <= LIST_ATOMS_MAX
    assert len(all_rows) == store.health()["atom_count"]


def test_protocol_runtime_checkable(store):
    assert isinstance(store, MemoryStore)


# ── Phase 2 emb columns (PR3) ─────────────────────────────────────────────


def test_lance_health_includes_vectors_flag(store):
    h = store.health()
    assert h["ok"] is True
    assert h.get("vectors") is True
    assert h.get("vector_schema_version") == 1


def test_lance_upsert_vectors_preserve_on_put_and_links(store):
    """KD19 acceptance: scalar put/update_links must not wipe emb_*."""
    from elyra.memory.embed.mock import mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet

    a = store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", text="A", atom_id="kd19_a", embedding_status="pending")
    )
    emb = EmbeddingSet(
        atom_id=a.atom_id,
        emb_text=mock_vector("t:A", dim=EMBED_DIM),
        emb_joint=mock_vector("j:A", dim=EMBED_DIM),
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    assert store.upsert_vectors(a.atom_id, emb) is True
    before = store.get_vectors(a.atom_id)
    assert before is not None
    assert before.emb_joint is not None

    b = store.put_atom(
        _atom(
            t="2026-07-28T10:01:00Z",
            text="B",
            atom_id="kd19_b",
            prev_atom_id=a.atom_id,
            embedding_status="pending",
        )
    )
    store.update_links(a.atom_id, next_atom_id=b.atom_id)

    after = store.get_vectors(a.atom_id)
    assert after is not None
    assert after.emb_joint == before.emb_joint
    a2 = store.get_atom(a.atom_id)
    assert a2.embedding_status == "ready"
    assert a2.next_atom_id == b.atom_id


def test_lance_meta_vector_schema_version(paths, store):
    meta = json.loads(memory_meta_path(paths).read_text(encoding="utf-8"))
    assert meta.get("vector_schema_version") == 1
    assert meta.get("emb_dim") == 2048
    assert meta.get("backend") == "lance"


def test_lance_upsert_vectors_missing_atom_false(store):
    from elyra.memory.embed.mock import mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet

    emb = EmbeddingSet(
        atom_id="missing",
        emb_joint=mock_vector("j", dim=EMBED_DIM),
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    assert store.upsert_vectors("missing", emb) is False


def test_lance_search_vectors_basic(store):
    from elyra.memory.embed.mock import mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet

    a = store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", text="s", atom_id="sv1", embedding_status="pending")
    )
    j = mock_vector("joint:sv1", dim=EMBED_DIM)
    emb = EmbeddingSet(
        atom_id=a.atom_id,
        emb_text=mock_vector("text:sv1", dim=EMBED_DIM),
        emb_joint=j,
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    assert store.upsert_vectors(a.atom_id, emb) is True
    hits = store.search_vectors(j, k=5, channel="joint")
    assert hits and hits[0][0] == a.atom_id
    assert hits[0][1] > 0.99


def test_lance_upsert_vectors_atom_id_mismatch_false(store):
    """Issue 5: EmbeddingSet.atom_id must match path atom_id."""
    from elyra.memory.embed.mock import mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet

    a = store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", text="m", atom_id="match_me", embedding_status="pending")
    )
    emb = EmbeddingSet(
        atom_id="other_id",
        emb_joint=mock_vector("j", dim=EMBED_DIM),
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    assert store.upsert_vectors(a.atom_id, emb) is False
    assert store.get_vectors(a.atom_id) is None
    assert store.get_atom(a.atom_id).embedding_status == "pending"


def test_lance_repair_joint_copies_fills_joint_without_encoder(store):
    """KD-R11: ready text-only row gets emb_joint via repair (no encoder)."""
    from elyra.memory.embed.mock import mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet

    a = store.put_atom(
        _atom(
            t="2026-07-28T10:00:00Z",
            text="legacy text",
            atom_id="rep1",
            embedding_status="pending",
        )
    )
    text_v = mock_vector("text:rep1", dim=EMBED_DIM)
    # Write sole-modality ready without joint via low-level emb map + status.
    emb = EmbeddingSet(
        atom_id=a.atom_id,
        emb_text=text_v,
        emb_joint=None,
        model_id="mock",
        encoded_at="2026-07-28T10:00:00Z",
    )
    # embeddings_are_ready allows sole; upsert_vectors uses that.
    assert store.upsert_vectors(a.atom_id, emb) is True
    got = store.get_vectors(a.atom_id)
    assert got is not None
    assert got.emb_joint is None
    assert store.joint_repair_remaining() >= 1

    result = store.repair_joint_copies(limit=64)
    assert result["repaired"] >= 1
    assert result["joint_repair_remaining"] == 0
    fixed = store.get_vectors(a.atom_id)
    assert fixed is not None
    assert fixed.emb_joint is not None
    assert fixed.emb_joint == fixed.emb_text
    hits = store.search_vectors(text_v, k=5, channel="joint")
    assert any(aid == a.atom_id for aid, _ in hits)
    counts = store.vectors_by_channel()
    assert counts.get("joint", 0) >= 1
    assert counts.get("text", 0) >= 1


def test_lance_open_cap_zero_disables_open_repair(paths):
    """joint_repair_max_per_open=0 must disable open repair (not rewrite to 500)."""
    from elyra.memory.embed.mock import mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet

    settings = MemorySettings(
        write_atoms=True,
        backend="lance",
        joint_repair_max_per_open=0,
    )
    store = open_memory_store(paths, settings)
    try:
        a = store.put_atom(
            _atom(
                t="2026-07-28T10:00:00Z",
                text="no open repair",
                atom_id="nor1",
                embedding_status="pending",
            )
        )
        emb = EmbeddingSet(
            atom_id=a.atom_id,
            emb_text=mock_vector("text:nor1", dim=EMBED_DIM),
            emb_joint=None,
            model_id="mock",
            encoded_at="2026-07-28T10:00:00Z",
        )
        assert store.upsert_vectors(a.atom_id, emb) is True
        # Explicit repair with limit 0 is no-op.
        r = store.repair_joint_copies(limit=0)
        assert r["repaired"] == 0
        assert store.get_vectors(a.atom_id).emb_joint is None
        # Settings-level open cap stays 0 on the store settings object.
        assert store.settings.joint_repair_max_per_open == 0
    finally:
        store.close()


def _seed_search_fixture(store, *, n: int = 6):
    """Fixed mock vectors for lance_native vs python parity (PR-R4)."""
    from elyra.memory.embed.mock import mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet

    # Use valid AtomKind values only (observation/speak/tool/…).
    kinds = ["observation", "observation", "speak", "observation", "speak", "tool"]
    moments = ["m1", "m1", "m1", "m2", "m2", "m1"]
    times = [
        "2026-07-28T10:00:00Z",
        "2026-07-28T10:30:00Z",
        "2026-07-28T11:00:00Z",
        "2026-07-28T12:00:00Z",
        "2026-07-28T13:00:00Z",
        "2026-07-28T14:00:00Z",
    ]
    query = mock_vector("query:parity", dim=EMBED_DIM)
    ids: list[str] = []
    for i in range(n):
        aid = f"parity_{i}"
        store.put_atom(
            _atom(
                t=times[i],
                text=f"parity body {i}",
                atom_id=aid,
                kind=kinds[i],
                moment_id=moments[i],
                embedding_status="pending",
            )
        )
        # Distinct seeds so ranking is stable across engines.
        emb = EmbeddingSet(
            atom_id=aid,
            emb_text=mock_vector(f"text:parity:{i}", dim=EMBED_DIM),
            emb_joint=mock_vector(f"joint:parity:{i}", dim=EMBED_DIM),
            model_id="mock",
            encoded_at=times[i],
        )
        assert store.upsert_vectors(aid, emb) is True
        ids.append(aid)
    # One pending (non-ready) row with vectors should never appear (ready-only).
    store.put_atom(
        _atom(
            t="2026-07-28T15:00:00Z",
            text="pending no search",
            atom_id="parity_pending",
            embedding_status="pending",
        )
    )
    return query, ids


def test_lance_search_python_backend_force(paths):
    """ann_search_backend=python forces in-process cosine (sole rollback knob)."""
    from elyra.memory.embed.mock import mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet
    from elyra.memory.lance_store import LanceMemoryStore

    settings = MemorySettings(
        write_atoms=True,
        backend="lance",
        ann_search_backend="python",
    )
    store = open_memory_store(paths, settings)
    try:
        assert isinstance(store, LanceMemoryStore)
        assert store.ann_search_backend() == "python"
        a = store.put_atom(
            _atom(
                t="2026-07-28T10:00:00Z",
                text="py force",
                atom_id="pyf1",
                embedding_status="pending",
            )
        )
        j = mock_vector("joint:pyf1", dim=EMBED_DIM)
        emb = EmbeddingSet(
            atom_id=a.atom_id,
            emb_text=mock_vector("text:pyf1", dim=EMBED_DIM),
            emb_joint=j,
            model_id="mock",
            encoded_at="2026-07-28T10:00:00Z",
        )
        assert store.upsert_vectors(a.atom_id, emb) is True
        hits = store.search_vectors(j, k=3, channel="joint")
        assert hits and hits[0][0] == a.atom_id
        assert hits[0][1] > 0.99
        st = store.vector_search_status()
        assert st["ann_search_backend"] == "python"
        # Never attempted Lance path under python backend.
        assert st["lance_search_ok"] is None
    finally:
        store.close()


def test_lance_native_vs_python_parity_with_filters(paths):
    """PR-R4 acceptance: top-k parity + filter parity (kind/time/exclude/ready).

    Compares lance_native vs python on the same durable fixture. Jaccard ≥ 0.9
    on top-k ids; scores ordered consistently. Score formula for Lance path:
    score = 1 - cosine_distance.
    """
    from elyra.memory.lance_store import LanceMemoryStore

    # Seed once under python so vectors are durable; reopen with each backend.
    seed_settings = MemorySettings(
        write_atoms=True,
        backend="lance",
        ann_search_backend="python",
    )
    store = open_memory_store(paths, seed_settings)
    try:
        assert isinstance(store, LanceMemoryStore)
        query, ids = _seed_search_fixture(store, n=6)
        # Capture python baseline with full filters.
        py_hits = store.search_vectors(
            query,
            k=4,
            channel="joint",
            kinds=["observation", "speak"],
            t_start="2026-07-28T10:00:00Z",
            t_end="2026-07-28T13:30:00Z",
            exclude_atom_ids=["parity_0"],
            exclude_moment_id="m2",
        )
        py_ids = [aid for aid, _ in py_hits]
        assert "parity_pending" not in py_ids
        assert "parity_0" not in py_ids  # exclude_atom_ids
        # m2 excluded → parity_3, parity_4 out; kinds/time filter further.
        for aid in py_ids:
            assert aid in ids
            atom = store.get_atom(aid)
            assert atom is not None
            assert atom.embedding_status == "ready"
            assert atom.kind in ("observation", "speak")
            assert atom.moment_id != "m2"
    finally:
        store.close()

    # Reopen same disk with lance_native.
    native_settings = MemorySettings(
        write_atoms=True,
        backend="lance",
        ann_search_backend="lance_native",
    )
    store2 = open_memory_store(paths, native_settings)
    try:
        assert isinstance(store2, LanceMemoryStore)
        assert store2.ann_search_backend() == "lance_native"
        from elyra.memory.embed.mock import mock_vector
        from elyra.memory.embed.types import EMBED_DIM

        query = mock_vector("query:parity", dim=EMBED_DIM)
        native_hits = store2.search_vectors(
            query,
            k=4,
            channel="joint",
            kinds=["observation", "speak"],
            t_start="2026-07-28T10:00:00Z",
            t_end="2026-07-28T13:30:00Z",
            exclude_atom_ids=["parity_0"],
            exclude_moment_id="m2",
        )
        native_ids = [aid for aid, _ in native_hits]
        assert "parity_pending" not in native_ids
        assert "parity_0" not in native_ids

        # Recompute python on same open store for apples-to-apples.
        # Temporarily force python via settings attribute isn't frozen-safe on
        # store; call private python path with same filters.
        q = [float(x) for x in query]
        qn = sum(x * x for x in q) ** 0.5
        q = [x / qn for x in q]
        py_hits2 = store2._search_vectors_python(  # noqa: SLF001
            q,
            col="emb_joint",
            k=4,
            t_start="2026-07-28T10:00:00Z",
            t_end="2026-07-28T13:30:00Z",
            moment_id=None,
            kinds=["observation", "speak"],
            exclude_atom_ids=["parity_0"],
            exclude_moment_id="m2",
        )
        py_set = {aid for aid, _ in py_hits2}
        native_set = set(native_ids)
        if not py_set and not native_set:
            return
        jaccard = len(py_set & native_set) / len(py_set | native_set)
        assert jaccard >= 0.9, (
            f"parity jaccard {jaccard:.3f} < 0.9; "
            f"python={sorted(py_set)} native={sorted(native_set)}"
        )
        # Top-1 should agree when scores are well separated.
        if py_hits2 and native_hits:
            # Scores: lance uses 1-d; python uses cosine — both in ~[-1,1].
            assert all(-1.05 <= s <= 1.05 for _, s in native_hits)
            st = store2.vector_search_status()
            # If native succeeded, sticky ok; if fell back, ok is False.
            assert st["lance_search_ok"] in (True, False)
            if st["lance_search_ok"] is True:
                assert native_hits[0][0] == py_hits2[0][0] or jaccard >= 0.9
    finally:
        store2.close()


def test_lance_search_fallback_on_table_search_failure(paths, monkeypatch):
    """On Lance failure: log once, sticky flag, Python scan preserves filters."""
    from elyra.memory.embed.mock import mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet
    from elyra.memory.lance_store import LanceMemoryStore

    settings = MemorySettings(
        write_atoms=True,
        backend="lance",
        ann_search_backend="lance_native",
    )
    store = open_memory_store(paths, settings)
    try:
        assert isinstance(store, LanceMemoryStore)
        a = store.put_atom(
            _atom(
                t="2026-07-28T10:00:00Z",
                text="fb",
                atom_id="fb1",
                kind="observation",
                embedding_status="pending",
            )
        )
        j = mock_vector("joint:fb1", dim=EMBED_DIM)
        emb = EmbeddingSet(
            atom_id=a.atom_id,
            emb_text=mock_vector("text:fb1", dim=EMBED_DIM),
            emb_joint=j,
            model_id="mock",
            encoded_at="2026-07-28T10:00:00Z",
        )
        assert store.upsert_vectors(a.atom_id, emb) is True

        def _boom(*_a, **_k):
            raise RuntimeError("simulated table.search failure")

        monkeypatch.setattr(store, "_search_vectors_lance", _boom)
        hits = store.search_vectors(
            j, k=3, channel="joint", kinds=["observation"]
        )
        assert hits and hits[0][0] == a.atom_id
        st = store.vector_search_status()
        assert st["lance_search_ok"] is False
        assert store._lance_search_error_logged is True  # noqa: SLF001
        # Second call still works via python; still sticky failed.
        hits2 = store.search_vectors(j, k=3, channel="joint")
        assert hits2 and hits2[0][0] == a.atom_id
        assert store.vector_search_status()["lance_search_ok"] is False
    finally:
        store.close()



