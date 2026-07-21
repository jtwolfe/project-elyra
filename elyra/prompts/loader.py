"""Load named prompt files from disk.

Scope: resolve system/orient (and other) prompts under prompts/.
In scope: home prompts_dir, fallback to project-root prompts/, UTF-8 text.
Out of scope: template rendering, embedding multi-page strings in Python.
"""

from __future__ import annotations

from pathlib import Path

from elyra.config import ElyraPaths, project_root, resolve_paths


def _normalize_prompt_filename(name: str) -> str | None:
    """Return a single safe ``*.md`` filename, or None if ``name`` is unsafe.

    Rejects empty names, absolute paths, separators, and ``.`` / ``..``.
    """
    if not name or not isinstance(name, str):
        return None
    raw = name.strip()
    if not raw:
        return None
    # Reject path-like input before pathlib absolute short-circuit.
    if raw.startswith(("/", "\\")) or "://" in raw:
        return None
    if any(sep in raw for sep in ("/", "\\")):
        return None
    filename = raw if raw.endswith(".md") else f"{raw}.md"
    path = Path(filename)
    if path.is_absolute() or len(path.parts) != 1:
        return None
    if path.parts[0] in (".", "..", ""):
        return None
    return filename


def resolve_prompt_path(
    name: str,
    paths: ElyraPaths | None = None,
) -> Path | None:
    """Find ``name`` (with or without .md) under home then project prompts/.

    Only a single filename is allowed (no directories, no ``..``). Result is
    confined to the matched prompts base via ``is_relative_to``.
    """
    filename = _normalize_prompt_filename(name)
    if filename is None:
        return None

    bases: list[Path] = []
    if paths is not None:
        bases.append(paths.prompts_dir)
    else:
        bases.append(resolve_paths().prompts_dir)
    project_prompts = project_root() / "prompts"
    if project_prompts not in bases:
        bases.append(project_prompts)

    for base in bases:
        base_resolved = base.resolve()
        candidate = (base / filename).resolve()
        try:
            if not candidate.is_relative_to(base_resolved):
                continue
        except (OSError, ValueError):
            continue
        if candidate.is_file():
            return candidate
    return None


def load_prompt(
    name: str,
    paths: ElyraPaths | None = None,
) -> str:
    """Return prompt text for ``name`` (e.g. ``system``, ``orient.md``).

    Raises ``FileNotFoundError`` if no matching file is found (including when
    ``name`` fails path-jail validation).
    """
    path = resolve_prompt_path(name, paths=paths)
    if path is None:
        raise FileNotFoundError(f"prompt not found: {name}")
    return path.read_text(encoding="utf-8")
