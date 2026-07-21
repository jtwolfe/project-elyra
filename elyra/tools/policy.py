"""Tool name policy and discovery-root resolution.

Scope: name normalization/validation, drafts isolation, bundled root resolve.
In scope: case-normalized keys, safe package names, BUNDLED_TOOLS_ROOT assert.
Out of scope: package loading, runner dispatch, promote/verify gates.
"""

from __future__ import annotations

import re
from pathlib import Path

from elyra.config import project_root

# Package / tool names: snake or kebab, start alnum/underscore.
_TOOL_NAME_RE = re.compile(r"^[a-z0-9_][a-z0-9_-]*$", re.IGNORECASE)

# Runner kinds allowed on disk packages (builtin only for bundled).
ALLOWED_RUNNER_KINDS = frozenset({"builtin", "sandbox_shell", "sandbox_python"})
# Model-created drafts may only use sandbox runners (enforced at promote later).
DRAFT_ALLOWED_RUNNER_KINDS = frozenset({"sandbox_shell", "sandbox_python"})
# Kinds that may set ends_moment / stop_reason from handler results.
CONTROL_TOOL_KINDS = frozenset({"control", "speak"})

# Directory segment names that must never be scanned as callable roots.
NON_CALLABLE_TOOL_SEGMENTS = frozenset({"drafts"})


class BundledToolsRootError(FileNotFoundError):
    """Raised when BUNDLED_TOOLS_ROOT cannot be resolved (non-editable install)."""


def normalize_tool_name(name: object) -> str:
    """Case-fold and strip for isolation keys (local vs bundled clash).

    Non-strings return ``""`` so callers can map to ``invalid_name`` without
    raising (execute public surface never raises on bad names).
    """
    if not isinstance(name, str):
        return ""
    return name.strip().casefold()


def is_valid_tool_name(name: object) -> bool:
    """True if ``name`` matches the allowed package name pattern."""
    if not isinstance(name, str):
        return False
    raw = name.strip()
    if not raw:
        return False
    return bool(_TOOL_NAME_RE.match(raw))


def assert_callable_root(root: Path, *, label: str) -> None:
    """Reject roots whose path ends with a non-callable segment (e.g. drafts)."""
    resolved = Path(root).resolve()
    if resolved.name.casefold() in NON_CALLABLE_TOOL_SEGMENTS:
        raise ValueError(
            f"{label} must not be a non-callable tools segment "
            f"(got {resolved!s}; drafts are never callable)"
        )


def is_under_drafts_tree(path: Path, *, tools_dir: Path | None = None) -> bool:
    """True if ``path`` resolves into a non-callable drafts tree.

    Covers:
      - ``$ELYRA_HOME/tools/drafts/...`` (via ``tools_dir``)
      - any resolved path containing a ``tools/drafts`` segment pair
        (catches symlinks from ``local/`` into drafts packages)

    Fail-closed on resolve errors (treat as drafts / non-callable).
    """
    try:
        resolved = Path(path).resolve()
    except OSError:
        return True

    if tools_dir is not None:
        try:
            drafts_root = (Path(tools_dir) / "drafts").resolve()
            if resolved == drafts_root or resolved.is_relative_to(drafts_root):
                return True
        except (OSError, ValueError):
            pass

    # tools/drafts segment pair anywhere on the resolved path (symlink targets).
    parts = [p.casefold() for p in resolved.parts]
    for i, part in enumerate(parts[:-1]):
        if part == "tools" and parts[i + 1] == "drafts":
            return True
    return False


def resolve_bundled_tools_root(override: Path | str | None = None) -> Path:
    """Locate shipped ``tools/bundled`` under the project/code tree.

    Resolution order:
      1. Explicit ``override`` (settings / test inject)
      2. ``project_root()/tools/bundled`` (editable / repo-root dogfood)

    Stretch 1 does not support non-editable wheel installs of bundled tools.
    Raises :class:`BundledToolsRootError` with a clear recovery message when
    the directory is missing.
    """
    if override is not None:
        root = Path(override).expanduser().resolve()
        if not root.is_dir():
            raise BundledToolsRootError(
                f"bundled_tools_root override is not a directory: {root}. "
                "Create tools/bundled under the project or pass a valid path."
            )
        assert_callable_root(root, label="bundled_tools_root")
        return root

    candidate = (project_root() / "tools" / "bundled").resolve()
    if candidate.is_dir():
        assert_callable_root(candidate, label="BUNDLED_TOOLS_ROOT")
        return candidate

    raise BundledToolsRootError(
        f"BUNDLED_TOOLS_ROOT not found at {candidate}. "
        "Stretch 1 requires an editable install or repo checkout with "
        "tools/bundled/, or set bundled_tools_root explicitly "
        "(elyra.toml / ToolRegistry(bundled_root=…)). "
        "Non-editable wheel packaging of bundled tools is out of scope for S1."
    )
