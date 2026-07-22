"""Full reset: path clears, worker port, API confirm, concurrent guards (PR8)."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elyra.config import resolve_paths
from elyra.goals import GoalsStore
from elyra.identity import IdentityStore
from elyra.llm.client import StubChatClient
from elyra.llm.queue import LlamaServerGate
from elyra.loop.doloop import DoLoopResult
from elyra.messages import append_message, list_messages
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import STATUS_SCHEDULED, TimerService
from elyra.presence.user_input import PHASE_IN_MOMENT
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.reset import (
    clear_goals,
    clear_messages,
    clear_moments,
    clear_sandbox,
    clear_tool_drafts,
    clear_wakes_disk,
    normalize_reset_flags,
)
from elyra.runtime.state import RuntimeState
from elyra.settings import default_settings
from elyra.users import UsersStore


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


def _stub_loop(**kwargs: Any) -> DoLoopResult:
    ctx = kwargs.get("ctx")
    mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
    return DoLoopResult(
        stop_reason="no_tools",
        hop_count=1,
        moment_id=mid,
        spoke=False,
    )


def _worker(paths) -> PresenceWorker:
    queue = WakeQueue(paths)
    timers = TimerService(paths, queue)
    return PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=threading.Event(),
        poll_seconds=0.05,
        settings=default_settings(),
        queue=queue,
        timers=timers,
        moments=MomentStore(paths),
        registry=_fake_registry(),
        goals=GoalsStore(paths),
        run_do_loop_fn=_stub_loop,
    )


class _ApiHarness:
    def __init__(self, paths) -> None:
        self.paths = paths
        stop = threading.Event()
        queue = WakeQueue(paths)
        timers = TimerService(paths, queue)
        moments = MomentStore(paths)
        goals = GoalsStore(paths)
        self.worker = PresenceWorker(
            paths=paths,
            client=StubChatClient(),
            stop_event=stop,
            poll_seconds=0.05,
            settings=default_settings(),
            queue=queue,
            timers=timers,
            moments=moments,
            registry=_fake_registry(),
            goals=goals,
            run_do_loop_fn=_stub_loop,
        )
        self._stop = stop
        config = RuntimeConfig(api_host="127.0.0.1", api_port=0)
        self.state = RuntimeState()
        self.gate = LlamaServerGate()
        self.server, self._api_thread = start_api_server(
            config,
            paths=paths,
            gate=self.gate,
            state=self.state,
            worker=self.worker,
            goals=goals,
            moments=moments,
            identity=IdentityStore(paths),
            users=UsersStore(paths),
            tools=None,
            skills=None,
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


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def test_normalize_reset_flags_defaults_and_ignores_skills():
    flags = normalize_reset_flags(None)
    assert flags["clear_sandbox"] is True
    assert flags["clear_drafts"] is True
    assert flags["clear_local_tools"] is False
    assert "clear_local_skills" not in flags

    flags2 = normalize_reset_flags(
        {"clear_sandbox": False, "clear_local_skills": True, "clear_drafts": False}
    )
    assert flags2["clear_sandbox"] is False
    assert flags2["clear_drafts"] is False
    assert "clear_local_skills" not in flags2


def test_clear_helpers_preserve_identity_users_skills_local(paths):
    # Seed ephemeral + preserved product.
    append_message("user", "hello", paths=paths)
    goals = GoalsStore(paths)
    goals.create_goal("g1")
    moments = MomentStore(paths)
    mid = moments.open_moment(why_now="test")
    moments.close_moment(mid, "no_tools", hop_count=1)

    sandbox = paths.data_dir / "sandbox" / "work.txt"
    sandbox.write_text("scratch", encoding="utf-8")
    drafts = paths.tools_dir / "drafts" / "mytool"
    drafts.mkdir(parents=True)
    (drafts / "TOOL.md").write_text("# draft", encoding="utf-8")

    skill = paths.skills_dir / "local" / "my-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# keep me", encoding="utf-8")

    identity = paths.data_dir / "identity" / "self.md"
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_text("# self keep", encoding="utf-8")
    user_prof = paths.data_dir / "users" / "operator" / "profile.md"
    user_prof.parent.mkdir(parents=True, exist_ok=True)
    user_prof.write_text("# user keep", encoding="utf-8")

    cont = paths.data_dir / "runtime" / "continuous.json"
    cont.parent.mkdir(parents=True, exist_ok=True)
    cont.write_text('{"enabled": true}\n', encoding="utf-8")

    local_tool = paths.tools_dir / "local" / "promoted"
    local_tool.mkdir(parents=True)
    (local_tool / "TOOL.md").write_text("# local keep", encoding="utf-8")

    clear_wakes_disk(paths)
    clear_moments(paths)
    clear_messages(paths)
    clear_goals(paths)
    clear_sandbox(paths)
    clear_tool_drafts(paths)

    assert list_messages(paths=paths) == []
    assert GoalsStore(paths).list_goals() == []
    assert MomentStore(paths).list_moments() == []
    assert not sandbox.exists()
    assert not (paths.tools_dir / "drafts" / "mytool").exists()
    assert (paths.tools_dir / "drafts").is_dir()

    # Preserved
    assert identity.read_text(encoding="utf-8") == "# self keep"
    assert user_prof.read_text(encoding="utf-8") == "# user keep"
    assert cont.read_text(encoding="utf-8").strip() == '{"enabled": true}'
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == "# keep me"
    assert (local_tool / "TOOL.md").read_text(encoding="utf-8") == "# local keep"


# ---------------------------------------------------------------------------
# Worker port
# ---------------------------------------------------------------------------


def test_reset_clears_timer_memory_no_refire(paths):
    """TimerService maps cleared — schedule_due after reset fires nothing."""
    w = _worker(paths)
    past = "2020-01-01T00:00:00Z"
    w._timers.schedule_timer(past, reason="stale")  # noqa: SLF001
    assert w._timers.list_timers(status=STATUS_SCHEDULED)  # noqa: SLF001

    # Also arm a wait so memory + disk both have rows.
    w._timers.arm_wait(  # noqa: SLF001
        prompt="?",
        user_id="operator",
        moment_id="m0",
        timeout=3600.0,
    )
    w._queue.enqueue("timer", {"reason": "pending-wake"})  # noqa: SLF001
    w._queue.enqueue("task_ready", {"task_id": "t1"})  # noqa: SLF001

    # Streak fields should zero; enabled preserved.
    w.set_continuous_enabled(True)
    w._continuous.streak = 3  # noqa: SLF001
    w._continuous.last_continue_wake_id = "wc1"  # noqa: SLF001

    out = w.reset_runtime_state()
    assert out["ok"] is True
    assert "wakes" in out["cleared"]
    assert "moments" in out["cleared"]
    assert out["phase"] == "idle"

    assert w._timers.list_timers(status=STATUS_SCHEDULED) == []  # noqa: SLF001
    assert w._timers.list_waits() == []  # noqa: SLF001
    assert w._queue.pending() == []  # noqa: SLF001
    assert w._queue.claimed() == []  # noqa: SLF001

    # No re-fire from ghost memory even with far-future "now".
    fired = w._timers.schedule_due(now="2030-01-01T00:00:00Z")  # noqa: SLF001
    assert fired == []
    assert w._queue.pending() == []  # noqa: SLF001

    assert w._continuous.enabled is True  # noqa: SLF001
    assert w._continuous.streak == 0  # noqa: SLF001
    assert w._continuous.last_continue_wake_id is None  # noqa: SLF001
    # continuous.json enabled preserved on disk
    cont_path = paths.data_dir / "runtime" / "continuous.json"
    assert cont_path.is_file()
    assert json.loads(cont_path.read_text(encoding="utf-8"))["enabled"] is True


def test_reset_busy_returns_worker_busy(paths):
    w = _worker(paths)
    with w._lock:  # noqa: SLF001
        w._busy = True  # noqa: SLF001
        w._phase = PHASE_IN_MOMENT  # noqa: SLF001
    out = w.reset_runtime_state()
    assert out["ok"] is False
    assert out["error"] == "worker_busy"
    assert out["phase"] == PHASE_IN_MOMENT


def test_reset_busy_only_flag_still_rejects(paths):
    w = _worker(paths)
    with w._lock:  # noqa: SLF001
        w._busy = True  # noqa: SLF001
        # phase may still read idle in edge cases
    out = w.reset_runtime_state()
    assert out["ok"] is False
    assert out["error"] == "worker_busy"


def test_concurrent_ops_while_resetting(paths):
    """While resetting flag is set, resolve/enqueue surface error=resetting."""
    w = _worker(paths)
    with w._lock:  # noqa: SLF001
        w._continuous.resetting = True  # noqa: SLF001

    r = w.resolve_user_input("hi")
    assert r["ok"] is False
    assert r["error"] == "resetting"

    with pytest.raises(RuntimeError, match="resetting"):
        w.enqueue_wake("background", {})

    cont = w.set_continuous_enabled(True)
    assert cont["ok"] is False
    assert cont["error"] == "resetting"

    # Second reset while flag set
    out = w.reset_runtime_state()
    assert out["ok"] is False
    assert out["error"] == "resetting"

    with w._lock:  # noqa: SLF001
        w._continuous.resetting = False  # noqa: SLF001


def test_partial_reset_shape(paths):
    """If a step fails, body is partial_reset with cleared + errors."""
    w = _worker(paths)
    append_message("user", "x", paths=paths)

    with patch(
        "elyra.presence.worker.clear_moments",
        side_effect=OSError("disk full"),
    ):
        out = w.reset_runtime_state()

    assert out["ok"] is False
    assert out["error"] == "partial_reset"
    assert "errors" in out
    assert any(e["step"] == "moments" for e in out["errors"])
    # messages may still have cleared depending on order; wakes before moments.
    assert "wakes" in out["cleared"]
    assert "cleared" in out


def test_reset_never_clears_skills_local(paths):
    w = _worker(paths)
    skill = paths.skills_dir / "local" / "keep"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("stay", encoding="utf-8")

    out = w.reset_runtime_state({"clear_local_skills": True})  # type: ignore[arg-type]
    assert out["ok"] is True
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == "stay"
    assert "local_skills" not in out["cleared"]


def test_reset_queue_empty_and_messages_gone(paths):
    w = _worker(paths)
    append_message("assistant", "bye", paths=paths)
    w._queue.enqueue("user_message", {"content": "a", "user_id": "operator"})  # noqa: SLF001
    goals = GoalsStore(paths)
    goals.create_goal("wipe me")

    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat().replace(
        "+00:00", "Z"
    )
    w._timers.schedule_timer(future, reason="later")  # noqa: SLF001

    out = w.reset_runtime_state()
    assert out["ok"] is True
    assert list_messages(paths=paths) == []
    assert GoalsStore(paths).list_goals() == []
    assert w._queue.pending() == []  # noqa: SLF001
    assert w.pending_wait is None
    assert w.phase == "idle"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_api_reset_success(paths):
    h = _ApiHarness(paths)
    try:
        append_message("user", "chat", paths=paths)
        h.worker._queue.enqueue("background", {"n": 1})  # noqa: SLF001

        code, body = h.post("/api/reset", {"confirm": "RESET"})
        assert code == 200, body
        assert body["ok"] is True
        assert "moments" in body["cleared"]
        assert "messages" in body["cleared"]
        assert "wakes" in body["cleared"]
        assert list_messages(paths=paths) == []
        assert h.worker._queue.pending() == []  # noqa: SLF001

        code, st = h.get("/api/status")
        assert code == 200
        assert st["phase"] == "idle"
        assert st.get("resetting") is False
    finally:
        h.close()


def test_api_reset_missing_confirm_400(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.post("/api/reset", {})
        assert code == 400
        assert body["ok"] is False
        assert "confirm" in body["error"]

        code, body = h.post("/api/reset", {"confirm": "reset"})
        assert code == 400
    finally:
        h.close()


def test_api_reset_busy_409(paths):
    h = _ApiHarness(paths)
    try:
        with h.worker._lock:  # noqa: SLF001
            h.worker._busy = True  # noqa: SLF001
            h.worker._phase = PHASE_IN_MOMENT  # noqa: SLF001
        code, body = h.post("/api/reset", {"confirm": "RESET"})
        assert code == 409, body
        assert body["ok"] is False
        assert body["error"] == "worker_busy"
        assert body["phase"] == PHASE_IN_MOMENT
    finally:
        h.close()


def test_api_resetting_503_on_messages(paths):
    h = _ApiHarness(paths)
    try:
        with h.worker._lock:  # noqa: SLF001
            h.worker._continuous.resetting = True  # noqa: SLF001
        code, body = h.post(
            "/api/messages",
            {"content": "hello", "user_id": "operator"},
        )
        assert code == 503, body
        assert body.get("error") == "resetting" or body.get("reason") == "resetting"
    finally:
        with h.worker._lock:  # noqa: SLF001
            h.worker._continuous.resetting = False  # noqa: SLF001
        h.close()


def test_api_second_reset_while_resetting_503(paths):
    h = _ApiHarness(paths)
    try:
        with h.worker._lock:  # noqa: SLF001
            h.worker._continuous.resetting = True  # noqa: SLF001
        code, body = h.post("/api/reset", {"confirm": "RESET"})
        assert code == 503, body
        assert body["error"] == "resetting"
    finally:
        with h.worker._lock:  # noqa: SLF001
            h.worker._continuous.resetting = False  # noqa: SLF001
        h.close()


def test_api_partial_reset_500(paths):
    h = _ApiHarness(paths)
    try:
        with patch(
            "elyra.presence.worker.clear_goals",
            side_effect=OSError("boom"),
        ):
            code, body = h.post("/api/reset", {"confirm": "RESET"})
        assert code == 500, body
        assert body["ok"] is False
        assert body["error"] == "partial_reset"
        assert "errors" in body
        assert "cleared" in body
        assert any(e["step"] == "goals" for e in body["errors"])
    finally:
        h.close()


def test_timer_clear_all_unit(paths):
    q = WakeQueue(paths)
    svc = TimerService(paths, q)
    svc.schedule_timer("2020-01-01T00:00:00Z", reason="x")
    svc.arm_wait(
        prompt="p",
        user_id="operator",
        moment_id="m",
        timeout=10.0,
    )
    assert svc.list_timers(status=STATUS_SCHEDULED)
    assert svc.list_waits()
    svc.clear_all()
    assert svc.list_timers(status=None) == []
    assert svc.list_waits(status=None) == []
    raw_t = json.loads(svc.timers_path.read_text(encoding="utf-8"))
    raw_w = json.loads(svc.waits_path.read_text(encoding="utf-8"))
    assert raw_t == []
    assert raw_w == []
    # Rehydrate from empty disk does not invent fires
    assert svc.rehydrate(now="2030-01-01T00:00:00Z") == []
