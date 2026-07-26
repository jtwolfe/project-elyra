"""In-process FakeSandboxClient for unit tests (no KVM / microsandbox).

Scope: cover ensure SM statuses missing/running/stopped/crashed/draining.
In scope: configurable get/create/start/remove/connect/ping/exec failures;
RO/RW mount map in create kwargs for isolation tests.
Out of scope: real SDK, product wiring.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from elyra.sandbox.errors import SandboxNotFoundError
from elyra.sandbox.paths import (
    GUEST_WORKSPACE_ROOT,
    MOUNT_SPEC,
    guest_env,
    resolve_msb_network_policy_id,
)
from elyra.sandbox.protocol import (
    ExecResult,
    SandboxInstanceStatus,
)


@dataclass
class FakeConnectedSandbox:
    """Connected fake sandbox with controllable ping/exec."""

    name: str
    client: FakeSandboxClient
    ping_ok: bool = True
    exec_results: dict[str, ExecResult] = field(default_factory=dict)
    default_exec: ExecResult = field(
        default_factory=lambda: ExecResult(exit_code=0, stdout_text="1\n")
    )
    detached: bool = False
    _stopped: bool = False

    async def ping(self) -> bool:
        if self._stopped:
            return False
        return self.ping_ok

    async def exec(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        self.client.event_log.append("exec")
        self.client.exec_calls += 1
        self.client.last_exec = {
            "cmd": cmd,
            "args": list(args or []),
            "cwd": cwd,
            "timeout": timeout,
            "env": dict(env) if env is not None else None,
        }
        if self.client.exec_raises_remaining > 0:
            self.client.exec_raises_remaining -= 1
            raise RuntimeError("forced exec raise (simulated sandbox death)")
        if self.client.empty_fail_remaining > 0:
            self.client.empty_fail_remaining -= 1
            return ExecResult(exit_code=1, stdout_text="", stderr_text="")
        if self._stopped:
            return ExecResult(exit_code=1, stderr_text="stopped")
        key = _exec_key(cmd, args or [])
        if key in self.exec_results:
            return self.exec_results[key]
        if key in self.client.exec_results:
            return self.client.exec_results[key]
        if self.client.fail_all_exec:
            return ExecResult(exit_code=1, stderr_text="forced_exec_fail")
        # Readiness probes: succeed by default when ping_ok.
        if not self.ping_ok:
            return ExecResult(exit_code=1, stderr_text="unhealthy")
        return self.default_exec

    async def stop(self, timeout: float | None = None) -> None:
        del timeout
        self.client.stop_calls += 1
        self.client.event_log.append("stop")
        self._stopped = True
        self.client._set_status(self.name, SandboxInstanceStatus.STOPPED)
        self.client._connected.pop(self.name, None)

    async def kill(self) -> None:
        self.client.event_log.append("kill")
        self._stopped = True
        self.client._set_status(self.name, SandboxInstanceStatus.CRASHED)
        self.client._connected.pop(self.name, None)

    async def detach(self) -> None:
        self.client.event_log.append("detach")
        self.client.detach_calls += 1
        self.detached = True


@dataclass
class FakeSandboxHandle:
    """Handle returned by FakeSandboxClient.get."""

    name: str
    status: str
    client: FakeSandboxClient
    connect_fails: bool = False

    async def connect(self) -> FakeConnectedSandbox:
        if self.connect_fails:
            raise RuntimeError(f"connect failed for {self.name}")
        self.client.event_log.append("connect")
        return self.client._ensure_connected(self.name)


def _exec_key(cmd: str, args: list[str]) -> str:
    return " ".join([cmd, *args])


class FakeSandboxClient:
    """In-memory client driving ensure state machine unit tests.

    Seed instances via ``seed`` / constructor ``instances`` map of
    name → status string. Toggle failures with attributes.

    ``build_create_kwargs`` mirrors the real client RO seed + RW tmp/tools
    mount map so lifecycle tests can assert volume policy without KVM.
    """

    def __init__(
        self,
        instances: Mapping[str, str] | None = None,
        *,
        create_fails: bool = False,
        start_fails: bool = False,
        remove_fails: bool = False,
        default_ping_ok: bool = True,
        connect_fails_for: set[str] | None = None,
        drain_seconds: float = 0.0,
        fail_all_exec: bool = False,
        exec_results: Mapping[str, ExecResult] | None = None,
        # When set, next N exec calls raise (simulate mid-exec death).
        exec_raises_times: int = 0,
        # When set, first N exec calls return empty non-zero (sandbox death heuristic).
        empty_fail_times: int = 0,
    ) -> None:
        self._status: dict[str, str] = {
            k: str(v) for k, v in (instances or {}).items()
        }
        self._connected: dict[str, FakeConnectedSandbox] = {}
        self._create_kwargs: dict[str, dict[str, Any]] = {}
        self.create_fails = create_fails
        self.start_fails = start_fails
        self.remove_fails = remove_fails
        self.default_ping_ok = default_ping_ok
        self.connect_fails_for = set(connect_fails_for or ())
        self.drain_seconds = drain_seconds
        self.fail_all_exec = fail_all_exec
        self.exec_results: dict[str, ExecResult] = dict(exec_results or {})
        self.exec_raises_remaining = exec_raises_times
        self.empty_fail_remaining = empty_fail_times
        self.create_calls = 0
        self.start_calls = 0
        self.remove_calls = 0
        self.get_calls = 0
        self.stop_calls = 0
        self.detach_calls = 0
        self.exec_calls = 0
        self.event_log: list[str] = []
        self.last_exec: dict[str, Any] | None = None

    def build_create_kwargs(
        self,
        host_root: str,
        *,
        image: str = "python",
        cpus: int = 1,
        memory: int = 512,
        security: str = "restricted",
        workdir: str = GUEST_WORKSPACE_ROOT,
        env: Mapping[str, str] | None = None,
        pull_policy: str = "if-missing",
        detached: bool = True,
    ) -> dict[str, Any]:
        """Accept RO/RW mount map policy (same shape as MicrosandboxClient)."""
        root = Path(host_root)
        volumes: dict[str, dict[str, Any]] = {}
        for guest, host_rel, readonly in MOUNT_SPEC:
            volumes[guest] = {
                "host": str(root / host_rel),
                "readonly": readonly,
            }
        return {
            "image": image,
            "cpus": cpus,
            "memory": memory,
            "security": security,
            "workdir": workdir,
            "env": dict(env if env is not None else guest_env()),
            "pull_policy": pull_policy,
            "detached": detached,
            "network": resolve_msb_network_policy_id(),
            "volumes": volumes,
            "host_root": str(root),
        }

    def create_kwargs_for(self, name: str) -> dict[str, Any] | None:
        return self._create_kwargs.get(name)

    def seed(self, name: str, status: str) -> None:
        """Set or replace observed status for ``name`` (no connected cache)."""
        self._status[name] = status
        self._connected.pop(name, None)

    def set_ping_ok(self, name: str, ok: bool) -> None:
        sb = self._connected.get(name)
        if sb is not None:
            sb.ping_ok = ok
        if not hasattr(self, "_ping_overrides"):
            self._ping_overrides: dict[str, bool] = {}
        self._ping_overrides[name] = ok

    def _ping_for(self, name: str) -> bool:
        overrides = getattr(self, "_ping_overrides", {})
        if name in overrides:
            return overrides[name]
        return self.default_ping_ok

    def _set_status(self, name: str, status: str) -> None:
        self._status[name] = status

    def _ensure_connected(self, name: str) -> FakeConnectedSandbox:
        existing = self._connected.get(name)
        if existing is not None and not existing._stopped:
            return existing
        sb = FakeConnectedSandbox(
            name=name,
            client=self,
            ping_ok=self._ping_for(name),
            exec_results=dict(self.exec_results),
            default_exec=(
                ExecResult(exit_code=1, stderr_text="forced_exec_fail")
                if self.fail_all_exec
                else ExecResult(exit_code=0, stdout_text="1\n")
            ),
        )
        self._connected[name] = sb
        self._status[name] = SandboxInstanceStatus.RUNNING
        return sb

    async def get(self, name: str) -> FakeSandboxHandle:
        self.get_calls += 1
        self.event_log.append("get")
        if name not in self._status:
            raise SandboxNotFoundError(f"sandbox not found: {name}")
        return FakeSandboxHandle(
            name=name,
            status=self._status[name],
            client=self,
            connect_fails=name in self.connect_fails_for,
        )

    async def create(self, name: str, **kwargs: Any) -> FakeConnectedSandbox:
        self.create_calls += 1
        self.event_log.append("create")
        if self.create_fails:
            raise RuntimeError(f"create failed for {name}")
        self._create_kwargs[name] = dict(kwargs)
        self._status[name] = SandboxInstanceStatus.RUNNING
        return self._ensure_connected(name)

    async def start(
        self,
        name: str,
        *,
        detached: bool = True,
    ) -> FakeConnectedSandbox:
        del detached
        self.start_calls += 1
        self.event_log.append("start")
        if name not in self._status:
            raise SandboxNotFoundError(f"sandbox not found: {name}")
        if self.start_fails:
            raise RuntimeError(f"start failed for {name}")
        self._status[name] = SandboxInstanceStatus.RUNNING
        return self._ensure_connected(name)

    async def remove(self, name: str) -> None:
        self.remove_calls += 1
        self.event_log.append("remove")
        if self.remove_fails:
            raise RuntimeError(f"remove failed for {name}")
        self._status.pop(name, None)
        self._connected.pop(name, None)
        self._create_kwargs.pop(name, None)

    async def wait_until_stopped(
        self,
        name: str,
        *,
        timeout: float | None = None,
    ) -> None:
        """Simulate draining → stopped (honors drain_seconds / timeout)."""
        self.event_log.append("wait_until_stopped")
        deadline = time.monotonic() + (timeout if timeout is not None else 30.0)
        sleep_for = self.drain_seconds
        if sleep_for > 0:
            remaining = deadline - time.monotonic()
            await asyncio.sleep(min(sleep_for, max(0.0, remaining)))
        if time.monotonic() > deadline and self._status.get(name) == (
            SandboxInstanceStatus.DRAINING
        ):
            raise TimeoutError(f"wait_until_stopped timed out for {name}")
        if name in self._status:
            self._status[name] = SandboxInstanceStatus.STOPPED
            self._connected.pop(name, None)
