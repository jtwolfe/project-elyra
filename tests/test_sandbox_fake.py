"""FakeSandboxClient unit tests (no real MSB / KVM)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from elyra.sandbox.errors import SandboxNotFoundError
from elyra.sandbox.fake import FakeConnectedSandbox, FakeSandboxClient
from elyra.sandbox.paths import (
    GUEST_WORKSPACE_ROOT,
    MOUNT_SPEC,
    guest_env,
)
from elyra.sandbox.protocol import (
    ConnectedSandbox,
    ExecResult,
    SandboxClient,
    SandboxInstanceStatus,
)


def test_fake_implements_sandbox_client_protocol() -> None:
    client = FakeSandboxClient()
    assert isinstance(client, SandboxClient)


def test_fake_get_missing_raises() -> None:
    client = FakeSandboxClient()
    with pytest.raises(SandboxNotFoundError):
        asyncio.run(client.get("sandbox0"))


def test_fake_create_start_stop_remove() -> None:
    client = FakeSandboxClient()

    async def _flow() -> None:
        sb = await client.create("sandbox0", image="python")
        assert isinstance(sb, ConnectedSandbox)
        assert await sb.ping() is True
        result = await sb.exec("python3", ["-c", "print(1)"])
        assert result.exit_code == 0
        await sb.stop()
        handle = await client.get("sandbox0")
        assert handle.status == SandboxInstanceStatus.STOPPED
        await client.start("sandbox0")
        handle2 = await client.get("sandbox0")
        assert handle2.status == SandboxInstanceStatus.RUNNING
        await client.remove("sandbox0")
        with pytest.raises(SandboxNotFoundError):
            await client.get("sandbox0")

    asyncio.run(_flow())
    assert client.create_calls == 1
    assert client.start_calls == 1
    assert client.remove_calls == 1
    assert client.stop_calls == 1


def test_fake_create_fails() -> None:
    client = FakeSandboxClient(create_fails=True)
    with pytest.raises(RuntimeError, match="create failed"):
        asyncio.run(client.create("sandbox0"))


def test_fake_start_fails_and_missing() -> None:
    client = FakeSandboxClient(start_fails=True)
    client.seed("sandbox0", SandboxInstanceStatus.STOPPED)
    with pytest.raises(RuntimeError, match="start failed"):
        asyncio.run(client.start("sandbox0"))
    client2 = FakeSandboxClient()
    with pytest.raises(SandboxNotFoundError):
        asyncio.run(client2.start("missing"))


def test_fake_remove_fails_leaves_state() -> None:
    client = FakeSandboxClient(remove_fails=True)
    client.seed("sandbox0", SandboxInstanceStatus.RUNNING)
    with pytest.raises(RuntimeError, match="remove failed"):
        asyncio.run(client.remove("sandbox0"))
    # State must remain consistent after failed remove.
    handle = asyncio.run(client.get("sandbox0"))
    assert handle.status == SandboxInstanceStatus.RUNNING


def test_fake_connect_fails() -> None:
    client = FakeSandboxClient(connect_fails_for={"sandbox0"})
    client.seed("sandbox0", SandboxInstanceStatus.RUNNING)

    async def _go() -> None:
        handle = await client.get("sandbox0")
        with pytest.raises(RuntimeError, match="connect failed"):
            await handle.connect()

    asyncio.run(_go())


def test_fake_exec_raises_and_empty_fail() -> None:
    client = FakeSandboxClient(exec_raises_times=1, empty_fail_times=1)
    sb = FakeConnectedSandbox(name="s", client=client)

    async def _go() -> None:
        with pytest.raises(RuntimeError, match="forced exec raise"):
            await sb.exec("python3", ["-c", "print(1)"])
        r = await sb.exec("python3", ["-c", "print(1)"])
        assert r.exit_code == 1
        assert r.stdout_text == ""

    asyncio.run(_go())


def test_fake_exec_results_override() -> None:
    key = "echo hi"
    client = FakeSandboxClient(
        exec_results={key: ExecResult(exit_code=0, stdout_text="hi\n")}
    )
    sb = FakeConnectedSandbox(name="s", client=client)
    result = asyncio.run(sb.exec("echo", ["hi"]))
    assert result.stdout_text == "hi\n"
    assert client.last_exec is not None
    assert client.last_exec["cmd"] == "echo"


def test_fake_stopped_exec_and_ping() -> None:
    client = FakeSandboxClient()
    sb = FakeConnectedSandbox(name="s", client=client)

    async def _go() -> None:
        await sb.stop()
        assert await sb.ping() is False
        r = await sb.exec("true")
        assert r.exit_code == 1

    asyncio.run(_go())


def test_fake_kill_sets_crashed() -> None:
    client = FakeSandboxClient()
    client.seed("sandbox0", SandboxInstanceStatus.RUNNING)
    sb = FakeConnectedSandbox(name="sandbox0", client=client)
    client._connected["sandbox0"] = sb

    async def _go() -> None:
        await sb.kill()
        handle = await client.get("sandbox0")
        assert handle.status == SandboxInstanceStatus.CRASHED

    asyncio.run(_go())


def test_fake_wait_until_stopped() -> None:
    client = FakeSandboxClient(drain_seconds=0.0)
    client.seed("sandbox0", SandboxInstanceStatus.DRAINING)
    asyncio.run(client.wait_until_stopped("sandbox0", timeout=1.0))
    handle = asyncio.run(client.get("sandbox0"))
    assert handle.status == SandboxInstanceStatus.STOPPED


def test_fake_build_create_kwargs_mount_map(tmp_path: Path) -> None:
    client = FakeSandboxClient()
    root = tmp_path / "sandbox0"
    root.mkdir()
    kwargs = client.build_create_kwargs(str(root))
    assert kwargs["workdir"] == GUEST_WORKSPACE_ROOT
    assert kwargs["env"] == guest_env()
    volumes = kwargs["volumes"]
    assert len(volumes) == len(MOUNT_SPEC)
    for guest, host_rel, readonly in MOUNT_SPEC:
        assert guest in volumes
        assert volumes[guest]["readonly"] is readonly
        assert volumes[guest]["host"] == str(root / host_rel)


def test_fake_detach() -> None:
    client = FakeSandboxClient()
    sb = FakeConnectedSandbox(name="s", client=client)
    asyncio.run(sb.detach())
    assert sb.detached is True
    assert client.detach_calls == 1
