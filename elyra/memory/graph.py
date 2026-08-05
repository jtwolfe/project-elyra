"""GraphView — projected structural + durable EdgeStore + soft semantic hops.

Scope: neighbourhood expand over Atom fields (sequential / parent-child /
same_moment / summary_*), durable EdgeStore kinds (created_with / recalls /
in_moment hub rewrite / has_channel opt-in), ephemeral semantic_hop via
injected EmbeddingIndex + warm embedder, and multimodal ``seed_from_query``
for pure semantic traverse start (text and/or media). Option A: virtual
hub/channel ids never leave expand as walk destinations.
Out of scope: TraversalSession budgets, promote write paths.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import AbstractSet, Any, Mapping, Sequence

from elyra.memory.config import MemorySettings
from elyra.memory.store import MemoryStore
from elyra.memory.types import Atom, parse_iso_z, to_iso_z, utc_now_iso
from elyra.memory.weights import (
    DEFAULT_EXPAND_KINDS,
    DEFAULT_MIN_EXPAND_WEIGHT,
    DEFAULT_TEMPORAL_HALF_LIFE_HOURS,
    EDGE_CHILD_OF,
    EDGE_CREATED_WITH,
    EDGE_HAS_CHANNEL,
    EDGE_IN_MOMENT,
    EDGE_KINDS,
    EDGE_PARENT_OF,
    EDGE_RECALLS,
    EDGE_SAME_MOMENT,
    EDGE_SEMANTIC_HOP,
    EDGE_SEQUENTIAL,
    EDGE_SUMMARY_CHILD,
    EDGE_SUMMARY_SOURCE,
    EDGE_SUPERSEDES,
    edge_weight,
    kind_priority,
    passes_min_weight,
)

# Durable kinds that store real atom destinations (bidirectional expand).
_DURABLE_REAL_KINDS: frozenset[str] = frozenset(
    {EDGE_CREATED_WITH, EDGE_RECALLS}
)
# Channel suffix tokens for virtual has_channel destinations.
_CHANNEL_VIRTUAL_SUFFIXES: tuple[str, ...] = (
    ":text",
    ":image",
    ":audio",
    ":video",
    ":joint",
)
MOMENT_HUB_PREFIX = "moment:"

_LOG = logging.getLogger(__name__)

# Defaults when settings omit a knob (aligned with MemorySettings / design §5.1).
DEFAULT_EXPAND_MAX_MS = 120
DEFAULT_PARCEL_CHILD_CAP = 32
DEFAULT_SAME_MOMENT_K = 8
DEFAULT_SEMANTIC_K = 10
DEFAULT_NEIGHBOR_K = 16
DEFAULT_SEED_K = 10
# Lite summary fabric expand caps (PR-C design §6).
DEFAULT_SUMMARY_SOURCE_LITE_K = 8
DEFAULT_SUMMARY_SOURCE_DEEP_K = 24
TRAVERSE_SUMMARY_EXPAND_MODES = frozenset({"lite", "deep"})

# Empty / skip reasons (parity with Phase 2 semantic omit vocabulary where shared).
REASON_NO_INDEX = "no_index"
REASON_ENCODER_COLD = "encoder_cold"
REASON_TIMEOUT = "timeout"
REASON_NO_HITS = "no_hits"
REASON_PARENT_OF_UNAVAILABLE = "parent_of_unavailable"
# Distinct from no_index: settings or call-site disabled semantic hops (Issue 3).
REASON_SEMANTIC_DISABLED = "semantic_disabled"
# Multimodal seed_from_query soft reasons (never cold-load encoder on start).
REASON_MEDIA_MISSING = "media_missing"
REASON_MEDIA_ENCODE_UNAVAILABLE = "media_encode_unavailable"
REASON_QUERY_REQUIRED = "query_required"

STRUCTURAL_KINDS: frozenset[str] = frozenset(
    {
        EDGE_SEQUENTIAL,
        EDGE_PARENT_OF,
        EDGE_CHILD_OF,
        EDGE_SAME_MOMENT,
        EDGE_SUMMARY_CHILD,
        EDGE_SUMMARY_SOURCE,
        EDGE_SUPERSEDES,
    }
)


def moment_hub_id(moment_id: str) -> str:
    """Stable virtual hub id for durable ``in_moment`` membership edges."""
    mid = str(moment_id or "").strip()
    if mid.startswith(MOMENT_HUB_PREFIX):
        return mid
    return f"{MOMENT_HUB_PREFIX}{mid}"


def is_moment_hub_id(node_id: str | None) -> bool:
    """True when ``node_id`` is a storage-only moment hub (not an Atom)."""
    if not node_id:
        return False
    return str(node_id).startswith(MOMENT_HUB_PREFIX)


def is_channel_virtual_id(node_id: str | None) -> bool:
    """True when ``node_id`` is a storage-only ``{atom}:{channel}`` stub."""
    if not node_id:
        return False
    s = str(node_id)
    return any(s.endswith(suf) for suf in _CHANNEL_VIRTUAL_SUFFIXES)


def is_virtual_graph_id(node_id: str | None) -> bool:
    """True for moment hubs or channel stubs (must never enter considered)."""
    return is_moment_hub_id(node_id) or is_channel_virtual_id(node_id)


@dataclass(frozen=True)
class GraphEdge:
    """One projected, durable, or ephemeral directed edge.

    Contract: ``dst_atom_id`` returned from ``neighbors`` / ``expand_moment`` is
    always a real atom id (Option A rewrite). Virtual hubs/channels are storage
    only and never appear as destinations after expand.
    """

    src_atom_id: str
    dst_atom_id: str
    edge_kind: str
    weight: float
    reason: str
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "meta", dict(self.meta))


def _now_ms() -> float:
    return time.monotonic() * 1000.0


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


def _int_setting(cfg: Any | None, name: str, default: int) -> int:
    if cfg is None:
        return default
    raw = getattr(cfg, name, None)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _float_setting(cfg: Any | None, name: str, default: float) -> float:
    if cfg is None:
        return default
    raw = getattr(cfg, name, None)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _bool_setting(cfg: Any | None, name: str, default: bool) -> bool:
    if cfg is None:
        return default
    raw = getattr(cfg, name, None)
    if raw is None:
        return default
    return bool(raw)


def _index_is_null(index: Any | None) -> bool:
    """True when index is missing or a NullEmbeddingIndex (backend=null)."""
    if index is None:
        return True
    try:
        health = index.health() if hasattr(index, "health") else {}
    except Exception:  # noqa: BLE001
        return True
    if not isinstance(health, Mapping):
        return False
    return str(health.get("backend") or "").lower() == "null"


class GraphView:
    """1-hop neighbourhood: projected + durable EdgeStore + optional semantic hops.

    When ``edge_store`` is provided, durable kinds are unioned into expand.
    ``in_moment`` hub destinations are rewritten to peer atom members (Option A).
    Default expand kinds exclude ``has_channel`` (see ``DEFAULT_EXPAND_KINDS``).
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        index: Any | None = None,
        embedder: Any | None = None,
        settings: MemorySettings | None = None,
        now: datetime | str | None = None,
        edge_store: Any | None = None,
        media_store: Any | None = None,
    ) -> None:
        self._store = store
        self._index = index
        self._embedder = embedder
        self._settings = settings or MemorySettings()
        self._now_override = now
        self._edge_store = edge_store
        self._media_store = media_store
        self._last_expand_meta: dict[str, Any] = {}
        # Process-local moment member ids after expand_moment / hub rewrite
        # (session frontier cache hook for PR6).
        self._moment_member_cache: dict[str, list[str]] = {}

    @property
    def last_expand_meta(self) -> dict[str, Any]:
        """Metadata from the most recent ``neighbors`` / seed call."""
        return dict(self._last_expand_meta)

    @property
    def media_store(self) -> Any | None:
        """Optional MediaStore for multimodal ``seed_from_query``."""
        return self._media_store

    @property
    def edge_store(self) -> Any | None:
        """Injected EdgeStore (may be None when durable fabric unused)."""
        return self._edge_store

    @property
    def moment_member_cache(self) -> dict[str, list[str]]:
        """Copy of moment_id → member atom_ids populated by expand_moment."""
        return {k: list(v) for k, v in self._moment_member_cache.items()}

    def _now_iso(self) -> str:
        if self._now_override is not None:
            return to_iso_z(self._now_override)
        return utc_now_iso()

    def _half_life(self) -> float:
        return _float_setting(
            self._settings,
            "traverse_temporal_half_life_hours",
            DEFAULT_TEMPORAL_HALF_LIFE_HOURS,
        )

    def _min_weight(self) -> float:
        return _float_setting(
            self._settings,
            "traverse_min_expand_weight",
            DEFAULT_MIN_EXPAND_WEIGHT,
        )

    def _parcel_child_cap(self) -> int:
        return max(
            0,
            _int_setting(
                self._settings, "traverse_parcel_child_cap", DEFAULT_PARCEL_CHILD_CAP
            ),
        )

    def _same_moment_k(self) -> int:
        return max(
            0,
            _int_setting(
                self._settings, "traverse_same_moment_k", DEFAULT_SAME_MOMENT_K
            ),
        )

    def _semantic_k(self) -> int:
        return max(
            0,
            _int_setting(self._settings, "traverse_semantic_k", DEFAULT_SEMANTIC_K),
        )

    def _neighbor_k(self) -> int:
        """Product default for neighbors top-k (``traverse_neighbor_k``)."""
        return max(
            1,
            _int_setting(
                self._settings, "traverse_neighbor_k", DEFAULT_NEIGHBOR_K
            ),
        )

    def _expand_max_ms(self) -> int:
        return max(
            0,
            _int_setting(
                self._settings, "traverse_expand_max_ms", DEFAULT_EXPAND_MAX_MS
            ),
        )

    def _allow_semantic(self) -> bool:
        return _bool_setting(
            self._settings, "traverse_allow_semantic_hops", True
        )

    def _expand_channels(self) -> bool:
        """When True, default expand includes ``has_channel`` (still no virtual dsts)."""
        return _bool_setting(
            self._settings, "traverse_expand_channels", False
        )

    def _default_expand_kinds(self) -> set[str]:
        """``kinds is None`` → DEFAULT_EXPAND_KINDS (± has_channel flag)."""
        wanted = set(DEFAULT_EXPAND_KINDS)
        if self._expand_channels():
            wanted.add(EDGE_HAS_CHANNEL)
        return wanted

    def _summary_expand_mode(self) -> str:
        """``lite`` (default) or ``deep`` (Phase 3 / #103 stub)."""
        raw = getattr(self._settings, "traverse_summary_expand", None)
        mode = str(raw or "lite").strip().lower()
        if mode not in TRAVERSE_SUMMARY_EXPAND_MODES:
            return "lite"
        return mode

    def _summary_source_k(self) -> int:
        """Cap for projected ``summary_source`` edges (lite K≤8; deep larger)."""
        if self._summary_expand_mode() == "deep":
            return max(0, DEFAULT_SUMMARY_SOURCE_DEEP_K)
        return max(0, DEFAULT_SUMMARY_SOURCE_LITE_K)

    def _weight_edge(
        self,
        edge_kind: str,
        *,
        src_id: str,
        dst: Atom,
        cosine: float | None = None,
        reason: str,
        meta: dict[str, Any] | None = None,
    ) -> GraphEdge | None:
        w = edge_weight(
            edge_kind,
            dst_t_start=dst.t_start,
            now=self._now_iso(),
            cosine=cosine,
            half_life_hours=self._half_life(),
            src_atom_id=src_id,
            dst_atom_id=dst.atom_id,
        )
        if not passes_min_weight(w, min_weight=self._min_weight()):
            return None
        m = dict(meta or {})
        m.setdefault("dst_t_start", dst.t_start)
        if cosine is not None:
            m.setdefault("cosine", float(cosine))
        return GraphEdge(
            src_atom_id=src_id,
            dst_atom_id=dst.atom_id,
            edge_kind=edge_kind,
            weight=w,
            reason=reason,
            meta=m,
        )

    # ── Structural projection ──────────────────────────────────────────────

    def _project_sequential(
        self,
        atom: Atom,
        *,
        exclude: AbstractSet[str],
        deadline: float | None,
        t0: float,
    ) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for direction, peer_id in (
            ("next", atom.next_atom_id),
            ("prev", atom.prev_atom_id),
        ):
            if deadline is not None and (_now_ms() - t0) > deadline:
                break
            if not peer_id or peer_id in exclude or peer_id == atom.atom_id:
                continue
            peer = self._store.get_atom(peer_id)
            if peer is None:
                continue
            e = self._weight_edge(
                EDGE_SEQUENTIAL,
                src_id=atom.atom_id,
                dst=peer,
                reason=f"sequential:{direction}",
                meta={"direction": direction},
            )
            if e is not None:
                edges.append(e)
        return edges

    def _project_child_of(
        self,
        atom: Atom,
        *,
        exclude: AbstractSet[str],
    ) -> list[GraphEdge]:
        """child → parent (O(1) via parent_atom_id)."""
        pid = atom.parent_atom_id
        if not pid or pid in exclude or pid == atom.atom_id:
            return []
        parent = self._store.get_atom(pid)
        if parent is None:
            return []
        e = self._weight_edge(
            EDGE_CHILD_OF,
            src_id=atom.atom_id,
            dst=parent,
            reason="child_of:parent",
            meta={"parent_atom_id": pid},
        )
        return [e] if e is not None else []

    def _children_via_moment(self, parent: Atom, *, cap: int) -> list[Atom]:
        """Moment-filter reverse: list_by_moment + parent_atom_id match (capped)."""
        if not parent.moment_id or cap <= 0:
            return []
        rows = self._store.list_by_moment(parent.moment_id)
        return [
            a
            for a in rows
            if a.parent_atom_id == parent.atom_id and a.atom_id != parent.atom_id
        ][:cap]

    def _resolve_parent_children(self, parent: Atom) -> tuple[list[Atom], str | None]:
        """Normative parent_of reverse: first_parcel_id chain → moment filter → omit.

        Prefer ``meta.first_parcel_id`` when present. If that chain is empty or
        stale (zero matching children) and the parent has a ``moment_id``, fall
        through to the moment filter so real children remain visible under
        partial/corrupt promote meta. omit_reason is set only when no children
        can be resolved without a full-table scan.
        """
        cap = self._parcel_child_cap()
        if cap <= 0:
            return [], REASON_PARENT_OF_UNAVAILABLE

        meta = parent.meta or {}
        first_id = meta.get("first_parcel_id")
        if first_id:
            parcel_count_raw = meta.get("parcel_count")
            try:
                parcel_count = (
                    int(parcel_count_raw) if parcel_count_raw is not None else cap
                )
            except (TypeError, ValueError):
                parcel_count = cap
            n = max(0, min(cap, parcel_count if parcel_count > 0 else cap))
            if n > 0:
                chain = self._store.walk_next(str(first_id), n=n)
                children: list[Atom] = []
                for a in chain:
                    if a.parent_atom_id == parent.atom_id:
                        children.append(a)
                    if len(children) >= n:
                        break
                if children:
                    return children, None
            # Stale/broken first_parcel_id: fall through to moment filter.

        if parent.moment_id:
            children = self._children_via_moment(parent, cap=cap)
            return children, None

        # No moment and (no meta or empty/stale meta chain).
        return [], REASON_PARENT_OF_UNAVAILABLE

    def _project_parent_of(
        self,
        atom: Atom,
        *,
        exclude: AbstractSet[str],
        deadline: float | None,
        t0: float,
        expand_meta: dict[str, Any],
    ) -> list[GraphEdge]:
        """parent → children (no new store index)."""
        children, omit = self._resolve_parent_children(atom)
        if omit:
            expand_meta.setdefault("parent_of_reason", omit)
            return []
        edges: list[GraphEdge] = []
        for child in children:
            if deadline is not None and (_now_ms() - t0) > deadline:
                break
            if child.atom_id in exclude or child.atom_id == atom.atom_id:
                continue
            e = self._weight_edge(
                EDGE_PARENT_OF,
                src_id=atom.atom_id,
                dst=child,
                reason="parent_of:child",
                meta={
                    "parcel_index": (child.meta or {}).get("parcel_index"),
                },
            )
            if e is not None:
                edges.append(e)
        return edges

    def _project_same_moment(
        self,
        atom: Atom,
        *,
        exclude: AbstractSet[str],
        deadline: float | None,
        t0: float,
    ) -> list[GraphEdge]:
        """Soft edges to other atoms in the same moment (capped k≤4)."""
        if not atom.moment_id:
            return []
        k = self._same_moment_k()
        if k <= 0:
            return []
        rows = self._store.list_by_moment(atom.moment_id)
        candidates: list[Atom] = [
            a
            for a in rows
            if a.atom_id != atom.atom_id and a.atom_id not in exclude
        ]
        # Weight all candidates then take top-k (v1: destination-age decay via
        # edge_weight, not |src.t_start − dst.t_start| within-moment distance).
        scored: list[GraphEdge] = []
        for peer in candidates:
            if deadline is not None and (_now_ms() - t0) > deadline:
                break
            e = self._weight_edge(
                EDGE_SAME_MOMENT,
                src_id=atom.atom_id,
                dst=peer,
                reason="same_moment",
                meta={"moment_id": atom.moment_id},
            )
            if e is not None:
                scored.append(e)
        scored.sort(key=lambda e: (-e.weight, e.dst_atom_id))
        return scored[:k]

    # ── Summary ladder fabric (meta projection; no edge table) ─────────────

    def _meta_id_list(self, meta: Mapping[str, Any], key: str) -> list[str]:
        raw = meta.get(key) if meta else None
        if not isinstance(raw, (list, tuple)):
            return []
        out: list[str] = []
        for item in raw:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                out.append(s)
        return out

    def _project_summary_child(
        self,
        atom: Atom,
        *,
        exclude: AbstractSet[str],
        deadline: float | None,
        t0: float,
    ) -> list[GraphEdge]:
        """parent summary → child summary via ``meta.child_atom_ids``.

        Design §6: only when coarser was built **from children**
        (``meta.from_children``). Raw-fallback ``child_atom_ids`` (experience
        atoms) must not emit ``summary_child``; 1h→raw uses ``summary_source``.
        Destination must be ``kind=summary`` (defense in depth).
        """
        if atom.kind != "summary":
            return []
        meta = atom.meta or {}
        if not meta.get("from_children"):
            return []
        ids = self._meta_id_list(meta, "child_atom_ids")
        if not ids:
            return []
        edges: list[GraphEdge] = []
        for dst_id in ids:
            if deadline is not None and (_now_ms() - t0) > deadline:
                break
            if dst_id in exclude or dst_id == atom.atom_id:
                continue
            dst = self._store.get_atom(dst_id)
            if dst is None or dst.kind != "summary":
                continue
            e = self._weight_edge(
                EDGE_SUMMARY_CHILD,
                src_id=atom.atom_id,
                dst=dst,
                reason="summary_child",
                meta={"child_scale": meta.get("child_scale")},
            )
            if e is not None:
                edges.append(e)
        return edges

    def _project_summary_source(
        self,
        atom: Atom,
        *,
        exclude: AbstractSet[str],
        deadline: float | None,
        t0: float,
    ) -> list[GraphEdge]:
        """1h summary → raw source experience via ``meta.source_atom_ids``.

        Lite caps K≤8; deep uses a larger K. Non-1h summaries yield no edges.
        """
        if atom.kind != "summary" or atom.scale != "1h":
            return []
        meta = atom.meta or {}
        ids = self._meta_id_list(meta, "source_atom_ids")
        if not ids:
            return []
        cap = self._summary_source_k()
        if cap <= 0:
            return []
        edges: list[GraphEdge] = []
        for dst_id in ids[:cap]:
            if deadline is not None and (_now_ms() - t0) > deadline:
                break
            if dst_id in exclude or dst_id == atom.atom_id:
                continue
            dst = self._store.get_atom(dst_id)
            if dst is None:
                continue
            e = self._weight_edge(
                EDGE_SUMMARY_SOURCE,
                src_id=atom.atom_id,
                dst=dst,
                reason="summary_source",
                meta={"scale": atom.scale},
            )
            if e is not None:
                edges.append(e)
        return edges

    def _project_supersedes(
        self,
        atom: Atom,
        *,
        exclude: AbstractSet[str],
    ) -> list[GraphEdge]:
        """new tip → previous version via ``meta.supersedes_atom_id``."""
        if atom.kind != "summary":
            return []
        meta = atom.meta or {}
        prev_id = meta.get("supersedes_atom_id") or meta.get("previous_version_id")
        if not prev_id:
            return []
        prev_id = str(prev_id).strip()
        if not prev_id or prev_id in exclude or prev_id == atom.atom_id:
            return []
        prev = self._store.get_atom(prev_id)
        if prev is None:
            return []
        e = self._weight_edge(
            EDGE_SUPERSEDES,
            src_id=atom.atom_id,
            dst=prev,
            reason="supersedes",
            meta={"version": meta.get("version")},
        )
        return [e] if e is not None else []

    # ── Semantic hop ───────────────────────────────────────────────────────

    def _semantic_unavailable_reason(self) -> str | None:
        """Return skip reason if semantic hops cannot run, else None.

        Vocabulary:
        - ``semantic_disabled`` — settings ``traverse_allow_semantic_hops=False``
        - ``no_index`` — missing / NullEmbeddingIndex
        - ``encoder_cold`` — embedder None / not warm
        Call-site ``allow_semantic=False`` is handled in ``neighbors`` (same
        ``semantic_disabled`` reason) so A2 can tell “off” from “no ANN.”
        """
        if not self._allow_semantic():
            return REASON_SEMANTIC_DISABLED
        if self._index is None or _index_is_null(self._index):
            return REASON_NO_INDEX
        if not _embedder_is_warm(self._embedder):
            return REASON_ENCODER_COLD
        return None

    def _project_semantic_hop(
        self,
        atom: Atom,
        *,
        exclude: AbstractSet[str],
        deadline: float | None,
        t0: float,
        expand_meta: dict[str, Any],
    ) -> list[GraphEdge]:
        skip = self._semantic_unavailable_reason()
        if skip is not None:
            expand_meta.setdefault("semantic_reason", skip)
            return []
        if deadline is not None and (_now_ms() - t0) > deadline:
            expand_meta["expand_truncated"] = True
            expand_meta.setdefault("semantic_reason", REASON_TIMEOUT)
            return []

        seed_text = (atom.content_text or "").strip()
        if not seed_text:
            expand_meta.setdefault("semantic_reason", REASON_NO_HITS)
            return []

        try:
            query_vec = self._embedder.encode_text(seed_text)
        except Exception:  # noqa: BLE001
            _LOG.exception("graph semantic encode failed for %s", atom.atom_id)
            expand_meta.setdefault("semantic_reason", REASON_ENCODER_COLD)
            return []
        if not query_vec:
            expand_meta.setdefault("semantic_reason", REASON_ENCODER_COLD)
            return []

        if deadline is not None and (_now_ms() - t0) > deadline:
            expand_meta["expand_truncated"] = True
            expand_meta.setdefault("semantic_reason", REASON_TIMEOUT)
            return []

        from elyra.memory.index import resolve_search_channel  # noqa: PLC0415

        health: dict[str, Any] = {}
        try:
            h = self._index.health() if hasattr(self._index, "health") else {}
            if isinstance(h, dict):
                health = h
        except Exception:  # noqa: BLE001
            health = {}

        vectors_by_channel = health.get("vectors_by_channel") or {}
        if not isinstance(vectors_by_channel, Mapping):
            vectors_by_channel = {}
        joint_repair_remaining = int(health.get("joint_repair_remaining") or 0)
        channel_req = str(
            getattr(self._settings, "semantic_search_channel", None) or "auto"
        ).strip().lower() or "auto"
        concrete, channel_reason = resolve_search_channel(
            channel_req,
            vectors_by_channel=vectors_by_channel,
            joint_repair_remaining=joint_repair_remaining,
        )
        expand_meta["semantic_channel"] = concrete
        expand_meta["semantic_channel_reason"] = channel_reason

        k = self._semantic_k()
        if k <= 0:
            return []
        try:
            hits = self._index.search(
                query_vec,
                k=k + 1,  # room to drop self
                channel=concrete,
                exclude_atom_ids=set(exclude) | {atom.atom_id},
            )
        except Exception:  # noqa: BLE001
            _LOG.exception("graph semantic search failed for %s", atom.atom_id)
            expand_meta.setdefault("semantic_reason", REASON_NO_HITS)
            return []

        if deadline is not None and (_now_ms() - t0) > deadline:
            expand_meta["expand_truncated"] = True
            expand_meta.setdefault("semantic_reason", REASON_TIMEOUT)
            # still pack any hits we got

        edges: list[GraphEdge] = []
        for hit in hits or []:
            if deadline is not None and (_now_ms() - t0) > deadline:
                expand_meta["expand_truncated"] = True
                break
            dst_id = getattr(hit, "atom_id", None)
            if not dst_id or dst_id == atom.atom_id or dst_id in exclude:
                continue
            score = float(getattr(hit, "score", 0.0) or 0.0)
            dst = getattr(hit, "atom", None)
            if dst is None:
                dst = self._store.get_atom(dst_id)
            if dst is None:
                continue
            ch = str(getattr(hit, "channel", concrete) or concrete)
            e = self._weight_edge(
                EDGE_SEMANTIC_HOP,
                src_id=atom.atom_id,
                dst=dst,
                cosine=score,
                reason=f"cosine={score:.2f} via {ch}",
                meta={"cosine": score, "channel": ch},
            )
            if e is not None:
                edges.append(e)
            if len(edges) >= k:
                break

        if not edges:
            expand_meta.setdefault("semantic_reason", REASON_NO_HITS)
        return edges

    # ── Durable EdgeStore + expand_moment (Option A hub rewrite) ───────────

    def _durable_edge_to_graph(
        self,
        *,
        src_id: str,
        dst: Atom,
        edge_kind: str,
        reason: str,
        meta: dict[str, Any] | None = None,
        cosine: float | None = None,
    ) -> GraphEdge | None:
        """Weight a durable edge at expand time (stored weight is not authority)."""
        m = dict(meta or {})
        cos = cosine
        if cos is None and "cosine" in m:
            try:
                cos = float(m["cosine"])
            except (TypeError, ValueError):
                cos = None
        return self._weight_edge(
            edge_kind,
            src_id=src_id,
            dst=dst,
            cosine=cos,
            reason=reason,
            meta=m,
        )

    def expand_moment(
        self,
        atom_id: str | None = None,
        *,
        moment_id: str | None = None,
        k: int | None = None,
        exclude_ids: AbstractSet[str] | None = None,
    ) -> list[GraphEdge]:
        """Moment members as edges with **real atom destinations only**.

        Resolves ``moment_id`` from the arg or ``get_atom(atom_id).moment_id``.
        Prefers EdgeStore hub membership (``list_edges_to(moment:{id})``);
        falls back to ``store.list_by_moment``. Includes all experience kinds
        present in the moment (tool/ledger walkable — OQ-E2).

        ``src_atom_id`` is the seed atom when ``atom_id`` is given; otherwise
        the moment hub label (glass/debug only — not a walk seed).
        """
        seed_id = str(atom_id).strip() if atom_id else None
        mid = str(moment_id).strip() if moment_id else None
        if not mid and seed_id:
            seed = self._store.get_atom(seed_id)
            if seed is not None and seed.moment_id:
                mid = str(seed.moment_id).strip()
        meta: dict[str, Any] = {
            "expand": "moment",
            "moment_id": mid,
            "atom_id": seed_id,
            "source": None,
        }
        if not mid:
            meta["error"] = "moment_not_resolved"
            self._last_expand_meta = meta
            return []

        exclude: set[str] = set(exclude_ids or ())
        if seed_id:
            exclude.add(seed_id)

        hub = moment_hub_id(mid)
        member_ids: list[str] = []
        source = "list_by_moment"

        if self._edge_store is not None:
            try:
                inbound = self._edge_store.list_edges_to(
                    hub, kinds=[EDGE_IN_MOMENT]
                )
                for de in inbound:
                    sid = str(getattr(de, "src_atom_id", "") or "").strip()
                    if sid and not is_virtual_graph_id(sid):
                        member_ids.append(sid)
                if member_ids:
                    source = "edge_store"
            except Exception:  # noqa: BLE001
                _LOG.exception("expand_moment edge_store list_edges_to failed")
                member_ids = []

        if not member_ids:
            try:
                rows = self._store.list_by_moment(mid)
            except Exception:  # noqa: BLE001
                _LOG.exception("expand_moment list_by_moment failed for %s", mid)
                rows = []
            for a in rows:
                aid = str(getattr(a, "atom_id", "") or "").strip()
                if aid and not is_virtual_graph_id(aid):
                    member_ids.append(aid)
            source = "list_by_moment"

        # Full membership (including seed) for frontier cache; peers for edges.
        seen_all: set[str] = set()
        all_members: list[str] = []
        for mid_atom in member_ids:
            if mid_atom in seen_all:
                continue
            seen_all.add(mid_atom)
            all_members.append(mid_atom)

        self._moment_member_cache[mid] = list(all_members)
        meta["source"] = source
        meta["member_count"] = len(all_members)
        meta["hub"] = hub

        peer_ids = [m for m in all_members if m not in exclude]
        src_for_edges = seed_id or hub
        cap = len(peer_ids) if k is None else max(0, int(k))
        edges: list[GraphEdge] = []
        for dst_id in peer_ids:
            if len(edges) >= cap:
                break
            dst = self._store.get_atom(dst_id)
            if dst is None:
                continue
            e = self._durable_edge_to_graph(
                src_id=src_for_edges,
                dst=dst,
                edge_kind=EDGE_IN_MOMENT,
                reason="in_moment:peer",
                meta={
                    "moment_id": mid,
                    "hub": hub,
                    "membership_source": source,
                },
            )
            if e is not None:
                edges.append(e)

        edges.sort(key=lambda e: (-e.weight, e.dst_atom_id))
        meta["returned"] = len(edges)
        self._last_expand_meta = meta
        return edges

    def _project_durable(
        self,
        atom: Atom,
        *,
        wanted: AbstractSet[str],
        exclude: AbstractSet[str],
        deadline: float | None,
        t0: float,
        expand_meta: dict[str, Any],
    ) -> list[GraphEdge]:
        """Union durable EdgeStore edges; rewrite hubs; drop virtual channels.

        - ``created_with`` / ``recalls``: outgoing + reverse (incoming as flip).
        - ``in_moment``: never emit hub dst; materialize peer members via
          ``expand_moment`` when seed has moment membership.
        - ``has_channel``: never emit ``{atom}:channel`` dst; record channel
          names in expand_meta only.
        """
        durable_wanted = wanted & {
            EDGE_CREATED_WITH,
            EDGE_RECALLS,
            EDGE_IN_MOMENT,
            EDGE_HAS_CHANNEL,
        }
        if not durable_wanted:
            return []

        edges: list[GraphEdge] = []
        channels_seen: list[str] = []

        def over() -> bool:
            return deadline is not None and (_now_ms() - t0) > deadline

        # ── in_moment: Option A peer materialization ──────────────────────
        if EDGE_IN_MOMENT in durable_wanted and not over():
            if atom.moment_id or self._edge_store is not None:
                peer_edges = self.expand_moment(
                    atom_id=atom.atom_id,
                    moment_id=atom.moment_id,
                    exclude_ids=exclude,
                )
                # expand_moment overwrites last_expand_meta — restore parent keys.
                for key in ("source", "member_count", "hub", "moment_id"):
                    val = self._last_expand_meta.get(key)
                    if val is not None:
                        expand_meta.setdefault(f"moment_{key}", val)
                edges.extend(peer_edges)

        if self._edge_store is None:
            return edges

        # ── Outgoing durable real kinds ───────────────────────────────────
        try:
            outgoing = self._edge_store.list_edges_from(atom.atom_id)
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "list_edges_from failed for %s", atom.atom_id
            )
            outgoing = []

        for de in outgoing:
            if over():
                break
            kind = str(getattr(de, "edge_kind", "") or "")
            if kind not in durable_wanted:
                continue
            dst_id = str(getattr(de, "dst_atom_id", "") or "").strip()
            if not dst_id or dst_id == atom.atom_id or dst_id in exclude:
                continue

            if kind == EDGE_IN_MOMENT:
                # Hubs already rewritten via expand_moment; skip raw hub rows.
                continue

            if kind == EDGE_HAS_CHANNEL or is_channel_virtual_id(dst_id):
                # Storage only — never walk into channel stubs.
                ch = None
                de_meta = getattr(de, "meta", None) or {}
                if isinstance(de_meta, Mapping):
                    ch = de_meta.get("channel")
                if not ch and ":" in dst_id:
                    ch = dst_id.rsplit(":", 1)[-1]
                if ch and ch not in channels_seen:
                    channels_seen.append(str(ch))
                continue

            if is_virtual_graph_id(dst_id):
                continue

            if kind not in _DURABLE_REAL_KINDS and kind != EDGE_HAS_CHANNEL:
                continue

            dst = self._store.get_atom(dst_id)
            if dst is None:
                continue
            de_meta = dict(getattr(de, "meta", None) or {})
            reason = str(getattr(de, "reason", "") or "") or f"durable:{kind}"
            e = self._durable_edge_to_graph(
                src_id=atom.atom_id,
                dst=dst,
                edge_kind=kind,
                reason=reason,
                meta=de_meta,
            )
            if e is not None:
                edges.append(e)

        # ── Reverse (incoming) for real-atom durable kinds ────────────────
        reverse_kinds = durable_wanted & _DURABLE_REAL_KINDS
        if reverse_kinds and not over():
            try:
                incoming = self._edge_store.list_edges_to(atom.atom_id)
            except Exception:  # noqa: BLE001
                _LOG.exception(
                    "list_edges_to failed for %s", atom.atom_id
                )
                incoming = []
            for de in incoming:
                if over():
                    break
                kind = str(getattr(de, "edge_kind", "") or "")
                if kind not in reverse_kinds:
                    continue
                peer_id = str(getattr(de, "src_atom_id", "") or "").strip()
                if (
                    not peer_id
                    or peer_id == atom.atom_id
                    or peer_id in exclude
                    or is_virtual_graph_id(peer_id)
                ):
                    continue
                peer = self._store.get_atom(peer_id)
                if peer is None:
                    continue
                de_meta = dict(getattr(de, "meta", None) or {})
                de_meta.setdefault("direction", "reverse")
                reason = (
                    str(getattr(de, "reason", "") or "")
                    or f"durable:{kind}:reverse"
                )
                e = self._durable_edge_to_graph(
                    src_id=atom.atom_id,
                    dst=peer,
                    edge_kind=kind,
                    reason=reason,
                    meta=de_meta,
                )
                if e is not None:
                    edges.append(e)

        if channels_seen:
            expand_meta["channels"] = channels_seen
        return edges

    # ── Public API ─────────────────────────────────────────────────────────

    def neighbors(
        self,
        atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        k: int | None = None,
        exclude_ids: AbstractSet[str] | None = None,
        allow_semantic: bool = True,
        expand_deadline_ms: int | None = None,
        semantic_deadline_ms: int | None = None,
    ) -> list[GraphEdge]:
        """1-hop expand sorted by weight desc (then kind priority, dst id).

        When ``kinds is None``, uses ``DEFAULT_EXPAND_KINDS`` (all EDGE_KINDS
        except ``has_channel``, unless ``traverse_expand_channels``). Explicit
        ``kinds`` is intersected with ``EDGE_KINDS``; empty → no edges.

        ``k`` defaults to ``settings.traverse_neighbor_k`` (product 16).

        Durable EdgeStore edges are unioned with projected structural kinds.
        ``in_moment`` hubs rewrite to peer atoms; virtual channel ids never
        appear as destinations. Semantic hops only if index present AND
        embedder warm AND ``allow_semantic`` and settings allow.

        **Dual deadlines (polish1 / KD-P0-structural):**

        - ``expand_deadline_ms`` — structural soft wall (default:
          ``traverse_expand_max_ms``). ``0`` = no structural soft wall.
        - ``semantic_deadline_ms`` — ANN/embed wall for this call. When
          **provided**, structural and semantic clocks are independent
          (structural first under expand budget; then ANN under semantic
          budget). When **omitted**, legacy single shared wall: semantic
          shares ``expand_deadline_ms`` from the same t0 (backward compat;
          never silently promotes callers to full wait).

        Dual ``same_moment`` + ``in_moment`` peers for the same dst collapse
        to the higher-priority kind (``in_moment`` wins — design §1.4 / §5.3).

        Skip reasons in ``last_expand_meta["semantic_reason"]``:

        - ``semantic_disabled`` — settings off or ``allow_semantic=False``
        - ``no_index`` — missing / Null index
        - ``encoder_cold`` — embedder cold / encode fail
        - ``timeout`` — semantic deadline exceeded / zero budget
        - ``no_hits`` — empty body / empty search / all below min weight

        Meta also reports ``structural_ms_budget/spent``,
        ``semantic_ms_budget/spent``, ``structural_truncated``,
        ``semantic_truncated``, ``dual_deadline``.

        On deadline exceed returns structural edges gathered so far (+ partial
        semantic if any); ``last_expand_meta["expand_truncated"]`` is set.

        Summary fabric (``kind=summary`` seed):
        - ``summary_child`` / ``summary_source`` project from meta under lite
          and deep (source K≤8 lite, larger deep; child only when
          ``from_children``).
        - **Supersedes (lite):** default expand (``kinds=None``) never walks
          ``supersedes``. Only an **explicit** ``EDGE_SUPERSEDES`` in the
          ``kinds`` argument, or ``traverse_summary_expand="deep"``, walks it.
          Passing an explicit full kind list that includes ``supersedes``
          therefore walks supersedes even under lite — use ``kinds=None`` for
          default lite semantics.
        """
        t0 = _now_ms()
        if k is None:
            k = self._neighbor_k()
        struct_budget = (
            float(expand_deadline_ms)
            if expand_deadline_ms is not None
            else float(self._expand_max_ms())
        )
        # Dual mode when semantic_deadline_ms is explicitly provided.
        dual = semantic_deadline_ms is not None
        if dual:
            sem_budget = float(semantic_deadline_ms)
        else:
            # Legacy single wall: semantic shares structural budget from t0.
            sem_budget = struct_budget
        # 0 means no soft wall (legacy) for structural; for dual semantic, 0 = skip ANN.
        struct_cap: float | None = struct_budget if struct_budget > 0 else None

        expand_meta: dict[str, Any] = {
            "atom_id": atom_id,
            "expand_truncated": False,
            "structural_truncated": False,
            "semantic_truncated": False,
            "dual_deadline": dual,
            "structural_ms_budget": int(struct_budget) if struct_budget > 0 else 0,
            "semantic_ms_budget": int(sem_budget) if sem_budget > 0 else 0,
            "structural_ms_spent": 0,
            "semantic_ms_spent": 0,
            "elapsed_ms": 0,
        }
        self._last_expand_meta = expand_meta

        atom = self._store.get_atom(atom_id)
        if atom is None:
            expand_meta["error"] = "atom_not_found"
            expand_meta["elapsed_ms"] = int(_now_ms() - t0)
            return []

        wanted: set[str]
        if kinds is None:
            wanted = self._default_expand_kinds()
        else:
            wanted = {str(x) for x in kinds if str(x) in EDGE_KINDS}

        exclude: set[str] = set(exclude_ids or ())
        exclude.add(atom_id)

        edges: list[GraphEdge] = []

        def over_struct() -> bool:
            return struct_cap is not None and (_now_ms() - t0) > struct_cap

        # Legacy shared-wall over() used by semantic when not dual.
        def over() -> bool:
            return over_struct()

        if EDGE_SEQUENTIAL in wanted and not over_struct():
            edges.extend(
                self._project_sequential(
                    atom, exclude=exclude, deadline=struct_cap, t0=t0
                )
            )
        if EDGE_CHILD_OF in wanted and not over_struct():
            edges.extend(self._project_child_of(atom, exclude=exclude))
        if EDGE_PARENT_OF in wanted and not over_struct():
            edges.extend(
                self._project_parent_of(
                    atom,
                    exclude=exclude,
                    deadline=struct_cap,
                    t0=t0,
                    expand_meta=expand_meta,
                )
            )
        if EDGE_SAME_MOMENT in wanted and not over_struct():
            edges.extend(
                self._project_same_moment(
                    atom, exclude=exclude, deadline=struct_cap, t0=t0
                )
            )

        # Summary ladder fabric: project when seed is a summary atom.
        # Lite (default): summary_child one hop + summary_source K≤8 from 1h;
        # do NOT walk supersedes unless the caller explicitly requested the kind.
        # Deep: same projections + optional supersedes (multi-hop is session depth).
        summary_mode = self._summary_expand_mode()
        expand_meta["summary_expand"] = summary_mode
        if atom.kind == "summary":
            if EDGE_SUMMARY_CHILD in wanted and not over_struct():
                edges.extend(
                    self._project_summary_child(
                        atom, exclude=exclude, deadline=struct_cap, t0=t0
                    )
                )
            if EDGE_SUMMARY_SOURCE in wanted and not over_struct():
                edges.extend(
                    self._project_summary_source(
                        atom, exclude=exclude, deadline=struct_cap, t0=t0
                    )
                )
            if EDGE_SUPERSEDES in wanted and not over_struct():
                # lite default expand skips supersedes; explicit kinds= still ok.
                walk_supersedes = summary_mode == "deep" or (
                    kinds is not None and EDGE_SUPERSEDES in kinds
                )
                if walk_supersedes:
                    edges.extend(
                        self._project_supersedes(atom, exclude=exclude)
                    )
                else:
                    expand_meta["supersedes_skipped"] = "lite"

        # Durable EdgeStore union + in_moment hub rewrite (Option A).
        if not over_struct():
            edges.extend(
                self._project_durable(
                    atom,
                    wanted=wanted,
                    exclude=exclude,
                    deadline=struct_cap,
                    t0=t0,
                    expand_meta=expand_meta,
                )
            )

        t_struct_end = _now_ms()
        expand_meta["structural_ms_spent"] = int(t_struct_end - t0)
        if over_struct():
            expand_meta["structural_truncated"] = True
            expand_meta["expand_truncated"] = True

        # Semantic ANN under independent (dual) or shared (legacy) deadline.
        if EDGE_SEMANTIC_HOP in wanted and allow_semantic:
            if dual:
                # Independent semantic clock after structural gather.
                if sem_budget <= 0:
                    expand_meta.setdefault("semantic_reason", REASON_TIMEOUT)
                    expand_meta["semantic_truncated"] = True
                    expand_meta["semantic_ms_spent"] = 0
                else:
                    t_sem0 = _now_ms()
                    sem_cap: float | None = sem_budget
                    edges.extend(
                        self._project_semantic_hop(
                            atom,
                            exclude=exclude,
                            deadline=sem_cap,
                            t0=t_sem0,
                            expand_meta=expand_meta,
                        )
                    )
                    expand_meta["semantic_ms_spent"] = int(_now_ms() - t_sem0)
                    if expand_meta.get("semantic_reason") == REASON_TIMEOUT:
                        expand_meta["semantic_truncated"] = True
                        expand_meta["expand_truncated"] = True
                    elif expand_meta.get("expand_truncated") and not expand_meta.get(
                        "structural_truncated"
                    ):
                        # semantic hop set expand_truncated for its own timeout
                        expand_meta["semantic_truncated"] = True
            elif not over():
                # Legacy: shared wall from t0
                edges.extend(
                    self._project_semantic_hop(
                        atom,
                        exclude=exclude,
                        deadline=struct_cap,
                        t0=t0,
                        expand_meta=expand_meta,
                    )
                )
                expand_meta["semantic_ms_spent"] = max(
                    0, int(_now_ms() - t0) - int(expand_meta["structural_ms_spent"])
                )
                if expand_meta.get("semantic_reason") == REASON_TIMEOUT:
                    expand_meta["semantic_truncated"] = True
            else:
                expand_meta["expand_truncated"] = True
                expand_meta["semantic_truncated"] = True
                expand_meta.setdefault("semantic_reason", REASON_TIMEOUT)
        elif EDGE_SEMANTIC_HOP in wanted and not allow_semantic:
            expand_meta.setdefault("semantic_reason", REASON_SEMANTIC_DISABLED)

        if over_struct():
            expand_meta["expand_truncated"] = True
            expand_meta["structural_truncated"] = True

        # Defense: drop any virtual destinations that slipped through.
        edges = [
            e for e in edges if not is_virtual_graph_id(e.dst_atom_id)
        ]

        # Dedupe by (dst, kind) keeping highest weight.
        best: dict[tuple[str, str], GraphEdge] = {}
        for e in edges:
            key = (e.dst_atom_id, e.edge_kind)
            prev = best.get(key)
            if prev is None or e.weight > prev.weight:
                best[key] = e

        # Dual same_moment + in_moment for the same dst: prefer durable
        # in_moment (kind priority — design §1.4 / §5.3). Other multi-kind
        # pairs to the same dst remain distinct edges.
        _moment_dual = {EDGE_SAME_MOMENT, EDGE_IN_MOMENT}
        drop_keys: set[tuple[str, str]] = set()
        by_dst_moment: dict[str, GraphEdge] = {}
        for e in best.values():
            if e.edge_kind not in _moment_dual:
                continue
            prev = by_dst_moment.get(e.dst_atom_id)
            if prev is None:
                by_dst_moment[e.dst_atom_id] = e
                continue
            # Prefer higher priority (lower rank), then weight.
            winner = e
            if kind_priority(prev.edge_kind) < kind_priority(e.edge_kind) or (
                kind_priority(prev.edge_kind) == kind_priority(e.edge_kind)
                and prev.weight >= e.weight
            ):
                winner = prev
            loser = e if winner is prev else prev
            by_dst_moment[e.dst_atom_id] = winner
            drop_keys.add((loser.dst_atom_id, loser.edge_kind))
        if drop_keys:
            best = {k: v for k, v in best.items() if k not in drop_keys}

        ordered = sorted(
            best.values(),
            key=lambda e: (
                -e.weight,
                kind_priority(e.edge_kind),
                e.dst_atom_id,
            ),
        )
        limit = max(0, int(k))
        result = ordered[:limit]
        expand_meta["elapsed_ms"] = int(_now_ms() - t0)
        expand_meta["returned"] = len(result)
        expand_meta["candidates"] = len(ordered)
        expand_meta["default_kinds"] = kinds is None
        self._last_expand_meta = expand_meta
        return result

    def seed_from_text(
        self,
        query: str,
        *,
        k: int = DEFAULT_SEED_K,
        exclude_moment_id: str | None = None,
        expand_deadline_ms: int | None = None,
        semantic_deadline_ms: int | None = None,
    ) -> list[tuple[str, float, str]]:
        """Text-only vector seeds — thin wrapper over :meth:`seed_from_query`."""
        return self.seed_from_query(
            query,
            k=k,
            exclude_moment_id=exclude_moment_id,
            expand_deadline_ms=expand_deadline_ms,
            semantic_deadline_ms=semantic_deadline_ms,
        )

    def seed_from_query(
        self,
        query: str | None = None,
        *,
        media_ids: Sequence[str] | None = None,
        k: int = DEFAULT_SEED_K,
        exclude_moment_id: str | None = None,
        expand_deadline_ms: int | None = None,
        semantic_deadline_ms: int | None = None,
        channel: str | None = None,
        media_store: Any | None = None,
    ) -> list[tuple[str, float, str]]:
        """Multimodal vector seeds → ``(atom_id, score, reason)``.

        Accepts text and/or media attachment ids. Uses the same encode path
        family as ``POST /api/memory/vectors/neighbors`` (resolve_one_media +
        encode_text / encode_{image,audio,video} / encode_joint). **Never**
        cold-loads the encoder — cold/missing embedder → ``encoder_cold``.

        Deadline: prefer ``semantic_deadline_ms`` (ANN/embed wall); fall back
        to ``expand_deadline_ms`` for backward compat; else
        ``traverse_expand_max_ms``. Long-path callers (traverse start) must
        pass the unified wait ceiling via ``semantic_deadline_ms``.

        Empty reasons in ``last_expand_meta["semantic_reason"]``:
        ``no_index`` | ``encoder_cold`` | ``timeout`` | ``no_hits`` |
        ``semantic_disabled`` | ``media_missing`` |
        ``media_encode_unavailable`` | ``query_required``.
        """
        t0 = _now_ms()
        if semantic_deadline_ms is not None:
            deadline = float(semantic_deadline_ms)
        elif expand_deadline_ms is not None:
            deadline = float(expand_deadline_ms)
        else:
            deadline = float(self._expand_max_ms())
        deadline_cap: float | None = deadline if deadline > 0 else None
        q = (query or "").strip()
        mids = [str(m).strip() for m in (media_ids or ()) if str(m).strip()]
        meta: dict[str, Any] = {
            "expand_truncated": False,
            "semantic_truncated": False,
            "seed": "query",
            "has_text": bool(q),
            "media_ids": list(mids),
            "semantic_ms_budget": int(deadline) if deadline > 0 else 0,
            "semantic_ms_spent": 0,
            "elapsed_ms": 0,
        }
        self._last_expand_meta = meta

        def finish(
            rows: list[tuple[str, float, str]], *, reason: str | None = None
        ) -> list[tuple[str, float, str]]:
            if reason and not rows:
                meta["semantic_reason"] = reason
            spent = int(_now_ms() - t0)
            meta["elapsed_ms"] = spent
            meta["semantic_ms_spent"] = spent
            if meta.get("expand_truncated"):
                meta["semantic_truncated"] = True
            meta["returned"] = len(rows)
            self._last_expand_meta = meta
            return rows

        if deadline_cap is not None and (_now_ms() - t0) > deadline_cap:
            meta["expand_truncated"] = True
            meta["semantic_truncated"] = True
            return finish([], reason=REASON_TIMEOUT)

        skip = self._semantic_unavailable_reason()
        if skip is not None:
            return finish([], reason=skip)

        if not q and not mids:
            # seed_from_text empty-query parity: no_hits (not a hard error).
            return finish([], reason=REASON_NO_HITS)

        # ── Resolve optional media (first-wins single resolve) ────────────
        media_store_eff = media_store if media_store is not None else self._media_store
        media_modality: str | None = None
        media_input: Any | None = None
        media_skip_reason: str | None = None
        if mids:
            if media_store_eff is None:
                media_skip_reason = REASON_MEDIA_MISSING
            else:
                from elyra.memory.embed.encode import (  # noqa: PLC0415
                    resolve_one_media,
                )

                max_bytes = int(
                    getattr(self._settings, "embed_media_max_bytes", 8_000_000)
                    or 8_000_000
                )
                for mid in mids:
                    if deadline_cap is not None and (_now_ms() - t0) > deadline_cap:
                        meta["expand_truncated"] = True
                        return finish([], reason=REASON_TIMEOUT)
                    try:
                        one = resolve_one_media(
                            media_store_eff, mid, max_bytes=max_bytes
                        )
                    except Exception:  # noqa: BLE001
                        _LOG.debug(
                            "seed_from_query resolve_one_media failed mid=%s",
                            mid,
                            exc_info=True,
                        )
                        one = {
                            "modality": None,
                            "input": None,
                            "skipped": f"{mid}:error",
                        }
                    if one.get("skipped") or not one.get("modality") or one.get(
                        "input"
                    ) is None:
                        media_skip_reason = REASON_MEDIA_MISSING
                        continue
                    media_modality = str(one["modality"])
                    media_input = one["input"]
                    meta["media_id"] = mid
                    meta["query_modality"] = media_modality
                    media_skip_reason = None
                    break
                if media_modality is None and media_skip_reason is None:
                    media_skip_reason = REASON_MEDIA_MISSING

        # Media-only query that failed resolve → soft empty (no silent text demote).
        if mids and media_modality is None and not q:
            return finish([], reason=media_skip_reason or REASON_MEDIA_MISSING)

        # ── Encode query vector (warm embedder only — no cold load) ───────
        query_vec: list[float] | None = None
        seed_channels: list[str] | None = None
        try:
            if media_modality is not None and media_input is not None:
                # Fail closed when media encode is known unavailable.
                media_ok: Any = None
                try:
                    h = self._embedder.health() if hasattr(self._embedder, "health") else {}
                    if isinstance(h, Mapping):
                        media_ok = h.get("media_encode")
                except Exception:  # noqa: BLE001
                    media_ok = None
                if media_ok is False:
                    if not q:
                        return finish([], reason=REASON_MEDIA_ENCODE_UNAVAILABLE)
                    # Text fallback when media encode unavailable but text present.
                    meta["media_encode_fallback"] = "text"
                    query_vec = list(self._embedder.encode_text(q))
                    seed_channels = ["text"]
                    meta["seed"] = "text"
                elif q:
                    from elyra.memory.embed.types import (  # noqa: PLC0415
                        ModalityParts,
                    )

                    parts = ModalityParts(
                        text=q,
                        image=media_input if media_modality == "image" else None,
                        audio=media_input if media_modality == "audio" else None,
                        video=media_input if media_modality == "video" else None,
                    )
                    encode_joint = getattr(self._embedder, "encode_joint", None)
                    if callable(encode_joint):
                        query_vec = list(encode_joint(parts))
                    else:
                        enc_fn = getattr(
                            self._embedder, f"encode_{media_modality}", None
                        )
                        if not callable(enc_fn):
                            return finish([], reason=REASON_ENCODER_COLD)
                        query_vec = list(enc_fn(media_input))
                    seed_channels = ["text", media_modality]
                    meta["seed"] = "text+media"
                else:
                    enc_fn = getattr(
                        self._embedder, f"encode_{media_modality}", None
                    )
                    if not callable(enc_fn):
                        return finish([], reason=REASON_ENCODER_COLD)
                    query_vec = list(enc_fn(media_input))
                    seed_channels = [media_modality]
                    meta["seed"] = "media"
            else:
                if not q:
                    return finish(
                        [], reason=media_skip_reason or REASON_QUERY_REQUIRED
                    )
                query_vec = list(self._embedder.encode_text(q))
                seed_channels = ["text"]
                meta["seed"] = "text"
                if media_skip_reason:
                    meta["media_skip"] = media_skip_reason
        except Exception:  # noqa: BLE001
            _LOG.exception("seed_from_query encode failed")
            return finish([], reason=REASON_ENCODER_COLD)

        if not query_vec:
            return finish([], reason=REASON_ENCODER_COLD)

        if deadline_cap is not None and (_now_ms() - t0) > deadline_cap:
            meta["expand_truncated"] = True
            return finish([], reason=REASON_TIMEOUT)

        from elyra.memory.index import resolve_search_channel  # noqa: PLC0415

        health: dict[str, Any] = {}
        try:
            h = self._index.health() if hasattr(self._index, "health") else {}
            if isinstance(h, dict):
                health = h
        except Exception:  # noqa: BLE001
            health = {}
        vectors_by_channel = health.get("vectors_by_channel") or {}
        if not isinstance(vectors_by_channel, Mapping):
            vectors_by_channel = {}
        joint_repair_remaining = int(health.get("joint_repair_remaining") or 0)
        channel_req = (
            str(channel).strip().lower()
            if channel
            else str(
                getattr(self._settings, "semantic_search_channel", None) or "auto"
            ).strip().lower()
        ) or "auto"
        concrete, channel_reason = resolve_search_channel(
            channel_req,
            vectors_by_channel=vectors_by_channel,
            joint_repair_remaining=joint_repair_remaining,
            seed_channels=seed_channels,
        )
        meta["semantic_channel"] = concrete
        meta["semantic_channel_reason"] = channel_reason

        top_k = max(0, int(k))
        if top_k <= 0:
            return finish([], reason=REASON_NO_HITS)

        try:
            hits = self._index.search(
                query_vec,
                k=top_k,
                channel=concrete,
                exclude_moment_id=exclude_moment_id,
            )
        except Exception:  # noqa: BLE001
            _LOG.exception("seed_from_query search failed")
            return finish([], reason=REASON_NO_HITS)

        if deadline_cap is not None and (_now_ms() - t0) > deadline_cap:
            meta["expand_truncated"] = True

        out: list[tuple[str, float, str]] = []
        for hit in hits or []:
            if deadline_cap is not None and (_now_ms() - t0) > deadline_cap:
                meta["expand_truncated"] = True
                break
            aid = getattr(hit, "atom_id", None)
            if not aid:
                continue
            score = float(getattr(hit, "score", 0.0) or 0.0)
            ch = str(getattr(hit, "channel", concrete) or concrete)
            out.append((str(aid), score, f"semantic:{ch}"))
            if len(out) >= top_k:
                break

        if not out:
            reason = REASON_TIMEOUT if meta.get("expand_truncated") else REASON_NO_HITS
            return finish([], reason=reason)
        return finish(out)

    def seed_temporal(
        self,
        *,
        around_atom_id: str | None = None,
        moment_id: str | None = None,
        k: int = DEFAULT_SEED_K,
    ) -> list[tuple[str, float, str]]:
        """Sequential neighbourhood and/or moment sample → ``(id, weight, reason)``."""
        top_k = max(0, int(k))
        if top_k <= 0:
            self._last_expand_meta = {"seed": "temporal", "returned": 0}
            return []

        seen: dict[str, tuple[float, str]] = {}
        now = self._now_iso()
        half = self._half_life()

        def consider(atom: Atom, reason: str, kind_for_weight: str) -> None:
            if atom.atom_id in seen:
                return
            w = edge_weight(
                kind_for_weight,
                dst_t_start=atom.t_start,
                now=now,
                half_life_hours=half,
                dst_atom_id=atom.atom_id,
            )
            if not passes_min_weight(w, min_weight=self._min_weight()):
                return
            seen[atom.atom_id] = (w, reason)

        if around_atom_id:
            start = self._store.get_atom(around_atom_id)
            if start is not None:
                consider(start, "temporal:anchor", EDGE_SEQUENTIAL)
                # walk_next/prev include start; skip self after first.
                for a in self._store.walk_next(around_atom_id, n=top_k + 1):
                    if a.atom_id == around_atom_id:
                        continue
                    consider(a, "temporal:next", EDGE_SEQUENTIAL)
                for a in self._store.walk_prev(around_atom_id, n=top_k + 1):
                    if a.atom_id == around_atom_id:
                        continue
                    consider(a, "temporal:prev", EDGE_SEQUENTIAL)

        mid = moment_id
        if mid is None and around_atom_id:
            a0 = self._store.get_atom(around_atom_id)
            if a0 is not None:
                mid = a0.moment_id
        if mid:
            for a in self._store.list_by_moment(mid):
                consider(a, "temporal:moment", EDGE_SAME_MOMENT)

        ordered = sorted(
            ((aid, w, r) for aid, (w, r) in seen.items()),
            key=lambda t: (-t[1], t[0]),
        )
        out = ordered[:top_k]
        self._last_expand_meta = {
            "seed": "temporal",
            "returned": len(out),
            "candidates": len(ordered),
        }
        return out


