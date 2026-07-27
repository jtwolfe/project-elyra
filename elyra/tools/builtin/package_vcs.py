"""Package VCS builtins: tools (get/revert) + skills (draft/promote/revert/get).

Thin host entries over promote.archive/revert helpers for tools, and skill
package archive helpers in this module. Meta-only version lists; truncated
previews. Bundled packages immutable.
"""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from elyra.config import ElyraPaths
from elyra.identity.layout import MAX_BODY_BYTES, body_byte_len
from elyra.skills import SkillCatalog, is_valid_skill_name, normalize_skill_name
from elyra.skills.catalog import local_skills_dir
from elyra.skills.catalog import _parse_frontmatter  # skill frontmatter gate (IK5)
from elyra.skills.policy import BundledSkillsRootError, resolve_bundled_skills_root
from elyra.tools.policy import is_valid_tool_name
from elyra.tools.promote import (
    VERSIONS_DIR_NAME,
    VERSIONS_META_NAME,
    PackageLockedError,
    archive_local_payload,
    bundled_name_exists,
    copy_package_payload,
    find_local_package_dir,
    gc_package_versions,
    load_versions_meta,
    local_package_dir,
    package_is_complete,
    revert_local_tool,
    save_versions_meta,
    whole_tree_rename_swap,
)
from elyra.tools.types import ToolContext, ToolResult
from elyra.tools.verify import REQUIRED_PACKAGE_FILES, content_hash, draft_package_dir
from elyra.util.versioning import VERSION_GC_LIMIT, VERSION_ID_RE

_LOG = logging.getLogger(__name__)

# Truncate package file previews so get_* stays thin (IK19).
_PREVIEW_CHARS = 2000
_REASON_MIN_LEN = 8
_SKILL_MD_NAME = "SKILL.md"


# ---------------------------------------------------------------------------
# Shared thin helpers
# ---------------------------------------------------------------------------


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
    """Thin tool package summary: required files present + TOOL.md preview."""
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


# ---------------------------------------------------------------------------
# Tool package VCS (PR1)
# ---------------------------------------------------------------------------


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
            "local_dir": result.get("local_dir")
            or str(local_package_dir(ctx.paths, name)),
            "restored_version_id": result.get("restored_version_id", version_id),
            "archived_version_id": result.get("archived_version_id"),
            "content_hash": result.get("content_hash"),
            "reloaded": reloaded,
            "callable": callable_now,
            "reason": reason,
        },
    )


# ---------------------------------------------------------------------------
# Skill package paths / validation / archive helpers (PR2)
# ---------------------------------------------------------------------------


def draft_skills_dir(paths: ElyraPaths) -> Path:
    """Resolved ``skills/drafts/`` root."""
    return (paths.skills_dir / "drafts").resolve()


def draft_skill_dir(paths: ElyraPaths, name: str) -> Path:
    """Resolved path to ``skills/drafts/<name>/``."""
    return (draft_skills_dir(paths) / name).resolve()


def local_skill_package_dir(paths: ElyraPaths, name: str) -> Path:
    """Resolved path to ``skills/local/<name>/``."""
    return (local_skills_dir(paths) / name).resolve()


def local_skills_root_dir(paths: ElyraPaths) -> Path:
    """Resolved ``skills/local/`` root."""
    return local_skills_dir(paths).resolve()


def skill_lock_path_for(paths: ElyraPaths, name: str) -> Path:
    """Path of non-blocking exclusive lock ``skills/local/.<casefold(name)>.lock``."""
    key = normalize_skill_name(name)
    return local_skills_root_dir(paths) / f".{key}.lock"


def find_local_skill_dir(paths: ElyraPaths, name: str) -> Path | None:
    """Return existing ``skills/local/<name>/`` (case-normalized), or None."""
    local_root = local_skills_root_dir(paths)
    if not local_root.is_dir():
        return None
    key = normalize_skill_name(name)
    for child in local_root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        if normalize_skill_name(child.name) == key:
            return child.resolve()
    return None


def bundled_skill_name_exists(
    name: str,
    *,
    bundled_root: Path | str | None = None,
) -> bool:
    """True if a bundled skill directory exists for ``name`` (case-normalized)."""
    try:
        root = resolve_bundled_skills_root(bundled_root)
    except BundledSkillsRootError:
        return False
    if not root.is_dir():
        return False
    key = normalize_skill_name(name)
    for child in root.iterdir():
        if child.is_dir() and normalize_skill_name(child.name) == key:
            return True
    return False


