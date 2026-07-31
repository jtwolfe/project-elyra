"""Period summary ladder — template + optional LLM, hourly cascade.

Scope: UTC grid windows, template render, versioned coarser tips (KD-TIP),
source packs, budgeted ``tick`` / ``refresh_due`` for idle, cascade via write
parent map, honesty meta fabric lists (``child_atom_ids`` / ``source_atom_ids`` /
``supersedes_atom_id``) for GraphView projection (PR-C).
In scope: SummaryLlm protocol consumer (no presence / ChatClient import).
Out of scope: meal pack policy (PR-D).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from elyra.memory.config import (
    LADDER_DIRNAME,
    LADDER_SOURCE_EDGE_K_DEFAULT,
    LADDER_SOURCE_EDGE_K_MAX,
    LADDER_STATE,
    MemorySettings,
)
from elyra.memory.ladder_llm import SummaryLlm, SummaryLlmError
from elyra.memory.store import MemoryStore
from elyra.memory.temporal import (
    child_scale,
    list_range,
    parent_scale_write,
    windows_in_horizon,
)
from elyra.memory.types import (
    PERIOD_SCALE_ORDER,
    PERIOD_SCALE_ORDER_WRITE,
    PERIOD_SCALES,
    PERIOD_SCALES_LEGACY,
    PERIOD_SCALES_WRITE,
    Atom,
    PeriodScale,
    parse_iso_z,
    stable_summary_id,
    to_iso_z,
    versioned_summary_id,
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
    "1y": 20,
}

# Raise 64 → 96 so a full day of 1h children fits (KD design).
_MAX_CHILD_IDS = 96
# Absolute ceiling for source_atom_ids (shared with config / settings).
_MAX_SOURCE_ATOM_IDS = LADDER_SOURCE_EDGE_K_MAX
_DEFAULT_SOURCE_EDGE_K = LADDER_SOURCE_EDGE_K_DEFAULT
_MAX_POINTER_IDS = 24
_HIGHLIGHT_TRUNCATE = 160
_PACK_LINE_TRUNCATE = 200
_DEFAULT_RANGE_LIMIT = 5000
_MAX_MOMENT_BLOCKS = 40
_MAX_ATOMS_PER_MOMENT = 8
_MIN_GAP_MINUTES = 5
_CHILD_BLURB_CHARS = 800

# Kinds excluded when collecting raw experience for a window.
_RAW_EXCLUDE_KINDS = frozenset({"summary", "parcel", "moment_meta"})

# Default lookback window counts per scale for refresh_due (write scales).
_DEFAULT_N_WINDOWS: dict[str, int] = {
    "15m": 8,
    "1h": 6,
    "6h": 4,
    "1d": 3,
    "1w": 3,
    "1m": 2,
    "1y": 2,
}

# Soft LLM token ceilings (draft / final). Soft targets, not hard char counters.
_LLM_MAX_TOKENS_DRAFT: dict[str, int] = {
    "1h": 800,
    "1d": 2200,
    "1w": 2800,
    "1m": 3400,
    "1y": 4500,
    "15m": 600,
    "6h": 1200,
}
_LLM_MAX_TOKENS_FINAL: dict[str, int] = {
    "1h": 600,
    "1d": 1800,
    "1w": 2500,
    "1m": 3000,
    "1y": 4000,
    "15m": 500,
    "6h": 1000,
}
# Approx char budget for skip-pass-B heuristic (~4 chars/token soft).
_SOFT_CHAR_BUDGET: dict[str, int] = {
    "1h": 2400,
    "1d": 7200,
    "1w": 10000,
    "1m": 12000,
    "1y": 16000,
    "15m": 2000,
    "6h": 4000,
}

# Always two-pass for coarser than 1h.
_ALWAYS_TWO_PASS = frozenset({"1d", "1w", "1m", "1y"})

# Coarser write scales get a new version atom per cascade (immutable old + tip).
# 1h / legacy use stable_summary_id tip-replace (no version fan-out by default).
_VERSIONED_SCALES = frozenset({"1d", "1w", "1m", "1y"})

# Provisional dogfood knobs for instance-age scale growth (design §9; PR-E pins).
# Not locked product law — may move after dogfood without design re-open.
LADDER_ENOUGH_1D_TIPS = 3  # unlock 1w early if age < 7d
LADDER_ENOUGH_1W_TIPS = 2  # unlock 1m early
LADDER_ENOUGH_1M_TIPS = 2  # unlock 1y early
_AGE_UNLOCK_1W = timedelta(days=7)
_AGE_UNLOCK_1M = timedelta(days=28)
_AGE_UNLOCK_1Y = timedelta(days=365)


def allowed_scales(
    instance_created_at: datetime | str | None,
    now: datetime | str,
    *,
    tip_counts: Mapping[str, int] | None = None,
) -> list[str]:
    """Write scales allowed for this instance age / provisional tip counts.

    Always returns ``1h`` and ``1d``. Coarser scales unlock by soft age
    thresholds (7d / 28d / 365d) **or** enough child-scale tips (provisional
    ``LADDER_ENOUGH_*`` constants).
    """
    now_dt = parse_iso_z(now) if not isinstance(now, datetime) else now
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=UTC)
    if instance_created_at is None:
        created = now_dt
    elif isinstance(instance_created_at, datetime):
        created = instance_created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
    else:
        try:
            created = parse_iso_z(instance_created_at)
        except (TypeError, ValueError):
            created = now_dt
    age = now_dt - created
    tips = dict(tip_counts or {})
    out: list[str] = ["1h", "1d"]
    if age >= _AGE_UNLOCK_1W or int(tips.get("1d", 0) or 0) >= LADDER_ENOUGH_1D_TIPS:
        out.append("1w")
    if age >= _AGE_UNLOCK_1M or int(tips.get("1w", 0) or 0) >= LADDER_ENOUGH_1W_TIPS:
        out.append("1m")
    if age >= _AGE_UNLOCK_1Y or int(tips.get("1m", 0) or 0) >= LADDER_ENOUGH_1M_TIPS:
        out.append("1y")
    return out


def scale_allowed_for_instance_age(
    scale: PeriodScale | str,
    instance_created_at: datetime | str | None,
    now: datetime | str,
    *,
    tip_counts: Mapping[str, int] | None = None,
) -> bool:
    """True when ``scale`` is in :func:`allowed_scales` for this instance."""
    s = str(scale)
    if s in PERIOD_SCALES_LEGACY:
        # Legacy repair is not age-gated; write path still controlled by settings.
        return True
    return s in allowed_scales(
        instance_created_at, now, tip_counts=tip_counts
    )


def read_instance_created_at(store: MemoryStore) -> datetime | None:
    """Read ``created_at`` from store ``meta.json`` (best-effort)."""
    meta_path = getattr(store, "meta_path", None)
    if meta_path is None:
        memory_dir = getattr(store, "memory_dir", None)
        if memory_dir is not None:
            meta_path = Path(memory_dir) / "meta.json"
    if meta_path is None:
        return None
    try:
        path = Path(meta_path)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("created_at")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return parse_iso_z(raw)
    except (TypeError, ValueError):
        return None


def count_tip_summaries(
    store: MemoryStore,
    scales: Sequence[PeriodScale | str] | None = None,
) -> dict[str, int]:
    """Count ladder-index tips per scale (KD-TIP; O(tips) via list_summaries)."""
    scale_list = list(scales) if scales is not None else list(PERIOD_SCALE_ORDER_WRITE)
    out: dict[str, int] = {}
    for scale in scale_list:
        try:
            tips = store.list_summaries(str(scale), limit=10_000, tips_only=True)
            out[str(scale)] = len(tips)
        except Exception:  # noqa: BLE001 — observability / gating must not raise
            out[str(scale)] = 0
    return out


def ladder_status_snapshot(
    store: MemoryStore | None,
    settings: MemorySettings | None = None,
    *,
    state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compact ladder observability for ``/api/status`` memory block (design §10)."""
    settings = settings or MemorySettings()
    block: dict[str, Any] = {
        "enabled": bool(getattr(settings, "ladder_enabled", True)),
        "summary_mode": str(getattr(settings, "summary_mode", "template") or "template"),
        "ladder_hourly_max_ms": int(
            getattr(settings, "ladder_hourly_max_ms", 12000) or 12000
        ),
        "ladder_catchup_max_hours": int(
            getattr(settings, "ladder_catchup_max_hours", 24) or 24
        ),
        "ladder_llm_max_calls_per_tick": int(
            getattr(settings, "ladder_llm_max_calls_per_tick", 3) or 3
        ),
        "ladder_llm_max_calls_per_hour": int(
            getattr(settings, "ladder_llm_max_calls_per_hour", 40) or 40
        ),
        "ladder_max_ms_per_tick": int(
            getattr(settings, "ladder_max_ms_per_tick", 200) or 200
        ),
        "last_hourly_process": None,
        "last_closed_1h_processed": None,
        "catchup_cursor": None,
        "llm_calls_hour": {"hour": None, "count": 0},
        "dirty_1h_count": 0,
        "cascade_pending_count": 0,
        "allowed_scales": ["1h", "1d"],
    }
    st = state
    if st is None and store is not None:
        try:
            st = load_ladder_state(store)
        except Exception:  # noqa: BLE001
            st = None
    if isinstance(st, Mapping):
        block["last_hourly_process"] = st.get("last_hourly_process")
        block["last_closed_1h_processed"] = st.get("last_closed_1h_processed")
        block["catchup_cursor"] = st.get("catchup_cursor")
        bucket = st.get("llm_calls_hour")
        if isinstance(bucket, dict):
            block["llm_calls_hour"] = {
                "hour": bucket.get("hour"),
                "count": int(bucket.get("count") or 0),
            }
        dirty = st.get("dirty_1h_windows") or []
        if isinstance(dirty, list):
            block["dirty_1h_count"] = len(dirty)
        pending = st.get("cascade_pending_1h") or []
        if isinstance(pending, list):
            block["cascade_pending_count"] = len(pending)
    if store is not None:
        try:
            created = read_instance_created_at(store)
            tips = count_tip_summaries(store)
            block["tip_counts"] = tips
            block["allowed_scales"] = allowed_scales(
                created, datetime.now(UTC), tip_counts=tips
            )
            if created is not None:
                block["instance_created_at"] = to_iso_z(created)
        except Exception:  # noqa: BLE001
            pass
    return block


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


