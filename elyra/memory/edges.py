"""Durable EdgeStore: Protocol, JSONL + Lance backends, budget FIFO helpers.

Scope (PR1 / design-memory-edges-and-traversal): sibling EdgeStore next to
atom MemoryStore; put/list/delete/count parity on both backends; kind unique
keys; outgoing budget FIFO for created_with (≤100) and total (~150).
Out of scope: promote writes, GraphView, traverse, retarget-to-ladder (PR3+).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from elyra.config import ElyraPaths
from elyra.memory.config import (
    EDGE_SCHEMA_VERSION,
    MemorySettings,
    edges_jsonl_path,
    ensure_memory_dirs,
    is_durable_edges_enabled,
    lance_root,
    memory_meta_path,
)
from elyra.memory.errors import MemoryUnavailable
from elyra.memory.types import to_iso_z, utc_now_iso
from elyra.memory.weights import (
    EDGE_CREATED_WITH,
    EDGE_HAS_CHANNEL,
    EDGE_IN_MOMENT,
    EDGE_RECALLS,
)

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
) -> list[DurableEdge]:
    """Drop oldest outgoing edges over kind/total caps. Returns dropped.

    Call after put when write paths care about windows. Idempotent when
    already under budget. Does **not** retarget (PR3).
    """
    outgoing = store.list_edges_from(src_atom_id)
    to_drop = plan_budget_drops(outgoing, settings)
    dropped: list[DurableEdge] = []
    for edge in sorted(to_drop, key=fifo_sort_key):
        if store.delete_edge(edge.edge_id):
            dropped.append(edge)
    return dropped


def put_edge_with_budget(
    store: EdgeStore,
    edge: DurableEdge,
    settings: MemorySettings | None = None,
) -> tuple[DurableEdge, list[DurableEdge]]:
    """``put_edge`` then enforce outgoing budgets on ``src``. Returns (stored, dropped)."""
    stored = store.put_edge(edge)
    dropped = enforce_outgoing_budgets(store, stored.src_atom_id, settings)
    return stored, dropped


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

    def compact(self) -> None:
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

    def health(self) -> dict[str, Any]:
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
            out: dict[str, Any] = {
                "ok": True,
                "backend": "lance",
                "edge_count": len(self._by_id),
                "lance_dir": str(self.lance_dir),
                "edges_by_kind": by_kind,
                "durable_edges_enabled": is_durable_edges_enabled(
                    self._settings
                ),
            }
            try:
                if self._table is not None and hasattr(
                    self._table, "count_rows"
                ):
                    out["disk_edge_count"] = int(self._table.count_rows())
                    out["edge_count_parity"] = (
                        out["edge_count"] == out["disk_edge_count"]
                    )
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
    ``UnavailableEdgeStore`` with reason ``edge_backend_unavailable`` so
    promote can soft-skip edge writes. When ``fail_soft=False``, re-raises.
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
        except Exception as exc:
            _LOG.warning(
                "edge backend=lance open failed (%s: %s); "
                "edge_backend_unavailable",
                type(exc).__name__,
                exc,
            )
            if fail_soft:
                return UnavailableEdgeStore("edge_backend_unavailable")
            raise MemoryUnavailable("edge_backend_unavailable") from exc
    elif backend not in ("jsonl", "lance"):
        _LOG.warning("unknown edge backend %r; using jsonl", backend)

    try:
        return JsonlEdgeStore(paths, cfg)
    except Exception as exc:
        _LOG.warning(
            "edge backend=jsonl open failed (%s: %s); edge_backend_unavailable",
            type(exc).__name__,
            exc,
        )
        if fail_soft:
            return UnavailableEdgeStore("edge_backend_unavailable")
        raise MemoryUnavailable("edge_backend_unavailable") from exc


__all__ = [
    "DurableEdge",
    "EdgeRecord",
    "EdgeStore",
    "JsonlEdgeStore",
    "LanceEdgeStore",
    "UnavailableEdgeStore",
    "durable_edge_from_dict",
    "durable_edge_to_dict",
    "edge_identity_key",
    "enforce_outgoing_budgets",
    "fifo_sort_key",
    "kind_outgoing_cap",
    "new_edge_id",
    "open_edge_store",
    "plan_budget_drops",
    "prepare_edge_for_put",
    "put_edge_with_budget",
    "select_fifo_overflow",
    "total_outgoing_cap",
]
