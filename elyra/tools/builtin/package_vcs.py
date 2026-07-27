"""Package VCS builtins: get_tool, revert_tool (skills VCS lands in PR2).

Thin host entries over promote.archive/revert helpers. Meta-only version lists;
truncated previews. Bundled packages immutable.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from elyra.tools.policy import is_valid_tool_name
from elyra.tools.promote import (
    VERSIONS_DIR_NAME,
    VERSIONS_META_NAME,
    bundled_name_exists,
    find_local_package_dir,
    load_versions_meta,
    local_package_dir,
    package_is_complete,
    revert_local_tool,
)
from elyra.tools.types import ToolContext, ToolResult
from elyra.tools.verify import REQUIRED_PACKAGE_FILES, draft_package_dir
from elyra.util.versioning import VERSION_ID_RE

_LOG = logging.getLogger(__name__)

# Truncate package file previews so get_tool stays thin (IK19).
_PREVIEW_CHARS = 2000
_REASON_MIN_LEN = 8


def _bundled_root(ctx: ToolContext):
    if ctx.registry is not None:
        return ctx.registry.bundled_root
    return None


def _read_preview(path: Path, *, limit: int = _PREVIEW_CHARS) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if len(text) > limit:
        return text[:limit] + "\n…(truncated)"
    return text


def _package_summary(package_dir: Path) -> dict[str, Any]:
    """Thin package summary: required files present + TOOL.md preview."""
    files_present = {
        name: (package_dir / name).is_file() for name in REQUIRED_PACKAGE_FILES
    }
    # List top-level names excluding versions sidecars for orientation.
    top_level: list[str] = []
    try:
        for child in sorted(package_dir.iterdir(), key=lambda p: p.name):
            if child.name in (VERSIONS_DIR_NAME, VERSIONS_META_NAME):
                continue
            if child.name.startswith(".") and child.name != "TOOL.md":
                # Keep .verify if present; skip lock/stage noise.
                if child.name.startswith(".promote") or child.name.startswith(
                    ".aside"
                ):
                    continue
            top_level.append(child.name + ("/" if child.is_dir() else ""))
    except OSError:
        pass
    summary: dict[str, Any] = {
        "path": str(package_dir),
        "complete": package_is_complete(package_dir),
        "files_present": files_present,
        "top_level": top_level,
    }
    tool_md = _read_preview(package_dir / "TOOL.md")
    if tool_md is not None:
        summary["tool_md_preview"] = tool_md
    return summary


def _versions_meta_only(package_dir: Path) -> list[dict[str, Any]]:
    """Return index rows only (no archive file bodies)."""
    rows = load_versions_meta(package_dir)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        vid = row.get("version_id")
        if not isinstance(vid, str):
            continue
        entry: dict[str, Any] = {"version_id": vid}
        for key in ("content_hash", "archived_at", "bytes", "reason"):
            if key in row:
                entry[key] = row[key]
        out.append(entry)
    return out


def get_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Read current/draft/version tool package summary; optional list_versions."""
    name = args.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        return ToolResult(ok=False, payload={}, error_reason="missing_name")
    if not isinstance(name, str) or not is_valid_tool_name(name):
        return ToolResult(ok=False, payload={}, error_reason="invalid_name")
    name = name.strip()

    which = args.get("which") or "current"
    if which not in ("current", "draft", "version"):
        return ToolResult(ok=False, payload={}, error_reason="invalid_which")

    list_versions = bool(args.get("list_versions"))
    version_id = args.get("version_id")

    payload: dict[str, Any] = {
        "name": name,
        "which": which,
    }

    if which == "draft":
        draft = draft_package_dir(ctx.paths, name)
        if not draft.is_dir():
            return ToolResult(
                ok=False,
                payload={"name": name, "which": "draft"},
                error_reason="draft_missing",
            )
        payload["package"] = _package_summary(draft)
        # Drafts do not carry package versions history.
        if list_versions:
            payload["versions"] = []
        return ToolResult(ok=True, payload=payload)

    local = find_local_package_dir(ctx.paths, name)
    if local is None:
        # Bundled-only names: allow summary of bundled package for which=current.
        if which == "current" and bundled_name_exists(
            name, bundled_root=_bundled_root(ctx)
        ):
            # Resolve bundled dir for read-only summary.
            try:
                from elyra.tools.policy import resolve_bundled_tools_root

                root = resolve_bundled_tools_root(_bundled_root(ctx))
                key = name.casefold()
                bundled_dir = None
                for child in root.iterdir():
                    if child.is_dir() and child.name.casefold() == key:
                        bundled_dir = child
                        break
                if bundled_dir is not None:
                    payload["source"] = "bundled"
                    payload["package"] = _package_summary(bundled_dir)
                    if list_versions:
                        payload["versions"] = []
                    return ToolResult(ok=True, payload=payload)
            except FileNotFoundError:
                pass
        return ToolResult(
            ok=False,
            payload={"name": name},
            error_reason="package_not_found",
        )

    payload["source"] = "local"
    payload["local_dir"] = str(local)

    if which == "version":
        if not isinstance(version_id, str) or not version_id.strip():
            return ToolResult(
                ok=False,
                payload={"name": name},
                error_reason="version_not_found",
            )
        version_id = version_id.strip()
        if not VERSION_ID_RE.fullmatch(version_id):
            return ToolResult(
                ok=False,
                payload={"name": name, "version_id": version_id},
                error_reason="version_not_found",
            )
        vdir = local / VERSIONS_DIR_NAME / version_id
        if not vdir.is_dir():
            return ToolResult(
                ok=False,
                payload={"name": name, "version_id": version_id},
                error_reason="version_not_found",
            )
        payload["version_id"] = version_id
        payload["package"] = _package_summary(vdir)
    else:
        payload["package"] = _package_summary(local)

    if list_versions:
        payload["versions"] = _versions_meta_only(local)

    return ToolResult(ok=True, payload=payload)


