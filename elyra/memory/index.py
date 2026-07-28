"""EmbeddingIndex façade — vector upsert + filtered search (Phase 2 PR3).

Scope: Protocol, Null/Memory/Lance implementations, minimal filtered search.
In scope: upsert, search (filters), optimize stub, health; ready = has vectors.
Out of scope: ANN hybrid recent-buffer (PR4), meal channel, torch.

``ready`` means the active index holds required vectors (KD20) and upsert
succeeded. JSONL production uses ``NullEmbeddingIndex`` (no ANN). CI meal
tests inject ``MemoryEmbeddingIndex``.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import (
    AbstractSet,
    Any,
    Protocol,
    Sequence,
    runtime_checkable,
)

from elyra.memory.embed.types import (
    CHANNEL_SET,
    EMBED_DIM,
    EmbeddingSet,
    embeddings_are_ready,
)
from elyra.memory.types import Atom, to_iso_z

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoredAtom:
    """One ANN / brute-force search hit."""

    atom_id: str
    score: float
    channel: str = "joint"
    atom: Atom | None = None


@runtime_checkable
class EmbeddingIndex(Protocol):
    """Vector index surface for encode drain + meal semantic select."""

    def upsert(self, embedding_set: EmbeddingSet) -> bool:
        """Persist vectors for ``embedding_set.atom_id``. Return True if ready."""
        ...

    def search(
        self,
        query: Sequence[float],
        *,
        k: int = 12,
        channel: str = "joint",
        t_start: datetime | str | None = None,
        t_end: datetime | str | None = None,
        moment_id: str | None = None,
        kinds: Sequence[str] | None = None,
        exclude_atom_ids: AbstractSet[str] | None = None,
        exclude_moment_id: str | None = None,
    ) -> list[ScoredAtom]:
        """Return scored hits; empty if unavailable.

        Candidates must have vectors in the index **and** (when a store atom
        is available) ``embedding_status == "ready"``. Aligns Memory + Lance
        so meal/semantic does not depend on backend-specific lag rules.
        """
        ...

    def optimize(self, *, max_ms: int | None = None) -> dict[str, Any]:
        """Build/refresh ANN structures (PR4 fills in; PR3 stub)."""
        ...

    def health(self) -> dict[str, Any]:
        """``{ok, backend, vectors_ready, index_stale, recent_buffer, ...}``."""
        ...


def _cosine(query: Sequence[float], vec: Sequence[float]) -> float:
    if len(query) != len(vec) or not query:
        return float("-inf")
    qn = math.sqrt(sum(float(x) * float(x) for x in query))
    vn = math.sqrt(sum(float(x) * float(x) for x in vec))
    if qn < 1e-12 or vn < 1e-12:
        return float("-inf")
    return sum(float(a) * float(b) for a, b in zip(query, vec, strict=False)) / (
        qn * vn
    )


def _passes_filters(
    atom: Atom | None,
    *,
    t_start: datetime | str | None,
    t_end: datetime | str | None,
    moment_id: str | None,
    kinds: Sequence[str] | None,
    exclude_atom_ids: AbstractSet[str] | None,
    exclude_moment_id: str | None,
    atom_id: str,
) -> bool:
    if exclude_atom_ids and atom_id in exclude_atom_ids:
        return False
    if atom is None:
        # Vector-only (no store atom): only id exclude applies; time/moment/kind
        # filters require atom metadata and therefore reject.
        return moment_id is None and kinds is None and t_start is None and t_end is None
    # Search candidates require ready status when atom is known (KD20 / meal).
    if atom.embedding_status != "ready":
        return False
    if kinds is not None and atom.kind not in set(kinds):
        return False
    if moment_id is not None and atom.moment_id != moment_id:
        return False
    if exclude_moment_id and atom.moment_id == exclude_moment_id:
        return False
    at = to_iso_z(atom.t_start)
    if t_start is not None and at < to_iso_z(t_start):
        return False
    if t_end is not None and at >= to_iso_z(t_end):
        return False
    return True


class NullEmbeddingIndex:
    """No-op index for JSONL / semantic-off. search always empty."""

    def upsert(self, embedding_set: EmbeddingSet) -> bool:
        return False

    def search(
        self,
        query: Sequence[float],
        *,
        k: int = 12,
        channel: str = "joint",
        t_start: datetime | str | None = None,
        t_end: datetime | str | None = None,
        moment_id: str | None = None,
        kinds: Sequence[str] | None = None,
        exclude_atom_ids: AbstractSet[str] | None = None,
        exclude_moment_id: str | None = None,
    ) -> list[ScoredAtom]:
        return []

    def optimize(self, *, max_ms: int | None = None) -> dict[str, Any]:
        return {"ok": True, "backend": "null", "optimized": False}

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": "null",
            "vectors_ready": 0,
            "index_stale": False,
            "recent_buffer": 0,
            "vectors": False,
        }


class MemoryEmbeddingIndex:
    """In-process dict index for CI / hermetic meal tests (no Lance).

    Optionally syncs ``embedding_status=ready`` onto a MemoryStore when
    ``store`` is provided (notify=False).
    """

    def __init__(self, store: Any | None = None) -> None:
        self._store = store
        self._lock = threading.RLock()
        # atom_id -> EmbeddingSet
        self._by_id: dict[str, EmbeddingSet] = {}

    def upsert(self, embedding_set: EmbeddingSet) -> bool:
        if not isinstance(embedding_set, EmbeddingSet):
            return False
        if not embeddings_are_ready(embedding_set):
            return False
        with self._lock:
            self._by_id[embedding_set.atom_id] = embedding_set
        if self._store is not None:
            try:
                atom = self._store.get_atom(embedding_set.atom_id)
                if atom is not None:
                    from elyra.memory.types import atom_replace  # noqa: PLC0415

                    meta = dict(atom.meta or {})
                    meta["embed_encode_ok"] = True
                    if embedding_set.model_id:
                        meta["embed_model"] = embedding_set.model_id
                    if embedding_set.encoded_at:
                        meta["embed_encoded_at"] = embedding_set.encoded_at
                    meta["embed_channels"] = list(embedding_set.channels_present)
                    updated = atom_replace(
                        atom, embedding_status="ready", meta=meta
                    )
                    try:
                        self._store.put_atom(updated, notify=False)
                    except TypeError:
                        self._store.put_atom(updated)
            except Exception:  # noqa: BLE001
                _LOG.exception(
                    "MemoryEmbeddingIndex status update failed atom_id=%s",
                    embedding_set.atom_id,
                )
        return True

    def get(self, atom_id: str) -> EmbeddingSet | None:
        with self._lock:
            return self._by_id.get(atom_id)

    def search(
        self,
        query: Sequence[float],
        *,
        k: int = 12,
        channel: str = "joint",
        t_start: datetime | str | None = None,
        t_end: datetime | str | None = None,
        moment_id: str | None = None,
        kinds: Sequence[str] | None = None,
        exclude_atom_ids: AbstractSet[str] | None = None,
        exclude_moment_id: str | None = None,
    ) -> list[ScoredAtom]:
        if channel not in CHANNEL_SET:
            return []
        with self._lock:
            items = list(self._by_id.items())
        hits: list[ScoredAtom] = []
        for atom_id, emb in items:
            atom = None
            if self._store is not None:
                try:
                    atom = self._store.get_atom(atom_id)
                except Exception:  # noqa: BLE001
                    atom = None
            if not _passes_filters(
                atom,
                t_start=t_start,
                t_end=t_end,
                moment_id=moment_id,
                kinds=kinds,
                exclude_atom_ids=exclude_atom_ids,
                exclude_moment_id=exclude_moment_id,
                atom_id=atom_id,
            ):
                continue
            vec = emb.channel_vector(channel)
            if vec is None:
                continue
            score = _cosine(query, vec)
            if score == float("-inf"):
                continue
            hits.append(
                ScoredAtom(
                    atom_id=atom_id,
                    score=score,
                    channel=channel,
                    atom=atom,
                )
            )
        hits.sort(key=lambda h: (-h.score, h.atom_id))
        return hits[: max(0, int(k))]

    def optimize(self, *, max_ms: int | None = None) -> dict[str, Any]:
        with self._lock:
            n = len(self._by_id)
        return {
            "ok": True,
            "backend": "memory",
            "optimized": False,
            "vectors_ready": n,
            "note": "in-memory index; no ANN build",
        }

    def health(self) -> dict[str, Any]:
        with self._lock:
            n = len(self._by_id)
        return {
            "ok": True,
            "backend": "memory",
            "vectors_ready": n,
            "index_stale": False,
            "recent_buffer": 0,
            "vectors": True,
            "emb_dim": EMBED_DIM,
        }


class LanceEmbeddingIndex:
    """EmbeddingIndex over ``LanceMemoryStore`` (same process / RLock).

    Vector durability lives in Lance emb columns; this façade owns search
    filters and health reporting. PR3: brute-force cosine only (no hybrid).
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    def upsert(self, embedding_set: EmbeddingSet) -> bool:
        if not isinstance(embedding_set, EmbeddingSet):
            return False
        if not embeddings_are_ready(embedding_set):
            return False
        try:
            return bool(
                self._store.upsert_vectors(embedding_set.atom_id, embedding_set)
            )
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "LanceEmbeddingIndex.upsert failed atom_id=%s",
                getattr(embedding_set, "atom_id", "?"),
            )
            return False

    def search(
        self,
        query: Sequence[float],
        *,
        k: int = 12,
        channel: str = "joint",
        t_start: datetime | str | None = None,
        t_end: datetime | str | None = None,
        moment_id: str | None = None,
        kinds: Sequence[str] | None = None,
        exclude_atom_ids: AbstractSet[str] | None = None,
        exclude_moment_id: str | None = None,
    ) -> list[ScoredAtom]:
        if channel not in CHANNEL_SET:
            return []
        try:
            pairs = self._store.search_vectors(
                query,
                k=k,
                channel=channel,
                t_start=t_start,
                t_end=t_end,
                moment_id=moment_id,
                kinds=kinds,
                exclude_atom_ids=list(exclude_atom_ids) if exclude_atom_ids else None,
                exclude_moment_id=exclude_moment_id,
            )
        except Exception:  # noqa: BLE001
            _LOG.exception("LanceEmbeddingIndex.search failed")
            return []
        out: list[ScoredAtom] = []
        for atom_id, score in pairs:
            atom = None
            try:
                atom = self._store.get_atom(atom_id)
            except Exception:  # noqa: BLE001
                atom = None
            out.append(
                ScoredAtom(
                    atom_id=atom_id,
                    score=float(score),
                    channel=channel,
                    atom=atom,
                )
            )
        return out

    def optimize(self, *, max_ms: int | None = None) -> dict[str, Any]:
        # PR4: create IVF/ANN index + drop buffer. PR3 stub only.
        return {
            "ok": True,
            "backend": "lance",
            "optimized": False,
            "note": "ANN optimize deferred to PR4",
            "max_ms": max_ms,
        }

    def health(self) -> dict[str, Any]:
        store_h: dict[str, Any] = {}
        try:
            store_h = dict(self._store.health() or {})
        except Exception:  # noqa: BLE001
            store_h = {"ok": False, "error": "store_health_failed"}

        vector_ok = bool(store_h.get("vectors"))
        vector_error = store_h.get("vector_error")
        # Fail-closed: migration failure → index not ok (scalar store may still be).
        ok = bool(store_h.get("ok", True)) and vector_ok and not vector_error
        return {
            "ok": ok,
            "backend": "lance",
            "vectors_ready": int(store_h.get("vectors_ready") or 0),
            "index_stale": False,  # PR4 tracks staleness
            "recent_buffer": 0,  # PR4
            "vectors": vector_ok,
            "vector_schema_version": store_h.get("vector_schema_version", 0),
            "error": vector_error or (None if ok else store_h.get("error")),
        }


def open_embedding_index(store: Any) -> EmbeddingIndex:
    """Factory: Lance store → LanceEmbeddingIndex; else NullEmbeddingIndex.

    Always wraps Lance (even when ``vector_schema_ok`` is False) so migration
    failure surfaces via ``health()["ok"]=False`` and ``error`` (fail-closed).
    CI tests that need vectors without Lance should construct
    ``MemoryEmbeddingIndex(store)`` explicitly.
    """
    cls_name = type(store).__name__
    if cls_name == "LanceMemoryStore" or hasattr(store, "upsert_vectors"):
        return LanceEmbeddingIndex(store)
    return NullEmbeddingIndex()


__all__ = [
    "EmbeddingIndex",
    "LanceEmbeddingIndex",
    "MemoryEmbeddingIndex",
    "NullEmbeddingIndex",
    "ScoredAtom",
    "open_embedding_index",
]
