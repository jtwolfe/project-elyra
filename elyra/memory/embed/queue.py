"""In-process encode queue with priority lanes + backpressure.

Scope: dual bulk lanes (P1 atom_create > P2 catchup), dedupe/promote,
drop P2-then-P1 → skipped, budgeted drain. Thread-safe via RLock.
In scope: enqueue/drain caps; status updates pending/failed/skipped only
(no production ready without EmbeddingIndex — KD8 / PR3).
Out of scope: Lance emb columns, ANN, meal query encode, EncodeWorker.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from enum import Enum
from typing import Any, Mapping, Protocol

from elyra.memory.embed.encode import (
    content_fingerprint,
    encode_atom,
    is_embeddable,
)
from elyra.memory.types import Atom, atom_replace

_LOG = logging.getLogger(__name__)

# meta keys written by drain / overflow (scalar atom only — no vectors on Atom).
_META_ATTEMPTS = "embed_attempts"
_META_ERROR = "embed_error"
_META_ENCODE_OK = "embed_encode_ok"
_META_CONTENT_FP = "embed_content_fp"
_META_ENCODED_AT = "embed_encoded_at"
_META_CHANNELS = "embed_channels"

_OVERFLOW_ERROR = "queue_overflow"


class EncodePriority(str, Enum):
    """Bulk encode queue lanes (lookup priority is gate-only, not a lane)."""

    ATOM_CREATE = "atom_create"  # P1 — live creates; preferred bulk
    CATCHUP = "catchup"  # P2 — historical scan / restart backlog


class _EmbeddingIndexLike(Protocol):
    """Minimal index surface used by drain (PR3 EmbeddingIndex)."""

    def upsert(self, embedding_set: Any) -> bool:
        ...


def _mark_atom_status(
    store: Any,
    atom: Atom,
    *,
    status: str,
    meta_updates: Mapping[str, Any] | None = None,
) -> Atom | None:
    """Best-effort put of atom with new embedding_status / meta. Never raises.

    Uses ``notify=False`` when supported so encode status writes do not
    re-fire the store write hook (avoids re-enqueue loops).
    """
    try:
        meta = dict(atom.meta or {})
        if meta_updates:
            meta.update(meta_updates)
        updated = atom_replace(atom, embedding_status=status, meta=meta)
        try:
            return store.put_atom(updated, notify=False)
        except TypeError:
            return store.put_atom(updated)
    except Exception:  # noqa: BLE001
        _LOG.exception(
            "encode queue status update failed atom_id=%s status=%s",
            atom.atom_id,
            status,
        )
        return None


def _coerce_priority(priority: EncodePriority | str) -> EncodePriority:
    if isinstance(priority, EncodePriority):
        return priority
    return EncodePriority(str(priority))


class EncodeQueue:
    """Thread-safe dual-lane encode queue (P1 atom_create > P2 catchup).

    Backpressure (KD22 refined):
    - max distinct ids = ``maxsize`` across both lanes (encode_queue_max)
    - enqueue dedupe by atom_id; promote catchup → atom_create
    - at capacity: drop oldest P2 first, then oldest P1 → best-effort
      ``skipped`` + queue_overflow
    """

    def __init__(self, maxsize: int = 1024) -> None:
        self._maxsize = max(1, int(maxsize))
        self._lock = threading.RLock()
        self._p1: deque[str] = deque()  # atom_create
        self._p2: deque[str] = deque()  # catchup
        self._queued: set[str] = set()
        self._lane: dict[str, EncodePriority] = {}
        self._dropped_total: int = 0

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def __len__(self) -> int:
        with self._lock:
            return len(self._queued)

    def qsize(self) -> int:
        with self._lock:
            return len(self._queued)

    def contains(self, atom_id: str) -> bool:
        with self._lock:
            return atom_id in self._queued

    def dropped_total(self) -> int:
        with self._lock:
            return self._dropped_total

    def depth_by_priority(self) -> dict[str, int]:
        """Return current depth per bulk lane (for health / tests)."""
        with self._lock:
            return {
                EncodePriority.ATOM_CREATE.value: len(self._p1),
                EncodePriority.CATCHUP.value: len(self._p2),
            }

    def clear(self) -> None:
        with self._lock:
            self._p1.clear()
            self._p2.clear()
            self._queued.clear()
            self._lane.clear()

    def enqueue(
        self,
        atom_id: str,
        *,
        priority: EncodePriority | str = EncodePriority.ATOM_CREATE,
        store: Any | None = None,
    ) -> bool:
        """Enqueue ``atom_id`` (dedupe / promote). Return True if new or promoted.

        Higher bulk priority wins: catchup → atom_create is a promote.
        At capacity, drop oldest from P2 then P1; best-effort mark dropped
        ``skipped`` with ``meta.embed_error=queue_overflow`` when ``store``
        is provided (store I/O runs **outside** the queue lock).
        """
        if not atom_id:
            return False
        pri = _coerce_priority(priority)
        dropped: list[str] = []
        changed = False

        with self._lock:
            if atom_id in self._queued:
                current = self._lane.get(atom_id, EncodePriority.CATCHUP)
                if (
                    current == EncodePriority.CATCHUP
                    and pri == EncodePriority.ATOM_CREATE
                ):
                    # Promote P2 → P1 (membership unchanged; lane changes).
                    try:
                        self._p2.remove(atom_id)
                    except ValueError:
                        pass
                    self._p1.append(atom_id)
                    self._lane[atom_id] = EncodePriority.ATOM_CREATE
                    changed = True
                # Already at same or higher priority → no-op.
            else:
                while len(self._queued) >= self._maxsize:
                    old = self._drop_oldest_locked()
                    if old is None:
                        break
                    dropped.append(old)
                if len(self._queued) >= self._maxsize:
                    # Could not free a slot (empty queue race); refuse enqueue.
                    pass
                else:
                    if pri == EncodePriority.ATOM_CREATE:
                        self._p1.append(atom_id)
                    else:
                        self._p2.append(atom_id)
                    self._queued.add(atom_id)
                    self._lane[atom_id] = pri
                    changed = True

        # Mark overflow + log outside lock (avoid lock order with store;
        # keep critical-section short under overflow storms).
        remaining = self.qsize()
        for old_id in dropped:
            _LOG.warning(
                "memory.embed.queue_dropped atom_id=%s remaining=%d",
                old_id,
                remaining,
            )
            self._mark_overflow(store, old_id)

        return changed

    def _drop_oldest_locked(self) -> str | None:
        """Drop oldest P2 then P1. Caller holds ``_lock``. No I/O or logging."""
        if self._p2:
            old = self._p2.popleft()
        elif self._p1:
            old = self._p1.popleft()
        else:
            return None
        self._queued.discard(old)
        self._lane.pop(old, None)
        self._dropped_total += 1
        return old

    def _mark_overflow(self, store: Any | None, atom_id: str) -> None:
        if store is None:
            return
        # Re-enqueued after drop (race with scan/hook) — do not skip legitimate work.
        if self.contains(atom_id):
            return
        try:
            atom = store.get_atom(atom_id)
            if atom is not None and atom.embedding_status in (
                "pending",
                "none",
                "failed",
            ):
                _mark_atom_status(
                    store,
                    atom,
                    status="skipped",
                    meta_updates={_META_ERROR: _OVERFLOW_ERROR},
                )
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "queue overflow mark-skipped failed atom_id=%s", atom_id
            )

    def pop_next(self) -> str | None:
        """Pop next atom_id (P1 then P2). For tests / custom drain."""
        item = self.pop_next_bulk()
        return item[0] if item is not None else None

    def pop_next_bulk(self) -> tuple[str, EncodePriority] | None:
        """Pop next bulk item: P1 first, then P2. Under lock."""
        with self._lock:
            if self._p1:
                aid = self._p1.popleft()
                pri = EncodePriority.ATOM_CREATE
            elif self._p2:
                aid = self._p2.popleft()
                pri = EncodePriority.CATCHUP
            else:
                return None
            self._queued.discard(aid)
            self._lane.pop(aid, None)
            return (aid, pri)

    def drain(
        self,
        store: Any,
        embedder: Any,
        index: Any | None = None,
        *,
        max_ms: int = 100,
        max_items: int = 4,
        media_store: Any | None = None,
        max_attempts: int = 3,
        settings: Any | None = None,
    ) -> dict[str, int]:
        """Drain up to ``max_items`` within ``max_ms``. Never raises.

        Pops under the queue lock; encodes / store I/O run **outside** the lock.
        Status transitions:
        - empty / kind skip → ``skipped``
        - encode exception / invalid → ``failed`` (or stay pending while
          attempts < max_attempts)
        - encode ok + index upsert → ``ready`` (tests / PR3 index only)
        - encode ok without index → leave ``pending``; set meta.embed_encode_ok
          so we do not re-encode every tick (KD8 — no false ready)

        Returns counters: ok, failed, skipped, remaining, dropped, processed.
        """
        media_max_bytes = 8_000_000
        media_max_seconds: int | None = 30
        single_modality_joint = True
        if settings is not None:
            max_ms = int(getattr(settings, "encode_max_ms_per_tick", max_ms))
            max_items = int(getattr(settings, "encode_max_items_per_tick", max_items))
            max_attempts = int(getattr(settings, "encode_max_attempts", max_attempts))
            media_max_bytes = int(
                getattr(settings, "embed_media_max_bytes", media_max_bytes)
            )
            media_max_seconds = int(
                getattr(settings, "embed_media_max_seconds", media_max_seconds or 30)
            )
            single_modality_joint = bool(
                getattr(settings, "embed_joint_for_single_modality", True)
            )

        stats: dict[str, int] = {
            "ok": 0,
            "failed": 0,
            "skipped": 0,
            "remaining": 0,
            "dropped": 0,
            "processed": 0,
        }
        t0 = time.monotonic()
        max_ms = max(0, int(max_ms))
        max_items = max(0, int(max_items))
        max_attempts = max(1, int(max_attempts))

        # Encoder closed / unhealthy → skip items soft (leave pending for later).
        embedder_ok = True
        try:
            health = embedder.health() if embedder is not None else {"ok": False}
            if not isinstance(health, Mapping) or not health.get("ok"):
                embedder_ok = False
        except Exception:  # noqa: BLE001
            embedder_ok = False

        processed = 0
        while processed < max_items:
            if max_ms > 0 and (time.monotonic() - t0) * 1000.0 >= max_ms:
                break
            # Membership pop under lock; encode outside lock.
            atom_id = self.pop_next()
            if atom_id is None:
                break
            processed += 1
            stats["processed"] = processed
            try:
                outcome = self._process_one(
                    store,
                    embedder,
                    index,
                    atom_id,
                    media_store=media_store,
                    max_attempts=max_attempts,
                    embedder_ok=embedder_ok,
                    media_max_bytes=media_max_bytes,
                    media_max_seconds=media_max_seconds,
                    single_modality_joint=single_modality_joint,
                )
                stats[outcome] = stats.get(outcome, 0) + 1
            except Exception:  # noqa: BLE001 — isolate per item
                _LOG.exception("encode drain item failed atom_id=%s", atom_id)
                stats["failed"] = stats.get("failed", 0) + 1

        stats["remaining"] = self.qsize()
        stats["dropped"] = self.dropped_total()
        return stats

    def _process_one(
        self,
        store: Any,
        embedder: Any,
        index: Any | None,
        atom_id: str,
        *,
        media_store: Any | None,
        max_attempts: int,
        embedder_ok: bool,
        media_max_bytes: int = 8_000_000,
        media_max_seconds: int | None = 30,
        single_modality_joint: bool = True,
    ) -> str:
        atom = store.get_atom(atom_id)
        if atom is None:
            return "skipped"

        status = atom.embedding_status or "none"
        if status in ("ready", "skipped"):
            return "skipped"

        # Already successfully encoded and content unchanged:
        # - index is None → leave pending (KD8); skip re-encode thrash
        # - index present → fall through to re-encode + upsert so PR3 can
        #   promote to ready (vectors are not stored on the Atom)
        fp = content_fingerprint(atom)
        meta = dict(atom.meta or {})
        if (
            meta.get(_META_ENCODE_OK)
            and meta.get(_META_CONTENT_FP) == fp
            and status == "pending"
            and index is None
        ):
            return "ok"

        if not is_embeddable(atom):
            _mark_atom_status(
                store,
                atom,
                status="skipped",
                meta_updates={_META_ERROR: "no modalities"},
            )
            return "skipped"

        if not embedder_ok or embedder is None:
            # Leave pending so a later tick with a healthy encoder can run.
            # Permanent unavailability is operator-driven (embed_enabled off).
            return "skipped"

        result = encode_atom(
            embedder,
            atom,
            media_store=media_store,
            media_max_bytes=media_max_bytes,
            media_max_seconds=media_max_seconds,
            single_modality_joint=single_modality_joint,
        )
        attempts = int(meta.get(_META_ATTEMPTS) or 0) + 1
        updates: dict[str, Any] = {_META_ATTEMPTS: attempts}

        # Media-only atom with unresolved media: leave pending (retry when
        # MediaStore is available). Do not burn toward permanent failed/skipped.
        if result.error == "media_unresolved":
            updates[_META_ERROR] = "media_unresolved"
            # Do not increment attempts for unresolved media.
            updates[_META_ATTEMPTS] = int(meta.get(_META_ATTEMPTS) or 0)
            _mark_atom_status(
                store, atom, status="pending", meta_updates=updates
            )
            return "skipped"

        if result.status == "skipped":
            updates[_META_ERROR] = result.error or "skipped"
            _mark_atom_status(store, atom, status="skipped", meta_updates=updates)
            return "skipped"

        if result.status == "failed" or result.embeddings is None:
            updates[_META_ERROR] = result.error or "encode_failed"
            if attempts >= max_attempts:
                _mark_atom_status(
                    store, atom, status="failed", meta_updates=updates
                )
                return "failed"
            # Stay pending for retry via idle scan.
            updates.pop(_META_ENCODE_OK, None)
            _mark_atom_status(
                store, atom, status="pending", meta_updates=updates
            )
            return "failed"

        # Encode produced vectors.
        emb = result.embeddings
        updates[_META_ENCODE_OK] = True
        updates[_META_CONTENT_FP] = fp
        updates[_META_ENCODED_AT] = emb.encoded_at or ""
        updates[_META_CHANNELS] = list(emb.channels_present)
        updates.pop(_META_ERROR, None)

        if index is not None:
            try:
                upsert = getattr(index, "upsert", None)
                ok = False
                if callable(upsert):
                    # PR3 EmbeddingIndex: upsert(EmbeddingSet) -> bool (True if ready).
                    # PR2 test doubles: upsert(atom_id, embeddings) -> bool.
                    # Only explicit truthy confirms vectors were held (KD20);
                    # None / False must NOT mark embedding_status=ready.
                    try:
                        result = upsert(emb)
                    except TypeError:
                        result = upsert(atom_id, emb)
                    ok = bool(result)
                if ok:
                    _mark_atom_status(
                        store, atom, status="ready", meta_updates=updates
                    )
                    return "ok"
            except Exception:  # noqa: BLE001
                _LOG.exception(
                    "embedding index upsert failed atom_id=%s", atom_id
                )
                updates[_META_ERROR] = "index_upsert_failed"
                # Fall through: leave pending with encode_ok so we do not
                # thrash re-encode; index can be retried in a later PR.

        # PR2 production path: no durable vectors → stay pending (KD8).
        _mark_atom_status(store, atom, status="pending", meta_updates=updates)
        return "ok"


def scan_pending_into_queue(
    store: Any,
    queue: EncodeQueue,
    *,
    limit: int = 16,
) -> int:
    """Backstop: enqueue atoms with embedding_status=pending (KD16).

    Includes **all** pending atoms (including ``embed_encode_ok``).
    Enqueues at **catchup** priority (P2). ``_process_one`` short-circuits
    re-encode when ``index is None`` and encode_ok matches; when an index is
    present (PR3), those ids are re-processed for upsert → ready. Skips ids
    already in the queue (except promote is N/A for catchup re-scan).
    Returns number newly enqueued. Never raises.
    """
    enqueued = 0
    try:
        rows = store.list_atoms(
            embedding_status="pending",
            limit=max(0, int(limit)),
            newest_first=True,
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("list_atoms pending scan failed")
        return 0
    for atom in rows:
        try:
            if queue.contains(atom.atom_id):
                continue
            if queue.enqueue(
                atom.atom_id,
                store=store,
                priority=EncodePriority.CATCHUP,
            ):
                enqueued += 1
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "pending scan enqueue failed atom_id=%s",
                getattr(atom, "atom_id", "?"),
            )
    return enqueued


# Ladder rewrites always set summary embedding_status="none"; encoding them
# would thrash. Catch-up targets experience atoms only.
_CATCHUP_SKIP_KINDS: frozenset[str] = frozenset(
    {"summary", "moment_meta", "parcel"}
)


def catchup_none_atoms_for_encode(
    store: Any,
    *,
    limit: int = 32,
    horizon_hours: float = 168.0,
    now_iso: str | None = None,
) -> int:
    """Mark embeddable historical ``none`` atoms as ``pending`` (OQ4).

    Bounded by ``limit`` per call and optional ``horizon_hours`` on ``t_start``.
    Never raises. Returns number flipped to pending.
    """
    from datetime import UTC, datetime, timedelta

    from elyra.memory.types import parse_iso_z, to_iso_z

    flipped = 0
    try:
        # Over-fetch then filter; list_atoms has a hard cap but dogfood scale is small.
        rows = store.list_atoms(
            embedding_status="none",
            limit=max(0, min(int(limit) * 4, 200)),
            newest_first=True,
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("list_atoms none catch-up failed")
        return 0

    if now_iso:
        try:
            now_dt = parse_iso_z(now_iso)
        except Exception:  # noqa: BLE001
            now_dt = datetime.now(tz=UTC)
    else:
        now_dt = datetime.now(tz=UTC)
    horizon = max(0.0, float(horizon_hours))
    cutoff = now_dt - timedelta(hours=horizon) if horizon > 0 else None

    for atom in rows:
        if flipped >= max(0, int(limit)):
            break
        try:
            if (atom.embedding_status or "none") != "none":
                continue
            if atom.kind in _CATCHUP_SKIP_KINDS:
                continue
            if not is_embeddable(atom):
                continue
            if cutoff is not None:
                try:
                    t0 = parse_iso_z(atom.t_start)
                except Exception:  # noqa: BLE001
                    continue
                if t0 < cutoff:
                    continue
            marked = _mark_atom_status(store, atom, status="pending")
            if marked is not None:
                flipped += 1
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "none catch-up failed atom_id=%s",
                getattr(atom, "atom_id", "?"),
            )
    if flipped:
        _LOG.info("embed catch-up marked %d none→pending atoms", flipped)
    return flipped


__all__ = [
    "EncodePriority",
    "EncodeQueue",
    "catchup_none_atoms_for_encode",
    "scan_pending_into_queue",
]
