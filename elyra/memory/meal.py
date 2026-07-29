"""Labeled memory meal composition, episodic policy, and media expand parity.

Scope: pure package assembly over MemoryStore (mock-friendly). Phase 1
deterministic episodic selection (KD17); Phase 2 supporting semantic channel
(KD1/KD10/KD11/KD20); slide-off never deletes store atoms.
In scope: MealItem/MealPackage, select_episodic, select_semantic, slide-off,
compose_meal, compose_outer_messages, expand_memory_meal_for_provider.
Out of scope: promote, presence/loop drop-in (rebuild_outer lives in worker).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

_LOG = logging.getLogger(__name__)

from elyra.memory.config import MemorySettings
from elyra.memory.store import MemoryStore
from elyra.memory.tokens import (
    DEFAULT_MEAL_BUDGET_TOKENS,
    EPISODIC_SUMMARY_SHARE,
    estimate_tokens,
    split_memory_budget_v2,
)
from elyra.memory.types import (
    PERIOD_SCALE_ORDER,
    Atom,
    PeriodScale,
    parse_iso_z,
    to_iso_z,
    window_bounds,
)

# Coarse → fine for summary packing (design select_episodic step 1).
_SUMMARY_PACK_ORDER: tuple[PeriodScale, ...] = (
    "1m",
    "1w",
    "1d",
    "6h",
    "1h",
    "15m",
)

# Fine → coarse drop order under pressure (step 3c).
_SUMMARY_DROP_ORDER: tuple[PeriodScale, ...] = PERIOD_SCALE_ORDER  # 15m … 1m

_RAW_EXCLUDE_KINDS = frozenset({"summary", "parcel", "moment_meta"})
_NON_SUMMARY_KINDS = (
    "observation",
    "speak",
    "tool",
    "model",
    "ledger",
)

EPISODIC_MAX_PRIOR_MOMENTS = 12
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


@dataclass(frozen=True)
class MealItem:
    """One labeled row or section fragment in the meal package."""

    atom_id: str | None  # None for ephemeral compact / multi-atom blocks
    channel: str  # temporal | episodic | semantic | orient | system | chain
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
    """Return the summary atom for exactly this window if present."""
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


def _summary_meal_item(atom: Atom) -> MealItem:
    scale = atom.scale or "?"
    label = f"episodic/summary {scale}"
    body = atom.content_text or ""
    return _item_from_parts(
        atom_id=atom.atom_id,
        channel="episodic",
        label=label,
        content=body,
        t_start=atom.t_start or atom.window_start,
        meta={
            "scale": scale,
            "window_start": atom.window_start,
            "window_end": atom.window_end,
            "kind": "summary",
        },
    )


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
    """Deterministic broader-episodic selection (KD17).

    1. Summary pass (coarse first) up to ``episodic_cap * 0.7``
    2. Raw fill of prior moments in the horizon (exclude open moment)
    3. Under pressure: drop tool/model → excess speak/obs → finer summaries
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

    seen: set[str] = set()
    summary_items: list[MealItem] = []
    summary_atoms: list[Atom] = []
    summary_budget = int(cap * EPISODIC_SUMMARY_SHARE)
    used = 0

    # --- 1. SUMMARY PASS (coarse → fine; current + previous window) ---
    for scale in _SUMMARY_PACK_ORDER:
        cur_start, cur_end = window_bounds(scale, now_dt)
        prev_end = cur_start
        prev_start, _ = window_bounds(
            scale, cur_start - timedelta(microseconds=1)
        )
        candidates: list[Atom] = []
        for ws, we in ((cur_start, cur_end), (prev_start, prev_end)):
            atom = _load_window_summary(store, scale, ws, we)
            if atom is not None:
                candidates.append(atom)
        for atom in candidates:
            if atom.atom_id in seen:
                continue
            item = _summary_meal_item(atom)
            if used + item.token_estimate > summary_budget and summary_items:
                continue
            if used + item.token_estimate > cap and summary_items:
                continue
            seen.add(atom.atom_id)
            summary_items.append(item)
            summary_atoms.append(atom)
            used += item.token_estimate

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
        )

    return items


