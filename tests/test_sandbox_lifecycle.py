"""SandboxLifecycleManager ensure state machine with FakeSandboxClient."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from elyra.config import ElyraPaths
from elyra.sandbox import (
    EnsureLockTimeoutError,
    FakeSandboxClient,
    PRIMARY_NAME,
    SandboxClientUnusableError,
    SandboxError,
    SandboxInstanceStatus,
    SandboxLifecycleManager,
    clear_sandbox_lifecycle,
    get_sandbox_lifecycle,
    set_sandbox_lifecycle,
)
from elyra.sandbox import lifecycle as lifecycle_mod
from elyra.sandbox.paths import ensure_host_tree


def _layout(tmp_path: Path) -> ElyraPaths:
    return ElyraPaths(
        home=tmp_path,
        model_dir=tmp_path / "model",
        data_dir=tmp_path / "data",
        skills_dir=tmp_path / "skills",
        tools_dir=tmp_path / "tools",
        prompts_dir=tmp_path / "prompts",
    )


def _seed_minimal(tmp_path: Path) -> Path:
    """Create a host primary tree that passes host_mount_ready.

    Uses empty seed_source so we control content (repo seed may also pass,
    but explicit files keep the test hermetic if seed layout changes).
    """
    layout = _layout(tmp_path)
    empty = tmp_path / "_empty_seed"
    empty.mkdir(exist_ok=True)
    root = ensure_host_tree(PRIMARY_NAME, layout, seed_source=empty)
    (root / "lib").mkdir(exist_ok=True)
    (root / "lib" / "paths.py").write_text("# seed\n", encoding="utf-8")
    general = root / "general"
    general.mkdir(exist_ok=True)
    (general / "now.py").write_text(
        "def main():\n    return {'ok': True}\n", encoding="utf-8"
    )
    (root / "tmp").mkdir(exist_ok=True)
    (root / "tools").mkdir(exist_ok=True)
    return root


def _manager(
    tmp_path: Path,
    client: FakeSandboxClient,
    *,
    skip_guest_readiness: bool = True,
    **kwargs,
) -> SandboxLifecycleManager:
    return SandboxLifecycleManager(
        paths=_layout(tmp_path),
        client=client,
        skip_guest_readiness=skip_guest_readiness,
        **kwargs,
    )


def test_ensure_missing_creates_ready(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient()  # empty → not found
    mgr = _manager(tmp_path, client)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert result.sandbox is not None
        assert mgr.is_ready(PRIMARY_NAME)
        assert client.create_calls == 1
        assert client.get_calls >= 1
    finally:
        mgr.shutdown()


def test_ensure_running_connects(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(
        instances={PRIMARY_NAME: SandboxInstanceStatus.RUNNING},
    )
    mgr = _manager(tmp_path, client)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert result.sandbox is not None
        assert client.create_calls == 0
    finally:
        mgr.shutdown()


def test_ensure_stopped_starts(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(
        instances={PRIMARY_NAME: SandboxInstanceStatus.STOPPED},
    )
    mgr = _manager(tmp_path, client)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert client.start_calls == 1
        assert client.create_calls == 0
    finally:
        mgr.shutdown()


def test_ensure_crashed_recreates(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(
        instances={PRIMARY_NAME: SandboxInstanceStatus.CRASHED},
    )
    mgr = _manager(tmp_path, client)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert client.remove_calls >= 1
        assert client.create_calls == 1
    finally:
        mgr.shutdown()


def test_ensure_draining_waits_then_starts(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(
        instances={PRIMARY_NAME: SandboxInstanceStatus.DRAINING},
        drain_seconds=0.05,
    )
    mgr = _manager(tmp_path, client)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert client.start_calls == 1
    finally:
        mgr.shutdown()


def test_ensure_running_ping_fail_recreates(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(
        instances={PRIMARY_NAME: SandboxInstanceStatus.RUNNING},
        default_ping_ok=False,
    )
    mgr = _manager(tmp_path, client)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert client.remove_calls >= 1
        assert client.create_calls == 1
    finally:
        mgr.shutdown()


def test_ensure_connect_fail_recreates(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(
        instances={PRIMARY_NAME: SandboxInstanceStatus.RUNNING},
        connect_fails_for={PRIMARY_NAME},
    )
    mgr = _manager(tmp_path, client)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert client.create_calls == 1
    finally:
        mgr.shutdown()


def test_ensure_start_fail_recreates(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(
        instances={PRIMARY_NAME: SandboxInstanceStatus.STOPPED},
        start_fails=True,
    )
    mgr = _manager(tmp_path, client)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert client.start_calls == 1
        assert client.create_calls == 1
    finally:
        mgr.shutdown()


def test_ensure_create_fail_degraded(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(create_fails=True)
    mgr = _manager(tmp_path, client)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "degraded"
        assert result.reason is not None
        assert "create_failed" in result.reason
        assert not mgr.is_ready(PRIMARY_NAME)
    finally:
        mgr.shutdown()


def test_ensure_client_unusable_degraded(tmp_path: Path) -> None:
    mgr = SandboxLifecycleManager(
        paths=_layout(tmp_path),
        client=None,
        client_unusable=True,
        skip_guest_readiness=True,
    )
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "degraded"
        assert result.reason == "client_unusable"
        assert mgr.client_unusable is True
    finally:
        mgr.shutdown()


def test_ensure_host_not_ready_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When host_mount_ready fails under skip_guest, ensure is degraded.

    Note: lifecycle always re-runs ensure_host_tree (which re-seeds from the
    repo), so we force the host check itself rather than fighting seed copy.
    """
    _seed_minimal(tmp_path)
    monkeypatch.setattr(
        "elyra.sandbox.health.host_mount_ready",
        lambda _root: (False, "host_seed_not_readable"),
    )
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "degraded"
        assert result.reason == "host_seed_not_readable"
        assert not mgr.is_ready(PRIMARY_NAME)
    finally:
        mgr.shutdown()


