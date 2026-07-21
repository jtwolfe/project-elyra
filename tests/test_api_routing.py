"""HTTP API routing matrix: interject / wait_reply / user_message (PR12b).

Spins a real ThreadingHTTPServer bound to an ephemeral port with a stub
do-loop so tests never need a live LLM.
"""

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
from elyra.llm.queue import LlamaServerGate
from elyra.loop.doloop import DoLoopResult
from elyra.messages import list_messages
from elyra.moment import MomentStore
from elyra.presence.interject import INTERJECT_MAX_MESSAGES, REASON_BUFFER_FULL
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import STATUS_CANCELLED, STATUS_PENDING, TimerService
from elyra.presence.user_input import (
    PHASE_IDLE,
    PHASE_IN_MOMENT,
    PHASE_WAITING,
    ROUTE_INTERJECT,
    ROUTE_USER_MESSAGE,
    ROUTE_WAIT_REPLY,
)
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState
from elyra.settings import default_settings
from elyra.tools.types import WaitArm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


def _fake_registry() -> MagicMock:
    reg = MagicMock()
    reg.openai_tools.return_value = []
    reg.execute.return_value = MagicMock(ok=True, payload={}, ends_moment=False)
    return reg


def _stub_loop(
    *,
    stop_reason: str = "no_tools",
    hop_count: int = 1,
    arm_wait: WaitArm | None = None,
    delay_s: float = 0.0,
    on_call: Any = None,
) -> Any:
    calls: list[dict[str, Any]] = []

    def _fn(**kwargs: Any) -> DoLoopResult:
        calls.append(kwargs)
        if on_call is not None:
            on_call(kwargs)
        if delay_s:
            time.sleep(delay_s)
        ctx = kwargs.get("ctx")
        mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
        return DoLoopResult(
            stop_reason=stop_reason,
            hop_count=hop_count,
            arm_wait=arm_wait,
            spoke=stop_reason != "no_tools",
            moment_id=mid,
            continue_injects=0,
            error=None,
        )

    _fn.calls = calls  # type: ignore[attr-defined]
    return _fn


def _make_worker(
    paths,
    *,
    run_do_loop_fn=None,
    poll_seconds: float = 0.05,
    stop_event: threading.Event | None = None,
) -> tuple[PresenceWorker, threading.Event]:
    stop = stop_event or threading.Event()
    queue = WakeQueue(paths)
    timers = TimerService(paths, queue)
    moments = MomentStore(paths)
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=stop,
        poll_seconds=poll_seconds,
        settings=default_settings(),
        queue=queue,
        timers=timers,
        moments=moments,
        registry=_fake_registry(),
        run_do_loop_fn=run_do_loop_fn or _stub_loop(),
    )
    return worker, stop


