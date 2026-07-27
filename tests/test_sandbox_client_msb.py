"""Optional microsandbox client — lazy import; no real MSB required."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from elyra.sandbox.client_msb import (
    MicrosandboxClient,
    microsandbox_available,
    try_create_real_client,
)
from elyra.sandbox.errors import SandboxClientUnusableError
from elyra.sandbox.paths import MOUNT_SPEC, guest_env


def test_microsandbox_available_false_when_missing() -> None:
    # Hermetic CI / default install: package not present.
    # If present in the environment, still must not raise.
    result = microsandbox_available()
    assert isinstance(result, bool)
    if "microsandbox" not in sys.modules and result is False:
        assert try_create_real_client() is None


def test_try_create_real_client_none_without_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "elyra.sandbox.client_msb.microsandbox_available",
        lambda: False,
    )
    assert try_create_real_client() is None


def test_try_create_real_client_handles_ctor_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "elyra.sandbox.client_msb.microsandbox_available",
        lambda: True,
    )

    class Boom:
        def __init__(self) -> None:
            raise RuntimeError("sdk init failed")

    monkeypatch.setattr("elyra.sandbox.client_msb.MicrosandboxClient", Boom)
    assert try_create_real_client() is None


def test_microsandbox_client_ctor_raises_without_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Force ImportError path inside MicrosandboxClient.__init__.
    import builtins

    real_import = builtins.__import__

    def _block_msb(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "microsandbox" or name.startswith("microsandbox."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_msb)
    with pytest.raises(SandboxClientUnusableError):
        MicrosandboxClient()


def test_build_create_kwargs_volume_map(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """build_create_kwargs uses RO seed + RW tmp/tools without importing real SDK.

    Inject a stub microsandbox module so MicrosandboxClient can construct.
    """
    stub = ModuleType("microsandbox")

    class _Volume:
        @staticmethod
        def bind(host: str, readonly: bool = False) -> dict[str, Any]:
            return {"host": host, "readonly": readonly}

    class _Network:
        @staticmethod
        def none() -> str:
            return "none"

        @staticmethod
        def public_only() -> str:
            return "public_only"

        @staticmethod
        def allow_all() -> str:
            return "allow_all"

    class _Sandbox:
        pass

    stub.Volume = _Volume  # type: ignore[attr-defined]
    stub.Network = _Network  # type: ignore[attr-defined]
    stub.Sandbox = _Sandbox  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "microsandbox", stub)

    client = MicrosandboxClient()
    root = tmp_path / "sandbox0"
    for _guest, host_rel, _ro in MOUNT_SPEC:
        (root / host_rel).mkdir(parents=True, exist_ok=True)

    kwargs = client.build_create_kwargs(
        str(root),
        env=guest_env(),
    )
    assert kwargs["image"] == "python"
    assert kwargs["cpus"] == 1
    assert kwargs["memory"] == 512
    assert kwargs["workdir"] == "/workspace"
    assert kwargs["network"] == "public_only"
    volumes = kwargs["volumes"]
    # KD17: every MOUNT_SPEC guest path must appear with matching readonly.
    assert len(volumes) == len(MOUNT_SPEC)
    for guest, host_rel, readonly in MOUNT_SPEC:
        assert guest in volumes, f"missing volume for {guest}"
        assert volumes[guest]["readonly"] is readonly
        assert volumes[guest]["host"] == str(root / host_rel)
    assert volumes["/workspace/media"]["readonly"] is True
    assert volumes["/workspace/tmp"]["readonly"] is False
    assert volumes["/workspace/tools"]["readonly"] is False