__all__ = [
    "DEFAULT_EXPAND_KINDS",
    "DEFAULT_EXPAND_MAX_MS",
    "DEFAULT_NEIGHBOR_K",
    "DEFAULT_PARCEL_CHILD_CAP",
    "DEFAULT_SAME_MOMENT_K",
    "DEFAULT_SEED_K",
    "DEFAULT_SEMANTIC_K",
    "DEFAULT_SUMMARY_SOURCE_DEEP_K",
    "DEFAULT_SUMMARY_SOURCE_LITE_K",
    "EDGE_CHILD_OF",
    "EDGE_CREATED_WITH",
    "EDGE_HAS_CHANNEL",
    "EDGE_IN_MOMENT",
    "EDGE_KINDS",
    "EDGE_PARENT_OF",
    "EDGE_RECALLS",
    "EDGE_SAME_MOMENT",
    "EDGE_SEMANTIC_HOP",
    "EDGE_SEQUENTIAL",
    "EDGE_SUMMARY_CHILD",
    "EDGE_SUMMARY_SOURCE",
    "EDGE_SUPERSEDES",
    "GraphEdge",
    "GraphView",
    "MOMENT_HUB_PREFIX",
    "REASON_ENCODER_COLD",
    "REASON_MEDIA_ENCODE_UNAVAILABLE",
    "REASON_MEDIA_MISSING",
    "REASON_NO_HITS",
    "REASON_NO_INDEX",
    "REASON_PARENT_OF_UNAVAILABLE",
    "REASON_QUERY_REQUIRED",
    "REASON_SEMANTIC_DISABLED",
    "REASON_TIMEOUT",
    "STRUCTURAL_KINDS",
    "TRAVERSE_SUMMARY_EXPAND_MODES",
    "is_channel_virtual_id",
    "is_moment_hub_id",
    "is_virtual_graph_id",
    "moment_hub_id",
]
