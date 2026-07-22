"""Glass catalog endpoints: goals, moments, tools, skills, identity (PR14)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.goals import GoalsStore
from elyra.identity import IdentityStore
from elyra.llm.client import StubChatClient
from elyra.llm.queue import LlamaServerGate
from elyra.loop.doloop import DoLoopResult
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import TimerService
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState
from elyra.settings import default_settings
from elyra.skills.catalog import SkillCatalog
from elyra.tools.registry import ToolRegistry
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
        # Real catalogs when bundled roots exist (editable tree); else empty.
        tools: ToolRegistry | None
        skills: SkillCatalog | None
        try:
            tools = ToolRegistry(paths)
        except Exception:  # noqa: BLE001
            tools = None
        try:
            skills = SkillCatalog(paths)
        except Exception:  # noqa: BLE001
            skills = None
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
            tools=tools,
            skills=skills,
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

    def patch(self, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method="PATCH",
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


def test_get_goals_empty_then_create(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/goals")
        assert code == 200
        assert body["goals"] == []

        code, created = h.post(
            "/api/goals",
            {"title": "Ship PR14", "acceptance": "glass panels work"},
        )
        assert code == 200, created
        assert created["ok"] is True
        assert created["goal"]["title"] == "Ship PR14"
        assert created["goal"]["status"] == "open"

        code, body = h.get("/api/goals")
        assert code == 200
        assert len(body["goals"]) == 1
        assert body["goals"][0]["title"] == "Ship PR14"
        assert body["goals"][0]["tasks"] == []
    finally:
        h.close()


def test_post_goals_requires_title(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.post("/api/goals", {"title": "  "})
        assert code == 400
        assert body["ok"] is False
    finally:
        h.close()


def test_get_moments_and_detail(paths):
    h = _ApiHarness(paths)
    try:
        store = MomentStore(paths)
        mid = store.open_moment(why_now="user_message:test", user_id="operator")
        store.append_beat(
            mid,
            {
                "type": "model",
                "content": "thinking aloud",
                "reasoning": "private chain of thought",
            },
        )
        store.append_beat(
            mid,
            {"type": "tool", "name": "speak", "ok": True},
        )
        store.close_moment(mid, "no_tools", hop_count=2)

        code, body = h.get("/api/moments?limit=10")
        assert code == 200
        assert len(body["moments"]) == 1
        assert body["moments"][0]["id"] == mid
        assert body["moments"][0]["why_now"] == "user_message:test"
        assert body["moments"][0]["stop_reason"] == "no_tools"

        # Negative limit clamps to empty list (not "all").
        code, empty = h.get("/api/moments?limit=-1")
        assert code == 200
        assert empty["moments"] == []

        code, detail = h.get(f"/api/moments/{mid}")
        assert code == 200
        assert detail["moment"]["id"] == mid
        assert len(detail["beats"]) == 2
        assert detail["beats"][0]["reasoning"] == "private chain of thought"
        assert detail["beats"][1]["name"] == "speak"
    finally:
        h.close()


def test_get_moment_not_found(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/moments/does-not-exist-xyz")
        assert code == 404
        assert body["ok"] is False
    finally:
        h.close()


def test_get_moment_invalid_id(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/moments/!not-valid")
        assert code == 400
        assert body["ok"] is False
    finally:
        h.close()


def test_get_identity_and_user(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/identity")
        assert code == 200
        assert "self" in body
        assert "digest" in body["self"]
        # Seeded by ensure_data_dirs
        assert isinstance(body["self"]["digest"], str)
        assert body["self"]["digest"]  # non-empty seed

        code, user = h.get("/api/users/operator")
        assert code == 200
        assert user["user_id"] == "operator"
        assert isinstance(user["profile"], str)
        assert user["profile"]  # seeded operator profile
    finally:
        h.close()


def test_get_user_invalid_id(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/users/..")
        assert code == 400
        assert body["ok"] is False
    finally:
        h.close()


def test_get_tools_and_skills_catalog(paths):
    h = _ApiHarness(paths)
    try:
        code, tools = h.get("/api/tools")
        assert code == 200
        assert "tools" in tools
        # Bundled tools present in repo-root resolution.
        names = {t["name"] for t in tools["tools"]}
        if tools["tools"]:
            assert "speak" in names or "read_file" in names
            for t in tools["tools"]:
                assert "name" in t
                assert "description" in t
                assert "source" in t

        code, skills = h.get("/api/skills")
        assert code == 200
        assert "skills" in skills
        if skills["skills"]:
            sn = {s["name"] for s in skills["skills"]}
            assert "talk" in sn or "do-work" in sn
    finally:
        h.close()


def test_existing_status_and_messages_still_work(paths):
    h = _ApiHarness(paths)
    try:
        code, st = h.get("/api/status")
        assert code == 200
        assert "phase" in st
        assert "pending_wait" in st
        # Continuous status block is additive on GET /api/status (PR4/PR7).
        assert "continuous" in st
        cont = st["continuous"]
        assert cont["enabled"] is False
        assert cont["streak"] == 0
        assert "max_streak" in cont
        assert "cooldown_seconds" in cont
        assert cont["pending_moment_continues"] == 0
        assert cont["last_enqueue_at"] is None
        assert cont["last_skip_reason"] is None

        code, msgs = h.get("/api/messages?limit=10")
        assert code == 200
        assert "messages" in msgs
    finally:
        h.close()


def test_patch_continuous_enable_disable_and_persist(paths):
    """PATCH /api/continuous toggles runtime flag and writes continuous.json."""
    from elyra.loop.continuous_policy import (
        continuous_runtime_path,
        load_continuous_runtime,
    )

    h = _ApiHarness(paths)
    try:
        code, body = h.patch("/api/continuous", {"enabled": True})
        assert code == 200, body
        assert body["ok"] is True
        assert body["enabled"] is True
        assert body["changed"] is True
        assert body["cancelled_moment_continues"] == []
        assert body["continuous"]["enabled"] is True

        code, st = h.get("/api/status")
        assert code == 200
        assert st["continuous"]["enabled"] is True

        path = continuous_runtime_path(paths.data_dir)
        assert path.is_file()
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["enabled"] is True
        assert "updated_at" in raw

        code, body = h.patch("/api/continuous", {"enabled": False})
        assert code == 200, body
        assert body["ok"] is True
        assert body["enabled"] is False
        assert body["changed"] is True
        assert body["continuous"]["enabled"] is False
        assert body["continuous"]["streak"] == 0

        reloaded = load_continuous_runtime(
            paths.data_dir, defaults=default_settings().continuous
        )
        assert reloaded.enabled is False
    finally:
        h.close()


def test_patch_continuous_off_cancels_pending_moment_continues(paths):
    """OFF cancels only moment_continue; leaves task_ready pending."""
    h = _ApiHarness(paths)
    try:
        h.worker.set_continuous_enabled(True)
        mc_a = h.worker._queue.enqueue(  # noqa: SLF001
            "moment_continue",
            {"source_moment_id": "m1"},
        )
        mc_b = h.worker._queue.enqueue(  # noqa: SLF001
            "moment_continue",
            {"source_moment_id": "m2"},
        )
        tr = h.worker._queue.enqueue(  # noqa: SLF001
            "task_ready",
            {"task_id": "t1", "goal_id": "g1"},
        )

        code, body = h.patch("/api/continuous", {"enabled": False})
        assert code == 200, body
        assert body["ok"] is True
        assert body["enabled"] is False
        cancelled = set(body["cancelled_moment_continues"])
        assert cancelled == {mc_a.id, mc_b.id}
        assert body["continuous"]["pending_moment_continues"] == 0

        pending_kinds = {p.kind for p in h.worker._queue.pending()}  # noqa: SLF001
        assert "moment_continue" not in pending_kinds
        assert "task_ready" in pending_kinds
        assert any(p.id == tr.id for p in h.worker._queue.pending())  # noqa: SLF001
    finally:
        h.close()


def test_patch_continuous_validation(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.patch("/api/continuous", {})
        assert code == 400
        assert body["ok"] is False
        assert "enabled" in body["error"]

        code, body = h.patch("/api/continuous", {"enabled": "yes"})
        assert code == 400
        assert body["ok"] is False

        code, body = h.patch("/api/continuous", {"enabled": 1})
        assert code == 400
        assert body["ok"] is False

        # Idempotent same-value toggle is still ok.
        code, body = h.patch("/api/continuous", {"enabled": False})
        assert code == 200
        assert body["ok"] is True
        assert body["changed"] is False
        assert body["enabled"] is False
    finally:
        h.close()


def test_patch_unknown_path_404(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.patch("/api/settings/continuous", {"enabled": True})
        assert code == 404
    finally:
        h.close()


def test_static_index_served(paths):
    h = _ApiHarness(paths)
    try:
        req = urllib.request.Request(h.base + "/", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
        assert "Goals" in html
        assert "Moments" in html
        assert "panel-tools" in html
        assert "wait-choices" in html
        assert "notice" in html
        assert "continuous-toggle" in html
        assert "Continuous work" in html
        assert "pill-autopilot" in html
        # Continuous control lives in the rail (single source of truth).
        assert "continuous-toggle-rail" in html
        assert "rail-continuous" in html
        assert "continuous-status-rail" in html
        # Removed per-panel chat/status header toggles (avoid duplication).
        assert "continuous-toggle-chat" not in html
        assert "continuous-toggle-status" not in html
        # Moments list is content-sized; detail owns leftover space.
        assert "list-panel-auto" in html
    finally:
        h.close()


def test_static_app_js_active_panel_poll(paths):
    """Glass app.js polls the active catalog panel and tracks selection."""
    h = _ApiHarness(paths)
    try:
        req = urllib.request.Request(h.base + "/app.js", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            js = resp.read().decode("utf-8")
        # Core identifiers
        assert "activePanel" in js
        assert "refreshActivePanel" in js
        assert "selectedMomentId" in js
        assert "momentSnapshotChanged" in js
        # Wiring: nav assigns activePanel; tick pushes active-panel refresh.
        assert "activePanel = name" in js
        assert "tasks.push(refreshActivePanel" in js
        # Continuous meta targets rail control (single source of truth).
        assert "continuous-status-rail" in js
        # Soft refresh commits snapshot only after success / retries on change.
        assert "momentSnapshotChanged(selectedMomentSnapshot" in js
        assert "tickInFlight" in js
        # Soft detail path + 404 closes vanished moments.
        assert "soft: true" in js or "{ soft: true }" in js
        assert "err.status === 404" in js or "err.status == 404" in js

        req_css = urllib.request.Request(h.base + "/style.css", method="GET")
        with urllib.request.urlopen(req_css, timeout=5) as resp:
            assert resp.status == 200
            css = resp.read().decode("utf-8")
        assert "list-panel-auto" in css
        assert "position: sticky" in css
        assert "height: 100vh" in css
    finally:
        h.close()
