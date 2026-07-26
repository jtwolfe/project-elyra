"""Shared identity/user layout helpers.

Scope: path jail, version filenames, atomic write, sha256, user id mint (K18).
In scope: safe user_id, version id mint, read_text_or_empty, write_atomic,
meta field allow-lists, body size cap, version GC limit.
Out of scope: promote gates, tools, glass, orient inject.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# Public version_id == archive filename stem only (K4).
VERSION_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[0-9a-f]{6}$")

# Single segment: letter/digit start; alnum, dot, underscore, hyphen after.
USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

MAX_BODY_BYTES = 64 * 1024
VERSION_GC_LIMIT = 50

# Allowed keys in meta.draft_meta after stripping operational keys.
ALLOWED_DRAFT_META_KEYS = frozenset(
    {
        "display_name",
        "goes_by",
        "full_name",
        "real_name_known",
        "provisional",
    }
)

# Never stored in draft_meta; never merged into top-level meta on promote.
OPERATIONAL_META_KEYS = frozenset({"force_full_name", "record_name_nudge"})


def content_sha256(text: str) -> str:
    """SHA-256 hex digest of UTF-8 text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mint_version_id(now: datetime | None = None) -> str:
    """Return e.g. ``20260726T153045Z_a1b2c3`` (filename stem)."""
    ts = now if now is not None else datetime.now(UTC)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    else:
        ts = ts.astimezone(UTC)
    compact = ts.strftime("%Y%m%dT%H%M%SZ")
    return f"{compact}_{secrets.token_hex(3)}"


def utc_now_iso() -> str:
    """UTC timestamp as ISO-8601 with offset (matches goals store style)."""
    return datetime.now(UTC).isoformat()


def validate_user_id(user_id: str) -> str:
    """Return ``user_id`` if it is a single safe path segment.

    Requires ``^[A-Za-z0-9][A-Za-z0-9._-]*$`` so blank/whitespace, control
    chars (including NUL), separators, and ``.`` / ``..`` are rejected.
    """
    if not isinstance(user_id, str) or not USER_ID_RE.fullmatch(user_id):
        raise ValueError(f"invalid user_id: {user_id!r}")
    path = Path(user_id)
    if path.is_absolute() or len(path.parts) != 1:
        raise ValueError(f"invalid user_id: {user_id!r}")
    return user_id


def read_text_or_empty(path: Path) -> str:
    """Return file text or empty string if missing/unreadable."""
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_atomic(path: Path, text: str) -> None:
    """Write via unique temp + replace (caller holds store lock)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    """Serialize mapping as pretty JSON and write atomically."""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    write_atomic(path, text)


def load_json_object(path: Path) -> dict[str, Any] | None:
    """Load a JSON object from path, or None if missing/invalid."""
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def strip_operational_keys(
    meta_patch: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split meta_patch into (draft_meta fields, operational flags).

    Only allowed draft keys land in draft_meta; operational keys are
    returned separately and never stored in draft_meta.
    """
    if not meta_patch:
        return {}, {}
    draft: dict[str, Any] = {}
    ops: dict[str, Any] = {}
    for key, value in meta_patch.items():
        if key in OPERATIONAL_META_KEYS:
            ops[key] = value
        elif key in ALLOWED_DRAFT_META_KEYS:
            draft[key] = value
        # Unknown keys silently dropped (fail soft for forward compat).
    return draft, ops


def full_name_change_requires_force(
    current_full_name: Any,
    patch_full_name: Any,
) -> bool:
    """True when patch sets/changes full_name vs current (incl. null→value)."""
    cur = current_full_name if current_full_name is not None else None
    new = patch_full_name if patch_full_name is not None else None
    # Normalize empty string to None for compare.
    if isinstance(cur, str) and not cur.strip():
        cur = None
    if isinstance(new, str) and not new.strip():
        new = None
    if cur is None and new is None:
        return False
    if cur is None and new is not None:
        return True
    if cur is not None and new is None:
        return True
    return str(cur) != str(new)