def test_with_ready_sandbox_context(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client)
    try:
        with mgr.with_ready_sandbox(PRIMARY_NAME) as sb:
            assert sb.name == PRIMARY_NAME
        assert mgr.is_ready(PRIMARY_NAME)
    finally:
        mgr.shutdown()


def test_with_ready_sandbox_client_unusable_raises(tmp_path: Path) -> None:
    mgr = SandboxLifecycleManager(
        paths=_layout(tmp_path),
        client=None,
        client_unusable=True,
    )
    try:
        with pytest.raises(SandboxClientUnusableError):
            with mgr.with_ready_sandbox(PRIMARY_NAME):
                pass
    finally:
        mgr.shutdown()


def test_with_ready_sandbox_ensure_fail_raises(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(create_fails=True)
    mgr = _manager(tmp_path, client)
    try:
        with pytest.raises(SandboxError, match="ensure not ready"):
            with mgr.with_ready_sandbox(PRIMARY_NAME):
                pass
    finally:
        mgr.shutdown()


def test_shutdown_stop_only_no_remove(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client)
    assert mgr.ensure(PRIMARY_NAME).ready
    removes_before = client.remove_calls
    mgr.shutdown()
    assert client.remove_calls == removes_before
    assert client.stop_calls >= 1
    assert not mgr.is_ready(PRIMARY_NAME)


def test_shutdown_clears_fingerprint_durable_state(tmp_path: Path) -> None:
    """Past issue: durable in-memory state (fingerprints) cleaned on shutdown."""
    _seed_minimal(tmp_path)
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client)
    try:
        assert mgr.ensure(PRIMARY_NAME).ready
        assert PRIMARY_NAME in mgr._fingerprints
        mgr.shutdown()
        assert PRIMARY_NAME not in mgr._fingerprints
        assert not mgr.is_ready(PRIMARY_NAME)
        assert mgr.get_connected(PRIMARY_NAME) is None
    finally:
        pass


def test_invalidate_clears_ready_cache(tmp_path: Path) -> None:
    _seed_minimal(tmp_path)
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client)
    try:
        assert mgr.ensure(PRIMARY_NAME).ready
        mgr.invalidate(PRIMARY_NAME)
        assert not mgr.is_ready(PRIMARY_NAME)
        assert mgr.get_connected(PRIMARY_NAME) is None
        # Fingerprint retained so mismatch detection still works if policy drifts.
        # Next ensure reconnects without necessarily recreating if still healthy.
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
    finally:
        mgr.shutdown()


