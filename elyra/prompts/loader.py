"""Load named prompt files from disk.

Scope: resolve system/orient (and other) prompts under prompts/.
In scope: home prompts_dir, fallback to project-root prompts/, UTF-8 text.
Out of scope: template rendering, embedding multi-page strings in Python.
"""

from __future__ import annotations

from pathlib import Path

from elyra.config import ElyraPaths, project_root, resolve_paths


def resolve_prompt_path(
    name: str,
    paths: ElyraPaths | None = None,
) -> Path | None:
    """Find ``name`` (with or without .md) under home then project prompts/."""
    filename = name if name.endswith(".md") else f"{name}.md"
    bases: list[Path] = []
    if paths is not None:
        bases.append(paths.prompts_dir)
    else:
        bases.append(resolve_paths().prompts_dir)
    project_prompts = project_root() / "prompts"
    if project_prompts not in bases:
        bases.append(project_prompts)

    for base in bases:
        candidate = base / filename
        if candidate.is_file():
            return candidate
    return None


def load_prompt(
    name: str,
    paths: ElyraPaths | None = None,
) -> str:
    """Return prompt text for ``name`` (e.g. ``system``, ``orient.md``).

    Raises ``FileNotFoundError`` if no matching file is found.
    """
    path = resolve_prompt_path(name, paths=paths)
    if path is None:
        raise FileNotFoundError(f"prompt not found: {name}")
    return path.read_text(encoding="utf-8")
