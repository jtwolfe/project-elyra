"""Builtin ``run`` tool — execute argv in the sandbox.

KD24: guest exec when isolation is on; host ``Sandbox.run`` only when
``ELYRA_SANDBOX=0``. No silent host fallback when isolation is on.

Scope: ToolResult wrapper over guest lifecycle exec or host Sandbox.run.
In scope: command as string or argv list, optional timeout, error_reason mapping.
Out of scope: network isolation policy (create-time), cgroups.
"""

from __future__ import annotations

import shlex
from typing import Any, Sequence

from elyra.sandbox import DEFAULT_RUN_TIMEOUT_SECONDS, Sandbox
from elyra.sandbox.paths import isolation_enabled
from elyra.tools.types import ToolContext, ToolResult

# Guest command size cap (elyra2 guest_shell / design: 4 KiB UTF-8).
_GUEST_MAX_COMMAND_BYTES = 4 * 1024


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


def _to_argv(command: str | list[str]) -> list[str] | ToolResult:
    """Normalize string (shlex) or list into argv for exec."""
    if isinstance(command, list):
        if not command:
            return ToolResult(ok=False, payload={}, error_reason="empty_command")
        return list(command)
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"invalid_command:{exc}",
        )
    if not argv:
        return ToolResult(ok=False, payload={}, error_reason="empty_command")
    return argv


def _command_byte_len(command: str | list[str]) -> int:
    if isinstance(command, str):
        return len(command.encode("utf-8"))
    return len(" ".join(command).encode("utf-8"))


def _guest_run(
    command: str | list[str],
    *,
    timeout: float,
) -> ToolResult:
    """Run command via warm lifecycle guest exec (fail closed — KD24)."""
    from elyra.tools.guest_exec import (
        EXECUTOR_BACKEND_MICROSANDBOX,
        guest_run_argv,
    )

    if _command_byte_len(command) > _GUEST_MAX_COMMAND_BYTES:
        return ToolResult(
            ok=False,
            payload={
                "executor_backend": EXECUTOR_BACKEND_MICROSANDBOX,
                "limit_bytes": _GUEST_MAX_COMMAND_BYTES,
                "hint": (
                    "Shell/run payload exceeds guest max; write files via "
                    "search_replace / FS tools instead of large shell heredocs."
                ),
            },
            error_reason="command_too_large",
        )

    argv = _to_argv(command)
    if isinstance(argv, ToolResult):
        return argv
    return guest_run_argv(argv, timeout=float(timeout))


def _host_run(
    command: str | list[str],
    *,
    timeout: float,
    sandbox: Sandbox,
) -> ToolResult:
    """Host process-level run when ``ELYRA_SANDBOX=0`` only."""
    from elyra.tools.guest_exec import EXECUTOR_BACKEND_HOST_STUB

    try:
        result = sandbox.run(command, timeout=float(timeout))
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
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "argv": list(result.argv),
            "executor_backend": EXECUTOR_BACKEND_HOST_STUB,
        },
    )


def run(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Run a command inside the sandbox.

    Isolation **on**: guest-only via warm lifecycle (KD24). Fail closed when
    lifecycle missing/unusable — never silent host fallback.

    Isolation **off** (``ELYRA_SANDBOX=0``): host ``Sandbox.run`` (cwd=sandbox
    root, shell=False, scrubbed env) for hermetic tests/CI.

    Args:
        command (required): argv list (preferred) or string (shlex-split).
        timeout (optional number, default 60): seconds before kill/timeout.

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

    if isolation_enabled():
        return _guest_run(command, timeout=float(timeout))

    sb = _require_sandbox(ctx)
    if isinstance(sb, ToolResult):
        return sb
    return _host_run(command, timeout=float(timeout), sandbox=sb)
