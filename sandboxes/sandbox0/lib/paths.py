"""Shared path helpers for sandbox tools.

Works in microsandbox guest (`/workspace`) and local host when tools resolve
root as parent of `general/` (or when callers pass an explicit root).
"""

from __future__ import annotations

from pathlib import Path

# Default mount root inside isolation guest (workspace-isolation DESIGN).
ELYRA_ROOT = Path("/workspace")
TMP_DIR = ELYRA_ROOT / "tmp"


def safe_under_workspace(
    path: str | Path,
    *,
    root: Path | None = None,
) -> Path:
    """Resolve path and ensure it stays under the Elyra workspace root.

    Args:
        path: Absolute path under root, or relative to root.
        root: Workspace root. Defaults to guest mount `/workspace`.
              For local seed tools, pass parent of `general/`.
    """
    base = (root or ELYRA_ROOT).resolve()
    raw = Path(path)
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        resolved = (base / raw).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {path}") from exc
    return resolved
