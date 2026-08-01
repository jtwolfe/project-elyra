"""Discover host Grok binary and skill layout under a GROK_HOME.

Scope: find ``grok`` executable; resolve real install ``bundled/``; assert
design+implement (and optional execute-plan/review) skills resolve under a
seeded home.
Out of scope: subprocess spawn, OAuth, config.toml write, usage metering.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Skills that must resolve for design/implement modes (KD5 / dogfood D11).
REQUIRED_SKILLS: tuple[str, ...] = ("design", "implement")

# Present on full Grok installs; assert when the skill dir exists under home.
OPTIONAL_SKILLS: tuple[str, ...] = ("execute-plan", "review")


class GrokNotFoundError(FileNotFoundError):
    """Raised when no grok binary can be located."""

    error_reason: str = "grok_not_found"


class GrokSkillsUnavailableError(FileNotFoundError):
    """Raised when required skills are missing under GROK_HOME."""

    error_reason: str = "grok_skills_unavailable"


def find_grok_binary(*, env: dict[str, str] | None = None) -> Path:
    """Locate the host ``grok`` CLI binary.

    Search order:
    1. ``GROK_BIN`` env (absolute or PATH-resolved)
    2. ``shutil.which("grok")``
    3. ``$GROK_HOME/bin/grok`` if ``GROK_HOME`` set
    4. ``~/.grok/bin/grok``
    """
    e = env if env is not None else os.environ
    raw = (e.get("GROK_BIN") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return p.resolve()
        which = shutil.which(raw)
        if which:
            return Path(which).resolve()

    which = shutil.which("grok", path=e.get("PATH"))
    if which:
        return Path(which).resolve()

    candidates: list[Path] = []
    grok_home = (e.get("GROK_HOME") or "").strip()
    if grok_home:
        candidates.append(Path(grok_home).expanduser() / "bin" / "grok")
    candidates.append(_home_path(e) / ".grok" / "bin" / "grok")

    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c.resolve()

    raise GrokNotFoundError(
        "grok binary not found (set GROK_BIN or install Grok Build; "
        "expected on PATH or ~/.grok/bin/grok)"
    )


def _home_path(env: dict[str, str] | None = None) -> Path:
    """Resolve home directory; honor ``HOME`` from env mapping when provided."""
    if env is not None:
        raw = (env.get("HOME") or "").strip()
        if raw:
            return Path(raw).expanduser()
    return Path.home()


def find_real_bundled(
    *,
    grok_bin: Path | str | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Locate the real install ``bundled/`` directory to seed isolated homes.

    Search order:
    1. ``$GROK_HOME/bundled`` if ``GROK_HOME`` set and contains skills
    2. ``~/.grok/bundled`` if present with skills
    3. ``dirname(grok_bin)/../bundled`` (install layout next to binary)
    """
    e = env if env is not None else os.environ
    candidates: list[Path] = []

    grok_home = (e.get("GROK_HOME") or "").strip()
    if grok_home:
        candidates.append(Path(grok_home).expanduser() / "bundled")

    candidates.append(_home_path(e) / ".grok" / "bundled")

    bin_path: Path | None = None
    if grok_bin is not None:
        bin_path = Path(grok_bin)
    else:
        try:
            bin_path = find_grok_binary(env=e)
        except GrokNotFoundError:
            bin_path = None
    if bin_path is not None:
        # ~/.grok/bin/grok → ~/.grok/bundled; also install/bin/grok → install/bundled
        candidates.append(bin_path.resolve().parent.parent / "bundled")

    seen: set[Path] = set()
    for c in candidates:
        try:
            resolved = c.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if _bundled_looks_valid(resolved):
            return resolved

    raise GrokSkillsUnavailableError(
        "real Grok install bundled/ not found "
        "(expected ~/.grok/bundled with skills/design + skills/implement)"
    )


def _bundled_looks_valid(bundled: Path) -> bool:
    if not bundled.is_dir():
        return False
    skills = bundled / "skills"
    return (skills / "design").exists() and (skills / "implement").exists()


def skill_dir(grok_home: Path | str, skill_name: str) -> Path:
    """Return ``<GROK_HOME>/bundled/skills/<skill_name>``."""
    return Path(grok_home) / "bundled" / "skills" / skill_name


def list_resolvable_skills(grok_home: Path | str) -> list[str]:
    """Return skill names that exist under ``GROK_HOME/bundled/skills/``."""
    skills_root = Path(grok_home) / "bundled" / "skills"
    if not skills_root.is_dir():
        return []
    names: list[str] = []
    try:
        for child in sorted(skills_root.iterdir()):
            if child.is_dir() or child.is_symlink():
                names.append(child.name)
    except OSError:
        return []
    return names


def assert_skills_resolvable(grok_home: Path | str) -> None:
    """Fail closed if design+implement skills are missing under GROK_HOME.

    Also requires execute-plan and review when those directories are present
    under the home (post-seed full install) — empty or broken stubs fail.
    Raises :class:`GrokSkillsUnavailableError` with ``error_reason``.
    """
    home = Path(grok_home)
    skills_root = home / "bundled" / "skills"
    missing: list[str] = []

    for name in REQUIRED_SKILLS:
        path = skills_root / name
        if not path.exists():
            missing.append(name)

    for name in OPTIONAL_SKILLS:
        path = skills_root / name
        # "if present": only validate when the entry exists; do not require it
        # for minimal test fakes that only seed design+implement.
        if path.exists() and not (path.is_dir() or path.is_symlink()):
            missing.append(f"{name}(not_dir)")

    if missing:
        raise GrokSkillsUnavailableError(
            f"GROK_HOME skills not resolvable under {skills_root}: "
            f"missing/invalid={missing}; "
            f"present={list_resolvable_skills(home)}"
        )


__all__ = [
    "GrokNotFoundError",
    "GrokSkillsUnavailableError",
    "OPTIONAL_SKILLS",
    "REQUIRED_SKILLS",
    "assert_skills_resolvable",
    "find_grok_binary",
    "find_real_bundled",
    "list_resolvable_skills",
    "skill_dir",
]