def skill_package_is_complete(package_dir: Path) -> bool:
    """True when SKILL.md exists (not hollow versions-only)."""
    package_dir = Path(package_dir)
    if not package_dir.is_dir():
        return False
    return (package_dir / _SKILL_MD_NAME).is_file()


def assemble_skill_md(name: str, description: str, body: str) -> str:
    """Assemble SKILL.md in the same format as hand-written packages."""
    desc_line = (description or "").strip() or name
    content = (
        f"---\n"
        f"name: {name}\n"
        f"description: {desc_line}\n"
        f"---\n\n"
        f"{body.lstrip()}"
    )
    if not content.endswith("\n"):
        content += "\n"
    return content


def validate_skill_package(package_dir: Path) -> str | None:
    """Skill promote gates (IK5): SKILL.md + frontmatter + size.

    Returns error_reason or None on success.
    """
    package_dir = Path(package_dir)
    skill_md = package_dir / _SKILL_MD_NAME
    if not skill_md.is_file():
        return "missing_skill_md"
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return "missing_skill_md"
    if body_byte_len(text) > MAX_BODY_BYTES:
        return "body_too_large"
    fields = _parse_frontmatter(text)
    name = (fields.get("name") or "").strip()
    description = (fields.get("description") or "").strip()
    if not name or not description:
        return "invalid_frontmatter"
    return None


def _skill_package_summary(package_dir: Path) -> dict[str, Any]:
    """Thin skill package summary: SKILL.md present + preview."""
    top_level: list[str] = []
    try:
        for child in sorted(package_dir.iterdir(), key=lambda p: p.name):
            if child.name in (VERSIONS_DIR_NAME, VERSIONS_META_NAME):
                continue
            if child.name.startswith(".") and child.name != _SKILL_MD_NAME:
                if child.name.startswith(".promote") or child.name.startswith(
                    ".aside"
                ):
                    continue
            top_level.append(child.name + ("/" if child.is_dir() else ""))
    except OSError:
        pass
    summary: dict[str, Any] = {
        "path": str(package_dir),
        "complete": skill_package_is_complete(package_dir),
        "files_present": {_SKILL_MD_NAME: (package_dir / _SKILL_MD_NAME).is_file()},
        "top_level": top_level,
    }
    skill_md = _read_preview(package_dir / _SKILL_MD_NAME)
    if skill_md is not None:
        summary["skill_md_preview"] = skill_md
    return summary


def _copy_history_onto_stage(dest: Path, stage: Path) -> None:
    """Copy versions/ + .versions_meta.json from dest onto stage."""
    dest_versions = dest / VERSIONS_DIR_NAME
    stage_versions = stage / VERSIONS_DIR_NAME
    if dest_versions.is_dir():
        if stage_versions.exists():
            shutil.rmtree(stage_versions)
        shutil.copytree(dest_versions, stage_versions)
    dest_meta = dest / VERSIONS_META_NAME
    if dest_meta.is_file():
        shutil.copy2(dest_meta, stage / VERSIONS_META_NAME)


def _unique_skill_sibling(local_root: Path, name: str, kind: str) -> Path:
    """``skills/local/.<casefold(name)>.<kind>.<pid>.<uuid>/`` — never inside dest."""
    token = uuid.uuid4().hex[:12]
    key = normalize_skill_name(name) or name
    return local_root / f".{key}.{kind}.{os.getpid()}.{token}"


def _rename_path(src: Path, dst: Path) -> None:
    """Rename with EXDEV fallback (copytree + rmtree), whole-tree only."""
    try:
        os.rename(src, dst)
    except OSError as exc:
        if getattr(exc, "errno", None) != errno.EXDEV:
            raise
        if dst.exists():
            raise
        shutil.copytree(src, dst)
        shutil.rmtree(src)


@contextmanager
def skill_package_lock(paths: ElyraPaths, name: str) -> Iterator[None]:
    """Non-blocking exclusive lock on ``skills/local/.<name>.lock``.

    Raises :class:`PackageLockedError` on contention. Always unlocks in finally.
    """
    root = local_skills_root_dir(paths)
    root.mkdir(parents=True, exist_ok=True)
    path = skill_lock_path_for(paths, name)
    fd = open(path, "a+", encoding="utf-8")  # noqa: SIM115 — closed in finally
    try:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PackageLockedError(str(path)) from exc
        except OSError as exc:
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