def test_create_readiness_before_detach(tmp_path: Path) -> None:
    """create → guest readiness exec → detach (not detach first)."""
    _seed_minimal(tmp_path)
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client, skip_guest_readiness=False)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert "create" in client.event_log
        assert "exec" in client.event_log
        assert "detach" in client.event_log
        assert client.event_log.index("create") < client.event_log.index("exec")
        # First readiness exec must precede detach.
        first_exec = client.event_log.index("exec")
        first_detach = client.event_log.index("detach")
        assert first_exec < first_detach
        assert result.sandbox is not None
        assert result.sandbox.detached is True  # type: ignore[attr-defined]
    finally:
        mgr.shutdown()


def test_stopped_readiness_before_detach(tmp_path: Path) -> None:
    """start → readiness → detach."""
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(
        instances={PRIMARY_NAME: SandboxInstanceStatus.STOPPED},
    )
    mgr = _manager(tmp_path, client, skip_guest_readiness=False)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert client.event_log.index("start") < client.event_log.index("exec")
        assert client.event_log.index("exec") < client.event_log.index("detach")
    finally:
        mgr.shutdown()


def test_fingerprint_mismatch_recreates(tmp_path: Path) -> None:
    """Stored fingerprint ≠ current → remove+create once."""
    _seed_minimal(tmp_path)
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client)
    try:
        assert mgr.ensure(PRIMARY_NAME).ready
        creates_after_first = client.create_calls
        removes_after_first = client.remove_calls
        # Simulate policy drift in-process.
        mgr._fingerprints[PRIMARY_NAME] = "stale-fingerprint-not-matching"
        # Instance still running after first ensure.
        client.seed(PRIMARY_NAME, SandboxInstanceStatus.RUNNING)
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert client.remove_calls > removes_after_first
        assert client.create_calls > creates_after_first
    finally:
        mgr.shutdown()


def test_shutdown_cold_cache_still_stops(tmp_path: Path) -> None:
    """Stop via get+connect when connected cache is empty."""
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(
        instances={PRIMARY_NAME: SandboxInstanceStatus.RUNNING},
    )
    mgr = _manager(tmp_path, client)
    try:
        # Never call ensure — cache cold; VM "running" in client.
        assert mgr.get_connected(PRIMARY_NAME) is None
        mgr.shutdown()
        assert client.stop_calls >= 1
        assert client._status.get(PRIMARY_NAME) == SandboxInstanceStatus.STOPPED
    finally:
        # bridge already shut down by first shutdown
        pass


def test_guest_readiness_pass(tmp_path: Path) -> None:
    """Full guest probes with Fake default exec → Ready."""
    _seed_minimal(tmp_path)
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client, skip_guest_readiness=False)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "ready"
        assert client.event_log.count("exec") >= 4  # four guest probes
    finally:
        mgr.shutdown()


def test_guest_readiness_fail_degraded(tmp_path: Path) -> None:
    """Forced guest exec failure → Degraded with guest reason."""
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(fail_all_exec=True)
    mgr = _manager(tmp_path, client, skip_guest_readiness=False)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "degraded"
        assert result.reason is not None
        assert result.reason.startswith("guest_")
    finally:
        mgr.shutdown()


