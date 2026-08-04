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
    Mapping,
    Protocol,
    Sequence,
    runtime_checkable,
)

from elyra.memory.embed.types import (
    CHANNEL_SET,
    CHANNELS,
    EMBED_DIM,
    EmbeddingSet,
    SEARCH_CHANNEL_SET,
    embeddings_are_ready,
    joint_copy_embedding_set,
    sole_non_joint_vector,
)
from elyra.memory.types import Atom, to_iso_z

_LOG = logging.getLogger(__name__)


# ── Channel resolve (KD-R2) ────────────────────────────────────────────────


def resolve_search_channel(
    request: str,
    *,
    vectors_by_channel: Mapping[str, int] | None = None,
    joint_repair_remaining: int = 0,
    seed_channels: Sequence[str] | None = None,
) -> tuple[str, str]:
    """Resolve a search channel request to a concrete column + reason.

    Pure function (KD-R16): product paths call this then ``search(concrete)``.
    ``auto`` is **not** in ``CHANNEL_SET``; resolve before any column lookup
    so ``channel="auto"`` never early-returns empty (KD-R2 footgun).

    While ``joint_repair_remaining > 0``, auto prefers text (or first sole
    modality with coverage) so product search does not lock onto sparse joint.

    **KD-M20 seed-aware auto:** when ``seed_channels`` is a sole media modality
    (``image`` / ``audio`` / ``video`` with no text), prefer that modality
    channel when covered; else joint if covered; else first sole with coverage.
    Text+media seeds (or no seed) keep joint-primary auto. Explicit ``channel=``
    always wins regardless of seed.
    """
    req = (request or "").strip().lower()
    if not req:
        req = "auto"
    if req in CHANNEL_SET:
        return req, "explicit"
    if req != "auto":
        # Unknown request — callers that pass raw to search still get [];
        # meal/glass should validate via SEARCH_CHANNEL_SET first.
        return "joint", "invalid_request"

    counts = dict(vectors_by_channel or {})
    remaining = max(0, int(joint_repair_remaining))

    # Normalize seed channel hints (KD-M20).
    seed_mods: list[str] = []
    if seed_channels:
        for raw in seed_channels:
            s = str(raw or "").strip().lower()
            if s in ("text", "image", "audio", "video") and s not in seed_mods:
                seed_mods.append(s)
    media_seeds = [m for m in seed_mods if m in ("image", "audio", "video")]
    has_text_seed = "text" in seed_mods
    # Sole media seed: exactly one media modality and no text.
    sole_media: str | None = (
        media_seeds[0]
        if len(media_seeds) == 1 and not has_text_seed and len(seed_mods) == 1
        else None
    )

    if sole_media is not None:
        mod = sole_media
        if remaining > 0:
            # Repair pending: still prefer matching modality when covered so
            # incomplete joint is not locked; else text / other sole / empty.
            if int(counts.get(mod) or 0) > 0:
                return mod, f"auto_seed_{mod}_repair_pending"
            if int(counts.get("text") or 0) > 0:
                return "text", "auto_text_repair_pending"
            for ch in ("image", "audio", "video"):
                if int(counts.get(ch) or 0) > 0:
                    return ch, f"auto_{ch}_repair_pending"
            return "joint", "auto_empty_repair_pending"
        if int(counts.get(mod) or 0) > 0:
            return mod, f"auto_seed_{mod}"
        if int(counts.get("joint") or 0) > 0:
            return "joint", "auto_joint_seed_fallback"
        if int(counts.get("text") or 0) > 0:
            return "text", "auto_text_seed_fallback"
        for ch in ("image", "audio", "video"):
            if int(counts.get(ch) or 0) > 0:
                return ch, f"auto_{ch}"
        return "joint", "auto_empty"

    # Text-only / text+media / no seed: joint-primary (existing KD-R2 policy).
    if remaining > 0:
        if int(counts.get("text") or 0) > 0:
            return "text", "auto_text_repair_pending"
        for ch in ("image", "audio", "video"):
            if int(counts.get(ch) or 0) > 0:
                return ch, f"auto_{ch}_repair_pending"
        return "joint", "auto_empty_repair_pending"

    if int(counts.get("joint") or 0) > 0:
        return "joint", "auto_joint"
    if int(counts.get("text") or 0) > 0:
        return "text", "auto_text"
    for ch in ("image", "audio", "video"):
        if int(counts.get(ch) or 0) > 0:
            return ch, f"auto_{ch}"
    return "joint", "auto_empty"


