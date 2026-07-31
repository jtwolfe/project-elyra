"""Temporal helpers: window iteration, range wrappers, scale parent/child."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.store import open_memory_store
from elyra.memory.temporal import (
    child_scale,
    group_atoms_by_window,
    iter_windows,
    list_range,
    parent_scale,
    parent_scale_write,
    walk_backward,
    walk_forward,
    windows_in_horizon,
)
from elyra.memory.types import Atom, new_atom_id, window_bounds


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    s = open_memory_store(paths, MemorySettings(write_atoms=True, backend="jsonl"))
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


def test_window_bounds_grids_via_types():
    """Window bounds — 15m / 1h / 6h / 1d / 1w / 1m / 1y UTC."""
    t = datetime(2026, 7, 28, 14, 22, 5, tzinfo=UTC)
    assert window_bounds("15m", t) == (
        datetime(2026, 7, 28, 14, 15, tzinfo=UTC),
        datetime(2026, 7, 28, 14, 30, tzinfo=UTC),
    )
    assert window_bounds("1h", t)[0].hour == 14
    assert window_bounds("6h", t)[0].hour == 12
    assert window_bounds("1d", t)[0] == datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    # Tuesday → week starts Monday 27th
    assert window_bounds("1w", t)[0] == datetime(2026, 7, 27, 0, 0, tzinfo=UTC)
    assert window_bounds("1m", t)[0] == datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    assert window_bounds("1y", t) == (
        datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        datetime(2027, 1, 1, 0, 0, tzinfo=UTC),
    )


def test_child_and_parent_scale_write_map():
    """Write map: 1h→None child, 1h→1d parent (never 6h)."""
    assert child_scale("1h") is None
    assert child_scale("1d") == "1h"
    assert child_scale("1w") == "1d"
    assert child_scale("1m") == "1w"
    assert child_scale("1y") == "1m"

    assert parent_scale("1h") == "1d"
    assert parent_scale("1d") == "1w"
    assert parent_scale("1w") == "1m"
    assert parent_scale("1m") == "1y"
    assert parent_scale("1y") is None

    assert parent_scale_write("1h") == "1d"
    assert parent_scale_write("1y") is None
    with pytest.raises(ValueError):
        parent_scale_write("15m")
    with pytest.raises(ValueError):
        parent_scale_write("6h")


def test_child_and_parent_scale_legacy():
    """Legacy 15m/6h maps for read/repair only."""
    assert child_scale("15m") is None
    assert child_scale("6h") == "1h"
    assert parent_scale("15m") == "1h"
    assert parent_scale("6h") == "1d"
    with pytest.raises(ValueError):
        child_scale("2h")


def test_iter_windows_15m():
    t0 = datetime(2026, 7, 28, 12, 5, tzinfo=UTC)
    t1 = datetime(2026, 7, 28, 12, 50, tzinfo=UTC)
    wins = list(iter_windows("15m", t0, t1))
    assert wins[0] == (
        datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        datetime(2026, 7, 28, 12, 15, tzinfo=UTC),
    )
    assert wins[-1][0] == datetime(2026, 7, 28, 12, 45, tzinfo=UTC)
    # 12:00, 12:15, 12:30, 12:45
    assert len(wins) == 4


def test_iter_windows_empty_span():
    t = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    assert list(iter_windows("1h", t, t)) == []


def test_iter_windows_1d_and_1m():
    t0 = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    t1 = datetime(2026, 7, 30, 10, 0, tzinfo=UTC)
    days = list(iter_windows("1d", t0, t1))
    # 28th, 29th, and 30th (starts midnight, still < t1=30th 10:00)
    assert len(days) == 3
    assert [d[0].day for d in days] == [28, 29, 30]

    months = list(
        iter_windows(
            "1m",
            datetime(2026, 6, 15, tzinfo=UTC),
            datetime(2026, 8, 1, tzinfo=UTC),
        )
    )
    assert [m[0].month for m in months] == [6, 7]


def test_iter_windows_1y():
    wins = list(
        iter_windows(
            "1y",
            datetime(2025, 6, 1, tzinfo=UTC),
            datetime(2027, 3, 1, tzinfo=UTC),
        )
    )
    assert [w[0].year for w in wins] == [2025, 2026, 2027]


def test_windows_in_horizon_count():
    now = datetime(2026, 7, 28, 12, 20, tzinfo=UTC)
    wins = windows_in_horizon("15m", now, n_windows=3)
    assert len(wins) == 3
    assert wins[-1] == window_bounds("15m", now)
    assert wins[0][0] == datetime(2026, 7, 28, 11, 45, tzinfo=UTC)


def test_windows_in_horizon_timedelta():
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    wins = windows_in_horizon("1h", now, horizon=timedelta(hours=3))
    # [09:00, 12:00) → 09, 10, 11 (and current 12? end is cur_end=13:00)
    starts = [w[0].hour for w in wins]
    assert 9 in starts
    assert 12 in starts


def test_list_range_wrapper(store):
    store.put_atom(_atom(t="2026-07-28T10:00:00Z", text="a"))
    store.put_atom(_atom(t="2026-07-28T10:30:00Z", text="b"))
    store.put_atom(_atom(t="2026-07-28T11:00:00Z", text="c"))
    rows = list_range(
        store,
        "2026-07-28T10:00:00Z",
        "2026-07-28T11:00:00Z",
    )
    assert [r.content_text for r in rows] == ["a", "b"]


def test_walk_forward_backward(store):
    a = store.put_atom(
        _atom(t="2026-07-28T10:00:00Z", text="a", atom_id="a_aaa")
    )
    b = store.put_atom(
        _atom(
            t="2026-07-28T10:01:00Z",
            text="b",
            atom_id="a_bbb",
            prev_atom_id=a.atom_id,
        )
    )
    store.update_links(a.atom_id, next_atom_id=b.atom_id)
    fwd = walk_forward(store, a.atom_id, n=5)
    assert [x.atom_id for x in fwd] == ["a_aaa", "a_bbb"]
    back = walk_backward(store, b.atom_id, n=5)
    assert [x.atom_id for x in back] == ["a_bbb", "a_aaa"]


def test_group_atoms_by_window():
    atoms = [
        _atom(t="2026-07-28T12:05:00Z", text="a"),
        _atom(t="2026-07-28T12:20:00Z", text="b"),
        _atom(t="2026-07-28T12:25:00Z", text="c"),
    ]
    buckets = group_atoms_by_window("15m", atoms)
    assert len(buckets) == 2
    k1 = (
        datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        datetime(2026, 7, 28, 12, 15, tzinfo=UTC),
    )
    k2 = (
        datetime(2026, 7, 28, 12, 15, tzinfo=UTC),
        datetime(2026, 7, 28, 12, 30, tzinfo=UTC),
    )
    assert len(buckets[k1]) == 1
    assert len(buckets[k2]) == 2