def write_skill_draft(
    paths: ElyraPaths,
    name: str,
    content: str,
) -> dict[str, Any]:
    """Write ``skills/drafts/<name>/SKILL.md`` only. Returns result dict."""
    if not isinstance(name, str) or not is_valid_skill_name(name):
        return {"ok": False, "error_reason": "invalid_name"}
    name = name.strip()
    if body_byte_len(content) > MAX_BODY_BYTES:
        return {"ok": False, "error_reason": "body_too_large"}

    drafts_root = draft_skills_dir(paths)
    draft_dir = draft_skill_dir(paths, name)
    try:
        drafts_root.mkdir(parents=True, exist_ok=True)
        draft_dir.mkdir(parents=True, exist_ok=True)
        if not draft_dir.resolve().is_relative_to(drafts_root.resolve()):
            return {"ok": False, "error_reason": "path_jail"}
        skill_md = draft_dir / _SKILL_MD_NAME
        skill_md.write_text(content, encoding="utf-8")
        final = skill_md.resolve()
        if not final.is_relative_to(drafts_root.resolve()):
            try:
                skill_md.unlink(missing_ok=True)
            except OSError:
                pass
            return {"ok": False, "error_reason": "path_jail"}
    except OSError as exc:
        return {
            "ok": False,
            "error_reason": f"write_failed:{type(exc).__name__}",
        }

    # Lightweight validate after write so callers get frontmatter errors early.
    shape_err = validate_skill_package(draft_dir)
    if shape_err is not None:
        return {"ok": False, "error_reason": shape_err, "draft_dir": str(draft_dir)}

    return {
        "ok": True,
        "name": name,
        "draft_dir": str(draft_dir),
        "path": str(draft_dir / _SKILL_MD_NAME),
        "content_hash": content_hash(draft_dir),
    }


def promote_draft_skill(
    paths: ElyraPaths,
    name: str,
    *,
    bundled_root: Path | str | None = None,
    force: Any = None,
) -> dict[str, Any]:
    """Promote skill draft → local with archive-on-replace. No force flag.

    Same whole-tree rename algorithm as tools (IK4). Skill gates: IK5 only
    (no verify_tool). Caller reloads SkillCatalog.
    """
    if force is not None:
        return {"ok": False, "error_reason": "force_not_allowed"}

    if not isinstance(name, str) or not is_valid_skill_name(name):
        return {"ok": False, "error_reason": "invalid_name"}
    name = name.strip()

    draft_dir = draft_skill_dir(paths, name)
    if not draft_dir.is_dir():
        return {"ok": False, "error_reason": "draft_missing"}

    shape_err = validate_skill_package(draft_dir)
    if shape_err is not None:
        return {"ok": False, "error_reason": shape_err}

    if bundled_skill_name_exists(name, bundled_root=bundled_root):
        return {"ok": False, "error_reason": "refuses_overwrite_bundled"}

    current_hash = content_hash(draft_dir)
    local_root = local_skills_root_dir(paths)
    local_root.mkdir(parents=True, exist_ok=True)
    dest = find_local_skill_dir(paths, name) or local_skill_package_dir(paths, name)

    stage: Path | None = None
    aside_full: Path | None = None
    archived_version_id: str | None = None

    try:
        with skill_package_lock(paths, name):
            dest = find_local_skill_dir(paths, name) or local_skill_package_dir(
                paths, name
            )
            stage = _unique_skill_sibling(local_root, name, "promote")
            aside_full = _unique_skill_sibling(local_root, name, "aside")

            try:
                # Stage draft payload only (SKILL.md); no history on draft.
                copy_package_payload(draft_dir, stage)
            except OSError as exc:
                _LOG.warning("skill promote stage copy failed for %s: %s", name, exc)
                if stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)
                return {
                    "ok": False,
                    "error_reason": f"promote_failed:{type(exc).__name__}",
                }

            if dest.exists():
                try:
                    _copy_history_onto_stage(dest, stage)
                    entry = archive_local_payload(dest, into=stage)
                    archived_version_id = str(entry["version_id"])
                    meta = load_versions_meta(stage)
                    meta.append(entry)
                    save_versions_meta(stage, meta)
                    gc_package_versions(stage, limit=VERSION_GC_LIMIT)
                except OSError as exc:
                    _LOG.warning("skill promote archive failed for %s: %s", name, exc)
                    if stage.exists():
                        shutil.rmtree(stage, ignore_errors=True)
                    return {
                        "ok": False,
                        "error_reason": f"promote_failed:{type(exc).__name__}",
                    }

            try:
                whole_tree_rename_swap(
                    stage=stage, dest=dest, aside_full=aside_full
                )
            except Exception as exc:  # noqa: BLE001 — structured promote_failed:*
                _LOG.warning("skill promote rename swap failed for %s: %s", name, exc)
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

            if stage is not None and stage.exists():
                shutil.rmtree(stage, ignore_errors=True)

            try:
                shutil.rmtree(draft_dir)
            except OSError as exc:
                _LOG.warning(
                    "skill promote draft cleanup failed for %s: %s", name, exc
                )

            try:
                draft_skills_dir(paths).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

            result: dict[str, Any] = {
                "ok": True,
                "skill_name": name,
                "local_dir": str(dest),
                "path": str(dest / _SKILL_MD_NAME),
                "content_hash": current_hash,
            }
            if archived_version_id is not None:
                result["archived_version_id"] = archived_version_id
            return result

    except PackageLockedError:
        return {"ok": False, "error_reason": "promote_locked"}


