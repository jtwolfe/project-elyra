"""In-process encode queue with backpressure (Phase 2 PR2 / KD22).

Scope: FIFO of atom_ids, dedupe, drop-oldest → skipped, idle drain.
In scope: enqueue/drain caps; status updates pending/failed/skipped only
(no production ready without EmbeddingIndex — KD8 / PR3).
Out of scope: Lance emb columns, ANN, meal query encode.
"""

from __future__ import annotations

import logging
import time
from collections import deque
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


class EncodeQueue:
    """In-process FIFO of atom_ids; single-writer (presence worker).

    Backpressure (KD22):
    - max distinct ids = ``maxsize`` (encode_queue_max)
    - enqueue dedupe by atom_id
    - at capacity: drop oldest → best-effort ``skipped`` + queue_overflow
    """

    def __init__(self, maxsize: int = 1024) -> None:
        self._maxsize = max(1, int(maxsize))
        self._fifo: deque[str] = deque()
        self._queued: set[str] = set()
        self._dropped_total: int = 0

    @property
    def maxsize(self) -> int:
        return self._maxsize

    def __len__(self) -> int:
        return len(self._fifo)

    def qsize(self) -> int:
        return len(self._fifo)

    def contains(self, atom_id: str) -> bool:
        return atom_id in self._queued

    def dropped_total(self) -> int:
        return self._dropped_total

    def clear(self) -> None:
        self._fifo.clear()
        self._queued.clear()

    def enqueue(self, atom_id: str, *, store: Any | None = None) -> bool:
        """Enqueue ``atom_id`` (dedupe). Return True if newly queued.

        If at ``maxsize``, drop the oldest id first; best-effort mark it
        ``skipped`` with ``meta.embed_error=queue_overflow`` when ``store``
        is provided.
        """
        if not atom_id:
            return False
        if atom_id in self._queued:
            return False
        while len(self._fifo) >= self._maxsize:
            self._drop_oldest(store=store)
        self._fifo.append(atom_id)
        self._queued.add(atom_id)
        return True

    def _drop_oldest(self, *, store: Any | None) -> str | None:
        if not self._fifo:
            return None
        old = self._fifo.popleft()
        self._queued.discard(old)
        self._dropped_total += 1
        _LOG.warning(
            "memory.embed.queue_dropped atom_id=%s remaining=%d",
            old,
            len(self._fifo),
        )
        if store is not None:
            try:
                atom = store.get_atom(old)
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
                    "queue overflow mark-skipped failed atom_id=%s", old
                )
        return old

    def pop_next(self) -> str | None:
        """Pop next atom_id (for tests / custom drain)."""
        if not self._fifo:
            return None
        aid = self._fifo.popleft()
        self._queued.discard(aid)
        return aid

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

        Status transitions in PR2:
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
        while processed < max_items and self._fifo:
            if max_ms > 0 and (time.monotonic() - t0) * 1000.0 >= max_ms:
                break
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
                )
                stats[outcome] = stats.get(outcome, 0) + 1
            except Exception:  # noqa: BLE001 — isolate per item
                _LOG.exception("encode drain item failed atom_id=%s", atom_id)
                stats["failed"] = stats.get("failed", 0) + 1

        stats["remaining"] = len(self._fifo)
        stats["dropped"] = self._dropped_total
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
    ``_process_one`` short-circuits re-encode when ``index is None`` and
    encode_ok matches; when an index is present (PR3), those ids are
    re-processed for upsert → ready. Skips ids already in the queue.
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
            if queue.enqueue(atom.atom_id, store=store):
                enqueued += 1
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "pending scan enqueue failed atom_id=%s",
                getattr(atom, "atom_id", "?"),
            )
    return enqueued


__all__ = [
    "EncodeQueue",
    "scan_pending_into_queue",
]
