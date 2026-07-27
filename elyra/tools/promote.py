"""Promote verified draft tool packages to tools/local/ (callable).

Archive-on-replace: re-promote archives prior local payload under
``versions/<version_id>/`` then whole-tree rename-swaps the new package.
Never hollows ``local/<name>/`` (versions-only intermediate is forbidden).

Scope: hash-bound promote, no force, refuse bundled overwrite, package GC 50,
non-blocking exclusive lock, registry.reload after success (caller).
Out of scope: verify execution, draft writes, skill install (PR2).
"""

from __future__ import annotations

import errno
import fcntl
import json
import logging
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from elyra.config import ElyraPaths
from elyra.tools.policy import (
    is_valid_tool_name,
    normalize_tool_name,
    resolve_bundled_tools_root,
)
from elyra.tools.registry import drafts_dir
from elyra.tools.verify import (
    REQUIRED_PACKAGE_FILES,
    content_hash,
    draft_package_dir,
    load_verify_record,
    validate_draft_package,
)
from elyra.util.versioning import VERSION_GC_LIMIT, VERSION_ID_RE, mint_version_id

_LOG = logging.getLogger(__name__)

VERSIONS_DIR_NAME = "versions"
VERSIONS_META_NAME = ".versions_meta.json"
# Names excluded from package payload archives (IK3).
_ARCHIVE_EXCLUDE_NAMES = frozenset(
    {VERSIONS_DIR_NAME, VERSIONS_META_NAME, "__pycache__"}
)


class PackageLockedError(Exception):
    """Non-blocking lock for this package name is already held."""


def local_package_dir(paths: ElyraPaths, name: str) -> Path:
    """Resolved path to ``tools/local/<name>/``."""
    return (paths.tools_dir / "local" / name).resolve()


def local_root_dir(paths: ElyraPaths) -> Path:
    """Resolved ``tools/local/`` root."""
    return (paths.tools_dir / "local").resolve()


def lock_path_for(paths: ElyraPaths, name: str) -> Path:
    """Path of non-blocking exclusive lock file ``tools/local/.<name>.lock``."""
    return local_root_dir(paths) / f".{name}.lock"


def bundled_name_exists(
    name: str,
    *,
    bundled_root: Path | str | None = None,
) -> bool:
    """True if a bundled package directory exists for ``name`` (case-normalized)."""
    try:
        root = resolve_bundled_tools_root(bundled_root)
    except FileNotFoundError:
        return False
    if not root.is_dir():
        return False
    key = normalize_tool_name(name)
    for child in root.iterdir():
        if child.is_dir() and normalize_tool_name(child.name) == key:
            return True
    return False


def find_local_package_dir(paths: ElyraPaths, name: str) -> Path | None:
    """Return existing ``tools/local/<name>/`` (case-normalized), or None.

    Skips hidden siblings (``.lock``, stage/aside temps).
    """
    local_root = local_root_dir(paths)
    if not local_root.is_dir():
        return None
    key = normalize_tool_name(name)
    for child in local_root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if normalize_tool_name(child.name) == key:
            return child.resolve()
    return None


def local_name_exists(paths: ElyraPaths, name: str) -> bool:
    """True if ``tools/local/<name>/`` already exists (case-normalized scan)."""
    return find_local_package_dir(paths, name) is not None


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _archive_ignore(dirpath: str, names: list[str]) -> list[str]:
    """Ignore nested versions/, meta, and __pycache__ when copying payload."""
    ignored: list[str] = []
    for n in names:
        if n in _ARCHIVE_EXCLUDE_NAMES or n.endswith(".pyc"):
            ignored.append(n)
    return ignored


