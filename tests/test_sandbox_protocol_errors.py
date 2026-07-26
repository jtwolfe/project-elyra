"""Protocol types and error hierarchy smoke tests."""

from __future__ import annotations

from elyra.sandbox.errors import (
    BridgeReentrancyError,
    BridgeShutdownError,
    BridgeTimeoutError,
    EnsureLockTimeoutError,
    SandboxClientUnusableError,
    SandboxError,
    SandboxNotFoundError,
)
from elyra.sandbox.protocol import ExecResult, SandboxInstanceStatus


def test_exec_result_defaults() -> None:
    r = ExecResult(exit_code=0)
    assert r.stdout_text == ""
    assert r.stderr_text == ""
    assert r.exit_code == 0


def test_instance_status_values() -> None:
    assert SandboxInstanceStatus.RUNNING == "running"
    assert SandboxInstanceStatus.STOPPED == "stopped"
    assert SandboxInstanceStatus.CRASHED == "crashed"
    assert SandboxInstanceStatus.DRAINING == "draining"


def test_error_hierarchy() -> None:
    assert issubclass(SandboxNotFoundError, SandboxError)
    assert issubclass(BridgeReentrancyError, SandboxError)
    assert issubclass(BridgeTimeoutError, SandboxError)
    assert issubclass(BridgeShutdownError, SandboxError)
    assert issubclass(SandboxClientUnusableError, SandboxError)
    assert issubclass(EnsureLockTimeoutError, SandboxError)
