"""Health / mount readiness unit tests (Fake only — no real MSB)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from elyra.sandbox.fake import FakeConnectedSandbox, FakeSandboxClient
from elyra.sandbox.health import (
    full_readiness,
    guest_mount_ready,
    host_mount_ready,
    host_seed_readable,
    host_tmp_tools_writable,
    ping_ok,
)
from elyra.sandbox.paths import GUEST_WORKSPACE_ROOT
from elyra.sandbox.protocol import ExecResult


def test_host_mount_ready_requires_seed(tmp_path: Path) -> None:
    root = tmp_path / "sandbox0"
    (root / "lib").mkdir(parents=True)
    (root / "tmp").mkdir()
    (root / "tools").mkdir()
    assert host_seed_readable(root) is False
    ok, reason = host_mount_ready(root)
    assert ok is False
    assert reason == "host_seed_not_readable"

    (root / "general").mkdir()
    (root / "general" / "now.py").write_text("x=1\n", encoding="utf-8")
    ok, reason = host_mount_ready(root)
    assert ok is True
    assert reason is None


def test_host_tmp_tools_not_writable_when_missing(tmp_path: Path) -> None:
    root = tmp_path / "sandbox0"
    (root / "lib").mkdir(parents=True)
    (root / "general").mkdir()
    (root / "general" / "now.py").write_text("x=1\n", encoding="utf-8")
    # tmp/tools missing
    assert host_tmp_tools_writable(root) is False
    ok, reason = host_mount_ready(root)
    assert ok is False
    assert reason == "host_tmp_tools_not_writable"


def test_guest_mount_ready_uses_guest_root_constant() -> None:
    """Probes must target GUEST_WORKSPACE_ROOT paths."""
    client = FakeSandboxClient()
    sb = FakeConnectedSandbox(name="s", client=client)
    ok, reason = asyncio.run(guest_mount_ready(sb))
    assert ok is True
    assert reason is None
    assert client.event_log.count("exec") >= 4
    # Last probe cwd should be guest workspace root.
    assert client.last_exec is not None
    assert client.last_exec["cwd"] == GUEST_WORKSPACE_ROOT


def test_guest_mount_ready_fail() -> None:
    client = FakeSandboxClient(fail_all_exec=True)
    sb = FakeConnectedSandbox(
        name="s",
        client=client,
        default_exec=ExecResult(exit_code=1, stderr_text="fail"),
    )
    ok, reason = asyncio.run(guest_mount_ready(sb))
    assert ok is False
    assert reason == "guest_python"


def test_ping_ok_and_failure() -> None:
    client = FakeSandboxClient()
    sb = FakeConnectedSandbox(name="s", client=client, ping_ok=True)
    assert asyncio.run(ping_ok(sb)) is True
    sb.ping_ok = False
    assert asyncio.run(ping_ok(sb)) is False


def test_full_readiness_host_then_guest(tmp_path: Path) -> None:
    root = tmp_path / "sandbox0"
    (root / "lib").mkdir(parents=True)
    (root / "general").mkdir()
    (root / "general" / "now.py").write_text("x=1\n", encoding="utf-8")
    (root / "tmp").mkdir()
    (root / "tools").mkdir()
    client = FakeSandboxClient()
    sb = FakeConnectedSandbox(name="s", client=client)
    ok, reason = asyncio.run(full_readiness(sb, root))
    assert ok is True
    assert reason is None
