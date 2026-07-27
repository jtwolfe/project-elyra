"""Growth builtins: install_tool_draft, verify_tool, promote_tool, install_skill.

Scope: fail-closed create-tool path and skill install (compat → package VCS).
In scope: path jail, reserved verify sidecars, hash invalidate, no force promote.
Out of scope: sandbox FS tools, do-loop wiring.
"""

from __future__ import annotations

import logging
import re
from pathlib import PurePosixPath
from typing import Any

from elyra.identity.layout import MAX_BODY_BYTES, body_byte_len
from elyra.skills import SkillCatalog, is_valid_skill_name
from elyra.tools.builtin.package_vcs import (
    assemble_skill_md,
    bundled_skill_name_exists,
    promote_draft_skill,
    write_skill_draft,
)
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
        return ToolResult(
            ok=False,
            payload={
                "args_keys": sorted(str(k) for k in args.keys()),
                "hint": (
                    "files is required: a JSON object map of "
                    "relative_path -> UTF-8 string content"
                ),
            },
            error_reason="missing_files",
        )
    if not isinstance(files, dict):
        # Opaque invalid_files forced multi-hop binary search in live dogfood.
        # Echo type + args shape so the model can fix the call without archaeology.
        sample: Any = None
        if isinstance(files, list):
            sample = f"list_len={len(files)}"
        elif isinstance(files, str):
            sample = f"str_len={len(files)}"
        return ToolResult(
            ok=False,
            payload={
                "received_type": type(files).__name__,
                "received_sample": sample,
                "args_keys": sorted(str(k) for k in args.keys()),
                "hint": (
                    "files must be a JSON object (map), not a list or string. "
                    "Example: {\"TOOL.md\": \"...\", \"schema.json\": \"{...}\", "
                    "\"runner.json\": \"{...}\", \"impl/main.py\": \"...\", "
                    "\"tests/test_main.py\": \"...\"}"
                ),
            },
            error_reason="invalid_files",
        )

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
                payload={
                    "path": rel,
                    "received_type": type(value).__name__,
                    "hint": "each files value must be a UTF-8 string (file contents)",
                },
                error_reason="invalid_file_content",
            )
        planned.append((rel, value))

    # Fail-closed empty write set BEFORE any draft-tree side effects (Phase A / K4).
    # files={} would otherwise mkdir tools/drafts/<name>/ then return ok+written:[].
    if not planned:
        return ToolResult(
            ok=False,
            payload={
                "name": name,
                "hint": "files must be a non-empty map of path -> content strings",
            },
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
    """Stage draft under tools/.verify and run package tests; write hash record."""
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

    payload: dict[str, Any] = {
        "name": result.get("tool_name", name),
        "passed": True,
        "content_hash": result.get("content_hash"),
        "log": result.get("log", ""),
        "stage_dir": result.get("stage_dir"),
    }
    if result.get("executor_backend") is not None:
        payload["executor_backend"] = result["executor_backend"]
    return ToolResult(ok=True, payload=payload)


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
    base_payload: dict[str, Any] = {
        "name": name,
        "local_dir": result.get("local_dir"),
        "content_hash": result.get("content_hash"),
    }
    if result.get("archived_version_id") is not None:
        base_payload["archived_version_id"] = result["archived_version_id"]

    if ctx.registry is not None:
        try:
            ctx.registry.reload()
        except Exception as exc:  # noqa: BLE001 — surface as soft warning in payload
            _LOG.warning("registry.reload after promote failed: %s", exc)
            base_payload["reloaded"] = False
            base_payload["reload_error"] = type(exc).__name__
            return ToolResult(ok=True, payload=base_payload)

    base_payload["reloaded"] = ctx.registry is not None
    base_payload["callable"] = (
        ctx.registry.has(name) if ctx.registry is not None else False
    )
    return ToolResult(ok=True, payload=base_payload)


def install_skill(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Compat one-shot: assemble SKILL.md → draft → promote (archive-on-overwrite).

    Same args as before. Writes via ``skills/drafts/`` then whole-tree promote to
    ``skills/local/``. When a local package already exists it is archived under
    ``versions/`` first. Prefer ``install_skill_draft`` + ``promote_skill`` when
    reviewing a draft before it goes live.
    """
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

    if body_byte_len(body) > MAX_BODY_BYTES:
        return ToolResult(ok=False, payload={}, error_reason="body_too_large")

    # Refuse overwriting shipped bundled skills (local may update).
    if bundled_skill_name_exists(name):
        return ToolResult(
            ok=False,
            payload={"name": name},
            error_reason="refuses_overwrite_bundled",
        )

    content = assemble_skill_md(name, description, body)
    if body_byte_len(content) > MAX_BODY_BYTES:
        return ToolResult(ok=False, payload={}, error_reason="body_too_large")

    draft_result = write_skill_draft(ctx.paths, name, content)
    if not draft_result.get("ok"):
        return ToolResult(
            ok=False,
            payload={
                k: v
                for k, v in draft_result.items()
                if k not in {"ok", "error_reason"} and v is not None
            },
            error_reason=str(draft_result.get("error_reason") or "write_failed"),
        )

    promo = promote_draft_skill(ctx.paths, name, force=None)
    if not promo.get("ok"):
        return ToolResult(
            ok=False,
            payload={
                k: v
                for k, v in promo.items()
                if k not in {"ok", "error_reason"} and v is not None
            },
            error_reason=str(promo.get("error_reason") or "promote_failed"),
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

    payload: dict[str, Any] = {
        "name": name,
        "path": promo.get("path") or str(promo.get("local_dir") or ""),
        "reloaded": reloaded,
    }
    if promo.get("archived_version_id") is not None:
        payload["archived_version_id"] = promo["archived_version_id"]
    if promo.get("content_hash") is not None:
        payload["content_hash"] = promo["content_hash"]
    return ToolResult(ok=True, payload=payload)


__all__ = [
    "install_skill",
    "install_tool_draft",
    "promote_tool",
    "verify_tool",
]
