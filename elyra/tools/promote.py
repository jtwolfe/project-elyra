"""Promote verified draft tool packages to tools/local/ (callable).

Scope: hash-bound promote, no force, refuse builtin/bundled overwrite,
registry.reload after successful promote.
Out of scope: verify execution, draft writes, skill install.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths
from elyra.tools.policy import (
    is_valid_tool_name,
    normalize_tool_name,
    resolve_bundled_tools_root,
)
from elyra.tools.registry import drafts_dir
from elyra.tools.verify import (
    content_hash,
    draft_package_dir,
    load_verify_record,
    validate_draft_package,
)

_LOG = logging.getLogger(__name__)


def local_package_dir(paths: ElyraPaths, name: str) -> Path:
    """Resolved path to ``tools/local/<name>/``."""
    return (paths.tools_dir / "local" / name).resolve()


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


def local_name_exists(paths: ElyraPaths, name: str) -> bool:
    """True if ``tools/local/<name>/`` already exists (case-normalized scan)."""
    local_root = (paths.tools_dir / "local").resolve()
    if not local_root.is_dir():
        return False
    key = normalize_tool_name(name)
    for child in local_root.iterdir():
        if child.is_dir() and normalize_tool_name(child.name) == key:
            return True
    return False


def promote_draft_tool(
    paths: ElyraPaths,
    name: str,
    *,
    bundled_root: Path | str | None = None,
    force: Any = None,
) -> dict[str, Any]:
    """Promote drafts → local when verify hash matches. No force flag.

    Returns dict with ok, error_reason (on fail), local_dir (on success).
    Caller is responsible for ``registry.reload()`` after success (or pass
    registry into the growth builtin which reloads).
    """
    if force is not None:
        # Explicitly reject any force attempt (design: no force flag).
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

    if local_name_exists(paths, name):
        return {"ok": False, "error_reason": "refuses_overwrite_local"}

    dest = local_package_dir(paths, name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        # Race / case edge: refuse rather than clobber.
        return {"ok": False, "error_reason": "refuses_overwrite_local"}

    try:
        # Copy then remove draft (atomic enough for S1; move can cross devices).
        shutil.copytree(draft_dir, dest)
        shutil.rmtree(draft_dir)
    except OSError as exc:
        _LOG.warning("promote copy/remove failed for %s: %s", name, exc)
        # Best-effort cleanup of partial dest
        if dest.exists() and not draft_dir.exists():
            pass  # draft gone; dest may be complete
        elif dest.exists() and draft_dir.exists():
            try:
                shutil.rmtree(dest)
            except OSError:
                pass
            return {
                "ok": False,
                "error_reason": f"promote_failed:{type(exc).__name__}",
            }
        return {
            "ok": False,
            "error_reason": f"promote_failed:{type(exc).__name__}",
        }

    # Ensure empty parent drafts dir still exists
    try:
        drafts_dir(paths).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    return {
        "ok": True,
        "tool_name": name,
        "local_dir": str(dest),
        "content_hash": current_hash,
    }


__all__ = [
    "bundled_name_exists",
    "local_name_exists",
    "local_package_dir",
    "promote_draft_tool",
]
