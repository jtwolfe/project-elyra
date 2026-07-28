"""EmbeddingIndex façade — vectors, hybrid recent-buffer, idle optimize (Phase 2 PR4).

Scope: Protocol, Null/Memory/Lance implementations, KD4 freshness policy.
In scope: upsert, hybrid/full search, recent buffer (in-process vectors),
optimize schedule hooks, health (index_stale).
Out of scope: meal channel, torch, hard meal latency budget (PR6).

``ready`` means the active index holds required vectors (KD20) and upsert
succeeded. JSONL production uses ``NullEmbeddingIndex`` (no ANN). CI meal
tests inject ``MemoryEmbeddingIndex``. Soft p95 targets only — hard budget
is meal-side later.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
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


# ── Public types ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoredAtom:
    """One ANN / brute-force search hit."""

    atom_id: str
    score: float
    channel: str = "joint"
    atom: Atom | None = None


@dataclass(frozen=True)
class RecentBufferEntry:
    """In-process only recent vector for hybrid search (KD4).

    Not persisted. Populated on every successful upsert; seeded on open when
    corpus is large enough for hybrid mode.
    """

    atom_id: str
    channel: str  # primary "joint"; text when only text ready
    vector: tuple[float, ...]
    encoded_at: str  # UTC Z
    t_start: str = ""
    moment_id: str | None = None
    kind: str = ""


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
        is available) ``embedding_status == "ready"``. Hybrid mode unions main
        ANN/scan with brute-force over the recent buffer (KD4).
        """
        ...

    def optimize(self, *, max_ms: int | None = None) -> dict[str, Any]:
        """Build/refresh ANN structures + trim buffer (idle only)."""
        ...

    def health(self) -> dict[str, Any]:
        """``{ok, backend, vectors_ready, index_stale, recent_buffer, ...}``."""
        ...


# ── ANN settings / buffer helpers ──────────────────────────────────────────


@dataclass(frozen=True)
class AnnSettings:
    """ANN freshness knobs (mirrors MemorySettings ann_* fields)."""

    recent_buffer_max: int = 256
    full_search_below: int = 2000
    optimize_every_n_encodes: int = 64
    optimize_interval_s: int = 300
    optimize_max_ms: int = 200
    force_full: bool = False


def _int_field(obj: Any, name: str, default: int) -> int:
    raw = getattr(obj, name, default)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def ann_settings_from(obj: Any | None = None, **overrides: Any) -> AnnSettings:
    """Build AnnSettings from MemorySettings-like object and/or kwargs."""
    base = AnnSettings()
    if obj is not None:
        base = AnnSettings(
            recent_buffer_max=max(0, _int_field(obj, "ann_recent_buffer_max", base.recent_buffer_max)),
            full_search_below=max(0, _int_field(obj, "ann_full_search_below", base.full_search_below)),
            optimize_every_n_encodes=max(
                0, _int_field(obj, "ann_optimize_every_n_encodes", base.optimize_every_n_encodes)
            ),
            optimize_interval_s=max(
                0, _int_field(obj, "ann_optimize_interval_s", base.optimize_interval_s)
            ),
            optimize_max_ms=max(0, _int_field(obj, "ann_optimize_max_ms", base.optimize_max_ms)),
            force_full=bool(getattr(obj, "ann_force_full", False)),
        )
    if not overrides:
        return base
    return AnnSettings(
        recent_buffer_max=int(overrides.get("recent_buffer_max", base.recent_buffer_max)),
        full_search_below=int(overrides.get("full_search_below", base.full_search_below)),
        optimize_every_n_encodes=int(
            overrides.get("optimize_every_n_encodes", base.optimize_every_n_encodes)
        ),
        optimize_interval_s=int(
            overrides.get("optimize_interval_s", base.optimize_interval_s)
        ),
        optimize_max_ms=int(overrides.get("optimize_max_ms", base.optimize_max_ms)),
        force_full=bool(overrides.get("force_full", base.force_full)),
    )


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


