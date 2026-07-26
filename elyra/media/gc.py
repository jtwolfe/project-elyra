"""Unbound attachment GC + sandbox mirror reconcile (PR10 / KD13, KD23).

Orphan policy (KD23):
  - Unbound = ``bound_message_id`` is null/None.
  - Delete unbound older than **24h**, **or** when unbound total bytes exceed
    **256 MiB** (oldest first until under budget).
  - Deletes meta + orphaned blobs carefully; best-effort sandbox mirror cleanup.

Reconcile (on ensure):
  - Re-project missing sandbox mirror files from durable meta+blob.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths, resolve_paths
from elyra.media.store import MediaStore, ensure_media_dirs
from elyra.media.types import Attachment

_LOG = logging.getLogger(__name__)

# KD23 product caps.
UNBOUND_MAX_AGE = timedelta(hours=24)
UNBOUND_MAX_BYTES = 256 * 1024 * 1024  # 256 MiB


def _parse_created_at(raw: str | None) -> datetime | None:
    """Parse ISO-8601 created_at (with optional Z) to aware UTC datetime."""
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def list_unbound(store: MediaStore) -> list[Attachment]:
    """Return unbound attachments sorted oldest-first (by created_at, then id)."""
    unbound: list[Attachment] = []
    for att_id in store.list_meta_ids():
        att = store.get(att_id)
        if att is None:
            continue
        if att.bound_message_id is None:
            unbound.append(att)

    def sort_key(a: Attachment) -> tuple[float, str]:
        dt = _parse_created_at(a.created_at)
        ts = dt.timestamp() if dt is not None else 0.0
        return (ts, a.id)

    unbound.sort(key=sort_key)
    return unbound


def _remove_sandbox_mirror(att: Attachment, *, paths: ElyraPaths) -> None:
    """Best-effort remove projected sandbox file + empty att dir."""
    try:
        from elyra.media.project import projected_path_for

        dest = projected_path_for(att, paths=paths)
    except (ValueError, OSError) as exc:
        _LOG.debug("gc: mirror path resolve failed for %s: %s", att.id, exc)
        return
    try:
        if dest.exists() or dest.is_symlink():
            dest.unlink(missing_ok=True)
        parent = dest.parent
        # Remove att_id directory if empty.
        if parent.is_dir():
            try:
                next(parent.iterdir())
            except StopIteration:
                parent.rmdir()
            except OSError:
                pass
    except OSError as exc:
        _LOG.debug("gc: mirror unlink failed for %s: %s", att.id, exc)


def delete_unbound_attachment(store: MediaStore, att: Attachment) -> bool:
    """Delete one unbound attachment: meta + orphan blob + sandbox mirror.

    Refuses to delete when ``bound_message_id`` is set (safety).
    """
    if att.bound_message_id is not None:
        _LOG.warning("gc: refusing to delete bound attachment %s", att.id)
        return False
    _remove_sandbox_mirror(att, paths=store.paths)
    return store.delete_attachment(att.id, remove_blob_if_orphan=True)


def gc_unbound_attachments(
    store: MediaStore | None = None,
    *,
    paths: ElyraPaths | None = None,
    now: datetime | None = None,
    max_age: timedelta = UNBOUND_MAX_AGE,
    max_bytes: int = UNBOUND_MAX_BYTES,
) -> dict[str, Any]:
    """Run unbound orphan GC (age and/or byte budget).

    Returns summary: ``{deleted, deleted_ids, unbound_remaining, unbound_bytes}``.
    """
    media = store or MediaStore(paths or resolve_paths())
    media.ensure_dirs()
    clock = now or _now_utc()
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)

    unbound = list_unbound(media)
    to_delete: list[Attachment] = []
    seen: set[str] = set()

    # 1) Age policy: older than max_age.
    age_cutoff = clock - max_age
    for att in unbound:
        dt = _parse_created_at(att.created_at)
        # Missing/unparseable created_at: treat as old enough to GC (hygiene).
        if dt is None or dt <= age_cutoff:
            to_delete.append(att)
            seen.add(att.id)

    # 2) Byte budget: if remaining unbound (after age deletes) still over max,
    #    delete oldest first until under budget.
    remaining = [a for a in unbound if a.id not in seen]
    total = sum(max(0, int(a.byte_size or 0)) for a in remaining)
    if total > max_bytes:
        for att in remaining:  # already oldest-first
            if total <= max_bytes:
                break
            to_delete.append(att)
            seen.add(att.id)
            total -= max(0, int(att.byte_size or 0))

    deleted_ids: list[str] = []
    for att in to_delete:
        try:
            if delete_unbound_attachment(media, att):
                deleted_ids.append(att.id)
                _LOG.info(
                    "media.gc unbound id=%s bytes=%s created_at=%s",
                    att.id,
                    att.byte_size,
                    att.created_at,
                )
        except OSError as exc:
            _LOG.warning("media.gc delete failed id=%s: %s", att.id, exc)

    after = list_unbound(media)
    after_bytes = sum(max(0, int(a.byte_size or 0)) for a in after)
    return {
        "deleted": len(deleted_ids),
        "deleted_ids": deleted_ids,
        "unbound_remaining": len(after),
        "unbound_bytes": after_bytes,
    }


def reconcile_mirrors(
    store: MediaStore | None = None,
    *,
    paths: ElyraPaths | None = None,
) -> dict[str, Any]:
    """Re-project missing sandbox mirrors from durable meta + blobs.

    Lightweight: only projects when dest missing or empty. Skips when blob gone.
    """
    media = store or MediaStore(paths or resolve_paths())
    media.ensure_dirs()
    projected = 0
    skipped = 0
    failed = 0
    for att_id in media.list_meta_ids():
        att = media.get(att_id)
        if att is None or not att.sha256:
            skipped += 1
            continue
        blob = media.blob_path(att.sha256)
        if not blob.is_file():
            skipped += 1
            continue
        try:
            from elyra.media.project import projected_path_for

            dest = projected_path_for(att, paths=media.paths)
        except (ValueError, OSError):
            failed += 1
            continue
        if dest.is_file() and dest.stat().st_size > 0:
            skipped += 1
            continue
        try:
            media._best_effort_project(att)  # noqa: SLF001 — intentional reproject
            if dest.is_file():
                projected += 1
            else:
                failed += 1
        except OSError as exc:
            _LOG.warning("media.reconcile project failed id=%s: %s", att_id, exc)
            failed += 1
    if projected:
        _LOG.info("media.reconcile projected=%s failed=%s", projected, failed)
    return {"projected": projected, "skipped": skipped, "failed": failed}


def media_stats(
    store: MediaStore | None = None,
    *,
    paths: ElyraPaths | None = None,
) -> dict[str, Any]:
    """Optional status counters: total/unbound counts and bytes (no filenames)."""
    media = store or MediaStore(paths or resolve_paths())
    if not media.meta_dir.is_dir():
        return {
            "count": 0,
            "bytes_total": 0,
            "unbound_count": 0,
            "unbound_bytes": 0,
        }
    count = 0
    bytes_total = 0
    unbound_count = 0
    unbound_bytes = 0
    for att_id in media.list_meta_ids():
        att = media.get(att_id)
        if att is None:
            continue
        count += 1
        sz = max(0, int(att.byte_size or 0))
        bytes_total += sz
        if att.bound_message_id is None:
            unbound_count += 1
            unbound_bytes += sz
    return {
        "count": count,
        "bytes_total": bytes_total,
        "unbound_count": unbound_count,
        "unbound_bytes": unbound_bytes,
    }


def ensure_media(
    paths: ElyraPaths | None = None,
    *,
    reconcile: bool = True,
    gc: bool = True,
) -> Path:
    """Create media dirs; optional lightweight reconcile + unbound GC (PR10).

    Called from process start / ``ensure_data_dirs``. Safe when store empty.
    """
    layout = paths or resolve_paths()
    root = ensure_media_dirs(layout)
    store = MediaStore(layout)
    if reconcile:
        try:
            reconcile_mirrors(store)
        except OSError as exc:
            _LOG.warning("media.reconcile on ensure failed: %s", exc)
    if gc:
        try:
            gc_unbound_attachments(store)
        except OSError as exc:
            _LOG.warning("media.gc on ensure failed: %s", exc)
    return root