def revert_local_skill(
    paths: ElyraPaths,
    name: str,
    version_id: str,
    reason: str,
    *,
    bundled_root: Path | str | None = None,
) -> dict[str, Any]:
    """Archive current local skill and restore ``versions/<version_id>/``."""
    if not isinstance(name, str) or not is_valid_skill_name(name):
        return {"ok": False, "error_reason": "invalid_name"}
    name = name.strip()

    if not isinstance(version_id, str) or not VERSION_ID_RE.fullmatch(version_id):
        return {"ok": False, "error_reason": "version_not_found"}

    if bundled_skill_name_exists(name, bundled_root=bundled_root):
        return {"ok": False, "error_reason": "refuses_overwrite_bundled"}

    dest = find_local_skill_dir(paths, name)
    if dest is None or not dest.is_dir():
        return {"ok": False, "error_reason": "package_not_found"}

    version_dir = dest / VERSIONS_DIR_NAME / version_id
    if not version_dir.is_dir():
        return {"ok": False, "error_reason": "version_not_found"}

    local_root = local_skills_root_dir(paths)
    stage: Path | None = None
    aside_full: Path | None = None

    try:
        with skill_package_lock(paths, name):
            dest = find_local_skill_dir(paths, name)
            if dest is None or not dest.is_dir():
                return {"ok": False, "error_reason": "package_not_found"}
            version_dir = dest / VERSIONS_DIR_NAME / version_id
            if not version_dir.is_dir():
                return {"ok": False, "error_reason": "version_not_found"}

            stage = _unique_skill_sibling(local_root, name, "revert")
            aside_full = _unique_skill_sibling(local_root, name, "aside")

            try:
                copy_package_payload(version_dir, stage)
                _copy_history_onto_stage(dest, stage)
                entry = archive_local_payload(
                    dest,
                    into=stage,
                    reason=f"pre_revert:{reason.strip()}",
                )
                meta = load_versions_meta(stage)
                meta.append(entry)
                save_versions_meta(stage, meta)
                gc_package_versions(stage, limit=VERSION_GC_LIMIT)
            except OSError as exc:
                _LOG.warning("skill revert stage failed for %s: %s", name, exc)
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
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("skill revert rename swap failed for %s: %s", name, exc)
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
                "skill_name": name,
                "local_dir": str(dest),
                "path": str(dest / _SKILL_MD_NAME),
                "restored_version_id": version_id,
                "archived_version_id": entry["version_id"],
                "content_hash": content_hash(dest) if dest.is_dir() else None,
            }

    except PackageLockedError:
        return {"ok": False, "error_reason": "package_locked"}


def _reload_skills(ctx: ToolContext) -> bool:
    existing = ctx.extras.get("skills")
    if isinstance(existing, SkillCatalog):
        try:
            existing.reload()
            return True
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("SkillCatalog.reload failed: %s", exc)
    return False


# ---------------------------------------------------------------------------
# Skill package VCS tools
# ---------------------------------------------------------------------------