def _shrink_episodic(
    items: list[MealItem],
    *,
    summary_atoms: Sequence[Atom],
    cap: int,
) -> list[MealItem]:
    """Drop order 3a → 3b → 3c until under cap."""
    items = list(items)

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

    def is_last_resort_summary(item: MealItem) -> bool:
        """Most recent 1h / 1d summaries protected until last resort."""
        return summary_scale(item) in ("1h", "1d")

    # 3a: drop raw tool/model atoms (oldest first)
    items = _drop_raw_kinds(
        items,
        kinds_pred=lambda k, _meta: k in ("tool", "model"),
        cap=cap,
    )
    if total() <= cap:
        return items

    # 3b: drop raw speak/observation beyond last 2 per prior moment
    items = _trim_speak_obs_per_moment(items, keep_last=2, cap=cap)
    if total() <= cap:
        return items

    # 3c: finer summaries first; keep 1h/1d until last resort
    for scale in _SUMMARY_DROP_ORDER:
        if total() <= cap:
            break
        survivors: list[MealItem] = []
        for item in items:
            sc = summary_scale(item)
            if sc == scale and not is_last_resort_summary(item):
                # Drop this non-protected summary of this scale.
                continue
            survivors.append(item)
        items = survivors

    # Last resort: drop protected 1h then 1d (and any remaining summaries)
    if total() > cap:
        for scale in ("15m", "1h", "6h", "1d", "1w", "1m"):
            if total() <= cap:
                break
            items = [i for i in items if summary_scale(i) != scale]

    # Still over: drop oldest prior-moment blocks, then any leftover summaries
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
        del items[sum_idxs[-1]]

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


