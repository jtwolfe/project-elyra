"""TraversalSession — model-guided multi-step memory walk (Phase 2a PR-A2).

Scope: temporary session state machine (start / step / finish / abandon),
seed union, expand/keep, template NL summary, budgets (KD-A18: idle TTL +
expand_ms + steps — no multi-hop session wall-clock), dual sticky snapshots
(KD-A9 / KD-A19), process-local TraversalRegistry for the presence worker,
sticky directed-keep tray ownership (S3 / KD-TRAY-SOT).

Out of scope: meal directed_keep packing (PR-A3), tools/skill (PR-A4),
glass Graph tab (PR-A5), replace mode (S4).
"""

from __future__ import annotations

import copy
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Sequence

from elyra.config import ElyraPaths
from elyra.memory.config import (
    MemorySettings,
    TRAVERSE_FRONTIER_MAX_MAX,
    TRAVERSE_KEEP_MAX_MAX,
    TRAVERSE_MAX_DEPTH_MAX,
    TRAVERSE_MAX_EXPAND_PER_STEP_MAX,
    TRAVERSE_MAX_NODES_MAX,
    TRAVERSE_MAX_STEPS_MAX,
    TRAVERSE_NEIGHBOR_K_MAX,
    is_directed_traversal_enabled,
    semantic_ann_deadline_ms,
)
from elyra.memory.graph import GraphView
from elyra.memory.keep_tray import (
    DEFAULT_ENTRY_CAP,
    DEFAULT_HARD_TTL_HOURS,
    DEFAULT_SOFT_TTL_HOURS,
    DirectedKeepTray,
    load_directed_keep_tray,
    save_directed_keep_tray,
    seed_tray_from_keep_ids,
)
from elyra.memory.store import MemoryStore
from elyra.memory.types import Atom, parse_iso_z, to_iso_z, utc_now_iso

_LOG = logging.getLogger(__name__)

SessionStatus = Literal["active", "confirmed", "abandoned", "timed_out"]

ERROR_TRAVERSE_DISABLED = "traverse_disabled"
ERROR_NO_ACTIVE = "no_active_session"
ERROR_SESSION_NOT_ACTIVE = "session_not_active"
ERROR_UNKNOWN_SESSION = "unknown_session"
ERROR_BUDGET = "budget_exhausted"
ERROR_NOT_CONSIDERED = "not_in_considered"

# Defaults mirrored from MemorySettings / design §5.1 budgets table (PR6).
_DEFAULT_MAX_DEPTH = 5
_DEFAULT_MAX_NODES = 80
_DEFAULT_MAX_STEPS = 12
_DEFAULT_MAX_SEEDS = 10  # PR5 dual reserve + semantic top
_DEFAULT_FRONTIER_MAX = 24
_DEFAULT_EXPAND_PER_STEP = 5
_DEFAULT_KEEP_MAX = 20
_DEFAULT_EXPAND_MS = 120
_DEFAULT_START_EXPAND_MS = 250  # PR5 / #103 warm semantic start headroom
_DEFAULT_LABEL_CHARS = 80
_DEFAULT_PREVIEW_CHARS = 400
_DEFAULT_INSPECT_CHARS = 800
_DEFAULT_INSPECT_MAX_IDS = 4
_DEFAULT_INSPECT_MAX_TOTAL = 2400
_DEFAULT_SCRATCHPAD = 200
_DEFAULT_TTL_S = 900
_DEFAULT_NEIGHBOR_K = 16
_DEFAULT_DUAL_START_N = 2
_SEED_MODES = frozenset(
    {"auto", "semantic_only", "temporal_only", "temporal", "explicit_only"}
)

# Host ~d2.5 local map caps (polish1 KD-P2 / design §2.5).
LOCAL_MAP_EDGES_CAP = 16
LOCAL_MAP_RING_CAP = 12
LOCAL_MAP_MOMENT_PEERS_CAP = 8
LOCAL_MAP_ASSOCIATIVE_CAP = 5
LOCAL_MAP_LADDER_CHILD_TIPS_CAP = 4
LOCAL_MAP_D2_FANOUT = 3
LOCAL_MAP_D1_TO_D2 = 4
LOCAL_MAPS_STEP_CAP = 3
# Default-filtered atom kinds (ring / primary); sequential bridge only.
NOISY_ATOM_KINDS: frozenset[str] = frozenset({"tool", "ledger", "model"})
# Prefer for ring / keep / primary map nodes (KD-P2).
PRIMARY_MAP_KINDS: frozenset[str] = frozenset(
    {"speak", "observation", "summary"}
)
_ASSOCIATIVE_EDGE_KINDS: frozenset[str] = frozenset(
    {"recalls", "semantic_hop"}
)
_MOMENT_PEER_EDGE_KINDS: frozenset[str] = frozenset(
    {"in_moment", "same_moment"}
)

# Budget keys accepted on start (tool/session overrides).
_BUDGET_OVERRIDE_KEYS = frozenset(
    {
        "max_steps",
        "max_nodes",
        "max_depth",
        "max_keep",
        "frontier_max",
        "max_expand_per_step",
        "neighbor_k",
    }
)


def clamp_budget(
    request: int | None,
    product_default: int,
    hard_max: int,
    *,
    lo: int = 1,
) -> int:
    """Clamp request-or-default to ``[lo, hard_max]`` (design §5.4).

    **Not** ``min(product_default, request)`` — tool may raise above the
    product default up to HARD_MAX (e.g. nodes default 80, hard 160,
    request 100 → 100).
    """
    raw = product_default if request is None else int(request)
    if hard_max < lo:
        return lo
    if raw < lo:
        return lo
    if raw > hard_max:
        return hard_max
    return raw


def _new_session_id() -> str:
    return "tr_" + uuid.uuid4().hex


def _int_cfg(cfg: Any | None, name: str, default: int) -> int:
    if cfg is None:
        return default
    raw = getattr(cfg, name, None)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _bool_cfg(cfg: Any | None, name: str, default: bool) -> bool:
    if cfg is None:
        return default
    raw = getattr(cfg, name, None)
    if raw is None:
        return default
    return bool(raw)


def _clip(text: str | None, n: int) -> str:
    if not text or n <= 0:
        return ""
    s = str(text).replace("\n", " ").strip()
    if len(s) <= n:
        return s
    if n <= 1:
        return s[:n]
    return s[: n - 1] + "…"


def _atom_body(store: MemoryStore, atom: Atom) -> str:
    """Body text for labels/previews; parcel → parent body when available."""
    text = (atom.content_text or "").strip()
    if atom.kind == "parcel" and atom.parent_atom_id:
        parent = store.get_atom(atom.parent_atom_id)
        if parent is not None and (parent.content_text or "").strip():
            return (parent.content_text or "").strip()
    return text


def _is_noisy_kind(kind: str | None) -> bool:
    return bool(kind) and str(kind) in NOISY_ATOM_KINDS


def _primary_kind_rank(kind: str | None) -> int:
    """Lower = preferred for ring ranking (speak > observation > summary)."""
    k = str(kind or "")
    if k == "speak":
        return 0
    if k == "observation":
        return 1
    if k == "summary":
        return 2
    if k in NOISY_ATOM_KINDS:
        return 90
    if k == "parcel":
        return 40
    return 50


def _noisy_short_label(atom: Atom, label_n: int) -> str:
    """Hygiene labels for noisy bridges — not raw JSON body clip (§2.3)."""
    kind = str(atom.kind or "")
    meta = atom.meta if isinstance(atom.meta, Mapping) else {}
    if kind == "tool":
        name = (
            meta.get("tool_name")
            or meta.get("name")
            or "tool"
        )
        return _clip(f"tool:{name}", label_n)
    if kind == "ledger":
        name = (
            meta.get("tool_name")
            or meta.get("ledger_name")
            or meta.get("name")
            or "ledger"
        )
        return _clip(f"ledger:{name}", label_n)
    if kind == "model":
        if meta.get("error_reason") or meta.get("transport_ok") is False:
            return _clip("fail", label_n)
        return _clip("ok", label_n)
    return _clip(kind or "noisy", label_n)


def _map_node_label(
    store: MemoryStore,
    atom: Atom,
    *,
    label_n: int,
    bridge_noisy: bool = False,
) -> str:
    if bridge_noisy or _is_noisy_kind(atom.kind):
        return _noisy_short_label(atom, label_n)
    return _clip(_atom_body(store, atom), label_n)


def _compass_node(
    store: MemoryStore,
    atom: Atom,
    *,
    label_n: int,
    weight: float | None = None,
    edge_kind: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "atom_id": atom.atom_id,
        "kind": atom.kind,
        "label": _map_node_label(store, atom, label_n=label_n),
    }
    if weight is not None:
        out["weight"] = float(weight)
    if edge_kind is not None:
        out["edge_kind"] = edge_kind
    return out