def revert_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Restore a prior package version; reason required (min 8 chars)."""
    name = args.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        return ToolResult(ok=False, payload={}, error_reason="missing_name")
    if not isinstance(name, str) or not is_valid_tool_name(name):
        return ToolResult(ok=False, payload={}, error_reason="invalid_name")
    name = name.strip()

    version_id = args.get("version_id")
    if not isinstance(version_id, str) or not version_id.strip():
        return ToolResult(ok=False, payload={}, error_reason="version_not_found")
    version_id = version_id.strip()

    reason = args.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < _REASON_MIN_LEN:
        return ToolResult(
            ok=False,
            payload={
                "hint": f"reason is required (min {_REASON_MIN_LEN} characters)",
            },
            error_reason="reason_required",
        )
    reason = reason.strip()

    if "force" in args:
        return ToolResult(ok=False, payload={}, error_reason="force_not_allowed")

    result = revert_local_tool(
        ctx.paths,
        name,
        version_id,
        reason,
        bundled_root=_bundled_root(ctx),
    )
    if not result.get("ok"):
        return ToolResult(
            ok=False,
            payload={
                k: v
                for k, v in result.items()
                if k not in {"ok", "error_reason"} and v is not None
            },
            error_reason=str(result.get("error_reason") or "revert_failed"),
        )

    reloaded = False
    callable_now = False
    if ctx.registry is not None:
        try:
            ctx.registry.reload()
            reloaded = True
            callable_now = ctx.registry.has(name)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("registry.reload after revert_tool failed: %s", exc)

    return ToolResult(
        ok=True,
        payload={
            "name": name,
            "local_dir": result.get("local_dir") or str(local_package_dir(ctx.paths, name)),
            "restored_version_id": result.get("restored_version_id", version_id),
            "archived_version_id": result.get("archived_version_id"),
            "content_hash": result.get("content_hash"),
            "reloaded": reloaded,
            "callable": callable_now,
            "reason": reason,
        },
    )


__all__ = [
    "get_tool",
    "revert_tool",
]
