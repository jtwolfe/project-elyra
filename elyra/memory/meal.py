"""Labeled memory meal composition, episodic policy, and media expand parity.

Scope: pure package assembly over MemoryStore (mock-friendly). Phase 1
deterministic episodic selection (KD17); Phase 2 supporting semantic channel
(KD1/KD10/KD11/KD20); Phase 2a directed_keep (KD-A7/A8/A16); glass-tail
band (S1 / #93); slide-off never deletes store atoms.
In scope: MealItem/MealPackage, select_episodic, select_semantic,
select_directed_keep, select_glass_tail, slide-off, compose_meal,
compose_outer_messages, expand_memory_meal_for_provider.
Out of scope: promote, presence/loop drop-in (rebuild_outer lives in worker).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

_LOG = logging.getLogger(__name__)

from elyra.memory.config import MemorySettings, is_directed_keep_enabled
from elyra.memory.store import MemoryStore
from elyra.memory.tokens import (
    DEFAULT_MEAL_BUDGET_TOKENS,
    EPISODIC_SUMMARY_SHARE,
    estimate_tokens,
    split_memory_budget_v4,
)
from elyra.memory.types import (
    Atom,
    PeriodScale,
    parse_iso_z,
    to_iso_z,
    window_bounds,
)

# Coarse → fine write-era pack order (design §7 / KD17 tip-only meal).
# Omit 15m/6h unless no 1h tip exists and legacy atoms present (soft fallback).
_SUMMARY_PACK_ORDER: tuple[PeriodScale, ...] = (
    "1y",
    "1m",
    "1w",
    "1d",
    "1h",
)

# Legacy soft-fallback scales (only when no write-era 1h tip packed).
_LEGACY_SUMMARY_FALLBACK_ORDER: tuple[PeriodScale, ...] = ("6h", "15m")

# Coarser tip drop order under pressure after 1h band (fine→coarse among
# remaining; 1d tip protected until last resort — design §7).
_COARSER_SUMMARY_DROP_ORDER: tuple[PeriodScale, ...] = ("1w", "1m", "1y")

_DEFAULT_RECENT_1H_MEAL = 6

_RAW_EXCLUDE_KINDS = frozenset({"summary", "parcel", "moment_meta"})
_NON_SUMMARY_KINDS = (
    "observation",
    "speak",
    "tool",
    "model",
    "ledger",
)

EPISODIC_MAX_PRIOR_MOMENTS = 18
_RAW_RANGE_LIMIT = 500
_COMPACT_LINE_CHARS = 80
_COMPACT_HEADER_LABEL = "temporal/compact"

# Semantic query seed (design select_semantic step 1).
_SEMANTIC_SEED_KINDS = frozenset({"observation", "speak", "model"})
_SEMANTIC_SEED_MAX_CHARS = 2000

# semantic_omitted_reason values (observability).
SEMANTIC_OMIT_ENCODER = "encoder"
SEMANTIC_OMIT_TIMEOUT = "timeout"
SEMANTIC_OMIT_EMPTY_SEED = "empty_seed"
SEMANTIC_OMIT_NO_INDEX = "no_index"
SEMANTIC_OMIT_MIN_SCORE = "min_score"
SEMANTIC_OMIT_NO_HITS = "no_hits"
SEMANTIC_OMIT_DEDUPED = "deduped"

# directed_keep omit reasons (Phase 2a / PR-A3).
DIRECTED_KEEP_OMIT_DISABLED = "disabled"
DIRECTED_KEEP_OMIT_EMPTY = "empty"
DIRECTED_KEEP_OMIT_DEDUPED = "deduped"
DIRECTED_KEEP_OMIT_BUDGET = "budget"

# Glass-tail band (S1 / #93 instance continuity).
GLASS_TAIL_CHANNEL = "glass_tail"
GLASS_TAIL_LABEL = "glass-tail"
# Glass-tail social bit (KD19): aligned with continuous_policy.SOCIAL_WAKE_KINDS.
# wait_timeout is **non-social** for glass_tail scope — do not re-add it here.
# rebuild_outer imports continuous_policy as the authoritative source of truth;
# this export exists for callers that still import from meal and must match.
SOCIAL_WAKE_KINDS = frozenset({"user_message", "wait_reply"})


@dataclass(frozen=True)
class MealItem:
    """One labeled row or section fragment in the meal package."""

    atom_id: str | None  # None for ephemeral compact / multi-atom blocks
    channel: str  # temporal | episodic | semantic | directed_keep | glass_tail | orient | system | chain
    label: str  # e.g. "temporal/moment", "episodic/summary 1h", "semantic"
    role: str  # user | assistant | system
    content: str
    token_estimate: int
    t_start: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "meta", dict(self.meta))


@dataclass(frozen=True)
class MealPackage:
    """Composed outer meal (memory channels only + stats)."""

    items: tuple[MealItem, ...]
    total_tokens: int
    slid_off_count: int
    compact_text: str | None  # in-meal only glue for slid-off span
    channels_present: tuple[str, ...]
    open_moment_id: str | None
    semantic_omitted_reason: str | None = None
    # PR-R2: channel + hit counters from select_semantic (additive).
    semantic_select_meta: dict[str, Any] | None = None
    # PR-A3: directed_keep omit reason + pack meta (additive).
    directed_keep_omitted_reason: str | None = None
    directed_keep_meta: dict[str, Any] | None = None
    # S1: glass-tail pack meta (packed count, floor, tokens).
    glass_tail_meta: dict[str, Any] | None = None


def moment_id_short(moment_id: str | None) -> str:
    """Short id for labels (first 8 chars of payload after optional prefix)."""
    if not moment_id:
        return "?"
    raw = moment_id
    if "_" in raw and not raw.startswith("m_"):
        # keep as-is for short ids
        pass
    # Strip common prefixes for display; keep at least 8 of the rest.
    for prefix in ("moment_", "m_"):
        if raw.startswith(prefix) and len(raw) > len(prefix) + 4:
            raw = raw[len(prefix) :]
            break
    return raw[:8] if len(raw) > 8 else raw


def format_atom_line(atom: Atom) -> str:
    """Render one atom as a dialogue-ish line for a host block."""
    hhmm = _hhmm(atom.t_start)
    body = atom.content_text or ""
    return f"[{hhmm}] ({atom.kind}) {body}"


def _hhmm(t_start: str | None) -> str:
    if not t_start:
        return "??:??"
    try:
        dt = parse_iso_z(t_start)
        return dt.strftime("%H:%M")
    except (TypeError, ValueError):
        # Best-effort slice of ISO-ish strings.
        if "T" in t_start and len(t_start) >= 16:
            return t_start[11:16]
        return "??:??"


def _label_header(label: str) -> str:
    return f"[context:{label}]"


def _item_from_parts(
    *,
    atom_id: str | None,
    channel: str,
    label: str,
    content: str,
    t_start: str | None = None,
    meta: dict[str, Any] | None = None,
    role: str = "user",
) -> MealItem:
    # Include label header in token estimate (matches rendered message).
    full = f"{_label_header(label)}\n{content}" if content else _label_header(label)
    return MealItem(
        atom_id=atom_id,
        channel=channel,
        label=label,
        role=role,
        content=content,
        token_estimate=estimate_tokens(full),
        t_start=t_start,
        meta=meta or {},
    )


def _atom_tokens(atom: Atom, *, label: str | None = None) -> int:
    line = format_atom_line(atom)
    if label:
        return estimate_tokens(f"{_label_header(label)}\n{line}")
    return estimate_tokens(line)


# ---------------------------------------------------------------------------
# Episodic selection (Phase 1 — deterministic policy; KD17)
# ---------------------------------------------------------------------------


def _load_window_summary(
    store: MemoryStore,
    scale: PeriodScale,
    window_start: datetime,
    window_end: datetime,
) -> Atom | None:
    """Return the tip summary atom for exactly this window if present.

    ``list_summaries`` walks the ladder index (one tip per scale+window);
    never walks version archives. When multiple tips share a window key the
    store index already points at the latest put.
    """
    hits = store.list_summaries(
        scale,
        overlapping=(window_start, window_end),
        limit=20,
    )
    target_ws = to_iso_z(window_start)
    for atom in hits:
        if atom.window_start and to_iso_z(atom.window_start) == target_ws:
            return atom
    return None


def _recent_1h_count(cfg: MemorySettings) -> int:
    """Meal recent-1h band size (design: ladder_recent_1h_meal / episodic_recent_1h_count)."""
    raw = getattr(cfg, "ladder_recent_1h_meal", None)
    if raw is None:
        raw = getattr(cfg, "episodic_recent_1h_count", None)
    try:
        n = int(raw) if raw is not None else _DEFAULT_RECENT_1H_MEAL
    except (TypeError, ValueError):
        n = _DEFAULT_RECENT_1H_MEAL
    return max(0, n)


def _load_1h_recent_band(
    store: MemoryStore,
    now_dt: datetime,
    recent_count: int,
) -> list[Atom]:
    """Load current open-hour tip (if any) + last N closed 1h tips (newest first).

    Pack order prefers open + recent closed so older closed hours lose first
    under the soft summary budget (aligned with under-pressure drop order).
    """
    cur_start, cur_end = window_bounds("1h", now_dt)
    out: list[Atom] = []
    open_tip = _load_window_summary(store, "1h", cur_start, cur_end)
    if open_tip is not None:
        out.append(open_tip)
    for i in range(1, max(0, int(recent_count)) + 1):
        t = cur_start - timedelta(hours=i)
        ws, we = window_bounds("1h", t)
        tip = _load_window_summary(store, "1h", ws, we)
        if tip is not None:
            out.append(tip)
    return out


def _summary_meal_item(atom: Atom) -> MealItem:
    scale = atom.scale or "?"
    label = f"episodic/summary {scale}"
    body = atom.content_text or ""
    meta: dict[str, Any] = {
        "scale": scale,
        "window_start": atom.window_start,
        "window_end": atom.window_end,
        "kind": "summary",
    }
    # Surface version for observability only; meal still packs tip-only.
    ameta = atom.meta or {}
    if "version" in ameta:
        meta["version"] = ameta.get("version")
    return _item_from_parts(
        atom_id=atom.atom_id,
        channel="episodic",
        label=label,
        content=body,
        t_start=atom.t_start or atom.window_start,
        meta=meta,
    )


def _summary_source_ok_for_meal(atom: Atom) -> bool:
    """True when this tip may appear in episodic meal summary packets.

    Reject Phase-1 style ``meta.source == "template"`` so Context never shows
    old inventory highlight sheets after LLM mode / rebuild. Allow ``llm`` and
    ``llm_fallback_template`` (honest fallback after failed generation).
    Missing source (legacy rows) is treated as template and skipped.
    """
    src = (atom.meta or {}).get("source")
    if src is None or src == "" or src == "template":
        return False
    return True


def _try_pack_summary(
    atom: Atom,
    *,
    seen: set[str],
    summary_items: list[MealItem],
    summary_atoms: list[Atom],
    used: int,
    summary_budget: int,
    cap: int,
) -> int:
    """Append one summary atom if budget allows; return updated used tokens.

    Soft summary-budget skips (here) and hard ``_shrink_episodic`` (after raw
    fill) cooperate: pack order prefers open/recent 1h so soft pressure drops
    oldest first; 3c recency drops apply when packed items + raw exceed cap.
    """
    if atom.atom_id in seen:
        return used
    if not _summary_source_ok_for_meal(atom):
        return used
    item = _summary_meal_item(atom)
    if used + item.token_estimate > summary_budget and summary_items:
        return used
    if used + item.token_estimate > cap and summary_items:
        return used
    seen.add(atom.atom_id)
    summary_items.append(item)
    summary_atoms.append(atom)
    return used + item.token_estimate


def _raw_prefer_key(atom: Atom) -> tuple[int, str, str]:
    """Lower is better for fill priority within a moment batch."""
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
    else:
        tier = 6
    return (tier, to_iso_z(atom.t_start), atom.atom_id)


def select_episodic(
    store: MemoryStore,
    now: datetime | str,
    open_moment_id: str | None,
    episodic_cap_tokens: int,
    *,
    settings: MemorySettings | None = None,
    horizon_hours: float | None = None,
    max_prior_moments: int = EPISODIC_MAX_PRIOR_MOMENTS,
) -> list[MealItem]:
    """Deterministic broader-episodic selection (KD17 / design §7).

    1. Summary pass (coarse first, tip-only + recent 1h band) up to
       ``episodic_cap * 0.7``
    2. Raw fill of prior moments in the horizon (exclude open moment)
    3. Under pressure: drop tool/model → excess speak/obs → oldest 1h then
       coarser tips (1d last-resort among coarses)
    """
    cfg = settings or MemorySettings()
    now_dt = parse_iso_z(now)
    cap = max(0, int(episodic_cap_tokens))
    if cap <= 0:
        return []

    h_hours = (
        float(horizon_hours)
        if horizon_hours is not None
        else float(cfg.episodic_horizon_hours)
    )
    horizon_start = now_dt - timedelta(hours=h_hours)
    recent_1h = _recent_1h_count(cfg)

    seen: set[str] = set()
    summary_items: list[MealItem] = []
    summary_atoms: list[Atom] = []
    summary_budget = int(cap * EPISODIC_SUMMARY_SHARE)
    used = 0
    # Gate legacy fallback on *existence* of a write-era 1h tip in the recent
    # band (store/list), not on whether soft budget admitted one.
    band_1h_candidates: list[Atom] = []

    # --- 1. SUMMARY PASS (write-era coarse → fine; tip-only + recent 1h) ---
    for scale in _SUMMARY_PACK_ORDER:
        if scale == "1h":
            candidates = _load_1h_recent_band(store, now_dt, recent_1h)
            band_1h_candidates = candidates
        else:
            # Coarser ≥1d: current open window tip only (not previous window).
            cur_start, cur_end = window_bounds(scale, now_dt)
            tip = _load_window_summary(store, scale, cur_start, cur_end)
            candidates = [tip] if tip is not None else []
        for atom in candidates:
            used = _try_pack_summary(
                atom,
                seen=seen,
                summary_items=summary_items,
                summary_atoms=summary_atoms,
                used=used,
                summary_budget=summary_budget,
                cap=cap,
            )

    # Soft fallback: legacy 15m/6h only when no write-era 1h tip exists.
    if not band_1h_candidates:
        for scale in _LEGACY_SUMMARY_FALLBACK_ORDER:
            cur_start, cur_end = window_bounds(scale, now_dt)
            tip = _load_window_summary(store, scale, cur_start, cur_end)
            if tip is None:
                continue
            used = _try_pack_summary(
                tip,
                seen=seen,
                summary_items=summary_items,
                summary_atoms=summary_atoms,
                used=used,
                summary_budget=summary_budget,
                cap=cap,
            )

    # --- 2. RAW FILL ---
    raw_atoms = store.list_range(
        horizon_start,
        now_dt,
        kinds=_NON_SUMMARY_KINDS,  # type: ignore[arg-type]
        exclude_moment_id=open_moment_id,
        limit=_RAW_RANGE_LIMIT,
    )
    # Group by moment; track latest t_start per moment for recency.
    by_moment: dict[str, list[Atom]] = {}
    moment_latest: dict[str, str] = {}
    for atom in raw_atoms:
        if atom.kind in _RAW_EXCLUDE_KINDS:
            continue
        mid = atom.moment_id or ""
        if not mid:
            continue
        if open_moment_id and mid == open_moment_id:
            continue
        if atom.atom_id in seen:
            continue
        by_moment.setdefault(mid, []).append(atom)
        ts = to_iso_z(atom.t_start)
        if mid not in moment_latest or ts > moment_latest[mid]:
            moment_latest[mid] = ts

    # Most recent moments first (by latest atom time).
    ordered_moments = sorted(
        by_moment.keys(),
        key=lambda m: moment_latest[m],
        reverse=True,
    )[: max(0, int(max_prior_moments))]

    raw_items: list[MealItem] = []
    raw_atom_list: list[Atom] = []  # parallel tracking for shrink
    for mid in ordered_moments:
        atoms = sorted(
            by_moment[mid],
            key=lambda a: (to_iso_z(a.t_start), a.atom_id),
        )
        # Prefer high-value kinds when packing under pressure, but keep
        # chronological render order inside the moment block.
        preferred = sorted(atoms, key=_raw_prefer_key)
        selected: list[Atom] = []
        # Greedy add preferred until we can't; then re-sort chrono for render.
        trial_used = used
        for atom in preferred:
            if atom.atom_id in seen:
                continue
            label = f"episodic/prior-moment {moment_id_short(mid)}"
            cost = _atom_tokens(atom, label=label if not selected else None)
            # First atom in block pays label; subsequent pay line only.
            if selected:
                cost = estimate_tokens(format_atom_line(atom) + "\n")
            if trial_used + cost > cap:
                continue
            selected.append(atom)
            trial_used += cost
        if not selected:
            continue
        selected.sort(key=lambda a: (to_iso_z(a.t_start), a.atom_id))
        item = _rebuild_prior_moment_item(mid, selected)
        if item is None:
            continue
        if used + item.token_estimate > cap and used > 0:
            # Pack atom-by-atom chronologically until cap.
            partial: list[Atom] = []
            partial_used = used
            label = f"episodic/prior-moment {moment_id_short(mid)}"
            for atom in selected:
                line = format_atom_line(atom)
                if not partial:
                    cost = estimate_tokens(f"{_label_header(label)}\n{line}")
                else:
                    cost = estimate_tokens(line + "\n")
                if partial_used + cost > cap:
                    break
                partial.append(atom)
                partial_used += cost
            if not partial:
                continue
            item = _rebuild_prior_moment_item(mid, partial)
            if item is None:
                continue
            selected = partial
        for a in selected:
            seen.add(a.atom_id)
            raw_atom_list.append(a)
        raw_items.append(item)
        used += item.token_estimate

    items = summary_items + raw_items
    used = sum(i.token_estimate for i in items)

    # --- 3. UNDER PRESSURE shrink ---
    if used > cap:
        items = _shrink_episodic(
            items,
            summary_atoms=summary_atoms,
            cap=cap,
            now=now_dt,
        )

    return items


def _summary_window_start_key(item: MealItem) -> str:
    """ISO window_start for ordering 1h band drops (ascending = oldest first)."""
    meta = item.meta or {}
    ws = meta.get("window_start")
    if ws:
        try:
            return to_iso_z(ws)
        except (TypeError, ValueError):
            return str(ws)
    if item.t_start:
        try:
            return to_iso_z(item.t_start)
        except (TypeError, ValueError):
            return str(item.t_start)
    return ""


def _shrink_episodic(
    items: list[MealItem],
    *,
    summary_atoms: Sequence[Atom],
    cap: int,
    now: datetime | None = None,
) -> list[MealItem]:
    """Drop order 3a → 3b → recency-aware summary drops until under cap.

    Summary pressure (design §7 PR-D):
      1. Drop oldest closed 1h one at a time (keep ≥1 closed while under cap)
      2. Drop last closed 1h if still over
      3. Drop open-hour 1h tip
      4. Drop coarser fine→coarse (1w→1m→1y; legacy 15m/6h early); protect 1d
      5. Last resort: drop 1d tip (never pull version archives under pressure)
    """
    items = list(items)
    now_dt = parse_iso_z(now) if now is not None else None
    open_1h_start = (
        to_iso_z(window_bounds("1h", now_dt)[0]) if now_dt is not None else None
    )

    def total(seq: Sequence[MealItem] | None = None) -> int:
        return sum(i.token_estimate for i in (seq if seq is not None else items))

    scale_by_id = {
        a.atom_id: a.scale for a in summary_atoms if a.scale is not None
    }

    def summary_scale(item: MealItem) -> str | None:
        if not item.label.startswith("episodic/summary"):
            return None
        return (item.meta or {}).get("scale") or scale_by_id.get(
            item.atom_id or ""
        )

    def is_1h(item: MealItem) -> bool:
        return summary_scale(item) == "1h"

    def is_open_1h(item: MealItem) -> bool:
        if not is_1h(item):
            return False
        if open_1h_start is None:
            # Without now, treat newest 1h as open (conservative).
            return False
        return _summary_window_start_key(item) == open_1h_start

    def is_closed_1h(item: MealItem) -> bool:
        return is_1h(item) and not is_open_1h(item)

    def drop_item(target: MealItem) -> None:
        nonlocal items
        items = [i for i in items if i is not target]

    def drop_scale(scale: str) -> None:
        nonlocal items
        items = [i for i in items if summary_scale(i) != scale]

    # 3a: drop raw tool/model atoms (oldest first)
    items = _drop_raw_kinds(
        items,
        kinds_pred=lambda k, _meta: k in ("tool", "model"),
        cap=cap,
    )
    if total() <= cap:
        return items

    # 3b: drop raw speak/observation beyond last 3 per prior moment (KD-V8)
    items = _trim_speak_obs_per_moment(items, keep_last=3, cap=cap)
    if total() <= cap:
        return items

    # 3c: recency-aware 1h band — drop oldest closed one at a time, keep ≥1 closed
    # until forced to zero.
    while total() > cap:
        closed = [i for i in items if is_closed_1h(i)]
        if len(closed) <= 1:
            break
        oldest = min(closed, key=_summary_window_start_key)
        drop_item(oldest)

    # Drop last closed 1h if still over.
    if total() > cap:
        closed = [i for i in items if is_closed_1h(i)]
        if closed:
            oldest = min(closed, key=_summary_window_start_key)
            drop_item(oldest)

    # Drop open-hour 1h tip if still over.
    if total() > cap:
        for item in list(items):
            if is_open_1h(item) or (
                open_1h_start is None and is_1h(item)
            ):
                drop_item(item)
                if total() <= cap:
                    break
        # If now was unknown, open_1h was false for all; drop remaining 1h here.
        if total() > cap and open_1h_start is None:
            remaining_1h = [i for i in items if is_1h(i)]
            for item in sorted(remaining_1h, key=_summary_window_start_key):
                drop_item(item)
                if total() <= cap:
                    break

    # Drop legacy soft-fallback tips before write-era coarser tips.
    if total() > cap:
        for scale in ("15m", "6h"):
            if total() <= cap:
                break
            drop_scale(scale)

    # Coarser write-era tips: 1w then 1m then 1y (1d protected until last resort).
    if total() > cap:
        for scale in _COARSER_SUMMARY_DROP_ORDER:
            if total() <= cap:
                break
            drop_scale(scale)

    # Last resort among coarses: 1d tip.
    if total() > cap:
        drop_scale("1d")

    # Any leftover summaries (e.g. unexpected scales) then prior-moment blocks.
    if total() > cap:
        for scale in ("1h", "1y", "1m", "1w", "1d", "6h", "15m"):
            if total() <= cap:
                break
            drop_scale(scale)

    while total() > cap and items:
        raw_idxs = [
            idx
            for idx, i in enumerate(items)
            if i.label.startswith("episodic/prior-moment")
        ]
        if raw_idxs:
            oldest = min(raw_idxs, key=lambda idx: items[idx].t_start or "")
            del items[oldest]
            continue
        sum_idxs = [
            idx
            for idx, i in enumerate(items)
            if i.label.startswith("episodic/summary")
        ]
        if not sum_idxs:
            break
        # Drop oldest summary leftover by window_start.
        drop_idx = min(
            sum_idxs, key=lambda idx: _summary_window_start_key(items[idx])
        )
        del items[drop_idx]

    return items


def _rebuild_prior_moment_item(
    mid: str,
    atoms: list[Atom],
) -> MealItem | None:
    if not atoms:
        return None
    atoms = sorted(atoms, key=lambda a: (to_iso_z(a.t_start), a.atom_id))
    label = f"episodic/prior-moment {moment_id_short(mid)}"
    body = "\n".join(format_atom_line(a) for a in atoms)
    return _item_from_parts(
        atom_id=None,
        channel="episodic",
        label=label,
        content=body,
        t_start=atoms[0].t_start,
        meta={
            "moment_id": mid,
            "atom_ids": [a.atom_id for a in atoms],
            "kinds": [a.kind for a in atoms],
            "atom_snapshots": [
                {
                    "atom_id": a.atom_id,
                    "kind": a.kind,
                    "t_start": a.t_start,
                    "content_text": a.content_text,
                    "meta": dict(a.meta or {}),
                }
                for a in atoms
            ],
        },
    )


def _atoms_from_prior_item(item: MealItem) -> list[Atom]:
    """Recover atom stubs from a prior-moment MealItem for shrink edits."""
    snaps = (item.meta or {}).get("atom_snapshots")
    if snaps:
        out: list[Atom] = []
        for s in snaps:
            out.append(
                Atom(
                    atom_id=s["atom_id"],
                    t_start=s["t_start"],
                    kind=s["kind"],
                    content_text=s.get("content_text") or "",
                    moment_id=(item.meta or {}).get("moment_id"),
                    meta=dict(s.get("meta") or {}),
                )
            )
        return out
    # Fallback: re-parse lines is lossy; use atom_ids + kinds only.
    mid = (item.meta or {}).get("moment_id")
    ids = (item.meta or {}).get("atom_ids") or []
    kinds = (item.meta or {}).get("kinds") or []
    lines = (item.content or "").split("\n")
    out = []
    for i, aid in enumerate(ids):
        kind = kinds[i] if i < len(kinds) else "observation"
        # Recover content after ") "
        text = ""
        if i < len(lines):
            line = lines[i]
            paren = line.find(") ")
            if paren >= 0:
                text = line[paren + 2 :]
        # Recover t_start from [HH:MM] if present — use synthetic.
        t_start = item.t_start or "1970-01-01T00:00:00Z"
        if i < len(lines) and lines[i].startswith("["):
            hhmm = lines[i][1:6] if len(lines[i]) >= 6 else "00:00"
            # Keep stable ordering via index suffix in t_start seconds.
            t_start = f"1970-01-01T{hhmm}:00Z"
        out.append(
            Atom(
                atom_id=aid,
                t_start=t_start,
                kind=kind,
                content_text=text,
                moment_id=mid,
            )
        )
    return out


def _drop_raw_kinds(
    items: list[MealItem],
    *,
    kinds_pred,
    cap: int,
) -> list[MealItem]:
    """Drop raw atoms matching kinds_pred (oldest first) from prior-moment blocks."""
    # Collect all candidate (item_idx, atom) for tool/model, oldest first.
    work: list[MealItem] = list(items)

    def tot() -> int:
        return sum(i.token_estimate for i in work)

    # Build list of droppable atom refs.
    droppable: list[tuple[int, str]] = []  # (item_idx, atom_id)
    for idx, item in enumerate(work):
        if not item.label.startswith("episodic/prior-moment"):
            continue
        atoms = _atoms_from_prior_item(item)
        for atom in sorted(atoms, key=lambda a: (to_iso_z(a.t_start), a.atom_id)):
            if kinds_pred(atom.kind, atom.meta or {}):
                droppable.append((idx, atom.atom_id))

    for item_idx, atom_id in droppable:
        if tot() <= cap:
            break
        item = work[item_idx]
        atoms = _atoms_from_prior_item(item)
        atoms = [a for a in atoms if a.atom_id != atom_id]
        mid = (item.meta or {}).get("moment_id") or ""
        rebuilt = _rebuild_prior_moment_item(mid, atoms)
        if rebuilt is None:
            work[item_idx] = None  # type: ignore[call-overload]
        else:
            work[item_idx] = rebuilt
    return [i for i in work if i is not None]


def _trim_speak_obs_per_moment(
    items: list[MealItem],
    *,
    keep_last: int,
    cap: int,
) -> list[MealItem]:
    """Drop speak/observation beyond last ``keep_last`` per prior moment."""
    work = list(items)

    def tot() -> int:
        return sum(i.token_estimate for i in work)

    if tot() <= cap:
        return work

    for idx, item in enumerate(list(work)):
        if tot() <= cap:
            break
        if not item.label.startswith("episodic/prior-moment"):
            continue
        atoms = _atoms_from_prior_item(item)
        speak_obs = [
            a
            for a in atoms
            if a.kind in ("speak", "observation")
        ]
        speak_obs.sort(key=lambda a: (to_iso_z(a.t_start), a.atom_id))
        if len(speak_obs) <= keep_last:
            continue
        drop_ids = {a.atom_id for a in speak_obs[:-keep_last]}
        # Drop oldest first among excess until under cap or none left.
        for atom in speak_obs:
            if tot() <= cap:
                break
            if atom.atom_id not in drop_ids:
                continue
            atoms = [a for a in atoms if a.atom_id != atom.atom_id]
            mid = (item.meta or {}).get("moment_id") or ""
            rebuilt = _rebuild_prior_moment_item(mid, atoms)
            if rebuilt is None:
                work[idx] = None  # type: ignore[call-overload]
                break
            work[idx] = rebuilt
            item = rebuilt
            # recompute drop set is fine; continue
    return [i for i in work if i is not None]


# ---------------------------------------------------------------------------
# Temporal slide-off (meal-only; store untouched)
# ---------------------------------------------------------------------------


def _is_protected_temporal(
    atom: Atom,
    *,
    all_atoms: Sequence[Atom],
    protect_tail: int,
) -> bool:
    """Return True if atom must stay while others can still be dropped."""
    if atom.media_ids:
        return True
    meta = atom.meta or {}
    if meta.get("wake_message_id"):
        return True

    # Tail protection: last K by order in all_atoms (already time-ordered).
    if protect_tail > 0 and atom in all_atoms[-protect_tail:]:
        return True
    # Identity via atom_id for safety if duplicates.
    tail_ids = {a.atom_id for a in all_atoms[-protect_tail:]} if protect_tail else set()
    if atom.atom_id in tail_ids:
        return True

    # Latest speak
    speaks = [a for a in all_atoms if a.kind == "speak"]
    if speaks and atom.atom_id == speaks[-1].atom_id:
        return True

    # Latest failed tool
    failed = [
        a
        for a in all_atoms
        if a.kind == "tool" and (a.meta or {}).get("ok") is False
    ]
    if failed and atom.atom_id == failed[-1].atom_id:
        return True

    return False


def build_compact_text(
    slid: Sequence[Atom],
    *,
    max_tokens: int = 400,
) -> str:
    """Meal-only compact template for a slid-off span (not a ladder atom)."""
    if not slid:
        return ""
    t0 = slid[0].t_start or "?"
    t1 = slid[-1].t_start or "?"
    header = (
        f"[{len(slid)} earlier steps in this moment slid from meal | {t0}–{t1}]"
    )
    lines = [header]
    for atom in slid:
        snippet = (atom.content_text or "")[:_COMPACT_LINE_CHARS]
        lines.append(f"- {atom.kind}: {snippet}")
    text = "\n".join(lines)
    # Cap at max_tokens via truncation of lines.
    while estimate_tokens(text) > max_tokens and len(lines) > 1:
        lines.pop()  # drop last highlight
        text = "\n".join(lines)
    if estimate_tokens(text) > max_tokens:
        # Truncate header-only body hard.
        budget_chars = max_tokens * 4
        text = text[:budget_chars]
    return text


def slide_off_temporal(
    atoms: Sequence[Atom],
    temporal_cap_tokens: int,
    *,
    protect_tail_atoms: int = 12,
    compact_max_tokens: int = 400,
    open_moment_id: str | None = None,
) -> tuple[list[Atom], str | None, int]:
    """Slide off oldest unprotected open-moment atoms from the meal only.

    Returns ``(kept_atoms, compact_text_or_none, slid_off_count)``.
    Never mutates the store — caller must not delete atoms.
    """
    ordered = sorted(
        atoms,
        key=lambda a: (to_iso_z(a.t_start), a.atom_id),
    )
    if not ordered:
        return [], None, 0

    mid = open_moment_id or ordered[0].moment_id
    label = f"temporal/moment {moment_id_short(mid)}"

    def package_tokens(kept: Sequence[Atom], compact: str | None) -> int:
        n = 0
        if compact:
            n += estimate_tokens(
                f"{_label_header(_COMPACT_HEADER_LABEL)}\n{compact}"
            )
        if kept:
            body = "\n".join(format_atom_line(a) for a in kept)
            n += estimate_tokens(f"{_label_header(label)}\n{body}")
        return n

    kept = list(ordered)
    slid: list[Atom] = []
    compact: str | None = None
    cap = max(0, int(temporal_cap_tokens))

    if package_tokens(kept, None) <= cap:
        return kept, None, 0

    # Prefer dropping early tool noise first among unprotected, then oldest.
    def drop_score(atom: Atom) -> tuple[int, str, str]:
        # Lower = drop sooner.
        is_tool_noise = 0 if atom.kind in ("tool", "model") else 1
        return (is_tool_noise, to_iso_z(atom.t_start), atom.atom_id)

    while kept and package_tokens(kept, compact) > cap:
        # Protection against original full set (tail / speak / media / wake).
        unprotected = [
            a
            for a in kept
            if not _is_protected_temporal(
                a, all_atoms=ordered, protect_tail=protect_tail_atoms
            )
        ]
        if not unprotected:
            break
        victim = min(unprotected, key=drop_score)
        kept.remove(victim)
        slid.append(victim)
        slid.sort(key=lambda a: (to_iso_z(a.t_start), a.atom_id))
        compact = build_compact_text(slid, max_tokens=compact_max_tokens)

    return kept, compact, len(slid)


def _temporal_items(
    kept: Sequence[Atom],
    compact: str | None,
    open_moment_id: str | None,
) -> list[MealItem]:
    items: list[MealItem] = []
    if compact:
        items.append(
            _item_from_parts(
                atom_id=None,
                channel="temporal",
                label=_COMPACT_HEADER_LABEL,
                content=compact,
                meta={"slid_off": True},
            )
        )
    if kept:
        mid = open_moment_id or kept[0].moment_id
        label = f"temporal/moment {moment_id_short(mid)}"
        body = "\n".join(format_atom_line(a) for a in kept)
        # Attach media / wake meta for compose_outer_messages.
        media_ids: list[str] = []
        wake_message_id = None
        atom_ids = []
        for a in kept:
            atom_ids.append(a.atom_id)
            media_ids.extend(list(a.media_ids or ()))
            if wake_message_id is None and (a.meta or {}).get("wake_message_id"):
                wake_message_id = a.meta.get("wake_message_id")
        items.append(
            _item_from_parts(
                atom_id=None,
                channel="temporal",
                label=label,
                content=body,
                t_start=kept[0].t_start,
                meta={
                    "moment_id": mid,
                    "atom_ids": atom_ids,
                    "media_ids": media_ids,
                    "wake_message_id": wake_message_id,
                    "atom_media": {
                        a.atom_id: list(a.media_ids)
                        for a in kept
                        if a.media_ids
                    },
                    "wake_atom_ids": [
                        a.atom_id
                        for a in kept
                        if (a.meta or {}).get("wake_message_id")
                    ],
                },
            )
        )
    return items


# ---------------------------------------------------------------------------
# Semantic selection (Phase 2 — supporting channel; KD1 / KD2 / KD10–12 / KD20)
# ---------------------------------------------------------------------------


def _now_ms() -> float:
    return time.monotonic() * 1000.0


def _embedder_is_warm(embedder: Any) -> bool:
    """True when embedder is healthy and already loaded (no cold load — KD12)."""
    if embedder is None:
        return False
    try:
        health = embedder.health()
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(health, Mapping) or not health.get("ok"):
        return False
    if hasattr(embedder, "is_loaded") and not bool(getattr(embedder, "is_loaded")):
        return False
    if hasattr(embedder, "loaded") and not bool(getattr(embedder, "loaded")):
        return False
    return True


def _last_glass_user_text(
    glass_rows: Sequence[Mapping[str, Any]] | None = None,
    *,
    glass_items: Sequence[MealItem] | None = None,
) -> str | None:
    """Newest non-empty user glass content (rows preferred; else packed items)."""
    if glass_rows:
        for row in reversed(list(glass_rows)):
            if str(row.get("role") or "") != "user":
                continue
            body = _glass_row_content(row).strip()
            if body:
                return body
    if glass_items:
        for item in reversed(list(glass_items)):
            if item.role != "user":
                continue
            body = (item.content or "").strip()
            if body:
                return body
    return None


def _semantic_seed_source(*, glass_used: bool, open_used: bool) -> str:
    """Classify seed provenance for semantic_select_meta.seed_source."""
    if glass_used and open_used:
        return "mixed"
    if glass_used:
        return "glass_tail"
    if open_used:
        return "open_moment"
    return "empty"


def build_semantic_query_seed(
    open_moment_atoms: Sequence[Atom],
    *,
    max_chars: int = _SEMANTIC_SEED_MAX_CHARS,
    glass_tail_user_text: str | None = None,
    social_wake: bool = False,
) -> str:
    """Prefer glass-tail last user text when social and present; else open-moment.

    Priority concat (within ``max_chars``):

    1. Glass-tail last user text when ``social_wake`` and non-empty text.
    2. Open-moment obs/speak/model (latest-first walk, chronological join).

    Returns query text only; callers that need provenance should use
    :func:`build_semantic_query_seed_with_source`.
    """
    seed, _src = build_semantic_query_seed_with_source(
        open_moment_atoms,
        max_chars=max_chars,
        glass_tail_user_text=glass_tail_user_text,
        social_wake=social_wake,
    )
    return seed


def build_semantic_query_seed_with_source(
    open_moment_atoms: Sequence[Atom],
    *,
    max_chars: int = _SEMANTIC_SEED_MAX_CHARS,
    glass_tail_user_text: str | None = None,
    social_wake: bool = False,
) -> tuple[str, str]:
    """Like :func:`build_semantic_query_seed` plus seed_source tag.

    ``seed_source`` is one of ``glass_tail`` | ``open_moment`` | ``mixed`` | ``empty``.
    """
    limit = max(0, int(max_chars))
    parts: list[str] = []
    total = 0
    glass_used = False
    open_used = False

    # 1. Social tip: glass-tail last user (prefer when present).
    glass = (glass_tail_user_text or "").strip() if social_wake else ""
    if glass and limit > 0:
        piece = glass[:limit]
        if piece:
            parts.append(piece)
            total += len(piece)
            glass_used = True

    # 2. Open-moment obs/speak/model (prefer latest; concat chronological).
    candidates = [
        a
        for a in open_moment_atoms
        if a.kind in _SEMANTIC_SEED_KINDS and (a.content_text or "").strip()
    ]
    candidates.sort(key=lambda a: (to_iso_z(a.t_start), a.atom_id))
    open_chunks: list[str] = []
    for atom in reversed(candidates):
        if total >= limit:
            break
        body = (atom.content_text or "").strip()
        remain = limit - total
        piece = body[:remain]
        if not piece:
            continue
        open_chunks.append(piece)
        total += len(piece)
        open_used = True
    open_chunks.reverse()
    parts.extend(open_chunks)

    seed = "\n".join(parts)
    return seed, _semantic_seed_source(glass_used=glass_used, open_used=open_used)


def _atom_ids_in_meal_items(items: Sequence[MealItem]) -> set[str]:
    """Collect atom_ids referenced by meal items (incl. multi-atom blocks)."""
    ids: set[str] = set()
    for item in items:
        if item.atom_id:
            ids.add(item.atom_id)
        meta = item.meta or {}
        for aid in meta.get("atom_ids") or []:
            if aid:
                ids.add(str(aid))
    return ids


def _map_parcel_to_parent(
    store: MemoryStore,
    hit_atom: Atom,
) -> tuple[Atom | None, bool]:
    """Map parcel hit → parent atom; return (atom, via_parcel)."""
    if hit_atom.kind == "parcel" and hit_atom.parent_atom_id:
        try:
            parent = store.get_atom(hit_atom.parent_atom_id)
        except Exception:  # noqa: BLE001
            parent = None
        if parent is not None:
            return parent, True
        return None, True
    return hit_atom, False


def _semantic_label(*, via_parcel: bool, score: float | None) -> str:
    base = "semantic/parcel→parent" if via_parcel else "semantic"
    if score is None:
        return base
    return f"{base} score={score:.2f}"


def select_semantic(
    store: MemoryStore,
    *,
    index: Any | None,
    embedder: Any | None,
    open_moment_atoms: Sequence[Atom],
    open_moment_id: str | None,
    cap_tokens: int,
    settings: MemorySettings | None = None,
    now: datetime | str | None = None,
    exclude_atom_ids: set[str] | None = None,
    deadline_ms: int | None = None,
    wait_for_completion: bool | None = None,
    wait_max_ms: int | None = None,
    glass_tail_user_text: str | None = None,
    social_wake: bool = False,
) -> tuple[list[MealItem], str | None, dict[str, Any] | None]:
    """Select supporting semantic neighbours under a hard wall-clock budget.

    Returns ``(items, omitted_reason, select_meta)``. On timeout / missing
    encoder / empty seed the channel is omitted (empty items + reason) — never
    blocks unbounded (KD2). Temporal/episodic winners are passed via
    ``exclude_atom_ids`` (KD11). Parcel hits map to parent atoms (label
    ``semantic/parcel→parent``).

    When ``social_wake`` and ``glass_tail_user_text`` are set, the query seed
    prefers the glass tip user text so social hops avoid ``empty_seed`` while
    open-moment promote is still pending (S5 / OQ8). ``select_meta.seed_source``
    reports ``glass_tail`` | ``open_moment`` | ``mixed`` | ``empty``.

    Wait-for-select (CPU dogfood): when ``wait_for_completion`` / settings
    ``semantic_wait_for_select`` is on, use ``semantic_wait_max_ms`` as the
    ceiling, drop the snappy encode sub-budget discard, and keep a finished
    encode when the vector is usable — including when encode alone already
    exceeded the ceiling (search+pack still run). Under wait, mid-pack does
    not hard-timeout an empty pack after a good encode. Fail-fast paths
    (no_index / cold encoder / empty_seed) are unchanged. Encode lag: new
    observations may still miss the vector index until encoded — tip + keep
    carry immediate recall; semantic is support only.

    Empty-pack omit priority (KD-R6): timeout > encoder > no_index >
    empty_seed > min_score > deduped > no_hits. ``select_meta`` carries
    channel / channel_reason / hit counters for MealPackage (PR-R2).
    """
    cfg = settings or MemorySettings()
    t0 = _now_ms()
    cap = max(0, int(cap_tokens))
    wait = (
        bool(wait_for_completion)
        if wait_for_completion is not None
        else bool(getattr(cfg, "semantic_wait_for_select", True))
    )
    if cap <= 0:
        # Semantic share can be zero under temporal floor; still leave a breadcrumb.
        return [], None, {
            "elapsed_ms": 0,
            "packed": 0,
            "cap_tokens": 0,
            "wait": wait,
            "deadline_ms": 0,
            "seed_source": "empty",
        }

    # Absolute ceiling: explicit deadline wins; else wait ceiling or snappy budget.
    if deadline_ms is not None:
        max_ms = int(deadline_ms)
    elif wait:
        from elyra.memory.config import clamp_semantic_wait_max_ms  # noqa: PLC0415

        raw_wait = (
            wait_max_ms
            if wait_max_ms is not None
            else getattr(cfg, "semantic_wait_max_ms", 15_000)
        )
        max_ms = clamp_semantic_wait_max_ms(int(raw_wait))
    else:
        max_ms = int(cfg.semantic_select_max_ms)
    if max_ms < 0:
        max_ms = 0

    def over_deadline() -> bool:
        return (_now_ms() - t0) > max_ms

    def early(
        reason: str | None,
        *,
        meta: dict[str, Any] | None = None,
    ) -> tuple[list[MealItem], str | None, dict[str, Any] | None]:
        base: dict[str, Any] = {
            "elapsed_ms": int(_now_ms() - t0),
            "wait": wait,
            "deadline_ms": max_ms,
        }
        if meta:
            base.update(meta)
        return [], reason, base

    if index is None:
        return early(SEMANTIC_OMIT_NO_INDEX)

    if not _embedder_is_warm(embedder):
        return early(SEMANTIC_OMIT_ENCODER)

    seed, seed_source = build_semantic_query_seed_with_source(
        open_moment_atoms,
        glass_tail_user_text=glass_tail_user_text,
        social_wake=bool(social_wake),
    )
    seed_meta: dict[str, Any] = {"seed_source": seed_source}
    # Light encode-lag awareness: tip seed does not wait for reindex of new atoms.
    if seed_source in ("glass_tail", "mixed"):
        seed_meta["encode_lag_note"] = (
            "new observations may lag vector index; tip+keep carry immediate recall"
        )
    if not seed.strip():
        return early(SEMANTIC_OMIT_EMPTY_SEED, meta=seed_meta)

    if over_deadline():
        return early(SEMANTIC_OMIT_TIMEOUT, meta=seed_meta)

    # Query encode. Wait mode keeps a finished good vector even past max_ms
    # (paid for encode → search+pack). Snappy mode caps with encode_query_max_ms
    # and discards after a slow encode.
    encode_budget = 0
    if not wait:
        encode_budget = min(
            max(0, int(cfg.encode_query_max_ms)),
            max(0, max_ms - int(_now_ms() - t0)),
        )
    t_enc0 = _now_ms()
    try:
        query_vec = embedder.encode_text(seed)
    except Exception:  # noqa: BLE001
        _LOG.exception("semantic query encode failed")
        return early(SEMANTIC_OMIT_ENCODER, meta=seed_meta)
    enc_elapsed = _now_ms() - t_enc0
    if not query_vec:
        return early(SEMANTIC_OMIT_ENCODER, meta=seed_meta)
    if not wait and (enc_elapsed > encode_budget or over_deadline()):
        return early(SEMANTIC_OMIT_TIMEOUT, meta=seed_meta)

    if now is None:
        from elyra.memory.types import utc_now_iso

        now = utc_now_iso()
    now_dt = parse_iso_z(now)
    horizon_start = now_dt - timedelta(hours=float(cfg.semantic_horizon_hours))

    exclude: set[str] = set(exclude_atom_ids or ())
    for a in open_moment_atoms:
        exclude.add(a.atom_id)

    # KD-R16 / KD-R2: pure resolve then search(concrete) — no multi-try.
    # Channel fields only set after resolve succeeds (Issue 4: no false "explicit").
    concrete: str | None = None
    channel_reason: str | None = None
    joint_repair_remaining = 0
    hits: list[Any] = []
    try:
        from elyra.memory.index import resolve_search_channel  # noqa: PLC0415

        health: dict[str, Any] = {}
        try:
            h = index.health() if hasattr(index, "health") else {}
            if isinstance(h, dict):
                health = h
        except Exception:  # noqa: BLE001
            health = {}
        # Design: JSONL / NullEmbeddingIndex → no_index (not empty-search no_hits).
        if str(health.get("backend") or "").lower() == "null":
            return early(
                SEMANTIC_OMIT_NO_INDEX,
                meta={**seed_meta, "backend": "null", "joint_repair_remaining": 0},
            )
        joint_repair_remaining = int(health.get("joint_repair_remaining") or 0)
        channel_req = str(
            getattr(cfg, "semantic_search_channel", None) or "auto"
        ).strip().lower() or "auto"
        concrete, channel_reason = resolve_search_channel(
            channel_req,
            vectors_by_channel=health.get("vectors_by_channel") or {},
            joint_repair_remaining=joint_repair_remaining,
        )
        hits = index.search(
            query_vec,
            k=int(cfg.semantic_top_k),
            channel=concrete,
            t_start=horizon_start,
            t_end=now_dt,
            exclude_atom_ids=exclude,
            exclude_moment_id=open_moment_id,
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("semantic index.search failed")
        fail_meta: dict[str, Any] = {
            **seed_meta,
            "joint_repair_remaining": joint_repair_remaining,
        }
        if concrete is not None:
            fail_meta["channel"] = concrete
        if channel_reason is not None:
            fail_meta["channel_reason"] = channel_reason
        return early(SEMANTIC_OMIT_NO_INDEX, meta=fail_meta)

    channel_meta: dict[str, Any] = {
        **seed_meta,
        "joint_repair_remaining": joint_repair_remaining,
    }
    if concrete is not None:
        channel_meta["channel"] = concrete
    if channel_reason is not None:
        channel_meta["channel_reason"] = channel_reason

    # Snappy: hard timeout before pack if already past deadline.
    # Wait: paid for encode (and search) with a good query_vec — still pack.
    if over_deadline() and not wait:
        return early(SEMANTIC_OMIT_TIMEOUT, meta=channel_meta)

    min_score = float(cfg.semantic_min_score)
    # 0.0 = off (accept all scores).
    apply_min = min_score > 0.0

    raw_hit_count = 0
    below_min = 0
    deduped_count = 0
    packed: list[MealItem] = []
    seen_ids: set[str] = set(exclude)
    used = 0

    for hit in hits or []:
        if over_deadline() and not wait:
            # Partial pack is OK only if we already have items; else timeout omit.
            if not packed:
                return early(SEMANTIC_OMIT_TIMEOUT, meta={
                    **channel_meta,
                    "raw_hits": raw_hit_count,
                    "below_min": below_min,
                    "deduped": deduped_count,
                    "packed": 0,
                })
            break
        raw_hit_count += 1
        score = getattr(hit, "score", None)
        if apply_min and score is not None and float(score) < min_score:
            below_min += 1
            continue

        atom_id = getattr(hit, "atom_id", None)
        atom = getattr(hit, "atom", None)
        if atom is None and atom_id:
            try:
                atom = store.get_atom(str(atom_id))
            except Exception:  # noqa: BLE001
                atom = None
        if atom is None:
            continue

        parent, via_parcel = _map_parcel_to_parent(store, atom)
        if parent is None:
            continue
        if parent.atom_id in seen_ids:
            deduped_count += 1
            continue  # temporal/episodic win (KD11) or already packed
        # Skip parcel-kind if somehow still parcel (parent missing path).
        if parent.kind in _RAW_EXCLUDE_KINDS and parent.kind != "parcel":
            continue
        # Do not include moment_meta / summary as semantic body.
        if parent.kind in ("moment_meta", "summary"):
            continue

        score_f = float(score) if score is not None else None
        label = _semantic_label(via_parcel=via_parcel, score=score_f)
        body = format_atom_line(parent)
        sem_meta: dict[str, Any] = {
            "score": score_f,
            "via_parcel": via_parcel,
            "hit_atom_id": atom.atom_id,
            "kind": parent.kind,
            "moment_id": parent.moment_id,
        }
        # Glass Context media marker (MM #124 PR5) — same meta.media_ids path as temporal.
        if parent.media_ids:
            sem_meta["media_ids"] = list(parent.media_ids)
        item = _item_from_parts(
            atom_id=parent.atom_id,
            channel="semantic",
            label=label,
            content=body,
            t_start=parent.t_start,
            meta=sem_meta,
        )
        if used + item.token_estimate > cap and packed:
            continue
        if used + item.token_estimate > cap and not packed:
            # Single hit larger than cap: skip rather than exceed.
            continue
        packed.append(item)
        seen_ids.add(parent.atom_id)
        used += item.token_estimate

    select_meta: dict[str, Any] = {
        **channel_meta,
        "raw_hits": raw_hit_count,
        "below_min": below_min,
        "deduped": deduped_count,
        "packed": len(packed),
        "elapsed_ms": int(_now_ms() - t0),
        "wait": wait,
        "deadline_ms": max_ms,
    }

    if packed:
        return packed, None, select_meta

    # Empty pack → omit reason priority (after early reasons already returned):
    # min_score > deduped > no_hits
    if apply_min and raw_hit_count > 0 and below_min == raw_hit_count:
        return [], SEMANTIC_OMIT_MIN_SCORE, select_meta
    if deduped_count > 0:
        return [], SEMANTIC_OMIT_DEDUPED, select_meta

    # Product indexes filter exclude_atom_ids inside search, so pack-side
    # dedup never sees those hits. Probe without exclude to distinguish
    # "channel empty" (no_hits) from "only already-in-package" (deduped).
    # Under wait, still probe after a late encode (paid work → honest omit);
    # snappy mode skips the probe when already past the wall-clock budget.
    if (
        raw_hit_count == 0
        and exclude
        and concrete is not None
        and (wait or not over_deadline())
    ):
        probe_deduped = _probe_deduped_against_exclude(
            store,
            index,
            query_vec=query_vec,
            channel=concrete,
            horizon_start=horizon_start,
            now_dt=now_dt,
            exclude=exclude,
            top_k=int(cfg.semantic_top_k),
        )
        if probe_deduped > 0:
            select_meta["deduped"] = probe_deduped
            select_meta["dedupe_probe"] = True
            select_meta["elapsed_ms"] = int(_now_ms() - t0)
            return [], SEMANTIC_OMIT_DEDUPED, select_meta
        select_meta["dedupe_probe"] = True

    select_meta["elapsed_ms"] = int(_now_ms() - t0)
    return [], SEMANTIC_OMIT_NO_HITS, select_meta


def _probe_deduped_against_exclude(
    store: MemoryStore,
    index: Any,
    *,
    query_vec: Sequence[float],
    channel: str,
    horizon_start: datetime,
    now_dt: datetime,
    exclude: set[str],
    top_k: int,
) -> int:
    """Cheap unexcluded probe: count hits whose parent is already in exclude.

    Primary search keeps exclude_* for packing quality. When that returns
    zero hits, this probe (no exclude_atom_ids / exclude_moment_id, still
    horizon-bound) detects "matched but already in temporal/episodic"
    (KD-R6) without inventing index stash.
    """
    probe_k = max(1, min(int(top_k) if top_k > 0 else 1, 3))
    try:
        probe_hits = index.search(
            query_vec,
            k=probe_k,
            channel=channel,
            t_start=horizon_start,
            t_end=now_dt,
            # Intentionally no exclude_atom_ids / exclude_moment_id.
        )
    except Exception:  # noqa: BLE001
        _LOG.debug("semantic dedupe probe search failed", exc_info=True)
        return 0

    counted = 0
    for hit in probe_hits or []:
        atom_id = getattr(hit, "atom_id", None)
        atom = getattr(hit, "atom", None)
        if atom is None and atom_id:
            try:
                atom = store.get_atom(str(atom_id))
            except Exception:  # noqa: BLE001
                atom = None
        if atom is None:
            continue
        parent, _via = _map_parcel_to_parent(store, atom)
        if parent is None:
            continue
        if parent.atom_id in exclude:
            counted += 1
    return counted


# ---------------------------------------------------------------------------
# Directed-keep selection (Phase 2a — confirmed keep-set only)
# ---------------------------------------------------------------------------


def select_directed_keep(
    store: MemoryStore,
    *,
    keep_ids: Sequence[str] | None,
    walk_summary: str | None = None,
    cap_tokens: int,
    settings: MemorySettings | None = None,
    exclude_atom_ids: set[str] | None = None,
    enabled: bool | None = None,
    soft_aged_ids: set[str] | Sequence[str] | None = None,
    entry_ages_s: Mapping[str, float] | None = None,
) -> tuple[list[MealItem], str | None, dict[str, Any] | None]:
    """Pack confirmed keep-set atoms as the ``directed_keep`` meal channel.

    Returns ``(items, omitted_reason, meta)``. Keep order is preserved, but
    soft-aged ids (when provided) are packed **after** young ones so budget
    pressure cuts age-soft entries first. Parcel ids map to parent (KD21).
    Dedupe drops ids already in temporal / episodic / semantic (caller supplies
    ``exclude_atom_ids``). Prepends a single summary item when ``walk_summary``
    is non-empty and fits. No open-moment equality filter (B5b — tray ids).

    Omit reasons: ``disabled`` / ``empty`` / ``deduped`` / ``budget``.
    """
    cfg = settings or MemorySettings()
    channel_on = (
        bool(enabled)
        if enabled is not None
        else is_directed_keep_enabled(cfg)
    )
    cap = max(0, int(cap_tokens))
    ids_raw = [str(i).strip() for i in (keep_ids or ()) if str(i or "").strip()]
    soft_set: set[str] = {str(x) for x in (soft_aged_ids or ()) if x}
    # Prefer young before soft-aged under pressure (age-soft cut first).
    if soft_set:
        young = [i for i in ids_raw if i not in soft_set]
        aged = [i for i in ids_raw if i in soft_set]
        ids = young + aged
    else:
        ids = list(ids_raw)
    exclude: set[str] = set(exclude_atom_ids or ())
    summary = (walk_summary or "").strip()
    ages = dict(entry_ages_s or {})

    def _meta(**extra: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "keep_ids_in": len(ids_raw),
            "packed": 0,
            "deduped": 0,
            "missing": 0,
            "cap_tokens": cap,
            "enabled": channel_on,
            "soft_aged_in": len(soft_set),
            "soft_aged_skipped": 0,
        }
        base.update(extra)
        return base

    if not channel_on:
        return [], DIRECTED_KEEP_OMIT_DISABLED, _meta()

    if not ids:
        return [], DIRECTED_KEEP_OMIT_EMPTY, _meta()

    if cap <= 0:
        return [], DIRECTED_KEEP_OMIT_BUDGET, _meta()

    atom_items: list[MealItem] = []
    used = 0
    deduped = 0
    missing = 0
    skipped_budget = 0
    soft_aged_skipped = 0
    candidates = 0  # loadable non-excluded keeps considered for pack
    seen: set[str] = set(exclude)

    # Reserve summary first when present so atom lines pack under remaining.
    summary_item: MealItem | None = None
    if summary:
        summary_item = _item_from_parts(
            atom_id=None,
            channel="directed_keep",
            label="directed-keep/summary",
            content=summary,
            meta={"kind": "walk_summary"},
        )
        if summary_item.token_estimate <= cap:
            used = summary_item.token_estimate
        else:
            summary_item = None  # cannot afford summary; try atoms alone

    for aid in ids:
        if aid in seen:
            deduped += 1
            continue
        try:
            atom = store.get_atom(aid)
        except Exception:  # noqa: BLE001
            atom = None
        if atom is None:
            missing += 1
            continue

        parent, via_parcel = _map_parcel_to_parent(store, atom)
        if parent is None:
            missing += 1
            continue
        if parent.kind in ("moment_meta", "summary"):
            missing += 1
            continue
        if parent.atom_id in seen:
            deduped += 1
            continue

        candidates += 1
        label = (
            "directed-keep/parcel→parent" if via_parcel else "directed-keep"
        )
        body = format_atom_line(parent)
        is_soft = aid in soft_set or parent.atom_id in soft_set
        item_meta: dict[str, Any] = {
            "via_parcel": via_parcel,
            "hit_atom_id": atom.atom_id,
            "kind": parent.kind,
            "moment_id": parent.moment_id,
            "soft_aged": is_soft,
        }
        if aid in ages:
            item_meta["age_seconds"] = ages[aid]
        elif parent.atom_id in ages:
            item_meta["age_seconds"] = ages[parent.atom_id]
        item = _item_from_parts(
            atom_id=parent.atom_id,
            channel="directed_keep",
            label=label,
            content=body,
            t_start=parent.t_start,
            meta=item_meta,
        )
        if used + item.token_estimate > cap:
            skipped_budget += 1
            if is_soft:
                soft_aged_skipped += 1
            continue
        atom_items.append(item)
        seen.add(parent.atom_id)
        used += item.token_estimate

    meta = _meta(
        packed=len(atom_items),
        deduped=deduped,
        missing=missing,
        skipped_budget=skipped_budget,
        soft_aged_skipped=soft_aged_skipped,
        summary_packed=False,
        tokens_used=used if atom_items or summary_item else 0,
    )

    if atom_items:
        packed: list[MealItem] = []
        if summary_item is not None:
            packed.append(summary_item)
            meta["summary_packed"] = True
        packed.extend(atom_items)
        meta["tokens_used"] = sum(i.token_estimate for i in packed)
        return packed, None, meta

    # No atom bodies packed — honest omit (summary alone is not a channel fill).
    if candidates == 0 and deduped > 0:
        return [], DIRECTED_KEEP_OMIT_DEDUPED, meta
    if candidates == 0:
        return [], DIRECTED_KEEP_OMIT_EMPTY, meta
    return [], DIRECTED_KEEP_OMIT_BUDGET, meta


# ---------------------------------------------------------------------------
# Glass-tail selection (S1 — durable glass tip band with true roles)
# ---------------------------------------------------------------------------


def _glass_row_eligible(row: Mapping[str, Any]) -> bool:
    """KD19: keep user/assistant rows with non-empty content OR attachments."""
    role = row.get("role")
    if role not in ("user", "assistant"):
        return False
    content = row.get("content") or ""
    if not isinstance(content, str):
        content = str(content)
    atts = row.get("attachments")
    has_atts = isinstance(atts, list) and len(atts) > 0
    return bool(content) or has_atts


def _glass_row_content(row: Mapping[str, Any]) -> str:
    content = row.get("content") or ""
    if not isinstance(content, str):
        return str(content)
    return content


def _glass_row_conversation_id(row: Mapping[str, Any]) -> str | None:
    """Normalize row conversation_id: missing / null / blank → None."""
    if "conversation_id" not in row:
        return None
    val = row.get("conversation_id")
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        return s or None
    return None


def _glass_row_matches_conversation(
    row: Mapping[str, Any], conversation_id: str
) -> bool:
    """Strict conversation scope (KD4 / §2.4) — no soft global fill.

    - Explicit matching conversation_id always included.
    - Legacy DM fill only: for ``dm:<uid>``, also rows with missing/null
      conversation_id and ``user_id == uid`` (pre-cutover only).
    - Groups: **never** legacy fill by member user_id.
    """
    row_cid = _glass_row_conversation_id(row)
    if row_cid == conversation_id:
        return True
    if conversation_id.startswith("dm:") and row_cid is None:
        peer = conversation_id[3:]
        if peer and row.get("user_id") == peer:
            return True
    return False


def _glass_row_media_ids(row: Mapping[str, Any]) -> list[str]:
    """Extract attachment content ids from a glass row (best-effort)."""
    atts = row.get("attachments")
    if not isinstance(atts, list):
        return []
    out: list[str] = []
    for att in atts:
        if isinstance(att, Mapping):
            aid = att.get("id")
            if aid is not None and str(aid).strip():
                out.append(str(aid))
        elif isinstance(att, str) and att.strip():
            out.append(att.strip())
    return out


def _glass_tail_labeled_content(
    row: Mapping[str, Any],
    *,
    conversation_id: str | None = None,
    label_users: Mapping[str, str] | None = None,
) -> str:
    """Speaker-labeled glass content (KD6 — single helper for estimate + select).

    - Group: user lines → ``[GoesBy (user_id)] content``
    - DM: short ``[GoesBy] content`` when ``label_users`` provided; else raw
    - Assistant: no user-style prefix; role stays assistant
    """
    content = _glass_row_content(row)
    role = str(row.get("role") or "user")
    if role != "user":
        return content
    uid_raw = row.get("user_id")
    if uid_raw is None:
        return content
    uid = str(uid_raw).strip()
    if not uid:
        return content

    cid = conversation_id.strip() if isinstance(conversation_id, str) else None
    is_group = bool(cid and cid.startswith("group:"))
    is_dm = bool(cid and cid.startswith("dm:"))

    if is_group:
        label = uid
        if label_users is not None:
            mapped = label_users.get(uid)
            if isinstance(mapped, str) and mapped.strip():
                label = mapped.strip()
        return f"[{label} ({uid})] {content}"

    if is_dm and label_users is not None:
        mapped = label_users.get(uid)
        label = mapped.strip() if isinstance(mapped, str) and mapped.strip() else uid
        return f"[{label}] {content}"

    return content


def _glass_tail_item_from_row(
    row: Mapping[str, Any],
    *,
    conversation_id: str | None = None,
    label_users: Mapping[str, str] | None = None,
) -> MealItem:
    """Build one glass-tail MealItem with true role + wake_message_id stamp.

    Labels applied via :func:`_glass_tail_labeled_content` so floor token
    accounting matches packed content (KD6).
    """
    role = str(row.get("role") or "user")
    if role not in ("user", "assistant"):
        role = "user"
    content = _glass_tail_labeled_content(
        row,
        conversation_id=conversation_id,
        label_users=label_users,
    )
    mid = row.get("id")
    mid_s = str(mid) if mid is not None else None
    atts = row.get("attachments")
    has_atts = isinstance(atts, list) and len(atts) > 0
    media_ids = _glass_row_media_ids(row)
    meta: dict[str, Any] = {"source": "glass"}
    if mid_s:
        meta["wake_message_id"] = mid_s
        meta["message_id"] = mid_s
    if has_atts:
        meta["attachments"] = list(atts)
    if media_ids:
        meta["media_ids"] = media_ids
    uid_raw = row.get("user_id")
    if uid_raw is not None and str(uid_raw).strip():
        meta["user_id"] = str(uid_raw).strip()
    if conversation_id:
        meta["conversation_id"] = conversation_id
    # Explicit role — never host default user for glass-tail (KD-ROLE).
    return _item_from_parts(
        atom_id=None,
        channel=GLASS_TAIL_CHANNEL,
        label=GLASS_TAIL_LABEL,
        content=content,
        t_start=row.get("created_at") if isinstance(row.get("created_at"), str) else None,
        meta=meta,
        role=role,
    )


def _eligible_glass_rows(
    glass_rows: Sequence[Mapping[str, Any]],
    *,
    conversation_id: str | None = None,
    exclude_message_ids: set[str] | None = None,
) -> list[Mapping[str, Any]]:
    """Role/content eligible rows, optionally strict-conversation filtered (KD4)."""
    exclude = {str(x) for x in (exclude_message_ids or ()) if x is not None}
    cid = conversation_id.strip() if isinstance(conversation_id, str) else None
    if cid == "":
        cid = None
    out: list[Mapping[str, Any]] = []
    for r in glass_rows:
        if not _glass_row_eligible(r):
            continue
        if r.get("id") is not None and str(r.get("id")) in exclude:
            continue
        if cid is not None and not _glass_row_matches_conversation(r, cid):
            continue
        out.append(r)
    return out


def estimate_glass_tail_floor_tokens(
    glass_rows: Sequence[Mapping[str, Any]],
    *,
    floor_messages: int = 6,
    max_messages: int = 20,
    exclude_message_ids: set[str] | None = None,
    conversation_id: str | None = None,
    label_users: Mapping[str, str] | None = None,
) -> int:
    """Token cost of packing the floor message set (newest eligible rows).

    Uses the same labeled content as :func:`select_glass_tail` (KD6).
    """
    eligible = _eligible_glass_rows(
        glass_rows,
        conversation_id=conversation_id,
        exclude_message_ids=exclude_message_ids,
    )
    if not eligible or floor_messages <= 0:
        return 0
    window = eligible[-max_messages:] if max_messages > 0 else list(eligible)
    floor_n = min(int(floor_messages), len(window))
    floor_rows = window[-floor_n:]
    return sum(
        _glass_tail_item_from_row(
            r, conversation_id=conversation_id, label_users=label_users
        ).token_estimate
        for r in floor_rows
    )


def select_glass_tail(
    glass_rows: Sequence[Mapping[str, Any]],
    *,
    cap_tokens: int,
    floor_messages: int = 6,
    max_messages: int = 20,
    social_wake: bool = False,
    conversation_id: str | None = None,
    exclude_message_ids: set[str] | None = None,
    label_users: Mapping[str, str] | None = None,
) -> tuple[list[MealItem], dict[str, Any]]:
    """Select + pack last-K glass messages into glass_tail MealItems.

    - Filter to role in {user, assistant}; keep non-empty content OR attachments
      (KD19 media-only rule).
    - When ``conversation_id`` is set: **strict** conversation scope (KD4) —
      matching rows + legacy DM fill only; never soft-fill other chats.
    - When ``conversation_id`` is null and ``social_wake`` is false: return empty
      items (KD5 pure-work / continuous — no fake social tip). Callers that
      pre-filter rows for social wakes may omit conversation_id and still pack.
    - Take newest-first window up to ``max_messages``, reverse to chronological
      (oldest → newest) so newest sits just before orient when placed before temporal.
    - Pack under ``cap_tokens``, preferring newest under pressure. When
      ``social_wake`` and rows exist, never drop below ``floor_messages`` when
      the cap allows (Prince Rupert floor). If still short, pack best-effort and
      set ``floor_shortfall=true``.
    - Speaker labels via shared helper (KD6). Semantic seed uses raw user text.
    - Each item uses original role and stamps ``meta.wake_message_id`` when id
      is present (hybrid skip / OQ6 prerequisite).
    """
    cap = max(0, int(cap_tokens))
    max_n = max(0, int(max_messages))
    floor_n = max(0, int(floor_messages)) if social_wake else 0
    cid = conversation_id.strip() if isinstance(conversation_id, str) else None
    if cid == "":
        cid = None

    # KD5: non-social + no conversation scope → empty tip (honest, no bleed).
    if cid is None and not social_wake:
        meta_empty: dict[str, Any] = {
            "packed": 0,
            "available": 0,
            "window": 0,
            "cap_tokens": cap,
            "tokens_used": 0,
            "floor_messages": 0,
            "floor_applied": False,
            "floor_shortfall": False,
            "social_wake": False,
            "last_user_text": None,
            "conversation_id": None,
        }
        return [], meta_empty

    eligible = _eligible_glass_rows(
        glass_rows,
        conversation_id=cid,
        exclude_message_ids=exclude_message_ids,
    )
    window = eligible[-max_n:] if max_n > 0 else []
    # Pack newest-first so tip is preferred under token pressure.
    packed_newest_first: list[MealItem] = []
    used = 0
    floor_shortfall = False
    target_floor = min(floor_n, len(window)) if floor_n > 0 else 0

    for row in reversed(window):
        item = _glass_tail_item_from_row(
            row, conversation_id=cid, label_users=label_users
        )
        under_floor = len(packed_newest_first) < target_floor
        if used + item.token_estimate > cap:
            if under_floor:
                # Floor messages must fit under (possibly raised) cap; stop.
                floor_shortfall = True
                break
            # Drop this older non-floor; try still-older rows that may fit.
            continue
        packed_newest_first.append(item)
        used += item.token_estimate

    if target_floor > 0 and len(packed_newest_first) < target_floor:
        floor_shortfall = True

    items = list(reversed(packed_newest_first))
    tokens_used = sum(i.token_estimate for i in items)
    floor_applied = (
        social_wake
        and target_floor > 0
        and len(items) >= min(target_floor, len(window))
        and not floor_shortfall
    )
    # Seed hygiene: raw user text (no speaker-label prefix) for semantic quality.
    last_user = _last_glass_user_text(window if window else eligible)
    meta: dict[str, Any] = {
        "packed": len(items),
        "available": len(eligible),
        "window": len(window),
        "cap_tokens": cap,
        "tokens_used": tokens_used,
        "floor_messages": floor_n,
        "floor_applied": floor_applied,
        "floor_shortfall": floor_shortfall,
        "social_wake": bool(social_wake),
        # S5: tip user text for semantic seed (not logged at INFO; meta only).
        "last_user_text": last_user,
        "conversation_id": cid,
    }
    return items, meta


def _raise_glass_tail_cap_for_floor(
    *,
    glass_tail_cap: int,
    floor_cost: int,
    semantic_cap: int,
    directed_keep_cap: int,
    episodic_cap: int,
) -> tuple[int, int, int, int, int]:
    """Steal support tokens for glass-tail message floor (never temporal).

    Cut order: semantic → directed_keep → episodic.
    Returns (glass_tail_cap, semantic_cap, directed_keep_cap, episodic_cap, stolen).
    """
    need = max(0, int(floor_cost) - int(glass_tail_cap))
    if need <= 0:
        return glass_tail_cap, semantic_cap, directed_keep_cap, episodic_cap, 0
    stolen = 0
    take = min(need, semantic_cap)
    semantic_cap -= take
    need -= take
    stolen += take
    take = min(need, directed_keep_cap)
    directed_keep_cap -= take
    need -= take
    stolen += take
    take = min(need, episodic_cap)
    episodic_cap -= take
    need -= take
    stolen += take
    glass_tail_cap = int(glass_tail_cap) + stolen
    return glass_tail_cap, semantic_cap, directed_keep_cap, episodic_cap, stolen


def _suppress_temporal_by_tail_ids(
    atoms: Sequence[Atom],
    tail_ids: set[str],
) -> list[Atom]:
    """OQ6: drop open-moment atoms whose wake_message_id is on glass-tail."""
    if not tail_ids:
        return list(atoms)
    out: list[Atom] = []
    for atom in atoms:
        wid = (atom.meta or {}).get("wake_message_id")
        if wid is not None and str(wid) in tail_ids:
            continue
        out.append(atom)
    return out


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------


def compose_meal(
    store: MemoryStore,
    *,
    open_moment_id: str | None,
    budget_tokens: int = DEFAULT_MEAL_BUDGET_TOKENS,
    system_text: str = "",
    orient_text: str = "",
    now: datetime | str | None = None,
    settings: MemorySettings | None = None,
    open_moment_atoms: Sequence[Atom] | None = None,
    index: Any | None = None,
    embedder: Any | None = None,
    directed_keep_ids: Sequence[str] | None = None,
    directed_keep_summary: str | None = None,
    directed_keep_soft_aged_ids: Sequence[str] | set[str] | None = None,
    directed_keep_ages_s: Mapping[str, float] | None = None,
    glass_rows: Sequence[Mapping[str, Any]] | None = None,
    social_wake: bool = False,
    glass_tail_active: bool | None = None,
    conversation_id: str | None = None,
    label_users: Mapping[str, str] | None = None,
) -> MealPackage:
    """Compose labeled temporal + episodic [+ semantic] [+ directed_keep] [+ glass_tail] package.

    Does not load prompts; pass ``system_text`` / ``orient_text`` for fixed cost.
    Does **not** mutate the store (slide-off is meal-only).

    When ``settings.semantic_enabled`` is false and directed_keep inactive and
    glass_tail inactive, Phase 1/2 budget math (golden parity).
    ``directed_keep_active`` requires effective keep flag **and** non-empty
    ``directed_keep_ids`` (KD-A7). Glass-tail is active whenever ``glass_rows``
    is non-empty (unless ``glass_tail_active`` overrides).
    ``conversation_id`` scopes glass_tail strictly (KD4); ``label_users`` drives
    speaker prefixes on user tip lines (KD6).
    Message order (KD-ORD): episodic → semantic → directed_keep → glass_tail
    → temporal.
    """
    cfg = settings or MemorySettings()
    if now is None:
        from elyra.memory.types import utc_now_iso

        now = utc_now_iso()
    now_dt = parse_iso_z(now)

    keep_ids_list = [
        str(i).strip()
        for i in (directed_keep_ids or ())
        if str(i or "").strip()
    ]
    dk_flag = is_directed_keep_enabled(cfg)
    directed_keep_active = dk_flag and bool(keep_ids_list)

    conv_id = conversation_id.strip() if isinstance(conversation_id, str) else None
    if conv_id == "":
        conv_id = None

    glass_list = list(glass_rows) if glass_rows else []
    if glass_tail_active is None:
        # KD5: non-social with no conversation → glass_tail inactive even if
        # a caller passed leftover rows (never pack a foreign tip on pure work).
        if not social_wake and conv_id is None:
            gt_active = False
        else:
            gt_active = bool(glass_list)
    else:
        gt_active = bool(glass_tail_active) and bool(glass_list)

    (
        _fixed,
        semantic_cap,
        directed_keep_cap,
        episodic_cap,
        glass_tail_cap,
        temporal_cap,
    ) = split_memory_budget_v4(
        budget_tokens,
        system_text=system_text,
        orient_text=orient_text,
        semantic_enabled=bool(cfg.semantic_enabled),
        directed_keep_active=directed_keep_active,
        glass_tail_active=gt_active,
        glass_tail_fraction=float(
            getattr(cfg, "glass_tail_fraction", 0.10) or 0.10
        ),
        semantic_fraction=cfg.semantic_fraction,
        directed_keep_fraction=float(
            getattr(cfg, "directed_keep_fraction", 0.08) or 0.08
        ),
        episodic_fraction=cfg.episodic_fraction,
        episodic_fraction_with_semantic=cfg.episodic_fraction_with_semantic,
        temporal_min_fraction=cfg.temporal_min_fraction,
    )

    floor_messages = int(
        getattr(cfg, "glass_tail_floor_messages", 6) or 6
    )
    max_messages = int(getattr(cfg, "glass_tail_max_messages", 20) or 20)

    # Message floor raise (social wakes): steal supports, never temporal.
    floor_stolen = 0
    if gt_active and social_wake and glass_list:
        floor_cost = estimate_glass_tail_floor_tokens(
            glass_list,
            floor_messages=floor_messages,
            max_messages=max_messages,
            conversation_id=conv_id,
            label_users=label_users,
        )
        (
            glass_tail_cap,
            semantic_cap,
            directed_keep_cap,
            episodic_cap,
            floor_stolen,
        ) = _raise_glass_tail_cap_for_floor(
            glass_tail_cap=glass_tail_cap,
            floor_cost=floor_cost,
            semantic_cap=semantic_cap,
            directed_keep_cap=directed_keep_cap,
            episodic_cap=episodic_cap,
        )

    # Glass-tail first so OQ6 can suppress matching temporal wake atoms.
    glass_tail_items: list[MealItem] = []
    glass_tail_meta: dict[str, Any] | None = None
    tail_ids: set[str] = set()
    if gt_active and glass_list:
        glass_tail_items, glass_tail_meta = select_glass_tail(
            glass_list,
            cap_tokens=glass_tail_cap,
            floor_messages=floor_messages,
            max_messages=max_messages,
            social_wake=bool(social_wake),
            conversation_id=conv_id,
            label_users=label_users,
        )
        if glass_tail_meta is not None and floor_stolen:
            glass_tail_meta = dict(glass_tail_meta)
            glass_tail_meta["floor_stolen_tokens"] = floor_stolen
            glass_tail_meta["cap_tokens_effective"] = glass_tail_cap
        for item in glass_tail_items:
            wid = (item.meta or {}).get("wake_message_id")
            if wid is not None:
                tail_ids.add(str(wid))

    # Open moment atoms.
    if open_moment_atoms is not None:
        temporal_atoms = list(open_moment_atoms)
    elif open_moment_id:
        temporal_atoms = store.list_by_moment(open_moment_id)
    else:
        temporal_atoms = []
    # Exclude summary/parcel/moment_meta from temporal spine.
    temporal_atoms = [
        a for a in temporal_atoms if a.kind not in _RAW_EXCLUDE_KINDS
    ]
    temporal_atoms.sort(key=lambda a: (to_iso_z(a.t_start), a.atom_id))
    # open_ids for epi/sem/dk dedupe uses full open moment (pre-OQ6 suppress).
    open_ids = {a.atom_id for a in temporal_atoms}
    # OQ6: glass-tail wins social message_id vs temporal observation.
    temporal_atoms = _suppress_temporal_by_tail_ids(temporal_atoms, tail_ids)

    # Episodic first (shrink under pressure before temporal slide-off).
    episodic_items = select_episodic(
        store,
        now_dt,
        open_moment_id,
        episodic_cap,
        settings=cfg,
    )
    # Dedup: if an atom appears in both, keep temporal (open moment wins).
    episodic_items = _dedup_episodic_against_open(episodic_items, open_ids)

    # If episodic still over cap after select, already shrunk; if total memory
    # over residual, shrink episodic further before sliding temporal.
    epi_tokens = sum(i.token_estimate for i in episodic_items)
    if epi_tokens > episodic_cap:
        episodic_items = _shrink_episodic(
            episodic_items, summary_atoms=[], cap=episodic_cap
        )

    kept, compact, slid_n = slide_off_temporal(
        temporal_atoms,
        temporal_cap,
        protect_tail_atoms=cfg.protect_tail_atoms,
        compact_max_tokens=cfg.compact_max_tokens,
        open_moment_id=open_moment_id,
    )
    temporal_items = _temporal_items(kept, compact, open_moment_id)

    # Semantic supporting channel (Phase 2).
    # S5 / §4.5: prefer glass-tail last user as query seed on social wakes.
    # Seed only from scoped sources (KD4/KD5) — never unscoped glass_list:
    # (a) glass_tail_meta["last_user_text"] from select, else
    # (b) _last_glass_user_text on conversation-eligible rows only.
    # Non-social + null conversation: force no glass seed (no foreign tip).
    glass_seed_text: str | None = None
    if not social_wake and conv_id is None:
        glass_seed_text = None
    else:
        if glass_tail_meta and isinstance(
            glass_tail_meta.get("last_user_text"), str
        ):
            glass_seed_text = glass_tail_meta.get("last_user_text") or None
        if not glass_seed_text and glass_list:
            scoped_for_seed = _eligible_glass_rows(
                glass_list, conversation_id=conv_id
            )
            glass_seed_text = _last_glass_user_text(scoped_for_seed)

    semantic_items: list[MealItem] = []
    semantic_omitted: str | None = None
    semantic_meta: dict[str, Any] | None = None
    if cfg.semantic_enabled:
        # Temporal + episodic win over semantic (KD11).
        exclude = open_ids | _atom_ids_in_meal_items(episodic_items)
        exclude |= _atom_ids_in_meal_items(temporal_items)
        semantic_items, semantic_omitted, semantic_meta = select_semantic(
            store,
            index=index,
            embedder=embedder,
            open_moment_atoms=temporal_atoms,
            open_moment_id=open_moment_id,
            cap_tokens=semantic_cap,
            settings=cfg,
            now=now_dt,
            exclude_atom_ids=exclude,
            glass_tail_user_text=glass_seed_text,
            social_wake=bool(social_wake),
        )

    # Directed-keep supporting channel (Phase 2a) — after semantic so
    # semantic wins same-id dedupe (KD-A8 priority).
    directed_items: list[MealItem] = []
    directed_omitted: str | None = None
    directed_meta: dict[str, Any] | None = None
    # Always surface meta when flag on (even empty keep) so glass can show
    # omit reason; when flag off and no ids, leave meta None for Phase 1/2 parity.
    if dk_flag or keep_ids_list:
        exclude_dk = open_ids | _atom_ids_in_meal_items(episodic_items)
        exclude_dk |= _atom_ids_in_meal_items(temporal_items)
        exclude_dk |= _atom_ids_in_meal_items(semantic_items)
        directed_items, directed_omitted, directed_meta = select_directed_keep(
            store,
            keep_ids=keep_ids_list,
            walk_summary=directed_keep_summary,
            cap_tokens=directed_keep_cap if directed_keep_active else 0,
            settings=cfg,
            exclude_atom_ids=exclude_dk,
            enabled=dk_flag,
            soft_aged_ids=directed_keep_soft_aged_ids,
            entry_ages_s=directed_keep_ages_s,
        )

    # Message order (KD-ORD): epi → sem → dk → glass_tail → temporal.
    items = (
        list(episodic_items)
        + list(semantic_items)
        + list(directed_items)
        + list(glass_tail_items)
        + list(temporal_items)
    )
    total = sum(i.token_estimate for i in items)
    channels = tuple(dict.fromkeys(i.channel for i in items))

    return MealPackage(
        items=tuple(items),
        total_tokens=total,
        slid_off_count=slid_n,
        compact_text=compact,
        channels_present=channels,
        open_moment_id=open_moment_id,
        semantic_omitted_reason=semantic_omitted,
        semantic_select_meta=semantic_meta,
        directed_keep_omitted_reason=directed_omitted,
        directed_keep_meta=directed_meta,
        glass_tail_meta=glass_tail_meta,
    )


def _dedup_episodic_against_open(
    items: list[MealItem],
    open_ids: set[str],
) -> list[MealItem]:
    """Remove open-moment atom_ids from episodic prior blocks; drop dups."""
    if not open_ids:
        return items
    out: list[MealItem] = []
    for item in items:
        if item.atom_id and item.atom_id in open_ids:
            continue
        if item.label.startswith("episodic/prior-moment"):
            atoms = [
                a
                for a in _atoms_from_prior_item(item)
                if a.atom_id not in open_ids
            ]
            if not atoms:
                continue
            mid = (item.meta or {}).get("moment_id") or ""
            rebuilt = _rebuild_prior_moment_item(mid, atoms)
            if rebuilt is not None:
                out.append(rebuilt)
            continue
        out.append(item)
    return out


def meal_item_to_message(item: MealItem) -> dict[str, Any]:
    """Render one MealItem as a chat message (host-block user role default)."""
    content = f"{_label_header(item.label)}\n{item.content}"
    msg: dict[str, Any] = {"role": item.role, "content": content}
    meta = item.meta or {}
    media_ids = meta.get("media_ids") or []
    if media_ids:
        msg["_memory_media_ids"] = list(media_ids)
    wake_id = meta.get("wake_message_id")
    if wake_id:
        msg["id"] = wake_id
    return msg


def compose_outer_messages(
    store: MemoryStore,
    *,
    open_moment_id: str | None = None,
    budget_tokens: int = DEFAULT_MEAL_BUDGET_TOKENS,
    system_text: str = "",
    orient_text: str = "",
    now: datetime | str | None = None,
    settings: MemorySettings | None = None,
    open_moment_atoms: Sequence[Atom] | None = None,
    package: MealPackage | None = None,
    index: Any | None = None,
    embedder: Any | None = None,
    directed_keep_ids: Sequence[str] | None = None,
    directed_keep_summary: str | None = None,
    glass_rows: Sequence[Mapping[str, Any]] | None = None,
    social_wake: bool = False,
    glass_tail_active: bool | None = None,
    conversation_id: str | None = None,
    label_users: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build outer message list: system → epi → sem → dk → glass_tail → temp → orient.

    Chain (tool hops) is owned by doloop and is **not** included here.
    Pass prebuilt ``package`` to avoid re-compose when caller already has one.
    """
    if package is None:
        package = compose_meal(
            store,
            open_moment_id=open_moment_id,
            budget_tokens=budget_tokens,
            system_text=system_text,
            orient_text=orient_text,
            now=now,
            settings=settings,
            open_moment_atoms=open_moment_atoms,
            index=index,
            embedder=embedder,
            directed_keep_ids=directed_keep_ids,
            directed_keep_summary=directed_keep_summary,
            glass_rows=glass_rows,
            social_wake=social_wake,
            glass_tail_active=glass_tail_active,
            conversation_id=conversation_id,
            label_users=label_users,
        )

    messages: list[dict[str, Any]] = []
    if system_text:
        messages.append({"role": "system", "content": system_text})

    for item in package.items:
        messages.append(meal_item_to_message(item))

    if orient_text:
        messages.append({"role": "user", "content": orient_text})

    return messages


