"""SandboxClient protocol and shared status types.

Scope: abstract surface lifecycle uses for real msb and FakeSandboxClient.
In scope: handle/connected sandbox protocols, create kwargs helpers.
Out of scope: microsandbox import, ensure state machine (PR2).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol, runtime_checkable


class SandboxInstanceStatus(StrEnum):
    """Observed microsandbox instance status (ensure SM inputs)."""

    RUNNING = "running"
    STOPPED = "stopped"
    CRASHED = "crashed"
    DRAINING = "draining"


@dataclass(frozen=True)
class ExecResult:
    """Normalized exec outcome (SDK-agnostic)."""

    exit_code: int
    stdout_text: str = ""
    stderr_text: str = ""


@runtime_checkable
class ConnectedSandbox(Protocol):
    """Connected sandbox capable of exec/ping/lifecycle ops."""

    @property
    def name(self) -> str: ...

    async def ping(self) -> bool: ...

    async def exec(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult: ...

    async def stop(self, timeout: float | None = None) -> None: ...

    async def kill(self) -> None: ...

    async def detach(self) -> None: ...


@runtime_checkable
class SandboxHandle(Protocol):
    """Lightweight handle from get(); must connect before exec."""

    @property
    def name(self) -> str: ...

    @property
    def status(self) -> str: ...

    async def connect(self) -> ConnectedSandbox: ...


@runtime_checkable
class SandboxClient(Protocol):
    """Async client for sandbox lifecycle (real SDK or fake)."""

    async def get(self, name: str) -> SandboxHandle: ...

    async def create(
        self,
        name: str,
        **kwargs: Any,
    ) -> ConnectedSandbox: ...

    async def start(
        self,
        name: str,
        *,
        detached: bool = True,
    ) -> ConnectedSandbox: ...

    async def remove(self, name: str) -> None: ...

    async def wait_until_stopped(
        self,
        name: str,
        *,
        timeout: float | None = None,
    ) -> None: ...
