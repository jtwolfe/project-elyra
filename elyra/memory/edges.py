"""Durable EdgeStore: Protocol, JSONL + Lance backends, budget FIFO helpers.

Scope (PR1-PR4 / design-memory-edges-and-traversal): sibling EdgeStore next to
atom MemoryStore; put/list/delete/count parity on both backends; kind unique
keys; outgoing budget FIFO for created_with (<=100) and total (~150);
created_with retarget to youngest 1h tip + vertical fabric ensure (OQ-E7);
speak-time recalls + encode-ready has_channel write helpers (soft-fail).

Warm-on-start P3 (batch + compact):
- ``put_edges_batch`` is **merge-blocking** for multi-edge paths (backfill);
  Lance uses one ``merge_insert`` for the batch to avoid per-row fragments.
- ``compact()`` is **best-effort**. JSONL rewrites one line per live edge.
  Lance tries ``table.compact_files()`` (then ``optimize``) when present;
  when no compact API exists, returns ``ok=False`` / ``reason=unsupported``
  (offline rebuild / quarantine remain operator fallbacks — design §4.5).
- Fragment heuristic: warn when Lance fragment/data-file count ≥
  ``edge_fragment_warn_threshold`` (default 500).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import (
    AbstractSet,
    Any,
    Mapping,
    Protocol,
    Sequence,
    runtime_checkable,
)

from elyra.config import ElyraPaths
from elyra.memory.config import (
    EDGE_BACKFILL_MAX_ATOMS_DEFAULT,
    EDGE_BACKFILL_MAX_MS_DEFAULT,
    EDGE_COMPACT_ON_OPEN_DEFAULT,
    EDGE_FRAGMENT_WARN_THRESHOLD_DEFAULT,
    EDGE_SCHEMA_VERSION,
    MemorySettings,
    edges_jsonl_path,
    ensure_memory_dirs,
    is_durable_edges_enabled,
    is_edge_backfill_dev_enabled,
    lance_root,
    memory_meta_path,
)
from elyra.memory.errors import MemoryUnavailable
from elyra.memory.types import (
    PERIOD_SCALE_ORDER_WRITE,
    parse_iso_z,
    to_iso_z,
    utc_now_iso,
    window_bounds,
)
from elyra.memory.weights import (
    EDGE_CREATED_WITH,
    EDGE_HAS_CHANNEL,
    EDGE_IN_MOMENT,
    EDGE_RECALLS,
    base_weight,
    edge_weight,
)

# Spoken atom kinds eligible as recalls ANN destinations (design §2.2).
RECALLS_DST_KINDS: tuple[str, ...] = ("speak", "observation")
# has_channel modality tokens (aligned with embed CHANNELS).
HAS_CHANNEL_NAMES: tuple[str, ...] = (
    "text",
    "image",
    "audio",
    "video",
    "joint",
)
_HAS_CHANNEL_SET: frozenset[str] = frozenset(HAS_CHANNEL_NAMES)

_LOG = logging.getLogger(__name__)

# Durable kinds that participate in outgoing FIFO budgets.
_BUDGET_FIFO_KINDS: frozenset[str] = frozenset(
    {EDGE_CREATED_WITH, EDGE_RECALLS, EDGE_IN_MOMENT, EDGE_HAS_CHANNEL}
)

# Per-kind default caps when settings omit a field (mirrors MemorySettings).
_DEFAULT_KIND_CAPS: Mapping[str, int] = {
    EDGE_CREATED_WITH: 100,
    EDGE_RECALLS: 8,
    EDGE_IN_MOMENT: 1,
    EDGE_HAS_CHANNEL: 5,
}

_EDGES_TABLE = "edges"
# Operator backfill flush size (merge-blocking batch path).
_BACKFILL_EDGE_BATCH = 64


# ── Record model ───────────────────────────────────────────────────────────


def new_edge_id() -> str:
    """Return a new edge id: ``e_`` + uuid hex."""
    return "e_" + uuid.uuid4().hex


@dataclass(frozen=True)
class DurableEdge:
    """One durable directed edge (EdgeStore row).

    Identity for upserts: ``(src_atom_id, dst_atom_id, edge_kind)``.
    Stored ``weight`` is optional cache only — expand recomputes via
    ``edge_weight`` / ``semantic_factor`` (design §1.5).
    """

    edge_id: str
    src_atom_id: str
    dst_atom_id: str
    edge_kind: str
    created_at: str
    updated_at: str = ""
    weight: float | None = None
    reason: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    schema_version: int = EDGE_SCHEMA_VERSION


# Alias used in some call sites / design prose.
EdgeRecord = DurableEdge


def edge_identity_key(
    src_atom_id: str, dst_atom_id: str, edge_kind: str
) -> tuple[str, str, str]:
    """Unique key for idempotent put (design §1.2)."""
    return (src_atom_id, dst_atom_id, edge_kind)


def fifo_sort_key(edge: DurableEdge) -> tuple[str, str]:
    """FIFO order: oldest first; ``edge_id`` breaks equal ``created_at``."""
    return (edge.created_at or "", edge.edge_id or "")


def kind_outgoing_cap(
    edge_kind: str, settings: MemorySettings | None = None
) -> int | None:
    """Return outgoing cap for ``edge_kind``, or None if uncapped by kind."""
    cfg = settings or MemorySettings()
    if edge_kind == EDGE_CREATED_WITH:
        return int(getattr(cfg, "edge_created_with_max", 100) or 100)
    if edge_kind == EDGE_RECALLS:
        return int(getattr(cfg, "edge_recalls_max", 8) or 8)
    if edge_kind == EDGE_IN_MOMENT:
        return int(_DEFAULT_KIND_CAPS[EDGE_IN_MOMENT])
    if edge_kind == EDGE_HAS_CHANNEL:
        return int(_DEFAULT_KIND_CAPS[EDGE_HAS_CHANNEL])
    return None


def total_outgoing_cap(settings: MemorySettings | None = None) -> int:
    """Hard max durable edges outgoing from one src (default 150)."""
    cfg = settings or MemorySettings()
    return int(getattr(cfg, "edge_max_per_atom", 150) or 150)


def select_fifo_overflow(
    edges: Sequence[DurableEdge],
    keep: int,
) -> list[DurableEdge]:
    """Return edges to drop so at most ``keep`` remain (oldest first).

    Stable order: ``(created_at ASC, edge_id ASC)``. When ``keep < 0``,
    keep is treated as 0. Empty / already-under-cap → [].
    """
    if keep < 0:
        keep = 0
    if len(edges) <= keep:
        return []
    ordered = sorted(edges, key=fifo_sort_key)
    drop_n = len(ordered) - keep
    return list(ordered[:drop_n])


def durable_edge_from_dict(data: Mapping[str, Any]) -> DurableEdge:
    """Build DurableEdge from a mapping (JSONL / Lance row)."""
    if not isinstance(data, Mapping):
        raise TypeError("edge data must be a mapping")
    edge_id = data.get("edge_id")
    if not isinstance(edge_id, str) or not edge_id:
        raise ValueError("edge_id required")
    src = data.get("src_atom_id")
    dst = data.get("dst_atom_id")
    kind = data.get("edge_kind") or data.get("kind")
    if not isinstance(src, str) or not src:
        raise ValueError("src_atom_id required")
    if not isinstance(dst, str) or not dst:
        raise ValueError("dst_atom_id required")
    if not isinstance(kind, str) or not kind:
        raise ValueError("edge_kind required")
    created_at = data.get("created_at") or utc_now_iso()
    if not isinstance(created_at, str):
        created_at = utc_now_iso()
    else:
        try:
            created_at = to_iso_z(created_at)
        except (TypeError, ValueError):
            pass
    updated_at = data.get("updated_at") or created_at
    if isinstance(updated_at, str):
        try:
            updated_at = to_iso_z(updated_at)
        except (TypeError, ValueError):
            pass
    else:
        updated_at = created_at
    weight_raw = data.get("weight")
    weight: float | None
    if weight_raw is None or weight_raw == "":
        weight = None
    else:
        try:
            weight = float(weight_raw)
        except (TypeError, ValueError):
            weight = None
    reason = data.get("reason") or ""
    if not isinstance(reason, str):
        reason = str(reason)
    meta_raw = data.get("meta")
    if meta_raw is None and "meta_json" in data:
        meta_raw = data.get("meta_json")
    meta: dict[str, Any]
    if meta_raw is None or meta_raw == "":
        meta = {}
    elif isinstance(meta_raw, dict):
        meta = dict(meta_raw)
    elif isinstance(meta_raw, str):
        try:
            parsed = json.loads(meta_raw)
            meta = dict(parsed) if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            meta = {}
    else:
        meta = {}
    sv = data.get("schema_version", EDGE_SCHEMA_VERSION)
    try:
        schema_version = int(sv)
    except (TypeError, ValueError):
        schema_version = EDGE_SCHEMA_VERSION
    return DurableEdge(
        edge_id=edge_id,
        src_atom_id=src,
        dst_atom_id=dst,
        edge_kind=kind,
        created_at=created_at,
        updated_at=updated_at if isinstance(updated_at, str) else created_at,
        weight=weight,
        reason=reason,
        meta=meta,
        schema_version=schema_version,
    )


def durable_edge_to_dict(edge: DurableEdge) -> dict[str, Any]:
    """Serialize edge for JSONL (meta as object)."""
    return {
        "edge_id": edge.edge_id,
        "src_atom_id": edge.src_atom_id,
        "dst_atom_id": edge.dst_atom_id,
        "edge_kind": edge.edge_kind,
        "created_at": edge.created_at,
        "updated_at": edge.updated_at or edge.created_at,
        "weight": edge.weight,
        "reason": edge.reason or "",
        "meta": dict(edge.meta or {}),
        "schema_version": int(edge.schema_version or EDGE_SCHEMA_VERSION),
    }


def prepare_edge_for_put(
    edge: DurableEdge,
    *,
    existing: DurableEdge | None = None,
    now: str | None = None,
) -> DurableEdge:
    """Normalize timestamps / ids; preserve edge_id+created_at on unique update."""
    now_iso = now or utc_now_iso()
    try:
        created = to_iso_z(edge.created_at) if edge.created_at else now_iso
    except (TypeError, ValueError):
        created = now_iso
    try:
        updated = to_iso_z(edge.updated_at) if edge.updated_at else now_iso
    except (TypeError, ValueError):
        updated = now_iso
    edge_id = edge.edge_id or new_edge_id()
    if existing is not None:
        # Unique (src, dst, kind): keep identity + original created_at.
        edge_id = existing.edge_id
        created = existing.created_at or created
        updated = now_iso
    if not edge.src_atom_id or not edge.dst_atom_id or not edge.edge_kind:
        raise ValueError("src_atom_id, dst_atom_id, and edge_kind are required")
    return replace(
        edge,
        edge_id=edge_id,
        created_at=created,
        updated_at=updated,
        meta=dict(edge.meta or {}),
        schema_version=int(edge.schema_version or EDGE_SCHEMA_VERSION),
        reason=edge.reason or "",
    )


# ── Protocol ───────────────────────────────────────────────────────────────


@runtime_checkable
class EdgeStore(Protocol):
    """Swappable durable edge persistence (sibling to MemoryStore)."""

    def put_edge(self, edge: DurableEdge) -> DurableEdge:
        """Insert or replace by (src, dst, kind). Returns stored edge."""
        ...

    def put_edges_batch(self, edges: Sequence[DurableEdge]) -> list[DurableEdge]:
        """Batch insert/replace by (src, dst, kind). Returns stored edges.

        Merge-blocking multi-edge write: disk durable before RAM indexes update.
        Empty input → []. Within-batch duplicate identity keys: last wins.
        """
        ...

    def get_edge(self, edge_id: str) -> DurableEdge | None:
        ...

    def delete_edge(self, edge_id: str) -> bool:
        """Delete by edge_id. True when a row was removed."""
        ...

    def list_edges_from(
        self,
        src_atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        """Outgoing edges from src (list_by_src)."""
        ...

    def list_edges_to(
        self,
        dst_atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        """Incoming edges to dst (list_by_dst)."""
        ...

    def list_edges_for_atom(
        self,
        atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        """Edges where atom is src or dst (deduped by edge_id)."""
        ...

    def count_edges_for_atom(
        self,
        atom_id: str,
        *,
        kind: str | None = None,
        outgoing_only: bool = True,
    ) -> int:
        """Count edges for atom; default outgoing only (budget path)."""
        ...

    def replace_edges_of_kind(
        self,
        src_atom_id: str,
        edge_kind: str,
        edges: Sequence[DurableEdge],
    ) -> list[DurableEdge]:
        """Delete all outgoing of kind from src, then put ``edges``."""
        ...

    def compact(self) -> dict[str, Any]:
        """Best-effort coalesce. Returns status dict (ok / reason / backend)."""
        ...

    def health(self) -> dict[str, Any]:
        ...

    def close(self) -> None:
        ...


# ── Budget helpers (pure + store-facing) ───────────────────────────────────


def plan_budget_drops(
    outgoing: Sequence[DurableEdge],
    settings: MemorySettings | None = None,
) -> list[DurableEdge]:
    """Compute edges to drop for kind caps + total outgoing cap (FIFO).

    Does not mutate the store. Inbound edges must not be passed here —
    budgets are **outgoing from src** only (KD-E14).
    """
    cfg = settings or MemorySettings()
    by_id: dict[str, DurableEdge] = {e.edge_id: e for e in outgoing}
    drop_ids: set[str] = set()

    # 1) Per-kind FIFO windows.
    by_kind: dict[str, list[DurableEdge]] = {}
    for e in outgoing:
        by_kind.setdefault(e.edge_kind, []).append(e)
    for kind, group in by_kind.items():
        cap = kind_outgoing_cap(kind, cfg)
        if cap is None:
            continue
        for d in select_fifo_overflow(group, cap):
            drop_ids.add(d.edge_id)

    # 2) Total outgoing hard max among remaining.
    remaining = [e for e in by_id.values() if e.edge_id not in drop_ids]
    total_cap = total_outgoing_cap(cfg)
    # Prefer dropping budgeted kinds first (created_with / recalls / …).
    if len(remaining) > total_cap:
        fifo_pool = [e for e in remaining if e.edge_kind in _BUDGET_FIFO_KINDS]
        other = [e for e in remaining if e.edge_kind not in _BUDGET_FIFO_KINDS]
        # Keep other, then newest of fifo_pool to fill residual.
        keep_other = len(other)
        keep_fifo = max(0, total_cap - keep_other)
        for d in select_fifo_overflow(fifo_pool, keep_fifo):
            drop_ids.add(d.edge_id)
        still = [e for e in remaining if e.edge_id not in drop_ids]
        if len(still) > total_cap:
            for d in select_fifo_overflow(still, total_cap):
                drop_ids.add(d.edge_id)

    return [by_id[i] for i in drop_ids if i in by_id]


def enforce_outgoing_budgets(
    store: EdgeStore,
    src_atom_id: str,
    settings: MemorySettings | None = None,
    *,
    atom_store: Any | None = None,
    retarget: bool | None = None,
) -> list[DurableEdge]:
    """Drop oldest outgoing edges over kind/total caps. Returns dropped.

    Call after put when write paths care about windows. Idempotent when
    already under budget.

    When ``retarget`` is true (or None and ``settings.edge_retarget_enabled``)
    and ``atom_store`` is provided, each dropped ``created_with`` edge is
    retargeted to the youngest 1h ladder tip for the dropped target (OQ-E7).
    Retarget is fail-soft: missing tip → drop only; never invents summaries.
    """
    cfg = settings or MemorySettings()
    outgoing = store.list_edges_from(src_atom_id)
    to_drop = plan_budget_drops(outgoing, cfg)
    dropped: list[DurableEdge] = []
    for edge in sorted(to_drop, key=fifo_sort_key):
        if store.delete_edge(edge.edge_id):
            dropped.append(edge)

    do_retarget = (
        bool(getattr(cfg, "edge_retarget_enabled", True))
        if retarget is None
        else bool(retarget)
    )
    if do_retarget and atom_store is not None and dropped:
        _retarget_created_with_drops(
            store,
            atom_store,
            dropped,
            settings=cfg,
            max_rounds=8,
        )
    return dropped


def put_edge_with_budget(
    store: EdgeStore,
    edge: DurableEdge,
    settings: MemorySettings | None = None,
    *,
    atom_store: Any | None = None,
    retarget: bool | None = None,
) -> tuple[DurableEdge, list[DurableEdge]]:
    """``put_edge`` then enforce outgoing budgets on ``src``. Returns (stored, dropped).

    Pass ``atom_store`` to enable created_with FIFO retarget (OQ-E7).
    """
    stored = store.put_edge(edge)
    dropped = enforce_outgoing_budgets(
        store,
        stored.src_atom_id,
        settings,
        atom_store=atom_store,
        retarget=retarget,
    )
    return stored, dropped


def put_edges_batch(
    store: EdgeStore,
    edges: Sequence[DurableEdge],
) -> list[DurableEdge]:
    """Batch upsert via ``store.put_edges_batch`` when available, else sequential.

    Prefer this helper from multi-edge paths (backfill) so backends without a
    native batch method still work. Disk durability contract is owned by the
    store implementation: Lance/JSONL batch methods update indexes only after
    durable writes for the rows that landed.
    """
    if not edges:
        return []
    batch_fn = getattr(store, "put_edges_batch", None)
    if callable(batch_fn):
        return list(batch_fn(edges))
    out: list[DurableEdge] = []
    for edge in edges:
        out.append(store.put_edge(edge))
    return out


def _prepare_edges_for_batch(
    edges: Sequence[DurableEdge],
    *,
    by_key: Mapping[tuple[str, str, str], str],
    by_id: Mapping[str, DurableEdge],
) -> tuple[list[DurableEdge], list[str]]:
    """Prepare edges for batch put.

    Within-batch identity-key duplicates: last wins (stable order of first
    appearance replaced). Returns ``(prepared, stale_edge_ids_to_drop)`` where
    stale ids are prior unique-key holders whose ``edge_id`` differs from the
    prepared row (must leave disk before/with the upsert).
    """
    ordered_keys: list[tuple[str, str, str]] = []
    by_batch_key: dict[tuple[str, str, str], DurableEdge] = {}
    for edge in edges:
        if edge is None:
            continue
        key = edge_identity_key(
            edge.src_atom_id, edge.dst_atom_id, edge.edge_kind
        )
        if key not in by_batch_key:
            ordered_keys.append(key)
        by_batch_key[key] = edge

    prepared: list[DurableEdge] = []
    stale_ids: list[str] = []
    seen_stale: set[str] = set()
    for key in ordered_keys:
        edge = by_batch_key[key]
        existing_id = by_key.get(key)
        existing = by_id.get(existing_id) if existing_id else None
        prep = prepare_edge_for_put(edge, existing=existing)
        if (
            existing is not None
            and existing.edge_id
            and existing.edge_id != prep.edge_id
            and existing.edge_id not in seen_stale
        ):
            stale_ids.append(existing.edge_id)
            seen_stale.add(existing.edge_id)
        prepared.append(prep)
    return prepared, stale_ids


# ── created_with retarget (OQ-E7) ──────────────────────────────────────────


# Coarser scales after 1h for vertical fabric ensure (write-era ladder).
_COARSER_SCALES: tuple[str, ...] = tuple(
    s for s in PERIOD_SCALE_ORDER_WRITE if s != "1h"
)


def find_1h_tip_for_target(
    atom_store: Any,
    target: Any,
    *,
    limit: int = 8,
) -> Any | None:
    """Return youngest 1h ladder tip for ``target`` (Atom), or None.

    Prefer tip whose ``meta.source_atom_ids`` contains ``target.atom_id``;
    else the sole/youngest tip overlapping the 1h window of ``target.t_start``.
    Never creates summaries. Fail-soft on missing/unparseable t_start.
    """
    if atom_store is None or target is None:
        return None
    t_start = getattr(target, "t_start", None)
    if not t_start:
        return None
    try:
        t_dt = parse_iso_z(t_start)
        w_start, w_end = window_bounds("1h", t_dt)
    except (TypeError, ValueError):
        return None
    try:
        tips = atom_store.list_summaries(
            "1h",
            overlapping=(w_start, w_end),
            tips_only=True,
            limit=max(1, int(limit)),
        )
    except Exception:  # noqa: BLE001 — fail-soft
        _LOG.exception("list_summaries 1h for retarget failed")
        return None
    if not tips:
        return None
    target_id = str(getattr(target, "atom_id", "") or "")
    if target_id:
        for tip in tips:
            meta = getattr(tip, "meta", None) or {}
            srcs = meta.get("source_atom_ids") if isinstance(meta, dict) else None
            if isinstance(srcs, (list, tuple)) and target_id in srcs:
                return tip
    # Youngest by window_start DESC, then atom_id DESC for stability.
    def _tip_key(a: Any) -> tuple[str, str]:
        ws = getattr(a, "window_start", None) or ""
        try:
            ws = to_iso_z(ws) if ws else ""
        except (TypeError, ValueError):
            ws = str(ws or "")
        return (ws, str(getattr(a, "atom_id", "") or ""))

    ordered = sorted(tips, key=_tip_key, reverse=True)
    return ordered[0]


def list_coarser_tips_for_1h(
    atom_store: Any,
    tip_1h: Any,
    *,
    limit_per_scale: int = 4,
) -> list[Any]:
    """Existing coarser ladder tips whose windows contain tip_1h.window_start.

    Scales fine→coarse after 1h: 1d → 1w → 1m → 1y. Never invents tips.
    Prefer tips that list tip_1h in ``meta.child_atom_ids`` / from_children.
    """
    if atom_store is None or tip_1h is None:
        return []
    anchor = getattr(tip_1h, "window_start", None) or getattr(
        tip_1h, "t_start", None
    )
    if not anchor:
        return []
    try:
        anchor_dt = parse_iso_z(anchor)
    except (TypeError, ValueError):
        return []
    tip_1h_id = str(getattr(tip_1h, "atom_id", "") or "")
    found: list[Any] = []
    for scale in _COARSER_SCALES:
        try:
            w_start, w_end = window_bounds(scale, anchor_dt)
            tips = atom_store.list_summaries(
                scale,
                overlapping=(w_start, w_end),
                tips_only=True,
                limit=max(1, int(limit_per_scale)),
            )
        except Exception:  # noqa: BLE001
            _LOG.exception("list_summaries %s for vertical ensure failed", scale)
            continue
        if not tips:
            continue
        preferred = None
        if tip_1h_id:
            for tip in tips:
                meta = getattr(tip, "meta", None) or {}
                if not isinstance(meta, dict):
                    continue
                children = meta.get("child_atom_ids") or []
                if isinstance(children, (list, tuple)) and tip_1h_id in children:
                    preferred = tip
                    break
                # Intermediate lineage: child may be intermediate scale tip.
                if meta.get("from_children") and preferred is None:
                    preferred = tip
        if preferred is None:
            # Youngest overlapping tip for the coarser window.
            def _ck(a: Any) -> tuple[str, str]:
                ws = getattr(a, "window_start", None) or ""
                try:
                    ws = to_iso_z(ws) if ws else ""
                except (TypeError, ValueError):
                    ws = str(ws or "")
                return (ws, str(getattr(a, "atom_id", "") or ""))

            preferred = sorted(tips, key=_ck, reverse=True)[0]
        found.append(preferred)
    return found


def retarget_created_with_edge(
    edge_store: EdgeStore,
    atom_store: Any,
    dropped: DurableEdge,
    *,
    settings: MemorySettings | None = None,
) -> DurableEdge | None:
    """Retarget one dropped created_with edge to youngest 1h tip (OQ-E7).

    Phase A: put created_with src → tip_1h with meta.retarget_from=T.
    Phase B: when edge_retarget_ensure_vertical, verify coarser existing tips
    and record their ids in meta.retarget_vertical (projected fabric walks;
    does **not** invent summary atoms or durable summary_child mirrors).

    Returns the new/updated edge, or None on fail-soft drop.
    """
    if dropped.edge_kind != EDGE_CREATED_WITH:
        return None
    cfg = settings or MemorySettings()
    if not bool(getattr(cfg, "edge_retarget_enabled", True)):
        return None
    src = dropped.src_atom_id
    target_id = dropped.dst_atom_id
    if not src or not target_id:
        return None
    # Do not retarget virtual hubs / channel stubs.
    if target_id.startswith("moment:"):
        return None
    if any(
        target_id.endswith(suf)
        for suf in (":text", ":image", ":audio", ":video", ":joint")
    ):
        return None
    try:
        target = atom_store.get_atom(target_id)
    except Exception:  # noqa: BLE001
        _LOG.exception("retarget get_atom failed target=%s", target_id)
        return None
    if target is None:
        return None
    tip_1h = find_1h_tip_for_target(atom_store, target)
    if tip_1h is None:
        _LOG.debug(
            "retarget_fail reason=no_1h_tip src=%s target=%s",
            src,
            target_id,
        )
        return None
    tip_id = str(getattr(tip_1h, "atom_id", "") or "")
    if not tip_id or tip_id == src:
        return None
    # Same dst already (edge to tip itself aged out) — nothing useful.
    if tip_id == target_id:
        return None

    vertical_ids: list[str] = []
    if bool(getattr(cfg, "edge_retarget_ensure_vertical", True)):
        for tip_c in list_coarser_tips_for_1h(atom_store, tip_1h):
            cid = str(getattr(tip_c, "atom_id", "") or "")
            if cid:
                vertical_ids.append(cid)

    now = utc_now_iso()
    meta: dict[str, Any] = {
        "retarget_from": target_id,
    }
    if vertical_ids:
        meta["retarget_vertical"] = vertical_ids
    # Preserve prior retarget chain if re-retargeting a tip later.
    prior_meta = dropped.meta if isinstance(dropped.meta, dict) else {}
    if prior_meta.get("retarget_from") and prior_meta.get("retarget_from") != target_id:
        meta["retarget_chain"] = list(
            prior_meta.get("retarget_chain") or []
        ) + [prior_meta.get("retarget_from")]

    edge = DurableEdge(
        edge_id=new_edge_id(),
        src_atom_id=src,
        dst_atom_id=tip_id,
        edge_kind=EDGE_CREATED_WITH,
        created_at=now,
        updated_at=now,
        weight=base_weight(EDGE_CREATED_WITH),
        reason="retarget_1h_tip",
        meta=meta,
        schema_version=EDGE_SCHEMA_VERSION,
    )
    try:
        # Unique (src, tip, kind) may already exist — put updates in place.
        # Do not re-enter budget+retarget here (caller manages rounds).
        stored = edge_store.put_edge(edge)
        _LOG.debug(
            "retarget_ok src=%s from=%s to=%s vertical=%s",
            src,
            target_id,
            tip_id,
            vertical_ids,
        )
        return stored
    except Exception:  # noqa: BLE001 — soft-fail
        _LOG.exception(
            "retarget put failed src=%s from=%s to=%s",
            src,
            target_id,
            tip_id,
        )
        return None


def _retarget_created_with_drops(
    edge_store: EdgeStore,
    atom_store: Any,
    dropped: Sequence[DurableEdge],
    *,
    settings: MemorySettings | None = None,
    max_rounds: int = 8,
) -> list[DurableEdge]:
    """Retarget created_with drops; re-enforce budget if retarget puts overflow.

    Bounded rounds so a cascade of FIFO drops cannot loop forever.
    """
    cfg = settings or MemorySettings()
    produced: list[DurableEdge] = []
    pending = [e for e in dropped if e.edge_kind == EDGE_CREATED_WITH]
    seen_targets: set[tuple[str, str]] = set()
    rounds = 0
    while pending and rounds < max(1, int(max_rounds)):
        rounds += 1
        next_pending: list[DurableEdge] = []
        for edge in pending:
            key = (edge.src_atom_id, edge.dst_atom_id)
            if key in seen_targets:
                continue
            seen_targets.add(key)
            new_edge = retarget_created_with_edge(
                edge_store, atom_store, edge, settings=cfg
            )
            if new_edge is None:
                continue
            produced.append(new_edge)
            # Retarget put may push kind/total over cap again.
            overflow = enforce_outgoing_budgets(
                edge_store,
                new_edge.src_atom_id,
                cfg,
                atom_store=None,
                retarget=False,
            )
            for d in overflow:
                if d.edge_kind == EDGE_CREATED_WITH:
                    next_pending.append(d)
        pending = next_pending
    return produced
# ── Write helpers: has_channel + recalls (PR4) ─────────────────────────────


def channel_virtual_id(atom_id: str, channel: str) -> str:
    """Storage-only destination for ``has_channel``: ``{atom_id}:{channel}``."""
    aid = str(atom_id or "").strip()
    ch = str(channel or "").strip().lower()
    if not aid or not ch:
        raise ValueError("atom_id and channel are required for channel_virtual_id")
    # Avoid double-suffix if caller already passed a virtual id.
    suffix = f":{ch}"
    if aid.endswith(suffix):
        return aid
    return f"{aid}:{ch}"


def rank_recalls_candidates(
    candidates: Sequence[tuple[str, float, str]],
    *,
    ann_k: int = 15,
    keep: int = 5,
) -> list[tuple[str, float]]:
    """Select durable recalls targets from ANN hits (atom_id, score, t_start).

    v1 ranking policy (OQ-E3 / design §2.2): take the top ``ann_k`` by
    similarity among spoken hits, then among those keep the newest ``keep``
    by ``dst.t_start`` (ISO-Z descending; equal timestamps → higher score,
    then atom_id). This is **not** a fused score.

    # IMPLEMENTATION NOTE (required by design): v1 ranking is sim-filter then
    # recency among survivors. Later improve with weighted sim×recency
    # (Stretch 2 Phase 3 / #117 adjacent) without changing the edge kind.
    """
    if not candidates or keep <= 0 or ann_k <= 0:
        return []
    # Score first (desc), stable by atom_id for ties.
    by_sim = sorted(
        candidates,
        key=lambda row: (-float(row[1]), str(row[0])),
    )
    top = by_sim[: max(0, int(ann_k))]
    # Newest first among survivors.
    by_recency = sorted(
        top,
        key=lambda row: (
            str(row[2] or ""),
            float(row[1]),
            str(row[0]),
        ),
        reverse=True,
    )
    out: list[tuple[str, float]] = []
    seen: set[str] = set()
    for atom_id, score, _t in by_recency:
        if atom_id in seen:
            continue
        seen.add(atom_id)
        out.append((atom_id, float(score)))
        if len(out) >= int(keep):
            break
    return out


def _embedder_is_warm(embedder: Any) -> bool:
    """True when embedder is healthy and already loaded (no cold load)."""
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


def _encode_queue_depth(encode_queue: Any | None) -> int:
    if encode_queue is None:
        return 0
    try:
        if hasattr(encode_queue, "qsize"):
            return int(encode_queue.qsize())
        return int(len(encode_queue))
    except Exception:  # noqa: BLE001
        return 0


def write_has_channel_edges(
    edge_store: EdgeStore | None,
    atom_id: str,
    channels: Sequence[str],
    *,
    settings: MemorySettings | None = None,
    reason: str = "encode_ready",
) -> list[DurableEdge]:
    """Write durable ``has_channel`` edges for each ready channel (soft-fail).

    One edge per channel name: ``src=atom_id``, ``dst={atom_id}:{channel}``.
    Idempotent unique key. Never raises; returns stored edges (may be empty).
    Gated by ``durable_edges_enabled``.
    """
    cfg = settings or MemorySettings()
    if not is_durable_edges_enabled(cfg):
        return []
    if edge_store is None or not atom_id:
        return []
    aid = str(atom_id)
    ready: list[str] = []
    for raw in channels or ():
        ch = str(raw or "").strip().lower()
        if ch in _HAS_CHANNEL_SET and ch not in ready:
            ready.append(ch)
    if not ready:
        return []

    now = utc_now_iso()
    stored_out: list[DurableEdge] = []
    try:
        for ch in ready:
            try:
                dst = channel_virtual_id(aid, ch)
            except ValueError:
                continue
            edge = DurableEdge(
                edge_id=new_edge_id(),
                src_atom_id=aid,
                dst_atom_id=dst,
                edge_kind=EDGE_HAS_CHANNEL,
                created_at=now,
                updated_at=now,
                weight=edge_weight(EDGE_HAS_CHANNEL),
                reason=reason or "encode_ready",
                meta={"channel": ch},
                schema_version=EDGE_SCHEMA_VERSION,
            )
            try:
                stored, _dropped = put_edge_with_budget(edge_store, edge, cfg)
                stored_out.append(stored)
            except MemoryUnavailable:
                _LOG.debug(
                    "has_channel put skipped atom_id=%s channel=%s (edge store unavailable)",
                    aid,
                    ch,
                )
                return stored_out
            except Exception:  # noqa: BLE001 — never block encode
                _LOG.exception(
                    "has_channel put failed atom_id=%s channel=%s",
                    aid,
                    ch,
                )
    except Exception:  # noqa: BLE001
        _LOG.exception("write_has_channel_edges failed atom_id=%s", aid)
    return stored_out


def write_speak_recalls(
    *,
    src_atom_id: str,
    spoken_text: str,
    settings: MemorySettings | None = None,
    edge_store: EdgeStore | None = None,
    index: Any | None = None,
    embedder: Any | None = None,
    encode_queue: Any | None = None,
    exclude_atom_ids: AbstractSet[str] | None = None,
    store: Any | None = None,
    max_ms: int | None = None,
    skip_metrics: dict[str, int] | None = None,
) -> list[DurableEdge]:
    """Write speak-time ``recalls`` edges (soft-fail; never blocks promote).

    Design §2.2 / KD-E3 / KD-P0-defer / OQ-E3:
    - Gate: durable_edges_enabled + semantic_enabled; warm embedder; index;
      encode queue depth under skip threshold; ANN wall under wait helper.
    - ANN k≈15 over spoken kinds (speak|observation); rank newest keep≈5.
    - Soft-fail on any error — returns [] rather than raising.
    - ``max_ms``: explicit ceiling (ms). When None, uses
      :func:`semantic_ann_deadline_ms` for site ``recalls`` (wait helper;
      wait-off → 0 = skip ANN). ``edge_recalls_max_ms`` is **not** the
      live ceiling (KD-P0-deprec).
    - ``skip_metrics``: optional mutable map reason→count for worker metrics.
    """
    cfg = settings or MemorySettings()

    def _skip(reason: str) -> list[DurableEdge]:
        if skip_metrics is not None:
            skip_metrics[reason] = int(skip_metrics.get(reason, 0) or 0) + 1
        return []

    if not is_durable_edges_enabled(cfg):
        return _skip("flag_off")
    if not bool(getattr(cfg, "semantic_enabled", False)):
        return _skip("flag_off")
    if edge_store is None or index is None or not src_atom_id:
        return _skip("missing_deps")
    text = (spoken_text or "").strip()
    if not text:
        # Media-only without text: soft-skip in v1 (MM media-as-query is PR5 path).
        return _skip("empty_text")

    # Encode pressure / cold gates — never block speak.
    skip_depth = int(getattr(cfg, "edge_recalls_skip_queue_depth", 64) or 64)
    if skip_depth > 0 and _encode_queue_depth(encode_queue) >= skip_depth:
        _LOG.debug(
            "recalls skipped reason=encode_pressure depth>=%s src=%s",
            skip_depth,
            src_atom_id,
        )
        return _skip("encode_pressure")
    if not _embedder_is_warm(embedder):
        _LOG.debug("recalls skipped reason=encoder_cold src=%s", src_atom_id)
        return _skip("encoder_cold")

    ann_k = max(1, int(getattr(cfg, "edge_recalls_ann_k", 15) or 15))
    keep = max(1, int(getattr(cfg, "edge_recalls_keep", 5) or 5))
    # Live ANN ceiling: explicit arg or unified wait helper (not edge_recalls_max_ms).
    if max_ms is not None:
        deadline_ms = max(0, int(max_ms))
    else:
        from elyra.memory.config import semantic_ann_deadline_ms

        deadline_ms = max(0, int(semantic_ann_deadline_ms(cfg, "recalls")))
    if deadline_ms <= 0:
        _LOG.debug(
            "recalls skipped reason=zero_budget src=%s (wait-off snappy=0)",
            src_atom_id,
        )
        return _skip("zero_budget")

    t0 = time.monotonic()
    try:
        query_vec = embedder.encode_text(text)
    except Exception:  # noqa: BLE001
        _LOG.debug(
            "recalls skipped reason=encode_failed src=%s",
            src_atom_id,
            exc_info=True,
        )
        return _skip("encode_failed")
    if (time.monotonic() - t0) * 1000.0 > deadline_ms:
        _LOG.debug("recalls skipped reason=ann_timeout_encode src=%s", src_atom_id)
        return _skip("ann_timeout")

    exclude: set[str] = {str(src_atom_id)}
    if exclude_atom_ids:
        exclude.update(str(x) for x in exclude_atom_ids if x)

    try:
        hits = index.search(
            query_vec,
            k=ann_k,
            channel="joint",
            kinds=list(RECALLS_DST_KINDS),
            exclude_atom_ids=exclude,
        )
    except Exception:  # noqa: BLE001
        _LOG.debug(
            "recalls skipped reason=search_failed src=%s",
            src_atom_id,
            exc_info=True,
        )
        return _skip("search_failed")
    if (time.monotonic() - t0) * 1000.0 > deadline_ms:
        _LOG.debug("recalls skipped reason=ann_timeout_search src=%s", src_atom_id)
        return _skip("ann_timeout")
    if not hits:
        return _skip("no_hits")

    candidates: list[tuple[str, float, str]] = []
    for hit in hits:
        try:
            aid = str(getattr(hit, "atom_id", "") or "")
            if not aid or aid in exclude:
                continue
            score = float(getattr(hit, "score", 0.0) or 0.0)
            t_start = ""
            atom = getattr(hit, "atom", None)
            if atom is not None:
                t_start = str(getattr(atom, "t_start", "") or "")
                kind = str(getattr(atom, "kind", "") or "")
                if kind and kind not in RECALLS_DST_KINDS:
                    continue
            if not t_start and store is not None:
                try:
                    row = store.get_atom(aid)
                    if row is not None:
                        t_start = str(getattr(row, "t_start", "") or "")
                        kind = str(getattr(row, "kind", "") or "")
                        if kind and kind not in RECALLS_DST_KINDS:
                            continue
                except Exception:  # noqa: BLE001
                    pass
            candidates.append((aid, score, t_start))
        except Exception:  # noqa: BLE001
            continue

    # v1 ranking site (OQ-E3): sim top-k then newest keep — see rank_recalls_candidates.
    # Later: weighted sim×recency (Phase 3 / #117 adjacent).
    chosen = rank_recalls_candidates(candidates, ann_k=ann_k, keep=keep)
    if not chosen:
        return []

    now = utc_now_iso()
    stored_out: list[DurableEdge] = []
    try:
        for dst_id, cosine in chosen:
            w = edge_weight(EDGE_RECALLS, cosine=cosine)
            edge = DurableEdge(
                edge_id=new_edge_id(),
                src_atom_id=str(src_atom_id),
                dst_atom_id=str(dst_id),
                edge_kind=EDGE_RECALLS,
                created_at=now,
                updated_at=now,
                weight=w,  # optional cache only; expand recomputes from meta.cosine
                reason="speak_recalls",
                meta={"cosine": float(cosine)},
                schema_version=EDGE_SCHEMA_VERSION,
            )
            try:
                stored, _dropped = put_edge_with_budget(edge_store, edge, cfg)
                stored_out.append(stored)
            except MemoryUnavailable:
                _LOG.debug(
                    "recalls put skipped src=%s (edge store unavailable)",
                    src_atom_id,
                )
                return stored_out
            except Exception:  # noqa: BLE001
                _LOG.exception(
                    "recalls put failed src=%s dst=%s",
                    src_atom_id,
                    dst_id,
                )
    except Exception:  # noqa: BLE001
        _LOG.exception("write_speak_recalls failed src=%s", src_atom_id)
    return stored_out


# ── Shared index mixin helpers ─────────────────────────────────────────────


def _filter_edges(
    edges: Sequence[DurableEdge],
    *,
    kinds: Sequence[str] | None,
    limit: int | None,
) -> list[DurableEdge]:
    out = list(edges)
    if kinds is not None:
        allow = frozenset(str(k) for k in kinds)
        out = [e for e in out if e.edge_kind in allow]
    out.sort(key=lambda e: (e.created_at, e.edge_id))
    if limit is not None:
        out = out[: max(0, int(limit))]
    return out


def _touch_meta_edge_schema(paths: ElyraPaths, *, backend: str) -> None:
    """Best-effort write ``edge_schema_version`` into memory meta.json."""
    path = memory_meta_path(paths)
    try:
        existing: dict[str, Any] = {}
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    existing = raw
            except (OSError, json.JSONDecodeError):
                existing = {}
        changed = False
        if existing.get("edge_schema_version") != EDGE_SCHEMA_VERSION:
            existing["edge_schema_version"] = EDGE_SCHEMA_VERSION
            changed = True
        if "schema_version" not in existing:
            existing["schema_version"] = 1
            changed = True
        if "backend" not in existing:
            existing["backend"] = backend
            changed = True
        if "created_at" not in existing:
            existing["created_at"] = utc_now_iso()
            changed = True
        if not changed and path.is_file():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(existing, ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    except Exception:  # noqa: BLE001
        _LOG.exception("failed to write edge_schema_version to meta.json")


# ── JSONL backend ──────────────────────────────────────────────────────────


class JsonlEdgeStore:
    """Append-only JSONL edge store under ``data/memory/edges.jsonl``."""

    def __init__(
        self,
        paths: ElyraPaths,
        settings: MemorySettings | None = None,
    ) -> None:
        self._paths = paths
        self._settings = settings or MemorySettings()
        self._lock = threading.RLock()
        self._by_id: dict[str, DurableEdge] = {}
        self._by_src: dict[str, list[str]] = {}
        self._by_dst: dict[str, list[str]] = {}
        self._by_key: dict[tuple[str, str, str], str] = {}
        self._line_count: int = 0
        self._corrupt_lines: int = 0
        self._closed: bool = False
        ensure_memory_dirs(self._paths)
        _touch_meta_edge_schema(self._paths, backend="jsonl")
        self._load()

    @property
    def settings(self) -> MemorySettings:
        return self._settings

    @property
    def edges_path(self) -> Path:
        return edges_jsonl_path(self._paths)

    def _check_open(self) -> None:
        if self._closed:
            raise MemoryUnavailable("edge store is closed")

    def _load(self) -> None:
        path = self.edges_path
        self._by_id.clear()
        self._by_src.clear()
        self._by_dst.clear()
        self._by_key.clear()
        self._line_count = 0
        self._corrupt_lines = 0
        if not path.is_file():
            return
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                self._line_count += 1
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    self._corrupt_lines += 1
                    continue
                if not isinstance(row, dict):
                    self._corrupt_lines += 1
                    continue
                if row.get("_deleted") is True:
                    eid = row.get("edge_id")
                    if isinstance(eid, str) and eid:
                        self._index_remove(eid)
                    continue
                try:
                    edge = durable_edge_from_dict(row)
                except (TypeError, ValueError):
                    self._corrupt_lines += 1
                    continue
                self._index_put(edge)

    def _index_remove(self, edge_id: str) -> DurableEdge | None:
        old = self._by_id.pop(edge_id, None)
        if old is None:
            return None
        key = edge_identity_key(old.src_atom_id, old.dst_atom_id, old.edge_kind)
        if self._by_key.get(key) == edge_id:
            del self._by_key[key]
        src_ids = self._by_src.get(old.src_atom_id)
        if src_ids is not None:
            try:
                src_ids.remove(edge_id)
            except ValueError:
                pass
            if not src_ids:
                del self._by_src[old.src_atom_id]
        dst_ids = self._by_dst.get(old.dst_atom_id)
        if dst_ids is not None:
            try:
                dst_ids.remove(edge_id)
            except ValueError:
                pass
            if not dst_ids:
                del self._by_dst[old.dst_atom_id]
        return old

    def _index_put(self, edge: DurableEdge) -> None:
        # Drop prior unique key holder if different edge_id.
        key = edge_identity_key(
            edge.src_atom_id, edge.dst_atom_id, edge.edge_kind
        )
        prior_id = self._by_key.get(key)
        if prior_id is not None and prior_id != edge.edge_id:
            self._index_remove(prior_id)
        old = self._by_id.get(edge.edge_id)
        if old is not None and (
            old.src_atom_id != edge.src_atom_id
            or old.dst_atom_id != edge.dst_atom_id
            or old.edge_kind != edge.edge_kind
        ):
            self._index_remove(edge.edge_id)
        self._by_id[edge.edge_id] = edge
        self._by_key[key] = edge.edge_id
        src_ids = self._by_src.setdefault(edge.src_atom_id, [])
        if edge.edge_id not in src_ids:
            src_ids.append(edge.edge_id)
        dst_ids = self._by_dst.setdefault(edge.dst_atom_id, [])
        if edge.edge_id not in dst_ids:
            dst_ids.append(edge.edge_id)

    def _append_row(self, row: dict[str, Any]) -> None:
        path = self.edges_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > 0:
            with path.open("rb") as handle:
                handle.seek(-1, 2)
                if handle.read(1) != b"\n":
                    with path.open("ab") as ah:
                        ah.write(b"\n")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._line_count += 1

    def put_edge(self, edge: DurableEdge) -> DurableEdge:
        with self._lock:
            self._check_open()
            key = edge_identity_key(
                edge.src_atom_id, edge.dst_atom_id, edge.edge_kind
            )
            existing_id = self._by_key.get(key)
            existing = self._by_id.get(existing_id) if existing_id else None
            prepared = prepare_edge_for_put(edge, existing=existing)
            self._append_row(durable_edge_to_dict(prepared))
            self._index_put(prepared)
            return prepared

    def put_edges_batch(
        self, edges: Sequence[DurableEdge]
    ) -> list[DurableEdge]:
        """Append many rows under one lock; index only after each durable append.

        Exception mid-batch: edges already appended are indexed before re-raise
        so RAM matches durable suffix (no success-with-only-RAM).
        """
        with self._lock:
            self._check_open()
            if not edges:
                return []
            prepared, _stale = _prepare_edges_for_batch(
                edges, by_key=self._by_key, by_id=self._by_id
            )
            if not prepared:
                return []
            written: list[DurableEdge] = []
            try:
                for prep in prepared:
                    self._append_row(durable_edge_to_dict(prep))
                    self._index_put(prep)
                    written.append(prep)
            except Exception:
                # written already indexed; re-raise for caller honesty.
                raise
            return written

    def get_edge(self, edge_id: str) -> DurableEdge | None:
        with self._lock:
            self._check_open()
            return self._by_id.get(edge_id)

    def delete_edge(self, edge_id: str) -> bool:
        with self._lock:
            self._check_open()
            if edge_id not in self._by_id:
                return False
            self._index_remove(edge_id)
            self._append_row({"edge_id": edge_id, "_deleted": True})
            return True

    def list_edges_from(
        self,
        src_atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        with self._lock:
            self._check_open()
            ids = list(self._by_src.get(src_atom_id, ()))
            edges = [self._by_id[i] for i in ids if i in self._by_id]
            return _filter_edges(edges, kinds=kinds, limit=limit)

    def list_edges_to(
        self,
        dst_atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        with self._lock:
            self._check_open()
            ids = list(self._by_dst.get(dst_atom_id, ()))
            edges = [self._by_id[i] for i in ids if i in self._by_id]
            return _filter_edges(edges, kinds=kinds, limit=limit)

    def list_edges_for_atom(
        self,
        atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        with self._lock:
            self._check_open()
            seen: dict[str, DurableEdge] = {}
            for eid in self._by_src.get(atom_id, ()):
                e = self._by_id.get(eid)
                if e is not None:
                    seen[e.edge_id] = e
            for eid in self._by_dst.get(atom_id, ()):
                e = self._by_id.get(eid)
                if e is not None:
                    seen[e.edge_id] = e
            return _filter_edges(list(seen.values()), kinds=kinds, limit=limit)

    def count_edges_for_atom(
        self,
        atom_id: str,
        *,
        kind: str | None = None,
        outgoing_only: bool = True,
    ) -> int:
        with self._lock:
            self._check_open()
            if outgoing_only:
                ids = list(self._by_src.get(atom_id, ()))
            else:
                ids = list(
                    {
                        *self._by_src.get(atom_id, ()),
                        *self._by_dst.get(atom_id, ()),
                    }
                )
            n = 0
            for eid in ids:
                e = self._by_id.get(eid)
                if e is None:
                    continue
                if kind is not None and e.edge_kind != kind:
                    continue
                n += 1
            return n

    def replace_edges_of_kind(
        self,
        src_atom_id: str,
        edge_kind: str,
        edges: Sequence[DurableEdge],
    ) -> list[DurableEdge]:
        with self._lock:
            self._check_open()
            existing = [
                e
                for e in (
                    self._by_id[i]
                    for i in list(self._by_src.get(src_atom_id, ()))
                    if i in self._by_id
                )
                if e.edge_kind == edge_kind
            ]
            for e in existing:
                self._index_remove(e.edge_id)
                self._append_row({"edge_id": e.edge_id, "_deleted": True})
            stored: list[DurableEdge] = []
            for edge in edges:
                prepared = prepare_edge_for_put(
                    replace(
                        edge,
                        src_atom_id=src_atom_id,
                        edge_kind=edge_kind,
                    )
                )
                self._append_row(durable_edge_to_dict(prepared))
                self._index_put(prepared)
                stored.append(prepared)
            return stored

    def health(self) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                return {
                    "ok": False,
                    "backend": "jsonl",
                    "edge_count": 0,
                    "error": "closed",
                }
            by_kind: dict[str, int] = {}
            for e in self._by_id.values():
                by_kind[e.edge_kind] = by_kind.get(e.edge_kind, 0) + 1
            return {
                "ok": True,
                "backend": "jsonl",
                "edge_count": len(self._by_id),
                "line_count": self._line_count,
                "corrupt_lines": self._corrupt_lines,
                "edges_by_kind": by_kind,
                "durable_edges_enabled": is_durable_edges_enabled(
                    self._settings
                ),
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def compact(self) -> dict[str, Any]:
        """Rewrite edges.jsonl with one latest line per live edge_id."""
        with self._lock:
            self._check_open()
            path = self.edges_path
            path.parent.mkdir(parents=True, exist_ok=True)
            edges = sorted(
                self._by_id.values(),
                key=lambda e: (e.created_at, e.edge_id),
            )
            tmp = path.with_name(
                f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                with tmp.open("w", encoding="utf-8") as handle:
                    for edge in edges:
                        handle.write(
                            json.dumps(
                                durable_edge_to_dict(edge), ensure_ascii=False
                            )
                            + "\n"
                        )
                tmp.replace(path)
                self._line_count = len(edges)
                self._corrupt_lines = 0
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise
            return {
                "ok": True,
                "backend": "jsonl",
                "edge_count": len(edges),
                "line_count": self._line_count,
            }


# ── Lance backend ──────────────────────────────────────────────────────────


def _sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _edges_schema():
    import pyarrow as pa  # noqa: PLC0415

    return pa.schema(
        [
            ("edge_id", pa.string()),
            ("src_atom_id", pa.string()),
            ("dst_atom_id", pa.string()),
            ("edge_kind", pa.string()),
            ("weight", pa.float64()),
            ("created_at", pa.string()),
            ("updated_at", pa.string()),
            ("reason", pa.string()),
            ("meta_json", pa.string()),
            ("schema_version", pa.int32()),
        ]
    )


def _edge_to_lance_row(edge: DurableEdge) -> dict[str, Any]:
    return {
        "edge_id": edge.edge_id,
        "src_atom_id": edge.src_atom_id,
        "dst_atom_id": edge.dst_atom_id,
        "edge_kind": edge.edge_kind,
        "weight": float(edge.weight) if edge.weight is not None else None,
        "created_at": edge.created_at,
        "updated_at": edge.updated_at or edge.created_at,
        "reason": edge.reason or "",
        "meta_json": json.dumps(edge.meta or {}, ensure_ascii=False),
        "schema_version": int(edge.schema_version or EDGE_SCHEMA_VERSION),
    }


def _edge_from_lance_row(row: Mapping[str, Any]) -> DurableEdge:
    data = dict(row)
    if "meta" not in data and "meta_json" in data:
        data["meta"] = data.get("meta_json")
    return durable_edge_from_dict(data)


def _require_lancedb():
    import lancedb  # noqa: PLC0415

    return lancedb


def _table_row_count(table: Any) -> int | None:
    if table is None:
        return None
    try:
        if hasattr(table, "count_rows"):
            return int(table.count_rows())
    except Exception:  # noqa: BLE001
        return None
    return None


def _materialize_edges_arrow(table: Any) -> Any:
    """Full-table materialize for edges (avoid bare to_arrow ~10-row default)."""
    import pyarrow as pa  # noqa: PLC0415

    n = _table_row_count(table)
    if n == 0:
        if hasattr(table, "schema") and table.schema is not None:
            return pa.Table.from_pylist([], schema=table.schema)
        return pa.Table.from_pylist([])
    errors: list[str] = []
    if hasattr(table, "head") and n is not None:
        try:
            arrow = table.head(int(n))
            if int(arrow.num_rows) == n:
                return arrow
            errors.append(f"head_row_mismatch got={arrow.num_rows} want={n}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"head_error: {type(exc).__name__}: {exc}")
    if hasattr(table, "to_lance"):
        try:
            arrow = table.to_lance().to_table()
            if n is None or int(arrow.num_rows) == n:
                return arrow
            errors.append(
                f"to_lance_row_mismatch got={arrow.num_rows} want={n}"
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"to_lance_error: {type(exc).__name__}: {exc}")
    raise MemoryUnavailable(
        "edge lance materialize failed: " + "; ".join(errors or ["unknown"])
    )


class LanceEdgeStore:
    """LanceDB ``edges`` table under ``data/memory/lance/``."""

    def __init__(
        self,
        paths: ElyraPaths,
        settings: MemorySettings | None = None,
    ) -> None:
        lancedb = _require_lancedb()
        self._paths = paths
        self._settings = settings or MemorySettings()
        self._lock = threading.RLock()
        self._by_id: dict[str, DurableEdge] = {}
        self._by_src: dict[str, list[str]] = {}
        self._by_dst: dict[str, list[str]] = {}
        self._by_key: dict[tuple[str, str, str], str] = {}
        self._closed: bool = False
        self._db: Any = None
        self._table: Any = None
        self._lancedb = lancedb
        ensure_memory_dirs(self._paths)
        lance_root(self._paths).mkdir(parents=True, exist_ok=True)
        _touch_meta_edge_schema(self._paths, backend="lance")
        self._open_db()
        self._load()

    @property
    def settings(self) -> MemorySettings:
        return self._settings

    @property
    def lance_dir(self) -> Path:
        return lance_root(self._paths)

    def _check_open(self) -> None:
        if self._closed:
            raise MemoryUnavailable("edge store is closed")

    def _open_db(self) -> None:
        import pyarrow as pa  # noqa: PLC0415

        uri = str(self.lance_dir)
        self._db = self._lancedb.connect(uri)
        names = list(self._db.table_names())
        if _EDGES_TABLE not in names:
            empty = pa.Table.from_pylist([], schema=_edges_schema())
            self._table = self._db.create_table(_EDGES_TABLE, empty)
            return
        self._table = self._db.open_table(_EDGES_TABLE)

    def _load(self) -> None:
        self._by_id.clear()
        self._by_src.clear()
        self._by_dst.clear()
        self._by_key.clear()
        if self._table is None:
            return
        try:
            arrow = _materialize_edges_arrow(self._table)
        except MemoryUnavailable:
            _LOG.exception("edge lance load materialize failed")
            raise
        rows = arrow.to_pylist() if hasattr(arrow, "to_pylist") else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                edge = _edge_from_lance_row(row)
            except (TypeError, ValueError):
                continue
            self._index_put(edge)
        # KD-ES-PARITY: disk>0 & RAM=0 is load failure — not an honest empty store.
        disk_n = _table_row_count(self._table)
        ram_n = len(self._by_id)
        if disk_n is not None and disk_n > 0 and ram_n == 0:
            raise MemoryUnavailable(
                f"edge_load_parity_failure: disk_edge_count={disk_n} ram=0"
            )
        _LOG.info(
            "memory.edges.load_complete edges=%d disk=%s parity=%s backend=lance",
            ram_n,
            disk_n if disk_n is not None else "?",
            (ram_n == disk_n) if disk_n is not None else "?",
        )
        self._maybe_fragment_warn_and_compact()

    def _fragment_count(self) -> int | None:
        """Best-effort Lance fragment / data-file count for scale heuristic."""
        try:
            if self._table is not None and hasattr(self._table, "to_lance"):
                ds = self._table.to_lance()
                if hasattr(ds, "get_fragments"):
                    return int(len(ds.get_fragments()))
        except Exception:  # noqa: BLE001
            pass
        try:
            data_dir = self.lance_dir / f"{_EDGES_TABLE}.lance" / "data"
            if data_dir.is_dir():
                return sum(1 for p in data_dir.iterdir() if p.is_file())
        except Exception:  # noqa: BLE001
            pass
        return None

    def _fragment_warn_threshold(self) -> int:
        raw = getattr(
            self._settings,
            "edge_fragment_warn_threshold",
            EDGE_FRAGMENT_WARN_THRESHOLD_DEFAULT,
        )
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = EDGE_FRAGMENT_WARN_THRESHOLD_DEFAULT
        return max(1, n)

    def _maybe_fragment_warn_and_compact(self) -> None:
        """Warn at ≥threshold fragments; optional best-effort compact_on_open."""
        frag_n = self._fragment_count()
        thr = self._fragment_warn_threshold()
        if frag_n is not None and frag_n >= thr:
            _LOG.warning(
                "memory.edges.fragment_scale fragments=%d threshold=%d "
                "backend=lance (batch put + compact recommended)",
                frag_n,
                thr,
            )
        mode = str(
            getattr(
                self._settings,
                "edge_compact_on_open",
                EDGE_COMPACT_ON_OPEN_DEFAULT,
            )
            or EDGE_COMPACT_ON_OPEN_DEFAULT
        ).strip().lower()
        should = mode in ("true", "1", "yes") or (
            mode == "auto" and frag_n is not None and frag_n >= thr
        )
        if not should:
            return
        try:
            result = self.compact()
            _LOG.info(
                "memory.edges.compact_on_open ok=%s reason=%s fragments_before=%s",
                result.get("ok"),
                result.get("reason") or result.get("method"),
                frag_n,
            )
        except Exception:  # noqa: BLE001
            _LOG.warning(
                "memory.edges.compact_on_open failed",
                exc_info=True,
            )

    def _index_remove(self, edge_id: str) -> DurableEdge | None:
        old = self._by_id.pop(edge_id, None)
        if old is None:
            return None
        key = edge_identity_key(old.src_atom_id, old.dst_atom_id, old.edge_kind)
        if self._by_key.get(key) == edge_id:
            del self._by_key[key]
        src_ids = self._by_src.get(old.src_atom_id)
        if src_ids is not None:
            try:
                src_ids.remove(edge_id)
            except ValueError:
                pass
            if not src_ids:
                del self._by_src[old.src_atom_id]
        dst_ids = self._by_dst.get(old.dst_atom_id)
        if dst_ids is not None:
            try:
                dst_ids.remove(edge_id)
            except ValueError:
                pass
            if not dst_ids:
                del self._by_dst[old.dst_atom_id]
        return old

    def _index_put(self, edge: DurableEdge) -> None:
        key = edge_identity_key(
            edge.src_atom_id, edge.dst_atom_id, edge.edge_kind
        )
        prior_id = self._by_key.get(key)
        if prior_id is not None and prior_id != edge.edge_id:
            self._index_remove(prior_id)
        old = self._by_id.get(edge.edge_id)
        if old is not None and (
            old.src_atom_id != edge.src_atom_id
            or old.dst_atom_id != edge.dst_atom_id
            or old.edge_kind != edge.edge_kind
        ):
            self._index_remove(edge.edge_id)
        self._by_id[edge.edge_id] = edge
        self._by_key[key] = edge.edge_id
        src_ids = self._by_src.setdefault(edge.src_atom_id, [])
        if edge.edge_id not in src_ids:
            src_ids.append(edge.edge_id)
        dst_ids = self._by_dst.setdefault(edge.dst_atom_id, [])
        if edge.edge_id not in dst_ids:
            dst_ids.append(edge.edge_id)

    def _upsert_row(self, edge: DurableEdge) -> None:
        row = _edge_to_lance_row(edge)
        (
            self._table.merge_insert("edge_id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute([row])
        )

    def _upsert_rows(self, edges: Sequence[DurableEdge]) -> None:
        """Single merge_insert for many rows (fragment-friendly)."""
        if not edges:
            return
        rows = [_edge_to_lance_row(e) for e in edges]
        (
            self._table.merge_insert("edge_id")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute(rows)
        )

    def _delete_row(self, edge_id: str) -> None:
        self._table.delete(f"edge_id = {_sql_quote(edge_id)}")

    def put_edge(self, edge: DurableEdge) -> DurableEdge:
        with self._lock:
            self._check_open()
            key = edge_identity_key(
                edge.src_atom_id, edge.dst_atom_id, edge.edge_kind
            )
            existing_id = self._by_key.get(key)
            existing = self._by_id.get(existing_id) if existing_id else None
            # If unique key maps to different edge_id, drop old disk row.
            if (
                existing is not None
                and existing.edge_id != (edge.edge_id or existing.edge_id)
            ):
                pass
            prepared = prepare_edge_for_put(edge, existing=existing)
            if (
                existing is not None
                and existing.edge_id != prepared.edge_id
            ):
                self._delete_row(existing.edge_id)
                self._index_remove(existing.edge_id)
            self._upsert_row(prepared)
            self._index_put(prepared)
            return prepared

    def put_edges_batch(
        self, edges: Sequence[DurableEdge]
    ) -> list[DurableEdge]:
        """One merge_insert for the batch; indexes update only after disk OK.

        Stale unique-key holders (different edge_id) are deleted on disk
        before merge. On any disk failure, RAM is left unchanged and the
        exception propagates (no success-with-only-RAM).
        """
        with self._lock:
            self._check_open()
            if not edges:
                return []
            prepared, stale_ids = _prepare_edges_for_batch(
                edges, by_key=self._by_key, by_id=self._by_id
            )
            if not prepared:
                return []
            # Disk first — never index until durable write succeeds.
            try:
                for sid in stale_ids:
                    self._delete_row(sid)
                self._upsert_rows(prepared)
            except Exception:
                raise
            for sid in stale_ids:
                self._index_remove(sid)
            for prep in prepared:
                self._index_put(prep)
            return prepared

    def get_edge(self, edge_id: str) -> DurableEdge | None:
        with self._lock:
            self._check_open()
            return self._by_id.get(edge_id)

    def delete_edge(self, edge_id: str) -> bool:
        with self._lock:
            self._check_open()
            if edge_id not in self._by_id:
                return False
            self._index_remove(edge_id)
            self._delete_row(edge_id)
            return True

    def list_edges_from(
        self,
        src_atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        with self._lock:
            self._check_open()
            ids = list(self._by_src.get(src_atom_id, ()))
            edges = [self._by_id[i] for i in ids if i in self._by_id]
            return _filter_edges(edges, kinds=kinds, limit=limit)

    def list_edges_to(
        self,
        dst_atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        with self._lock:
            self._check_open()
            ids = list(self._by_dst.get(dst_atom_id, ()))
            edges = [self._by_id[i] for i in ids if i in self._by_id]
            return _filter_edges(edges, kinds=kinds, limit=limit)

    def list_edges_for_atom(
        self,
        atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        with self._lock:
            self._check_open()
            seen: dict[str, DurableEdge] = {}
            for eid in self._by_src.get(atom_id, ()):
                e = self._by_id.get(eid)
                if e is not None:
                    seen[e.edge_id] = e
            for eid in self._by_dst.get(atom_id, ()):
                e = self._by_id.get(eid)
                if e is not None:
                    seen[e.edge_id] = e
            return _filter_edges(list(seen.values()), kinds=kinds, limit=limit)

    def count_edges_for_atom(
        self,
        atom_id: str,
        *,
        kind: str | None = None,
        outgoing_only: bool = True,
    ) -> int:
        with self._lock:
            self._check_open()
            if outgoing_only:
                ids = list(self._by_src.get(atom_id, ()))
            else:
                ids = list(
                    {
                        *self._by_src.get(atom_id, ()),
                        *self._by_dst.get(atom_id, ()),
                    }
                )
            n = 0
            for eid in ids:
                e = self._by_id.get(eid)
                if e is None:
                    continue
                if kind is not None and e.edge_kind != kind:
                    continue
                n += 1
            return n

    def replace_edges_of_kind(
        self,
        src_atom_id: str,
        edge_kind: str,
        edges: Sequence[DurableEdge],
    ) -> list[DurableEdge]:
        with self._lock:
            self._check_open()
            existing = [
                e
                for e in (
                    self._by_id[i]
                    for i in list(self._by_src.get(src_atom_id, ()))
                    if i in self._by_id
                )
                if e.edge_kind == edge_kind
            ]
            for e in existing:
                self._index_remove(e.edge_id)
                self._delete_row(e.edge_id)
            stored: list[DurableEdge] = []
            for edge in edges:
                prepared = prepare_edge_for_put(
                    replace(
                        edge,
                        src_atom_id=src_atom_id,
                        edge_kind=edge_kind,
                    )
                )
                self._upsert_row(prepared)
                self._index_put(prepared)
                stored.append(prepared)
            return stored

    def compact(self) -> dict[str, Any]:
        """Best-effort fragment coalesce (warm-on-start P3 / design §4.5).

        Tries in order:
        1. ``table.compact_files()`` when present (preferred)
        2. ``table.optimize()`` when present
        3. else ``ok=False``, ``reason=unsupported`` — operator offline rebuild
           / quarantine remain fallbacks (see module docstring).

        Does not raise for missing APIs; may raise on I/O errors from a
        present compact API.
        """
        with self._lock:
            self._check_open()
            if self._table is None:
                return {
                    "ok": False,
                    "backend": "lance",
                    "reason": "no_table",
                }
            before = self._fragment_count()
            # Prefer compact_files (lancedb 0.20+ table API).
            if hasattr(self._table, "compact_files") and callable(
                self._table.compact_files
            ):
                try:
                    stats = self._table.compact_files()
                    after = self._fragment_count()
                    return {
                        "ok": True,
                        "backend": "lance",
                        "method": "compact_files",
                        "fragments_before": before,
                        "fragments_after": after,
                        "stats": str(stats) if stats is not None else None,
                    }
                except Exception as exc:  # noqa: BLE001
                    _LOG.warning(
                        "memory.edges.compact compact_files failed: %s",
                        exc,
                    )
                    return {
                        "ok": False,
                        "backend": "lance",
                        "reason": f"compact_files_error:{type(exc).__name__}",
                        "fragments_before": before,
                    }
            if hasattr(self._table, "optimize") and callable(
                self._table.optimize
            ):
                try:
                    self._table.optimize()
                    after = self._fragment_count()
                    return {
                        "ok": True,
                        "backend": "lance",
                        "method": "optimize",
                        "fragments_before": before,
                        "fragments_after": after,
                    }
                except Exception as exc:  # noqa: BLE001
                    _LOG.warning(
                        "memory.edges.compact optimize failed: %s",
                        exc,
                    )
                    return {
                        "ok": False,
                        "backend": "lance",
                        "reason": f"optimize_error:{type(exc).__name__}",
                        "fragments_before": before,
                    }
            return {
                "ok": False,
                "backend": "lance",
                "reason": "unsupported",
                "note": (
                    "No compact_files/optimize on this lancedb build; "
                    "use offline rebuild or quarantine (design §4.5)."
                ),
                "fragments_before": before,
            }

    def health(self) -> dict[str, Any]:
        """Health with KD-ES-PARITY honesty.

        - disk=0, RAM=0 → ok (true empty)
        - disk>0, RAM=0 → ok=false (load parity failure; open should have failed)
        - disk≠RAM, both >0 → ok=false (parity mismatch; not ready fabric)
        - disk=RAM → ok=true
        """
        with self._lock:
            if self._closed:
                return {
                    "ok": False,
                    "backend": "lance",
                    "edge_count": 0,
                    "error": "closed",
                }
            by_kind: dict[str, int] = {}
            for e in self._by_id.values():
                by_kind[e.edge_kind] = by_kind.get(e.edge_kind, 0) + 1
            ram_n = len(self._by_id)
            out: dict[str, Any] = {
                "ok": True,
                "backend": "lance",
                "edge_count": ram_n,
                "lance_dir": str(self.lance_dir),
                "edges_by_kind": by_kind,
                "durable_edges_enabled": is_durable_edges_enabled(
                    self._settings
                ),
            }
            frag_n = self._fragment_count()
            if frag_n is not None:
                thr = self._fragment_warn_threshold()
                out["fragment_count"] = frag_n
                out["fragment_warn_threshold"] = thr
                out["fragment_warn"] = frag_n >= thr
            try:
                if self._table is not None and hasattr(
                    self._table, "count_rows"
                ):
                    disk_n = int(self._table.count_rows())
                    out["disk_edge_count"] = disk_n
                    parity = ram_n == disk_n
                    out["edge_count_parity"] = parity
                    if disk_n > 0 and ram_n == 0:
                        # Critical: do not report honest empty fabric.
                        out["ok"] = False
                        out["error"] = "edge_load_parity_failure"
                    elif not parity:
                        # R1 lock: partial / mismatch → not ready.
                        out["ok"] = False
                        out["error"] = "edge_count_parity_mismatch"
            except Exception:  # noqa: BLE001
                pass
            return out

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._table = None
            self._db = None


# ── Null / unavailable (fail-soft open) ────────────────────────────────────


class UnavailableEdgeStore:
    """No-op EdgeStore when backend cannot open (reason for promote soft-fail)."""

    def __init__(self, reason: str = "edge_backend_unavailable") -> None:
        self.reason = reason
        self._closed = False

    def put_edge(self, edge: DurableEdge) -> DurableEdge:
        raise MemoryUnavailable(self.reason)

    def put_edges_batch(
        self, edges: Sequence[DurableEdge]
    ) -> list[DurableEdge]:
        raise MemoryUnavailable(self.reason)

    def get_edge(self, edge_id: str) -> DurableEdge | None:
        return None

    def delete_edge(self, edge_id: str) -> bool:
        return False

    def list_edges_from(
        self,
        src_atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        return []

    def list_edges_to(
        self,
        dst_atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        return []

    def list_edges_for_atom(
        self,
        atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[DurableEdge]:
        return []

    def count_edges_for_atom(
        self,
        atom_id: str,
        *,
        kind: str | None = None,
        outgoing_only: bool = True,
    ) -> int:
        return 0

    def replace_edges_of_kind(
        self,
        src_atom_id: str,
        edge_kind: str,
        edges: Sequence[DurableEdge],
    ) -> list[DurableEdge]:
        raise MemoryUnavailable(self.reason)

    def compact(self) -> dict[str, Any]:
        return {
            "ok": False,
            "backend": "unavailable",
            "reason": self.reason,
        }

    def health(self) -> dict[str, Any]:
        return {
            "ok": False,
            "backend": "unavailable",
            "edge_count": 0,
            "error": self.reason,
        }

    def close(self) -> None:
        self._closed = True


# ── Factory ────────────────────────────────────────────────────────────────


def open_edge_store(
    paths: ElyraPaths,
    settings: MemorySettings | None = None,
    *,
    fail_soft: bool = True,
) -> EdgeStore:
    """Factory sibling to ``open_memory_store``.

    Uses ``settings.backend`` (jsonl | lance). Lance requires ``lancedb``.
    When ``fail_soft`` (default), open failure returns
    ``UnavailableEdgeStore`` so promote can soft-skip edge writes.
    When ``fail_soft=False``, re-raises.

    Soft-fail reason strings (worker permanent vs transient classification):
    - ``edge_backend_unavailable`` — ImportError / missing lancedb (permanent)
    - ``edge_backend_open_failed`` — open/IO/materialize error (transient)
    - ``edge_load_parity_failure`` — disk>0 & RAM=0 after load (integrity)
    """
    cfg = settings or MemorySettings()
    backend = (cfg.backend or "jsonl").strip().lower()
    if backend == "lance":
        try:
            import lancedb  # noqa: F401, PLC0415

            return LanceEdgeStore(paths, cfg)
        except ImportError:
            _LOG.warning(
                "edge backend=lance requested but lancedb not installed; "
                "edge_backend_unavailable (pip install elyra[memory-lance])"
            )
            if fail_soft:
                return UnavailableEdgeStore("edge_backend_unavailable")
            raise MemoryUnavailable("edge_backend_unavailable") from None
        except MemoryUnavailable as exc:
            reason = str(exc) or "edge_backend_open_failed"
            # Preserve parity/integrity reason prefix for worker classification.
            if reason.startswith("edge_load_parity_failure"):
                soft_reason = reason
            else:
                soft_reason = f"edge_backend_open_failed:{reason}"
            _LOG.warning(
                "edge backend=lance open failed (%s); soft_reason=%s",
                reason,
                soft_reason if fail_soft else "raise",
            )
            if fail_soft:
                return UnavailableEdgeStore(soft_reason)
            raise
        except Exception as exc:
            _LOG.warning(
                "edge backend=lance open failed (%s: %s); "
                "edge_backend_open_failed",
                type(exc).__name__,
                exc,
            )
            if fail_soft:
                return UnavailableEdgeStore(
                    f"edge_backend_open_failed:{type(exc).__name__}"
                )
            raise MemoryUnavailable(
                f"edge_backend_open_failed:{type(exc).__name__}"
            ) from exc
    elif backend not in ("jsonl", "lance"):
        _LOG.warning("unknown edge backend %r; using jsonl", backend)

    try:
        return JsonlEdgeStore(paths, cfg)
    except Exception as exc:
        _LOG.warning(
            "edge backend=jsonl open failed (%s: %s); edge_backend_open_failed",
            type(exc).__name__,
            exc,
        )
        if fail_soft:
            return UnavailableEdgeStore(
                f"edge_backend_open_failed:{type(exc).__name__}"
            )
        raise MemoryUnavailable(
            f"edge_backend_open_failed:{type(exc).__name__}"
        ) from exc


def _list_atoms_for_backfill(atom_store: Any, max_atoms: int) -> list[Any]:
    """Newest-first bulk list for operator backfill (bypasses glass LIST_ATOMS_MAX).

    Prefers ``list_atoms(..., glass_cap=False)`` when the store supports it;
    falls back to default glass-capped listing for older/mock stores.
    """
    list_fn = getattr(atom_store, "list_atoms", None)
    if not callable(list_fn):
        raise RuntimeError("list_atoms_unavailable")
    lim = max(0, int(max_atoms))
    if lim == 0:
        return []
    try:
        return list(
            list_fn(newest_first=True, limit=lim, glass_cap=False) or []
        )
    except TypeError:
        # Older mocks / Protocol stubs without glass_cap kwarg.
        return list(list_fn(newest_first=True, limit=lim) or [])


def backfill_durable_edges(
    atom_store: Any,
    edge_store: EdgeStore | None,
    *,
    settings: MemorySettings | None = None,
    max_atoms: int | None = None,
    max_ms: float | int | None = None,
    kinds: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Structural-first durable edge backfill (polish1 KD-P-backfill §4.2).

    V1: ``in_moment`` only — for each atom with ``moment_id``, write hub edge
    when missing. Idempotent: re-run yields ``written≈0``. Synchronous;
    soft-fails per atom / batch. Never reconstructs ``created_with`` /
    ``recalls``.

    Uses ``put_edges_batch`` (merge-blocking) when the store supports it so
    multi-edge history repair does not explode Lance fragments.

    Requires ``durable_edges_enabled`` and ``edge_backfill_dev_enabled``.
    Scans up to ``max_atoms`` (default 2000) newest-first without the glass
    ``LIST_ATOMS_MAX`` ceiling when the store supports ``glass_cap=False``.
    """
    from elyra.memory.graph import moment_hub_id

    cfg = settings or MemorySettings()
    t0 = time.monotonic()
    kind_set = tuple(kinds) if kinds is not None else (EDGE_IN_MOMENT,)
    # Only structural kinds supported in v1.
    do_in_moment = EDGE_IN_MOMENT in kind_set

    max_atoms_eff = (
        int(max_atoms)
        if max_atoms is not None
        else int(
            getattr(cfg, "edge_backfill_max_atoms", EDGE_BACKFILL_MAX_ATOMS_DEFAULT)
            or EDGE_BACKFILL_MAX_ATOMS_DEFAULT
        )
    )
    max_atoms_eff = max(0, max_atoms_eff)
    max_ms_eff = (
        float(max_ms)
        if max_ms is not None
        else float(
            getattr(cfg, "edge_backfill_max_ms", EDGE_BACKFILL_MAX_MS_DEFAULT)
            or EDGE_BACKFILL_MAX_MS_DEFAULT
        )
    )
    max_ms_eff = max(0.0, max_ms_eff)

    result: dict[str, Any] = {
        "ok": False,
        "scanned": 0,
        "written": 0,
        "written_by_kind": {},
        "skipped": 0,
        "errors": 0,
        "elapsed_ms": 0,
        "truncated": False,
        "kinds": list(kind_set),
        "max_atoms": max_atoms_eff,
        "max_ms": int(max_ms_eff),
        "batch": False,
    }

    if not is_edge_backfill_dev_enabled(cfg):
        result["error"] = "edge_backfill_dev_disabled"
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return result
    if not is_durable_edges_enabled(cfg):
        result["error"] = "durable_edges_disabled"
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return result
    if edge_store is None or isinstance(edge_store, UnavailableEdgeStore):
        reason = "edge_store_unavailable"
        if isinstance(edge_store, UnavailableEdgeStore):
            reason = str(getattr(edge_store, "reason", None) or reason)
        result["error"] = reason
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return result
    if atom_store is None:
        result["error"] = "store_unavailable"
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return result
    if not do_in_moment:
        result["error"] = "no_supported_kinds"
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return result

    written_by_kind: dict[str, int] = {EDGE_IN_MOMENT: 0}
    scanned = 0
    written = 0
    skipped = 0
    errors = 0
    truncated = False
    used_batch = callable(getattr(edge_store, "put_edges_batch", None))
    pending: list[DurableEdge] = []

    def _flush_pending() -> None:
        nonlocal written, errors, skipped
        if not pending:
            return
        batch = list(pending)
        pending.clear()
        try:
            stored = put_edges_batch(edge_store, batch)
            written += len(stored)
            written_by_kind[EDGE_IN_MOMENT] = (
                written_by_kind.get(EDGE_IN_MOMENT, 0) + len(stored)
            )
        except Exception:  # noqa: BLE001 — soft-fail whole batch
            _LOG.exception(
                "backfill put_edges_batch failed n=%d", len(batch)
            )
            errors += len(batch)

    try:
        atoms = _list_atoms_for_backfill(atom_store, max_atoms_eff)
    except RuntimeError as exc:
        result["error"] = str(exc) or "list_atoms_unavailable"
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return result
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("backfill list_atoms failed")
        result["error"] = str(exc) or type(exc).__name__
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        return result

    for atom in atoms:
        if scanned >= max_atoms_eff:
            truncated = True
            break
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        if elapsed_ms >= max_ms_eff:
            truncated = True
            break
        scanned += 1
        moment_id = getattr(atom, "moment_id", None) or ""
        moment_id = str(moment_id).strip()
        if not moment_id:
            skipped += 1
            continue
        src_id = str(getattr(atom, "atom_id", "") or "")
        if not src_id:
            skipped += 1
            continue
        try:
            hub = moment_hub_id(moment_id)
            existing = edge_store.list_edges_from(
                src_id, kinds=[EDGE_IN_MOMENT], limit=4
            )
            if any(getattr(e, "dst_atom_id", None) == hub for e in existing):
                skipped += 1
                continue
            now = utc_now_iso()
            pending.append(
                DurableEdge(
                    edge_id=new_edge_id(),
                    src_atom_id=src_id,
                    dst_atom_id=hub,
                    edge_kind=EDGE_IN_MOMENT,
                    created_at=now,
                    updated_at=now,
                    weight=base_weight(EDGE_IN_MOMENT),
                    reason="promote_membership",
                    meta={"moment_id": moment_id},
                )
            )
            if len(pending) >= _BACKFILL_EDGE_BATCH:
                _flush_pending()
        except Exception:  # noqa: BLE001 — soft-fail per atom (scan/list)
            _LOG.exception(
                "backfill in_moment failed atom_id=%s", src_id
            )
            errors += 1

    # Flush remaining pending before exit (including when truncated mid-scan
    # after some edges were collected — durable partial progress is OK).
    # When max_ms=0, scanned never increments and pending stays empty.
    _flush_pending()

    # Truncated when wall or max_atoms stop mid-scan, or store returned a
    # full page while more may exist (caller can raise max_atoms).
    if not truncated and len(atoms) >= max_atoms_eff > 0:
        # May or may not have more atoms; honest when we hit the request cap.
        try:
            health = getattr(atom_store, "health", None)
            total = None
            if callable(health):
                h = health() or {}
                if isinstance(h, dict) and h.get("atom_count") is not None:
                    total = int(h["atom_count"])
            if total is not None and total > scanned:
                truncated = True
        except Exception:  # noqa: BLE001
            pass

    result.update(
        {
            "ok": True,
            "scanned": scanned,
            "written": written,
            "written_by_kind": written_by_kind,
            "skipped": skipped,
            "errors": errors,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "truncated": truncated,
            "batch": used_batch,
        }
    )
    return result


__all__ = [
    "DurableEdge",
    "EdgeRecord",
    "EdgeStore",
    "HAS_CHANNEL_NAMES",
    "JsonlEdgeStore",
    "LanceEdgeStore",
    "RECALLS_DST_KINDS",
    "UnavailableEdgeStore",
    "backfill_durable_edges",
    "channel_virtual_id",
    "durable_edge_from_dict",
    "durable_edge_to_dict",
    "edge_identity_key",
    "enforce_outgoing_budgets",
    "fifo_sort_key",
    "find_1h_tip_for_target",
    "kind_outgoing_cap",
    "list_coarser_tips_for_1h",
    "new_edge_id",
    "open_edge_store",
    "plan_budget_drops",
    "prepare_edge_for_put",
    "put_edge_with_budget",
    "put_edges_batch",
    "retarget_created_with_edge",
    "rank_recalls_candidates",
    "select_fifo_overflow",
    "total_outgoing_cap",
    "write_has_channel_edges",
    "write_speak_recalls",
]
