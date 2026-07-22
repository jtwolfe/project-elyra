"""Growth builtins: install_tool_draft, verify_tool, promote_tool, install_skill.

Scope: fail-closed create-tool path and skill install to skills/local/.
In scope: path jail, reserved verify sidecars, hash invalidate, no force promote.
Out of scope: sandbox FS tools, do-loop wiring.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any

from elyra.skills import SkillCatalog, is_valid_skill_name, normalize_skill_name
from elyra.skills.catalog import local_skills_dir
from elyra.skills.policy import BundledSkillsRootError, resolve_bundled_skills_root
from elyra.tools.policy import is_valid_tool_name
from elyra.tools.promote import promote_draft_tool
from elyra.tools.registry import drafts_dir
from elyra.tools.types import ToolContext, ToolResult
from elyra.tools.verify import (
    DEFAULT_VERIFY_TIMEOUT_SECONDS,
    delete_verify_record,
    draft_package_dir,
    verify_draft_tool,
)

_LOG = logging.getLogger(__name__)

# Reserved control sidecars clients must not plant (final path component).
_RESERVED_BASENAME_RE = re.compile(
    r"^\.verify(\..*)?$|^\.promote(\..*)?$",
    re.IGNORECASE,
)


def _settings_verify_timeout(ctx: ToolContext) -> float:
    if ctx.settings is not None:
        try:
            return float(ctx.settings.tools.verify_timeout_seconds)
        except (AttributeError, TypeError, ValueError):
            pass
    return float(DEFAULT_VERIFY_TIMEOUT_SECONDS)


def _is_reserved_relpath(rel: str) -> bool:
    """True if any segment is a reserved control sidecar name."""
    try:
        parts = PurePosixPath(rel).parts
    except (ValueError, TypeError):
        return True
    for part in parts:
        if _RESERVED_BASENAME_RE.match(part):
            return True
    return False


def _validate_relative_file_key(key: object) -> str | None:
    """Return normalized relative path or None if illegal.

    Rejects non-str, empty, absolute, ``..`` / ``.`` segments, reserved
    ``.verify*`` / ``.promote*`` basenames, and null bytes.
    """
    if not isinstance(key, str):
        return None
    raw = key.strip().replace("\\", "/")
    if not raw or "\x00" in raw:
        return None
    # Absolute (POSIX or Windows drive)
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        return None
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        return None
    parts = pure.parts
    if not parts:
        return None
    for part in parts:
        if part in ("", ".", ".."):
            return None
    # Normalized relative form
    norm = pure.as_posix()
    if norm.startswith("../") or norm == ".." or "/../" in f"/{norm}/":
        return None
    if _is_reserved_relpath(norm):
        return None
    return norm


def _safe_write_under(root: Path, rel: str, content: str) -> str | None:
    """Write ``content`` to ``root/rel`` if the resolved path stays under root.

    Returns error_reason or None on success.
    """
    root_resolved = root.resolve()
    # Build target without resolving symlinks mid-path first
    target = root_resolved.joinpath(*PurePosixPath(rel).parts)
    try:
        # Ensure parent exists and stays under root
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        parent_resolved = parent.resolve()
        if not (
            parent_resolved == root_resolved
            or parent_resolved.is_relative_to(root_resolved)
        ):
            return "path_escape"
        # Re-resolve target after parents exist
        # Write via open after checking final resolve would stay inside
        # (file may not exist yet)
        if target.exists():
            final = target.resolve()
            if not (
                final == root_resolved or final.is_relative_to(root_resolved)
            ):
                return "path_escape"
        target.write_text(content, encoding="utf-8")
        final = target.resolve()
        if not (final == root_resolved or final.is_relative_to(root_resolved)):
            # Extremely defensive: remove and fail
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            return "path_escape"
    except OSError as exc:
        return f"write_failed:{type(exc).__name__}"
    return None


def install_tool_draft(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Write/update files only under ``tools/drafts/<name>/``; invalidate verify."""
    name = args.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        return ToolResult(ok=False, payload={}, error_reason="missing_name")
    if not isinstance(name, str) or not is_valid_tool_name(name):
        return ToolResult(ok=False, payload={}, error_reason="invalid_name")
    name = name.strip()

    files = args.get("files")
    if files is None:
        return ToolResult(ok=False, payload={}, error_reason="missing_files")
    if not isinstance(files, dict):
        return ToolResult(ok=False, payload={}, error_reason="invalid_files")

    # Validate all keys before writing anything (atomic fail-closed).
    planned: list[tuple[str, str]] = []
    for key, value in files.items():
        rel = _validate_relative_file_key(key)
        if rel is None:
            # Distinguish reserved vs general path jail
            if isinstance(key, str) and _is_reserved_relpath(
                key.strip().replace("\\", "/")
            ):
                return ToolResult(
                    ok=False,
                    payload={"path": key},
                    error_reason="reserved_path",
                )
            return ToolResult(
                ok=False,
                payload={"path": key if isinstance(key, str) else None},
                error_reason="path_jail",
            )
        if not isinstance(value, str):
            return ToolResult(
                ok=False,
                payload={"path": rel},
                error_reason="invalid_file_content",
            )
        planned.append((rel, value))

    # Fail-closed empty write set BEFORE any draft-tree side effects (Phase A / K4).
    # files={} would otherwise mkdir tools/drafts/<name>/ then return ok+written:[].
    if not planned:
        return ToolResult(
            ok=False,
            payload={"name": name},
            error_reason="empty_files",
        )

    draft_root = draft_package_dir(ctx.paths, name)
    try:
        draft_root.mkdir(parents=True, exist_ok=True)
        # Ensure we only ever write under drafts/
        drafts_root = drafts_dir(ctx.paths).resolve()
        if not draft_root.resolve().is_relative_to(drafts_root):
            return ToolResult(ok=False, payload={}, error_reason="path_jail")
    except OSError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"mkdir_failed:{type(exc).__name__}",
        )

    written: list[str] = []
    for rel, content in planned:
        err = _safe_write_under(draft_root, rel, content)
        if err is not None:
            # Always attempt verify invalidation even on partial write
            delete_verify_record(draft_root)
            return ToolResult(
                ok=False,
                payload={"path": rel, "written": written},
                error_reason=err,
            )
        written.append(rel)

    # Always delete .verify.json after writes (invalidate prior verify).
    deleted = delete_verify_record(draft_root)

    return ToolResult(
        ok=True,
        payload={
            "name": name,
            "draft_dir": str(draft_root),
            "written": written,
            "verify_invalidated": deleted,
        },
    )


