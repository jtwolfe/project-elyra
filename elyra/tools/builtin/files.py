"""Builtin file tools — sample/test-double until PR7 real handlers.

Scope: read_file host entry for tools/bundled/read_file.
In scope: sandbox.read_text when ctx.sandbox is set; pure double otherwise.
Out of scope: list_dir/grep/search_replace/run (PR7).
"""

from __future__ import annotations

from typing import Any

from elyra.sandbox import PathEscapeError
from elyra.tools.types import ToolContext, ToolResult


def read_file(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Read a text file from the sandbox (or return a test-double payload).

    Real FS behaviour prefers ``ctx.sandbox`` when present. Without a sandbox,
    returns a deterministic test double so registry tests do not require full
    FS wiring. PR7 replaces this with the full sandbox tool group.
    """
    path = args.get("path")
    if not isinstance(path, str) or not path.strip():
        return ToolResult(
            ok=False,
            payload={},
            error_reason="missing_path",
        )
    path = path.strip()

    if ctx.sandbox is not None:
        try:
            content = ctx.sandbox.read_text(path)
        except PathEscapeError:
            return ToolResult(ok=False, payload={}, error_reason="path_escape")
        except FileNotFoundError:
            return ToolResult(ok=False, payload={}, error_reason="not_found")
        except IsADirectoryError:
            return ToolResult(ok=False, payload={}, error_reason="is_directory")
        except OSError as exc:
            return ToolResult(
                ok=False,
                payload={},
                error_reason=f"os_error:{type(exc).__name__}",
            )
        return ToolResult(
            ok=True,
            payload={"path": path, "content": content},
        )

    # Pure test double (no sandbox on context).
    return ToolResult(
        ok=True,
        payload={
            "path": path,
            "content": f"(test double read_file) {path}",
            "test_double": True,
        },
    )