def install_skill_draft(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Write only ``skills/drafts/<name>/SKILL.md`` (no promote)."""
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

    body = args.get("body")
    if body is None:
        return ToolResult(ok=False, payload={}, error_reason="missing_body")
    if not isinstance(body, str):
        return ToolResult(ok=False, payload={}, error_reason="invalid_body")

    if body_byte_len(body) > MAX_BODY_BYTES:
        return ToolResult(ok=False, payload={}, error_reason="body_too_large")

    content = assemble_skill_md(name, description, body)
    if body_byte_len(content) > MAX_BODY_BYTES:
        return ToolResult(ok=False, payload={}, error_reason="body_too_large")

    result = write_skill_draft(ctx.paths, name, content)
    if not result.get("ok"):
        return ToolResult(
            ok=False,
            payload={
                k: v
                for k, v in result.items()
                if k not in {"ok", "error_reason"} and v is not None
            },
            error_reason=str(result.get("error_reason") or "write_failed"),
        )

    return ToolResult(
        ok=True,
        payload={
            "name": name,
            "draft_dir": result.get("draft_dir"),
            "path": result.get("path"),
            "content_hash": result.get("content_hash"),
        },
    )


def promote_skill(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Promote skill draft → skills/local/; archive prior local; catalog reload."""
    name = args.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        return ToolResult(ok=False, payload={}, error_reason="missing_name")
    if not isinstance(name, str) or not is_valid_skill_name(name):
        return ToolResult(ok=False, payload={}, error_reason="invalid_name")
    name = name.strip()

    if "force" in args:
        return ToolResult(ok=False, payload={}, error_reason="force_not_allowed")

    result = promote_draft_skill(ctx.paths, name, force=None)
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

    reloaded = _reload_skills(ctx)
    payload: dict[str, Any] = {
        "name": name,
        "local_dir": result.get("local_dir"),
        "path": result.get("path"),
        "content_hash": result.get("content_hash"),
        "reloaded": reloaded,
    }
    if result.get("archived_version_id") is not None:
        payload["archived_version_id"] = result["archived_version_id"]
    return ToolResult(ok=True, payload=payload)


def revert_skill(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Restore a prior skill package version; reason required (min 8 chars)."""
    name = args.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        return ToolResult(ok=False, payload={}, error_reason="missing_name")
    if not isinstance(name, str) or not is_valid_skill_name(name):
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

    result = revert_local_skill(ctx.paths, name, version_id, reason)
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

    reloaded = _reload_skills(ctx)
    return ToolResult(
        ok=True,
        payload={
            "name": name,
            "local_dir": result.get("local_dir")
            or str(local_skill_package_dir(ctx.paths, name)),
            "path": result.get("path"),
            "restored_version_id": result.get("restored_version_id", version_id),
            "archived_version_id": result.get("archived_version_id"),
            "content_hash": result.get("content_hash"),
            "reloaded": reloaded,
            "reason": reason,
        },
    )


def get_skill(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Read current/draft/version skill package summary; optional list_versions."""
    name = args.get("name")
    if name is None or (isinstance(name, str) and not name.strip()):
        return ToolResult(ok=False, payload={}, error_reason="missing_name")
    if not isinstance(name, str) or not is_valid_skill_name(name):
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
        draft = draft_skill_dir(ctx.paths, name)
        if not draft.is_dir() or not (draft / _SKILL_MD_NAME).is_file():
            return ToolResult(
                ok=False,
                payload={"name": name, "which": "draft"},
                error_reason="draft_missing",
            )
        payload["package"] = _skill_package_summary(draft)
        if list_versions:
            payload["versions"] = []
        return ToolResult(ok=True, payload=payload)

    local = find_local_skill_dir(ctx.paths, name)
    if local is None:
        if which == "current" and bundled_skill_name_exists(name):
            try:
                root = resolve_bundled_skills_root()
                key = normalize_skill_name(name)
                bundled_dir = None
                for child in root.iterdir():
                    if child.is_dir() and normalize_skill_name(child.name) == key:
                        bundled_dir = child
                        break
                if bundled_dir is not None:
                    payload["source"] = "bundled"
                    payload["package"] = _skill_package_summary(bundled_dir)
                    if list_versions:
                        payload["versions"] = []
                    return ToolResult(ok=True, payload=payload)
            except BundledSkillsRootError:
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
        payload["package"] = _skill_package_summary(vdir)
    else:
        payload["package"] = _skill_package_summary(local)

    if list_versions:
        payload["versions"] = _versions_meta_only(local)

    return ToolResult(ok=True, payload=payload)


__all__ = [
    "assemble_skill_md",
    "bundled_skill_name_exists",
    "draft_skill_dir",
    "draft_skills_dir",
    "find_local_skill_dir",
    "get_skill",
    "get_tool",
    "install_skill_draft",
    "local_skill_package_dir",
    "promote_draft_skill",
    "promote_skill",
    "revert_local_skill",
    "revert_skill",
    "revert_tool",
    "skill_lock_path_for",
    "skill_package_is_complete",
    "skill_package_lock",
    "validate_skill_package",
    "write_skill_draft",
]
