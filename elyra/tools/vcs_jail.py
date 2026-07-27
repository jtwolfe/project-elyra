"""Git/gh path jail — allowed_repo_roots resolve + escape refusal.

Scope: effective_allowed_roots (empty sentinel → project_root + home),
resolve_repo_path (expanduser, resolve, relative_to any root, symlink check).
Out of scope: subprocess, tool handlers.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from elyra.config import ElyraPaths
    from elyra.settings import Settings


class PathJailError(ValueError):
    """Raised when a path escapes allowed_repo_roots or fails jail checks."""

    def __init__(self, reason: str = "path_jail", message: str = "") -> None:
        self.reason = reason
        super().__init__(message or reason)


def effective_allowed_roots(
    settings: Settings | None,
    paths: ElyraPaths,
) -> list[Path]:
    """Return resolved allowed roots for VCS tools.

    Empty ``settings.tools.allowed_repo_roots`` is a **use-site** sentinel:
    ``[project_root(), paths.home]`` only (not expanded at settings load).
    """
    configured: tuple[str, ...] = ()
    if settings is not None:
        tools = getattr(settings, "tools", None)
        if tools is not None:
            raw = getattr(tools, "allowed_repo_roots", ()) or ()
            if isinstance(raw, (list, tuple)):
                configured = tuple(str(x) for x in raw)

    if not configured:
        from elyra.config import project_root

        return [
            project_root().resolve(),
            Path(paths.home).expanduser().resolve(),
        ]

    out: list[Path] = []
    for item in configured:
        try:
            out.append(Path(item).expanduser().resolve())
        except OSError as exc:
            raise PathJailError(
                "path_jail",
                f"cannot resolve allowed root {item!r}: {exc}",
            ) from exc
    return out


def _under_any_root(candidate: Path, roots: Sequence[Path]) -> Path | None:
    """Return the matching allowed root if candidate is under it, else None."""
    for root in roots:
        root_r = root if root.is_absolute() else root.resolve()
        try:
            root_r = root_r.resolve()
        except OSError:
            continue
        if candidate == root_r or candidate.is_relative_to(root_r):
            return root_r
    return None


def _normalize_roots(allowed_roots: Sequence[Path | str]) -> list[Path]:
    roots_resolved: list[Path] = []
    for r in allowed_roots:
        try:
            roots_resolved.append(Path(r).expanduser().resolve())
        except OSError:
            continue
    return roots_resolved


def resolve_repo_path(
    raw: str,
    allowed_roots: Sequence[Path | str],
    *,
    require_git: bool = True,
    base: Path | None = None,
) -> Path:
    """Resolve ``raw`` under the VCS path jail.

    - expanduser + resolve (relative paths join ``base`` when provided,
      otherwise first allowed root)
    - refuse paths outside any allowed root
    - refuse symlink targets that escape allowed roots
    - optionally require a ``.git`` file or directory (repo or worktree)

    Raises ``PathJailError`` with ``reason`` of ``path_jail``, ``not_a_repo``,
    or ``invalid_path``.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise PathJailError("invalid_path", "repo path must be a non-empty string")

    text = raw.strip()
    roots_resolved = _normalize_roots(allowed_roots)
    if not roots_resolved:
        raise PathJailError("path_jail", "no allowed_repo_roots configured")

    try:
        p = Path(text).expanduser()
        if p.is_absolute():
            candidate = p.resolve()
        else:
            join_base = base if base is not None else roots_resolved[0]
            candidate = (Path(join_base).expanduser().resolve() / p).resolve()
    except OSError as exc:
        raise PathJailError("path_jail", f"cannot resolve path: {exc}") from exc

    if _under_any_root(candidate, roots_resolved) is None:
        raise PathJailError(
            "path_jail",
            f"path outside allowed_repo_roots: {text!r}",
        )

    # Symlink escape: if the pre-resolve path is a symlink, re-check target.
    try:
        pre = Path(text).expanduser()
        if not pre.is_absolute() and base is not None:
            pre = Path(base).expanduser() / Path(text)
        elif not pre.is_absolute():
            pre = roots_resolved[0] / Path(text)
        if pre.is_symlink():
            target = pre.resolve()
            if _under_any_root(target, roots_resolved) is None:
                raise PathJailError(
                    "path_jail",
                    f"symlink escapes allowed_repo_roots: {text!r}",
                )
    except PathJailError:
        raise
    except OSError as exc:
        raise PathJailError("path_jail", f"symlink check failed: {exc}") from exc

    if require_git:
        git_marker = candidate / ".git"
        if not git_marker.exists():
            raise PathJailError(
                "not_a_repo",
                f"not a git repository (missing .git): {text!r}",
            )

    return candidate


def path_in_jail(path: Path | str, allowed_roots: Sequence[Path | str]) -> bool:
    """True if resolved ``path`` lies under any allowed root."""
    try:
        candidate = Path(path).expanduser().resolve()
    except OSError:
        return False
    roots = _normalize_roots(allowed_roots)
    return _under_any_root(candidate, roots) is not None


__all__ = [
    "PathJailError",
    "effective_allowed_roots",
    "path_in_jail",
    "resolve_repo_path",
]
