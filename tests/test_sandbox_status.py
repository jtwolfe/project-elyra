"""H2c: sandbox status block + async warm + FS cutover hermetic tests."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.llm.client import StubChatClient
from elyra.llm.queue import ChatRequestGate
from elyra.loop.doloop import DoLoopResult
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import TimerService
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState
from elyra.sandbox import (
    FakeSandboxClient,
    Sandbox,
    SandboxLifecycleManager,
    clear_sandbox_lifecycle,
    get_sandbox_lifecycle,
    sandbox_status_block,
    set_sandbox_lifecycle,
)
from elyra.sandbox.paths import PRIMARY_NAME, host_root_for
from elyra.sandbox.status import PILL_OFF, PILL_READY, PILL_UNUSABLE, PILL_WARMING
from elyra.settings import default_settings


@pytest.fixture(autouse=True)
def _clean_registry() -> Any:
    clear_sandbox_lifecycle()
    yield
    clear_sandbox_lifecycle()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ELYRA_SANDBOX", "0")
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


def test_status_block_isolation_off(home: Path) -> None:
    paths = resolve_paths(home)
    life = SandboxLifecycleManager(paths=paths, client_unusable=True)
    set_sandbox_lifecycle(life)
    block = sandbox_status_block(paths)
    assert block["isolation_enabled"] is False
    assert block["ready"] is False
    assert block["mount_ready"] is False
    assert block["pyenv_ready"] is False
    assert block["reason"] == "isolation_disabled"
    assert block["pill"] == PILL_OFF
    assert block["lifecycle_registered"] is True
    assert "secret" not in json.dumps(block).lower()
    # No absolute host paths in status values (host_tree_exists is bool only).
    for k, v in block.items():
        if isinstance(v, str):
            assert not v.startswith("/home"), k


def test_status_block_client_unusable_isolation_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ELYRA_SANDBOX", raising=False)
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    from elyra.sandbox.paths import ensure_host_tree

    ensure_host_tree(PRIMARY_NAME, paths)
    life = SandboxLifecycleManager(paths=paths, client_unusable=True)
    set_sandbox_lifecycle(life)
    block = sandbox_status_block(paths)
    assert block["isolation_enabled"] is True
    assert block["client_unusable"] is True
    assert block["ready"] is False
    assert block["reason"] == "client_unusable"
    assert block["pill"] == PILL_UNUSABLE
    assert block["host_tree_exists"] is True
    assert block["network_policy"] in {"none", "public_only", "allow_all"}


def test_status_block_warming_then_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ELYRA_SANDBOX", raising=False)
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    client = FakeSandboxClient()
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    # Before ensure: warming
    block = sandbox_status_block(paths, warm_done=False)
    assert block["reason"] == "warming"
    assert block["pill"] == PILL_WARMING
    assert block["ready"] is False

    result = life.ensure(PRIMARY_NAME)
    assert result.ready is True
    block2 = sandbox_status_block(paths)
    # H3b: product ready requires pyenv_ready as well as mount_ready.
    assert block2["mount_ready"] is True
    assert block2["pyenv_ready"] is False
    assert block2["ready"] is False
    assert block2["reason"] == "pyenv_not_ready"
    assert block2["pill"] == PILL_WARMING


def test_status_block_pyenv_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ELYRA_SANDBOX", raising=False)
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    client = FakeSandboxClient()
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    set_sandbox_lifecycle(life)
    life.ensure(PRIMARY_NAME)
    root = host_root_for(PRIMARY_NAME, paths)
    marker = root / ".elyra_pyenv_ready"
    marker.write_text("ok\n", encoding="utf-8")
    block = sandbox_status_block(paths)
    assert block["pyenv_ready"] is True
    assert block["mount_ready"] is True
    assert block["ready"] is True  # H3b: mount + pyenv
    assert block["reason"] is None
    assert block["pill"] == PILL_READY


def test_product_sandbox_sees_seed_layout(home: Path) -> None:
    paths = resolve_paths(home)
    sb = Sandbox(paths)
    sb.ensure_root()
    names = sb.list_dir(".")
    for d in ("lib", "general", "fixtures", "tmp", "tools"):
        assert d in names
    # Guest path alias
    assert "lib" in sb.list_dir("/workspace")


def test_worker_ensure_sandbox_uses_sandbox0(home: Path) -> None:
    paths = resolve_paths(home)
    stop = threading.Event()
    w = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=stop,
        settings=default_settings(),
        queue=WakeQueue(paths),
        timers=TimerService(paths, WakeQueue(paths)),
        registry=MagicMock(),
    )
    sb = w._ensure_sandbox()  # noqa: SLF001
    assert sb.root.name == "sandbox0"
    assert (sb.root / "tmp").is_dir()


def _stub_loop(**kwargs: Any) -> DoLoopResult:
    ctx = kwargs.get("ctx")
    mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
    return DoLoopResult(
        stop_reason="no_tools",
        hop_count=1,
        moment_id=mid,
        spoke=False,
    )


def test_api_status_includes_sandbox_block(home: Path) -> None:
    paths = resolve_paths(home)
    stop = threading.Event()
    queue = WakeQueue(paths)
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=stop,
        poll_seconds=0.05,
        settings=default_settings(),
        queue=queue,
        timers=TimerService(paths, queue),
        registry=MagicMock(
            openai_tools=MagicMock(return_value=[]),
            execute=MagicMock(
                return_value=MagicMock(ok=True, payload={}, ends_moment=False)
            ),
        ),
        run_do_loop_fn=_stub_loop,
    )
    life = SandboxLifecycleManager(paths=paths, client_unusable=True)
    set_sandbox_lifecycle(life)
    config = RuntimeConfig(api_host="127.0.0.1", api_port=0)
    state = RuntimeState()
    gate = ChatRequestGate()
    server, _thread = start_api_server(
        config,
        paths=paths,
        gate=gate,
        state=state,
        worker=worker,
    )
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"
    try:
        req = urllib.request.Request(base + "/api/status", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        assert "sandbox" in body
        sb = body["sandbox"]
        assert sb["name"] == "sandbox0"
        assert "isolation_enabled" in sb
        assert "mount_ready" in sb
        assert "pyenv_ready" in sb
        assert "pill" in sb
        # No secrets
        raw = json.dumps(sb)
        assert "api_key" not in raw
        assert "token" not in raw.lower()
        assert "bearer" not in raw.lower()
    finally:
        stop.set()
        server.shutdown()
        server.server_close()


def test_supervisor_async_warm_does_not_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """start() returns without waiting for multi-minute ensure (KD23)."""
    monkeypatch.delenv("ELYRA_SANDBOX", raising=False)
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()

    client = FakeSandboxClient()
    # Slow ensure via monkeypatch
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    original_ensure = life.ensure
    started = threading.Event()
    release = threading.Event()

    def slow_ensure(name: str = PRIMARY_NAME, **kwargs: Any) -> Any:
        started.set()
        release.wait(timeout=5)
        return original_ensure(name, **kwargs)

    life.ensure = slow_ensure  # type: ignore[method-assign]

    from elyra.runtime.supervisor import ElyraSupervisor

    sup = ElyraSupervisor(
        paths=paths,
        config=RuntimeConfig(api_host="127.0.0.1", api_port=0, start_llama_server=False),
        use_stub_llm=True,
        sandbox_lifecycle=life,
    )
    t0 = time.monotonic()
    try:
        sup.start()
        elapsed = time.monotonic() - t0
        # Must not block on warm (slow ensure still waiting).
        assert elapsed < 2.0
        assert get_sandbox_lifecycle() is life
        assert started.wait(timeout=2.0)
        block = sup.sandbox_status()
        assert block["reason"] == "warming" or block["pill"] == PILL_WARMING
        release.set()
        # Let warm finish
        if sup._sandbox_warm_thread is not None:  # noqa: SLF001
            sup._sandbox_warm_thread.join(timeout=5)
        block2 = sup.sandbox_status()
        assert block2["ready"] is True
        assert block2["mount_ready"] is True
    finally:
        release.set()
        sup.shutdown()
        assert get_sandbox_lifecycle() is None


def test_supervisor_shutdown_worker_before_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ELYRA_SANDBOX", "0")
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    order: list[str] = []

    client = FakeSandboxClient()
    life = SandboxLifecycleManager(
        paths=paths, client=client, skip_guest_readiness=True
    )
    real_shutdown = life.shutdown

    def tracked_shutdown(*a: Any, **k: Any) -> None:
        order.append("sandbox_shutdown")
        real_shutdown(*a, **k)

    life.shutdown = tracked_shutdown  # type: ignore[method-assign]

    from elyra.runtime.supervisor import ElyraSupervisor

    sup = ElyraSupervisor(
        paths=paths,
        config=RuntimeConfig(api_host="127.0.0.1", api_port=0, start_llama_server=False),
        use_stub_llm=True,
        sandbox_lifecycle=life,
    )
    sup.start()
    # Patch worker join to record order
    wt = sup._worker_thread  # noqa: SLF001
    if wt is not None:
        real_join = wt.join

        def tracked_join(*a: Any, **k: Any) -> None:
            order.append("worker_join")
            real_join(*a, **k)

        wt.join = tracked_join  # type: ignore[method-assign]
    sup.shutdown()
    assert "worker_join" in order
    assert "sandbox_shutdown" in order
    assert order.index("worker_join") < order.index("sandbox_shutdown")
    assert get_sandbox_lifecycle() is None


def test_no_orient_sandbox_line() -> None:
    """KD26: orient template must not inject sandbox ready/warming/unusable."""
    from elyra.prompts.loader import load_prompt

    orient = load_prompt("orient")
    lower = orient.lower()
    # No dedicated sandbox status placeholders / lines.
    assert "sandbox_ready" not in lower
    assert "mount_ready" not in lower
    assert "{{sandbox" not in lower