# ---------------------------------------------------------------------------
# Media expand parity (PR6)
# ---------------------------------------------------------------------------


def _resolve_media_ids_to_attachments(
    media_ids: Sequence[str],
    media_store: Any | None,
) -> list[dict[str, Any]]:
    """Build attachment dicts from media content ids (MediaStore when present)."""
    out: list[dict[str, Any]] = []
    for raw in media_ids:
        mid = str(raw or "").strip()
        if not mid:
            continue
        if media_store is None:
            out.append({"id": mid})
            continue
        try:
            meta = media_store.get(mid)
        except Exception:  # noqa: BLE001 — inventory best-effort
            meta = None
        if meta is None:
            out.append({"id": mid})
            continue
        if hasattr(meta, "to_dict"):
            d = meta.to_dict()
            if isinstance(d, dict):
                out.append(d)
                continue
        out.append(
            {
                "id": mid,
                "filename": getattr(meta, "filename", None) or "file",
                "kind": getattr(meta, "kind", None) or "file",
                "mime": getattr(meta, "mime", None) or "application/octet-stream",
                "byte_size": getattr(meta, "byte_size", None),
                "sandbox_relpath": getattr(meta, "sandbox_relpath", None),
            }
        )
    return out


def _meal_has_wake_id(
    messages: Sequence[Mapping[str, Any]],
    wake_message_id: str | None,
) -> bool:
    if not wake_message_id:
        return False
    wake = str(wake_message_id)
    for msg in messages:
        mid = msg.get("id")
        if mid is not None and str(mid) == wake:
            return True
    return False