def test_ensure_lock_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Held instance lock → Degraded lock_timeout."""
    _seed_minimal(tmp_path)
    monkeypatch.setattr(lifecycle_mod, "INSTANCE_LOCK_WAIT_SECONDS", 0.05)
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client)
    lock = mgr._instance_lock(PRIMARY_NAME)
    held = threading.Event()
    release = threading.Event()

    def _holder() -> None:
        lock.acquire()
        try:
            held.set()
            release.wait(timeout=5.0)
        finally:
            lock.release()

    holder = threading.Thread(target=_holder, daemon=True)
    holder.start()
    assert held.wait(timeout=2.0)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "degraded"
        assert result.reason == "lock_timeout"
    finally:
        release.set()
        holder.join(timeout=2.0)
        mgr.shutdown()


def test_with_ready_sandbox_lock_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """with_ready_sandbox raises EnsureLockTimeoutError.

    RLock is re-entrant on the same thread, so the holder must be another thread.
    """
    _seed_minimal(tmp_path)
    monkeypatch.setattr(lifecycle_mod, "INSTANCE_LOCK_WAIT_SECONDS", 0.05)
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client)
    lock = mgr._instance_lock(PRIMARY_NAME)
    held = threading.Event()
    release = threading.Event()

    def _holder() -> None:
        lock.acquire()
        try:
            held.set()
            release.wait(timeout=5.0)
        finally:
            lock.release()

    t = threading.Thread(target=_holder, daemon=True)
    t.start()
    assert held.wait(timeout=2.0)
    try:
        with pytest.raises(EnsureLockTimeoutError):
            with mgr.with_ready_sandbox(PRIMARY_NAME):
                pass
    finally:
        release.set()
        t.join(timeout=2.0)
        mgr.shutdown()


def test_ensure_wall_timeout(tmp_path: Path) -> None:
    """Exhausted wall budget → ensure_wall_timeout."""
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(drain_seconds=0.5)
    client.seed(PRIMARY_NAME, SandboxInstanceStatus.DRAINING)
    mgr = _manager(tmp_path, client)
    try:
        # Tiny wall so drain wait exhausts budget.
        result = mgr.ensure(PRIMARY_NAME, timeout=0.05)
        assert result.status == "degraded"
        assert result.reason == "ensure_wall_timeout"
    finally:
        mgr.shutdown()


def test_ensure_unknown_status_degraded(tmp_path: Path) -> None:
    """Edge: unknown status string → degraded, no create."""
    _seed_minimal(tmp_path)
    client = FakeSandboxClient(instances={PRIMARY_NAME: "hibernating"})
    mgr = _manager(tmp_path, client)
    try:
        result = mgr.ensure(PRIMARY_NAME)
        assert result.status == "degraded"
        assert result.reason is not None
        assert "unknown_status" in result.reason
        assert client.create_calls == 0
    finally:
        mgr.shutdown()


def test_create_kwargs_include_volume_map(tmp_path: Path) -> None:
    """Ensure create kwargs carry every MOUNT_SPEC mount (incl. media RO)."""
    from elyra.sandbox.paths import MOUNT_SPEC

    root = _seed_minimal(tmp_path)
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client)
    try:
        assert mgr.ensure(PRIMARY_NAME).ready
        kwargs = client.create_kwargs_for(PRIMARY_NAME)
        assert kwargs is not None
        volumes = kwargs.get("volumes") or {}
        assert len(volumes) == len(MOUNT_SPEC)
        for guest, host_rel, readonly in MOUNT_SPEC:
            assert guest in volumes
            assert volumes[guest]["readonly"] is readonly
            assert str(root / host_rel) in volumes[guest]["host"]
        assert volumes["/workspace/media"]["readonly"] is True
        assert volumes["/workspace/tmp"]["readonly"] is False
        assert volumes["/workspace/tools"]["readonly"] is False
    finally:
        mgr.shutdown()


def test_default_ctor_without_msb_is_client_unusable(tmp_path: Path) -> None:
    """Without microsandbox installed, default ctor marks client unusable."""
    # No client injection → try_create_real_client(); hermetic CI has no msb.
    mgr = SandboxLifecycleManager(paths=_layout(tmp_path))
    try:
        # If somehow msb is installed, client may be usable — skip assert then.
        from elyra.sandbox.client_msb import microsandbox_available

        if not microsandbox_available():
            assert mgr.client_unusable is True
            assert mgr.client is None
            result = mgr.ensure(PRIMARY_NAME)
            assert result.status == "degraded"
            assert result.reason == "client_unusable"
    finally:
        mgr.shutdown()


def test_registry_wires_lifecycle_type(tmp_path: Path) -> None:
    """Registry accepts SandboxLifecycleManager (typed surface)."""
    clear_sandbox_lifecycle()
    _seed_minimal(tmp_path)
    client = FakeSandboxClient()
    mgr = _manager(tmp_path, client)
    try:
        set_sandbox_lifecycle(mgr)
        got = get_sandbox_lifecycle()
        assert got is mgr
        assert isinstance(got, SandboxLifecycleManager)
        assert got.ensure(PRIMARY_NAME).ready
    finally:
        clear_sandbox_lifecycle()
        mgr.shutdown()