def verify_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Stage draft under sandbox/.verify and run package tests; write hash record."""
    name = args.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        return ToolResult(ok=False, payload={}, error_reason="missing_name")
    if not isinstance(name, str) or not is_valid_tool_name(name):
        return ToolResult(ok=False, payload={}, error_reason="invalid_name")
    name = name.strip()

    timeout = _settings_verify_timeout(ctx)
    result = verify_draft_tool(ctx.paths, name, timeout_seconds=timeout)

    if not result.get("ok"):
        payload = {
            k: v
            for k, v in result.items()
            if k not in {"ok", "error_reason"} and v is not None
        }
        return ToolResult(
            ok=False,
            payload=payload,
            error_reason=str(result.get("error_reason") or "verify_failed"),
        )

    return ToolResult(
        ok=True,
        payload={
            "name": result.get("tool_name", name),
            "passed": True,
            "content_hash": result.get("content_hash"),
            "log": result.get("log", ""),
            "stage_dir": result.get("stage_dir"),
        },
    )


def promote_tool(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Promote verified draft → tools/local/; reload registry. No force."""
    name = args.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        return ToolResult(ok=False, payload={}, error_reason="missing_name")
    if not isinstance(name, str) or not is_valid_tool_name(name):
        return ToolResult(ok=False, payload={}, error_reason="invalid_name")
    name = name.strip()

    # Reject force if present (even false is a signal the model tried force API)
    if "force" in args:
        return ToolResult(ok=False, payload={}, error_reason="force_not_allowed")

    bundled_root = None
    if ctx.registry is not None:
        bundled_root = ctx.registry.bundled_root

    result = promote_draft_tool(
        ctx.paths,
        name,
        bundled_root=bundled_root,
        force=None,
    )
    if not result.get("ok"):
        return ToolResult(
            ok=False,
            payload={
                k: v
                for k, v in result.items()
                if k not in {"ok", "error_reason"} and v is not None
            },
            error_reason=str(result.get("error_reason") or "promote_failed"),
        )

    # Mid-process callable: reload registry so next hop can use the tool.
    if ctx.registry is not None:
        try:
            ctx.registry.reload()
        except Exception as exc:  # noqa: BLE001 — surface as soft warning in payload
            _LOG.warning("registry.reload after promote failed: %s", exc)
            return ToolResult(
                ok=True,
                payload={
                    "name": name,
                    "local_dir": result.get("local_dir"),
                    "content_hash": result.get("content_hash"),
                    "reloaded": False,
                    "reload_error": type(exc).__name__,
                },
            )

    return ToolResult(
        ok=True,
        payload={
            "name": name,
            "local_dir": result.get("local_dir"),
            "content_hash": result.get("content_hash"),
            "reloaded": ctx.registry is not None,
            "callable": (
                ctx.registry.has(name) if ctx.registry is not None else False
            ),
        },
    )