def _inject_hybrid_wake_row(
    messages: list[dict[str, Any]],
    *,
    glass_by_id: Mapping[str, Mapping[str, Any]],
    wake_message_id: str,
) -> list[dict[str, Any]]:
    """Inject a single glass wake row when the memory meal lacks that id.

    Never reintroduces full sliding glass — only the protected wake message.
    Inserted immediately before orient (last user row without id / media stamp)
    when present; otherwise appended.
    """
    row = glass_by_id.get(str(wake_message_id))
    if row is None:
        return messages
    content = row.get("content")
    if not isinstance(content, str):
        content = "" if content is None else str(content)
    hybrid: dict[str, Any] = {
        "role": str(row.get("role") or "user"),
        "content": content,
        "id": str(wake_message_id),
    }
    # Prefer insert before final orient-like row (user, no glass id stamp).
    insert_at = len(messages)
    if messages:
        last = messages[-1]
        if (
            last.get("role") == "user"
            and last.get("id") is None
            and not last.get("_memory_media_ids")
        ):
            insert_at = len(messages) - 1
    out = list(messages)
    out.insert(insert_at, hybrid)
    return out


def _seed_glass_for_memory_media(
    messages: Sequence[Mapping[str, Any]],
    glass_by_id: Mapping[str, Mapping[str, Any]],
    media_store: Any | None,
) -> tuple[list[dict[str, Any]], dict[str, Mapping[str, Any]]]:
    """Ensure glass index can resolve atom media_ids for expand_meal_for_provider.

    - Messages with ``id`` + ``_memory_media_ids``: seed attachments when glass
      row is missing or has empty attachments.
    - Messages with only ``_memory_media_ids``: assign a synthetic host id so
      inventory expand can correlate (stripped before Completions).
    """
    glass: dict[str, Mapping[str, Any]] = dict(glass_by_id)
    out: list[dict[str, Any]] = []
    synth_i = 0
    for msg in messages:
        row = dict(msg)
        mem_ids = list(row.get("_memory_media_ids") or [])
        mid = row.get("id")
        if not mem_ids:
            out.append(row)
            continue

        atts = _resolve_media_ids_to_attachments(mem_ids, media_store)
        if mid is not None:
            mid_s = str(mid)
            existing = glass.get(mid_s)
            existing_atts = None
            if existing is not None:
                raw = existing.get("attachments")
                if isinstance(raw, list) and raw:
                    existing_atts = raw
            if existing_atts is None and atts:
                base = dict(existing) if existing is not None else {
                    "id": mid_s,
                    "role": row.get("role") or "user",
                    "content": row.get("content")
                    if isinstance(row.get("content"), str)
                    else "",
                }
                base["attachments"] = atts
                glass[mid_s] = base
            out.append(row)
            continue

        # No glass id: synthetic correlation id for inventory-only expand.
        synth_i += 1
        synth_id = f"_memory_media_{synth_i}"
        row["id"] = synth_id
        glass[synth_id] = {
            "id": synth_id,
            "role": row.get("role") or "user",
            "content": row.get("content")
            if isinstance(row.get("content"), str)
            else "",
            "attachments": atts,
        }
        out.append(row)
    return out, glass