def _buffer_entry_passes(
    entry: RecentBufferEntry,
    *,
    t_start: datetime | str | None,
    t_end: datetime | str | None,
    moment_id: str | None,
    kinds: Sequence[str] | None,
    exclude_atom_ids: AbstractSet[str] | None,
    exclude_moment_id: str | None,
) -> bool:
    """Cheap prefilter using denormalized buffer fields (no store hit)."""
    if exclude_atom_ids and entry.atom_id in exclude_atom_ids:
        return False
    if kinds is not None and entry.kind and entry.kind not in set(kinds):
        return False
    if kinds is not None and not entry.kind:
        return False
    if moment_id is not None and entry.moment_id != moment_id:
        return False
    if exclude_moment_id and entry.moment_id == exclude_moment_id:
        return False
    at = entry.t_start or ""
    if t_start is not None and at and at < to_iso_z(t_start):
        return False
    if t_end is not None and at and at >= to_iso_z(t_end):
        return False
    # Missing denorm time/kind when those filters requested → reject (safe).
    if (t_start is not None or t_end is not None) and not at:
        return False
    return True


def _merge_hits(
    main: Sequence[ScoredAtom], buffer: Sequence[ScoredAtom], *, k: int
) -> list[ScoredAtom]:
    """Union main + buffer by atom_id, keep best score, sort desc."""
    by_id: dict[str, ScoredAtom] = {}
    for hit in main:
        prev = by_id.get(hit.atom_id)
        if prev is None or hit.score > prev.score:
            by_id[hit.atom_id] = hit
    for hit in buffer:
        prev = by_id.get(hit.atom_id)
        if prev is None or hit.score > prev.score:
            by_id[hit.atom_id] = hit
    hits = list(by_id.values())
    hits.sort(key=lambda h: (-h.score, h.atom_id))
    return hits[: max(0, int(k))]


def _buffer_channel_and_vector(
    emb: EmbeddingSet, preferred: str = "joint"
) -> tuple[str, tuple[float, ...]] | None:
    """Pick channel vector for buffer: joint preferred, else sole ready modality."""
    if preferred in CHANNEL_SET:
        vec = emb.channel_vector(preferred)
        if vec is not None:
            return preferred, vec
    if emb.emb_joint is not None:
        return "joint", emb.emb_joint
    for ch in ("text", "image", "audio", "video"):
        vec = emb.channel_vector(ch)
        if vec is not None:
            return ch, vec
    return None


class _RecentBuffer:
    """Bounded in-process recent-vector map (atom_id → entry)."""

    def __init__(self, max_size: int) -> None:
        self.max_size = max(0, int(max_size))
        self._by_id: dict[str, RecentBufferEntry] = {}

    def __len__(self) -> int:
        return len(self._by_id)

    def clear(self) -> None:
        self._by_id.clear()

    def get(self, atom_id: str) -> RecentBufferEntry | None:
        return self._by_id.get(atom_id)

    def items(self) -> list[RecentBufferEntry]:
        return list(self._by_id.values())

    def push(self, entry: RecentBufferEntry) -> None:
        if self.max_size <= 0:
            return
        self._by_id[entry.atom_id] = entry
        self._evict()

    def _evict(self) -> None:
        while len(self._by_id) > self.max_size:
            oldest = min(
                self._by_id.values(),
                key=lambda e: (e.encoded_at or "", e.atom_id),
            )
            del self._by_id[oldest.atom_id]

    def trim_older_than(self, watermark: str) -> int:
        """Drop entries with encoded_at <= watermark. Returns removed count."""
        if not watermark:
            return 0
        drop = [
            aid
            for aid, e in self._by_id.items()
            if (e.encoded_at or "") <= watermark
        ]
        for aid in drop:
            del self._by_id[aid]
        return len(drop)


