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


def test_protocol_runtime_checkable(store):
    assert isinstance(store, MemoryStore)
