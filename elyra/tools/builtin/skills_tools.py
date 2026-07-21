"""Builtin skill tools — load playbook bodies mid-loop.

Scope: load_skill host entry for tools/bundled/load_skill.
In scope: resolve SkillCatalog from ctx, full body payload, skills_used append.
Out of scope: install_skill (PR13), orient catalog formatting.
"""

from __future__ import annotations

from typing import Any

from elyra.skills import SkillCatalog, is_valid_skill_name, normalize_skill_name
from elyra.skills.policy import BundledSkillsRootError
from elyra.tools.types import ToolContext, ToolResult


def _resolve_catalog(ctx: ToolContext) -> SkillCatalog | None:
    """Return SkillCatalog from ctx.extras or build from paths.

    Callers (do-loop) should inject ``ctx.extras["skills"]`` once per moment.
    When absent, build a catalog from ``ctx.paths`` (tests / ad-hoc execute).
    """
    existing = ctx.extras.get("skills")
    if isinstance(existing, SkillCatalog):
        return existing
    try:
        return SkillCatalog(ctx.paths)
    except BundledSkillsRootError:
        return None


def load_skill(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Load a skill playbook body by name.

    Catalog is short (name + description in orient); this tool returns the full
    SKILL.md text. On success, appends the skill name to ``ctx.skills_used``
    (once per name per bag).
    """
    name = args.get("name")
    # Absent / empty string → missing_name; type-wrong or malformed → invalid_name
    # (align with PR6 tool execute: non-str is invalid, not "missing").
    if name is None:
        return ToolResult(
            ok=False,
            payload={},
            error_reason="missing_name",
        )
    if not isinstance(name, str):
        return ToolResult(
            ok=False,
            payload={},
            error_reason="invalid_name",
        )
    name = name.strip()
    if not name:
        return ToolResult(
            ok=False,
            payload={},
            error_reason="missing_name",
        )
    if not is_valid_skill_name(name):
        return ToolResult(
            ok=False,
            payload={},
            error_reason="invalid_name",
        )

    catalog = _resolve_catalog(ctx)
    if catalog is None:
        return ToolResult(
            ok=False,
            payload={},
            error_reason="skills_unavailable",
        )

    meta = catalog.load(name)
    if meta is None:
        return ToolResult(
            ok=False,
            payload={"name": name},
            error_reason="unknown_skill",
        )

    # Track for moment meta (append once).
    key = normalize_skill_name(meta.name)
    already = {normalize_skill_name(s) for s in ctx.skills_used if isinstance(s, str)}
    if key not in already:
        ctx.skills_used.append(meta.name)

    return ToolResult(
        ok=True,
        payload={
            "name": meta.name,
            "description": meta.description,
            "source": meta.source,
            "body": meta.body,
        },
    )
