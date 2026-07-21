"""Builtin ``run`` tool — execute argv in the sandbox (shell=False).

Scope: ToolResult wrapper over Sandbox.run.
In scope: command as string or argv list, optional timeout, error_reason mapping.
Out of scope: host shell, network isolation, cgroups (sandbox trust boundary).
"""

from __future__ import annotations

from typing import Any, Sequence

from elyra.sandbox import DEFAULT_RUN_TIMEOUT_SECONDS, Sandbox
from elyra.tools.types import ToolContext, ToolResult


def _require_sandbox(ctx: ToolContext) -> Sandbox | ToolResult:
    if ctx.sandbox is None:
        return ToolResult(ok=False, payload={}, error_reason="no_sandbox")
    return ctx.sandbox


def _normalize_command(
    raw: Any,
) -> str | list[str] | ToolResult:
    """Accept string or list of strings; reject other shapes."""
    if isinstance(raw, str):
        if not raw.strip():
            return ToolResult(ok=False, payload={}, error_reason="empty_command")
        return raw
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        if len(raw) == 0:
            return ToolResult(ok=False, payload={}, error_reason="empty_command")
        argv: list[str] = []
        for item in raw:
            if not isinstance(item, str):
                return ToolResult(
                    ok=False,
                    payload={},
                    error_reason="invalid_command",
                )
            argv.append(item)
        return argv
    return ToolResult(ok=False, payload={}, error_reason="invalid_command")


def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Run a command inside the sandbox (cwd=sandbox root, shell=False).

    Args:
        command (required): argv list (preferred) or string (shlex-split).
        timeout (optional number, default 60): seconds before process-group kill.

    Success (``ok=True``) when the process was launched and finished (or timed
    out). Non-zero exit and timeout are reported in the payload so the model
    can read them — they are not tool infrastructure failures.
    """
    if "command" not in args:
        return ToolResult(ok=False, payload={}, error_reason="missing_command")

    command = _normalize_command(args["command"])
    if isinstance(command, ToolResult):
        return command

    timeout = args.get("timeout", DEFAULT_RUN_TIMEOUT_SECONDS)
    if timeout is None:
        timeout = DEFAULT_RUN_TIMEOUT_SECONDS
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return ToolResult(ok=False, payload={}, error_reason="invalid_timeout")
    if timeout <= 0:
        return ToolResult(ok=False, payload={}, error_reason="invalid_timeout")

    sb = _require_sandbox(ctx)
    if isinstance(sb, ToolResult):
        return sb

    try:
        result = sb.run(command, timeout=float(timeout))
    except ValueError as exc:
        msg = str(exc).lower()
        if "empty" in msg:
            return ToolResult(ok=False, payload={}, error_reason="empty_command")
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"invalid_command:{exc}",
        )
    except OSError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"os_error:{type(exc).__name__}",
        )
    except Exception as exc:  # noqa: BLE001 — surface cleanly to model
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"run_error:{type(exc).__name__}",
        )

    return ToolResult(
        ok=True,
        payload={
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "argv": list(result.argv),
        },
    )