def empty_vectors_by_channel() -> dict[str, int]:
    """Zero counts for all durable embed channels."""
    return {c: 0 for c in CHANNELS}


def _int_setting(obj: Any | None, name: str, default: int) -> int:
    """Read int setting; only missing/None falls back to default (0 is valid)."""
    if obj is None:
        return default
    raw = getattr(obj, name, None)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _count_vectors_by_channel_from_sets(
    emb_sets: Sequence[EmbeddingSet],
) -> dict[str, int]:
    counts = empty_vectors_by_channel()
    for emb in emb_sets:
        for ch in CHANNELS:
            if emb.channel_vector(ch) is not None:
                counts[ch] = counts.get(ch, 0) + 1
    return counts


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
    # KD-R3: never IVF when channel n < ivf_min_vectors (0 = always attempt).
    ivf_min_vectors: int = 256
    # KD-R3: channel names to target for create_index (subset of CHANNEL_SET).
    index_channels: tuple[str, ...] = ("joint",)


def _int_field(obj: Any, name: str, default: int) -> int:
    raw = getattr(obj, name, default)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _index_channels_field(
    obj: Any, name: str, default: tuple[str, ...]
) -> tuple[str, ...]:
    raw = getattr(obj, name, default)
    if raw is None:
        return default
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        return default
    out: list[str] = []
    for item in items:
        ch = str(item).strip().lower()
        if ch.startswith("emb_"):
            ch = ch[len("emb_") :]
        if ch in CHANNEL_SET and ch not in out:
            out.append(ch)
    return tuple(out) if out else default


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
            ivf_min_vectors=max(
                0, _int_field(obj, "ann_ivf_min_vectors", base.ivf_min_vectors)
            ),
            index_channels=_index_channels_field(
                obj, "ann_index_channels", base.index_channels
            ),
        )
    if not overrides:
        return base
    index_channels = overrides.get("index_channels", base.index_channels)
    if isinstance(index_channels, str):
        index_channels = (index_channels,)
    elif not isinstance(index_channels, tuple):
        index_channels = tuple(index_channels)
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
        ivf_min_vectors=max(
            0, int(overrides.get("ivf_min_vectors", base.ivf_min_vectors))
        ),
        index_channels=tuple(index_channels) if index_channels else base.index_channels,
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
        # Channels with a successfully built ANN index (health honesty KD-R3).
        self.ann_index_channels: list[str] = []
        self.last_optimize_notes: list[str] = []
        self._opened = False

    def mark_upsert(self) -> None:
        self.encodes_since_optimize += 1

    def is_stale(self, *, vectors_ready: int = 0) -> bool:
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
        # KD4 open step (3): schedule idle optimize if no ANN index exists.
        if not self.ann_index_built and int(vectors_ready) > 0:
            return True
        return False

    def use_full_search(self, vectors_ready: int) -> bool:
        """Full/unindexed until above threshold **and** ANN index built (KD4)."""
        if self.ann.force_full:
            return True
        if vectors_ready < max(0, int(self.ann.full_search_below)):
            return True
        # Hybrid only when corpus is large enough *and* ANN is built.
        if not self.ann_index_built:
            return True
        if self.seed_incomplete and self.is_stale(vectors_ready=vectors_ready):
            return True
        return False

    def mark_optimized(
        self,
        *,
        watermark: str,
        ann_built: bool = False,
        built_channels: Sequence[str] | None = None,
        notes: Sequence[str] | None = None,
    ) -> int:
        """Advance watermark and drop buffer entries now covered by the index.

        Only call when optimize actually succeeded (ANN built or exhaustive
        main explicitly marked built). Watermark is raised to at least the
        newest buffered ``encoded_at`` so synthetic timestamps still clear.
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
        if notes is not None:
            self.last_optimize_notes = [str(n) for n in notes]
        if ann_built:
            self.ann_index_built = True
            if built_channels is not None:
                merged = list(self.ann_index_channels)
                for ch in built_channels:
                    c = str(ch).strip().lower()
                    if c and c not in merged:
                        merged.append(c)
                self.ann_index_channels = merged
        return removed

    def resolve_search_mode(
        self,
        vectors_ready: int,
        *,
        ann_search_backend: str = "lance_native",
        lance_search_ok: bool | None = None,
        engine: str = "lance",
    ) -> str:
        """Honest search_mode (KD-R4 / OQ-R6).

        One of: ``full_python`` | ``full_lance`` | ``hybrid`` |
        ``hybrid_python_fallback``.

        - ``engine="memory"``: in-process index — full_python / hybrid
        - ``engine="lance"``: lance_native + healthy → full_lance / hybrid;
          python config or sticky Lance failure → full_python /
          hybrid_python_fallback
        """
        full = self.use_full_search(vectors_ready)
        backend = (ann_search_backend or "lance_native").strip().lower()
        if engine == "memory":
            return "full_python" if full else "hybrid"
        # Lance path
        use_python = backend == "python" or lance_search_ok is False
        if full:
            return "full_python" if use_python else "full_lance"
        return "hybrid_python_fallback" if use_python else "hybrid"

    def health_fields(
        self,
        *,
        vectors_ready: int,
        vectors_by_channel: Mapping[str, int] | None = None,
        joint_repair_remaining: int = 0,
        joint_repair_last_batch: int = 0,
        ann_search_backend: str = "lance_native",
        lance_search_ok: bool | None = None,
        engine: str = "lance",
    ) -> dict[str, Any]:
        mode = self.resolve_search_mode(
            vectors_ready,
            ann_search_backend=ann_search_backend,
            lance_search_ok=lance_search_ok,
            engine=engine,
        )
        return {
            "vectors_ready": int(vectors_ready),
            "index_stale": self.is_stale(vectors_ready=vectors_ready),
            "recent_buffer": len(self.buffer),
            "search_mode": mode,
            "ann_search_backend": (
                (ann_search_backend or "lance_native").strip().lower()
            ),
            "encodes_since_optimize": self.encodes_since_optimize,
            "last_optimize": self.last_optimize_at,
            "seed_incomplete": self.seed_incomplete,
            "ann_index_built": self.ann_index_built,
            "ann_index_channels": list(self.ann_index_channels),
            "last_optimize_notes": list(self.last_optimize_notes),
            "vectors_by_channel": dict(
                vectors_by_channel
                if vectors_by_channel is not None
                else empty_vectors_by_channel()
            ),
            "joint_repair_remaining": int(joint_repair_remaining),
            "joint_repair_last_batch": int(joint_repair_last_batch),
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
        del max_ms
        notes = ["null index; no ANN"]
        return {
            "ok": True,
            "backend": "null",
            "optimized": False,
            "notes": notes,
            "note": notes[0],
            "ann_index_built": False,
        }

    def repair_joint_copies(self, *, limit: int = 64) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": "null",
            "repaired": 0,
            "joint_repair_remaining": 0,
            "joint_repair_last_batch": 0,
        }

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "backend": "null",
            "vectors_ready": 0,
            "index_stale": False,
            "recent_buffer": 0,
            "vectors": False,
            # JSONL / Null never calls table.search (KD-R4).
            "search_mode": "full_python",
            "ann_search_backend": "python",
            "vectors_by_channel": empty_vectors_by_channel(),
            "joint_repair_remaining": 0,
            "joint_repair_last_batch": 0,
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
        joint_repair_max_per_open: int | None = None,
    ) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._by_id: dict[str, EmbeddingSet] = {}
        self._fresh = _FreshnessState(ann or ann_settings_from(settings))
        # Dict main is always exhaustive → treat as "index built" for hybrid policy.
        self._fresh.ann_index_built = True
        self._joint_repair_last_batch = 0
        self._settings = settings
        if joint_repair_max_per_open is None:
            open_cap = _int_setting(settings, "joint_repair_max_per_open", 500)
        else:
            open_cap = int(joint_repair_max_per_open)
        self._joint_repair_max_per_open = max(0, open_cap)
        # On open with existing store vectors: apply full vs seed policy.
        self._apply_open_policy()
        # KD-R11: eager joint-copy repair on open (bounded; 0 disables).
        if self._joint_repair_max_per_open > 0:
            self.repair_joint_copies(limit=self._joint_repair_max_per_open)

    def _apply_open_policy(self) -> None:
        with self._lock:
            n = len(self._by_id)
            thr = max(0, int(self._fresh.ann.full_search_below))
            if self._fresh.ann.force_full or n < thr:
                self._fresh.seed_incomplete = False
                return
            # Above threshold: seed for hybrid readiness.
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
        target = min(max(0, int(limit)), len(items))
        self._fresh.seed_incomplete = bool(target > 0 and seeded < target)
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

    def _require_joint_for_upsert(self) -> bool:
        """OQ-R4: new encodes require joint when single-mod joint flag is on."""
        if self._settings is None:
            return False
        return bool(
            getattr(self._settings, "embed_joint_for_single_modality", False)
        )

    def upsert(self, embedding_set: EmbeddingSet) -> bool:
        if not isinstance(embedding_set, EmbeddingSet):
            return False
        require_joint = self._require_joint_for_upsert()
        if not embeddings_are_ready(
            embedding_set, require_joint=require_joint
        ):
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

    def _vectors_by_channel_unlocked(self) -> dict[str, int]:
        return _count_vectors_by_channel_from_sets(list(self._by_id.values()))

    def _joint_repair_remaining_unlocked(self) -> int:
        n = 0
        for emb in self._by_id.values():
            if emb.emb_joint is None and sole_non_joint_vector(emb) is not None:
                n += 1
        return n

    def repair_joint_copies(self, *, limit: int = 64) -> dict[str, Any]:
        """Copy sole modality → emb_joint for ready rows missing joint (KD-R11).

        No encoder. Re-pushes buffer entries as joint. Idempotent.
        On store persist failure, rolls back in-memory state (no false remaining=0).
        """
        cap = max(0, int(limit))
        repaired = 0
        with self._lock:
            if cap > 0:
                for atom_id, emb in list(self._by_id.items()):
                    if repaired >= cap:
                        break
                    fixed = joint_copy_embedding_set(emb)
                    if fixed is None:
                        continue
                    atom = None
                    persist_ok = True
                    if self._store is not None:
                        try:
                            atom = self._store.get_atom(atom_id)
                        except Exception:  # noqa: BLE001
                            atom = None
                        # Persist vectors when store supports upsert_vectors.
                        upsert = getattr(self._store, "upsert_vectors", None)
                        if callable(upsert):
                            try:
                                ok = upsert(atom_id, fixed)
                                if ok is False:
                                    persist_ok = False
                            except Exception:  # noqa: BLE001
                                persist_ok = False
                                _LOG.debug(
                                    "MemoryEmbeddingIndex repair upsert failed %s",
                                    atom_id,
                                    exc_info=True,
                                )
                        elif atom is not None:
                            try:
                                from elyra.memory.types import (  # noqa: PLC0415
                                    atom_replace,
                                )

                                meta = dict(atom.meta or {})
                                meta["embed_channels"] = list(
                                    fixed.channels_present
                                )
                                meta["embed_encode_ok"] = True
                                updated = atom_replace(
                                    atom, embedding_status="ready", meta=meta
                                )
                                try:
                                    self._store.put_atom(updated, notify=False)
                                except TypeError:
                                    self._store.put_atom(updated)
                            except Exception:  # noqa: BLE001
                                persist_ok = False
                    if not persist_ok:
                        # Do not advance _by_id / buffer / repaired on durable fail.
                        continue
                    self._by_id[atom_id] = fixed
                    entry = _entry_from_emb_and_atom(fixed, atom)
                    if entry is not None:
                        self._fresh.buffer.push(entry)
                    repaired += 1
            self._joint_repair_last_batch = repaired
            remaining = self._joint_repair_remaining_unlocked()
            counts = self._vectors_by_channel_unlocked()
        return {
            "ok": True,
            "backend": "memory",
            "repaired": repaired,
            "joint_repair_remaining": remaining,
            "joint_repair_last_batch": repaired,
            "vectors_by_channel": counts,
        }

    def _resolve_channel(self, channel: str) -> str | None:
        """Resolve request channel; return concrete or None if invalid."""
        ch = (channel or "").strip().lower()
        if ch == "auto":
            with self._lock:
                counts = self._vectors_by_channel_unlocked()
                remaining = self._joint_repair_remaining_unlocked()
            concrete, _reason = resolve_search_channel(
                "auto",
                vectors_by_channel=counts,
                joint_repair_remaining=remaining,
            )
            return concrete
        if ch in CHANNEL_SET:
            return ch
        return None

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
        # KD-R2: resolve auto *before* CHANNEL_SET check (never treat auto as []).
        concrete = self._resolve_channel(channel)
        if concrete is None:
            return []
        with self._lock:
            n = len(self._by_id)
            full = self._fresh.use_full_search(n)
            # Full: scan all ready (main). Hybrid: main top-k ∪ buffer cosine.
            main_k = max(0, int(k)) if full else max(0, int(k))
            main = self._search_main_unlocked(
                query,
                k=main_k if not full else max(main_k, n),
                channel=concrete,
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
                channel=concrete,
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
        notes = ["in-memory index; watermark advanced (no IVF)"]
        with self._lock:
            watermark = to_iso_z(datetime.now(UTC))
            removed = self._fresh.mark_optimized(
                watermark=watermark,
                ann_built=True,
                built_channels=list(self._fresh.ann.index_channels or ("joint",)),
                notes=notes,
            )
            n = len(self._by_id)
            return {
                "ok": True,
                "backend": "memory",
                "optimized": True,
                "vectors_ready": n,
                "buffer_trimmed": removed,
                "recent_buffer": len(self._fresh.buffer),
                "last_optimize": self._fresh.last_optimize_at,
                "ann_index_built": self._fresh.ann_index_built,
                "notes": list(notes),
                "note": notes[0],
                "last_optimize_notes": list(notes),
            }

    def health(self) -> dict[str, Any]:
        with self._lock:
            n = len(self._by_id)
            fields = self._fresh.health_fields(
                vectors_ready=n,
                vectors_by_channel=self._vectors_by_channel_unlocked(),
                joint_repair_remaining=self._joint_repair_remaining_unlocked(),
                joint_repair_last_batch=self._joint_repair_last_batch,
                ann_search_backend="python",
                engine="memory",
            )
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
        joint_repair_max_per_open: int | None = None,
    ) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._fresh = _FreshnessState(ann or ann_settings_from(settings))
        self._settings = settings
        self._joint_repair_last_batch = 0
        # Open repair is owned by LanceMemoryStore (single open-cap owner).
        # Index only seeds buffer so joint entries from store open are visible.
        if joint_repair_max_per_open is None:
            self._joint_repair_max_per_open = max(
                0, _int_setting(settings, "joint_repair_max_per_open", 500)
            )
        else:
            self._joint_repair_max_per_open = max(0, int(joint_repair_max_per_open))
        if seed_on_open:
            self._apply_open_policy()

    def _vectors_ready_count(self) -> int:
        try:
            h = self._store.health() or {}
            return int(h.get("vectors_ready") or 0)
        except Exception:  # noqa: BLE001
            return 0

    def _vectors_by_channel(self) -> dict[str, int]:
        try:
            fn = getattr(self._store, "vectors_by_channel", None)
            if callable(fn):
                return dict(fn() or empty_vectors_by_channel())
            h = self._store.health() or {}
            raw = h.get("vectors_by_channel")
            if isinstance(raw, dict):
                base = empty_vectors_by_channel()
                base.update({str(k): int(v) for k, v in raw.items()})
                return base
        except Exception:  # noqa: BLE001
            pass
        return empty_vectors_by_channel()

    def _joint_repair_remaining(self) -> int:
        try:
            fn = getattr(self._store, "joint_repair_remaining", None)
            if callable(fn):
                return max(0, int(fn() or 0))
            h = self._store.health() or {}
            if "joint_repair_remaining" in h:
                return max(0, int(h.get("joint_repair_remaining") or 0))
        except Exception:  # noqa: BLE001
            pass
        return 0

    def repair_joint_copies(self, *, limit: int = 64) -> dict[str, Any]:
        """Eager joint-copy repair via store; re-push buffer as joint (KD-R11).

        Idle/explicit path only — open repair is owned by the store so the
        ``joint_repair_max_per_open`` cap is not applied twice.
        """
        cap = max(0, int(limit))
        repaired = 0
        store_result: dict[str, Any] = {"ok": True}
        store_ok = True
        repair_fn = getattr(self._store, "repair_joint_copies", None)
        if callable(repair_fn) and cap > 0:
            try:
                store_result = dict(repair_fn(limit=cap) or {})
                repaired = int(store_result.get("repaired") or 0)
                if store_result.get("ok") is False:
                    store_ok = False
            except Exception:  # noqa: BLE001
                _LOG.exception("store.repair_joint_copies failed")
                store_result = {"ok": False, "repaired": 0}
                store_ok = False

        # Re-push repaired ready vectors into buffer as joint channel.
        if repaired > 0:
            get_vectors = getattr(self._store, "get_vectors", None)
            list_seed = getattr(self._store, "list_ready_embeddings_for_seed", None)
            with self._lock:
                ranked: list[tuple[str, EmbeddingSet, Atom | None]] = []
                if callable(list_seed):
                    try:
                        ranked = list(list_seed(limit=max(repaired, 64)) or [])
                    except Exception:  # noqa: BLE001
                        ranked = []
                for item in ranked:
                    if len(item) >= 3:
                        _aid, emb, atom = item[0], item[1], item[2]
                    else:
                        continue
                    if emb is None or emb.emb_joint is None:
                        continue
                    entry = _entry_from_emb_and_atom(emb, atom)
                    if entry is not None:
                        self._fresh.buffer.push(entry)
                # Fallback: if store returns repaired ids
                ids = store_result.get("repaired_ids") or []
                if callable(get_vectors) and ids:
                    for aid in ids:
                        try:
                            emb = get_vectors(aid)
                            atom = self._store.get_atom(aid)
                        except Exception:  # noqa: BLE001
                            continue
                        if emb is None:
                            continue
                        entry = _entry_from_emb_and_atom(emb, atom)
                        if entry is not None:
                            self._fresh.buffer.push(entry)

        remaining = self._joint_repair_remaining()
        self._joint_repair_last_batch = repaired
        return {
            "ok": store_ok,
            "backend": "lance",
            "repaired": repaired,
            "joint_repair_remaining": remaining,
            "joint_repair_last_batch": repaired,
            "vectors_by_channel": self._vectors_by_channel(),
            **{
                k: v
                for k, v in store_result.items()
                if k not in ("ok", "repaired")
            },
        }

    def _resolve_channel(self, channel: str) -> str | None:
        ch = (channel or "").strip().lower()
        if ch == "auto":
            concrete, _reason = resolve_search_channel(
                "auto",
                vectors_by_channel=self._vectors_by_channel(),
                joint_repair_remaining=self._joint_repair_remaining(),
            )
            return concrete
        if ch in CHANNEL_SET:
            return ch
        return None

    def _apply_open_policy(self) -> None:
        """KD4 open/restart: full below threshold; seed when above threshold.

        Search stays full until ANN is built (see ``use_full_search``), but the
        buffer is still seeded once corpus ≥ ``full_search_below`` so hybrid is
        ready after the first successful optimize.
        """
        with self._lock:
            n = self._vectors_ready_count()
            thr = max(0, int(self._fresh.ann.full_search_below))
            if self._fresh.ann.force_full or n < thr:
                self._fresh.seed_incomplete = False
                _LOG.debug(
                    "LanceEmbeddingIndex open: full search mode vectors_ready=%s",
                    n,
                )
                return
            # Above threshold: seed last N ready by encoded_at (not glass list).
            try:
                seeded = self._seed_buffer_unlocked(
                    limit=self._fresh.ann.recent_buffer_max
                )
                _LOG.debug(
                    "LanceEmbeddingIndex open: seed n=%s buffer=%s ann_built=%s",
                    seeded,
                    len(self._fresh.buffer),
                    self._fresh.ann_index_built,
                )
            except Exception:  # noqa: BLE001
                self._fresh.seed_incomplete = True
                _LOG.exception("LanceEmbeddingIndex seed on open failed")

    def _seed_buffer_unlocked(self, *, limit: int) -> int:
        """Seed buffer from durable ready rows ordered by encoded_at desc.

        Prefers ``store.list_ready_embeddings_for_seed`` (bypasses glass
        ``LIST_ATOMS_MAX``). Falls back to get_vectors scan only if needed.
        Marks ``seed_incomplete`` when fewer than ``min(limit, vectors_ready)``.
        """
        limit = max(0, int(limit))
        if limit == 0:
            self._fresh.seed_incomplete = False
            return 0

        ranked: list[tuple[str, EmbeddingSet, Atom]] = []
        seed_fn = getattr(self._store, "list_ready_embeddings_for_seed", None)
        if callable(seed_fn):
            try:
                ranked = list(seed_fn(limit=limit) or [])
            except Exception:  # noqa: BLE001
                _LOG.exception("list_ready_embeddings_for_seed failed")
                ranked = []

        if not ranked:
            # Fallback: scan via get_vectors when dedicated API missing.
            # Still avoid glass list_atoms (LIST_ATOMS_MAX starvation).
            get_vectors = getattr(self._store, "get_vectors", None)
            list_fn = getattr(self._store, "list_atoms", None)
            # Prefer emb side-map iteration if store exposes it for tests.
            emb_map = getattr(self._store, "_embs", None) or getattr(
                self._store, "_emb_by_id", None
            )
            if isinstance(emb_map, dict) and callable(get_vectors):
                for atom_id in list(emb_map.keys()):
                    try:
                        emb = get_vectors(atom_id)
                        atom = self._store.get_atom(atom_id)
                    except Exception:  # noqa: BLE001
                        continue
                    if emb is None or atom is None:
                        continue
                    if getattr(atom, "embedding_status", None) != "ready":
                        continue
                    if not embeddings_are_ready(emb):
                        continue
                    ranked.append((atom_id, emb, atom))
                ranked.sort(
                    key=lambda t: (t[1].encoded_at or "", t[0]),
                    reverse=True,
                )
                ranked = ranked[:limit]
            elif callable(list_fn) and callable(get_vectors):
                # Last resort: page list_atoms but warn — may under-seed.
                _LOG.warning(
                    "ANN seed falling back to list_atoms (may hit LIST_ATOMS_MAX)"
                )
                try:
                    atoms = list(
                        list_fn(
                            embedding_status="ready",
                            limit=limit,
                            newest_first=True,
                        )
                        or []
                    )
                except Exception:  # noqa: BLE001
                    atoms = []
                for atom in atoms:
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
                ranked = ranked[:limit]

        seeded = 0
        for _aid, emb, atom in ranked[:limit]:
            entry = _entry_from_emb_and_atom(emb, atom)
            if entry is not None:
                self._fresh.buffer.push(entry)
                seeded += 1

        n_ready = self._vectors_ready_count()
        target = min(limit, n_ready) if n_ready > 0 else limit
        # Incomplete when we could not fill buffer up to policy target.
        self._fresh.seed_incomplete = bool(target > 0 and seeded < target)
        return seeded

    def seed_buffer(self, *, max_ms: int | None = None) -> dict[str, Any]:
        """Continue / re-run buffer seed (idle tick; budget soft)."""
        t0 = time.monotonic()
        with self._lock:
            n = self._seed_buffer_unlocked(limit=self._fresh.ann.recent_buffer_max)
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            if max_ms is not None and elapsed_ms > float(max_ms):
                # Soft budget exceeded with empty buffer → still incomplete.
                if len(self._fresh.buffer) == 0:
                    self._fresh.seed_incomplete = True
            return {
                "ok": True,
                "backend": "lance",
                "seeded": n,
                "recent_buffer": len(self._fresh.buffer),
                "seed_incomplete": self._fresh.seed_incomplete,
                "elapsed_ms": elapsed_ms,
            }

    def _require_joint_for_upsert(self) -> bool:
        if self._settings is None:
            return False
        return bool(
            getattr(self._settings, "embed_joint_for_single_modality", False)
        )

    def upsert(self, embedding_set: EmbeddingSet) -> bool:
        if not isinstance(embedding_set, EmbeddingSet):
            return False
        require_joint = self._require_joint_for_upsert()
        if not embeddings_are_ready(
            embedding_set, require_joint=require_joint
        ):
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
        # KD-R2: resolve auto before CHANNEL_SET check.
        concrete = self._resolve_channel(channel)
        if concrete is None:
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
                channel=concrete,
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
                channel=concrete,
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

    def _create_index_for_channel(
        self, channel: str, *, max_ms: int | None
    ) -> str:
        """Best-effort IVF create for one emb column. Raises on hard failure.

        Returns a short success note. Callers must gate on n>0 / IVF min first
        (KD-R3 — never call when channel has zero vectors).
        """
        col = f"emb_{channel}" if not channel.startswith("emb_") else channel
        ch = col[len("emb_") :] if col.startswith("emb_") else channel
        create = getattr(self._store, "create_vector_index", None)
        if callable(create):
            create(channel=ch, max_ms=max_ms)
            return f"built:{col}"
        table = getattr(self._store, "_table", None)
        if table is not None and hasattr(table, "create_index"):
            try:
                table.create_index(
                    metric="cosine",
                    vector_column_name=col,
                    replace=True,
                )
                return f"built:{col}"
            except TypeError:
                table.create_index(col)
                return f"built:{col}"
        raise RuntimeError("no create_vector_index / create_index on store")

    def optimize(self, *, max_ms: int | None = None) -> dict[str, Any]:
        """Idle-only: best-effort Lance vector index + buffer trim (KD4 / KD-R3).

        Never call mid-hop. Soft max_ms only — ANN create may exceed; caller
        (worker idle tick) owns scheduling.

        KD-R3 safety:
        - Never IVF/create_index when channel has n=0 vectors.
        - Skip IVF when n < ``ann_ivf_min_vectors`` (full scan remains correct).
        - Never claim ``ann_index_built`` or trim buffer on skip / false success.
        """
        t0 = time.monotonic()
        budget = max_ms
        if budget is None:
            budget = self._fresh.ann.optimize_max_ms
        counts = self._vectors_by_channel()
        targets = self._fresh.ann.index_channels or ("joint",)
        ivf_min = max(0, int(self._fresh.ann.ivf_min_vectors))
        notes: list[str] = []
        built_channels: list[str] = []

        for ch in targets:
            channel = str(ch).strip().lower()
            if channel.startswith("emb_"):
                channel = channel[len("emb_") :]
            if channel not in CHANNEL_SET:
                notes.append(f"invalid_channel:{ch}")
                continue
            col = f"emb_{channel}"
            n = int(counts.get(channel) or 0)
            if n == 0:
                notes.append(f"no_vectors:{col}")
                continue
            if n < ivf_min:
                notes.append(f"below_ivf_min:{col}:{n}")
                continue
            try:
                built_note = self._create_index_for_channel(channel, max_ms=budget)
                built_channels.append(channel)
                notes.append(f"{built_note}:{n}")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"error:{col}:{exc!s}"[:160])
                _LOG.debug("LanceEmbeddingIndex.optimize %s: %s", col, exc)

        any_built = bool(built_channels)
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        note = "; ".join(notes) if notes else "ann not built"

        with self._lock:
            n_ready = self._vectors_ready_count()
            if not any_built:
                # CRITICAL: leave ann_index_built unchanged; do NOT trim buffer.
                self._fresh.last_optimize_notes = list(notes)
                return {
                    "ok": True,
                    "backend": "lance",
                    "optimized": False,
                    "vectors_ready": n_ready,
                    "vectors_by_channel": dict(counts),
                    "buffer_trimmed": 0,
                    "recent_buffer": len(self._fresh.buffer),
                    "last_optimize": self._fresh.last_optimize_at,
                    "ann_index_built": self._fresh.ann_index_built,
                    "ann_index_channels": list(self._fresh.ann_index_channels),
                    "index_stale": self._fresh.is_stale(vectors_ready=n_ready),
                    "elapsed_ms": elapsed_ms,
                    "max_ms": budget,
                    "notes": list(notes),
                    "note": note,
                    "last_optimize_notes": list(notes),
                }

            watermark = to_iso_z(datetime.now(UTC))
            removed = self._fresh.mark_optimized(
                watermark=watermark,
                ann_built=True,
                built_channels=built_channels,
                notes=notes,
            )
            return {
                "ok": True,
                "backend": "lance",
                "optimized": True,
                "vectors_ready": n_ready,
                "vectors_by_channel": dict(counts),
                "buffer_trimmed": removed,
                "recent_buffer": len(self._fresh.buffer),
                "last_optimize": self._fresh.last_optimize_at,
                "ann_index_built": self._fresh.ann_index_built,
                "ann_index_channels": list(self._fresh.ann_index_channels),
                "index_stale": self._fresh.is_stale(vectors_ready=n_ready),
                "elapsed_ms": elapsed_ms,
                "max_ms": budget,
                "notes": list(notes),
                "note": note,
                "last_optimize_notes": list(notes),
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
        counts = self._vectors_by_channel()
        remaining = self._joint_repair_remaining()

        # KD-R4: honest search_mode from config + sticky Lance search status.
        backend = "lance_native"
        if self._settings is not None:
            raw = getattr(self._settings, "ann_search_backend", None)
            if isinstance(raw, str) and raw.strip():
                backend = raw.strip().lower()
        lance_ok: bool | None = None
        status_fn = getattr(self._store, "vector_search_status", None)
        if callable(status_fn):
            try:
                st = status_fn() or {}
                backend = str(st.get("ann_search_backend") or backend)
                lance_ok = st.get("lance_search_ok")
            except Exception:  # noqa: BLE001
                pass
        elif hasattr(self._store, "ann_search_backend"):
            try:
                backend = str(self._store.ann_search_backend())
            except Exception:  # noqa: BLE001
                pass

        with self._lock:
            fields = self._fresh.health_fields(
                vectors_ready=int(store_h.get("vectors_ready") or 0),
                vectors_by_channel=counts,
                joint_repair_remaining=remaining,
                joint_repair_last_batch=self._joint_repair_last_batch,
                ann_search_backend=backend,
                lance_search_ok=lance_ok,
                engine="lance",
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
    "SEARCH_CHANNEL_SET",
    "ScoredAtom",
    "ann_settings_from",
    "empty_vectors_by_channel",
    "joint_copy_embedding_set",
    "open_embedding_index",
    "resolve_search_channel",
]
