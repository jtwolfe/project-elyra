"""Process registry for sandbox lifecycle (placeholder until PR2)."""

from __future__ import annotations

from elyra.sandbox import (
    FakeSandboxClient,
    clear_sandbox_lifecycle,
    get_sandbox_lifecycle,
    set_sandbox_lifecycle,
)


def test_registry_default_none() -> None:
    clear_sandbox_lifecycle()
    assert get_sandbox_lifecycle() is None


def test_registry_set_get_clear() -> None:
    clear_sandbox_lifecycle()
    # Placeholder: any object is acceptable until SandboxLifecycleManager lands.
    marker = FakeSandboxClient()
    set_sandbox_lifecycle(marker)
    assert get_sandbox_lifecycle() is marker
    clear_sandbox_lifecycle()
    assert get_sandbox_lifecycle() is None


def test_registry_set_none_clears() -> None:
    clear_sandbox_lifecycle()
    marker = object()
    set_sandbox_lifecycle(marker)
    set_sandbox_lifecycle(None)
    assert get_sandbox_lifecycle() is None
