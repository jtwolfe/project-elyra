"""Period summary ladder — template-first (Phase 1).

Scope: UTC grid windows, template render, stable-id replace, budgeted
``refresh_due`` for idle ticks. Child-summary preference for coarser scales.
In scope: no LLM; deterministic highlight ranking; ladder/state.json when store
exposes ``memory_dir``.
Out of scope: presence worker hook (PR5), meal composition, promote.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from elyra.memory.config import LADDER_DIRNAME, LADDER_STATE
from elyra.memory.store import MemoryStore
from elyra.memory.temporal import (
    child_scale,
    list_range,
    windows_in_horizon,
)
from elyra.memory.types import (
    PERIOD_SCALE_ORDER,
    PERIOD_SCALES,
    Atom,
    PeriodScale,
    parse_iso_z,
    stable_summary_id,
    to_iso_z,
    window_bounds,
)

_LOG = logging.getLogger(__name__)

# Max highlight lines by scale (design: 12 / 16 / 20).
_MAX_HIGHLIGHTS: dict[str, int] = {
    "15m": 12,
    "1h": 16,
    "6h": 20,
    "1d": 20,
    "1w": 20,
    "1m": 20,
}

_MAX_CHILD_IDS = 64
_HIGHLIGHT_TRUNCATE = 160
_DEFAULT_RANGE_LIMIT = 5000

# Kinds excluded when collecting raw experience for a window.
_RAW_EXCLUDE_KINDS = frozenset({"summary", "parcel", "moment_meta"})

# Default lookback window counts per scale for refresh_due.
_DEFAULT_N_WINDOWS: dict[str, int] = {
    "15m": 8,
    "1h": 6,
    "6h": 4,
    "1d": 3,
    "1w": 3,
    "1m": 2,
}


def max_highlights(scale: PeriodScale | str) -> int:
    """Return highlight budget for ``scale``."""
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    return _MAX_HIGHLIGHTS[scale]


def _highlight_rank(atom: Atom) -> tuple[int, str, str]:
    """Sort key: speak > observation > ledger > failed tool > other; then time.

    Lower rank tuple sorts first (preferred).
    """
    kind = atom.kind
    meta = atom.meta or {}
    if kind == "speak":
        tier = 0
    elif kind == "observation":
        tier = 1
    elif kind == "ledger":
        tier = 2
    elif kind == "tool" and meta.get("ok") is False:
        tier = 3
    elif kind == "tool":
        tier = 4
    elif kind == "model":
        tier = 5
    elif kind == "summary":
        tier = 6
    else:
        tier = 7
    return (tier, to_iso_z(atom.t_start), atom.atom_id)


def _truncate(text: str, limit: int = _HIGHLIGHT_TRUNCATE) -> str:
    text = (text or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]
    return text[: limit - 1] + "…"


def _goal_ids_from_atoms(atoms: Sequence[Atom]) -> list[str]:
    seen: list[str] = []
    found: set[str] = set()
    for atom in atoms:
        meta = atom.meta or {}
        candidates: list[str] = []
        gid = meta.get("goal_id")
        if isinstance(gid, str) and gid:
            candidates.append(gid)
        gids = meta.get("goal_ids")
        if isinstance(gids, (list, tuple)):
            for g in gids:
                if isinstance(g, str) and g:
                    candidates.append(g)
        for g in candidates:
            if g not in found:
                found.add(g)
                seen.append(g)
    return seen


def _why_now_open_thread(atoms: Sequence[Atom]) -> str | None:
    """Last non-empty why_now in chronological order, if any."""
    last: str | None = None
    for atom in sorted(atoms, key=lambda a: (to_iso_z(a.t_start), a.atom_id)):
        meta = atom.meta or {}
        why = meta.get("why_now")
        if isinstance(why, str) and why.strip():
            last = why.strip()
    return last


def select_highlights(
    atoms: Sequence[Atom],
    *,
    scale: PeriodScale | str,
    limit: int | None = None,
) -> list[Atom]:
    """Rank and pick highlight atoms for a summary window.

    Prefer speak > observation > ledger > failed tool > other; sample remainder.
    """
    cap = limit if limit is not None else max_highlights(scale)
    if cap <= 0 or not atoms:
        return []
    ranked = sorted(atoms, key=_highlight_rank)
    return ranked[:cap]


def collect_window_sources(
    store: MemoryStore,
    scale: PeriodScale | str,
    window_start: datetime | str,
    window_end: datetime | str,
    *,
    prefer_children: bool = True,
    limit: int = _DEFAULT_RANGE_LIMIT,
) -> tuple[list[Atom], bool, PeriodScale | None]:
    """Collect source atoms for a ladder window.

    Prefers existing child-scale summary atoms when ``prefer_children`` and a
    child scale exists and any child summaries overlap the window. Falls back
    to raw (non-summary) atoms when children are missing.

    Returns ``(sources, from_children, child_scale_used)``.
    """
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    w_start = parse_iso_z(window_start)
    w_end = parse_iso_z(window_end)
    child = child_scale(scale)

    if prefer_children and child is not None:
        children = store.list_summaries(
            child,  # type: ignore[arg-type]
            overlapping=(w_start, w_end),
            limit=limit,
        )
        # Only those fully or partially inside; list_summaries already overlaps.
        if children:
            children = sorted(
                children, key=lambda a: (to_iso_z(a.window_start or a.t_start), a.atom_id)
            )
            return children, True, child

    raw = list_range(
        store,
        w_start,
        w_end,
        limit=limit,
    )
    sources = [a for a in raw if a.kind not in _RAW_EXCLUDE_KINDS]
    # Also drop any summary of this same scale (self).
    sources = [
        a
        for a in sources
        if not (a.kind == "summary" and a.scale == scale)
    ]
    return sources, False, child


def render_template_summary(
    *,
    scale: PeriodScale | str,
    window_start: datetime | str,
    window_end: datetime | str,
    sources: Sequence[Atom],
    highlights: Sequence[Atom] | None = None,
) -> str:
    """Render normative Phase 1 template body (no LLM)."""
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    ws = to_iso_z(window_start)
    we = to_iso_z(window_end)

    moments = sorted(
        {
            a.moment_id
            for a in sources
            if isinstance(a.moment_id, str) and a.moment_id
        }
    )
    n_moments = len(moments)
    n_atoms = len(sources)
    n_speak = sum(1 for a in sources if a.kind == "speak")
    n_tool = sum(1 for a in sources if a.kind == "tool")

    goals = _goal_ids_from_atoms(sources)
    if goals:
        # Short list — cap display length.
        shown = goals[:8]
        goals_text = ", ".join(shown)
        if len(goals) > 8:
            goals_text += f", +{len(goals) - 8}"
    else:
        goals_text = "—"

    if highlights is None:
        highlights = select_highlights(sources, scale=scale)

    lines = [
        f"[summary {scale} | {ws} → {we}]",
        f"moments: {n_moments} | atoms: {n_atoms} | speaks: {n_speak} | tools: {n_tool}",
        f"goals touched: {goals_text}",
        "highlights:",
    ]
    if highlights:
        for h in highlights:
            t = to_iso_z(h.t_start)
            body = _truncate(h.content_text or "")
            lines.append(f"- {t} {h.kind}: {body}")
    else:
        lines.append("- (none)")

    open_thread = _why_now_open_thread(sources)
    if open_thread:
        lines.append(f"(open threads: {_truncate(open_thread, 200)})")
    else:
        lines.append("(open threads: —)")

    return "\n".join(lines)


def build_summary_atom(
    store: MemoryStore,
    scale: PeriodScale | str,
    window_start: datetime | str,
    window_end: datetime | str | None = None,
    *,
    prefer_children: bool = True,
) -> Atom:
    """Build (do not store) a template summary atom for the window.

    Uses ``stable_summary_id`` so replace-in-place overwrites the same id.
    """
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    w_start = parse_iso_z(window_start)
    if window_end is None:
        _, w_end = window_bounds(scale, w_start)
    else:
        w_end = parse_iso_z(window_end)

    sources, from_children, child = collect_window_sources(
        store,
        scale,
        w_start,
        w_end,
        prefer_children=prefer_children,
    )
    highlights = select_highlights(sources, scale=scale)
    body = render_template_summary(
        scale=scale,
        window_start=w_start,
        window_end=w_end,
        sources=sources,
        highlights=highlights,
    )
    child_ids = [a.atom_id for a in sources[:_MAX_CHILD_IDS]]
    atom_id = stable_summary_id(scale, w_start)
    meta: dict[str, Any] = {
        "child_atom_ids": child_ids,
        "n_atoms": len(sources),
        "n_moments": len(
            {
                a.moment_id
                for a in sources
                if isinstance(a.moment_id, str) and a.moment_id
            }
        ),
        "n_speak": sum(1 for a in sources if a.kind == "speak"),
        "n_tool": sum(1 for a in sources if a.kind == "tool"),
        "source": "template",
        "from_children": from_children,
        "child_scale": child,
    }
    return Atom(
        atom_id=atom_id,
        t_start=to_iso_z(w_start),
        t_end=to_iso_z(w_end),
        kind="summary",
        scale=scale,
        window_start=to_iso_z(w_start),
        window_end=to_iso_z(w_end),
        moment_id=None,
        content_text=body,
        content_ref="inline",
        meta=meta,
        embedding_status="none",
    )


def refresh_window(
    store: MemoryStore,
    scale: PeriodScale | str,
    t: datetime | str,
    *,
    prefer_children: bool = True,
    skip_empty: bool = True,
) -> Atom | None:
    """Build and ``put_atom`` the summary for the ``scale`` window containing ``t``.

    Returns the stored atom, or ``None`` when ``skip_empty`` and no sources.
    Replace-stable: same ``(scale, window_start)`` always uses ``stable_summary_id``.
    """
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    w_start, w_end = window_bounds(scale, t)
    sources, _, _ = collect_window_sources(
        store, scale, w_start, w_end, prefer_children=prefer_children
    )
    if skip_empty and not sources:
        return None
    atom = build_summary_atom(
        store,
        scale,
        w_start,
        w_end,
        prefer_children=prefer_children,
    )
    return store.put_atom(atom)


def _state_path_for_store(store: MemoryStore) -> Path | None:
    memory_dir = getattr(store, "memory_dir", None)
    if memory_dir is None:
        return None
    return Path(memory_dir) / LADDER_DIRNAME / LADDER_STATE


def load_ladder_state(
    store: MemoryStore | None = None,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Load ladder/state.json (or empty default)."""
    state_path = path or (_state_path_for_store(store) if store else None)
    if state_path is None or not state_path.is_file():
        return {
            "round_robin_idx": 0,
            "last_refresh": {},
        }
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("ladder state load failed: %s", exc)
        return {
            "round_robin_idx": 0,
            "last_refresh": {},
        }
    if not isinstance(data, dict):
        return {"round_robin_idx": 0, "last_refresh": {}}
    data.setdefault("round_robin_idx", 0)
    data.setdefault("last_refresh", {})
    return data