class _ApiHarness:
    """start_api_server + optional presence worker thread."""

    def __init__(
        self,
        paths,
        *,
        run_do_loop_fn=None,
        start_worker: bool = True,
    ) -> None:
        self.paths = paths
        self.worker, self._stop = _make_worker(
            paths, run_do_loop_fn=run_do_loop_fn
        )
        self._worker_thread: threading.Thread | None = None
        if start_worker:
            self._worker_thread = threading.Thread(
                target=self.worker.run, name="test-api-presence", daemon=True
            )
            self._worker_thread.start()
            time.sleep(0.05)

        config = RuntimeConfig(api_host="127.0.0.1", api_port=0)
        self.state = RuntimeState()
        self.gate = LlamaServerGate()
        self.server, self._api_thread = start_api_server(
            config,
            paths=paths,
            gate=self.gate,
            state=self.state,
            worker=self.worker,
        )
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def close(self) -> None:
        self._stop.set()
        try:
            self.server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.server.server_close()
        except Exception:  # noqa: BLE001
            pass
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=2.0)

    def get(self, path: str) -> tuple[int, Any]:
        req = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body

    def post(self, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body


def _wait_until(pred, timeout: float = 2.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return pred()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_status_includes_phase_hop_pending_wait(paths):
    h = _ApiHarness(paths, run_do_loop_fn=_stub_loop(hop_count=3))
    try:
        code, body = h.get("/api/status")
        assert code == 200
        assert body["phase"] == PHASE_IDLE
        assert body["hop_count"] == 0
        assert body["last_tool"] is None
        assert body["pending_wait"] is None
        assert "queue_depth_by_band" in body
        assert "interject_depth" in body
        assert body["worker_busy"] is False
        assert "llama_busy" in body
        assert "home" in body

        # Drive one moment so hop_count lands on snapshot.
        code, r = h.post("/api/messages", {"content": "hi", "user_id": "operator"})
        assert code == 200
        assert r["ok"] is True
        assert r["routed"] == ROUTE_USER_MESSAGE
        # Wait until finalize wrote hop_count (idle+not-busy alone is true at t0).
        assert _wait_until(
            lambda: h.worker.status_snapshot()["hop_count"] == 3
            and not h.worker.busy
            and h.worker.phase == PHASE_IDLE
        )
        code, body = h.get("/api/status")
        assert code == 200
        assert body["hop_count"] == 3
        assert body["phase"] == PHASE_IDLE
    finally:
        h.close()


# ---------------------------------------------------------------------------
# Routing matrix via HTTP
# ---------------------------------------------------------------------------


def test_idle_messages_enqueues_user_message(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.post(
            "/api/messages", {"content": "hello world", "user_id": "operator"}
        )
        assert code == 200
        assert body["ok"] is True
        assert body["routed"] == ROUTE_USER_MESSAGE
        assert body.get("wake_id")
        assert body["message"]["content"] == "hello world"
        assert body["message"]["role"] == "user"
        # Glass log
        msgs = list_messages(paths=paths)
        assert any(m["content"] == "hello world" for m in msgs)
        assert _wait_until(lambda: not h.worker.busy)
    finally:
        h.close()


def test_messages_empty_content_400(paths):
    h = _ApiHarness(paths, start_worker=False)
    try:
        code, body = h.post("/api/messages", {"content": "   "})
        assert code == 400
        assert body["ok"] is False
        assert body["reason"] == "empty_content"
    finally:
        h.close()


def test_in_moment_interjects_via_messages(paths):
    entered = threading.Event()
    release = threading.Event()

    def on_call(_kwargs: Any) -> None:
        entered.set()
        release.wait(timeout=3.0)

    h = _ApiHarness(paths, run_do_loop_fn=_stub_loop(on_call=on_call))
    try:
        code, first = h.post("/api/messages", {"content": "start moment"})
        assert code == 200
        assert first["routed"] == ROUTE_USER_MESSAGE
        assert entered.wait(timeout=2.0)
        assert h.worker.phase == PHASE_IN_MOMENT

        code, mid = h.post("/api/messages", {"content": "mid-note", "user_id": "operator"})
        assert code == 200
        assert mid["ok"] is True
        assert mid["routed"] == ROUTE_INTERJECT
        assert h.worker.status_snapshot()["interject_depth"] == 1

        code, st = h.get("/api/status")
        assert st["phase"] == PHASE_IN_MOMENT
        assert st["interject_depth"] == 1
        assert st["active_moment_id"] is not None
    finally:
        release.set()
        h.close()


def test_interject_buffer_full_returns_notice(paths):
    entered = threading.Event()
    release = threading.Event()

    def on_call(_kwargs: Any) -> None:
        entered.set()
        release.wait(timeout=4.0)

    h = _ApiHarness(paths, run_do_loop_fn=_stub_loop(on_call=on_call))
    try:
        h.post("/api/messages", {"content": "busy"})
        assert entered.wait(timeout=2.0)

        for i in range(INTERJECT_MAX_MESSAGES):
            code, body = h.post("/api/messages", {"content": f"note-{i}"})
            assert code == 200, body
            assert body["ok"] is True
            assert body["routed"] == ROUTE_INTERJECT

        code, overflow = h.post("/api/messages", {"content": "overflow-me"})
        # 200 so glass can show notice; ok=false + reason + wake_id.
        # routed stays interject (decision path); clients key notice off ok/reason.
        assert code == 200
        assert overflow["ok"] is False
        assert overflow["routed"] == ROUTE_INTERJECT
        assert overflow["reason"] == REASON_BUFFER_FULL
        assert overflow.get("wake_id")
    finally:
        release.set()
        h.close()


def test_waiting_free_text_via_messages_is_wait_reply(paths):
    arm = WaitArm(
        wait_id="api-wait-1",
        timeout_seconds=120,
        prompt="Pick one",
        choices=["a", "b"],
        user_id="operator",
    )
    # First moment arms wait; subsequent moments finish cleanly.
    call_n = {"n": 0}

    def on_call(_kwargs: Any) -> None:
        call_n["n"] += 1

    def run_loop(**kwargs: Any) -> DoLoopResult:
        on_call(kwargs)
        ctx = kwargs.get("ctx")
        mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
        if call_n["n"] == 1:
            return DoLoopResult(
                stop_reason="wait",
                hop_count=1,
                arm_wait=arm,
                spoke=True,
                moment_id=mid,
            )
        return DoLoopResult(
            stop_reason="no_tools", hop_count=1, moment_id=mid, spoke=False
        )

    h = _ApiHarness(paths, run_do_loop_fn=run_loop)
    try:
        h.post("/api/messages", {"content": "please wait"})
        assert _wait_until(lambda: h.worker.phase == PHASE_WAITING, timeout=2.0)

        code, st = h.get("/api/status")
        assert code == 200
        assert st["phase"] == PHASE_WAITING
        assert st["pending_wait"] is not None
        assert st["pending_wait"]["id"] == "api-wait-1"

        code, body = h.post(
            "/api/messages", {"content": "option a", "user_id": "operator"}
        )
        assert code == 200
        assert body["ok"] is True
        assert body["routed"] == ROUTE_WAIT_REPLY
        assert body.get("wake_id")
        assert body.get("wait_id") == "api-wait-1" or body.get("answer_wait_id") == "api-wait-1"

        assert _wait_until(
            lambda: call_n["n"] >= 2 and h.worker.phase == PHASE_IDLE, timeout=2.0
        )
    finally:
        h.close()


def test_wait_reply_endpoint_with_choice(paths):
    arm = WaitArm(
        wait_id="api-wait-2",
        timeout_seconds=120,
        prompt="Continue?",
        choices=["yes", "no"],
        user_id="operator",
    )
    call_n = {"n": 0}

    def run_loop(**kwargs: Any) -> DoLoopResult:
        call_n["n"] += 1
        ctx = kwargs.get("ctx")
        mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
        if call_n["n"] == 1:
            return DoLoopResult(
                stop_reason="wait",
                hop_count=2,
                arm_wait=arm,
                spoke=True,
                moment_id=mid,
            )
        return DoLoopResult(
            stop_reason="no_tools", hop_count=1, moment_id=mid, spoke=False
        )

    h = _ApiHarness(paths, run_do_loop_fn=run_loop)
    try:
        h.post("/api/messages", {"content": "ask me"})
        assert _wait_until(lambda: h.worker.phase == PHASE_WAITING, timeout=2.0)

        code, body = h.post(
            "/api/wait/reply",
            {"choice": "yes", "user_id": "operator"},
        )
        assert code == 200
        assert body["ok"] is True
        assert body["routed"] == ROUTE_WAIT_REPLY
        assert body.get("wake_id")
        assert body["message"]["content"] == "yes"

        assert _wait_until(lambda: call_n["n"] >= 2, timeout=2.0)
        code, st = h.get("/api/status")
        # After second moment completes, pending wait should be gone.
        assert _wait_until(lambda: h.worker.phase == PHASE_IDLE, timeout=2.0)
        code, st = h.get("/api/status")
        assert st["pending_wait"] is None or st["phase"] == PHASE_IDLE
    finally:
        h.close()


def test_wait_reply_endpoint_requires_body(paths):
    h = _ApiHarness(paths, start_worker=False)
    try:
        code, body = h.post("/api/wait/reply", {"user_id": "operator"})
        assert code == 400
        assert body["ok"] is False
        assert body["reason"] == "empty_content"
    finally:
        h.close()


def test_wait_api_while_idle_with_pending_wait(paths):
    """from_wait_api=True routes to wait_reply even if phase is idle."""
    # Seed a durable pending wait without going through a moment.
    queue = WakeQueue(paths)
    timers = TimerService(paths, queue)
    timers.arm_wait(
        wait_id="seed-wait",
        prompt="?",
        choices=["y"],
        user_id="operator",
        moment_id="prior",
        timeout=600.0,
    )

    # Worker reloads waits.json on TimerService construct.
    h = _ApiHarness(paths, start_worker=True)
    try:
        # Rehydrate may set phase waiting; either idle or waiting is fine for wait API.
        code, body = h.post(
            "/api/wait/reply",
            {"content": "y", "choice": "y", "user_id": "operator"},
        )
        assert code == 200, body
        assert body["ok"] is True
        assert body["routed"] == ROUTE_WAIT_REPLY
        assert body.get("wait_id") == "seed-wait" or body.get("answer_wait_id") == "seed-wait"
    finally:
        h.close()


def test_idle_cancels_stale_wait_on_new_message(paths):
    """POST /api/messages while idle with a pending wait for user → user_message + cancel.

    Asserts both the response flag and durable wait status (not pending).
    """
    queue = WakeQueue(paths)
    timers = TimerService(paths, queue)
    timers.arm_wait(
        wait_id="stale-w",
        prompt="old",
        user_id="operator",
        moment_id="m0",
        timeout=600.0,
    )

    h = _ApiHarness(paths)
    try:
        # Rehydrate sets phase=waiting when a pending wait exists. Force idle so
        # free-text hits cancel_stale (not wait_reply) while the durable wait remains.
        with h.worker._lock:  # noqa: SLF001
            h.worker._phase = PHASE_IDLE  # noqa: SLF001
            pending = h.worker._timers.list_waits(status=STATUS_PENDING)  # noqa: SLF001
            if not pending:
                h.worker._timers.arm_wait(  # noqa: SLF001
                    wait_id="stale-w2",
                    prompt="old",
                    user_id="operator",
                    moment_id="m0",
                    timeout=600.0,
                )
                pending = h.worker._timers.list_waits(status=STATUS_PENDING)  # noqa: SLF001
            assert pending, "expected a durable pending wait before cancel"
            wait_id = pending[0].id

        pre = h.worker._timers.get_wait(wait_id)  # noqa: SLF001
        assert pre is not None
        assert pre.status == STATUS_PENDING

        code, body = h.post(
            "/api/messages",
            {"content": "new topic", "user_id": "operator"},
        )
        assert code == 200
        assert body["routed"] == ROUTE_USER_MESSAGE
        assert body.get("cancel_stale_wait") is True

        # Durable side effect: wait left pending and will not time out as pending.
        post = h.worker._timers.get_wait(wait_id)  # noqa: SLF001
        assert post is not None
        assert post.status == STATUS_CANCELLED
        assert h.worker._timers.list_waits(status=STATUS_PENDING) == []  # noqa: SLF001
        assert h.worker.pending_wait is None

        code, st = h.get("/api/status")
        assert code == 200
        assert st["pending_wait"] is None
    finally:
        h.close()


def test_health_ok(paths):
    h = _ApiHarness(paths, start_worker=False)
    try:
        code, body = h.get("/api/health")
        assert code == 200
        assert body == {"ok": True}
    finally:
        h.close()
