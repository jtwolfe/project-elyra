"""Sandbox path jail: resolve user paths under a fixed root.

Scope: join + resolve under root; deny escapes and symlink escapes.
In scope: relative/absolute user paths, symlink target re-check.
Out of scope: FS I/O, process execution.
"""

from __future__ import annotations

from pathlib import Path


class PathEscapeError(ValueError):
    """Raised when a user path or symlink target escapes the sandbox root."""


def resolve(root: Path, user_path: str) -> Path:
    """Resolve ``user_path`` under ``root``; raise PathEscapeError if outside jail.

    Algorithm (persistent sandbox jail):
    - Join and resolve; deny if not under root.
    - Reject absolute paths that escape.
    - If the path is a symlink, re-check the resolved target under root.
    """
    root_r = root.resolve()
    raw = Path(user_path)
    if raw.is_absolute():
        candidate = raw.resolve()
    else:
        candidate = (root_r / user_path).resolve()

    try:
        candidate.relative_to(root_r)
    except ValueError as exc:
        raise PathEscapeError(f"path escapes sandbox: {user_path!r}") from exc

    # Symlink re-check: after resolve the candidate may already be the target;
    # also inspect the pre-resolve path when it is a symlink (e.g. dangling).
    link = _symlink_path(root_r, user_path, candidate)
    if link is not None:
        target = link.resolve()
        try:
            target.relative_to(root_r)
        except ValueError as exc:
            raise PathEscapeError(
                f"symlink escapes sandbox: {user_path!r}"
            ) from exc

    return candidate


def _symlink_path(root_r: Path, user_path: str, candidate: Path) -> Path | None:
    """Return a path that is a symlink if one should be re-checked, else None."""
    if candidate.is_symlink():
        return candidate
    raw = Path(user_path)
    if raw.is_absolute():
        joined = raw
    else:
        joined = root_r / user_path
    # Avoid following: is_symlink is False for missing paths.
    try:
        if joined.is_symlink():
            return joined
    except OSError:
        return None
    return None