def save_ladder_state(
    state: Mapping[str, Any],
    store: MemoryStore | None = None,
    *,
    path: Path | None = None,
) -> None:
    """Persist ladder state when a path is available."""
    state_path = path or (_state_path_for_store(store) if store else None)
    if state_path is None:
        return
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(dict(state), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(state_path)
    except OSError as exc:
        _LOG.warning("ladder state save failed: %s", exc)


def _windows_needing_refresh(
    store: MemoryStore,
    scale: PeriodScale | str,
    now: datetime,
    *,
    n_windows: int | None = None,
) -> list[tuple[datetime, datetime]]:
    """Recent windows that have source content (cheap due-set for idle tick)."""
    n = n_windows if n_windows is not None else _DEFAULT_N_WINDOWS.get(scale, 3)
    candidates = windows_in_horizon(scale, now, n_windows=n)
    due: list[tuple[datetime, datetime]] = []
    for w_start, w_end in candidates:
        sources, _, _ = collect_window_sources(
            store, scale, w_start, w_end, prefer_children=True
        )
        if sources:
            due.append((w_start, w_end))
    return due


def refresh_due(
    store: MemoryStore,
    now: datetime | str | None = None,
    *,
    max_ms: int = 50,
    scales: Sequence[PeriodScale | str] | None = None,
    state: MutableMapping[str, Any] | None = None,
    prefer_children: bool = True,
    n_windows: int | None = None,
) -> dict[str, Any]:
    """Refresh due period summaries under a wall-clock budget (idle use).

    Normative idle behaviour (presence wiring is a later PR):

    - At most **one scale** per call (round-robin over ``scales``).
    - Stop before starting another window once ``max_ms`` has elapsed.
    - Template-only — **never** calls an LLM.
    - ``max_ms <= 0`` advances round-robin but refreshes nothing.

    Returns a small result dict: scale, refreshed count, window keys, elapsed_ms.
    """
    now_dt = parse_iso_z(now) if now is not None else datetime.now(UTC)

    scale_list: list[str] = (
        list(scales) if scales is not None else list(PERIOD_SCALE_ORDER)
    )
    if not scale_list:
        return {
            "scale": None,
            "refreshed": 0,
            "windows": [],
            "elapsed_ms": 0.0,
            "skipped": True,
            "reason": "no_scales",
        }

    owned_state = state is None
    if state is None:
        state = load_ladder_state(store)

    idx = int(state.get("round_robin_idx") or 0) % len(scale_list)
    scale = scale_list[idx]
    # Advance round-robin for next tick regardless of work done.
    state["round_robin_idx"] = (idx + 1) % len(scale_list)

    t0 = time.monotonic()
    refreshed_windows: list[str] = []
    refreshed = 0

    if max_ms is not None and int(max_ms) <= 0:
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        if owned_state:
            save_ladder_state(state, store)
        return {
            "scale": scale,
            "refreshed": 0,
            "windows": [],
            "elapsed_ms": elapsed_ms,
            "skipped": True,
            "reason": "max_ms",
        }

    due = _windows_needing_refresh(
        store, scale, now_dt, n_windows=n_windows
    )
    budget = float(max_ms) if max_ms is not None else float("inf")

    for w_start, w_end in due:
        elapsed = (time.monotonic() - t0) * 1000.0
        if elapsed >= budget:
            break
        try:
            atom = refresh_window(
                store,
                scale,
                w_start,
                prefer_children=prefer_children,
                skip_empty=True,
            )
        except Exception:  # noqa: BLE001 — idle path must never raise
            _LOG.exception(
                "ladder refresh failed scale=%s window=%s",
                scale,
                to_iso_z(w_start),
            )
            continue
        if atom is not None:
            refreshed += 1
            refreshed_windows.append(to_iso_z(w_start))

    elapsed_ms = (time.monotonic() - t0) * 1000.0
    last = state.setdefault("last_refresh", {})
    if not isinstance(last, dict):
        last = {}
        state["last_refresh"] = last
    last[str(scale)] = to_iso_z(now_dt)

    if owned_state:
        save_ladder_state(state, store)

    return {
        "scale": scale,
        "refreshed": refreshed,
        "windows": refreshed_windows,
        "elapsed_ms": elapsed_ms,
        "skipped": False,
        "reason": None,
    }


__all__ = [
    "build_summary_atom",
    "collect_window_sources",
    "load_ladder_state",
    "max_highlights",
    "refresh_due",
    "refresh_window",
    "render_template_summary",
    "save_ladder_state",
    "select_highlights",
]