def _task_ids_from_atoms(atoms: Sequence[Atom]) -> list[str]:
    seen: list[str] = []
    found: set[str] = set()
    for atom in atoms:
        meta = atom.meta or {}
        for key in ("task_id", "task_ids"):
            val = meta.get(key)
            if isinstance(val, str) and val and val not in found:
                found.add(val)
                seen.append(val)
            elif isinstance(val, (list, tuple)):
                for t in val:
                    if isinstance(t, str) and t and t not in found:
                        found.add(t)
                        seen.append(t)
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


def _scale_writable(
    scale: PeriodScale | str,
    *,
    settings: MemorySettings | None = None,
    allow_legacy: bool | None = None,
) -> bool:
    """True when the scale may receive a new write under settings."""
    if scale not in PERIOD_SCALES:
        return False
    if scale in PERIOD_SCALES_WRITE:
        return True
    if scale in PERIOD_SCALES_LEGACY:
        if allow_legacy is not None:
            return bool(allow_legacy)
        if settings is not None:
            return bool(getattr(settings, "ladder_write_legacy_scales", False))
        return False
    return False


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

    Write map: child of ``1h`` is ``None`` (raw only); child of ``1d`` is ``1h``.

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
        if children:
            children = sorted(
                children,
                key=lambda a: (to_iso_z(a.window_start or a.t_start), a.atom_id),
            )
            return children, True, child

    raw = list_range(
        store,
        w_start,
        w_end,
        limit=limit,
    )
    sources = [a for a in raw if a.kind not in _RAW_EXCLUDE_KINDS]
    sources = [
        a
        for a in sources
        if not (a.kind == "summary" and a.scale == scale)
    ]
    return sources, False, child


def moment_blocks_for_window(
    store: MemoryStore,
    w_start: datetime | str,
    w_end: datetime | str,
    *,
    limit: int = _DEFAULT_RANGE_LIMIT,
    max_moments: int = _MAX_MOMENT_BLOCKS,
    max_atoms_per_moment: int = _MAX_ATOMS_PER_MOMENT,
) -> list[dict[str, Any]]:
    """Group raw experience atoms by ``moment_id`` into ordered moment blocks.

    Each block: ``moment_id``, ``t0``, ``t1``, ``why_now``, ``n_atoms``, ``lines``.
    """
    w_s = parse_iso_z(w_start)
    w_e = parse_iso_z(w_end)
    raw = list_range(store, w_s, w_e, limit=limit)
    atoms = [a for a in raw if a.kind not in _RAW_EXCLUDE_KINDS]
    by_moment: dict[str, list[Atom]] = {}
    orphan: list[Atom] = []
    for a in atoms:
        mid = a.moment_id
        if isinstance(mid, str) and mid:
            by_moment.setdefault(mid, []).append(a)
        else:
            orphan.append(a)

    blocks: list[dict[str, Any]] = []
    for mid, group in by_moment.items():
        group_sorted = sorted(group, key=lambda a: (to_iso_z(a.t_start), a.atom_id))
        t0 = group_sorted[0].t_start
        t1 = group_sorted[-1].t_end or group_sorted[-1].t_start
        why = _why_now_open_thread(group_sorted)
        ranked = sorted(group_sorted, key=_highlight_rank)[:max_atoms_per_moment]
        lines = [
            f"{a.kind}: {_truncate(a.content_text or '', _PACK_LINE_TRUNCATE)}"
            for a in ranked
        ]
        blocks.append(
            {
                "moment_id": mid,
                "t0": to_iso_z(t0),
                "t1": to_iso_z(t1),
                "why_now": why,
                "n_atoms": len(group_sorted),
                "lines": lines,
            }
        )
    blocks.sort(key=lambda b: b["t0"])
    if orphan:
        ranked_o = sorted(orphan, key=_highlight_rank)[:max_atoms_per_moment]
        blocks.append(
            {
                "moment_id": None,
                "t0": to_iso_z(ranked_o[0].t_start) if ranked_o else to_iso_z(w_s),
                "t1": to_iso_z(ranked_o[-1].t_start) if ranked_o else to_iso_z(w_e),
                "why_now": None,
                "n_atoms": len(orphan),
                "lines": [
                    f"{a.kind}: {_truncate(a.content_text or '', _PACK_LINE_TRUNCATE)}"
                    for a in ranked_o
                ],
            }
        )
    return blocks[:max_moments]


