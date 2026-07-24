"""Process-wide sandbox lifecycle registry.

Scope: set/get/clear for SandboxLifecycleManager (supervisor + runners).
In scope: thread-safe process singleton (analogous to runtime state).
Out of scope: ensure state machine (lifecycle.py).
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from elyra.sandbox.lifecycle import SandboxLifecycleManager

_lock = threading.Lock()
_lifecycle: SandboxLifecycleManager | None = None


def set_sandbox_lifecycle(manager: SandboxLifecycleManager | None) -> None:
    """Register (or clear with None) the process-wide lifecycle manager."""
    global _lifecycle
    with _lock:
        _lifecycle = manager


def get_sandbox_lifecycle() -> SandboxLifecycleManager | None:
    """Return the registered lifecycle manager, or None if unset."""
    with _lock:
        return _lifecycle


def clear_sandbox_lifecycle() -> None:
    """Clear the process-wide registry (tests / shutdown)."""
    set_sandbox_lifecycle(None)