class _FreshnessState:
    """Shared KD4 freshness bookkeeping for Memory/Lance indexes."""

    def __init__(self, ann: AnnSettings) -> None:
        self.ann = ann
        self.buffer = _RecentBuffer(ann.recent_buffer_max)
        self.encodes_since_optimize = 0
        self.last_optimize_at: str | None = None
        self.last_optimize_mono: float | None = None
        self.seed_incomplete = False
        self.ann_index_built = False
        self._opened = False

    def mark_upsert(self) -> None:
        self.encodes_since_optimize += 1

    def is_stale(self) -> bool:
        if len(self.buffer) > 0:
            return True
        if self.seed_incomplete:
            return True
        thr = max(0, int(self.ann.optimize_every_n_encodes))
        if thr and self.encodes_since_optimize >= thr:
            return True
        interval = max(0, int(self.ann.optimize_interval_s))
        if (
            interval
            and self.last_optimize_mono is not None
            and (time.monotonic() - self.last_optimize_mono) >= interval
        ):
            return True
        # Never optimized but have encodes → schedule optimize.
        if self.last_optimize_at is None and self.encodes_since_optimize > 0:
            return True
        return False

    def use_full_search(self, vectors_ready: int) -> bool:
        if self.ann.force_full:
            return True
        if vectors_ready < max(0, int(self.ann.full_search_below)):
            return True
        if self.seed_incomplete and self.is_stale():
            return True
        return False

    def mark_optimized(self, *, watermark: str, ann_built: bool = False) -> int:
        """Advance watermark and drop buffer entries now covered by the index.

        Watermark is raised to at least the newest buffered ``encoded_at`` so
        synthetic / clock-skew timestamps still clear on optimize (KD4: drop
        entries older than optimize watermark when safe). Concurrent upserts
        after this call may re-populate the buffer.
        """
        wm = watermark or ""
        for entry in self.buffer.items():
            if entry.encoded_at and entry.encoded_at > wm:
                wm = entry.encoded_at
        removed = self.buffer.trim_older_than(wm)
        # Empty encoded_at entries: trim_older_than treats "" <= wm as True.
        self.encodes_since_optimize = 0
        self.last_optimize_at = wm or watermark
        self.last_optimize_mono = time.monotonic()
        if ann_built:
            self.ann_index_built = True
        return removed

    def health_fields(self, *, vectors_ready: int) -> dict[str, Any]:
        return {
            "vectors_ready": int(vectors_ready),
            "index_stale": self.is_stale(),
            "recent_buffer": len(self.buffer),
            "search_mode": (
                "full" if self.use_full_search(vectors_ready) else "hybrid"
            ),
            "encodes_since_optimize": self.encodes_since_optimize,
            "last_optimize": self.last_optimize_at,
            "seed_incomplete": self.seed_incomplete,
            "ann_index_built": self.ann_index_built,
        }


def _entry_from_emb_and_atom(
    emb: EmbeddingSet,
    atom: Atom | None,
    *,
    channel_pref: str = "joint",
) -> RecentBufferEntry | None:
    picked = _buffer_channel_and_vector(emb, channel_pref)
    if picked is None:
        return None
    channel, vector = picked
    encoded_at = emb.encoded_at or ""
    if not encoded_at and atom is not None:
        meta = atom.meta or {}
        encoded_at = str(meta.get("embed_encoded_at") or "")
    t_start = ""
    moment_id: str | None = None
    kind = ""
    if atom is not None:
        t_start = to_iso_z(atom.t_start)
        moment_id = atom.moment_id
        kind = str(atom.kind or "")
    return RecentBufferEntry(
        atom_id=emb.atom_id,
        channel=channel,
        vector=tuple(float(x) for x in vector),
        encoded_at=encoded_at or to_iso_z(datetime.now(UTC)),
        t_start=t_start,
        moment_id=moment_id,
        kind=kind,
    )


def _search_buffer(
    buffer: _RecentBuffer,
    query: Sequence[float],
    *,
    channel: str,
    t_start: datetime | str | None,
    t_end: datetime | str | None,
    moment_id: str | None,
    kinds: Sequence[str] | None,
    exclude_atom_ids: AbstractSet[str] | None,
    exclude_moment_id: str | None,
    store: Any | None = None,
) -> list[ScoredAtom]:
    hits: list[ScoredAtom] = []
    for entry in buffer.items():
        # Buffer stores one primary channel vector (joint preferred).
        if entry.channel != channel:
            continue
        if not _buffer_entry_passes(
            entry,
            t_start=t_start,
            t_end=t_end,
            moment_id=moment_id,
            kinds=kinds,
            exclude_atom_ids=exclude_atom_ids,
            exclude_moment_id=exclude_moment_id,
        ):
            continue
        atom = None
        if store is not None:
            try:
                atom = store.get_atom(entry.atom_id)
            except Exception:  # noqa: BLE001
                atom = None
            if atom is not None and atom.embedding_status != "ready":
                continue
        score = _cosine(query, entry.vector)
        if score == float("-inf"):
            continue
        hits.append(
            ScoredAtom(
                atom_id=entry.atom_id,
                score=score,
                channel=entry.channel,
                atom=atom,
            )
        )
    return hits


