"""Process registry for SandboxLifecycleManager."""

from __future__ import annotations

from pathlib import Path

from elyra.config import ElyraPaths
from elyra.sandbox import (
    FakeSandboxClient,
    SandboxLifecycleManager,
    clear_sandbox_lifecycle,
    get_sandbox_lifecycle,
    set_sandbox_lifecycle,
)


def _layout(tmp_path: Path) -> ElyraPaths:
    return ElyraPaths(
        home=tmp_path,
        model_dir=tmp_path / "model",
        data_dir=tmp_path / "data",
        skills_dir=tmp_path / "skills",
        tools_dir=tmp_path / "tools",
        prompts_dir=tmp_path / "prompts",
    )


def test_registry_default_none() -> None:
    clear_sandbox_lifecycle()
    assert get_sandbox_lifecycle() is None


def test_registry_set_get_clear(tmp_path: Path) -> None:
    clear_sandbox_lifecycle()
    client = FakeSandboxClient()
    mgr = SandboxLifecycleManager(
        paths=_layout(tmp_path),
        client=client,
        client_unusable=False,
        skip_guest_readiness=True,
    )
    try:
        set_sandbox_lifecycle(mgr)
        assert get_sandbox_lifecycle() is mgr
        assert isinstance(get_sandbox_lifecycle(), SandboxLifecycleManager)
        clear_sandbox_lifecycle()
        assert get_sandbox_lifecycle() is None
    finally:
        clear_sandbox_lifecycle()
        mgr.shutdown()


def test_registry_set_none_clears(tmp_path: Path) -> None:
    clear_sandbox_lifecycle()
    mgr = SandboxLifecycleManager(
        paths=_layout(tmp_path),
        client=FakeSandboxClient(),
        skip_guest_readiness=True,
    )
    try:
        set_sandbox_lifecycle(mgr)
        set_sandbox_lifecycle(None)
        assert get_sandbox_lifecycle() is None
    finally:
        mgr.shutdown()