def build_semantic_query_seed(
    open_moment_atoms: Sequence[Atom],
    *,
    max_chars: int = _SEMANTIC_SEED_MAX_CHARS,
) -> str:
    """Build query text from open-moment seed (latest obs/speak/model, ≤2k)."""
    candidates = [
        a
        for a in open_moment_atoms
        if a.kind in _SEMANTIC_SEED_KINDS and (a.content_text or "").strip()
    ]
    candidates.sort(key=lambda a: (to_iso_z(a.t_start), a.atom_id))
    chunks: list[str] = []
    total = 0
    # Prefer latest: walk reverse, then reverse for chronological concat.
    for atom in reversed(candidates):
        if total >= max_chars:
            break
        body = (atom.content_text or "").strip()
        remain = max_chars - total
        piece = body[:remain]
        if not piece:
            continue
        chunks.append(piece)
        total += len(piece)
    chunks.reverse()
    return "\n".join(chunks)


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
) -> tuple[list[MealItem], str | None, dict[str, Any] | None]:
    """Select supporting semantic neighbours under a hard wall-clock budget.

    Returns ``(items, omitted_reason, select_meta)``. On timeout / missing
    encoder / empty seed the channel is omitted (empty items + reason) — never
    blocks unbounded (KD2). Temporal/episodic winners are passed via
    ``exclude_atom_ids`` (KD11). Parcel hits map to parent atoms (label
    ``semantic/parcel→parent``).

    Wait-for-select (CPU dogfood): when ``wait_for_completion`` / settings
    ``semantic_wait_for_select`` is on, use ``semantic_wait_max_ms`` as the
    ceiling, drop the snappy encode sub-budget discard, and keep a finished
    encode when the vector is usable — including when encode alone already
    exceeded the ceiling (search+pack still run). Under wait, mid-pack does
    not hard-timeout an empty pack after a good encode. Fail-fast paths
    (no_index / cold encoder / empty_seed) are unchanged.

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

    seed = build_semantic_query_seed(open_moment_atoms)
    if not seed.strip():
        return early(SEMANTIC_OMIT_EMPTY_SEED)

    if over_deadline():
        return early(SEMANTIC_OMIT_TIMEOUT)

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
        return early(SEMANTIC_OMIT_ENCODER)
    enc_elapsed = _now_ms() - t_enc0
    if not query_vec:
        return early(SEMANTIC_OMIT_ENCODER)
    if not wait and (enc_elapsed > encode_budget or over_deadline()):
        return early(SEMANTIC_OMIT_TIMEOUT)

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
                meta={"backend": "null", "joint_repair_remaining": 0},
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
            "joint_repair_remaining": joint_repair_remaining,
        }
        if concrete is not None:
            fail_meta["channel"] = concrete
        if channel_reason is not None:
            fail_meta["channel_reason"] = channel_reason
        return early(SEMANTIC_OMIT_NO_INDEX, meta=fail_meta)

    channel_meta: dict[str, Any] = {
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
        item = _item_from_parts(
            atom_id=parent.atom_id,
            channel="semantic",
            label=label,
            content=body,
            t_start=parent.t_start,
            meta={
                "score": score_f,
                "via_parcel": via_parcel,
                "hit_atom_id": atom.atom_id,
                "kind": parent.kind,
                "moment_id": parent.moment_id,
            },
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
    if (
        raw_hit_count == 0
        and exclude
        and concrete is not None
        and not over_deadline()
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
) -> MealPackage:
    """Compose labeled temporal + episodic [+ semantic] package under budget.

    Does not load prompts; pass ``system_text`` / ``orient_text`` for fixed cost.
    Does **not** mutate the store (slide-off is meal-only).

    When ``settings.semantic_enabled`` is false, Phase 1 budget math and
    channels only (golden parity). When true, ``split_memory_budget_v2`` and
    optional ``select_semantic`` (pass ``index`` / warm ``embedder``).
    Message order (KD10): episodic → semantic → temporal.
    """
    cfg = settings or MemorySettings()
    if now is None:
        from elyra.memory.types import utc_now_iso

        now = utc_now_iso()
    now_dt = parse_iso_z(now)

    _fixed, semantic_cap, episodic_cap, temporal_cap = split_memory_budget_v2(
        budget_tokens,
        system_text=system_text,
        orient_text=orient_text,
        semantic_enabled=bool(cfg.semantic_enabled),
        semantic_fraction=cfg.semantic_fraction,
        episodic_fraction=cfg.episodic_fraction,
        episodic_fraction_with_semantic=cfg.episodic_fraction_with_semantic,
        temporal_min_fraction=cfg.temporal_min_fraction,
    )

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

    # Episodic first (shrink under pressure before temporal slide-off).
    episodic_items = select_episodic(
        store,
        now_dt,
        open_moment_id,
        episodic_cap,
        settings=cfg,
    )
    # Dedup: if an atom appears in both, keep temporal (open moment wins).
    open_ids = {a.atom_id for a in temporal_atoms}
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
        )

    # Message order (KD10): episodic → semantic → temporal (compact in temporal).
    items = list(episodic_items) + list(semantic_items) + list(temporal_items)
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
) -> list[dict[str, Any]]:
    """Build outer message list: system → episodic → semantic → temporal → orient.

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
        media_store=media_store,
        provider=provider,
        expand_last_user_images=expand_last_user_images,
        xai_files_client=xai_files_client,
        upload_files_to_xai=upload_files_to_xai,
    )


__all__ = [
    "EPISODIC_MAX_PRIOR_MOMENTS",
    "SEMANTIC_OMIT_DEDUPED",
    "SEMANTIC_OMIT_EMPTY_SEED",
    "SEMANTIC_OMIT_ENCODER",
    "SEMANTIC_OMIT_MIN_SCORE",
    "SEMANTIC_OMIT_NO_HITS",
    "SEMANTIC_OMIT_NO_INDEX",
    "SEMANTIC_OMIT_TIMEOUT",
    "MealItem",
    "MealPackage",
    "build_compact_text",
    "build_semantic_query_seed",
    "compose_meal",
    "compose_outer_messages",
    "expand_memory_meal_for_provider",
    "format_atom_line",
    "meal_item_to_message",
    "moment_id_short",
    "select_episodic",
    "select_semantic",
    "slide_off_temporal",
]
