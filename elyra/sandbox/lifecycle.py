"""SandboxLifecycleManager — warm sandbox ensure state machine.

Scope: ensure(name) for missing/running/stopped/crashed/draining; shutdown stop-only.
In scope: instance lock, one-shot recreate, readiness, connected cache, bridge,
last ensure reason for status surface.
Out of scope: tool invoke (PR4+); async warm thread lives on the supervisor.

Fingerprints are **in-memory only** (v1): after process restart the mismatch
branch is not taken until a successful Ready in this process stores a fp again.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

from elyra.config import ElyraPaths, resolve_paths
from elyra.sandbox.async_bridge import AsyncBridge
from elyra.sandbox.errors import (
    EnsureLockTimeoutError,
    SandboxClientUnusableError,
    SandboxError,
    SandboxNotFoundError,
)
from elyra.sandbox.health import full_readiness, ping_ok
from elyra.sandbox.paths import (
    GUEST_WORKSPACE_ROOT,
    MSB_CPUS,
    MSB_IMAGE,
    MSB_MEMORY_MIB,
    MSB_PULL_POLICY,
    MSB_SECURITY,
    PRIMARY_NAME,
    ensure_host_tree,
    guest_env,
    mount_fingerprint,
    resolve_msb_network_policy_id,
)
from elyra.sandbox.protocol import ConnectedSandbox, SandboxClient, SandboxInstanceStatus

_LOG = logging.getLogger(__name__)

# Timeouts (DESIGN resilience table).
ENSURE_WALL_SECONDS = 60.0
INSTANCE_LOCK_WAIT_SECONDS = 5.0
DRAIN_WAIT_SECONDS = 15.0
SHUTDOWN_STOP_SECONDS = 10.0
BRIDGE_SLACK_SECONDS = 5.0

EnsureStatus = Literal["ready", "degraded"]


class _WallTimeout(Exception):
    """Internal: ensure wall budget exhausted."""


@dataclass(frozen=True)
class EnsureResult:
    """Outcome of one ensure transition."""

    status: EnsureStatus
    name: str
    reason: str | None = None
    sandbox: ConnectedSandbox | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready" and self.sandbox is not None


class SandboxLifecycleManager:
    """Owns ensure SM, bridge, and connected handle cache for warm sandboxes."""

    def __init__(
        self,
        *,
        paths: ElyraPaths | None = None,
        client: SandboxClient | None = None,
        bridge: AsyncBridge | None = None,
        client_unusable: bool = False,
        skip_guest_readiness: bool = False,
    ) -> None:
        self._paths = paths or resolve_paths()
        self._bridge = bridge or AsyncBridge()
        self._owns_bridge = bridge is None
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self._connected: dict[str, ConnectedSandbox] = {}
        self._ready: dict[str, bool] = {}
        # In-memory only (v1); not persisted across process restarts.
        self._fingerprints: dict[str, str] = {}
        # Last ensure reason per name (status surface; no secrets/paths).
        self._last_reason: dict[str, str | None] = {}
        # True after at least one ensure() call for name (async warm progress).
        self._ensure_attempted: dict[str, bool] = {}
        self._skip_guest_readiness = skip_guest_readiness

        if client is not None:
            self._client: SandboxClient | None = client
            self.client_unusable = client_unusable
        elif client_unusable:
            self._client = None
            self.client_unusable = True
        else:
            # Prefer explicit fake/real injection; default tries optional real client.
            from elyra.sandbox.client_msb import try_create_real_client

            real = try_create_real_client()
            if real is None:
                self._client = None
                self.client_unusable = True
            else:
                self._client = real
                self.client_unusable = False

    @property
    def bridge(self) -> AsyncBridge:
        return self._bridge

    @property
    def client(self) -> SandboxClient | None:
        return self._client

    def _instance_lock(self, name: str) -> threading.RLock:
        with self._locks_guard:
            lock = self._locks.get(name)
            if lock is None:
                lock = threading.RLock()
                self._locks[name] = lock
            return lock

    def is_ready(self, name: str = PRIMARY_NAME) -> bool:
        """True when last successful ensure cached a ready connected sandbox."""
        return bool(self._ready.get(name)) and name in self._connected

    def get_connected(self, name: str = PRIMARY_NAME) -> ConnectedSandbox | None:
        return self._connected.get(name)

    def last_ensure_reason(self, name: str = PRIMARY_NAME) -> str | None:
        """Return last ensure reason for ``name`` (None when ready / never run)."""
        if self.client_unusable:
            return "client_unusable"
        return self._last_reason.get(name)

    def ensure_attempted(self, name: str = PRIMARY_NAME) -> bool:
        """True after at least one ``ensure`` call for ``name`` this process."""
        if self.client_unusable:
            return True
        return bool(self._ensure_attempted.get(name))

    def ensure(
        self,
        name: str = PRIMARY_NAME,
        *,
        timeout: float = ENSURE_WALL_SECONDS,
    ) -> EnsureResult:
        """Run ensure state machine; at most one remove+create per call.

        ``timeout`` is a single wall-clock budget for the whole transition
        (not a per-step allowance).
        """
        if self.client_unusable or self._client is None:
            self._last_reason[name] = "client_unusable"
            self._ensure_attempted[name] = True
            return EnsureResult(
                status="degraded",
                name=name,
                reason="client_unusable",
            )

        deadline = time.monotonic() + max(0.0, timeout)
        lock = self._instance_lock(name)
        acquired = lock.acquire(timeout=INSTANCE_LOCK_WAIT_SECONDS)
        if not acquired:
            self._last_reason[name] = "lock_timeout"
            self._ensure_attempted[name] = True
            return EnsureResult(
                status="degraded",
                name=name,
                reason="lock_timeout",
            )
        try:
            try:
                result = self._ensure_locked(name, deadline=deadline)
            except _WallTimeout:
                result = self._degraded(name, "ensure_wall_timeout")
            self._last_reason[name] = (
                None if result.ready else (result.reason or "degraded")
            )
            self._ensure_attempted[name] = True
            return result
        finally:
            lock.release()

    def _remaining(self, deadline: float) -> float:
        rem = deadline - time.monotonic()
        if rem <= 0:
            raise _WallTimeout()
        return rem

    def _run(self, coro: Any, *, deadline: float, cap: float | None = None) -> Any:
        """Run coro under remaining wall budget (+ bridge slack)."""
        try:
            rem = self._remaining(deadline)
            if cap is not None:
                rem = min(rem, cap)
            if rem <= 0:
                raise _WallTimeout()
        except _WallTimeout:
            # Avoid "coroutine was never awaited" when budget is already gone.
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            raise
        return self._bridge.run(coro, timeout=rem + BRIDGE_SLACK_SECONDS)

    def _run_fixed(self, coro: Any, *, timeout: float) -> Any:
        """Run with an explicit timeout (shutdown path; no ensure wall)."""
        return self._bridge.run(coro, timeout=timeout + BRIDGE_SLACK_SECONDS)

    def _ensure_locked(self, name: str, *, deadline: float) -> EnsureResult:
        assert self._client is not None
        host_root = ensure_host_tree(name, self._paths)
        fp = mount_fingerprint(
            name,
            host_root,
            image=MSB_IMAGE,
            network_policy_id=resolve_msb_network_policy_id(),
        )

        try:
            handle = self._run(self._client.get(name), deadline=deadline)
        except SandboxNotFoundError:
            return self._create_and_ready(name, host_root, fp, deadline=deadline)
        except _WallTimeout:
            raise
        except SandboxError as exc:
            _LOG.warning("ensure get failed for %s: %s", name, exc)
            return self._degraded(name, f"get_failed:{exc}")
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return self._create_and_ready(name, host_root, fp, deadline=deadline)
            _LOG.warning("ensure get failed for %s: %s", name, exc)
            return self._degraded(name, f"get_failed:{exc}")

        status = str(getattr(handle, "status", "unknown")).lower()
        stored_fp = self._fingerprints.get(name)
        if stored_fp is not None and stored_fp != fp:
            return self._recreate_once(
                name,
                host_root,
                fp,
                deadline=deadline,
                reason="fingerprint_mismatch",
            )

        if status == SandboxInstanceStatus.RUNNING:
            return self._handle_running(
                name, handle, host_root, fp, deadline=deadline
            )
        if status == SandboxInstanceStatus.STOPPED:
            return self._handle_stopped(name, host_root, fp, deadline=deadline)
        if status == SandboxInstanceStatus.CRASHED:
            return self._recreate_once(
                name, host_root, fp, deadline=deadline, reason="crashed"
            )
        if status == SandboxInstanceStatus.DRAINING:
            return self._handle_draining(name, host_root, fp, deadline=deadline)
        return self._degraded(name, f"unknown_status:{status}")

    def _handle_running(
        self,
        name: str,
        handle: Any,
        host_root: Path,
        fp: str,
        *,
        deadline: float,
    ) -> EnsureResult:
        try:
            sb = self._run(handle.connect(), deadline=deadline)
        except _WallTimeout:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOG.info("connect failed for running %s: %s — recreate", name, exc)
            return self._recreate_once(
                name, host_root, fp, deadline=deadline, reason="connect_failed"
            )
        try:
            if not self._run(ping_ok(sb), deadline=deadline, cap=15.0):
                return self._recreate_once(
                    name, host_root, fp, deadline=deadline, reason="ping_failed"
                )
        except _WallTimeout:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOG.info("ping failed for %s: %s — recreate", name, exc)
            return self._recreate_once(
                name, host_root, fp, deadline=deadline, reason="ping_failed"
            )
        # Running reconnect: readiness only (no detach — already detached owner).
        return self._ready_if_mounts(
            name, sb, host_root, fp, deadline=deadline, detach_after=False
        )

    def _handle_stopped(
        self,
        name: str,
        host_root: Path,
        fp: str,
        *,
        deadline: float,
    ) -> EnsureResult:
        assert self._client is not None
        try:
            sb = self._run(
                self._client.start(name, detached=True),
                deadline=deadline,
            )
        except _WallTimeout:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOG.info("start failed for %s: %s — recreate", name, exc)
            return self._recreate_once(
                name, host_root, fp, deadline=deadline, reason="start_failed"
            )
        try:
            if not self._run(ping_ok(sb), deadline=deadline, cap=15.0):
                return self._recreate_once(
                    name, host_root, fp, deadline=deadline, reason="ping_failed"
                )
        except _WallTimeout:
            raise
        except Exception as exc:  # noqa: BLE001
            return self._recreate_once(
                name,
                host_root,
                fp,
                deadline=deadline,
                reason=f"ping_failed:{exc}",
            )
        # Design: start → ping → readiness → detach if owning.
        return self._ready_if_mounts(
            name, sb, host_root, fp, deadline=deadline, detach_after=True
        )

    def _handle_draining(
        self,
        name: str,
        host_root: Path,
        fp: str,
        *,
        deadline: float,
    ) -> EnsureResult:
        assert self._client is not None
        rem = self._remaining(deadline)
        wait_t = min(rem, DRAIN_WAIT_SECONDS)
        try:
            # Pass wait_t only; _run adds bridge slack once.
            self._run(
                self._client.wait_until_stopped(name, timeout=wait_t),
                deadline=deadline,
                cap=wait_t,
            )
        except _WallTimeout:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOG.info("drain wait failed for %s: %s — recreate", name, exc)
            return self._recreate_once(
                name, host_root, fp, deadline=deadline, reason="drain_timeout"
            )
        return self._handle_stopped(name, host_root, fp, deadline=deadline)

    def _create_and_ready(
        self,
        name: str,
        host_root: Path,
        fp: str,
        *,
        deadline: float,
    ) -> EnsureResult:
        assert self._client is not None
        kwargs = self._create_kwargs(host_root)
        try:
            sb = self._run(self._client.create(name, **kwargs), deadline=deadline)
        except _WallTimeout:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("create failed for %s: %s", name, exc)
            return self._degraded(name, f"create_failed:{exc}")
        # Design: create → mount readiness → detach.
        return self._ready_if_mounts(
            name, sb, host_root, fp, deadline=deadline, detach_after=True
        )

    def _recreate_once(
        self,
        name: str,
        host_root: Path,
        fp: str,
        *,
        deadline: float,
        reason: str,
    ) -> EnsureResult:
        """One-shot remove (+kill/stop best-effort) then create."""
        assert self._client is not None
        _LOG.info("recreate sandbox %s (reason=%s)", name, reason)
        self._clear_cache(name)
        try:
            try:
                handle = self._run(
                    self._client.get(name), deadline=deadline, cap=15.0
                )
                status = str(getattr(handle, "status", "")).lower()
                if status == SandboxInstanceStatus.RUNNING:
                    try:
                        sb = self._run(
                            handle.connect(), deadline=deadline, cap=15.0
                        )
                        self._run(sb.kill(), deadline=deadline, cap=15.0)
                    except _WallTimeout:
                        raise
                    except Exception:  # noqa: BLE001
                        pass
            except SandboxNotFoundError:
                pass
            except _WallTimeout:
                raise
            except Exception:  # noqa: BLE001
                pass
            self._run(self._client.remove(name), deadline=deadline, cap=30.0)
        except SandboxNotFoundError:
            pass
        except _WallTimeout:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("remove before recreate failed for %s: %s", name, exc)
        return self._create_and_ready(name, host_root, fp, deadline=deadline)

    def _ready_if_mounts(
        self,
        name: str,
        sb: ConnectedSandbox,
        host_root: Path,
        fp: str,
        *,
        deadline: float,
        detach_after: bool,
    ) -> EnsureResult:
        if self._skip_guest_readiness:
            from elyra.sandbox.health import host_mount_ready

            ok, reason = host_mount_ready(host_root)
            if not ok:
                self._clear_cache(name)
                return self._degraded(name, reason or "host_not_ready")
        else:
            try:
                ok, reason = self._run(
                    full_readiness(sb, host_root, timeout=15.0),
                    deadline=deadline,
                    cap=30.0,
                )
            except _WallTimeout:
                raise
            except Exception as exc:  # noqa: BLE001
                self._clear_cache(name)
                return self._degraded(name, f"readiness_error:{exc}")
            if not ok:
                self._clear_cache(name)
                return self._degraded(name, reason or "mount_not_ready")

        # Detach only after readiness succeeds.
        if detach_after:
            try:
                self._run(sb.detach(), deadline=deadline, cap=10.0)
            except _WallTimeout:
                raise
            except Exception as exc:  # noqa: BLE001
                _LOG.debug("detach after readiness ignored for %s: %s", name, exc)

        self._connected[name] = sb
        self._ready[name] = True
        self._fingerprints[name] = fp
        return EnsureResult(status="ready", name=name, sandbox=sb)

    def _create_kwargs(self, host_root: Path) -> dict[str, Any]:
        client = self._client
        if client is not None and hasattr(client, "build_create_kwargs"):
            return client.build_create_kwargs(  # type: ignore[attr-defined]
                str(host_root),
                image=MSB_IMAGE,
                cpus=MSB_CPUS,
                memory=MSB_MEMORY_MIB,
                security=MSB_SECURITY,
                workdir=GUEST_WORKSPACE_ROOT,
                env=guest_env(),
                pull_policy=MSB_PULL_POLICY,
                detached=True,
            )
        return {
            "image": MSB_IMAGE,
            "cpus": MSB_CPUS,
            "memory": MSB_MEMORY_MIB,
            "security": MSB_SECURITY,
            "workdir": GUEST_WORKSPACE_ROOT,
            "env": guest_env(),
            "pull_policy": MSB_PULL_POLICY,
            "detached": True,
            "network": resolve_msb_network_policy_id(),
            "host_root": str(host_root),
        }

    def _degraded(self, name: str, reason: str) -> EnsureResult:
        self._ready[name] = False
        self._connected.pop(name, None)
        return EnsureResult(status="degraded", name=name, reason=reason)

    def _clear_cache(self, name: str) -> None:
        self._ready[name] = False
        self._connected.pop(name, None)

    def invalidate(self, name: str = PRIMARY_NAME) -> None:
        """Drop ready/connected cache so the next with_ready_sandbox re-ensures.

        Used after mid-exec sandbox death (DESIGN: one ensure reconnect before
        retrying exec once). Does not stop the VM — ensure decides recreate.
        """
        self._clear_cache(name)

    @contextmanager
    def with_ready_sandbox(
        self,
        name: str = PRIMARY_NAME,
        *,
        timeout: float = ENSURE_WALL_SECONDS,
    ) -> Iterator[ConnectedSandbox]:
        """Lock; ping; ensure if unhealthy; yield connected sandbox.

        On failure raises SandboxError. Used by runners in later PRs.
        """
        if self.client_unusable or self._client is None:
            raise SandboxClientUnusableError("sandbox client unusable")

        deadline = time.monotonic() + max(0.0, timeout)
        lock = self._instance_lock(name)
        acquired = lock.acquire(timeout=INSTANCE_LOCK_WAIT_SECONDS)
        if not acquired:
            raise EnsureLockTimeoutError(f"lock timeout for {name}")
        try:
            sb = self._connected.get(name)
            healthy = False
            if sb is not None and self._ready.get(name):
                try:
                    healthy = bool(
                        self._run(ping_ok(sb), deadline=deadline, cap=15.0)
                    )
                except _WallTimeout as exc:
                    raise SandboxError("ensure_wall_timeout") from exc
                except Exception:  # noqa: BLE001
                    healthy = False
            if not healthy:
                try:
                    result = self._ensure_locked(name, deadline=deadline)
                except _WallTimeout as exc:
                    raise SandboxError("ensure_wall_timeout") from exc
                if not result.ready or result.sandbox is None:
                    raise SandboxError(
                        f"ensure not ready: {result.reason or result.status}"
                    )
                sb = result.sandbox
            assert sb is not None
            yield sb
        finally:
            lock.release()

    def _stop_instance(self, name: str, *, stop_timeout: float) -> None:
        """Stop by cached handle or get+connect (cold cache) — never remove."""
        if self.client_unusable or self._client is None:
            return
        if self._bridge.closed:
            _LOG.warning("cannot stop sandbox %s: bridge already closed", name)
            return

        sb = self._connected.get(name)
        if sb is not None:
            try:
                self._run_fixed(sb.stop(timeout=stop_timeout), timeout=stop_timeout)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("sandbox stop failed for %s: %s", name, exc)
            return

        # Cold cache: still stop a running detached VM.
        try:
            handle = self._run_fixed(self._client.get(name), timeout=stop_timeout)
        except SandboxNotFoundError:
            return
        except Exception as exc:  # noqa: BLE001
            if _is_not_found(exc):
                return
            _LOG.warning("shutdown get failed for %s: %s", name, exc)
            return

        status = str(getattr(handle, "status", "")).lower()
        if status not in {
            SandboxInstanceStatus.RUNNING,
            SandboxInstanceStatus.DRAINING,
        }:
            return
        try:
            connected = self._run_fixed(handle.connect(), timeout=stop_timeout)
            self._run_fixed(
                connected.stop(timeout=stop_timeout),
                timeout=stop_timeout,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("cold-cache sandbox stop failed for %s: %s", name, exc)

    def shutdown(
        self,
        name: str = PRIMARY_NAME,
        *,
        stop_timeout: float = SHUTDOWN_STOP_SECONDS,
    ) -> None:
        """Stop warm sandbox (no remove) and shut down the async bridge.

        Always attempts stop even when the connected cache is empty (get+connect).
        Clears in-memory fingerprints and ready/connected cache (durable-state
        cleanup for process lifetime; fingerprints are v1 in-memory only).
        """
        lock = self._instance_lock(name)
        acquired = lock.acquire(timeout=INSTANCE_LOCK_WAIT_SECONDS)
        if not acquired:
            _LOG.warning(
                "shutdown: lock timeout for %s; attempting stop without exclusive lock",
                name,
            )
        try:
            try:
                self._stop_instance(name, stop_timeout=stop_timeout)
            finally:
                self._clear_cache(name)
                self._fingerprints.pop(name, None)
                self._last_reason.pop(name, None)
                self._ensure_attempted.pop(name, None)
        finally:
            if acquired:
                lock.release()
        if self._owns_bridge:
            self._bridge.shutdown()


def _is_not_found(exc: BaseException) -> bool:
    if isinstance(exc, SandboxNotFoundError):
        return True
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return "notfound" in name or "not found" in msg or "does not exist" in msg
