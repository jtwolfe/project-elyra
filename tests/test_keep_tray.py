"""Sticky directed-keep tray: load/save, merge, TTL, LRU (S3 / B5+B5b)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.graph import GraphView
from elyra.memory.keep_tray import (
    DEFAULT_ENTRY_CAP,
    DEFAULT_HARD_TTL_HOURS,
    DEFAULT_SOFT_TTL_HOURS,
    DirectedKeepTray,
    KeepTrayEntry,
    load_directed_keep_tray,
    merge_confirm,
    save_directed_keep_tray,
    seed_tray_from_keep_ids,
    tray_runtime_path,
)
from elyra.memory.store import open_memory_store
from elyra.memory.traverse import TraversalRegistry
from elyra.memory.types import Atom, new_atom_id, to_iso_z


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


def _now(hours_ago: float = 0.0) -> str:
    base = datetime(2026, 7, 28, 15, 0, 0, tzinfo=UTC)
    return to_iso_z(base - timedelta(hours=hours_ago))


def test_merge_confirm_unions_ids():
    tray = DirectedKeepTray(entry_cap=32)
    merge_confirm(tray, ["a1", "a2"], now=_now(0), walk_summary_nl="first")
    merge_confirm(tray, ["a2", "a3"], now=_now(0), walk_summary_nl="second")
    assert set(tray.atom_ids()) == {"a1", "a2", "a3"}
    assert tray.walk_summary_nl == "second"
    # a2 reinforced on second confirm
    by = tray.entry_map()
    assert by["a2"].confirmed_at == _now(0) or by["a2"].last_reinforced_at == _now(0)


def test_confirm_merge_default_unions_ids(paths):
    """KD-MRG: two finish confirms union under cap (registry SoT)."""
    store = open_memory_store(
        paths, MemorySettings(write_atoms=True, backend="jsonl")
    )
    try:
        a1 = store.put_atom(
            Atom(
                atom_id="a_m1",
                t_start=_now(1),
                kind="observation",
                content_text="one",
                content_ref="inline",
                moment_id="m1",
            )
        )
        a2 = store.put_atom(
            Atom(
                atom_id="a_m2",
                t_start=_now(1),
                kind="observation",
                content_text="two",
                content_ref="inline",
                moment_id="m1",
            )
        )
        settings = MemorySettings(
            directed_traversal_enabled=True,
            write_atoms=True,
            backend="jsonl",
            traverse_keep_adjacent=False,
        )
        reg = TraversalRegistry(settings=settings, paths=paths)
        gv = GraphView(store, settings=settings, now=_now(0))
        reg.start(gv, goal="g1", seed_atom_ids=[a1.atom_id], moment_id="m1")
        reg.finish(keep_ids=[a1.atom_id])
        reg.start(gv, goal="g2", seed_atom_ids=[a2.atom_id], moment_id="m1")
        reg.finish(keep_ids=[a2.atom_id])
        ids, summary = reg.get_meal_keep_ids()
        assert set(ids) == {a1.atom_id, a2.atom_id}
        assert summary  # latest walk summary retained
        assert reg.ensure_tray().entry_map()[a1.atom_id].atom_id == a1.atom_id
    finally:
        store.close()


def test_directed_keep_hard_ttl_evicts():
    tray = DirectedKeepTray(max_age_hard_hours=24.0)
    tray.entries = [
        KeepTrayEntry(
            atom_id="old",
            confirmed_at=_now(30),
            last_reinforced_at=_now(30),
        ),
        KeepTrayEntry(
            atom_id="young",
            confirmed_at=_now(1),
            last_reinforced_at=_now(1),
        ),
    ]
    dropped = tray.drop_hard_ttl(now=_now(0), hard_hours=24.0)
    assert dropped == 1
    assert tray.atom_ids() == ["young"]


def test_directed_keep_lru_over_cap():
    tray = DirectedKeepTray(entry_cap=2)
    tray.entries = [
        KeepTrayEntry(
            atom_id="oldest",
            confirmed_at=_now(5),
            last_reinforced_at=_now(5),
        ),
        KeepTrayEntry(
            atom_id="mid",
            confirmed_at=_now(3),
            last_reinforced_at=_now(3),
        ),
        KeepTrayEntry(
            atom_id="newest",
            confirmed_at=_now(1),
            last_reinforced_at=_now(1),
        ),
    ]
    dropped = tray.lru_trim(entry_cap=2)
    assert dropped == 1
    assert "oldest" not in tray.atom_ids()
    assert set(tray.atom_ids()) == {"mid", "newest"}


def test_directed_keep_tray_restart_reload(paths):
    """P7: persist → new registry ensure_tray reloads; hard TTL applied."""
    tray = seed_tray_from_keep_ids(
        ["a_live", "a_dead"],
        now=_now(1),
        walk_summary_nl="reload me",
        hard_hours=24.0,
        entry_cap=32,
    )
    # Age a_dead past hard TTL by rewriting timestamps.
    tray.entry_map()["a_dead"].last_reinforced_at = _now(30)
    tray.entry_map()["a_dead"].confirmed_at = _now(30)
    save_directed_keep_tray(tray, paths=paths)
    path = tray_runtime_path(paths.data_dir)
    assert path.is_file()

    reg = TraversalRegistry(
        settings=MemorySettings(directed_traversal_enabled=True),
        paths=paths,
        now_fn=lambda: _now(0),
    )
    loaded = reg.ensure_tray()
    assert "a_live" in loaded.atom_ids()
    assert "a_dead" not in loaded.atom_ids()
    ids, summary = reg.get_meal_keep_ids()
    assert ids == ["a_live"]
    assert summary == "reload me"

    # reset clears RAM only; file survives
    reg.reset()
    assert reg.directed_keep_tray is None
    ids2, _ = reg.get_meal_keep_ids()
    assert ids2 == ["a_live"]


def test_load_save_roundtrip(paths):
    tray = DirectedKeepTray()
    merge_confirm(
        tray,
        ["x1"],
        now=_now(0),
        session_id="tr_1",
        moment_id="m9",
        walk_summary_nl="hi",
    )
    save_directed_keep_tray(tray, paths=paths)
    loaded = load_directed_keep_tray(paths)
    assert loaded.atom_ids() == ["x1"]
    assert loaded.walk_summary_nl == "hi"
    assert loaded.entries[0].source_moment_id == "m9"


def test_meal_keep_ids_soft_aged_last():
    tray = DirectedKeepTray(soft_evict_after_hours=3.0)
    tray.entries = [
        KeepTrayEntry(
            atom_id="soft",
            confirmed_at=_now(5),
            last_reinforced_at=_now(5),
        ),
        KeepTrayEntry(
            atom_id="fresh",
            confirmed_at=_now(0.5),
            last_reinforced_at=_now(0.5),
        ),
    ]
    ids, _, soft = tray.meal_keep_ids(now=_now(0), soft_hours=3.0)
    assert ids[0] == "fresh"
    assert ids[-1] == "soft"
    assert "soft" in soft
    assert "fresh" not in soft


def test_defaults():
    assert DEFAULT_HARD_TTL_HOURS == 24.0
    assert DEFAULT_SOFT_TTL_HOURS == 3.0
    assert DEFAULT_ENTRY_CAP == 32
