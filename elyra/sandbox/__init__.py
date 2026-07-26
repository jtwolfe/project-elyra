"""Sandbox package: path jail (product FS) + warm-MSB surfaces (H2a–H2c).

Public API:
- Path jail resolve + product ``Sandbox`` under ``sandboxes/sandbox0/`` (H2c).
- H2a: protocols, errors, async bridge, fake client, health, registry, host seed.
- H2b: lifecycle manager, optional MSB client, ``isolation_enabled``.
- H2c: supervisor wiring helpers, status block (no secrets), FS cutover.

Trust boundary: FS methods are path-jailed; ``run`` is process-level only
(cwd + scrubbed env + shell=False) — not a container. Guest exec via lifecycle
lands in later PRs.
"""

from __future__ import annotations

from elyra.sandbox.async_bridge import AsyncBridge
from elyra.sandbox.client_msb import (
    MicrosandboxClient,
    microsandbox_available,
    try_create_real_client,
)
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
from elyra.sandbox.lifecycle import EnsureResult, SandboxLifecycleManager
from elyra.sandbox.paths import (
    ENV_ELYRA_SANDBOX,
    GUEST_WORKSPACE_ROOT,
    PRIMARY_NAME,
    PathEscapeError,
    ensure_host_tree,
    isolation_enabled,
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
    normalize_user_path,
)
from elyra.sandbox.status import sandbox_status_block
from elyra.sandbox.workspace_seed import host_primary_root

__all__ = [
    "AsyncBridge",
    "BridgeReentrancyError",
    "BridgeShutdownError",
    "BridgeTimeoutError",
    "ConnectedSandbox",
    "DEFAULT_RUN_TIMEOUT_SECONDS",
    "ENV_ELYRA_SANDBOX",
    "EnsureLockTimeoutError",
    "EnsureResult",
    "ExecResult",
    "FakeConnectedSandbox",
    "FakeSandboxClient",
    "FakeSandboxHandle",
    "GUEST_WORKSPACE_ROOT",
    "MicrosandboxClient",
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
    "SandboxLifecycleManager",
    "SandboxNotFoundError",
    "clear_sandbox_lifecycle",
    "ensure_host_tree",
    "get_sandbox_lifecycle",
    "host_primary_root",
    "isolation_enabled",
    "microsandbox_available",
    "normalize_user_path",
    "resolve",
    "sandbox_status_block",
    "set_sandbox_lifecycle",
    "try_create_real_client",
]
