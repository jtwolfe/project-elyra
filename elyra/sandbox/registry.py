"""Process-wide sandbox lifecycle registry.

Scope: set/get/clear for SandboxLifecycleManager used by later PRs.
In scope: thread-safe process singleton (analogous to runtime state).
Out of scope: ensure state machine, supervisor wiring (PR2/PR3).

Placeholder: manager type is ``object`` until lifecycle lands in PR2.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_lifecycle: object | None = None


def set_sandbox_lifecycle(manager: object | None) -> None:
    """Register (or clear with None) the process-wide lifecycle manager."""
    global _lifecycle
    with _lock:
        _lifecycle = manager


def get_sandbox_lifecycle() -> object | None:
    """Return the registered lifecycle manager, or None if unset."""
    with _lock:
        return _lifecycle


def clear_sandbox_lifecycle() -> None:
    """Clear the process-wide registry (tests / shutdown)."""
    set_sandbox_lifecycle(None)
