"""Sandbox package: path jail (product FS) + warm-MSB surfaces (H2a+).

Public API:
- Path jail resolve + product ``Sandbox`` under ``data/sandbox/`` (unchanged).
- H2a: protocols, errors, async bridge, fake client, health, registry, host seed.

Trust boundary: FS methods are path-jailed; ``run`` is process-level only
(cwd + scrubbed env + shell=False) — not a container. Lifecycle/MSB client
land in PR2; product FS root cutover lands in PR3.
"""

from __future__ import annotations

from elyra.sandbox.async_bridge import AsyncBridge
from elyra.sandbox.errors import (
    BridgeReentrancyError,
    BridgeShutdownError,
    BridgeTimeoutError,
    EnsureLockTimeoutError,
    SandboxClientUnusableError,
    SandboxError,
    SandboxNotFoundError,
)
from elyra.sandbox.fake import FakeConnectedSandbox, FakeSandboxClient, FakeSandboxHandle
from elyra.sandbox.paths import (
    GUEST_WORKSPACE_ROOT,
    PRIMARY_NAME,
    PathEscapeError,
    ensure_host_tree,
    resolve,
)
from elyra.sandbox.protocol import (
    ConnectedSandbox,
    ExecResult,
    SandboxClient,
    SandboxHandle,
    SandboxInstanceStatus,
)
from elyra.sandbox.registry import (
    clear_sandbox_lifecycle,
    get_sandbox_lifecycle,
    set_sandbox_lifecycle,
)
from elyra.sandbox.sandbox import (
    DEFAULT_RUN_TIMEOUT_SECONDS,
    OUTPUT_CAP_BYTES,
    RunResult,
    Sandbox,
)
from elyra.sandbox.workspace_seed import host_primary_root

__all__ = [
    "AsyncBridge",
    "BridgeReentrancyError",
    "BridgeShutdownError",
    "BridgeTimeoutError",
    "ConnectedSandbox",
    "DEFAULT_RUN_TIMEOUT_SECONDS",
    "EnsureLockTimeoutError",
    "ExecResult",
    "FakeConnectedSandbox",
    "FakeSandboxClient",
    "FakeSandboxHandle",
    "GUEST_WORKSPACE_ROOT",
    "OUTPUT_CAP_BYTES",
    "PRIMARY_NAME",
    "PathEscapeError",
    "RunResult",
    "Sandbox",
    "SandboxClient",
    "SandboxClientUnusableError",
    "SandboxError",
    "SandboxHandle",
    "SandboxInstanceStatus",
    "SandboxNotFoundError",
    "clear_sandbox_lifecycle",
    "ensure_host_tree",
    "get_sandbox_lifecycle",
    "host_primary_root",
    "resolve",
    "set_sandbox_lifecycle",
]