def gap_spans(
    window_start: datetime | str,
    window_end: datetime | str,
    moment_intervals: Sequence[tuple[datetime | str, datetime | str]],
    *,
    min_gap: timedelta | None = None,
) -> list[tuple[datetime, datetime]]:
    """Half-open empty ranges with no moments; emit gaps ≥ min threshold.

    ``moment_intervals`` are half-open ``[t0, t1)`` coverage spans. Overlapping
    / adjacent intervals are merged before gap extraction.
    """
    w_s = parse_iso_z(window_start)
    w_e = parse_iso_z(window_end)
    if w_e <= w_s:
        return []
    threshold = min_gap if min_gap is not None else timedelta(minutes=_MIN_GAP_MINUTES)

    spans: list[tuple[datetime, datetime]] = []
    for raw0, raw1 in moment_intervals:
        t0 = parse_iso_z(raw0)
        t1 = parse_iso_z(raw1)
        if t1 <= t0:
            t1 = t0 + timedelta(seconds=1)
        # Clip to window.
        t0 = max(t0, w_s)
        t1 = min(t1, w_e)
        if t1 > t0:
            spans.append((t0, t1))
    if not spans:
        gap = (w_s, w_e)
        return [gap] if (w_e - w_s) >= threshold else []

    spans.sort(key=lambda p: p[0])
    merged: list[tuple[datetime, datetime]] = [spans[0]]
    for s, e in spans[1:]:
        ms, me = merged[-1]
        if s <= me:
            merged[-1] = (ms, max(me, e))
        else:
            merged.append((s, e))

    gaps: list[tuple[datetime, datetime]] = []
    cursor = w_s
    for s, e in merged:
        if s > cursor and (s - cursor) >= threshold:
            gaps.append((cursor, s))
        cursor = max(cursor, e)
    if w_e > cursor and (w_e - cursor) >= threshold:
        gaps.append((cursor, w_e))
    return gaps


