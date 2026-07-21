"""Discover skill packages and load full SKILL.md bodies.

Scope: scan bundled + local skills, short catalog, full body on load.
In scope: BUNDLED_SKILLS_ROOT assert, local-over-bundled, frontmatter parse.
Out of scope: install_skill writes, loop orient formatting, create-skill.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from elyra.config import ElyraPaths, resolve_paths
from elyra.skills.policy import (
    is_valid_skill_name,
    normalize_skill_name,
    resolve_bundled_skills_root,
)

_LOG = logging.getLogger(__name__)

SOURCE_BUNDLED = "bundled"
SOURCE_LOCAL = "local"

# YAML-ish frontmatter between --- fences (stdlib only; no PyYAML dep).
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(.*?)\n---\s*(?:\n|$)",
    re.DOTALL,
)


@dataclass(frozen=True)
class SkillMeta:
    """Parsed skill identity from SKILL.md + directory name."""

    name: str
    description: str
    source: str  # bundled | local
    package_dir: Path
    # Full SKILL.md text (frontmatter + body) — set on load(); empty in catalog-only.
    body: str = ""


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Parse simple key: value frontmatter (no nested YAML)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            fields[key] = value
    return fields


def _body_after_frontmatter(text: str) -> str:
    """Return markdown body after frontmatter fence (or full text if none)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return text
    return text[match.end() :]


def load_skill_meta(
    package_dir: Path,
    *,
    source: str,
    default_name: str | None = None,
    include_body: bool = False,
) -> SkillMeta:
    """Load SKILL.md for a package directory.

    Directory basename is the authority for the skill name when frontmatter
    omits ``name`` (dogfood: folder name = skill name).
    """
    package_dir = Path(package_dir)
    dir_name = package_dir.name
    name = default_name or dir_name
    description = ""
    body = ""

    skill_md = package_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"missing SKILL.md in {package_dir}")

    text = skill_md.read_text(encoding="utf-8")
    fields = _parse_frontmatter(text)
    if fields.get("name"):
        name = fields["name"].strip()
    if fields.get("description"):
        description = fields["description"].strip()
    if not description:
        # First non-empty body line as fallback description.
        for line in _body_after_frontmatter(text).splitlines():
            stripped = line.strip().lstrip("#").strip()
            if stripped:
                description = stripped
                break
    if include_body:
        body = text

    # Directory basename is canonical (case-only frontmatter mismatch cannot
    # advertise a different callable/catalog name).
    if name != dir_name:
        if normalize_skill_name(name) != normalize_skill_name(dir_name):
            _LOG.warning(
                "skill package dir %s name %r differs; using directory name",
                dir_name,
                name,
            )
        name = dir_name

    return SkillMeta(
        name=name,
        description=description or name,
        source=source,
        package_dir=package_dir,
        body=body,
    )


class SkillCatalog:
    """In-memory catalog of skill packages (bundled + local).

    Catalog entries are short (name + description). Full SKILL.md body is
    loaded on demand via :meth:`load`.
    """

    def __init__(
        self,
        paths: ElyraPaths | None = None,
        *,
        bundled_root: Path | str | None = None,
        local_root: Path | str | None = None,
    ) -> None:
        """Build catalog and scan roots.

        Parameters
        ----------
        paths:
            Elyra home paths (local skills under ``paths.skills_dir / "local"``).
        bundled_root:
            Override for BUNDLED_SKILLS_ROOT (tests / elyra.toml). When None,
            resolve from project tree; missing dir raises BundledSkillsRootError.
        local_root:
            Override local skills directory (default ``$ELYRA_HOME/skills/local``).
        """
        self._paths = paths or resolve_paths()
        self._bundled_root = resolve_bundled_skills_root(bundled_root)
        if local_root is not None:
            self._local_root = Path(local_root).expanduser().resolve()
        else:
            self._local_root = (self._paths.skills_dir / "local").resolve()
        self._by_key: dict[str, SkillMeta] = {}
        self._override_logged: set[str] = set()
        self.reload()

    @property
    def bundled_root(self) -> Path:
        return self._bundled_root

    @property
    def local_root(self) -> Path:
        return self._local_root

    @property
    def paths(self) -> ElyraPaths:
        return self._paths

    def reload(self) -> None:
        """Rescan bundled + local; local names win over bundled (log once)."""
        found: dict[str, SkillMeta] = {}
        for meta in self._scan_root(self._bundled_root, source=SOURCE_BUNDLED):
            key = normalize_skill_name(meta.name)
            found[key] = meta
        for meta in self._scan_root(self._local_root, source=SOURCE_LOCAL):
            key = normalize_skill_name(meta.name)
            if key in found and found[key].source == SOURCE_BUNDLED:
                if key not in self._override_logged:
                    _LOG.info(
                        "local skill %r overrides bundled package at %s",
                        meta.name,
                        found[key].package_dir,
                    )
                    self._override_logged.add(key)
            found[key] = meta
        self._by_key = found

    def names(self) -> list[str]:
        """Sorted skill names."""
        return sorted(m.name for m in self._by_key.values())

    def get(self, name: str) -> SkillMeta | None:
        """Return short meta (no body) for a skill, or None."""
        return self._by_key.get(normalize_skill_name(name))

    def has(self, name: str) -> bool:
        return normalize_skill_name(name) in self._by_key

    def catalog(self) -> list[dict[str, str]]:
        """Short catalog for orient: name + description only (sorted by name)."""
        items = sorted(self._by_key.values(), key=lambda m: m.name)
        return [
            {"name": m.name, "description": m.description}
            for m in items
        ]

    def load(self, name: str) -> SkillMeta | None:
        """Load full SKILL.md body for ``name``.

        Returns None when the name is unknown. Re-reads disk so body is current
        after external edits without requiring a full reload for other skills.
        """
        if not isinstance(name, str) or not is_valid_skill_name(name):
            return None
        key = normalize_skill_name(name)
        short = self._by_key.get(key)
        if short is None:
            return None
        try:
            return load_skill_meta(
                short.package_dir,
                source=short.source,
                default_name=short.package_dir.name,
                include_body=True,
            )
        except (OSError, ValueError) as exc:
            _LOG.warning("failed to load skill %s: %s", name, exc)
            return None

    def _scan_root(self, root: Path, *, source: str) -> Iterable[SkillMeta]:
        if not root.is_dir():
            return
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if child.name.startswith("."):
                continue
            if not is_valid_skill_name(child.name):
                _LOG.warning("skip skill package with invalid name: %s", child)
                continue
            if not (child / "SKILL.md").is_file():
                continue
            try:
                yield load_skill_meta(
                    child,
                    source=source,
                    default_name=child.name,
                    include_body=False,
                )
            except Exception as exc:  # noqa: BLE001 — skip bad packages
                _LOG.warning("skip broken skill package %s: %s", child, exc)


def local_skills_dir(paths: ElyraPaths) -> Path:
    """Path to local skills root (install_skill writes here)."""
    return paths.skills_dir / "local"


__all__ = [
    "SOURCE_BUNDLED",
    "SOURCE_LOCAL",
    "SkillCatalog",
    "SkillMeta",
    "load_skill_meta",
    "local_skills_dir",
]