# ── NullEmbeddingIndex ─────────────────────────────────────────────────────


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
            "search_mode": "full",
        }


# ── MemoryEmbeddingIndex ───────────────────────────────────────────────────


class MemoryEmbeddingIndex:
    """In-process dict index for CI / hermetic meal tests (no Lance).

    Implements KD4 recent-buffer + hybrid merge so meal/tests exercise the
    same freshness policy as Lance without disk. Optionally syncs
    ``embedding_status=ready`` onto a MemoryStore when ``store`` is provided.
    """

    def __init__(
        self,
        store: Any | None = None,
        *,
        ann: AnnSettings | None = None,
        settings: Any | None = None,
    ) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._by_id: dict[str, EmbeddingSet] = {}
        self._fresh = _FreshnessState(ann or ann_settings_from(settings))
        # On open with existing store vectors: apply full vs seed policy.
        self._apply_open_policy()

    def _apply_open_policy(self) -> None:
        with self._lock:
            n = len(self._by_id)
            if self._fresh.use_full_search(n):
                self._fresh.seed_incomplete = False
                return
            # Hybrid: seed buffer from in-memory sets (and store if needed).
            self._seed_buffer_unlocked(limit=self._fresh.ann.recent_buffer_max)

    def _seed_buffer_unlocked(self, *, limit: int) -> int:
        """Seed buffer from current vectors (newest encoded_at first)."""
        items: list[tuple[str, EmbeddingSet, Atom | None]] = []
        for atom_id, emb in self._by_id.items():
            atom = None
            if self._store is not None:
                try:
                    atom = self._store.get_atom(atom_id)
                except Exception:  # noqa: BLE001
                    atom = None
            items.append((atom_id, emb, atom))
        items.sort(
            key=lambda t: (
                t[1].encoded_at or "",
                t[0],
            ),
            reverse=True,
        )
        seeded = 0
        for _aid, emb, atom in items[: max(0, limit)]:
            entry = _entry_from_emb_and_atom(emb, atom)
            if entry is not None:
                self._fresh.buffer.push(entry)
                seeded += 1
        self._fresh.seed_incomplete = False
        return seeded

    def seed_buffer(self, *, max_ms: int | None = None) -> dict[str, Any]:
        """Idle/open seed from current vectors (budget ignored in-memory)."""
        del max_ms  # in-memory seed is cheap
        with self._lock:
            n = self._seed_buffer_unlocked(limit=self._fresh.ann.recent_buffer_max)
            return {
                "ok": True,
                "backend": "memory",
                "seeded": n,
                "recent_buffer": len(self._fresh.buffer),
                "seed_incomplete": self._fresh.seed_incomplete,
            }

    def upsert(self, embedding_set: EmbeddingSet) -> bool:
        if not isinstance(embedding_set, EmbeddingSet):
            return False
        if not embeddings_are_ready(embedding_set):
            return False
        atom: Atom | None = None
        with self._lock:
            self._by_id[embedding_set.atom_id] = embedding_set
            if self._store is not None:
                try:
                    atom = self._store.get_atom(embedding_set.atom_id)
                except Exception:  # noqa: BLE001
                    atom = None
            entry = _entry_from_emb_and_atom(embedding_set, atom)
            if entry is not None:
                self._fresh.buffer.push(entry)
            self._fresh.mark_upsert()
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

    def _search_main_unlocked(
        self,
        query: Sequence[float],
        *,
        k: int,
        channel: str,
        t_start: datetime | str | None,
        t_end: datetime | str | None,
        moment_id: str | None,
        kinds: Sequence[str] | None,
        exclude_atom_ids: AbstractSet[str] | None,
        exclude_moment_id: str | None,
    ) -> list[ScoredAtom]:
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
            n = len(self._by_id)
            full = self._fresh.use_full_search(n)
            # Full: scan all ready (main). Hybrid: main top-k ∪ buffer cosine.
            main_k = max(0, int(k)) if full else max(0, int(k))
            main = self._search_main_unlocked(
                query,
                k=main_k if not full else max(main_k, n),
                channel=channel,
                t_start=t_start,
                t_end=t_end,
                moment_id=moment_id,
                kinds=kinds,
                exclude_atom_ids=exclude_atom_ids,
                exclude_moment_id=exclude_moment_id,
            )
            if full and not self._fresh.buffer:
                return main[: max(0, int(k))]
            buf_hits = _search_buffer(
                self._fresh.buffer,
                query,
                channel=channel,
                t_start=t_start,
                t_end=t_end,
                moment_id=moment_id,
                kinds=kinds,
                exclude_atom_ids=exclude_atom_ids,
                exclude_moment_id=exclude_moment_id,
                store=self._store,
            )
            return _merge_hits(main, buf_hits, k=k)

    def optimize(self, *, max_ms: int | None = None) -> dict[str, Any]:
        del max_ms
        with self._lock:
            watermark = to_iso_z(datetime.now(UTC))
            removed = self._fresh.mark_optimized(watermark=watermark, ann_built=True)
            n = len(self._by_id)
            return {
                "ok": True,
                "backend": "memory",
                "optimized": True,
                "vectors_ready": n,
                "buffer_trimmed": removed,
                "recent_buffer": len(self._fresh.buffer),
                "last_optimize": self._fresh.last_optimize_at,
                "note": "in-memory index; watermark advanced (no IVF)",
            }

    def health(self) -> dict[str, Any]:
        with self._lock:
            n = len(self._by_id)
            fields = self._fresh.health_fields(vectors_ready=n)
        return {
            "ok": True,
            "backend": "memory",
            "vectors": True,
            "emb_dim": EMBED_DIM,
            **fields,
        }


