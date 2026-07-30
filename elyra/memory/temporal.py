"""Temporal query helpers over MemoryStore.

Scope: range wrappers, sequential walks, period-window iteration.
In scope: pure helpers over the MemoryStore Protocol; no write policy.
Out of scope: summary generation (ladder), meal composition, promote.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterator, Sequence

from elyra.memory.store import MemoryStore
from elyra.memory.types import (
    PERIOD_SCALE_ORDER,
    PERIOD_SCALES,
    Atom,
    AtomKind,
    PeriodScale,
    parse_iso_z,
    window_bounds,
)

# Fine → coarse; child of scale i is scale i-1.
_CHILD_SCALE: dict[str, PeriodScale | None] = {
    "15m": None,
    "1h": "15m",
    "6h": "1h",
    "1d": "6h",
    "1w": "1d",
    "1m": "1w",
}


def child_scale(scale: PeriodScale | str) -> PeriodScale | None:
    """Return the finer child scale for ladder rollup, or None for 15m."""
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    return _CHILD_SCALE[scale]


def parent_scale(scale: PeriodScale | str) -> PeriodScale | None:
    """Return the coarser parent scale, or None for 1m."""
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    order = list(PERIOD_SCALE_ORDER)
    idx = order.index(scale)  # type: ignore[arg-type]
    if idx + 1 >= len(order):
        return None
    return order[idx + 1]


def list_range(
    store: MemoryStore,
    t_start: datetime | str,
    t_end: datetime | str,
    *,
    kinds: Sequence[AtomKind | str] | None = None,
    exclude_moment_id: str | None = None,
    limit: int = 200,
) -> list[Atom]:
    """Half-open ``[t_start, t_end)`` by ``t_start``; oldest first.

    Thin wrapper around ``MemoryStore.list_range`` for a stable import surface.
    """
    return store.list_range(
        t_start,
        t_end,
        kinds=kinds,  # type: ignore[arg-type]
        exclude_moment_id=exclude_moment_id,
        limit=limit,
    )


def list_by_moment(
    store: MemoryStore,
    moment_id: str,
    *,
    kinds: Sequence[AtomKind | str] | None = None,
    limit: int | None = None,
) -> list[Atom]:
    """Atoms in a moment, time-ordered. Wrapper around store."""
    return store.list_by_moment(
        moment_id,
        kinds=kinds,  # type: ignore[arg-type]
        limit=limit,
    )


def walk_forward(
    store: MemoryStore, atom_id: str, *, n: int = 20
) -> list[Atom]:
    """Follow ``next_atom_id`` up to ``n`` steps (including start)."""
    return store.walk_next(atom_id, n=n)


def walk_backward(
    store: MemoryStore, atom_id: str, *, n: int = 20
) -> list[Atom]:
    """Follow ``prev_atom_id`` up to ``n`` steps (including start)."""
    return store.walk_prev(atom_id, n=n)


def iter_windows(
    scale: PeriodScale | str,
    t_from: datetime | str,
    t_to: datetime | str,
) -> Iterator[tuple[datetime, datetime]]:
    """Yield half-open ``[start, end)`` UTC windows of ``scale`` covering time.

    Windows are those whose start falls in ``[floor(t_from), t_to)`` — i.e.
    every grid window that begins before ``t_to`` and ends after ``t_from``
    (intersecting the half-open query span). Order is chronological.
    """
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    start_bound = parse_iso_z(t_from)
    end_bound = parse_iso_z(t_to)
    if end_bound <= start_bound:
        return
    # First window that intersects [start_bound, end_bound).
    cursor, _ = window_bounds(scale, start_bound)
    # If window ends at or before start_bound, advance (shouldn't happen
    # because window_bounds floors to containing window).
    while cursor < end_bound:
        w_start, w_end = window_bounds(scale, cursor)
        if w_end > start_bound and w_start < end_bound:
            yield w_start, w_end
        if w_end <= cursor:
            # Safety: avoid infinite loop on bad grids.
            break
        cursor = w_end


def windows_in_horizon(
    scale: PeriodScale | str,
    now: datetime | str,
    *,
    horizon: timedelta | None = None,
    n_windows: int | None = None,
) -> list[tuple[datetime, datetime]]:
    """Return recent windows of ``scale`` ending at or before ``now``'s window.

    Prefer ``n_windows`` (count of grid cells looking back including current).
    If only ``horizon`` is set, cover ``[now - horizon, now]``.
    Default: 4 windows for fine scales, 3 for day+, when both omitted.
    """
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    now_dt = parse_iso_z(now)
    cur_start, cur_end = window_bounds(scale, now_dt)

    if n_windows is None and horizon is None:
        n_windows = 4 if scale in ("15m", "1h", "6h") else 3

    if horizon is not None:
        t_from = now_dt - horizon
        return list(iter_windows(scale, t_from, cur_end))

    assert n_windows is not None
    n = max(1, int(n_windows))
    # Walk backward n-1 steps from current window start.
    out: list[tuple[datetime, datetime]] = []
    start = cur_start
    for _ in range(n):
        w_start, w_end = window_bounds(scale, start)
        out.append((w_start, w_end))
        # Step one microsecond before start to land in previous window.
        start = w_start - timedelta(microseconds=1)
    out.reverse()
    return out


def group_atoms_by_window(
    scale: PeriodScale | str,
    atoms: Sequence[Atom],
) -> dict[tuple[datetime, datetime], list[Atom]]:
    """Bucket atoms by the ``scale`` window containing each atom's ``t_start``."""
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    buckets: dict[tuple[datetime, datetime], list[Atom]] = {}
    for atom in atoms:
        try:
            key = window_bounds(scale, atom.t_start)
        except (TypeError, ValueError):
            continue
        buckets.setdefault(key, []).append(atom)
    return buckets


__all__ = [
    "child_scale",
    "group_atoms_by_window",
    "iter_windows",
    "list_by_moment",
    "list_range",
    "parent_scale",
    "walk_backward",
    "walk_forward",
    "windows_in_horizon",
]
