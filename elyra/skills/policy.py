"""Skill name policy and discovery-root resolution.

Scope: name normalization/validation, BUNDLED_SKILLS_ROOT resolve.
In scope: case-normalized keys, safe package names, bundled root assert.
Out of scope: package loading, install_skill writes, tool dispatch.
"""

from __future__ import annotations

import re
from pathlib import Path

from elyra.config import project_root

# Same pattern as tools: snake or kebab, start alnum/underscore.
_SKILL_NAME_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]*$", re.IGNORECASE)


class BundledSkillsRootError(FileNotFoundError):
    """Raised when BUNDLED_SKILLS_ROOT cannot be resolved (non-editable install)."""


def normalize_skill_name(name: object) -> str:
    """Case-fold and strip for isolation keys (local vs bundled clash).

    Non-strings return ``""`` so callers can map to invalid_name without raising.
    """
    if not isinstance(name, str):
        return ""
    return name.strip().casefold()


def is_valid_skill_name(name: object) -> bool:
    """True if ``name`` matches the allowed package name pattern."""
    if not isinstance(name, str):
        return False
    raw = name.strip()
    if not raw:
        return False
    return bool(_SKILL_NAME_RE.match(raw))


def resolve_bundled_skills_root(override: Path | str | None = None) -> Path:
    """Locate shipped ``skills/bundled`` under the project/code tree.

    Resolution order:
      1. Explicit ``override`` (settings / test inject)
      2. ``project_root()/skills/bundled`` (editable / repo-root dogfood)

    Stretch 1 does not support non-editable wheel installs of bundled skills.
    Raises :class:`BundledSkillsRootError` with a clear recovery message when
    the directory is missing.
    """
    if override is not None:
        root = Path(override).expanduser().resolve()
        if not root.is_dir():
            raise BundledSkillsRootError(
                f"bundled_skills_root override is not a directory: {root}. "
                "Create skills/bundled under the project or pass a valid path."
            )
        return root

    candidate = (project_root() / "skills" / "bundled").resolve()
    if candidate.is_dir():
        return candidate

    raise BundledSkillsRootError(
        f"BUNDLED_SKILLS_ROOT not found at {candidate}. "
        "Stretch 1 requires an editable install or repo checkout with "
        "skills/bundled/, or set bundled_skills_root explicitly "
        "(elyra.toml / SkillCatalog(bundled_root=…)). "
        "Non-editable wheel packaging of bundled skills is out of scope for S1."
    )