def _bundled_skill_exists(name: str) -> bool:
    try:
        root = resolve_bundled_skills_root()
    except BundledSkillsRootError:
        return False
    key = normalize_skill_name(name)
    if not root.is_dir():
        return False
    for child in root.iterdir():
        if child.is_dir() and normalize_skill_name(child.name) == key:
            return True
    return False


def install_skill(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Write only ``skills/local/<name>/SKILL.md`` (no draft/verify gate)."""
    name = args.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        return ToolResult(ok=False, payload={}, error_reason="missing_name")
    if not isinstance(name, str) or not is_valid_skill_name(name):
        return ToolResult(ok=False, payload={}, error_reason="invalid_name")
    name = name.strip()

    description = args.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str):
        return ToolResult(ok=False, payload={}, error_reason="invalid_description")
    description = description.strip()

    body = args.get("body")
    if body is None:
        return ToolResult(ok=False, payload={}, error_reason="missing_body")
    if not isinstance(body, str):
        return ToolResult(ok=False, payload={}, error_reason="invalid_body")

    # Refuse overwriting shipped bundled skills (local may update).
    if _bundled_skill_exists(name):
        return ToolResult(
            ok=False,
            payload={"name": name},
            error_reason="refuses_overwrite_bundled",
        )

    local_root = local_skills_dir(ctx.paths)
    pkg_dir = (local_root / name).resolve()
    try:
        local_root_resolved = local_root.resolve()
        local_root_resolved.mkdir(parents=True, exist_ok=True)
        if not pkg_dir.is_relative_to(local_root_resolved):
            return ToolResult(ok=False, payload={}, error_reason="path_jail")
        pkg_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"mkdir_failed:{type(exc).__name__}",
        )

    # Assemble SKILL.md in the same format as hand-written packages.
    desc_line = description or name
    content = (
        f"---\n"
        f"name: {name}\n"
        f"description: {desc_line}\n"
        f"---\n\n"
        f"{body.lstrip()}"
    )
    if not content.endswith("\n"):
        content += "\n"

    skill_md = pkg_dir / "SKILL.md"
    try:
        skill_md.write_text(content, encoding="utf-8")
        final = skill_md.resolve()
        if not final.is_relative_to(local_root_resolved):
            try:
                skill_md.unlink(missing_ok=True)
            except OSError:
                pass
            return ToolResult(ok=False, payload={}, error_reason="path_jail")
    except OSError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"write_failed:{type(exc).__name__}",
        )

    # Reload catalog if injected so same moment can load_skill next hop.
    reloaded = False
    existing = ctx.extras.get("skills")
    if isinstance(existing, SkillCatalog):
        try:
            existing.reload()
            reloaded = True
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("SkillCatalog.reload after install_skill failed: %s", exc)

    return ToolResult(
        ok=True,
        payload={
            "name": name,
            "path": str(skill_md),
            "reloaded": reloaded,
        },
    )


__all__ = [
    "install_skill",
    "install_tool_draft",
    "promote_tool",
    "verify_tool",
]
