"""Builtin sandbox FS tools: read_file, list_dir, grep, search_replace.

Scope: thin ToolResult wrappers over Sandbox path-jailed FS ops.
In scope: arg checks, PathEscapeError / OSError → error_reason, payload shape.
Out of scope: run (see run_cmd.py), host FS outside sandbox, shell.
"""

from __future__ import annotations

import re
from typing import Any

from elyra.sandbox import PathEscapeError, Sandbox
from elyra.tools.types import ToolContext, ToolResult


def _require_sandbox(ctx: ToolContext) -> Sandbox | ToolResult:
    """Return sandbox or a failed ToolResult when ctx.sandbox is unset."""
    if ctx.sandbox is None:
        return ToolResult(ok=False, payload={}, error_reason="no_sandbox")
    return ctx.sandbox


def _str_arg(args: dict[str, Any], key: str) -> str | None:
    """Return stripped string arg or None if missing/non-str/blank."""
    raw = args.get(key)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped if stripped else None


def read_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Read a UTF-8 text file under the sandbox workspace.

    Args: ``path`` (required) — path relative to sandbox root (or absolute under root).
    """
    path = _str_arg(args, "path")
    if path is None:
        return ToolResult(ok=False, payload={}, error_reason="missing_path")

    sb = _require_sandbox(ctx)
    if isinstance(sb, ToolResult):
        return sb

    try:
        content = sb.read_text(path)
    except PathEscapeError:
        return ToolResult(ok=False, payload={}, error_reason="path_escape")
    except FileNotFoundError:
        return ToolResult(ok=False, payload={}, error_reason="not_found")
    except IsADirectoryError:
        return ToolResult(ok=False, payload={}, error_reason="is_directory")
    except ValueError:
        return ToolResult(ok=False, payload={}, error_reason="invalid_path")
    except OSError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"os_error:{type(exc).__name__}",
        )
    return ToolResult(ok=True, payload={"path": path, "content": content})


def list_dir(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """List directory entry names under the sandbox (not recursive).

    Args: ``path`` (optional, default ``"."``) — directory under sandbox root.
    """
    raw = args.get("path", ".")
    if raw is None:
        path = "."
    elif not isinstance(raw, str):
        return ToolResult(ok=False, payload={}, error_reason="invalid_path")
    else:
        path = raw.strip() or "."

    sb = _require_sandbox(ctx)
    if isinstance(sb, ToolResult):
        return sb

    try:
        entries = sb.list_dir(path)
    except PathEscapeError:
        return ToolResult(ok=False, payload={}, error_reason="path_escape")
    except FileNotFoundError:
        return ToolResult(ok=False, payload={}, error_reason="not_found")
    except NotADirectoryError:
        return ToolResult(ok=False, payload={}, error_reason="not_a_directory")
    except ValueError:
        return ToolResult(ok=False, payload={}, error_reason="invalid_path")
    except OSError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"os_error:{type(exc).__name__}",
        )
    return ToolResult(ok=True, payload={"path": path, "entries": entries})


def grep(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Search file contents under a sandbox path (files only; recursive).

    Args:
        pattern (required): substring or regex.
        path (optional, default ``"."``): file or directory under sandbox.
        regex (optional bool, default false): treat pattern as regex.
        max_matches (optional int, default 200): cap on returned hits.
    """
    pattern = args.get("pattern")
    if not isinstance(pattern, str) or not pattern:
        return ToolResult(ok=False, payload={}, error_reason="missing_pattern")

    raw_path = args.get("path", ".")
    if raw_path is None:
        path = "."
    elif not isinstance(raw_path, str):
        return ToolResult(ok=False, payload={}, error_reason="invalid_path")
    else:
        path = raw_path.strip() or "."

    regex = bool(args.get("regex", False))

    max_matches = args.get("max_matches", 200)
    if max_matches is None:
        max_matches = 200
    if not isinstance(max_matches, int) or isinstance(max_matches, bool):
        return ToolResult(ok=False, payload={}, error_reason="invalid_max_matches")
    if max_matches < 1:
        return ToolResult(ok=False, payload={}, error_reason="invalid_max_matches")

    sb = _require_sandbox(ctx)
    if isinstance(sb, ToolResult):
        return sb

    try:
        matches = sb.grep(
            pattern,
            path,
            regex=regex,
            max_matches=max_matches,
        )
    except PathEscapeError:
        return ToolResult(ok=False, payload={}, error_reason="path_escape")
    except FileNotFoundError:
        return ToolResult(ok=False, payload={}, error_reason="not_found")
    except re.error as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"invalid_regex:{exc}",
        )
    except ValueError:
        return ToolResult(ok=False, payload={}, error_reason="invalid_path")
    except OSError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"os_error:{type(exc).__name__}",
        )

    truncated = len(matches) >= max_matches
    return ToolResult(
        ok=True,
        payload={
            "pattern": pattern,
            "path": path,
            "matches": matches,
            "truncated": truncated,
        },
    )


def search_replace(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Replace text in a sandbox file (atomic write via sandbox).

    Args:
        path (required): file under sandbox.
        old (required, non-empty): substring to replace.
        new (required, may be empty): replacement text.
        count (optional int, default 0): max replacements; 0 = all.
    """
    path = _str_arg(args, "path")
    if path is None:
        return ToolResult(ok=False, payload={}, error_reason="missing_path")

    old = args.get("old")
    if not isinstance(old, str):
        return ToolResult(ok=False, payload={}, error_reason="missing_old")
    if not old:
        return ToolResult(ok=False, payload={}, error_reason="empty_old")

    new = args.get("new")
    if not isinstance(new, str):
        return ToolResult(ok=False, payload={}, error_reason="missing_new")

    count = args.get("count", 0)
    if count is None:
        count = 0
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return ToolResult(ok=False, payload={}, error_reason="invalid_count")

    sb = _require_sandbox(ctx)
    if isinstance(sb, ToolResult):
        return sb

    try:
        n = sb.search_replace(path, old, new, count=count)
    except PathEscapeError:
        return ToolResult(ok=False, payload={}, error_reason="path_escape")
    except FileNotFoundError:
        return ToolResult(ok=False, payload={}, error_reason="not_found")
    except IsADirectoryError:
        return ToolResult(ok=False, payload={}, error_reason="is_directory")
    except ValueError as exc:
        msg = str(exc).lower()
        if "non-empty" in msg or "old" in msg:
            return ToolResult(ok=False, payload={}, error_reason="empty_old")
        return ToolResult(ok=False, payload={}, error_reason="invalid_path")
    except OSError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"os_error:{type(exc).__name__}",
        )
    return ToolResult(
        ok=True,
        payload={"path": path, "replacements": n},
    )