def slugify_goes_by(goes_by: str) -> str | None:
    """Slugify goes_by for user_id candidate; None if unusable."""
    if not isinstance(goes_by, str):
        return None
    chars: list[str] = []
    for ch in goes_by.lower():
        if ch.isalnum():
            chars.append(ch)
        else:
            chars.append("_")
    slug = "".join(chars)
    while "__" in slug:
        slug = slug.replace("__", "_")
    slug = slug.strip("_.")
    if not slug:
        return None
    # Path jail requires alnum start; prefix if needed.
    if not slug[0].isalnum():
        slug = "u_" + slug
    if not USER_ID_RE.fullmatch(slug):
        return None
    return slug[:48]


def mint_user_id(
    goes_by: str,
    existing_ids: set[str],
    *,
    user_id: str | None = None,
) -> str:
    """Mint a path-jail-safe user_id (K18).

    1. Explicit user_id: validate; free → return; taken → raise user_id_exists.
    2. Else slugify goes_by; free → return.
    3. Collision: candidate_ + 4hex up to 16 tries, else guest_ + 6hex.
    """
    if user_id is not None and isinstance(user_id, str) and user_id.strip():
        safe = validate_user_id(user_id.strip())
        if safe in existing_ids:
            raise ValueError(f"user_id_exists: {safe!r}")
        return safe

    candidate = slugify_goes_by(goes_by) if goes_by else None
    if candidate is None:
        # Fall through to guest random.
        for _ in range(32):
            guest = f"guest_{secrets.token_hex(3)}"
            if guest not in existing_ids:
                return guest
        # Extremely unlikely
        return f"guest_{secrets.token_hex(8)}"

    if candidate not in existing_ids:
        return candidate

    base = candidate[:40]
    for _ in range(16):
        trial = f"{base}_{secrets.token_hex(2)}"
        if trial not in existing_ids:
            return trial
    for _ in range(32):
        guest = f"guest_{secrets.token_hex(3)}"
        if guest not in existing_ids:
            return guest
    return f"guest_{secrets.token_hex(8)}"


def rebuild_versions_index(versions_dir: Path) -> list[dict[str, Any]]:
    """Rebuild meta.versions from ``versions/*.md`` (index heal).

    Scans directory for files matching VERSION_ID_RE; recomputes sha256/bytes.
    Sorted by version_id (UTC compact prefix → chronological).
    """
    if not versions_dir.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(versions_dir.iterdir()):
        if not path.is_file() or path.suffix != ".md":
            continue
        stem = path.stem
        if not VERSION_ID_RE.fullmatch(stem):
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            continue
        raw = body.encode("utf-8")
        entries.append(
            {
                "version_id": stem,
                "promoted_at": None,
                "sha256": content_sha256(body),
                "bytes": len(raw),
            }
        )
    return entries