def build_source_pack(
    scale: PeriodScale | str,
    window_start: datetime | str,
    window_end: datetime | str,
    sources: Sequence[Atom],
    *,
    identity_names: Mapping[str, str] | None = None,
    from_children: bool = False,
    store: MemoryStore | None = None,
) -> str:
    """Render structured source pack text for LLM / template diagnostics.

    For raw (1h) sources, groups by moment and includes gap spans. For child
    tips, lists child blurbs and missing slots when possible.
    """
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    ws = to_iso_z(window_start)
    we = to_iso_z(window_end)
    w_s = parse_iso_z(window_start)
    w_e = parse_iso_z(window_end)
    names = dict(identity_names or {})
    self_name = names.get("self") or names.get("display_name") or "Elyra"
    user_name = names.get("user") or names.get("user_display_name") or "user"

    lines = [
        f"[window {scale} | {ws} → {we}]",
        f"[identity] self={self_name}; user={user_name} (soft names only)",
    ]

    if from_children:
        child = child_scale(scale)
        lines.append(f"[child tips scale={child}]")
        if sources:
            for a in sources:
                blurb = _truncate(a.content_text or "", _CHILD_BLURB_CHARS)
                tip_ws = a.window_start or a.t_start
                lines.append(f"- {tip_ws} {a.atom_id}: {blurb}")
        else:
            lines.append("- (none)")
        # Note missing child windows when store available.
        if store is not None and child is not None:
            child_windows = list(
                _iter_child_windows(child, w_s, w_e)
            )
            present_starts = {
                to_iso_z(parse_iso_z(a.window_start or a.t_start))
                for a in sources
                if a.window_start or a.t_start
            }
            missing = [
                to_iso_z(cw)
                for cw, _ in child_windows
                if to_iso_z(cw) not in present_starts and cw + timedelta(microseconds=1) < w_e
            ]
            if missing:
                lines.append("[missing child windows]")
                for m in missing[:48]:
                    lines.append(f"- {m}")
        return "\n".join(lines)

    # Raw experience pack with moments + gaps.
    lines.append("[moments]")
    blocks: list[dict[str, Any]]
    if store is not None:
        blocks = moment_blocks_for_window(store, w_s, w_e)
    else:
        blocks = _moment_blocks_from_sources(sources)

    if blocks:
        for b in blocks:
            mid = b.get("moment_id") or "—"
            why = b.get("why_now") or "—"
            lines.append(
                f"- {mid} {b['t0']}–{b['t1']} why_now={why} n_atoms={b['n_atoms']}"
            )
            for ln in b.get("lines") or []:
                lines.append(f"  - {ln}")
    else:
        lines.append("- (none)")

    intervals: list[tuple[datetime | str, datetime | str]] = [
        (b["t0"], b["t1"]) for b in blocks if b.get("t0") and b.get("t1")
    ]
    gaps = gap_spans(w_s, w_e, intervals)
    lines.append("[gaps]")
    if gaps:
        for g0, g1 in gaps:
            mins = int((g1 - g0).total_seconds() // 60)
            lines.append(
                f"- no moments from {to_iso_z(g0)} to {to_iso_z(g1)} (~{mins}m)"
            )
    else:
        lines.append("- (none above threshold)")

    highlights = select_highlights(sources, scale=scale)
    lines.append("[highlights ranked]")
    if highlights:
        for h in highlights:
            lines.append(
                f"- {to_iso_z(h.t_start)} {h.kind}: "
                f"{_truncate(h.content_text or '', _PACK_LINE_TRUNCATE)}"
            )
    else:
        lines.append("- (none)")

    return "\n".join(lines)


def _moment_blocks_from_sources(sources: Sequence[Atom]) -> list[dict[str, Any]]:
    by_moment: dict[str, list[Atom]] = {}
    for a in sources:
        mid = a.moment_id if isinstance(a.moment_id, str) and a.moment_id else "_orphan"
        by_moment.setdefault(mid, []).append(a)
    blocks: list[dict[str, Any]] = []
    for mid, group in by_moment.items():
        group_sorted = sorted(group, key=lambda a: (to_iso_z(a.t_start), a.atom_id))
        ranked = sorted(group_sorted, key=_highlight_rank)[:_MAX_ATOMS_PER_MOMENT]
        blocks.append(
            {
                "moment_id": None if mid == "_orphan" else mid,
                "t0": to_iso_z(group_sorted[0].t_start),
                "t1": to_iso_z(group_sorted[-1].t_end or group_sorted[-1].t_start),
                "why_now": _why_now_open_thread(group_sorted),
                "n_atoms": len(group_sorted),
                "lines": [
                    f"{a.kind}: {_truncate(a.content_text or '', _PACK_LINE_TRUNCATE)}"
                    for a in ranked
                ],
            }
        )
    blocks.sort(key=lambda b: b["t0"])
    return blocks[:_MAX_MOMENT_BLOCKS]


def _iter_child_windows(
    child_scale_name: str,
    w_start: datetime,
    w_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Grid child windows whose start is in ``[w_start, w_end)``."""
    out: list[tuple[datetime, datetime]] = []
    cursor = w_start
    # Safety cap for year-of-months etc.
    for _ in range(400):
        if cursor >= w_end:
            break
        cs, ce = window_bounds(child_scale_name, cursor)
        if cs >= w_end:
            break
        if cs >= w_start:
            out.append((cs, ce))
        if ce <= cursor:
            break
        cursor = ce
    return out


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


def _count_stats(sources: Sequence[Atom]) -> dict[str, int]:
    return {
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
    }


def uses_versioned_ids(scale: PeriodScale | str) -> bool:
    """True when coarser cascade writes a new version atom (not tip-replace)."""
    return str(scale) in _VERSIONED_SCALES


def child_content_hash(sources: Sequence[Atom]) -> str:
    """Set-stable hash of child tip set for skip-unchanged cascade.

    Material is ``atom_id`` + short body digest per source, sorted by
    ``atom_id`` so caller order cannot falsely miss a hash-equal skip.
    """
    ordered = sorted(sources, key=lambda a: a.atom_id)
    parts: list[str] = []
    for a in ordered:
        body = a.content_text or ""
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]
        parts.append(a.atom_id)
        parts.append(digest)
    material = "|".join(parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def resolve_tip(
    store: MemoryStore,
    scale: PeriodScale | str,
    window_start: datetime | str,
    window_end: datetime | str | None = None,
) -> Atom | None:
    """Return the ladder-index tip for ``(scale, window_start)`` if any (KD-TIP)."""
    w_start = parse_iso_z(window_start)
    if window_end is None:
        _, w_end = window_bounds(scale, w_start)
    else:
        w_end = parse_iso_z(window_end)
    tips = store.list_summaries(
        scale,  # type: ignore[arg-type]
        overlapping=(w_start, w_end),
        limit=32,
        tips_only=True,
    )
    target = to_iso_z(w_start)
    for tip in tips:
        if tip.window_start and to_iso_z(tip.window_start) == target:
            return tip
    return None
def _source_edge_k(settings: MemorySettings | None) -> int:
    """Write-time cap for ``meta.source_atom_ids`` (PR-C edge fabric)."""
    raw = _DEFAULT_SOURCE_EDGE_K
    if settings is not None:
        try:
            raw = int(getattr(settings, "ladder_source_edge_k", _DEFAULT_SOURCE_EDGE_K))
        except (TypeError, ValueError):
            raw = _DEFAULT_SOURCE_EDGE_K
    if raw < 0:
        raw = 0
    return min(raw, _MAX_SOURCE_ATOM_IDS)


def _build_honesty_meta(
    *,
    sources: Sequence[Atom],
    from_children: bool,
    child: PeriodScale | None,
    summary_mode_requested: str,
    source: str,
    llm_passes: int = 0,
    llm_error: str | None = None,
    llm_model: str | None = None,
    draft_chars: int | None = None,
    version: int = 1,
    supersedes_atom_id: str | None = None,
    child_content_hash_value: str | None = None,
    settings: MemorySettings | None = None,
    scale: PeriodScale | str | None = None,
) -> dict[str, Any]:
    stats = _count_stats(sources)
    # Fabric honesty: child_atom_ids are child *summaries* when from_children.
    # 1h raw keeps a legacy pointer list; coarser raw fallback leaves empty so
    # GraphView cannot emit summary_child → experience atoms.
    if from_children:
        child_ids = [a.atom_id for a in sources[:_MAX_CHILD_IDS]]
    elif scale == "1h":
        child_ids = [a.atom_id for a in sources[:_MAX_CHILD_IDS]]
    else:
        child_ids = []
    source_ids: list[str] = []
    if not from_children:
        ranked = sorted(sources, key=_highlight_rank)
        source_ids = [a.atom_id for a in ranked[: _source_edge_k(settings)]]
    goals = _goal_ids_from_atoms(sources)[:_MAX_POINTER_IDS]
    tasks = _task_ids_from_atoms(sources)[:_MAX_POINTER_IDS]
    pointer_atoms = [a.atom_id for a in select_highlights(sources, scale="1h", limit=12)]
    cch = child_content_hash_value
    if cch is None:
        cch = child_content_hash(sources)
    meta: dict[str, Any] = {
        "source": source,
        "summary_mode_requested": summary_mode_requested,
        "from_children": from_children,
        "child_scale": child,
        "child_atom_ids": child_ids,
        "source_atom_ids": source_ids,
        "n_atoms": stats["n_atoms"],
        "n_moments": stats["n_moments"],
        "n_speak": stats["n_speak"],
        "n_tool": stats["n_tool"],
        "pointer_atom_ids": pointer_atoms[:_MAX_POINTER_IDS],
        "pointer_goal_ids": goals,
        "pointer_task_ids": tasks,
        "version": int(version),
        "supersedes_atom_id": supersedes_atom_id,
        "previous_version_id": supersedes_atom_id,
        "child_content_hash": cch,
        "llm_model": llm_model,
        "llm_passes": llm_passes,
        "llm_error": llm_error,
        "draft_chars": draft_chars,
        "generated_at": to_iso_z(datetime.now(UTC)),
    }
    return meta


def _llm_generate_body(
    *,
    scale: str,
    window_start: datetime,
    window_end: datetime,
    sources: Sequence[Atom],
    from_children: bool,
    llm: SummaryLlm,
    identity_names: Mapping[str, str] | None,
    store: MemoryStore | None,
    max_passes: int = 2,
) -> tuple[str, int, int | None, str | None]:
    """Two-pass LLM generation. Returns (body, passes, draft_chars, error).

    On failure raises SummaryLlmError with ``passes_attempted`` set so callers
    can count partial usage. When ``max_passes < 2``, pass B is skipped and the
    draft is accepted as final (respects per-tick LLM call budget).
    """
    pack = build_source_pack(
        scale,
        window_start,
        window_end,
        sources,
        identity_names=identity_names,
        from_children=from_children,
        store=store,
    )
    draft_tokens = _LLM_MAX_TOKENS_DRAFT.get(scale, 800)
    final_tokens = _LLM_MAX_TOKENS_FINAL.get(scale, 600)
    soft_chars = _SOFT_CHAR_BUDGET.get(scale, 2400)
    passes_attempted = 0

    system = (
        "You write honest period memory narratives for an AI instance. "
        "Ground every claim in the source pack. Note idle/gap spans; do not "
        "invent work. Prefer under-claim. Soft-merge continuous tool chains "
        "into one beat when natural. Use soft self/other names from the pack. "
        "You may mention important a_/g_/t_ ids in prose."
    )
    user_a = (
        f"Write a draft narrative for scale={scale} window "
        f"{to_iso_z(window_start)} → {to_iso_z(window_end)}.\n\n"
        f"{pack}"
    )
    try:
        draft = llm.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_a},
            ],
            max_tokens=draft_tokens,
        )
    except SummaryLlmError as exc:
        # Count the failed in-flight pass A call.
        raise SummaryLlmError(str(exc), passes_attempted=1) from exc
    passes_attempted = 1
    draft_chars = len(draft)
    want_b = scale in _ALWAYS_TWO_PASS or draft_chars > soft_chars
    # Honour remaining call budget: do not start pass B without a second slot.
    if not want_b or int(max_passes) < 2:
        return draft.strip(), 1, draft_chars, None

    user_b = (
        f"Reduce the draft to the final stored body for scale={scale}. "
        f"Keep honesty about gaps; soft target ~{final_tokens} tokens; "
        f"hard cap well under 8000 characters. Preserve key pointers.\n\n"
        f"DRAFT:\n{draft}"
    )
    try:
        final = llm.complete(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user_b},
            ],
            max_tokens=final_tokens,
        )
    except SummaryLlmError as exc:
        # Include the failed in-flight pass B call (A + B = 2).
        raise SummaryLlmError(
            str(exc), passes_attempted=passes_attempted + 1
        ) from exc
    return final.strip(), 2, draft_chars, None


def build_summary_atom(
    store: MemoryStore,
    scale: PeriodScale | str,
    window_start: datetime | str,
    window_end: datetime | str | None = None,
    *,
    prefer_children: bool = True,
    settings: MemorySettings | None = None,
    llm: SummaryLlm | None = None,
    identity_names: Mapping[str, str] | None = None,
    allow_legacy: bool | None = None,
    version: int = 1,
    supersedes_atom_id: str | None = None,
    child_content_hash_value: str | None = None,
    llm_max_passes: int = 2,
) -> Atom:
    """Build (do not store) a summary atom for the window.

    * ``1h`` / legacy: ``stable_summary_id`` (tip-replace).
    * Coarser write scales: ``versioned_summary_id(scale, start, version)``.
    """
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    if not _scale_writable(scale, settings=settings, allow_legacy=allow_legacy):
        raise ValueError(
            f"scale {scale!r} is not writable "
            f"(legacy writes disabled; set ladder_write_legacy_scales=true)"
        )
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
    mode = "template"
    if settings is not None:
        mode = str(getattr(settings, "summary_mode", "template") or "template").lower()
    summary_mode_requested = mode if mode in ("template", "llm") else "template"

    body: str
    source_tag = "template"
    llm_passes = 0
    llm_error: str | None = None
    draft_chars: int | None = None
    cch = child_content_hash_value
    if cch is None:
        cch = child_content_hash(sources)

    if summary_mode_requested == "llm" and llm is not None:
        # Cap passes by remaining call budget (Issue 5): always-two-pass scales
        # accept draft-as-final when only one call remains.
        max_passes = max(1, int(llm_max_passes or 1))
        try:
            body, llm_passes, draft_chars, llm_error = _llm_generate_body(
                scale=str(scale),
                window_start=w_start,
                window_end=w_end,
                sources=sources,
                from_children=from_children,
                llm=llm,
                identity_names=identity_names,
                store=store,
                max_passes=max_passes,
            )
            if not body.strip():
                raise SummaryLlmError("empty_body", passes_attempted=llm_passes)
            source_tag = "llm"
        except SummaryLlmError as exc:
            _LOG.warning(
                "ladder LLM failed scale=%s window=%s: %s; template fallback",
                scale,
                to_iso_z(w_start),
                exc,
            )
            llm_error = str(exc)[:200]
            highlights = select_highlights(sources, scale=scale)
            body = render_template_summary(
                scale=scale,
                window_start=w_start,
                window_end=w_end,
                sources=sources,
                highlights=highlights,
            )
            source_tag = "llm_fallback_template"
            # Count partial complete() attempts so pacing stays honest.
            llm_passes = int(getattr(exc, "passes_attempted", 0) or 0)
        except Exception as exc:  # noqa: BLE001 — never kill presence
            _LOG.warning(
                "ladder LLM unexpected error scale=%s: %s; template fallback",
                scale,
                exc,
            )
            llm_error = f"unexpected: {exc}"[:200]
            highlights = select_highlights(sources, scale=scale)
            body = render_template_summary(
                scale=scale,
                window_start=w_start,
                window_end=w_end,
                sources=sources,
                highlights=highlights,
            )
            source_tag = "llm_fallback_template"
            llm_passes = 0
    else:
        highlights = select_highlights(sources, scale=scale)
        body = render_template_summary(
            scale=scale,
            window_start=w_start,
            window_end=w_end,
            sources=sources,
            highlights=highlights,
        )
        source_tag = "template"

    ver = max(1, int(version))
    if uses_versioned_ids(scale):
        atom_id = versioned_summary_id(scale, w_start, ver)
    else:
        atom_id = stable_summary_id(scale, w_start)
    meta = _build_honesty_meta(
        sources=sources,
        from_children=from_children,
        child=child,
        summary_mode_requested=summary_mode_requested,
        source=source_tag,
        llm_passes=llm_passes,
        llm_error=llm_error,
        draft_chars=draft_chars,
        version=ver,
        supersedes_atom_id=supersedes_atom_id,
        child_content_hash_value=cch,
        settings=settings,
        scale=scale,
    )
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
    settings: MemorySettings | None = None,
    llm: SummaryLlm | None = None,
    identity_names: Mapping[str, str] | None = None,
    allow_legacy: bool | None = None,
    llm_max_passes: int = 2,
) -> Atom | None:
    """Build and ``put_atom`` the summary for the ``scale`` window containing ``t``.

    * Coarser scales: immutable old + new version atom; tip pointer moves via
      ladder index on put. Skip when ``child_content_hash`` matches tip.
    * ``1h`` / legacy: tip-replace with ``stable_summary_id``; skip when body
      unchanged.

    Returns the stored (or existing tip) atom, or ``None`` when empty/non-writable.
    """
    if scale not in PERIOD_SCALES:
        raise ValueError(f"invalid period scale: {scale!r}")
    if not _scale_writable(scale, settings=settings, allow_legacy=allow_legacy):
        _LOG.debug("ladder refresh skipped non-writable scale=%s", scale)
        return None
    w_start, w_end = window_bounds(scale, t)
    sources, _, _ = collect_window_sources(
        store, scale, w_start, w_end, prefer_children=prefer_children
    )
    if skip_empty and not sources:
        return None

    tip = resolve_tip(store, scale, w_start, w_end)
    cch = child_content_hash(sources)
    version = 1
    supersedes: str | None = None

    if uses_versioned_ids(scale):
        if tip is not None:
            prev_hash = (tip.meta or {}).get("child_content_hash")
            # Hash-equal → skip build/LLM and do not mint a version.
            if prev_hash and prev_hash == cch:
                return tip
            prev_ver = int((tip.meta or {}).get("version") or 1)
            version = prev_ver + 1
            supersedes = tip.atom_id
    else:
        # 1h / legacy tip-replace: skip only when body *and* hash match tip.
        if tip is not None:
            prev_hash = (tip.meta or {}).get("child_content_hash")
            if prev_hash and prev_hash == cch:
                return tip

    atom = build_summary_atom(
        store,
        scale,
        w_start,
        w_end,
        prefer_children=prefer_children,
        settings=settings,
        llm=llm,
        identity_names=identity_names,
        allow_legacy=allow_legacy,
        version=version,
        supersedes_atom_id=supersedes,
        child_content_hash_value=cch,
        llm_max_passes=llm_max_passes,
    )

    if uses_versioned_ids(scale):
        # Hash already differed (or no tip). Always put a new version atom so
        # tip child_content_hash advances even when the rendered body is equal
        # (avoids LLM/template rebuild loop with a stale tip hash).
        # Never rewrite previous version rows; tip moves on put (KD-TIP).
        return store.put_atom(atom)

    # Tip-replace path (1h / legacy): same stable id.
    existing = store.get_atom(atom.atom_id)
    if existing is not None:
        same_body = (existing.content_text or "") == (atom.content_text or "")
        same_hash = (existing.meta or {}).get("child_content_hash") == cch
        if same_body and same_hash:
            return existing
        # Body and/or hash changed — put so tip meta (incl. hash) stays honest.
    return store.put_atom(atom)


def _state_path_for_store(store: MemoryStore) -> Path | None:
    memory_dir = getattr(store, "memory_dir", None)
    if memory_dir is None:
        return None
    return Path(memory_dir) / LADDER_DIRNAME / LADDER_STATE


def _default_ladder_state() -> dict[str, Any]:
    return {
        "round_robin_idx": 0,
        "last_refresh": {},
        "last_hourly_process": None,
        "last_closed_1h_processed": None,
        "dirty_1h_windows": [],
        # Hours whose 1h tip landed but parent cascade did not finish (budget).
        "cascade_pending_1h": [],
        # Next 1h window_start to consider (inclusive). Catch-up is done when
        # cursor >= current open hour start.
        "catchup_cursor": None,
        "llm_calls_hour": {"hour": None, "count": 0},
        "schema_version": 2,
    }


def load_ladder_state(
    store: MemoryStore | None = None,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Load ladder/state.json (or empty default, schema_version 2)."""
    state_path = path or (_state_path_for_store(store) if store else None)
    default = _default_ladder_state()
    if state_path is None or not state_path.is_file():
        return dict(default)
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("ladder state load failed: %s", exc)
        return dict(default)
    if not isinstance(data, dict):
        return dict(default)
    for key, val in default.items():
        data.setdefault(key, val if not isinstance(val, (dict, list)) else type(val)())
    if not isinstance(data.get("last_refresh"), dict):
        data["last_refresh"] = {}
    if not isinstance(data.get("dirty_1h_windows"), list):
        data["dirty_1h_windows"] = []
    if not isinstance(data.get("cascade_pending_1h"), list):
        data["cascade_pending_1h"] = []
    if not isinstance(data.get("llm_calls_hour"), dict):
        data["llm_calls_hour"] = {"hour": None, "count": 0}
    data["schema_version"] = 2
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
        payload = dict(state)
        payload["schema_version"] = 2
        tmp.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tmp.replace(state_path)
    except OSError as exc:
        _LOG.warning("ladder state save failed: %s", exc)


def mark_dirty_1h(
    store: MemoryStore | None = None,
    t: datetime | str | None = None,
    *,
    state: MutableMapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark the 1h window containing ``t`` dirty (moment finalize — no LLM).

    Does not generate summaries; idle ``tick`` will process dirty hours.
    """
    now_dt = parse_iso_z(t) if t is not None else datetime.now(UTC)
    w_start, _ = window_bounds("1h", now_dt)
    key = to_iso_z(w_start)
    owned = state is None
    if state is None:
        state = load_ladder_state(store)
    dirty = state.setdefault("dirty_1h_windows", [])
    if not isinstance(dirty, list):
        dirty = []
        state["dirty_1h_windows"] = dirty
    if key not in dirty:
        dirty.append(key)
    if owned:
        save_ladder_state(state, store)
    return {"dirty_1h": key, "dirty_count": len(dirty)}


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
    max_ms: int = 200,
    scales: Sequence[PeriodScale | str] | None = None,
    state: MutableMapping[str, Any] | None = None,
    prefer_children: bool = True,
    n_windows: int | None = None,
    settings: MemorySettings | None = None,
    llm: SummaryLlm | None = None,
    identity_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Refresh due period summaries under a wall-clock budget (idle nibble).

    - At most **one scale** per call (round-robin over write scales by default).
    - Stop before starting another window once ``max_ms`` has elapsed.
    - ``max_ms <= 0`` advances round-robin but refreshes nothing.
    """
    now_dt = parse_iso_z(now) if now is not None else datetime.now(UTC)

    if scales is not None:
        scale_list: list[str] = list(scales)
    else:
        # WRITE scales by default; include legacy 15m/6h when repair writes on.
        write_legacy = bool(
            settings is not None
            and getattr(settings, "ladder_write_legacy_scales", False)
        )
        scale_list = (
            list(PERIOD_SCALE_ORDER) if write_legacy else list(PERIOD_SCALE_ORDER_WRITE)
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
    # Nibble is template-oriented; do not spend LLM budget here unless mode=llm
    # and caller injected llm (still respect skip for hop-path safety).
    nibble_llm = llm if (
        settings is not None
        and str(getattr(settings, "summary_mode", "template")).lower() == "llm"
    ) else None

    for w_start, _w_end in due:
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
                settings=settings,
                llm=nibble_llm,
                identity_names=identity_names,
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


def _llm_calls_remaining(
    state: MutableMapping[str, Any],
    now: datetime,
    *,
    settings: MemorySettings,
) -> int:
    """Return remaining LLM calls allowed this UTC hour / tick pacing."""
    max_hour = int(getattr(settings, "ladder_llm_max_calls_per_hour", 40) or 40)
    bucket = state.setdefault("llm_calls_hour", {"hour": None, "count": 0})
    if not isinstance(bucket, dict):
        bucket = {"hour": None, "count": 0}
        state["llm_calls_hour"] = bucket
    hour_key = to_iso_z(now.replace(minute=0, second=0, microsecond=0))
    if bucket.get("hour") != hour_key:
        bucket["hour"] = hour_key
        bucket["count"] = 0
    used = int(bucket.get("count") or 0)
    return max(0, max_hour - used)


def _record_llm_calls(state: MutableMapping[str, Any], n: int, now: datetime) -> None:
    if n <= 0:
        return
    bucket = state.setdefault("llm_calls_hour", {"hour": None, "count": 0})
    if not isinstance(bucket, dict):
        bucket = {"hour": None, "count": 0}
        state["llm_calls_hour"] = bucket
    hour_key = to_iso_z(now.replace(minute=0, second=0, microsecond=0))
    if bucket.get("hour") != hour_key:
        bucket["hour"] = hour_key
        bucket["count"] = 0
    bucket["count"] = int(bucket.get("count") or 0) + int(n)


def _count_llm_usage_from_atom(
    atom: Atom | None,
    *,
    remaining_calls: int,
    state: MutableMapping[str, Any] | None,
    now_dt: datetime,
    used_llm: bool,
) -> int:
    """Debit remaining_calls from atom meta; return updated remaining."""
    if atom is None or not used_llm:
        return remaining_calls
    src = (atom.meta or {}).get("source")
    passes = int((atom.meta or {}).get("llm_passes") or 0)
    if src == "llm" and passes > 0:
        spent = max(1, passes)
        remaining_calls = max(0, remaining_calls - spent)
        if state is not None:
            _record_llm_calls(state, spent, now_dt)
    elif src == "llm_fallback_template":
        # Count attempted completes even when body fell back to template.
        spent = max(1, passes) if passes > 0 else 1
        remaining_calls = max(0, remaining_calls - spent)
        if state is not None:
            _record_llm_calls(state, spent, now_dt)
    return remaining_calls


def _refresh_wrote(tip_before: Atom | None, atom: Atom) -> bool:
    """True when ``refresh_window`` minted/replaced rather than returned a no-op tip.

    Detects new version ids, tip-replace content/hash updates, and first writes.
    """
    if tip_before is None:
        return True
    if atom.atom_id != tip_before.atom_id:
        return True
    before_meta = tip_before.meta or {}
    after_meta = atom.meta or {}
    if before_meta.get("child_content_hash") != after_meta.get("child_content_hash"):
        return True
    if (tip_before.content_text or "") != (atom.content_text or ""):
        return True
    if int(before_meta.get("version") or 0) != int(after_meta.get("version") or 0):
        return True
    return False


def cascade_from_hour(
    store: MemoryStore,
    hour_start: datetime | str,
    *,
    settings: MemorySettings | None = None,
    llm: SummaryLlm | None = None,
    identity_names: Mapping[str, str] | None = None,
    max_ms: float | None = None,
    t0: float | None = None,
    llm_calls_left: int | None = None,
    state: MutableMapping[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Recompute coarser tips for the parent chain of ``hour_start``.

    Walks write parent map only: 1d → 1w → 1m → 1y. Stops on budget / LLM cap.
    """
    h_start = parse_iso_z(hour_start)
    settings = settings or MemorySettings()
    start_mono = t0 if t0 is not None else time.monotonic()
    budget = float(max_ms) if max_ms is not None else float(
        getattr(settings, "ladder_hourly_max_ms", 12000) or 12000
    )
    remaining_calls = (
        llm_calls_left
        if llm_calls_left is not None
        else int(getattr(settings, "ladder_llm_max_calls_per_tick", 3) or 3)
    )
    now_dt = now or datetime.now(UTC)
    refreshed: list[str] = []
    stopped_reason: str | None = None
    instance_created = read_instance_created_at(store)
    s: str | None = "1h"
    while s is not None:
        try:
            p = parent_scale_write(s)
        except ValueError:
            break
        if p is None:
            break
        elapsed = (time.monotonic() - start_mono) * 1000.0
        if elapsed >= budget:
            stopped_reason = "budget"
            break
        # Instance-age / enough-tips gate (design §9): stop cascade; coarser gated too.
        tip_counts = count_tip_summaries(store)
        if not scale_allowed_for_instance_age(
            p, instance_created, now_dt, tip_counts=tip_counts
        ):
            stopped_reason = "instance_age"
            break
        w_start, w_end = window_bounds(p, h_start)
        skip_empty = bool(getattr(settings, "ladder_skip_empty", True))
        sources, _, _ = collect_window_sources(
            store, p, w_start, w_end, prefer_children=True
        )
        if skip_empty and not sources:
            s = p
            continue
        mode_llm = str(getattr(settings, "summary_mode", "template")).lower() == "llm"
        use_llm = llm if (mode_llm and remaining_calls > 0) else None
        # Issue 5: cap passes so a 2-pass scale cannot overrun remaining=1.
        max_passes = min(2, remaining_calls) if use_llm is not None else 1
        tip_before = resolve_tip(store, p, w_start, w_end)
        try:
            atom = refresh_window(
                store,
                p,
                w_start,
                prefer_children=True,
                skip_empty=skip_empty,
                settings=settings,
                llm=use_llm,
                identity_names=identity_names,
                llm_max_passes=max_passes,
            )
        except Exception:  # noqa: BLE001
            _LOG.exception("cascade refresh failed scale=%s", p)
            s = p
            continue
        if atom is not None and _refresh_wrote(tip_before, atom):
            # Only count real puts — hash-equal no-ops must not inflate metrics
            # or re-debit LLM pacing from the existing tip's meta.
            refreshed.append(f"{p}:{to_iso_z(w_start)}")
            remaining_calls = _count_llm_usage_from_atom(
                atom,
                remaining_calls=remaining_calls,
                state=state,
                now_dt=now_dt,
                used_llm=use_llm is not None,
            )
        s = p
        if remaining_calls <= 0 and mode_llm:
            # Continue cascade with template only.
            llm = None

    return {
        "hour_start": to_iso_z(h_start),
        "refreshed": refreshed,
        "llm_calls_left": remaining_calls,
        "stopped_reason": stopped_reason,
        "elapsed_ms": (time.monotonic() - start_mono) * 1000.0,
        "complete": stopped_reason is None,
    }


def process_closed_hours(
    store: MemoryStore,
    now: datetime | str | None = None,
    *,
    settings: MemorySettings | None = None,
    llm: SummaryLlm | None = None,
    identity_names: Mapping[str, str] | None = None,
    state: MutableMapping[str, Any] | None = None,
    max_ms: int | None = None,
) -> dict[str, Any]:
    """Close due 1h windows oldest-first, cascade parents, respect budgets.

    ``catchup_cursor`` means the next 1h ``window_start`` to consider
    (inclusive). On full-slice completion it advances past the last examined
    closed hour (including empties). Incomplete cascade leaves the hour in
    ``cascade_pending_1h`` so the next tick re-cascades without re-skipping.
    """
    settings = settings or MemorySettings()
    now_dt = parse_iso_z(now) if now is not None else datetime.now(UTC)
    owned_state = state is None
    if state is None:
        state = load_ladder_state(store)

    budget = float(
        max_ms
        if max_ms is not None
        else getattr(settings, "ladder_hourly_max_ms", 12000) or 12000
    )
    catchup_max = int(getattr(settings, "ladder_catchup_max_hours", 24) or 24)
    max_calls_tick = int(getattr(settings, "ladder_llm_max_calls_per_tick", 3) or 3)
    skip_empty = bool(getattr(settings, "ladder_skip_empty", True))
    mode = str(getattr(settings, "summary_mode", "template") or "template").lower()

    t0 = time.monotonic()
    hour_remaining = _llm_calls_remaining(state, now_dt, settings=settings)
    remaining_calls = min(max_calls_tick, hour_remaining) if mode == "llm" else 0

    # Current open hour start — catch-up is done when cursor reaches this.
    current_hour_start, _ = window_bounds("1h", now_dt)

    # Candidate closed hours: horizon catchup + dirty + cascade_pending.
    candidates = windows_in_horizon("1h", now_dt, n_windows=max(catchup_max + 1, 2))
    closed: list[datetime] = []
    for w_s, w_e in candidates:
        if w_e <= now_dt:
            closed.append(w_s)

    dirty_raw = state.get("dirty_1h_windows") or []
    dirty_set: set[str] = set()
    if isinstance(dirty_raw, list):
        for d in dirty_raw:
            if isinstance(d, str) and d:
                dirty_set.add(d)
                try:
                    closed.append(parse_iso_z(d))
                except (TypeError, ValueError):
                    pass

    pending_raw = state.get("cascade_pending_1h") or []
    cascade_pending: set[str] = set()
    if isinstance(pending_raw, list):
        for d in pending_raw:
            if isinstance(d, str) and d:
                cascade_pending.add(d)
                try:
                    closed.append(parse_iso_z(d))
                except (TypeError, ValueError):
                    pass

    # Catchup cursor: only process hours at/after cursor when set.
    cursor_raw = state.get("catchup_cursor")
    cursor_dt: datetime | None = None
    if isinstance(cursor_raw, str) and cursor_raw:
        try:
            cursor_dt = parse_iso_z(cursor_raw)
        except (TypeError, ValueError):
            cursor_dt = None

    # Unique, oldest-first.
    uniq: dict[str, datetime] = {}
    for c in closed:
        uniq[to_iso_z(c)] = c
    ordered = sorted(uniq.values(), key=lambda d: d)
    if cursor_dt is not None:
        # Always include cascade_pending / dirty even if before cursor so we
        # can resume incomplete work after a budget stop.
        forced = dirty_set | cascade_pending
        ordered = [
            d for d in ordered if d >= cursor_dt or to_iso_z(d) in forced
        ]

    processed: list[str] = []
    cascaded: list[str] = []
    examined: list[datetime] = []
    stopped_reason: str | None = None
    budget_break_hour: datetime | None = None

    for hour_start in ordered[:catchup_max]:
        elapsed = (time.monotonic() - t0) * 1000.0
        if elapsed >= budget:
            stopped_reason = "budget"
            budget_break_hour = hour_start
            break

        w_start, w_end = window_bounds("1h", hour_start)
        # Skip open hour (not closed).
        if w_end > now_dt:
            continue

        examined.append(w_start)
        key = to_iso_z(w_start)
        sources, _, _ = collect_window_sources(
            store, "1h", w_start, w_end, prefer_children=False
        )
        tip_id = stable_summary_id("1h", w_start)
        existing = store.get_atom(tip_id)
        is_dirty = key in dirty_set
        is_cascade_pending = key in cascade_pending
        tip_empty = existing is not None and not (existing.content_text or "").strip()
        needs_1h = existing is None or is_dirty or tip_empty

        # Empty tip with no sources: keep dirty for retry; do not clear (Issue 8).
        if tip_empty and not sources:
            if not is_dirty:
                dirty_set.add(key)
            continue

        # No tip, no sources, skip_empty → resolved empty; advance past.
        if skip_empty and not sources and existing is None and not is_cascade_pending:
            dirty_set.discard(key)
            cascade_pending.discard(key)
            continue

        # Tip exists, clean, cascade done → nothing to do; still examined.
        if not needs_1h and not is_cascade_pending:
            dirty_set.discard(key)
            continue

        did_1h = False
        if needs_1h:
            use_llm = llm if (mode == "llm" and remaining_calls > 0) else None
            max_passes = min(2, remaining_calls) if use_llm is not None else 1
            try:
                atom = refresh_window(
                    store,
                    "1h",
                    w_start,
                    prefer_children=False,
                    skip_empty=skip_empty,
                    settings=settings,
                    llm=use_llm,
                    identity_names=identity_names,
                    llm_max_passes=max_passes,
                )
            except Exception:  # noqa: BLE001
                _LOG.exception("1h refresh failed window=%s", to_iso_z(w_start))
                # Keep dirty so we retry later.
                dirty_set.add(key)
                continue

            if atom is not None:
                processed.append(key)
                did_1h = True
                remaining_calls = _count_llm_usage_from_atom(
                    atom,
                    remaining_calls=remaining_calls,
                    state=state,
                    now_dt=now_dt,
                    used_llm=use_llm is not None,
                )
            elif skip_empty and not sources:
                # refresh returned None (empty); leave dirty if tip_empty else resolve.
                dirty_set.discard(key)
                cascade_pending.discard(key)
                continue
            else:
                # Unexpected None with sources — keep dirty for retry.
                dirty_set.add(key)
                continue

            state["last_closed_1h_processed"] = key

        # Cascade when we just wrote 1h OR cascade was left incomplete.
        if did_1h or is_cascade_pending:
            cas = cascade_from_hour(
                store,
                w_start,
                settings=settings,
                llm=llm if remaining_calls > 0 else None,
                identity_names=identity_names,
                max_ms=budget,
                t0=t0,
                llm_calls_left=remaining_calls,
                state=state,
                now=now_dt,
            )
            remaining_calls = int(cas.get("llm_calls_left") or remaining_calls)
            cascaded.extend(cas.get("refreshed") or [])
            if cas.get("stopped_reason") == "budget":
                # Issue 1: do not treat hour as done — keep cascade pending
                # and cursor on this hour so next tick resumes parents.
                # After a successful 1h put, drop dirty so resume is cascade-only
                # (avoids re-running refresh_window for 1h before cascade).
                stopped_reason = "budget"
                budget_break_hour = w_start
                cascade_pending.add(key)
                dirty_set.discard(key)
                state["catchup_cursor"] = key
                break
            # Cascade finished cleanly.
            cascade_pending.discard(key)
            dirty_set.discard(key)
        else:
            dirty_set.discard(key)
    else:
        # for-else: completed slice without mid-break.
        stopped_reason = stopped_reason or None

    # Cursor advancement (Issues 2–3).
    # - Budget break: already set to the incomplete hour above.
    # - Full completion: advance past last *examined* closed hour (incl. empties),
    #   or to current open hour start when the slice is fully resolved.
    if budget_break_hour is not None:
        state["catchup_cursor"] = to_iso_z(budget_break_hour)
    elif examined:
        next_cursor = examined[-1] + timedelta(hours=1)
        # Cap at current open hour — no point parking beyond live frontier.
        if next_cursor > current_hour_start:
            next_cursor = current_hour_start
        state["catchup_cursor"] = to_iso_z(next_cursor)
    else:
        # No closed hours in range (or all filtered): catch-up complete for now.
        state["catchup_cursor"] = to_iso_z(current_hour_start)

    state["dirty_1h_windows"] = sorted(dirty_set)
    state["cascade_pending_1h"] = sorted(cascade_pending)
    state["last_hourly_process"] = to_iso_z(now_dt)

    if owned_state:
        save_ladder_state(state, store)

    return {
        "processed_1h": processed,
        "cascaded": cascaded,
        "elapsed_ms": (time.monotonic() - t0) * 1000.0,
        "stopped_reason": stopped_reason,
        "llm_calls_left": remaining_calls,
        "examined_1h": [to_iso_z(e) for e in examined],
    }


def _hourly_due(state: Mapping[str, Any], now: datetime) -> bool:
    """True when we should run the hourly process path this tick.

    Catch-up is behind only when ``catchup_cursor`` (next hour start to
    consider) is strictly before the current open hour start, or when dirty /
    cascade_pending work remains.
    """
    dirty = state.get("dirty_1h_windows") or []
    if isinstance(dirty, list) and dirty:
        return True
    pending = state.get("cascade_pending_1h") or []
    if isinstance(pending, list) and pending:
        return True
    last = state.get("last_hourly_process")
    if not last:
        return True
    try:
        last_dt = parse_iso_z(last)
    except (TypeError, ValueError):
        return True
    # Crossed an hour boundary since last process.
    last_hour = last_dt.replace(minute=0, second=0, microsecond=0)
    now_hour = now.replace(minute=0, second=0, microsecond=0)
    if now_hour > last_hour:
        return True
    # Catch-up still behind: cursor is next hour start to consider.
    cursor = state.get("catchup_cursor")
    if isinstance(cursor, str) and cursor:
        try:
            c = parse_iso_z(cursor)
            # current_hour_start = start of the open hour containing now.
            current_hour_start, _ = window_bounds("1h", now)
            if c < current_hour_start:
                return True
        except (TypeError, ValueError):
            pass
    return False


def tick(
    store: MemoryStore,
    now: datetime | str | None = None,
    *,
    settings: MemorySettings | None = None,
    llm: SummaryLlm | None = None,
    identity_names: Mapping[str, str] | None = None,
    max_ms: int | None = None,
    state: MutableMapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Idle entry: hourly process when due, else nibble over write scales."""
    settings = settings or MemorySettings()
    now_dt = parse_iso_z(now) if now is not None else datetime.now(UTC)
    owned_state = state is None
    if state is None:
        state = load_ladder_state(store)

    if not bool(getattr(settings, "ladder_enabled", True)):
        return {"path": "disabled", "elapsed_ms": 0.0}

    if _hourly_due(state, now_dt):
        hourly_ms = (
            max_ms
            if max_ms is not None
            else int(getattr(settings, "ladder_hourly_max_ms", 12000) or 12000)
        )
        result = process_closed_hours(
            store,
            now_dt,
            settings=settings,
            llm=llm,
            identity_names=identity_names,
            state=state,
            max_ms=hourly_ms,
        )
        result["path"] = "hourly"
        if owned_state:
            save_ladder_state(state, store)
        return result

    nibble_ms = (
        max_ms
        if max_ms is not None
        else int(getattr(settings, "ladder_max_ms_per_tick", 200) or 200)
    )
    # Nibble: template-first; pass llm only when summary_mode=llm.
    use_llm = (
        llm
        if str(getattr(settings, "summary_mode", "template")).lower() == "llm"
        else None
    )
    # Age-gate write scales; keep legacy only when repair flag is on.
    write_legacy = bool(getattr(settings, "ladder_write_legacy_scales", False))
    if write_legacy:
        nibble_scales: list[str] = list(PERIOD_SCALE_ORDER)
    else:
        tips = count_tip_summaries(store)
        created = read_instance_created_at(store)
        allowed = set(allowed_scales(created, now_dt, tip_counts=tips))
        nibble_scales = [s for s in PERIOD_SCALE_ORDER_WRITE if s in allowed]
        if not nibble_scales:
            nibble_scales = ["1h", "1d"]
    result = refresh_due(
        store,
        now_dt,
        max_ms=nibble_ms,
        scales=nibble_scales,
        state=state,
        settings=settings,
        llm=use_llm,
        identity_names=identity_names,
    )
    result["path"] = "nibble"
    if owned_state:
        save_ladder_state(state, store)
    return result


__all__ = [
    "LADDER_ENOUGH_1D_TIPS",
    "LADDER_ENOUGH_1M_TIPS",
    "LADDER_ENOUGH_1W_TIPS",
    "allowed_scales",
    "build_source_pack",
    "build_summary_atom",
    "cascade_from_hour",
    "child_content_hash",
    "collect_window_sources",
    "count_tip_summaries",
    "gap_spans",
    "ladder_status_snapshot",
    "load_ladder_state",
    "mark_dirty_1h",
    "max_highlights",
    "moment_blocks_for_window",
    "process_closed_hours",
    "read_instance_created_at",
    "refresh_due",
    "refresh_window",
    "render_template_summary",
    "resolve_tip",
    "save_ladder_state",
    "scale_allowed_for_instance_age",
    "select_highlights",
    "tick",
    "uses_versioned_ids",
]
