"""MemoryStore Protocol contract over hermetic JSONL backend."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings, memory_meta_path, memory_root
from elyra.memory.jsonl_store import JsonlMemoryStore
from elyra.memory.store import MemoryStore, open_memory_store
from elyra.memory.types import (
    Atom,
    new_atom_id,
    stable_summary_id,
    to_iso_z,
    window_bounds,
)


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths) -> JsonlMemoryStore:
    s = open_memory_store(paths, MemorySettings(write_atoms=True, backend="jsonl"))
    assert isinstance(s, JsonlMemoryStore)
    return s


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


def test_ensure_data_dirs_creates_memory(paths):
    assert (paths.data_dir / "memory").is_dir()


def test_open_creates_meta_json(paths, store):
    meta_path = memory_meta_path(paths)
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["schema_version"] == 1
    assert meta["backend"] == "jsonl"
    assert meta["created_at"]


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
    # Two append lines for same id (dirty until compact).
    assert h["line_count"] >= 2


def test_list_by_moment_order(store):
    store.put_atom(_atom(t="2026-07-28T10:02:00Z", text="b", moment_id="mA"))
    store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="a", moment_id="mA"))
    store.put_atom(_atom(t="2026-07-28T10:01:00Z", text="x", moment_id="mB"))
    rows = store.list_by_moment("mA")
    assert [r.content_text for r in rows] == ["a", "b"]
    speaks = store.list_by_moment(
        "mA",
        kinds=["speak"],
    )
    assert speaks == []
    store.put_atom(
        _atom(t="2026-07-28T10:03:00Z", kind="speak", text="hi", moment_id="mA")
    )
    assert len(store.list_by_moment("mA", kinds=["speak"])) == 1


def test_list_range_half_open(store):
    store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="in", moment_id="m1"))
    store.put_atom(_atom(t="2026-07-28T11:00:00Z", text="end", moment_id="m1"))
    store.put_atom(_atom(t="2026-07-28T09:59:59Z", text="before", moment_id="m1"))
    rows = store.list_range(
        "2026-07-28T10:00:00Z",
        "2026-07-28T11:00:00Z",
    )
    assert [r.content_text for r in rows] == ["in"]
    # exclude moment
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
    assert h["backend"] == "jsonl"
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
    a = store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="persist", moment_id="m9"))
    store.update_links(a.atom_id, next_atom_id=None)  # no-op shape
    store.close()

    store2 = open_memory_store(paths, MemorySettings())
    got = store2.get_atom(a.atom_id)
    assert got is not None
    assert got.content_text == "persist"
    assert store2.list_by_moment("m9")[0].atom_id == a.atom_id
    store2.close()


def test_content_spill_to_blob(paths):
    # Cap high so body survives; inline low to force blob.
    settings = MemorySettings(
        atom_max_chars=50_000,
        inline_max_chars=32,
    )
    store = open_memory_store(paths, settings)
    long_text = "x" * 100
    a = store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", text=long_text, moment_id="m1")
    )
    assert a.content_ref.startswith("blob:")
    # Live atom always has full render body (KD18).
    assert a.content_text == long_text
    got = store.get_atom(a.atom_id)
    assert got is not None
    assert got.content_text == long_text
    assert got.content_ref.startswith("blob:")

    # Disk line keeps locator; body empty on the line.
    lines = (memory_root(paths) / "atoms.jsonl").read_text(encoding="utf-8")
    row = json.loads(lines.strip().splitlines()[-1])
    assert row["content_ref"].startswith("blob:")
    assert row["content_text"] == ""

    rel = a.content_ref[len("blob:") :]
    blob_path = memory_root(paths) / rel
    assert blob_path.is_file()
    assert blob_path.read_text(encoding="utf-8") == long_text

    store.close()
    # Reload hydrates from blob.
    store2 = open_memory_store(paths, settings)
    again = store2.get_atom(a.atom_id)
    assert again is not None
    assert again.content_text == long_text
    store2.close()


def test_atom_max_chars_cap(paths):
    settings = MemorySettings(atom_max_chars=10, inline_max_chars=10_000)
    store = open_memory_store(paths, settings)
    a = store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", text="0123456789ABCDEF")
    )
    assert a.content_text == "0123456789"
    assert a.meta.get("truncated") is True
    store.close()


def test_compact_latest_wins(store):
    aid = new_atom_id()
    store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="v1", atom_id=aid))
    store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="v2", atom_id=aid))
    store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="v3", atom_id=aid))
    assert store.health()["line_count"] == 3
    store.compact()
    assert store.health()["line_count"] == 1
    assert store.get_atom(aid).content_text == "v3"
    # File has one line
    text = store.atoms_path.read_text(encoding="utf-8").strip()
    assert len(text.splitlines()) == 1


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
    # overlapping window
    ov = store.list_summaries(
        "15m",
        overlapping=(start, end),
    )
    assert len(ov) == 1
    miss = store.list_summaries(
        "15m",
        overlapping=(
            start + timedelta(hours=2),
            end + timedelta(hours=2),
        ),
    )
    assert miss == []


def test_protocol_runtime_checkable(store):
    assert isinstance(store, MemoryStore)


def test_rlock_concurrent_puts(paths):
    store = open_memory_store(paths, MemorySettings())
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            for j in range(20):
                store.put_atom(
                    _atom(
                        t=f"2026-07-28T10:00:{j:02d}Z",
                        text=f"t{i}-{j}",
                        moment_id=f"m{i}",
                    )
                )
        except BaseException as exc:  # noqa: BLE001 — collect for assert
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert store.health()["atom_count"] == 80
    store.close()


def test_close_rejects_ops(store):
    store.close()
    from elyra.memory.errors import MemoryUnavailable

    with pytest.raises(MemoryUnavailable):
        store.put_atom(_atom(t="2026-07-28T10:00:00Z"))


def test_spill_then_shrink_reloads_short_body(paths):
    """Issue 1: replace-to-short must force inline; reopen must not revive blob."""
    from elyra.memory.types import atom_replace

    settings = MemorySettings(atom_max_chars=50_000, inline_max_chars=32)
    store = open_memory_store(paths, settings)
    aid = new_atom_id()
    long_text = "x" * 100
    a = store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", text=long_text, atom_id=aid)
    )
    assert a.content_ref.startswith("blob:")
    blob_rel = a.content_ref[len("blob:") :]
    blob_path = memory_root(paths) / blob_rel
    assert blob_path.is_file()

    # Shrink body but pass through the prior blob content_ref (stale).
    short = store.put_atom(
        atom_replace(a, content_text="short", content_ref=a.content_ref)
    )
    assert short.content_text == "short"
    assert short.content_ref == "inline"
    assert store.get_atom(aid).content_text == "short"

    store.close()
    store2 = open_memory_store(paths, settings)
    again = store2.get_atom(aid)
    assert again is not None
    assert again.content_text == "short"
    assert again.content_ref == "inline"
    store2.close()


def test_global_tail_excludes_summary(store):
    """Issue 2: ladder summaries must not become sequential chain tips."""
    obs = store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", kind="observation", text="exp")
    )
    start, end = window_bounds(
        "15m", datetime(2026, 7, 28, 11, 0, tzinfo=UTC)
    )
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
    assert tail.kind != "summary"


def test_list_range_mixed_iso_offset_forms(store):
    """Issue 3: +00:00 and Z for the same instant must both match the range."""
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
    # put normalizes stored t_start to Z
    for r in rows:
        assert r.t_start.endswith("Z")
        assert "+00:00" not in r.t_start


def test_update_links_missing_raises(store):
    from elyra.memory import MemoryAtomNotFound

    with pytest.raises(MemoryAtomNotFound):
        store.update_links("a_does_not_exist", next_atom_id="a_other")


def test_maybe_compact_dirty_threshold(paths):
    settings = MemorySettings(jsonl_compact_dirty=3)
    store = open_memory_store(paths, settings)
    aid = new_atom_id()
    # 1 atom, 4 lines → dirty = 3 >= threshold
    for i in range(4):
        store.put_atom(
            _atom(t="2026-07-28T10:00:00Z", text=f"v{i}", atom_id=aid)
        )
    assert store.health()["atom_count"] == 1
    assert store.health()["line_count"] == 4
    assert store.needs_compact() is True
    assert store.maybe_compact() is True
    assert store.health()["line_count"] == 1
    assert store.needs_compact() is False
    assert store.maybe_compact() is False
    assert store.get_atom(aid).content_text == "v3"
    store.close()


def test_default_inline_max_allows_spill(paths):
    """Default atom_max (8000) > inline_max (4000) so mid-size bodies spill."""
    assert MemorySettings().inline_max_chars == 4000
    assert MemorySettings().atom_max_chars == 8000
    store = open_memory_store(paths, MemorySettings())
    body = "y" * 5000  # between 4000 and 8000
    a = store.put_atom(_atom(t="2026-07-28T10:00:00Z", text=body))
    assert a.content_ref.startswith("blob:")
    assert a.content_text == body
    store.close()