def expand_memory_meal_for_provider(
    messages: Sequence[Mapping[str, Any]],
    *,
    glass_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    wake_message_id: str | None = None,
    viewing_att_ids: Sequence[str] | None = None,
    viewing_entries: Mapping[str, Any] | None = None,
    media_store: Any | None = None,
    provider: str = "xai",
    expand_last_user_images: bool = False,
    xai_files_client: Any | None = None,
    upload_files_to_xai: bool = False,
) -> list[dict[str, Any]]:
    """Expand a memory outer meal for Completions (media continuity).

    When ``memory.enabled`` excludes sliding glass history, vision/inventory
    must still work via atom ``media_ids`` markers and/or the wake glass row.

    * Hybrid: if no meal row carries ``id == wake_message_id``, inject **one**
      glass wake row (never full sliding history) when present in
      ``glass_by_id``.
    * Seed glass attachments from ``_memory_media_ids`` when needed.
    * Forward ``viewing_att_ids`` / ``viewing_entries`` so expand injects the
      viewing carrier and shares the wake∪viewing multimodal budget (KD-V4/V10).
    * Delegate MIME / vision / inventory policy to
      :func:`elyra.media.prompt.expand_meal_for_provider`.
    """
    from elyra.media.prompt import expand_meal_for_provider

    glass_src: Mapping[str, Mapping[str, Any]] = glass_by_id or {}
    meal: list[dict[str, Any]] = [dict(m) for m in messages]

    wake_s = str(wake_message_id) if wake_message_id else None
    if wake_s and not _meal_has_wake_id(meal, wake_s):
        meal = _inject_hybrid_wake_row(
            meal, glass_by_id=glass_src, wake_message_id=wake_s
        )
        _LOG.debug(
            "expand_memory_meal: hybrid wake row injected id=%r",
            wake_s,
        )

    meal, glass = _seed_glass_for_memory_media(meal, glass_src, media_store)

    return expand_meal_for_provider(
        meal,
        glass_by_id=glass,
        wake_message_id=wake_message_id,
        viewing_att_ids=viewing_att_ids,
        viewing_entries=viewing_entries,
        media_store=media_store,
        provider=provider,
        expand_last_user_images=expand_last_user_images,
        xai_files_client=xai_files_client,
        upload_files_to_xai=upload_files_to_xai,
    )