def _empty_local_map_shell(
    store: MemoryStore,
    focus: Atom,
    *,
    label_n: int,
    preview_n: int,
    include_noisy: bool,
    map_truncated: bool,
    structural_ms_budget: int | None,
    structural_ms_spent: int = 0,
    associative_extra: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Focus-only map (no d1 expand). Used when structural budget is exhausted.

    ``expand_deadline_ms=0`` must NOT mean GraphView unlimited — budget honesty
    requires map_truncated + empty edges/ring (compass may still use free
    prev/next fields on the focus atom).
    """
    focus_label = _map_node_label(store, focus, label_n=label_n)
    focus_preview = _clip(_atom_body(store, focus), preview_n)
    if _is_noisy_kind(focus.kind):
        focus_label = _noisy_short_label(focus, label_n)
        focus_preview = focus_label

    sequential: dict[str, Any] = {}
    if focus.prev_atom_id:
        prev_a = store.get_atom(focus.prev_atom_id)
        if prev_a is not None:
            sequential["prev"] = _compass_node(
                store, prev_a, label_n=label_n, edge_kind="sequential"
            )
            if _is_noisy_kind(prev_a.kind) and not include_noisy:
                sequential["prev"]["bridge_noisy"] = True
                sequential["prev"]["label"] = _noisy_short_label(prev_a, label_n)
    if focus.next_atom_id:
        next_a = store.get_atom(focus.next_atom_id)
        if next_a is not None:
            sequential["next"] = _compass_node(
                store, next_a, label_n=label_n, edge_kind="sequential"
            )
            if _is_noisy_kind(next_a.kind) and not include_noisy:
                sequential["next"]["bridge_noisy"] = True
                sequential["next"]["label"] = _noisy_short_label(next_a, label_n)

    associative: list[dict[str, Any]] = []
    assoc_ids: set[str] = set()
    for extra in associative_extra or ():
        if len(associative) >= LOCAL_MAP_ASSOCIATIVE_CAP:
            break
        aid = str(extra.get("atom_id") or extra.get("dst") or "").strip()
        if not aid or aid in assoc_ids or aid == focus.atom_id:
            continue
        atom = store.get_atom(aid)
        if atom is None:
            continue
        if _is_noisy_kind(atom.kind) and not include_noisy:
            continue
        assoc_ids.add(aid)
        associative.append(
            _compass_node(
                store,
                atom,
                label_n=label_n,
                weight=float(extra.get("weight") or 0.0),
                edge_kind=str(extra.get("edge_kind") or "semantic_hop"),
            )
        )

    meta_out: dict[str, Any] = {
        "structural_ms_spent": int(structural_ms_spent),
        "map_truncated": bool(map_truncated),
        "budget_exhausted": True,
    }
    if structural_ms_budget is not None:
        meta_out["structural_ms_budget"] = int(structural_ms_budget)

    return {
        "focus": {
            "atom_id": focus.atom_id,
            "kind": focus.kind,
            "label": focus_label,
            "preview": focus_preview,
        },
        "edges": [],
        "ring": [],
        "compass": {
            "sequential": sequential,
            "moment_peers": [],
            "ladder": {},
            "associative": associative[:LOCAL_MAP_ASSOCIATIVE_CAP],
        },
        "filters": {
            "noisy_kinds_omitted": [],
            "include_noisy": bool(include_noisy),
        },
        "meta": meta_out,
    }


def build_local_map(
    graph: GraphView,
    focus_id: str,
    *,
    include_noisy: bool = False,
    expand_deadline_ms: int | None = None,
    label_n: int = _DEFAULT_LABEL_CHARS,
    preview_n: int = _DEFAULT_PREVIEW_CHARS,
    neighbor_k: int = _DEFAULT_NEIGHBOR_K,
    associative_extra: Sequence[Mapping[str, Any]] | None = None,
    prefetched_edges: Sequence[Any] | None = None,
) -> dict[str, Any] | None:
    """Host-assembled ~d2.5 local map for one focus atom (KD-P2).

    Structural-only neighbors + capped d2 compass fanout. Default kind filter
    omits tool/ledger/model from the ring; sequential bridges to noisy dsts
    are kept with ``bridge_noisy=true`` and short hygiene labels.

    Budget honesty (GraphView convention differs):
    - ``expand_deadline_ms is None`` — use GraphView product default soft wall
    - ``expand_deadline_ms > 0`` — structural soft wall for d1 + d2
    - ``expand_deadline_ms == 0`` — **exhausted**: no neighbor expand, focus-only
      map with ``meta.map_truncated=true`` (never pass 0 into GraphView as
      “unlimited”)

    ``prefetched_edges`` reuses step Phase-A structural edges so the map does
    not re-expand d1 under a shared remaining budget.
    """
    store = graph._store  # noqa: SLF001
    focus_id = str(focus_id or "").strip()
    if not focus_id:
        return None
    focus = store.get_atom(focus_id)
    if focus is None:
        return None

    t0 = _now_ms()
    map_truncated = False
    membership_source: str | None = None
    noisy_omitted: set[str] = set()

    # Exhausted structural budget: focus-only truncated map (Issue 1).
    # GraphView treats expand_deadline_ms=0 as unlimited — never pass 0.
    if expand_deadline_ms is not None and int(expand_deadline_ms) <= 0:
        if prefetched_edges:
            # Have free d1 from step expand — use them, skip paid neighbors,
            # skip d2 (no remaining budget for fanout).
            edges_raw = list(prefetched_edges)
            map_truncated = True
        else:
            return _empty_local_map_shell(
                store,
                focus,
                label_n=label_n,
                preview_n=preview_n,
                include_noisy=include_noisy,
                map_truncated=True,
                structural_ms_budget=0,
                structural_ms_spent=0,
                associative_extra=associative_extra,
            )
    elif prefetched_edges is not None:
        # Reuse step expand edges for d1 (no second full neighbors call).
        edges_raw = list(prefetched_edges)
    else:
        # One structural expand for the focus (no ANN for map build — §2.4).
        edges_raw = graph.neighbors(
            focus_id,
            k=max(1, int(neighbor_k)),
            allow_semantic=False,
            expand_deadline_ms=expand_deadline_ms,
            semantic_deadline_ms=0,
        )
        meta = graph.last_expand_meta
        if meta.get("expand_truncated") or meta.get("structural_truncated"):
            map_truncated = True
        ms = meta.get("membership_source")
        if ms:
            membership_source = str(ms)

    edge_rows: list[dict[str, Any]] = []
    ring_candidates: list[dict[str, Any]] = []
    # Track best weight per dst for ranking.
    seen_edge_keys: set[tuple[str, str]] = set()

    for e in edges_raw:
        dst_id = str(e.dst_atom_id)
        ek = str(e.edge_kind or "")
        key = (dst_id, ek)
        if key in seen_edge_keys:
            continue
        seen_edge_keys.add(key)
        dst = store.get_atom(dst_id)
        if dst is None:
            continue
        dst_kind = str(dst.kind or "")
        noisy = _is_noisy_kind(dst_kind)
        bridge = False
        if noisy and not include_noisy:
            # Bridge rule: only sequential edges listed; omit dst from ring.
            if ek != "sequential":
                noisy_omitted.add(dst_kind)
                continue
            bridge = True
            noisy_omitted.add(dst_kind)
        label = _map_node_label(
            store, dst, label_n=label_n, bridge_noisy=bridge or noisy
        )
        row: dict[str, Any] = {
            "dst": dst_id,
            "edge_kind": ek,
            "weight": float(e.weight),
            "reason": str(e.reason or ""),
            "dst_kind": dst_kind or None,
            "dst_label": label,
        }
        if bridge:
            row["bridge_noisy"] = True
        edge_rows.append(row)

        if bridge:
            continue
        if noisy and not include_noisy:
            continue
        # Prefer primary kinds; parcels use parent body via _atom_body.
        ring_candidates.append(
            {
                "atom_id": dst_id,
                "kind": dst_kind or None,
                "label": label,
                "depth": 1,
                "weight": float(e.weight),
                "_rank": _primary_kind_rank(dst_kind),
            }
        )

    # Sort edges by weight desc, then kind, then dst; cap.
    edge_rows.sort(
        key=lambda r: (-float(r["weight"]), str(r["edge_kind"]), str(r["dst"]))
    )
    if len(edge_rows) > LOCAL_MAP_EDGES_CAP:
        edge_rows = edge_rows[:LOCAL_MAP_EDGES_CAP]
        map_truncated = True

    # Ring: prefer speak/observation/summary, then weight; unique by atom_id.
    ring_candidates.sort(
        key=lambda r: (int(r["_rank"]), -float(r["weight"]), str(r["atom_id"]))
    )
    ring: list[dict[str, Any]] = []
    ring_ids: set[str] = set()
    for cand in ring_candidates:
        aid = str(cand["atom_id"])
        if aid in ring_ids:
            continue
        ring_ids.add(aid)
        ring.append(
            {
                "atom_id": aid,
                "kind": cand["kind"],
                "label": cand["label"],
                "depth": 1,
                "weight": float(cand["weight"]),
            }
        )
        if len(ring) >= LOCAL_MAP_RING_CAP:
            break
    if len(ring_candidates) > LOCAL_MAP_RING_CAP:
        # More candidates than cap → truncated ring.
        map_truncated = map_truncated or len(ring_ids) < len(
            {str(c["atom_id"]) for c in ring_candidates}
        )

    # ── Compass from d1 ──────────────────────────────────────────────────
    sequential: dict[str, Any] = {}
    # Prefer live prev/next fields on focus (honest direction).
    if focus.prev_atom_id:
        prev_a = store.get_atom(focus.prev_atom_id)
        if prev_a is not None and (
            include_noisy or not _is_noisy_kind(prev_a.kind)
        ):
            sequential["prev"] = _compass_node(
                store, prev_a, label_n=label_n, edge_kind="sequential"
            )
        elif prev_a is not None and _is_noisy_kind(prev_a.kind):
            # Sequential bridge always listed in compass for time spine.
            sequential["prev"] = _compass_node(
                store, prev_a, label_n=label_n, edge_kind="sequential"
            )
            sequential["prev"]["bridge_noisy"] = True
            sequential["prev"]["label"] = _noisy_short_label(prev_a, label_n)
    if focus.next_atom_id:
        next_a = store.get_atom(focus.next_atom_id)
        if next_a is not None:
            sequential["next"] = _compass_node(
                store, next_a, label_n=label_n, edge_kind="sequential"
            )
            if _is_noisy_kind(next_a.kind) and not include_noisy:
                sequential["next"]["bridge_noisy"] = True
                sequential["next"]["label"] = _noisy_short_label(next_a, label_n)

    moment_peers: list[dict[str, Any]] = []
    moment_ids: set[str] = set()
    associative: list[dict[str, Any]] = []
    assoc_ids: set[str] = set()
    parent_summary: dict[str, Any] | None = None
    child_tips: list[dict[str, Any]] = []
    child_ids: set[str] = set()

    def _maybe_peer(dst: Atom, w: float, ek: str) -> None:
        if len(moment_peers) >= LOCAL_MAP_MOMENT_PEERS_CAP:
            return
        if dst.atom_id in moment_ids or dst.atom_id == focus_id:
            return
        if _is_noisy_kind(dst.kind) and not include_noisy:
            return
        moment_ids.add(dst.atom_id)
        moment_peers.append(
            _compass_node(
                store, dst, label_n=label_n, weight=w, edge_kind=ek
            )
        )

    def _maybe_assoc(dst: Atom, w: float, ek: str) -> None:
        if len(associative) >= LOCAL_MAP_ASSOCIATIVE_CAP:
            return
        if dst.atom_id in assoc_ids or dst.atom_id == focus_id:
            return
        if _is_noisy_kind(dst.kind) and not include_noisy:
            return
        assoc_ids.add(dst.atom_id)
        associative.append(
            _compass_node(
                store, dst, label_n=label_n, weight=w, edge_kind=ek
            )
        )

    for e in edges_raw:
        dst = store.get_atom(e.dst_atom_id)
        if dst is None:
            continue
        ek = str(e.edge_kind or "")
        w = float(e.weight)
        if ek in _MOMENT_PEER_EDGE_KINDS:
            _maybe_peer(dst, w, ek)
        if ek in _ASSOCIATIVE_EDGE_KINDS:
            _maybe_assoc(dst, w, ek)
        if ek in ("parent_of", "summary_source", "child_of") and dst.kind == "summary":
            if parent_summary is None and ek in ("parent_of", "summary_source"):
                parent_summary = _compass_node(
                    store, dst, label_n=label_n, weight=w, edge_kind=ek
                )
        if ek in ("summary_child", "child_of", "parent_of"):
            # child tips: children / sources under ladder (cap).
            if (
                len(child_tips) < LOCAL_MAP_LADDER_CHILD_TIPS_CAP
                and dst.atom_id not in child_ids
                and dst.atom_id != focus_id
                and (include_noisy or not _is_noisy_kind(dst.kind))
            ):
                # Prefer summary_child / child destinations over parent link.
                if ek in ("summary_child", "child_of") or (
                    ek == "parent_of" and focus.kind == "summary"
                ):
                    child_ids.add(dst.atom_id)
                    child_tips.append(
                        _compass_node(
                            store, dst, label_n=label_n, weight=w, edge_kind=ek
                        )
                    )

    # Optional associative extras already computed this call (seed semantic).
    for extra in associative_extra or ():
        if len(associative) >= LOCAL_MAP_ASSOCIATIVE_CAP:
            break
        aid = str(extra.get("atom_id") or extra.get("dst") or "").strip()
        if not aid or aid in assoc_ids or aid == focus_id:
            continue
        atom = store.get_atom(aid)
        if atom is None:
            continue
        if _is_noisy_kind(atom.kind) and not include_noisy:
            continue
        assoc_ids.add(aid)
        associative.append(
            _compass_node(
                store,
                atom,
                label_n=label_n,
                weight=float(extra.get("weight") or 0.0),
                edge_kind=str(extra.get("edge_kind") or "semantic_hop"),
            )
        )

    # ── d2 structural fanout for compass (§2.1 / §2.5) ────────────────────
    # Skip d2 when budget already exhausted (deadline 0 / prefetched-only path).
    # Never pass expand_deadline_ms=0 into GraphView (means unlimited).
    allow_d2 = expand_deadline_ms is None or int(expand_deadline_ms) > 0
    d1_for_d2 = (
        sorted(
            ring,
            key=lambda r: (
                _primary_kind_rank(r.get("kind")),
                -float(r.get("weight") or 0.0),
                str(r.get("atom_id")),
            ),
        )[:LOCAL_MAP_D1_TO_D2]
        if allow_d2
        else []
    )

    struct_budget = (
        float(expand_deadline_ms)
        if expand_deadline_ms is not None and int(expand_deadline_ms) > 0
        else None
    )
    spent = _now_ms() - t0
    for d1 in d1_for_d2:
        if struct_budget is not None and spent >= struct_budget:
            map_truncated = True
            break
        remaining: int | None
        if struct_budget is not None:
            remaining = max(0, int(struct_budget - spent))
            if remaining <= 0:
                map_truncated = True
                break
        else:
            # None deadline → GraphView product default (do not pass 0).
            remaining = None
        d1_id = str(d1["atom_id"])
        hop2 = graph.neighbors(
            d1_id,
            k=LOCAL_MAP_D2_FANOUT,
            allow_semantic=False,
            expand_deadline_ms=remaining,
            semantic_deadline_ms=0,
            exclude_ids={focus_id, d1_id, *ring_ids},
        )
        hop_meta = graph.last_expand_meta
        spent = _now_ms() - t0
        if hop_meta.get("expand_truncated") or hop_meta.get("structural_truncated"):
            map_truncated = True
        for e2 in hop2:
            dst2 = store.get_atom(e2.dst_atom_id)
            if dst2 is None:
                continue
            ek2 = str(e2.edge_kind or "")
            w2 = float(e2.weight)
            if ek2 in _MOMENT_PEER_EDGE_KINDS:
                _maybe_peer(dst2, w2, ek2)
            if ek2 in ("summary_child", "child_of") and (
                include_noisy or not _is_noisy_kind(dst2.kind)
            ):
                if (
                    len(child_tips) < LOCAL_MAP_LADDER_CHILD_TIPS_CAP
                    and dst2.atom_id not in child_ids
                ):
                    child_ids.add(dst2.atom_id)
                    child_tips.append(
                        _compass_node(
                            store,
                            dst2,
                            label_n=label_n,
                            weight=w2,
                            edge_kind=ek2,
                        )
                    )
            if ek2 in _ASSOCIATIVE_EDGE_KINDS:
                _maybe_assoc(dst2, w2, ek2)

    ladder: dict[str, Any] = {}
    if parent_summary is not None:
        ladder["parent_summary"] = parent_summary
    if child_tips:
        ladder["child_tips"] = child_tips[:LOCAL_MAP_LADDER_CHILD_TIPS_CAP]

    focus_label = _map_node_label(store, focus, label_n=label_n)
    focus_preview = _clip(_atom_body(store, focus), preview_n)
    # Noisy focus still gets hygiene label when kind is tool/ledger/model.
    if _is_noisy_kind(focus.kind):
        focus_label = _noisy_short_label(focus, label_n)
        focus_preview = focus_label

    filters = {
        "noisy_kinds_omitted": sorted(noisy_omitted) if not include_noisy else [],
        "include_noisy": bool(include_noisy),
    }
    meta_out: dict[str, Any] = {
        "structural_ms_spent": int(_now_ms() - t0),
        "map_truncated": map_truncated,
    }
    if membership_source:
        meta_out["membership_source"] = membership_source
    if expand_deadline_ms is not None:
        meta_out["structural_ms_budget"] = max(0, int(expand_deadline_ms))
        if int(expand_deadline_ms) <= 0:
            meta_out["budget_exhausted"] = True

    return {
        "focus": {
            "atom_id": focus_id,
            "kind": focus.kind,
            "label": focus_label,
            "preview": focus_preview,
        },
        "edges": edge_rows,
        "ring": ring,
        "compass": {
            "sequential": sequential,
            "moment_peers": moment_peers[:LOCAL_MAP_MOMENT_PEERS_CAP],
            "ladder": ladder,
            "associative": associative[:LOCAL_MAP_ASSOCIATIVE_CAP],
        },
        "filters": filters,
        "meta": meta_out,
    }


def _now_ms() -> float:
    return time.monotonic() * 1000.0


# ── Budget + session DTOs ───────────────────────────────────────────────────


@dataclass
class BudgetState:
    """Session budgets (KD-A18) — no multi-hop wall-clock field.

    PR6: also carries session-scoped frontier / expand-per-step / neighbor_k
    so tool overrides apply for the whole walk (not re-read only from settings).
    """

    max_steps: int = _DEFAULT_MAX_STEPS
    max_nodes: int = _DEFAULT_MAX_NODES
    max_depth: int = _DEFAULT_MAX_DEPTH
    max_keep: int = _DEFAULT_KEEP_MAX
    expand_ms_budget: int = _DEFAULT_EXPAND_MS
    frontier_max: int = _DEFAULT_FRONTIER_MAX
    max_expand_per_step: int = _DEFAULT_EXPAND_PER_STEP
    neighbor_k: int = _DEFAULT_NEIGHBOR_K
    steps_spent: int = 0
    nodes_spent: int = 0  # considered count
    depth_spent: int = 0
    expand_ms_spent_last: int = 0
    expand_truncated: bool = False
    # Polish1 dual deadline honesty (per-step ANN bound — KD-P0-step-ann).
    semantic_ms_budget_step: int = 0
    semantic_ms_spent_last: int = 0
    semantic_ann_calls_last: int = 0

    @property
    def steps_remaining(self) -> int:
        return max(0, self.max_steps - self.steps_spent)

    @property
    def nodes_remaining(self) -> int:
        return max(0, self.max_nodes - self.nodes_spent)

    @property
    def depth_remaining(self) -> int:
        return max(0, self.max_depth - self.depth_spent)

    def keep_slots_remaining(self, keep_count: int) -> int:
        return max(0, self.max_keep - keep_count)

    def to_surface(self, *, keep_count: int = 0) -> dict[str, Any]:
        """Thin decision surface budget block (no session wall countdown).

        Dual-deadline honesty (KD-P-glass / KD-P0-structural): structural
        expand wall vs semantic ANN budget are separate fields (aliases
        included for glass naming).
        """
        return {
            "nodes_remaining": self.nodes_remaining,
            "depth_remaining": self.depth_remaining,
            "steps_remaining": self.steps_remaining,
            "keep_slots_remaining": self.keep_slots_remaining(keep_count),
            "expand_ms_budget": self.expand_ms_budget,
            "expand_ms_spent_last": self.expand_ms_spent_last,
            "expand_truncated": self.expand_truncated,
            # Structural aliases (expand_ms is the structural soft wall).
            "structural_ms_budget": self.expand_ms_budget,
            "structural_ms_spent": self.expand_ms_spent_last,
            "structural_truncated": self.expand_truncated,
            "semantic_ms_budget_step": self.semantic_ms_budget_step,
            "semantic_ms_spent_last": self.semantic_ms_spent_last,
            "semantic_ann_calls_last": self.semantic_ann_calls_last,
            # Glass-facing aliases (KD-P-glass §5.2).
            "semantic_ms_budget": self.semantic_ms_budget_step,
            "semantic_ms_spent": self.semantic_ms_spent_last,
            "nodes_spent": self.nodes_spent,
            "depth_spent": self.depth_spent,
            "steps_spent": self.steps_spent,
            "max_nodes": self.max_nodes,
            "max_depth": self.max_depth,
            "max_steps": self.max_steps,
            "max_keep": self.max_keep,
            "frontier_max": self.frontier_max,
            "max_expand_per_step": self.max_expand_per_step,
            "neighbor_k": self.neighbor_k,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_steps": self.max_steps,
            "max_nodes": self.max_nodes,
            "max_depth": self.max_depth,
            "max_keep": self.max_keep,
            "expand_ms_budget": self.expand_ms_budget,
            "frontier_max": self.frontier_max,
            "max_expand_per_step": self.max_expand_per_step,
            "neighbor_k": self.neighbor_k,
            "steps_spent": self.steps_spent,
            "nodes_spent": self.nodes_spent,
            "depth_spent": self.depth_spent,
            "expand_ms_spent_last": self.expand_ms_spent_last,
            "expand_truncated": self.expand_truncated,
            "structural_ms_budget": self.expand_ms_budget,
            "structural_ms_spent": self.expand_ms_spent_last,
            "structural_truncated": self.expand_truncated,
            "semantic_ms_budget_step": self.semantic_ms_budget_step,
            "semantic_ms_spent_last": self.semantic_ms_spent_last,
            "semantic_ann_calls_last": self.semantic_ann_calls_last,
            "semantic_ms_budget": self.semantic_ms_budget_step,
            "semantic_ms_spent": self.semantic_ms_spent_last,
            "steps_remaining": self.steps_remaining,
            "nodes_remaining": self.nodes_remaining,
            "depth_remaining": self.depth_remaining,
        }


@dataclass
class ConsideredNode:
    atom_id: str
    kind: str | None
    label: str
    preview: str
    via_edge_kind: str | None
    via_reason: str | None
    depth: int
    weight: float | None
    parent_id: str | None = None  # walk-tree parent (expand source)

    def summary(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "kind": self.kind,
            "label": self.label,
            "via_edge_kind": self.via_edge_kind,
            "depth": self.depth,
            "weight": self.weight,
        }


@dataclass
class FrontierItem:
    atom_id: str
    label: str
    preview: str
    kind: str | None
    edge_kind: str | None
    weight: float
    reason: str
    depth: int

    def to_surface(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "label": self.label,
            "preview": self.preview,
            "kind": self.kind,
            "edge_kind": self.edge_kind,
            "weight": self.weight,
            "reason": self.reason,
            "depth": self.depth,
        }


@dataclass
class ConfirmedKeepSnapshot:
    """Meal-only thin slice — NOT the glass last-walk source (KD-A19)."""

    session_id: str
    goal: str
    keep_ids: tuple[str, ...]
    walk_summary_nl: str
    finished_at: str
    moment_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "keep_ids": list(self.keep_ids),
            "walk_summary_nl": self.walk_summary_nl,
            "finished_at": self.finished_at,
            "moment_id": self.moment_id,
        }


@dataclass
class AtomPreview:
    atom_id: str
    kind: str | None
    body: str
    truncated: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "atom_id": self.atom_id,
            "kind": self.kind,
            "body": self.body,
            "truncated": self.truncated,
        }
        if self.error:
            d["error"] = self.error
        return d


@dataclass
class TraversalSession:
    """In-process temporary walk state (no durable Atom rows — KD-A1)."""

    session_id: str
    goal: str
    status: SessionStatus
    seed_ids: tuple[str, ...]
    considered: dict[str, ConsideredNode]
    frontier: list[FrontierItem]
    keep_ids: list[str]
    scratchpad: str
    budgets: BudgetState
    created_at: str
    updated_at: str
    finished_at: str | None = None
    moment_id: str | None = None
    walk_summary_nl: str | None = None
    seed_reasons: list[str] = field(default_factory=list)
    edge_kind_counts: dict[str, int] = field(default_factory=dict)
    expand_truncated: bool = False
    error_reason: str | None = None
    # Session frontier cache: moment_id → member atom_ids after expand_moment
    # (#105 / design §5.2). Populated from GraphView on step expand.
    moment_member_cache: dict[str, list[str]] = field(default_factory=dict)

    def touch(self, now: str) -> None:
        self.updated_at = now

    def to_view(self) -> dict[str, Any]:
        """Full session DTO for glass / tests (considered summary + budgets)."""
        considered_list = [
            n.summary()
            for n in sorted(
                self.considered.values(),
                key=lambda c: (c.depth, c.atom_id),
            )
        ]
        return {
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status,
            "seed_ids": list(self.seed_ids),
            "seed_reasons": list(self.seed_reasons),
            "considered": considered_list,
            "considered_count": len(self.considered),
            "frontier": [f.to_surface() for f in self.frontier],
            "keep_ids": list(self.keep_ids),
            "scratchpad": self.scratchpad,
            "walk_summary_nl": self.walk_summary_nl,
            "budgets": self.budgets.snapshot(),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
            "moment_id": self.moment_id,
            "expand_truncated": self.expand_truncated,
            "edge_kind_counts": dict(self.edge_kind_counts),
            "error_reason": self.error_reason,
            "moment_cache_size": len(self.moment_member_cache),
        }

    def to_thin_surface(self) -> dict[str, Any]:
        """Model-facing thin decision surface (no full considered dump)."""
        return {
            "session_id": self.session_id,
            "status": self.status,
            "goal": self.goal,
            "budget": self.budgets.to_surface(keep_count=len(self.keep_ids)),
            "frontier": [f.to_surface() for f in self.frontier],
            "keep_set": list(self.keep_ids),
            "scratchpad": self.scratchpad,
            "considered_count": len(self.considered),
            "seed_reasons": list(self.seed_reasons),
            "expand_truncated": self.expand_truncated,
            "walk_summary_nl": self.walk_summary_nl,
            "error_reason": self.error_reason,
        }


@dataclass
class GraphSessionView:
    """Prefer active if present else last_session (glass GET contract)."""

    which: Literal["active", "last", "none"]
    session: dict[str, Any] | None
    has_active: bool
    has_last_session: bool
    meal_keep_count: int
    meal_keep_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "which": self.which,
            "session": self.session,
            "has_active": self.has_active,
            "has_last_session": self.has_last_session,
            "meal_keep_count": self.meal_keep_count,
            "meal_keep_ids": list(self.meal_keep_ids),
        }


# ── Pure helpers ────────────────────────────────────────────────────────────


def build_walk_summary_nl(
    session: TraversalSession,
    *,
    summary_hint: str | None = None,
    goal_max: int = 80,
    labels_max: int = 6,
) -> str:
    """Deterministic template NL walk summary (KD-A6)."""
    goal_short = _clip(session.goal, goal_max) or "memories"
    n_considered = len(session.considered)
    n_steps = session.budgets.steps_spent
    seed_kinds = ", ".join(session.seed_reasons) if session.seed_reasons else "none"
    hist = session.edge_kind_counts
    if hist:
        edges = ", ".join(f"{k}={v}" for k, v in sorted(hist.items()))
        edges_clause = f"edges walked: {edges}"
    else:
        # KD-P-glass §5.2: honest zero walked (not "memory has no edges").
        edges_clause = "edges walked: none"
    n_kept = len(session.keep_ids)
    labels: list[str] = []
    for kid in session.keep_ids[:labels_max]:
        node = session.considered.get(kid)
        if node and node.label:
            labels.append(node.label)
        else:
            labels.append(kid[:12])
    kept_part = ", ".join(labels) if labels else "none"
    if n_kept > labels_max:
        kept_part += f" (+{n_kept - labels_max} more)"
    lines = [
        f"I walked through memories about {goal_short}.",
        f"Considered {n_considered} atoms across {n_steps} steps "
        f"(seeds: {seed_kinds}; {edges_clause}).",
        f"Kept {n_kept}: {kept_part}.",
    ]
    hint = (summary_hint or "").strip()
    if hint:
        lines.append(_clip(hint, 240))
    return "\n".join(lines)


def inspect_atoms(
    store: MemoryStore,
    atom_ids: Sequence[str],
    *,
    settings: MemorySettings | None = None,
    chars_per_id: int | None = None,
    max_ids: int | None = None,
    max_total_chars: int | None = None,
) -> list[AtomPreview]:
    """Return capped body slices for mid-walk inspect (KD-A17)."""
    cfg = settings or MemorySettings()
    per = (
        chars_per_id
        if chars_per_id is not None
        else _int_cfg(cfg, "traverse_inspect_chars_per_id", _DEFAULT_INSPECT_CHARS)
    )
    mid = (
        max_ids
        if max_ids is not None
        else _int_cfg(cfg, "traverse_inspect_max_ids", _DEFAULT_INSPECT_MAX_IDS)
    )
    total_cap = (
        max_total_chars
        if max_total_chars is not None
        else _int_cfg(
            cfg, "traverse_inspect_max_total_chars", _DEFAULT_INSPECT_MAX_TOTAL
        )
    )
    mid = max(0, mid)
    per = max(0, per)
    total_cap = max(0, total_cap)
    out: list[AtomPreview] = []
    used = 0
    for raw in list(atom_ids)[:mid]:
        aid = str(raw)
        atom = store.get_atom(aid)
        if atom is None:
            out.append(
                AtomPreview(
                    atom_id=aid,
                    kind=None,
                    body="",
                    error="atom_not_found",
                )
            )
            continue
        body = _atom_body(store, atom)
        room = max(0, total_cap - used)
        take = min(per, room)
        truncated = len(body) > take
        slice_ = body[:take] if take > 0 else ""
        used += len(slice_)
        out.append(
            AtomPreview(
                atom_id=aid,
                kind=atom.kind,
                body=slice_,
                truncated=truncated,
            )
        )
        if used >= total_cap:
            break
    return out


# ── TraversalRegistry (worker-owned) ────────────────────────────────────────


class TraversalRegistry:
    """Process-local session registry: active + last_session + keep tray.

    Single active session (one open moment at a time in the worker). Sticky
    ``last_session`` (glass KD-P-glass §5.1) survives abandon / idle TTL /
    new start **and** moment close (process-life only). Moment close abandons
    active only. Clear last_session via ``reset()``, newer ``finish``, or
    ``clear_confirmed_keep(clear_glass=True)``. Meal directed_keep is owned by
    the registry tray (KD-TRAY-SOT / KD-A16) — survives moment close; reloads
    from disk. Thin ``last_confirmed_keep`` snapshot remains for compat/inspect.
    """

    def __init__(
        self,
        *,
        settings: MemorySettings | None = None,
        now_fn: Callable[[], str] | None = None,
        monotonic_fn: Callable[[], float] | None = None,
        paths: ElyraPaths | Path | None = None,
    ) -> None:
        self._settings = settings or MemorySettings()
        self._now_fn = now_fn or utc_now_iso
        self._monotonic = monotonic_fn or (lambda: time.monotonic())
        self._active: TraversalSession | None = None
        self._last_session: TraversalSession | None = None
        self._last_confirmed_keep: ConfirmedKeepSnapshot | None = None
        # KD-TRAY-SOT: single live tray; lazy-loaded via ensure_tray().
        self._directed_keep_tray: DirectedKeepTray | None = None
        self._tray_paths: ElyraPaths | Path | None = paths

    # -- settings / factories ------------------------------------------------

    def bind_settings(self, settings: MemorySettings) -> None:
        self._settings = settings

    def bind_paths(self, paths: ElyraPaths | Path | None) -> None:
        """Bind data paths for tray load/save (worker construction)."""
        self._tray_paths = paths

    @property
    def settings(self) -> MemorySettings:
        return self._settings

    @property
    def active_session(self) -> TraversalSession | None:
        return self._active

    @property
    def last_session(self) -> TraversalSession | None:
        return self._last_session

    @property
    def last_confirmed_keep(self) -> ConfirmedKeepSnapshot | None:
        return self._last_confirmed_keep

    @property
    def directed_keep_tray(self) -> DirectedKeepTray | None:
        """Live tray if already loaded; None until ensure_tray()."""
        return self._directed_keep_tray

    def enabled(self) -> bool:
        return is_directed_traversal_enabled(self._settings)

    def _tray_policy(self) -> tuple[float, float, int]:
        cfg = self._settings
        hard = float(
            getattr(cfg, "directed_keep_hard_ttl_hours", DEFAULT_HARD_TTL_HOURS)
            or DEFAULT_HARD_TTL_HOURS
        )
        soft = float(
            getattr(cfg, "directed_keep_soft_ttl_hours", DEFAULT_SOFT_TTL_HOURS)
            or DEFAULT_SOFT_TTL_HOURS
        )
        cap = int(
            getattr(cfg, "directed_keep_entry_cap", DEFAULT_ENTRY_CAP)
            or DEFAULT_ENTRY_CAP
        )
        return hard, soft, max(1, cap)

    def ensure_tray(self) -> DirectedKeepTray:
        """Lazy-load tray from disk (or empty); apply hard TTL; return SoT.

        If tray file missing but RAM ``last_confirmed_keep`` present, seed from
        snap (process-local upgrade) then save when paths are bound.
        """
        hard, soft, cap = self._tray_policy()
        now = self._now_fn()
        if self._directed_keep_tray is not None:
            tray = self._directed_keep_tray
            tray.max_age_hard_hours = hard
            tray.soft_evict_after_hours = soft
            tray.entry_cap = cap
            tray.drop_hard_ttl(now=now, hard_hours=hard)
            return tray

        tray = load_directed_keep_tray(self._tray_paths)
        tray.max_age_hard_hours = hard
        tray.soft_evict_after_hours = soft
        tray.entry_cap = cap

        # Migration: seed from thin snap when file empty (process upgrade).
        if not tray.entries and self._last_confirmed_keep is not None:
            snap = self._last_confirmed_keep
            tray = seed_tray_from_keep_ids(
                list(snap.keep_ids),
                now=snap.finished_at or now,
                session_id=snap.session_id,
                moment_id=snap.moment_id,
                walk_summary_nl=snap.walk_summary_nl or None,
                hard_hours=hard,
                soft_hours=soft,
                entry_cap=cap,
            )
            try:
                save_directed_keep_tray(tray, paths=self._tray_paths)
            except Exception:  # noqa: BLE001
                _LOG.exception("seed save directed_keep_tray failed")

        tray.drop_hard_ttl(now=now, hard_hours=hard)
        self._directed_keep_tray = tray
        return tray

    def get_meal_keep_ids(
        self,
    ) -> tuple[list[str], str | None]:
        """Meal path: tray ids + summary. No open-moment equality (B5b)."""
        hard, soft, _cap = self._tray_policy()
        tray = self.ensure_tray()
        ids, summary, _soft = tray.meal_keep_ids(
            now=self._now_fn(), hard_hours=hard, soft_hours=soft
        )
        return ids, summary

    def get_tray_inspect(self) -> dict[str, Any]:
        """Tray ages / entries for context inspect (via registry SoT)."""
        tray = self.ensure_tray()
        return tray.inspect_block(now=self._now_fn())

    # -- queries -------------------------------------------------------------

    def get_traversal(self, session_id: str) -> TraversalSession | None:
        if self._active is not None and self._active.session_id == session_id:
            return self._active
        if (
            self._last_session is not None
            and self._last_session.session_id == session_id
        ):
            return self._last_session
        return None

    def get_last_confirmed_keep(
        self, moment_id: str | None = None
    ) -> ConfirmedKeepSnapshot | None:
        snap = self._last_confirmed_keep
        if snap is None:
            return None
        if moment_id is not None and snap.moment_id not in (None, moment_id):
            return None
        return snap

    def get_last_session(
        self, moment_id: str | None = None
    ) -> TraversalSession | None:
        sess = self._last_session
        if sess is None:
            return None
        if moment_id is not None and sess.moment_id not in (None, moment_id):
            return None
        return sess

    def get_graph_session_view(
        self, moment_id: str | None = None, *, which: str | None = None
    ) -> GraphSessionView:
        """Prefer active if present else last_session (glass GET)."""
        # Meal keep ids from tray (B5b-free) — not snap.moment_id filter.
        try:
            meal_ids, _summary = self.get_meal_keep_ids()
        except Exception:  # noqa: BLE001
            meal = self.get_last_confirmed_keep(moment_id)
            meal_ids = list(meal.keep_ids) if meal else []
        active = self._active
        last = self.get_last_session(moment_id)
        has_active = active is not None
        has_last = last is not None

        want = (which or "").strip().lower()
        if want == "meal":
            return GraphSessionView(
                which="none",
                session=None,
                has_active=has_active,
                has_last_session=has_last,
                meal_keep_count=len(meal_ids),
                meal_keep_ids=meal_ids,
            )
        if want == "last":
            sess = last
            return GraphSessionView(
                which="last" if sess else "none",
                session=sess.to_view() if sess else None,
                has_active=has_active,
                has_last_session=has_last,
                meal_keep_count=len(meal_ids),
                meal_keep_ids=meal_ids,
            )
        if want == "active":
            sess = active
            return GraphSessionView(
                which="active" if sess else "none",
                session=sess.to_view() if sess else None,
                has_active=has_active,
                has_last_session=has_last,
                meal_keep_count=len(meal_ids),
                meal_keep_ids=meal_ids,
            )
        # Default: active else last.
        if active is not None:
            return GraphSessionView(
                which="active",
                session=active.to_view(),
                has_active=True,
                has_last_session=has_last,
                meal_keep_count=len(meal_ids),
                meal_keep_ids=meal_ids,
            )
        if last is not None:
            return GraphSessionView(
                which="last",
                session=last.to_view(),
                has_active=False,
                has_last_session=True,
                meal_keep_count=len(meal_ids),
                meal_keep_ids=meal_ids,
            )
        return GraphSessionView(
            which="none",
            session=None,
            has_active=False,
            has_last_session=False,
            meal_keep_count=len(meal_ids),
            meal_keep_ids=meal_ids,
        )

    # -- lifecycle -----------------------------------------------------------

    def start(
        self,
        graph: GraphView,
        *,
        goal: str,
        seed_query: str | None = None,
        seed_atom_ids: Sequence[str] | None = None,
        seed_media_ids: Sequence[str] | None = None,
        seed_mode: str | None = None,
        moment_id: str | None = None,
        budget_overrides: Mapping[str, int] | None = None,
        include_noisy_kinds: bool = False,
    ) -> dict[str, Any]:
        """Create an active session; abandon any previous active only.

        Retains ``last_confirmed_keep`` and ``last_session`` (KD-A9 / KD-A19).
        Flags-off → ``error_reason=traverse_disabled`` without mutating sticky.

        **Seed modes (PR5 / #103 / #105 seed half):**
        - ``auto`` (default): semantic with dual temporal slot reserve; strip
          fill when semantic empty.
        - ``semantic_only``: pure semantic (text and/or media); never temporal.
        - ``temporal_only`` (alias ``temporal``): temporal strip only.
        - ``explicit_only``: only ``seed_atom_ids``; no semantic/temporal fill.

        Dual start reserves ``dual_n`` slots **before** semantic fill so
        temporal anchors are not starved when ANN returns a full top-k.
        Start never cold-loads the encoder (GraphView ``encoder_cold`` honesty).

        Host-assembled ``local_map`` (KD-P2) is built for the primary seed under
        remaining structural start budget; ANN is not re-run for the map.
        """
        if not self.enabled():
            return {
                "ok": False,
                "error_reason": ERROR_TRAVERSE_DISABLED,
                "status": "disabled",
            }

        now = self._now_fn()
        # Abandon previous active only — sticky snapshots stay.
        if self._active is not None and self._active.status == "active":
            self._drop_active(status="abandoned", now=now)

        cfg = self._settings
        label_n = _int_cfg(cfg, "traverse_label_chars", _DEFAULT_LABEL_CHARS)
        preview_n = _int_cfg(cfg, "traverse_preview_chars", _DEFAULT_PREVIEW_CHARS)
        max_seeds = _int_cfg(cfg, "traverse_max_seeds", _DEFAULT_MAX_SEEDS)
        max_nodes = _int_cfg(cfg, "traverse_max_nodes", _DEFAULT_MAX_NODES)
        max_depth = _int_cfg(cfg, "traverse_max_depth", _DEFAULT_MAX_DEPTH)
        max_steps = _int_cfg(cfg, "traverse_max_steps", _DEFAULT_MAX_STEPS)
        max_keep = _int_cfg(cfg, "traverse_keep_max", _DEFAULT_KEEP_MAX)
        expand_ms = _int_cfg(cfg, "traverse_expand_max_ms", _DEFAULT_EXPAND_MS)
        start_ms = _int_cfg(
            cfg, "traverse_start_expand_max_ms", _DEFAULT_START_EXPAND_MS
        )
        if start_ms <= 0:
            start_ms = expand_ms
        frontier_max = _int_cfg(cfg, "traverse_frontier_max", _DEFAULT_FRONTIER_MAX)

        # Normalize seed_mode.
        raw_mode = (
            seed_mode
            if seed_mode is not None
            else getattr(cfg, "traverse_default_seed_mode", None)
        )
        mode = str(raw_mode or "auto").strip().lower() or "auto"
        if mode not in _SEED_MODES:
            mode = "auto"
        if mode == "temporal":
            mode = "temporal_only"

        dual_enabled = _bool_cfg(cfg, "traverse_dual_start", True)
        dual_n_cfg = _int_cfg(cfg, "traverse_dual_start_n", _DEFAULT_DUAL_START_N)
        dual_n = max(0, min(4, dual_n_cfg)) if dual_enabled and mode == "auto" else 0

        expand_per = _int_cfg(
            cfg, "traverse_max_expand_per_step", _DEFAULT_EXPAND_PER_STEP
        )
        neighbor_k = _int_cfg(cfg, "traverse_neighbor_k", _DEFAULT_NEIGHBOR_K)

        # PR6 §5.4: clamp(request or product_default, 1, HARD_MAX) — NOT
        # min(product_default, request). Tools may raise above product default.
        ov = budget_overrides or {}

        def _ov(key: str) -> int | None:
            if key not in ov or ov[key] is None:
                return None
            try:
                return int(ov[key])  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None

        max_steps = clamp_budget(_ov("max_steps"), max_steps, TRAVERSE_MAX_STEPS_MAX)
        max_nodes = clamp_budget(_ov("max_nodes"), max_nodes, TRAVERSE_MAX_NODES_MAX)
        max_depth = clamp_budget(_ov("max_depth"), max_depth, TRAVERSE_MAX_DEPTH_MAX)
        max_keep = clamp_budget(_ov("max_keep"), max_keep, TRAVERSE_KEEP_MAX_MAX)
        frontier_max = clamp_budget(
            _ov("frontier_max"), frontier_max, TRAVERSE_FRONTIER_MAX_MAX
        )
        expand_per = clamp_budget(
            _ov("max_expand_per_step"), expand_per, TRAVERSE_MAX_EXPAND_PER_STEP_MAX
        )
        neighbor_k = clamp_budget(
            _ov("neighbor_k"), neighbor_k, TRAVERSE_NEIGHBOR_K_MAX
        )

        budgets = BudgetState(
            max_steps=max_steps,
            max_nodes=max_nodes,
            max_depth=max_depth,
            max_keep=max_keep,
            expand_ms_budget=expand_ms,
            frontier_max=frontier_max,
            max_expand_per_step=expand_per,
            neighbor_k=neighbor_k,
        )

        session = TraversalSession(
            session_id=_new_session_id(),
            goal=(goal or "").strip() or "explore",
            status="active",
            seed_ids=(),
            considered={},
            frontier=[],
            keep_ids=[],
            scratchpad="",
            budgets=budgets,
            created_at=now,
            updated_at=now,
            moment_id=moment_id,
        )

        store = graph._store  # noqa: SLF001 — same process GraphView
        seed_order: list[str] = []
        seed_reason_tags: list[str] = []
        seed_sources = {"explicit": 0, "semantic": 0, "temporal": 0}
        expand_truncated = False
        expand_ms_spent = 0
        semantic_reason: str | None = None
        semantic_hits = 0

        def _add_seed(
            aid: str,
            atom: Atom,
            *,
            source: str,
            via_edge_kind: str | None,
            via_reason: str,
            weight: float,
        ) -> bool:
            if len(seed_order) >= max_seeds:
                return False
            if aid in session.considered:
                return False
            self._add_considered(
                session,
                store,
                atom,
                via_edge_kind=via_edge_kind,
                via_reason=via_reason,
                depth=0,
                weight=float(weight),
                parent_id=None,
                label_n=label_n,
                preview_n=preview_n,
            )
            seed_order.append(aid)
            seed_sources[source] = seed_sources.get(source, 0) + 1
            if source not in seed_reason_tags:
                seed_reason_tags.append(source)
            return True

        # 1) Explicit seeds (point lookups — free of expand_ms; free of dual reserve).
        for raw in seed_atom_ids or ():
            if len(seed_order) >= max_seeds:
                break
            aid = str(raw)
            atom = store.get_atom(aid)
            if atom is None:
                continue
            _add_seed(
                aid,
                atom,
                source="explicit",
                via_edge_kind=None,
                via_reason="seed:explicit",
                weight=1.0,
            )

        # 2) Semantic seed_from_query under unified wait / snappy ANN ceiling
        # (KD-P0: start_ms is structural/reporting only — NOT the ANN cap).
        want_semantic = mode in ("auto", "semantic_only")
        media_list = [str(m) for m in (seed_media_ids or ()) if str(m).strip()]
        q = ""
        if seed_query is not None:
            q = str(seed_query).strip()
        elif want_semantic:
            q = session.goal.strip()

        # Wait-on: effective_semantic_wait_max_ms; wait-off: snappy traverse.
        # Settings already overlay runtime wait via worker _memory_settings_with_wait.
        semantic_deadline = semantic_ann_deadline_ms(cfg, "traverse")
        session.budgets.semantic_ms_budget_step = int(semantic_deadline)
        semantic_ms_spent = 0

        if want_semantic and (q or media_list) and len(seed_order) < max_seeds:
            # RESERVE dual_n slots so temporal anchors cannot be starved.
            semantic_room = max_seeds - len(seed_order) - dual_n
            if semantic_room > 0:
                t0 = _now_ms()
                hits = graph.seed_from_query(
                    q or None,
                    media_ids=media_list or None,
                    k=semantic_room,
                    exclude_moment_id=moment_id,
                    expand_deadline_ms=start_ms,
                    semantic_deadline_ms=semantic_deadline,
                )
                expand_ms_spent = int(_now_ms() - t0)
                semantic_ms_spent = expand_ms_spent
                meta = graph.last_expand_meta
                if meta.get("expand_truncated") or meta.get("semantic_truncated"):
                    expand_truncated = True
                sem_reason = meta.get("semantic_reason")
                if sem_reason:
                    semantic_reason = str(sem_reason)
                # Ceiling leaves dual_n slots empty for temporal anchors.
                semantic_ceiling = max_seeds - dual_n
                if hits:
                    for aid, score, reason in hits:
                        if len(seed_order) >= semantic_ceiling:
                            break
                        if seed_sources["semantic"] >= semantic_room:
                            break
                        atom = store.get_atom(aid)
                        if atom is None:
                            continue
                        if _add_seed(
                            aid,
                            atom,
                            source="semantic",
                            via_edge_kind="semantic_hop",
                            via_reason=reason,
                            weight=float(score),
                        ):
                            semantic_hits += 1
                elif semantic_reason:
                    if semantic_reason not in seed_reason_tags:
                        seed_reason_tags.append(semantic_reason)
                if expand_truncated and "expand_truncated" not in seed_reason_tags:
                    seed_reason_tags.append("expand_truncated")

        # 3) Temporal: dual anchors when semantic non-empty; strip fill when empty.
        def _fill_temporal(*, cap: int) -> int:
            if cap <= 0 or len(seed_order) >= max_seeds:
                return 0
            around = seed_order[0] if seed_order else None
            if around is None:
                if moment_id:
                    tail = store.moment_tail(moment_id)
                else:
                    tail = store.global_tail()
                around = tail.atom_id if tail is not None else None
            temporal = graph.seed_temporal(
                around_atom_id=around,
                moment_id=moment_id,
                k=cap + len(seed_order),
            )
            added = 0
            for aid, score, reason in temporal:
                if added >= cap or len(seed_order) >= max_seeds:
                    break
                atom = store.get_atom(aid)
                if atom is None:
                    continue
                if _add_seed(
                    aid,
                    atom,
                    source="temporal",
                    via_edge_kind=None,
                    via_reason=reason,
                    weight=float(score),
                ):
                    added += 1
            return added

        if mode == "semantic_only":
            # Pure semantic — empty frontier is OK (never temporal fill).
            pass
        elif mode == "explicit_only":
            pass
        elif mode == "temporal_only":
            _fill_temporal(cap=max_seeds - len(seed_order))
        elif mode == "auto":
            if dual_n > 0 and semantic_hits >= 1:
                # Reserved dual temporal anchors (not full strip).
                _fill_temporal(cap=min(dual_n, max_seeds - len(seed_order)))
            elif semantic_hits == 0:
                # Collapse path: full temporal strip fill — honest tags.
                _fill_temporal(cap=max_seeds - len(seed_order))

        session.seed_ids = tuple(seed_order)
        session.seed_reasons = seed_reason_tags
        session.expand_truncated = expand_truncated
        session.budgets.expand_ms_spent_last = expand_ms_spent
        session.budgets.expand_truncated = expand_truncated
        session.budgets.semantic_ms_spent_last = semantic_ms_spent
        # ANN call counted when seed_from_query was invoked (spent or reason set).
        session.budgets.semantic_ann_calls_last = (
            1 if (semantic_ms_spent > 0 or bool(semantic_reason)) else 0
        )
        session.budgets.nodes_spent = len(session.considered)
        session.budgets.depth_spent = 0

        # Frontier = seeds ranked by weight.
        self._rebuild_frontier(session, frontier_max=frontier_max)
        self._active = session

        # KD-P2: host local_map for primary focus (first seed). No re-ANN.
        # remaining_struct=0 → truncated focus-only map (never GraphView unlimited).
        local_map: dict[str, Any] | None = None
        map_enabled = _bool_cfg(cfg, "traverse_local_map_enabled", True)
        if map_enabled and seed_order:
            primary_focus = seed_order[0]
            remaining_struct: int | None
            if start_ms > 0:
                remaining_struct = max(0, int(start_ms) - int(expand_ms_spent))
            else:
                remaining_struct = expand_ms if expand_ms > 0 else None
            # Associative extras: other semantic seeds already computed this start.
            assoc_extra: list[dict[str, Any]] = []
            for sid in seed_order[1:]:
                node = session.considered.get(sid)
                if node is None:
                    continue
                if node.via_edge_kind == "semantic_hop" or (
                    node.via_reason or ""
                ).startswith("semantic"):
                    assoc_extra.append(
                        {
                            "atom_id": sid,
                            "weight": float(node.weight or 0.0),
                            "edge_kind": "semantic_hop",
                        }
                    )
            try:
                local_map = build_local_map(
                    graph,
                    primary_focus,
                    include_noisy=bool(include_noisy_kinds),
                    expand_deadline_ms=remaining_struct,
                    label_n=label_n,
                    preview_n=preview_n,
                    neighbor_k=neighbor_k,
                    associative_extra=assoc_extra or None,
                )
            except Exception:  # noqa: BLE001 — map failure must not kill start
                _LOG.debug("local_map build failed on start", exc_info=True)
                local_map = None

        view = session.to_thin_surface()
        view["ok"] = True
        view["seed_ids"] = list(session.seed_ids)
        view["seed_sources"] = dict(seed_sources)
        view["seed_mode"] = mode
        view["dual_n"] = dual_n
        view["semantic_reason"] = semantic_reason
        view["start_ms_budget"] = start_ms
        view["start_ms_spent"] = expand_ms_spent
        view["semantic_ms_budget"] = int(semantic_deadline)
        view["semantic_ms_spent"] = semantic_ms_spent
        view["local_map"] = local_map
        view["local_maps"] = None
        return view

    def step(
        self,
        graph: GraphView,
        *,
        session_id: str | None = None,
        expand_ids: Sequence[str] | None = None,
        keep_ids: Sequence[str] | None = None,
        scratchpad: str | None = None,
        include_noisy_kinds: bool = False,
    ) -> dict[str, Any]:
        """One tool step: expand selected frontier nodes + optional keep.

        When focus moves (expand_ids processed), attach host ``local_map`` for
        the first successfully expanded id and optional ``local_maps`` (≤3).
        """
        if not self.enabled():
            return {
                "ok": False,
                "error_reason": ERROR_TRAVERSE_DISABLED,
                "status": "disabled",
            }
        session = self._require_active(session_id)
        if isinstance(session, dict):
            return session

        now = self._now_fn()
        session.touch(now)
        cfg = self._settings
        label_n = _int_cfg(cfg, "traverse_label_chars", _DEFAULT_LABEL_CHARS)
        preview_n = _int_cfg(cfg, "traverse_preview_chars", _DEFAULT_PREVIEW_CHARS)
        # Prefer session-scoped budgets (set at start with HARD_MAX clamp).
        expand_per = session.budgets.max_expand_per_step
        frontier_max = session.budgets.frontier_max
        neighbor_k = session.budgets.neighbor_k
        expand_ms = session.budgets.expand_ms_budget
        scratch_n = _int_cfg(cfg, "traverse_scratchpad_chars", _DEFAULT_SCRATCHPAD)

        if scratchpad is not None:
            session.scratchpad = _clip(scratchpad, scratch_n)

        # Provisional keeps (must be considered).
        if keep_ids:
            self._merge_keeps(session, keep_ids)

        # Budget gate: no expand if steps/nodes/depth exhausted.
        can_expand = (
            session.budgets.steps_remaining > 0
            and session.budgets.nodes_remaining > 0
            and session.budgets.depth_remaining > 0
        )
        store = graph._store  # noqa: SLF001
        expand_truncated = False
        expand_ms_spent = 0
        newly: list[str] = []
        # KD-P0-step-ann: one shared semantic ANN budget per step; at most one
        # semantic_hop call (first expand_id that still has ANN budget).
        # Structural multi-id expand is charged ONLY against expand_ms_budget;
        # ANN wait time must NOT empty packing or starve further expand_ids.
        step_semantic_budget = int(semantic_ann_deadline_ms(cfg, "traverse"))
        session.budgets.semantic_ms_budget_step = step_semantic_budget
        ann_calls_this_step = 0
        semantic_ms_spent = 0
        struct_spent_total = 0  # dual-deadline structural accounting only

        def _pack_edges(src_id: str, next_depth: int, edges: list[Any]) -> None:
            """Accept returned edges (nodes/depth caps only — no expand_ms wall)."""
            nonlocal newly
            for e in edges:
                if session.budgets.nodes_remaining <= 0:
                    break
                if e.dst_atom_id in session.considered:
                    continue
                atom = store.get_atom(e.dst_atom_id)
                if atom is None:
                    continue
                self._add_considered(
                    session,
                    store,
                    atom,
                    via_edge_kind=e.edge_kind,
                    via_reason=e.reason,
                    depth=next_depth,
                    weight=e.weight,
                    parent_id=src_id,
                    label_n=label_n,
                    preview_n=preview_n,
                )
                newly.append(e.dst_atom_id)
                session.edge_kind_counts[e.edge_kind] = (
                    session.edge_kind_counts.get(e.edge_kind, 0) + 1
                )
                if next_depth > session.budgets.depth_spent:
                    session.budgets.depth_spent = next_depth

        # Focuses we successfully ran expand on (for local_map / local_maps).
        expanded_focus_ids: list[str] = []
        # Semantic/recalls hits from this step's first ANN (associative compass).
        step_assoc_by_focus: dict[str, list[dict[str, Any]]] = {}
        # Phase-A structural edges per focus — reused for map d1 (Issue 2).
        step_struct_edges_by_focus: dict[str, list[Any]] = {}

        if can_expand and expand_ids:
            picks = [str(x) for x in expand_ids][: max(0, expand_per)]
            struct_budget = float(expand_ms) if expand_ms > 0 else None
            session.budgets.steps_spent += 1

            for src_id in picks:
                if session.budgets.nodes_remaining <= 0:
                    break
                # Continue multi-id structural expand using structural spend only
                # (never wall-clock including ANN wait).
                if struct_budget is not None and struct_spent_total >= struct_budget:
                    expand_truncated = True
                    break
                src_node = session.considered.get(src_id)
                if src_node is None:
                    continue
                # Depth of destinations = parent depth + 1.
                next_depth = src_node.depth + 1
                if next_depth > session.budgets.max_depth:
                    continue
                remaining_struct: int | None
                if struct_budget is not None:
                    remaining_struct = max(
                        0, int(struct_budget - struct_spent_total)
                    )
                    if remaining_struct <= 0:
                        expand_truncated = True
                        break
                else:
                    remaining_struct = None  # no structural soft wall

                # Phase A: structural-only under remaining expand_ms.
                # Note: remaining_struct==0 never reaches here (break above).
                edges_struct = graph.neighbors(
                    src_id,
                    k=neighbor_k,
                    exclude_ids=set(session.considered.keys()),
                    allow_semantic=False,
                    expand_deadline_ms=remaining_struct,
                    semantic_deadline_ms=0,
                )
                self._sync_moment_cache(session, graph)
                meta_struct = graph.last_expand_meta
                struct_spent_total += int(
                    meta_struct.get("structural_ms_spent") or 0
                )
                if meta_struct.get("structural_truncated") or meta_struct.get(
                    "expand_truncated"
                ):
                    expand_truncated = True
                _pack_edges(src_id, next_depth, edges_struct)
                expanded_focus_ids.append(src_id)
                step_struct_edges_by_focus[src_id] = list(edges_struct)

                # Phase B: at most one semantic_hop under independent ANN budget.
                if (
                    ann_calls_this_step == 0
                    and step_semantic_budget > 0
                    and session.budgets.nodes_remaining > 0
                ):
                    edges_sem = graph.neighbors(
                        src_id,
                        kinds=["semantic_hop"],
                        k=neighbor_k,
                        exclude_ids=set(session.considered.keys()),
                        allow_semantic=True,
                        expand_deadline_ms=0,  # no structural work this pass
                        semantic_deadline_ms=step_semantic_budget,
                    )
                    ann_calls_this_step += 1
                    meta_sem = graph.last_expand_meta
                    semantic_ms_spent += int(
                        meta_sem.get("semantic_ms_spent") or 0
                    )
                    # Shared budget consumed (not reused for further ids).
                    step_semantic_budget = 0
                    # Pack ANN results always — do not apply expand_ms wall.
                    # semantic_timeout is honesty only, not discard.
                    _pack_edges(src_id, next_depth, edges_sem)
                    if edges_sem:
                        step_assoc_by_focus[src_id] = [
                            {
                                "atom_id": e.dst_atom_id,
                                "weight": float(e.weight),
                                "edge_kind": e.edge_kind,
                            }
                            for e in edges_sem
                        ]

            # expand_ms honesty: structural spend only (ANN is separate budget).
            expand_ms_spent = int(struct_spent_total)
        elif not can_expand and expand_ids:
            # Still count a step attempt? Design: exceed caps → stop further
            # expand; finish with partial keep still allowed. Do not increment
            # steps when we refuse expand due to budget (surface remaining 0).
            pass

        session.budgets.nodes_spent = len(session.considered)
        session.budgets.expand_ms_spent_last = expand_ms_spent
        session.budgets.expand_truncated = expand_truncated
        session.budgets.semantic_ms_spent_last = semantic_ms_spent
        session.budgets.semantic_ann_calls_last = ann_calls_this_step
        session.expand_truncated = expand_truncated or session.expand_truncated

        self._rebuild_frontier(
            session, frontier_max=frontier_max, prefer_new=newly
        )

        # KD-P2: local_map for first expanded focus; local_maps when multi.
        # Shared remaining structural budget across maps (Issue 2) — do not
        # re-spend a full expand_ms per focus. Reuse Phase-A edges for d1.
        local_map: dict[str, Any] | None = None
        local_maps: list[dict[str, Any]] | None = None
        map_enabled = _bool_cfg(cfg, "traverse_local_map_enabled", True)
        if map_enabled and expanded_focus_ids:
            if expand_ms > 0:
                map_remaining: int | None = max(
                    0, int(expand_ms) - int(expand_ms_spent)
                )
            else:
                map_remaining = None
            built: list[dict[str, Any]] = []
            for fid in expanded_focus_ids[:LOCAL_MAPS_STEP_CAP]:
                deadline = map_remaining
                # 0 remaining → truncated (prefetched d1 only, no d2 / no re-expand).
                try:
                    m = build_local_map(
                        graph,
                        fid,
                        include_noisy=bool(include_noisy_kinds),
                        expand_deadline_ms=deadline,
                        label_n=label_n,
                        preview_n=preview_n,
                        neighbor_k=neighbor_k,
                        associative_extra=step_assoc_by_focus.get(fid),
                        prefetched_edges=step_struct_edges_by_focus.get(fid),
                    )
                except Exception:  # noqa: BLE001
                    _LOG.debug(
                        "local_map build failed on step focus=%s",
                        fid,
                        exc_info=True,
                    )
                    m = None
                if m is not None:
                    built.append({"focus_id": fid, "map": m})
                    if map_remaining is not None:
                        spent_map = int(
                            (m.get("meta") or {}).get("structural_ms_spent") or 0
                        )
                        map_remaining = max(0, int(map_remaining) - spent_map)
            if built:
                local_map = built[0]["map"]
                if len(built) > 1:
                    local_maps = built

        view = session.to_thin_surface()
        view["ok"] = True
        view["newly_expanded"] = newly
        view["local_map"] = local_map
        view["local_maps"] = local_maps
        return view

    def finish(
        self,
        graph: GraphView | None = None,
        *,
        session_id: str | None = None,
        keep_ids: Sequence[str] | None = None,
        summary_hint: str | None = None,
    ) -> dict[str, Any]:
        """Confirm active session → last_session + last_confirmed_keep."""
        if not self.enabled():
            return {
                "ok": False,
                "error_reason": ERROR_TRAVERSE_DISABLED,
                "status": "disabled",
            }
        session = self._require_active(session_id)
        if isinstance(session, dict):
            return session

        now = self._now_fn()
        session.touch(now)
        if keep_ids is not None:
            # Replace keep-set with final list (still must be considered).
            session.keep_ids = []
            self._merge_keeps(session, keep_ids)

        # Optional sequential ±1 adjacent expand of keep set.
        if (
            graph is not None
            and _bool_cfg(self._settings, "traverse_keep_adjacent", True)
        ):
            self._keep_adjacent(session, graph)

        session.status = "confirmed"
        session.finished_at = now
        session.walk_summary_nl = build_walk_summary_nl(
            session, summary_hint=summary_hint
        )
        session.frontier = []  # freeze / empty frontier on confirm

        # Dual snapshot (KD-A9 + KD-A19) + sticky tray merge (S3 / KD-MRG).
        frozen = copy.deepcopy(session)
        self._last_session = frozen
        self._last_confirmed_keep = ConfirmedKeepSnapshot(
            session_id=session.session_id,
            goal=session.goal,
            keep_ids=tuple(session.keep_ids),
            walk_summary_nl=session.walk_summary_nl or "",
            finished_at=now,
            moment_id=session.moment_id,
        )
        # Registry-owned tray: merge-on-confirm default (union under cap/TTL).
        hard, soft, cap = self._tray_policy()
        tray = self.ensure_tray()
        tray.merge_confirm(
            list(session.keep_ids),
            now=now,
            session_id=session.session_id,
            moment_id=session.moment_id,
            walk_summary_nl=session.walk_summary_nl,
            hard_hours=hard,
            soft_hours=soft,
            entry_cap=cap,
        )
        try:
            save_directed_keep_tray(tray, paths=self._tray_paths)
        except Exception:  # noqa: BLE001
            _LOG.exception("save directed_keep_tray after finish failed")
        self._active = None

        view = frozen.to_view()
        view["ok"] = True
        view["keep_set"] = list(frozen.keep_ids)
        view["thin_surface"] = frozen.to_thin_surface()
        view["tray_entry_count"] = len(tray.entries)
        return view

    def abandon(
        self,
        *,
        session_id: str | None = None,
        reason: str = "abandoned",
    ) -> dict[str, Any]:
        """Abandon active only — sticky last_session + last_confirmed retained."""
        if not self.enabled():
            return {
                "ok": False,
                "error_reason": ERROR_TRAVERSE_DISABLED,
                "status": "disabled",
            }
        session = self._require_active(session_id)
        if isinstance(session, dict):
            return session

        now = self._now_fn()
        status: SessionStatus = (
            "timed_out" if reason in ("timed_out", "idle_ttl", "timeout") else "abandoned"
        )
        dropped = self._drop_active(status=status, now=now)
        view = dropped.to_view() if dropped else {"status": status}
        view["ok"] = True
        view["last_session_retained"] = self._last_session is not None
        view["last_confirmed_retained"] = self._last_confirmed_keep is not None
        return view

    def clear_confirmed_keep(
        self,
        *,
        moment_id: str | None = None,
        clear_glass: bool = False,
    ) -> dict[str, Any]:
        """Clear meal thin snapshot + tray; optional clear_glass drops last_session."""
        snap = self._last_confirmed_keep
        if moment_id is not None and snap is not None:
            if snap.moment_id not in (None, moment_id):
                return {"ok": True, "cleared_keep": False, "cleared_glass": False}
        self._last_confirmed_keep = None
        # Operator clear: wipe live tray and persist empty (meal path uses tray).
        empty = DirectedKeepTray()
        hard, soft, cap = self._tray_policy()
        empty.max_age_hard_hours = hard
        empty.soft_evict_after_hours = soft
        empty.entry_cap = cap
        self._directed_keep_tray = empty
        try:
            save_directed_keep_tray(empty, paths=self._tray_paths)
        except Exception:  # noqa: BLE001
            _LOG.exception("save empty directed_keep_tray after clear failed")
        cleared_glass = False
        if clear_glass:
            if moment_id is None or (
                self._last_session is not None
                and self._last_session.moment_id in (None, moment_id)
            ):
                self._last_session = None
                cleared_glass = True
        return {
            "ok": True,
            "cleared_keep": True,
            "cleared_glass": cleared_glass,
        }

    def sweep_idle(self, *, now: str | None = None) -> TraversalSession | None:
        """Abandon active if idle longer than traverse_session_ttl_s.

        Does not touch last_confirmed_keep or last_session.
        Returns the dropped session if any.
        """
        active = self._active
        if active is None or active.status != "active":
            return None
        ttl = _int_cfg(self._settings, "traverse_session_ttl_s", _DEFAULT_TTL_S)
        if ttl <= 0:
            return None
        try:
            updated = parse_iso_z(active.updated_at)
            now_dt = parse_iso_z(now or self._now_fn())
        except (TypeError, ValueError):
            return None
        age_s = (now_dt - updated).total_seconds()
        if age_s < ttl:
            return None
        return self._drop_active(
            status="timed_out", now=to_iso_z(now_dt) if now is None else (now or self._now_fn())
        )

    def on_moment_close(self, moment_id: str | None = None) -> None:
        """Moment end: abandon active only; retain glass last_session (KD-P-glass).

        Retains:
        - registry tray + meal-thin last_confirmed_keep (B5 / KD-A16 meal path)
        - ``_last_session`` process-life sticky walk for Graph GET (polish1 §5.1)

        Clear ``_last_session`` only via ``reset()``, newer ``finish`` replace,
        process death, or ``clear_confirmed_keep(clear_glass=True)``.
        """
        now = self._now_fn()
        if self._active is not None:
            if moment_id is None or self._active.moment_id in (None, moment_id):
                self._drop_active(status="abandoned", now=now)
        # B5: do NOT clear _directed_keep_tray or last_confirmed_keep.
        # KD-P-glass: do NOT clear _last_session (process-life stickiness).

    def reset(self) -> None:
        """Process RAM reset: clear sessions + snap + tray RAM (file survives)."""
        self._active = None
        self._last_session = None
        self._last_confirmed_keep = None
        # KD-TRAY-LOAD: drop RAM tray; next ensure_tray reloads from disk.
        # Do NOT delete directed_keep_tray.json.
        self._directed_keep_tray = None

    # -- internals -----------------------------------------------------------

    def _require_active(
        self, session_id: str | None
    ) -> TraversalSession | dict[str, Any]:
        active = self._active
        if active is None or active.status != "active":
            return {
                "ok": False,
                "error_reason": ERROR_NO_ACTIVE,
                "status": "none",
            }
        if session_id is not None and active.session_id != session_id:
            return {
                "ok": False,
                "error_reason": ERROR_UNKNOWN_SESSION,
                "status": active.status,
                "session_id": active.session_id,
            }
        return active

    def _drop_active(
        self, *, status: SessionStatus, now: str
    ) -> TraversalSession | None:
        active = self._active
        if active is None:
            return None
        active.status = status
        active.finished_at = now
        active.updated_at = now
        active.keep_ids = []  # provisional keep discarded
        active.walk_summary_nl = None
        self._active = None
        # v1: do not promote abandoned to last_session (design: optional).
        return active

    def _add_considered(
        self,
        session: TraversalSession,
        store: MemoryStore,
        atom: Atom,
        *,
        via_edge_kind: str | None,
        via_reason: str | None,
        depth: int,
        weight: float | None,
        parent_id: str | None,
        label_n: int,
        preview_n: int,
    ) -> None:
        if atom.atom_id in session.considered:
            return
        if len(session.considered) >= session.budgets.max_nodes:
            return
        body = _atom_body(store, atom)
        node = ConsideredNode(
            atom_id=atom.atom_id,
            kind=atom.kind,
            label=_clip(body, label_n),
            preview=_clip(body, preview_n),
            via_edge_kind=via_edge_kind,
            via_reason=via_reason,
            depth=depth,
            weight=weight,
            parent_id=parent_id,
        )
        session.considered[atom.atom_id] = node

    def _merge_keeps(
        self, session: TraversalSession, keep_ids: Sequence[str]
    ) -> None:
        max_keep = session.budgets.max_keep
        for raw in keep_ids:
            aid = str(raw)
            if aid in session.keep_ids:
                continue
            if aid not in session.considered:
                continue
            if len(session.keep_ids) >= max_keep:
                break
            session.keep_ids.append(aid)

    def _keep_adjacent(self, session: TraversalSession, graph: GraphView) -> None:
        """Expand keep-set with sequential ±1 neighbours still under keep_max."""
        store = graph._store  # noqa: SLF001
        max_keep = session.budgets.max_keep
        extras: list[str] = []
        for kid in list(session.keep_ids):
            atom = store.get_atom(kid)
            if atom is None:
                continue
            for peer_id in (atom.prev_atom_id, atom.next_atom_id):
                if not peer_id or peer_id in session.keep_ids or peer_id in extras:
                    continue
                peer = store.get_atom(peer_id)
                if peer is None:
                    continue
                # Ensure considered for summary labels.
                if peer_id not in session.considered:
                    if len(session.considered) < session.budgets.max_nodes:
                        label_n = _int_cfg(
                            self._settings,
                            "traverse_label_chars",
                            _DEFAULT_LABEL_CHARS,
                        )
                        preview_n = _int_cfg(
                            self._settings,
                            "traverse_preview_chars",
                            _DEFAULT_PREVIEW_CHARS,
                        )
                        parent_node = session.considered.get(kid)
                        self._add_considered(
                            session,
                            store,
                            peer,
                            via_edge_kind="sequential",
                            via_reason="keep_adjacent",
                            depth=parent_node.depth if parent_node else 0,
                            weight=None,
                            parent_id=kid,
                            label_n=label_n,
                            preview_n=preview_n,
                        )
                extras.append(peer_id)
        for aid in extras:
            if len(session.keep_ids) >= max_keep:
                break
            if aid not in session.keep_ids:
                session.keep_ids.append(aid)
        session.budgets.nodes_spent = len(session.considered)

    def _sync_moment_cache(
        self, session: TraversalSession, graph: GraphView
    ) -> None:
        """Copy GraphView moment_member_cache into session (#105 / §5.2).

        Moments are append-mostly; each expand_moment overwrites the entry so
        a mid-walk membership growth refreshes once per expand.
        """
        try:
            cache = graph.moment_member_cache
        except Exception:  # noqa: BLE001
            return
        if not cache:
            return
        for mid, members in cache.items():
            if not mid:
                continue
            session.moment_member_cache[str(mid)] = list(members)

    def _rebuild_frontier(
        self,
        session: TraversalSession,
        *,
        frontier_max: int,
        prefer_new: Sequence[str] | None = None,
    ) -> None:
        """Rebuild frontier from considered nodes ranked by weight (drop lowest)."""
        prefer = set(prefer_new or ())
        items: list[FrontierItem] = []
        for node in session.considered.values():
            # Seeds + newly expanded stay on frontier; already-kept can remain.
            w = float(node.weight) if node.weight is not None else 0.0
            items.append(
                FrontierItem(
                    atom_id=node.atom_id,
                    label=node.label,
                    preview=node.preview if node.atom_id in prefer or node.depth == 0 else "",
                    kind=node.kind,
                    edge_kind=node.via_edge_kind,
                    weight=w,
                    reason=node.via_reason or "seed",
                    depth=node.depth,
                )
            )
        # Prefer newly expanded for preview fill; rank weight desc.
        items.sort(
            key=lambda f: (
                0 if f.atom_id in prefer else 1,
                -f.weight,
                f.depth,
                f.atom_id,
            )
        )
        # Cap; drop lowest weight (already sorted).
        cap = max(0, frontier_max)
        session.frontier = items[:cap]
        # Ensure previews on frontier for seeds / new (design KD-A17).
        for f in session.frontier:
            node = session.considered.get(f.atom_id)
            if node and not f.preview:
                f.preview = node.preview


__all__ = [
    "ERROR_BUDGET",
    "ERROR_NO_ACTIVE",
    "ERROR_NOT_CONSIDERED",
    "ERROR_SESSION_NOT_ACTIVE",
    "ERROR_TRAVERSE_DISABLED",
    "ERROR_UNKNOWN_SESSION",
    "LOCAL_MAP_ASSOCIATIVE_CAP",
    "LOCAL_MAP_D1_TO_D2",
    "LOCAL_MAP_D2_FANOUT",
    "LOCAL_MAP_EDGES_CAP",
    "LOCAL_MAP_LADDER_CHILD_TIPS_CAP",
    "LOCAL_MAP_MOMENT_PEERS_CAP",
    "LOCAL_MAP_RING_CAP",
    "LOCAL_MAPS_STEP_CAP",
    "NOISY_ATOM_KINDS",
    "PRIMARY_MAP_KINDS",
    "AtomPreview",
    "BudgetState",
    "ConfirmedKeepSnapshot",
    "ConsideredNode",
    "FrontierItem",
    "GraphSessionView",
    "TraversalRegistry",
    "TraversalSession",
    "build_local_map",
    "build_walk_summary_nl",
    "clamp_budget",
    "inspect_atoms",
]
