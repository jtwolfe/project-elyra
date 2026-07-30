"""GraphView — projected structural edges + optional soft semantic hops (Phase 2a).

Scope: neighbourhood expand over Atom fields (sequential / parent-child /
same_moment) and ephemeral semantic_hop via injected EmbeddingIndex + warm
embedder. No durable edge table; no session/worker wiring.
Out of scope: TraversalSession, meal directed_keep, tools, glass.
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
    DEFAULT_MIN_EXPAND_WEIGHT,
    DEFAULT_TEMPORAL_HALF_LIFE_HOURS,
    EDGE_CHILD_OF,
    EDGE_KINDS,
    EDGE_PARENT_OF,
    EDGE_SAME_MOMENT,
    EDGE_SEMANTIC_HOP,
    EDGE_SEQUENTIAL,
    edge_weight,
    passes_min_weight,
)

_LOG = logging.getLogger(__name__)

# Defaults when settings omit a knob (PR-A1 read-only; PR-A2 owns full flags).
DEFAULT_EXPAND_MAX_MS = 80
DEFAULT_PARCEL_CHILD_CAP = 32
DEFAULT_SAME_MOMENT_K = 4
DEFAULT_SEMANTIC_K = 8
DEFAULT_NEIGHBOR_K = 12
DEFAULT_SEED_K = 8

# Empty / skip reasons (parity with Phase 2 semantic omit vocabulary where shared).
REASON_NO_INDEX = "no_index"
REASON_ENCODER_COLD = "encoder_cold"
REASON_TIMEOUT = "timeout"
REASON_NO_HITS = "no_hits"
REASON_PARENT_OF_UNAVAILABLE = "parent_of_unavailable"
# Distinct from no_index: settings or call-site disabled semantic hops (Issue 3).
REASON_SEMANTIC_DISABLED = "semantic_disabled"

STRUCTURAL_KINDS: frozenset[str] = frozenset(
    {EDGE_SEQUENTIAL, EDGE_PARENT_OF, EDGE_CHILD_OF, EDGE_SAME_MOMENT}
)


@dataclass(frozen=True)
class GraphEdge:
    """One projected or ephemeral directed edge."""

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
    """1-hop neighbourhood over projected Atom edges (+ optional semantic hops)."""

    def __init__(
        self,
        store: MemoryStore,
        *,
        index: Any | None = None,
        embedder: Any | None = None,
        settings: MemorySettings | None = None,
        now: datetime | str | None = None,
    ) -> None:
        self._store = store
        self._index = index
        self._embedder = embedder
        self._settings = settings or MemorySettings()
        self._now_override = now
        self._last_expand_meta: dict[str, Any] = {}

    @property
    def last_expand_meta(self) -> dict[str, Any]:
        """Metadata from the most recent ``neighbors`` / ``seed_from_text`` call."""
        return dict(self._last_expand_meta)

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

    # ── Public API ─────────────────────────────────────────────────────────

    def neighbors(
        self,
        atom_id: str,
        *,
        kinds: Sequence[str] | None = None,
        k: int = DEFAULT_NEIGHBOR_K,
        exclude_ids: AbstractSet[str] | None = None,
        allow_semantic: bool = True,
        expand_deadline_ms: int | None = None,
    ) -> list[GraphEdge]:
        """1-hop expand sorted by weight desc (then dst_atom_id for stability).

        Semantic hops only if index present AND embedder warm AND
        ``allow_semantic`` and settings allow. Skip reasons in
        ``last_expand_meta["semantic_reason"]``:

        - ``semantic_disabled`` — settings off or ``allow_semantic=False``
        - ``no_index`` — missing / Null index
        - ``encoder_cold`` — embedder cold / encode fail
        - ``timeout`` — expand deadline exceeded on semantic leg
        - ``no_hits`` — empty body / empty search / all below min weight

        On deadline exceed returns structural edges gathered so far (+ partial
        semantic if any); ``last_expand_meta["expand_truncated"]`` is set.
        """
        t0 = _now_ms()
        deadline = (
            float(expand_deadline_ms)
            if expand_deadline_ms is not None
            else float(self._expand_max_ms())
        )
        # 0 means no soft wall for this call.
        deadline_cap: float | None = deadline if deadline > 0 else None

        expand_meta: dict[str, Any] = {
            "atom_id": atom_id,
            "expand_truncated": False,
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
            wanted = set(EDGE_KINDS)
        else:
            wanted = {str(x) for x in kinds if str(x) in EDGE_KINDS}

        exclude: set[str] = set(exclude_ids or ())
        exclude.add(atom_id)

        edges: list[GraphEdge] = []

        def over() -> bool:
            return deadline_cap is not None and (_now_ms() - t0) > deadline_cap

        if EDGE_SEQUENTIAL in wanted and not over():
            edges.extend(
                self._project_sequential(
                    atom, exclude=exclude, deadline=deadline_cap, t0=t0
                )
            )
        if EDGE_CHILD_OF in wanted and not over():
            edges.extend(self._project_child_of(atom, exclude=exclude))
        if EDGE_PARENT_OF in wanted and not over():
            edges.extend(
                self._project_parent_of(
                    atom,
                    exclude=exclude,
                    deadline=deadline_cap,
                    t0=t0,
                    expand_meta=expand_meta,
                )
            )
        if EDGE_SAME_MOMENT in wanted and not over():
            edges.extend(
                self._project_same_moment(
                    atom, exclude=exclude, deadline=deadline_cap, t0=t0
                )
            )
        if EDGE_SEMANTIC_HOP in wanted and allow_semantic and not over():
            edges.extend(
                self._project_semantic_hop(
                    atom,
                    exclude=exclude,
                    deadline=deadline_cap,
                    t0=t0,
                    expand_meta=expand_meta,
                )
            )
        elif EDGE_SEMANTIC_HOP in wanted and allow_semantic and over():
            expand_meta["expand_truncated"] = True
            expand_meta.setdefault("semantic_reason", REASON_TIMEOUT)
        elif EDGE_SEMANTIC_HOP in wanted and not allow_semantic:
            expand_meta.setdefault("semantic_reason", REASON_SEMANTIC_DISABLED)

        if over():
            expand_meta["expand_truncated"] = True

        # Dedupe by (dst, kind) keeping highest weight.
        best: dict[tuple[str, str], GraphEdge] = {}
        for e in edges:
            key = (e.dst_atom_id, e.edge_kind)
            prev = best.get(key)
            if prev is None or e.weight > prev.weight:
                best[key] = e
        ordered = sorted(
            best.values(),
            key=lambda e: (-e.weight, e.edge_kind, e.dst_atom_id),
        )
        limit = max(0, int(k))
        result = ordered[:limit]
        expand_meta["elapsed_ms"] = int(_now_ms() - t0)
        expand_meta["returned"] = len(result)
        expand_meta["candidates"] = len(ordered)
        self._last_expand_meta = expand_meta
        return result

    def seed_from_text(
        self,
        query: str,
        *,
        k: int = DEFAULT_SEED_K,
        exclude_moment_id: str | None = None,
        expand_deadline_ms: int | None = None,
    ) -> list[tuple[str, float, str]]:
        """Vector seeds → ``(atom_id, score, reason)``.

        Empty reasons in ``last_expand_meta["semantic_reason"]``:
        ``no_index`` | ``encoder_cold`` | ``timeout`` | ``no_hits`` |
        ``semantic_disabled``.
        """
        t0 = _now_ms()
        deadline = (
            float(expand_deadline_ms)
            if expand_deadline_ms is not None
            else float(self._expand_max_ms())
        )
        deadline_cap: float | None = deadline if deadline > 0 else None
        meta: dict[str, Any] = {
            "expand_truncated": False,
            "seed": "text",
            "elapsed_ms": 0,
        }
        self._last_expand_meta = meta

        def finish(
            rows: list[tuple[str, float, str]], *, reason: str | None = None
        ) -> list[tuple[str, float, str]]:
            if reason and not rows:
                meta["semantic_reason"] = reason
            meta["elapsed_ms"] = int(_now_ms() - t0)
            meta["returned"] = len(rows)
            self._last_expand_meta = meta
            return rows

        if deadline_cap is not None and (_now_ms() - t0) > deadline_cap:
            meta["expand_truncated"] = True
            return finish([], reason=REASON_TIMEOUT)

        skip = self._semantic_unavailable_reason()
        if skip is not None:
            return finish([], reason=skip)

        q = (query or "").strip()
        if not q:
            return finish([], reason=REASON_NO_HITS)

        try:
            query_vec = self._embedder.encode_text(q)
        except Exception:  # noqa: BLE001
            _LOG.exception("seed_from_text encode failed")
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
        channel_req = str(
            getattr(self._settings, "semantic_search_channel", None) or "auto"
        ).strip().lower() or "auto"
        concrete, channel_reason = resolve_search_channel(
            channel_req,
            vectors_by_channel=vectors_by_channel,
            joint_repair_remaining=joint_repair_remaining,
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
            _LOG.exception("seed_from_text search failed")
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
    "DEFAULT_EXPAND_MAX_MS",
    "DEFAULT_NEIGHBOR_K",
    "DEFAULT_PARCEL_CHILD_CAP",
    "DEFAULT_SAME_MOMENT_K",
    "DEFAULT_SEED_K",
    "DEFAULT_SEMANTIC_K",
    "EDGE_CHILD_OF",
    "EDGE_KINDS",
    "EDGE_PARENT_OF",
    "EDGE_SAME_MOMENT",
    "EDGE_SEMANTIC_HOP",
    "EDGE_SEQUENTIAL",
    "GraphEdge",
    "GraphView",
    "REASON_ENCODER_COLD",
    "REASON_NO_HITS",
    "REASON_NO_INDEX",
    "REASON_PARENT_OF_UNAVAILABLE",
    "REASON_SEMANTIC_DISABLED",
    "REASON_TIMEOUT",
    "STRUCTURAL_KINDS",
]