def heal_versions_index(
    meta: dict[str, Any],
    versions_dir: Path,
) -> dict[str, Any]:
    """Reconcile meta.versions with versions/ directory (K17).

    Policy (meta authoritative when index is usable):

    1. Drop index rows whose files are missing.
    2. If cleaned index is non-empty: keep it; **delete disk orphans**
       (files not in index). Prevents re-inflation after deferred GC
       (meta trimmed but drop files not yet unlinked).
    3. If cleaned index is empty but disk has version files: rebuild
       index from directory (index-loss recovery).

    Returns (possibly updated) meta. Logs a warning on rebuild/orphan prune.
    """
    index = meta.get("versions")
    if not isinstance(index, list):
        index = []

    disk_ids: set[str] = set()
    if versions_dir.is_dir():
        for path in versions_dir.iterdir():
            if path.is_file() and path.suffix == ".md":
                if VERSION_ID_RE.fullmatch(path.stem):
                    disk_ids.add(path.stem)

    index_ids: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for row in index:
        if not isinstance(row, dict):
            continue
        vid = row.get("version_id")
        if not isinstance(vid, str) or not VERSION_ID_RE.fullmatch(vid):
            continue
        if vid not in disk_ids:
            # Orphan index row — drop
            continue
        index_ids.add(vid)
        cleaned.append(row)

    disk_orphans = disk_ids - index_ids

    # Case: usable meta index (non-empty after dropping missing files).
    # Meta is authoritative — prune disk files not cited by the index.
    if cleaned:
        if disk_orphans or len(cleaned) != len(index):
            if disk_orphans:
                logger.warning(
                    "identity versions dir has orphans under %s; "
                    "deleting %d file(s) not in meta index",
                    versions_dir,
                    len(disk_orphans),
                )
                prune_orphan_version_files(cleaned, versions_dir)
            meta["versions"] = cleaned
            return meta
        meta["versions"] = cleaned
        return meta

    # Case: empty/broken index but disk has archives — rebuild from dir.
    if disk_ids:
        logger.warning(
            "identity versions index empty/missing under %s; rebuilding from dir",
            versions_dir,
        )
        old_by_id = {
            r["version_id"]: r
            for r in index
            if isinstance(r, dict) and isinstance(r.get("version_id"), str)
        }
        rebuilt = rebuild_versions_index(versions_dir)
        for row in rebuilt:
            old = old_by_id.get(row["version_id"])
            if old and old.get("promoted_at"):
                row["promoted_at"] = old["promoted_at"]
        meta["versions"] = rebuilt
        return meta

    meta["versions"] = []
    return meta


def prune_orphan_version_files(
    versions: list[dict[str, Any]],
    versions_dir: Path,
) -> None:
    """Delete versions/*.md files not cited by the given index (meta authoritative)."""
    keep_ids: set[str] = set()
    for row in versions:
        if not isinstance(row, dict):
            continue
        vid = row.get("version_id")
        if isinstance(vid, str) and VERSION_ID_RE.fullmatch(vid):
            keep_ids.add(vid)
    if not versions_dir.is_dir():
        return
    for path in list(versions_dir.iterdir()):
        if not path.is_file() or path.suffix != ".md":
            continue
        if not VERSION_ID_RE.fullmatch(path.stem):
            continue
        if path.stem not in keep_ids:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def trim_versions_index(
    versions: list[dict[str, Any]],
    *,
    limit: int = VERSION_GC_LIMIT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split versions into (keep, drop) without touching disk.

    Index order is append-oldest-first; drop from the front when over limit.
    Callers must persist ``keep`` in meta before deleting drop files (Issue 3).
    """
    if len(versions) <= limit:
        return versions, []
    drop = versions[: len(versions) - limit]
    keep = versions[len(versions) - limit :]
    return keep, drop


def delete_version_files(
    drop: list[dict[str, Any]],
    versions_dir: Path,
) -> None:
    """Best-effort delete archived version bodies for dropped index rows."""
    for row in drop:
        vid = row.get("version_id") if isinstance(row, dict) else None
        if not isinstance(vid, str):
            continue
        path = versions_dir / f"{vid}.md"
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def gc_versions(
    versions: list[dict[str, Any]],
    versions_dir: Path,
    *,
    limit: int = VERSION_GC_LIMIT,
) -> list[dict[str, Any]]:
    """Trim index and delete dropped files immediately.

    Prefer :func:`trim_versions_index` + commit meta + :func:`delete_version_files`
    for promote paths so durable deletes only follow committed meta.
    """
    keep, drop = trim_versions_index(versions, limit=limit)
    delete_version_files(drop, versions_dir)
    return keep


def archive_index_entry(
    archive_path: Path,
    *,
    version_id: str,
    promoted_at: str,
) -> dict[str, Any]:
    """Build a versions index row by hashing the on-disk archive body (Issue 7)."""
    body = read_text_or_empty(archive_path)
    raw = body.encode("utf-8")
    return {
        "version_id": version_id,
        "promoted_at": promoted_at,
        "sha256": content_sha256(body),
        "bytes": len(raw),
    }

def body_byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def check_body_size(body: str) -> str | None:
    """Return error code if body too large, else None."""
    if body_byte_len(body) > MAX_BODY_BYTES:
        return "body_too_large"
    return None
