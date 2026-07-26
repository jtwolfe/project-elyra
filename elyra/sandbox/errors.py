"""Sandbox package error types.

Scope: structured errors for bridge, client, and lifecycle internals.
In scope: exception hierarchy used by elyra.sandbox.*.
Out of scope: ToolResult / anomaly mapping (later PRs).
"""

from __future__ import annotations


class SandboxError(Exception):
    """Base for sandbox lifecycle / bridge failures."""


class SandboxNotFoundError(SandboxError):
    """Named sandbox does not exist (client get miss)."""


class BridgeReentrancyError(SandboxError):
    """bridge.run called from a coroutine on the bridge loop."""


class BridgeTimeoutError(SandboxError):
    """bridge.run timed out waiting for a coroutine."""


class BridgeShutdownError(SandboxError):
    """bridge.run called after shutdown (or during closed state)."""


class SandboxClientUnusableError(SandboxError):
    """Real SDK client permanently unavailable (import / runtime missing)."""


class EnsureLockTimeoutError(SandboxError):
    """Timed out acquiring per-instance ensure/invoke lock."""