# ── LanceEmbeddingIndex ────────────────────────────────────────────────────


class LanceEmbeddingIndex:
    """EmbeddingIndex over ``LanceMemoryStore`` (same process / RLock).

    Vector durability lives in Lance emb columns; this façade owns KD4 recent
    buffer, hybrid/full search policy, optimize, and health reporting.
    """

    def __init__(
        self,
        store: Any,
        *,
        ann: AnnSettings | None = None,
        settings: Any | None = None,
        seed_on_open: bool = True,
    ) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._fresh = _FreshnessState(ann or ann_settings_from(settings))
        if seed_on_open:
            self._apply_open_policy()

    def _vectors_ready_count(self) -> int:
        try:
            h = self._store.health() or {}
            return int(h.get("vectors_ready") or 0)
        except Exception:  # noqa: BLE001
            return 0

    def _apply_open_policy(self) -> None:
        """KD4 open/restart: full mode below threshold, else seed buffer."""
        with self._lock:
            n = self._vectors_ready_count()
            if self._fresh.use_full_search(n):
                self._fresh.seed_incomplete = False
                _LOG.debug(
                    "LanceEmbeddingIndex open: full search mode vectors_ready=%s",
                    n,
                )
                return
            # Hybrid path: seed last N ready by encoded_at.
            try:
                seeded = self._seed_buffer_unlocked(
                    limit=self._fresh.ann.recent_buffer_max
                )
                self._fresh.seed_incomplete = False
                _LOG.debug(
                    "LanceEmbeddingIndex open: hybrid seed n=%s buffer=%s",
                    seeded,
                    len(self._fresh.buffer),
                )
            except Exception:  # noqa: BLE001
                self._fresh.seed_incomplete = True
                _LOG.exception("LanceEmbeddingIndex seed on open failed")

    def _seed_buffer_unlocked(self, *, limit: int) -> int:
        """Seed buffer from durable ready rows ordered by encoded_at desc."""
        limit = max(0, int(limit))
        if limit == 0:
            return 0
        # list_atoms sorts by t_start; over-fetch then order by encoded_at.
        fetch = max(limit, min(limit * 4, 1024))
        atoms: list[Atom] = []
        try:
            list_fn = getattr(self._store, "list_atoms", None)
            if callable(list_fn):
                atoms = list(
                    list_fn(
                        embedding_status="ready",
                        limit=fetch,
                        newest_first=True,
                    )
                    or []
                )
        except Exception:  # noqa: BLE001
            _LOG.exception("seed list_atoms failed")
            atoms = []

        ranked: list[tuple[str, EmbeddingSet, Atom]] = []
        get_vectors = getattr(self._store, "get_vectors", None)
        for atom in atoms:
            emb = None
            if callable(get_vectors):
                try:
                    emb = get_vectors(atom.atom_id)
                except Exception:  # noqa: BLE001
                    emb = None
            if emb is None or not embeddings_are_ready(emb):
                continue
            ranked.append((atom.atom_id, emb, atom))
        ranked.sort(
            key=lambda t: (t[1].encoded_at or "", t[0]),
            reverse=True,
        )
        seeded = 0
        for _aid, emb, atom in ranked[:limit]:
            entry = _entry_from_emb_and_atom(emb, atom)
            if entry is not None:
                self._fresh.buffer.push(entry)
                seeded += 1
        return seeded

    def seed_buffer(self, *, max_ms: int | None = None) -> dict[str, Any]:
        """Continue / re-run buffer seed (idle tick; budget soft)."""
        t0 = time.monotonic()
        with self._lock:
            n = self._seed_buffer_unlocked(limit=self._fresh.ann.recent_buffer_max)
            # Soft budget only — seed is capped by buffer max.
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            if max_ms is not None and elapsed_ms > float(max_ms):
                self._fresh.seed_incomplete = len(self._fresh.buffer) == 0
            else:
                self._fresh.seed_incomplete = False
            return {
                "ok": True,
                "backend": "lance",
                "seeded": n,
                "recent_buffer": len(self._fresh.buffer),
                "seed_incomplete": self._fresh.seed_incomplete,
                "elapsed_ms": elapsed_ms,
            }

    def upsert(self, embedding_set: EmbeddingSet) -> bool:
        if not isinstance(embedding_set, EmbeddingSet):
            return False
        if not embeddings_are_ready(embedding_set):
            return False
        try:
            ok = bool(
                self._store.upsert_vectors(embedding_set.atom_id, embedding_set)
            )
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "LanceEmbeddingIndex.upsert failed atom_id=%s",
                getattr(embedding_set, "atom_id", "?"),
            )
            return False
        if not ok:
            return False
        atom = None
        try:
            atom = self._store.get_atom(embedding_set.atom_id)
        except Exception:  # noqa: BLE001
            atom = None
        with self._lock:
            entry = _entry_from_emb_and_atom(embedding_set, atom)
            if entry is not None:
                self._fresh.buffer.push(entry)
            self._fresh.mark_upsert()
        return True

    def _search_main(
        self,
        query: Sequence[float],
        *,
        k: int,
        channel: str,
        t_start: datetime | str | None,
        t_end: datetime | str | None,
        moment_id: str | None,
        kinds: Sequence[str] | None,
        exclude_atom_ids: AbstractSet[str] | None,
        exclude_moment_id: str | None,
    ) -> list[ScoredAtom]:
        try:
            pairs = self._store.search_vectors(
                query,
                k=k,
                channel=channel,
                t_start=t_start,
                t_end=t_end,
                moment_id=moment_id,
                kinds=kinds,
                exclude_atom_ids=(
                    list(exclude_atom_ids) if exclude_atom_ids else None
                ),
                exclude_moment_id=exclude_moment_id,
            )
        except Exception:  # noqa: BLE001
            _LOG.exception("LanceEmbeddingIndex.search main failed")
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
            n = self._vectors_ready_count()
            full = self._fresh.use_full_search(n)
            # Full: request large k so scan returns all matching ready rows.
            main_k = max(0, int(k))
            if full:
                main_k = max(main_k, n, 1)
            main = self._search_main(
                query,
                k=main_k,
                channel=channel,
                t_start=t_start,
                t_end=t_end,
                moment_id=moment_id,
                kinds=kinds,
                exclude_atom_ids=exclude_atom_ids,
                exclude_moment_id=exclude_moment_id,
            )
            # Always union buffer (correctness: never miss unindexed recent).
            buf_hits = _search_buffer(
                self._fresh.buffer,
                query,
                channel=channel,
                t_start=t_start,
                t_end=t_end,
                moment_id=moment_id,
                kinds=kinds,
                exclude_atom_ids=exclude_atom_ids,
                exclude_moment_id=exclude_moment_id,
                store=self._store,
            )
            if not buf_hits:
                return main[: max(0, int(k))]
            return _merge_hits(main, buf_hits, k=k)

    def optimize(self, *, max_ms: int | None = None) -> dict[str, Any]:
        """Idle-only: best-effort Lance vector index + buffer trim (KD4).

        Never call mid-hop. Soft max_ms only — ANN create may exceed; caller
        (worker idle tick) owns scheduling.
        """
        t0 = time.monotonic()
        budget = max_ms
        if budget is None:
            budget = self._fresh.ann.optimize_max_ms
        ann_built = False
        note = "watermark advanced"
        # Best-effort create IVF / auto vector index on emb_joint.
        try:
            create = getattr(self._store, "create_vector_index", None)
            table = getattr(self._store, "_table", None)
            if callable(create):
                create(channel="joint", max_ms=budget)
                ann_built = True
                note = "store.create_vector_index"
            elif table is not None and hasattr(table, "create_index"):
                try:
                    # lancedb 0.20: create_index on vector column (may no-op / err).
                    table.create_index(
                        metric="cosine",
                        vector_column_name="emb_joint",
                        replace=True,
                    )
                    ann_built = True
                    note = "table.create_index emb_joint"
                except TypeError:
                    try:
                        table.create_index("emb_joint")
                        ann_built = True
                        note = "table.create_index(emb_joint)"
                    except Exception as exc:  # noqa: BLE001
                        note = f"create_index skipped: {exc!s}"[:160]
                except Exception as exc:  # noqa: BLE001
                    note = f"create_index skipped: {exc!s}"[:160]
        except Exception as exc:  # noqa: BLE001
            note = f"optimize ann attempt failed: {exc!s}"[:160]
            _LOG.debug("LanceEmbeddingIndex.optimize ann: %s", exc)

        watermark = to_iso_z(datetime.now(UTC))
        with self._lock:
            removed = self._fresh.mark_optimized(
                watermark=watermark, ann_built=ann_built or self._fresh.ann_index_built
            )
            n = self._vectors_ready_count()
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            return {
                "ok": True,
                "backend": "lance",
                "optimized": True,
                "vectors_ready": n,
                "buffer_trimmed": removed,
                "recent_buffer": len(self._fresh.buffer),
                "last_optimize": self._fresh.last_optimize_at,
                "ann_index_built": self._fresh.ann_index_built,
                "elapsed_ms": elapsed_ms,
                "max_ms": budget,
                "note": note,
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
        with self._lock:
            fields = self._fresh.health_fields(
                vectors_ready=int(store_h.get("vectors_ready") or 0)
            )
        return {
            "ok": ok,
            "backend": "lance",
            "vectors": vector_ok,
            "vector_schema_version": store_h.get("vector_schema_version", 0),
            "error": vector_error or (None if ok else store_h.get("error")),
            **fields,
        }


def open_embedding_index(
    store: Any,
    settings: Any | None = None,
    *,
    ann: AnnSettings | None = None,
) -> EmbeddingIndex:
    """Factory: Lance store → LanceEmbeddingIndex; else NullEmbeddingIndex.

    Always wraps Lance (even when ``vector_schema_ok`` is False) so migration
    failure surfaces via ``health()["ok"]=False`` and ``error`` (fail-closed).
    CI tests that need vectors without Lance should construct
    ``MemoryEmbeddingIndex(store)`` explicitly.

    Pass ``settings`` (MemorySettings) or ``ann`` for KD4 buffer/optimize knobs.
    """
    cls_name = type(store).__name__
    if cls_name == "LanceMemoryStore" or hasattr(store, "upsert_vectors"):
        return LanceEmbeddingIndex(store, ann=ann, settings=settings)
    return NullEmbeddingIndex()


__all__ = [
    "AnnSettings",
    "EmbeddingIndex",
    "LanceEmbeddingIndex",
    "MemoryEmbeddingIndex",
    "NullEmbeddingIndex",
    "RecentBufferEntry",
    "ScoredAtom",
    "ann_settings_from",
    "open_embedding_index",
]