def copy_package_payload(src: Path, dst: Path) -> None:
    """Copy package payload files from src → dst, excluding archive sidecars.

    Excludes top-level and nested ``versions/``, ``.versions_meta.json``,
    ``__pycache__``. Never nests archives (IK3).
    """
    src = Path(src)
    dst = Path(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for child in src.iterdir():
        if child.name in _ARCHIVE_EXCLUDE_NAMES:
            continue
        target = dst / child.name
        if child.is_dir():
            if child.name == "__pycache__":
                continue
            shutil.copytree(child, target, ignore=_archive_ignore, dirs_exist_ok=True)
        elif child.is_file():
            shutil.copy2(child, target)


def _dir_byte_size(root: Path) -> int:
    total = 0
    if not root.is_dir():
        return 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
            except OSError:
                pass
    return total


def load_versions_meta(package_dir: Path) -> list[dict[str, Any]]:
    """Load package versions index from ``.versions_meta.json`` (list or wrapped)."""
    path = Path(package_dir) / VERSIONS_META_NAME
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else []
    except (OSError, json.JSONDecodeError) as exc:
        _LOG.warning("unreadable versions meta %s: %s", path, exc)
        return []
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        rows = data.get("versions")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    return []


def save_versions_meta(package_dir: Path, entries: list[dict[str, Any]]) -> None:
    """Write versions index as a JSON array (design: index list)."""
    path = Path(package_dir) / VERSIONS_META_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def archive_local_payload(
    package_dir: Path,
    *,
    version_id: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Archive payload of ``package_dir`` into ``versions/<version_id>/``.

    Copies payload only (excludes nested versions/, meta, __pycache__).
    Computes ``content_hash`` on the archive destination after copy (IK3).
    Does **not** append meta or run GC — caller owns index + GC.

    Returns index entry: version_id, content_hash, archived_at, bytes, reason?
    """
    package_dir = Path(package_dir)
    vid = version_id if version_id is not None else mint_version_id()
    if not VERSION_ID_RE.fullmatch(vid):
        raise ValueError(f"invalid version_id: {vid!r}")
    versions_root = package_dir / VERSIONS_DIR_NAME
    versions_root.mkdir(parents=True, exist_ok=True)
    archive_dir = versions_root / vid
    if archive_dir.exists():
        # Extremely unlikely collision; mint again once.
        vid = mint_version_id()
        archive_dir = versions_root / vid
    archive_dir.mkdir(parents=True, exist_ok=False)
    copy_package_payload(package_dir, archive_dir)
    # content_hash of archive dir after payload copy (payload-only).
    chash = content_hash(archive_dir)
    entry: dict[str, Any] = {
        "version_id": vid,
        "content_hash": chash,
        "archived_at": _utc_now_iso(),
        "bytes": _dir_byte_size(archive_dir),
    }
    if reason is not None and isinstance(reason, str) and reason.strip():
        entry["reason"] = reason.strip()
    return entry


def gc_package_versions(
    package_dir: Path,
    *,
    limit: int = VERSION_GC_LIMIT,
) -> list[dict[str, Any]]:
    """Trim package ``versions/`` directories + meta to ``limit`` (oldest first).

    Package directory GC only — never call identity ``gc_versions`` (*.md).
    GC is applied on the tree that holds history (prefer stage before rename).
    """
    package_dir = Path(package_dir)
    entries = load_versions_meta(package_dir)
    versions_root = package_dir / VERSIONS_DIR_NAME

    # Heal: drop index rows whose dirs are missing; keep chronological order.
    cleaned: list[dict[str, Any]] = []
    for row in entries:
        vid = row.get("version_id")
        if not isinstance(vid, str) or not VERSION_ID_RE.fullmatch(vid):
            continue
        if (versions_root / vid).is_dir():
            cleaned.append(row)

    # Also pick up orphan dirs not in index (append at end as recovered).
    if versions_root.is_dir():
        indexed = {
            r["version_id"]
            for r in cleaned
            if isinstance(r.get("version_id"), str)
        }
        for child in sorted(versions_root.iterdir()):
            if child.is_dir() and VERSION_ID_RE.fullmatch(child.name):
                if child.name not in indexed:
                    cleaned.append(
                        {
                            "version_id": child.name,
                            "content_hash": content_hash(child),
                            "archived_at": None,
                            "bytes": _dir_byte_size(child),
                        }
                    )

    if len(cleaned) <= limit:
        save_versions_meta(package_dir, cleaned)
        return cleaned

    drop = cleaned[: len(cleaned) - limit]
    keep = cleaned[len(cleaned) - limit :]
    save_versions_meta(package_dir, keep)
    for row in drop:
        vid = row.get("version_id")
        if not isinstance(vid, str):
            continue
        drop_dir = versions_root / vid
        if drop_dir.is_dir():
            try:
                shutil.rmtree(drop_dir)
            except OSError as exc:
                _LOG.warning("package GC rmtree failed %s: %s", drop_dir, exc)
    return keep


def _copy_history_onto_stage(dest: Path, stage: Path) -> None:
    """Copy versions/ + .versions_meta.json from dest onto stage (prefer copy)."""
    dest_versions = dest / VERSIONS_DIR_NAME
    stage_versions = stage / VERSIONS_DIR_NAME
    if dest_versions.is_dir():
        if stage_versions.exists():
            shutil.rmtree(stage_versions)
        shutil.copytree(dest_versions, stage_versions)
    dest_meta = dest / VERSIONS_META_NAME
    if dest_meta.is_file():
        shutil.copy2(dest_meta, stage / VERSIONS_META_NAME)


def _rename_path(src: Path, dst: Path) -> None:
    """Rename with EXDEV fallback (copytree + rmtree), whole-tree only."""
    try:
        os.rename(src, dst)
    except OSError as exc:
        if getattr(exc, "errno", None) != errno.EXDEV:
            raise
        # Cross-device: whole-tree copy then remove src (never child-shuffle).
        if dst.exists():
            raise
        shutil.copytree(src, dst)
        shutil.rmtree(src)


def whole_tree_rename_swap(
    *,
    stage: Path,
    dest: Path,
    aside_full: Path,
) -> None:
    """Atomic whole-tree swap: dest→aside, stage→dest, rmtree aside.

    Invariant: whenever ``dest`` exists it is a complete package. Between
    renames the live name may be **absent**, never hollow (IK4).

    On failure after dest→aside, recovers aside→dest and re-raises.
    """
    if not dest.exists():
        _rename_path(stage, dest)
        return

    _rename_path(dest, aside_full)
    try:
        _rename_path(stage, dest)
    except Exception:
        # Recovery: restore prior complete package under live name.
        try:
            if dest.exists():
                # Partial stage landed — remove before restoring.
                shutil.rmtree(dest, ignore_errors=True)
            _rename_path(aside_full, dest)
        except Exception as recover_exc:  # noqa: BLE001
            _LOG.error(
                "promote recovery failed dest=%s aside=%s: %s",
                dest,
                aside_full,
                recover_exc,
            )
        raise
    # Success: remove aside only after stage is live.
    try:
        shutil.rmtree(aside_full)
    except OSError as exc:
        _LOG.warning("aside cleanup failed %s: %s", aside_full, exc)


@contextmanager
def package_lock(paths: ElyraPaths, name: str) -> Iterator[None]:
    """Non-blocking exclusive lock on ``tools/local/.<name>.lock``.

    Raises :class:`PackageLockedError` on contention. Always unlocks in finally.
    No automatic stale-lock steal in v1.
    """
    root = local_root_dir(paths)
    root.mkdir(parents=True, exist_ok=True)
    path = lock_path_for(paths, name)
    fd = open(path, "a+", encoding="utf-8")  # noqa: SIM115 — closed in finally
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PackageLockedError(str(path)) from exc
        except OSError as exc:
            # Some platforms may raise EAGAIN/EACCES instead.
            if getattr(exc, "errno", None) in (
                errno.EAGAIN,
                errno.EACCES,
                errno.EWOULDBLOCK,
            ):
                raise PackageLockedError(str(path)) from exc
            raise
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            fd.close()
        except OSError:
            pass


def _unique_sibling(local_root: Path, name: str, kind: str) -> Path:
    """``tools/local/.<name>.<kind>.<pid>.<uuid>/`` — never inside dest."""
    token = uuid.uuid4().hex[:12]
    return local_root / f".{name}.{kind}.{os.getpid()}.{token}"


def package_is_complete(package_dir: Path) -> bool:
    """True when required payload files exist (not hollow versions-only)."""
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        return False
    return all((package_dir / f).is_file() for f in REQUIRED_PACKAGE_FILES)


def promote_draft_tool(
    paths: ElyraPaths,
    name: str,
    *,
    bundled_root: Path | str | None = None,
    force: Any = None,
) -> dict[str, Any]:
    """Promote drafts → local with archive-on-replace. No force flag.

    Normative whole-tree rename algorithm (IK4). Returns dict with ok,
    error_reason (on fail), local_dir / content_hash / archived_version_id
    (on success). Caller reloads registry.
    """
    if force is not None:
        return {"ok": False, "error_reason": "force_not_allowed"}

    if not isinstance(name, str) or not is_valid_tool_name(name):
        return {"ok": False, "error_reason": "invalid_name"}
    name = name.strip()

    draft_dir = draft_package_dir(paths, name)
    if not draft_dir.is_dir():
        return {"ok": False, "error_reason": "draft_missing"}

    shape_err = validate_draft_package(draft_dir)
    if shape_err is not None:
        return {"ok": False, "error_reason": shape_err}

    record = load_verify_record(draft_dir)
    if record is None:
        return {"ok": False, "error_reason": "verify_required"}
    if record.get("passed") is not True:
        return {"ok": False, "error_reason": "verify_not_passed"}

    recorded_hash = record.get("content_hash")
    if not isinstance(recorded_hash, str) or not recorded_hash:
        return {"ok": False, "error_reason": "verify_hash_missing"}

    current_hash = content_hash(draft_dir)
    if recorded_hash != current_hash:
        return {
            "ok": False,
            "error_reason": "verify_hash_mismatch",
            "content_hash": current_hash,
            "recorded_hash": recorded_hash,
        }

    if bundled_name_exists(name, bundled_root=bundled_root):
        return {"ok": False, "error_reason": "refuses_overwrite_bundled"}

    local_root = local_root_dir(paths)
    local_root.mkdir(parents=True, exist_ok=True)
    dest = find_local_package_dir(paths, name) or local_package_dir(paths, name)

    stage: Path | None = None
    aside_full: Path | None = None
    archived_version_id: str | None = None

    try:
        with package_lock(paths, name):
            # Re-resolve dest under lock (race with another promoter).
            dest = find_local_package_dir(paths, name) or local_package_dir(
                paths, name
            )
            stage = _unique_sibling(local_root, name, "promote")
            aside_full = _unique_sibling(local_root, name, "aside")

            # Step 5: stage draft payload first; live dest untouched.
            try:
                shutil.copytree(draft_dir, stage)
            except OSError as exc:
                _LOG.warning("promote stage copy failed for %s: %s", name, exc)
                if stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)
                return {
                    "ok": False,
                    "error_reason": f"promote_failed:{type(exc).__name__}",
                }

            # Step 6: if dest exists, archive payload and attach history to stage.
            if dest.exists():
                try:
                    entry = archive_local_payload(dest)
                    archived_version_id = str(entry["version_id"])
                    meta = load_versions_meta(dest)
                    meta.append(entry)
                    save_versions_meta(dest, meta)
                    # Preferred: copy history onto stage (dest intact until rename).
                    _copy_history_onto_stage(dest, stage)
                    # GC on stage before rename (documented choice).
                    gc_package_versions(stage, limit=VERSION_GC_LIMIT)
                except OSError as exc:
                    _LOG.warning("promote archive failed for %s: %s", name, exc)
                    if stage.exists():
                        shutil.rmtree(stage, ignore_errors=True)
                    return {
                        "ok": False,
                        "error_reason": f"promote_failed:{type(exc).__name__}",
                    }

            # Step 7: whole-tree rename swap (never hollow live name).
            try:
                whole_tree_rename_swap(
                    stage=stage, dest=dest, aside_full=aside_full
                )
            except OSError as exc:
                _LOG.warning("promote rename swap failed for %s: %s", name, exc)
                if stage is not None and stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)
                if aside_full is not None and aside_full.exists() and not dest.exists():
                    try:
                        _rename_path(aside_full, dest)
                    except OSError:
                        pass
                return {
                    "ok": False,
                    "error_reason": f"promote_failed:{type(exc).__name__}",
                }

            # Stage should now be gone (renamed to dest); clean leftovers.
            if stage is not None and stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

            # Step 8: remove draft only after full success.
            try:
                shutil.rmtree(draft_dir)
            except OSError as exc:
                _LOG.warning("promote draft cleanup failed for %s: %s", name, exc)

            try:
                drafts_dir(paths).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

            result: dict[str, Any] = {
                "ok": True,
                "tool_name": name,
                "local_dir": str(dest),
                "content_hash": current_hash,
            }
            if archived_version_id is not None:
                result["archived_version_id"] = archived_version_id
            return result

    except PackageLockedError:
        return {"ok": False, "error_reason": "promote_locked"}


def revert_local_tool(
    paths: ElyraPaths,
    name: str,
    version_id: str,
    reason: str,
    *,
    bundled_root: Path | str | None = None,
) -> dict[str, Any]:
    """Archive current local package and restore ``versions/<version_id>/``.

    Same whole-tree rename + lock as promote. Does not delete the restored
    version from history. Reason required (caller validates min length).
    """
    if not isinstance(name, str) or not is_valid_tool_name(name):
        return {"ok": False, "error_reason": "invalid_name"}
    name = name.strip()

    if not isinstance(version_id, str) or not VERSION_ID_RE.fullmatch(version_id):
        return {"ok": False, "error_reason": "version_not_found"}

    if bundled_name_exists(name, bundled_root=bundled_root):
        return {"ok": False, "error_reason": "refuses_overwrite_bundled"}

    dest = find_local_package_dir(paths, name)
    if dest is None or not dest.is_dir():
        return {"ok": False, "error_reason": "package_not_found"}

    version_dir = dest / VERSIONS_DIR_NAME / version_id
    if not version_dir.is_dir():
        return {"ok": False, "error_reason": "version_not_found"}

    local_root = local_root_dir(paths)
    stage: Path | None = None
    aside_full: Path | None = None

    try:
        with package_lock(paths, name):
            dest = find_local_package_dir(paths, name)
            if dest is None or not dest.is_dir():
                return {"ok": False, "error_reason": "package_not_found"}
            version_dir = dest / VERSIONS_DIR_NAME / version_id
            if not version_dir.is_dir():
                return {"ok": False, "error_reason": "version_not_found"}

            stage = _unique_sibling(local_root, name, "revert")
            aside_full = _unique_sibling(local_root, name, "aside")

            try:
                # Archive current payload first (pre_revert reason).
                entry = archive_local_payload(
                    dest, reason=f"pre_revert:{reason.strip()}"
                )
                meta = load_versions_meta(dest)
                meta.append(entry)
                save_versions_meta(dest, meta)

                # Stage = chosen version payload + full history (incl. pre_revert).
                copy_package_payload(version_dir, stage)
                _copy_history_onto_stage(dest, stage)
                gc_package_versions(stage, limit=VERSION_GC_LIMIT)
            except OSError as exc:
                _LOG.warning("revert stage failed for %s: %s", name, exc)
                if stage is not None and stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)
                return {
                    "ok": False,
                    "error_reason": f"revert_failed:{type(exc).__name__}",
                }

            try:
                whole_tree_rename_swap(
                    stage=stage, dest=dest, aside_full=aside_full
                )
            except OSError as exc:
                _LOG.warning("revert rename swap failed for %s: %s", name, exc)
                if stage is not None and stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)
                if aside_full is not None and aside_full.exists() and not dest.exists():
                    try:
                        _rename_path(aside_full, dest)
                    except OSError:
                        pass
                return {
                    "ok": False,
                    "error_reason": f"revert_failed:{type(exc).__name__}",
                }

            if stage is not None and stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

            return {
                "ok": True,
                "tool_name": name,
                "local_dir": str(dest),
                "restored_version_id": version_id,
                "archived_version_id": entry["version_id"],
                "content_hash": content_hash(dest)
                if dest.is_dir()
                else None,
            }

    except PackageLockedError:
        return {"ok": False, "error_reason": "package_locked"}


__all__ = [
    "VERSIONS_DIR_NAME",
    "VERSIONS_META_NAME",
    "PackageLockedError",
    "archive_local_payload",
    "bundled_name_exists",
    "copy_package_payload",
    "find_local_package_dir",
    "gc_package_versions",
    "load_versions_meta",
    "local_name_exists",
    "local_package_dir",
    "local_root_dir",
    "lock_path_for",
    "package_is_complete",
    "package_lock",
    "promote_draft_tool",
    "revert_local_tool",
    "save_versions_meta",
    "whole_tree_rename_swap",
]