__all__ = [
    "DIRECTED_KEEP_OMIT_BUDGET",
    "DIRECTED_KEEP_OMIT_DEDUPED",
    "DIRECTED_KEEP_OMIT_DISABLED",
    "DIRECTED_KEEP_OMIT_EMPTY",
    "EPISODIC_MAX_PRIOR_MOMENTS",
    "GLASS_TAIL_CHANNEL",
    "GLASS_TAIL_LABEL",
    "SEMANTIC_OMIT_DEDUPED",
    "SEMANTIC_OMIT_EMPTY_SEED",
    "SEMANTIC_OMIT_ENCODER",
    "SEMANTIC_OMIT_MIN_SCORE",
    "SEMANTIC_OMIT_NO_HITS",
    "SEMANTIC_OMIT_NO_INDEX",
    "SEMANTIC_OMIT_TIMEOUT",
    "SOCIAL_WAKE_KINDS",
    "MealItem",
    "MealPackage",
    "build_compact_text",
    "build_semantic_query_seed",
    "build_semantic_query_seed_with_source",
    "compose_meal",
    "compose_outer_messages",
    "estimate_glass_tail_floor_tokens",
    "expand_memory_meal_for_provider",
    "format_atom_line",
    "meal_item_to_message",
    "moment_id_short",
    "select_directed_keep",
    "select_episodic",
    "select_glass_tail",
    "select_semantic",
    "slide_off_temporal",
]
