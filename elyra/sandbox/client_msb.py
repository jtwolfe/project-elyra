"""Optional real microsandbox client (lazy import; never required at install).

Scope: thin async adapter implementing SandboxClient over microsandbox SDK.
In scope: get/create/start/remove/connect/ping/exec; try_create_real_client.
Out of scope: ensure SM, product factory, supervisor wiring (PR3).
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from elyra.sandbox.errors import SandboxClientUnusableError, SandboxNotFoundError
from elyra.sandbox.paths import MOUNT_SPEC, resolve_msb_network_policy_id
from elyra.sandbox.protocol import ExecResult

_LOG = logging.getLogger(__name__)


def _msb_network(Network: Any) -> Any:
    """Map resolved policy id → microsandbox Network object.

    Product policy ids: ``none`` | ``public_only`` | ``allow_all``.
    microsandbox 0.6.x removed ``Network.public_only`` in favor of
    ``Network.from_profiles("public")``; keep a fallback for older SDKs
    that still expose ``public_only``.

    Fail closed when neither public mapping exists — do **not** silently
    fall back to ``allow_all`` (operator must set ELYRA_SANDBOX_NETWORK=allow_all
    explicitly if unrestricted egress is intended).
    """
    policy = resolve_msb_network_policy_id()
    if policy == "none":
        return Network.none()
    if policy == "allow_all":
        return Network.allow_all()
    # public_only (default product egress for tool dogfood)
    public_only = getattr(Network, "public_only", None)
    if callable(public_only):
        return public_only()
    from_profiles = getattr(Network, "from_profiles", None)
    if callable(from_profiles):
        return from_profiles("public")
    raise AttributeError(
        "microsandbox Network has neither public_only nor from_profiles; "
        "upgrade microsandbox, or set ELYRA_SANDBOX_NETWORK=allow_all / none "
        "explicitly"
    )


def microsandbox_available() -> bool:
    """True when the optional microsandbox package can be imported."""
    try:
        import microsandbox  # noqa: F401
    except ImportError:
        return False
    return True


def try_create_real_client() -> MicrosandboxClient | None:
    """Return a real client or None if microsandbox is not installed."""
    if not microsandbox_available():
        return None
    try:
        return MicrosandboxClient()
    except Exception as exc:  # noqa: BLE001 — optional dep hard fail
        _LOG.warning("microsandbox client construction failed: %s", exc)
        return None


class _MsbConnected:
    """Wrap a live microsandbox.Sandbox as ConnectedSandbox."""

    def __init__(self, sb: Any, name: str) -> None:
        self._sb = sb
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def raw(self) -> Any:
        return self._sb

    async def ping(self) -> bool:
        result = await self._sb.ping()
        # SDK may return bool or truthy object.
        return bool(result)

    async def exec(
        self,
        cmd: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        kwargs: dict[str, Any] = {}
        if cwd is not None:
            kwargs["cwd"] = cwd
        if timeout is not None:
            kwargs["timeout"] = timeout
        if env is not None:
            kwargs["env"] = dict(env)
        out = await self._sb.exec(cmd, list(args or []), **kwargs)
        return ExecResult(
            exit_code=int(getattr(out, "exit_code", 1)),
            stdout_text=str(getattr(out, "stdout_text", "") or ""),
            stderr_text=str(getattr(out, "stderr_text", "") or ""),
        )

    async def stop(self, timeout: float | None = None) -> None:
        if timeout is not None:
            await self._sb.stop(timeout=timeout)
        else:
            await self._sb.stop()

    async def kill(self) -> None:
        await self._sb.kill()

    async def detach(self) -> None:
        await self._sb.detach()


class _MsbHandle:
    """Wrap SandboxHandle from Sandbox.get."""

    def __init__(self, handle: Any, name: str) -> None:
        self._handle = handle
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def status(self) -> str:
        raw = getattr(self._handle, "status", None)
        if raw is None:
            return "unknown"
        # StrEnum or str
        return str(getattr(raw, "value", raw)).lower()

    async def connect(self) -> _MsbConnected:
        sb = await self._handle.connect()
        return _MsbConnected(sb, self._name)


class MicrosandboxClient:
    """SandboxClient adapter over the optional microsandbox package."""

    def __init__(self) -> None:
        try:
            from microsandbox import Network, Sandbox, Volume  # type: ignore[import-untyped]
        except ImportError as exc:
            raise SandboxClientUnusableError(
                "microsandbox package is not installed"
            ) from exc
        self._Sandbox = Sandbox
        self._Network = Network
        self._Volume = Volume

    def build_create_kwargs(
        self,
        host_root: str,
        *,
        image: str = "python",
        cpus: int = 1,
        memory: int = 512,
        security: str = "restricted",
        workdir: str = "/workspace",
        env: Mapping[str, str] | None = None,
        pull_policy: str = "if-missing",
        detached: bool = True,
    ) -> dict[str, Any]:
        """Pinned create kwargs (DESIGN SDK contract).

        Volumes are derived from ``MOUNT_SPEC`` (KD17) so live MSB guests always
        match fingerprint / fake client mounts (including RO ``/workspace/media``).
        """
        Volume = self._Volume
        Network = self._Network
        from pathlib import Path

        root = Path(host_root)
        volumes = {
            guest: Volume.bind(str(root / host_rel), readonly=readonly)
            for guest, host_rel, readonly in MOUNT_SPEC
        }
        return {
            "image": image,
            "cpus": cpus,
            "memory": memory,
            "security": security,
            "workdir": workdir,
            "env": dict(env or {}),
            "pull_policy": pull_policy,
            "detached": detached,
            "network": _msb_network(Network),
            "volumes": volumes,
        }

    async def get(self, name: str) -> _MsbHandle:
        try:
            handle = await self._Sandbox.get(name)
        except Exception as exc:  # map not-found-ish errors
            msg = str(exc).lower()
            name_l = type(exc).__name__.lower()
            if "notfound" in name_l or "not found" in msg or "does not exist" in msg:
                raise SandboxNotFoundError(f"sandbox not found: {name}") from exc
            raise
        return _MsbHandle(handle, name)

    async def create(self, name: str, **kwargs: Any) -> _MsbConnected:
        sb = await self._Sandbox.create(name, **kwargs)
        return _MsbConnected(sb, name)

    async def start(self, name: str, *, detached: bool = True) -> _MsbConnected:
        sb = await self._Sandbox.start(name, detached=detached)
        return _MsbConnected(sb, name)

    async def remove(self, name: str) -> None:
        await self._Sandbox.remove(name)

    async def wait_until_stopped(
        self,
        name: str,
        *,
        timeout: float | None = None,
    ) -> None:
        # Prefer SDK helper if present; else poll get().status.
        import asyncio
        import time

        deadline = time.monotonic() + (timeout if timeout is not None else 30.0)
        while True:
            try:
                handle = await self.get(name)
            except SandboxNotFoundError:
                return
            status = handle.status
            if status in {"stopped", "crashed"}:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"wait_until_stopped timed out for {name}")
            await asyncio.sleep(0.2)
