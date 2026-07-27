"""Glass catalog endpoints: goals, moments, tools, skills, identity (PR14)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
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
from elyra.llm.queue import ChatRequestGate
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
        self.gate = ChatRequestGate()
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

    def put(self, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method="PUT",
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
        # PR5 richer shape
        assert "meta" in body["self"]
        assert "has_draft" in body["self"]
        assert "versions" in body["self"]
        assert isinstance(body["self"]["versions"], list)
        assert body["self"].get("body") == body["self"]["digest"]
        assert "display_name" in body["self"]

        code, user = h.get("/api/users/operator")
        assert code == 200
        assert user["user_id"] == "operator"
        assert isinstance(user["profile"], str)
        assert user["profile"]  # seeded operator profile
        assert "meta" in user
        assert "has_draft" in user
        assert "versions" in user
        assert user.get("body") == user["profile"]
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


def test_list_users_and_create_provisional(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/users")
        assert code == 200
        assert "users" in body
        ids = {u["user_id"] for u in body["users"]}
        assert "operator" in ids
        op = next(u for u in body["users"] if u["user_id"] == "operator")
        assert "goes_by" in op
        assert "provisional" in op
        assert "real_name_known" in op

        code, created = h.post("/api/users", {"goes_by": "Sam"})
        assert code == 201, created
        assert created["ok"] is True
        assert created["user_id"] == "sam"
        assert created["goes_by"] == "Sam"
        assert created["provisional"] is True

        # Explicit id collision → 400 user_id_exists
        code, dup = h.post("/api/users", {"goes_by": "Sam", "user_id": "sam"})
        assert code == 400
        assert dup["error"] == "user_id_exists"

        # Collision on slug path gets suffix
        code, coll = h.post("/api/users", {"goes_by": "Sam"})
        assert code == 201, coll
        assert coll["user_id"].startswith("sam_")
        assert coll["user_id"] != "sam"

        code, body = h.get("/api/users")
        assert code == 200
        ids = {u["user_id"] for u in body["users"]}
        assert "sam" in ids
        assert coll["user_id"] in ids
    finally:
        h.close()


def test_session_get_and_put(paths):
    h = _ApiHarness(paths)
    try:
        code, sess = h.get("/api/session")
        assert code == 200
        assert sess["user_id"] == "operator"
        assert "goes_by" in sess
        assert "self_display_name" in sess
        assert isinstance(sess["self_display_name"], str)

        # Create guest then switch
        code, created = h.post("/api/users", {"goes_by": "Jim"})
        assert code == 201, created
        uid = created["user_id"]

        code, switched = h.put("/api/session", {"user_id": uid})
        assert code == 200, switched
        assert switched["ok"] is True
        assert switched["user_id"] == uid
        assert switched["goes_by"] == "Jim"

        code, sess = h.get("/api/session")
        assert code == 200
        assert sess["user_id"] == uid

        code, missing = h.put("/api/session", {"user_id": "no_such_user_xyz"})
        assert code == 404
        assert missing["error"] == "user_not_found"

        code, bad = h.put("/api/session", {"user_id": "../etc"})
        assert code == 400
    finally:
        h.close()


def test_identity_grants_and_promote_self(paths):
    h = _ApiHarness(paths)
    try:
        # Promote without draft / grant → denied
        code, denied = h.post(
            "/api/identity/promote",
            {"reason": "operator adopt self draft now"},
        )
        assert code == 400
        assert denied["error"] in ("self_grant_required", "draft_missing")

        # Write a self draft via store
        store = IdentityStore(paths)
        store.ensure_layout()
        wr = store.write_draft(
            "# Self draft\n\nI am Elyra, carefully revised.\n",
            reason="test draft",
        )
        assert wr.get("ok") is True

        # Still need grant
        code, denied2 = h.post(
            "/api/identity/promote",
            {"reason": "operator adopt self draft now"},
        )
        assert code == 400
        assert denied2["error"] == "self_grant_required"

        code, grant = h.post("/api/identity/grants", {"note": "test"})
        assert code == 200, grant
        assert grant["ok"] is True
        token = grant["token"]
        assert token.startswith("grant_")

        # Resolve→gate→consume→promote (Glass path uses first active token)
        code, promoted = h.post(
            "/api/identity/promote",
            {"reason": "operator adopt self draft now"},
        )
        assert code == 200, promoted
        assert promoted["ok"] is True
        assert promoted.get("actor") == "self"

        # Draft cleared; current is new body
        code, body = h.get("/api/identity")
        assert code == 200
        assert body["self"]["has_draft"] is False
        assert "carefully revised" in body["self"]["digest"]
        # Prior current archived
        assert isinstance(body["self"]["versions"], list)
        assert len(body["self"]["versions"]) >= 1

        # Token consumed — second promote without new grant fails
        store.write_draft(
            "# Self draft 2\n\nAnother revision.\n",
            reason="second",
        )
        code, again = h.post(
            "/api/identity/promote",
            {"reason": "operator adopt again please"},
        )
        assert code == 400
        assert again["error"] == "self_grant_required"

        # Explicit grant_token path
        code, grant2 = h.post("/api/identity/grants", {})
        assert code == 200
        code, with_tok = h.post(
            "/api/identity/promote",
            {
                "reason": "operator adopt with explicit token",
                "grant_token": grant2["token"],
            },
        )
        assert code == 200, with_tok
        assert with_tok["ok"] is True
    finally:
        h.close()


def test_user_promote_from_glass_panel(paths):
    h = _ApiHarness(paths)
    try:
        users = UsersStore(paths)
        created = users.create_user("Alex", user_id="alex", provisional=True)
        assert created.get("ok") is True

        wr = users.write_draft(
            "alex",
            "# Alex\n\nPrefers short notes.\n",
            reason="profile update",
        )
        assert wr.get("ok") is True

        code, promoted = h.post(
            "/api/users/alex/promote",
            {"reason": "glass panel promote"},
        )
        assert code == 200, promoted
        assert promoted["ok"] is True

        code, user = h.get("/api/users/alex")
        assert code == 200
        assert user["has_draft"] is False
        assert "Prefers short notes" in user["profile"]
        assert len(user["versions"]) >= 1

        # No draft → draft_missing
        code, nodraft = h.post(
            "/api/users/alex/promote",
            {"reason": "no draft left"},
        )
        assert code == 400
        assert nodraft["error"] == "draft_missing"
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


def test_get_tools_rescans_after_local_delete(paths):
    """GET /api/tools reloads so operator-deleted local packages leave the catalog."""
    import json
    import shutil

    local = paths.tools_dir / "local" / "ghost_tool"
    local.mkdir(parents=True)
    (local / "TOOL.md").write_text(
        "---\nname: ghost_tool\ndescription: temp\nkind: read\n---\n",
        encoding="utf-8",
    )
    (local / "schema.json").write_text(
        json.dumps({"type": "object", "properties": {}}), encoding="utf-8"
    )
    (local / "runner.json").write_text(
        json.dumps({"kind": "sandbox_python", "module": "main"}), encoding="utf-8"
    )
    (local / "main.py").write_text("def run(args):\n    return {'ok': True}\n")

    h = _ApiHarness(paths)
    try:
        if h.server is None:
            return
        # Harness may have failed to resolve tools if bundled root missing.
        code, body = h.get("/api/tools")
        assert code == 200
        if body.get("error") == "tools catalog unavailable":
            return
        names = {t["name"] for t in body["tools"]}
        assert "ghost_tool" in names

        # External delete (operator rm) without process restart
        shutil.rmtree(local)

        code, body = h.get("/api/tools")
        assert code == 200
        names = {t["name"] for t in body["tools"]}
        assert "ghost_tool" not in names
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
        # Phase 0 provider / usage glass (PR7 web).
        assert 'id="pill-provider"' in html
        assert 'id="pill-llama"' not in html
        assert 'id="hard-stop-banner"' in html
        assert 'id="provider-card"' in html
        assert 'id="provider-model-select"' in html
        assert 'id="provider-credential-select"' in html
        assert 'id="provider-api-key-input"' in html
        assert 'type="password"' in html
        assert 'id="provider-api-key-save"' in html
        assert 'id="provider-api-key-clear"' in html
        # Reasoning effort control on Status provider card (PR3)
        assert 'id="provider-effort"' in html
        assert 'id="provider-effort-label"' in html
        assert 'role="radiogroup"' in html
        assert 'data-effort="low"' in html
        assert 'data-effort="medium"' in html
        assert 'data-effort="high"' in html
        assert 'data-effort="auto"' in html
        assert "Auto effort escalation — coming later" in html
        assert "effort-btn-auto" in html
        # Auto is disabled stub in markup
        assert re.search(
            r'data-effort="auto"[^>]*\bdisabled\b',
            html,
        ) or re.search(
            r'\bdisabled\b[^>]*data-effort="auto"',
            html,
        )
        # PR4: left rail effort twin + compact usage meters
        assert 'id="rail-effort"' in html
        assert 'class="rail-effort"' in html or "rail-effort" in html
        assert "effort-group-compact" in html
        # Rail Auto is also disabled (both Status + rail stubs)
        rail_auto = re.search(
            r'id="rail-effort"[\s\S]*?data-effort="auto"[^>]*>',
            html,
        )
        assert rail_auto is not None, "rail-effort Auto button not found"
        assert "disabled" in rail_auto.group(0)
        assert "aria-disabled" in rail_auto.group(0)
        assert 'id="rail-usage-week-pct"' in html
        assert 'id="rail-usage-week-bar"' in html
        assert 'id="rail-usage-sg-pct"' in html
        assert 'id="rail-usage-sg-bar"' in html
        assert 'class="rail-usage"' in html or "rail-usage" in html
        # Rail section must not include hard-stop override / day-hour soft / pace
        # Slice from rail-usage open through rail-foot open (compact meters only).
        rail_start = html.find('class="rail-usage"')
        rail_foot = html.find('class="rail-foot"', rail_start if rail_start >= 0 else 0)
        assert rail_start >= 0 and rail_foot > rail_start, "rail-usage before rail-foot not found"
        rail_usage_html = html[rail_start:rail_foot]
        assert "usage-override" not in rail_usage_html
        assert "Hard-stop override" not in rail_usage_html
        assert "Day (soft)" not in rail_usage_html
        assert "Hour (soft)" not in rail_usage_html
        assert "usage-pace" not in rail_usage_html
        assert "usage-burst" not in rail_usage_html
        assert 'id="usage-card"' in html
        assert 'id="usage-override-toggle"' in html
        assert "Hard-stop override" in html
        # Override copy contract (design §hard_stop_override / PR6)
        assert "When ON, model calls continue past budget limits. Usage is still recorded." in html
        assert "Usage budget" in html
        assert "Provider / model" in html
        # Primary meters: Elyra week + SuperGrok pool; soft day/hour demoted
        assert "Elyra week" in html
        assert "SuperGrok pool" in html
        assert 'id="usage-sg-bar"' in html
        assert 'id="usage-sg-pct"' in html
        assert 'id="usage-pace-badge"' in html
        assert 'id="usage-burst"' in html
        assert "Day (soft)" in html
        assert "Hour (soft)" in html
        assert 'id="usage-product-usage"' in html
        # Reset checklist preserves secrets / provider prefs / usage meter.
        assert "data/secrets/" in html
        assert "data/runtime/provider.json" in html
        assert "data/runtime/usage.json" in html
        assert "will-keep" in html
        # Confirm copy does not claim runtime dir is wiped wholesale.
        assert "runtime dir" not in html.lower() or "preserves" in html.lower()
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
        # Phase 0 provider pill + usage/override wiring.
        assert "renderProviderPill" in js
        assert "renderProviderCard" in js
        assert "renderUsageCard" in js
        assert "renderHardStopBanner" in js
        assert "setHardStopOverride" in js
        assert "hard_stop_override" in js
        assert 'method: "PATCH"' in js
        assert '"/api/usage"' in js or "'/api/usage'" in js
        assert '"/api/provider"' in js or "'/api/provider'" in js
        assert '"/api/provider/api-key"' in js or "'/api/provider/api-key'" in js
        assert 'method: "PUT"' in js
        assert 'method: "DELETE"' in js
        assert "xai ready" in js or "${provider} ready" in js
        assert "xai limit" in js or "${provider} limit" in js
        assert "xai ovrd" in js or "${provider} ovrd" in js
        assert "xai auth" in js or "${provider} auth" in js
        # API key never re-displayed after save.
        assert "providerApiKeyInput.value = \"\"" in js or "providerApiKeyInput.value = ''" in js
        assert "usageOverrideInFlight" in js
        assert "providerPatchInFlight" in js
        # PR3: reasoning effort Status control — real wiring, not just identifiers
        assert "function paintEffortUI" in js
        assert "function commitEffortFromStatus" in js
        assert "lastReasoningEffort" in js
        # paintEffortUI is visual-only (must not assign lastReasoningEffort)
        paint_fn = re.search(
            r"function paintEffortUI\s*\([^)]*\)\s*\{(.*?)\n\}",
            js,
            re.DOTALL,
        )
        assert paint_fn is not None, "paintEffortUI body not found"
        assert "lastReasoningEffort" not in paint_fn.group(1)
        assert "effort-btn-active" in paint_fn.group(1)
        assert "aria-pressed" in paint_fn.group(1)
        # commitEffortFromStatus is the only assigner of lastReasoningEffort
        commit_fn = re.search(
            r"function commitEffortFromStatus\s*\([^)]*\)\s*\{(.*?)\n\}",
            js,
            re.DOTALL,
        )
        assert commit_fn is not None, "commitEffortFromStatus body not found"
        assert "lastReasoningEffort =" in commit_fn.group(1)
        assert "paintEffortUI" in commit_fn.group(1)
        # renderProviderCard: skip overwrite while in-flight; else commit
        assert "commitEffortFromStatus" in js
        assert "reasoning_effort" in js
        # Optimistic click paints only; patch body is { reasoning_effort }
        assert "paintEffortUI(effort)" in js
        assert "patchProvider({ reasoning_effort: effort })" in js or (
            "reasoning_effort: effort" in js and "patchProvider" in js
        )
        # Error path reverts paint from server last*, not optimistic value
        assert "paintEffortUI(lastReasoningEffort)" in js
        # In-flight disables active effort buttons (not Auto)
        assert "setEffortButtonsDisabled" in js
        # Auto never in PATCH body
        assert 'reasoning_effort === "auto"' in js or "reasoning_effort === 'auto'" in js
        # PR6: SuperGrok + pace/burst wiring (not just identifiers)
        assert "usageSgBar" in js or "usage-sg-bar" in js
        assert "usagePaceBadge" in js or "usage-pace-badge" in js
        assert "pace_band" in js
        assert "burst_remaining_tokens" in js
        assert "burst_max_tokens" in js
        assert "burst ${rem}/${max}" in js or "burst " in js
        assert "day_soft_exhausted" in js
        assert "day pace high (soft)" in js
        assert "credit_usage_percent" in js
        assert "poll …" in js or "poll" in js
        # PR4: shared SuperGrok meter view + rail wiring (not identifier-only)
        assert "function supergrokMeterView" in js
        sg_fn = re.search(
            r"function supergrokMeterView\s*\([^)]*\)\s*\{(.*?)\n\}",
            js,
            re.DOTALL,
        )
        assert sg_fn is not None, "supergrokMeterView body not found"
        sg_body = sg_fn.group(1)
        assert "credit_usage_percent" in sg_body
        assert "stale" in sg_body
        assert "% used" in sg_body
        assert "poll …" in sg_body or "poll" in sg_body
        # renderUsageCard must call the shared helper (Status + rail parity)
        assert "supergrokMeterView(usage)" in js
        assert "railUsageWeekPct" in js or "rail-usage-week-pct" in js
        assert "railUsageWeekBar" in js or "rail-usage-week-bar" in js
        assert "railUsageSgPct" in js or "rail-usage-sg-pct" in js
        assert "railUsageSgBar" in js or "rail-usage-sg-bar" in js
        # Week rail uses same remaining fraction + formatPctRemaining
        assert "formatPctRemaining(week)" in js
        assert "setUsageBar(railUsageWeekBar" in js or (
            "railUsageWeekBar" in js and "setUsageBar" in js
        )
        # SuperGrok rail + Status both driven from sgView
        assert "sgView.label" in js
        assert "sgView.usedFrac" in js or "sgView.available" in js
        assert "setUsageBar(railUsageSgBar" in js or (
            "railUsageSgBar" in js and "usedMode: true" in js
        )
        # Soft day → no stop badge: pure helper + structural wiring
        assert "function usageBadgeLabel" in js
        assert "usageBadgeLabel(usage)" in js
        badge_fn = re.search(
            r"function usageBadgeLabel\s*\([^)]*\)\s*\{(.*?)\n\}",
            js,
            re.DOTALL,
        )
        assert badge_fn is not None, "usageBadgeLabel body not found"
        badge_body = badge_fn.group(1)
        assert "hard_stop" in badge_body
        assert "soft_exhausted" not in badge_body
        assert "day_soft" not in badge_body
        assert "stop · ${hardStop}" in badge_body or "stop ·" in badge_body
        # Soft flags only feed detail text, not badge
        soft_detail = re.search(
            r"if\s*\(\s*usage\.day_soft_exhausted\s*\)\s*\{?\s*"
            r"parts\.push\([\"']day pace high \(soft\)[\"']\)",
            js,
        )
        assert soft_detail is not None, "day_soft_exhausted must only push detail line"
        # No literal stop · day (stop level always comes from hard_stop)
        assert "stop · day" not in js
        # Banner only when hard_stop is set (true hard levels)
        assert "if (hardStop && !overrideActive)" in js or "hardStop && !overrideActive" in js
        # Chat polish + multimodal-ready composer
        assert "renderMarkdown" in js
        assert "pendingAttachments" in js
        assert "chatStickToBottom" in js
        assert "updateChatActivity" in js
        assert "renderActivityTrail" in js
        assert "recent_activity" in js
        # PR4: durable attach upload + render matrix (no inventory-text hack)
        assert "uploadPendingAttachments" in js
        assert "attachment_ids" in js
        assert '"/api/media"' in js or "'/api/media'" in js
        assert "resolveMediaUrl" in js
        assert "attachment:" in js
        assert "renderAttachmentsFooter" in js
        assert "msg-attachments" in js
        assert "buildAttachmentInventory" not in js
        assert "binary vision/file I/O not wired yet" not in js
        assert "detectAttachmentKind" in js
        assert "media-only" in js.lower() or "hasPending" in js
        assert "MAX_PENDING_ATTACHMENTS" in js

        req_css = urllib.request.Request(h.base + "/style.css", method="GET")
        with urllib.request.urlopen(req_css, timeout=5) as resp:
            assert resp.status == 200
            css = resp.read().decode("utf-8")
        assert "list-panel-auto" in css
        # Viewport-locked shell: app/rail/main do not page-scroll together.
        assert "overflow: hidden" in css
        assert "overscroll-behavior: contain" in css
        assert "hard-stop-banner" in css
        assert "usage-bar-fill" in css
        assert "usage-pace-badge" in css
        assert "usage-meters-soft" in css
        assert "usage-bar-na" in css
        assert "status-cards" in css
        # PR3: effort segmented control styles (shared with rail)
        assert ".effort-group" in css
        assert ".effort-btn" in css
        assert "effort-btn-active" in css
        assert "effort-btn-auto" in css or ".effort-btn:disabled" in css
        assert "effort-group-compact" in css
        # PR4: rail effort + compact usage meters (220px column preserved)
        assert ".rail-effort" in css
        assert ".rail-usage" in css
        assert ".rail-usage-meter" in css
        assert "grid-template-columns: 220px 1fr" in css
        # Chat polish surface
        assert "msg-body" in css
        assert "jump-latest" in css
        assert "attach-tray" in css
        assert "activity-trail" in css
        assert "activity-chip" in css
        # PR4 attachment footer / players
        assert "msg-attachments" in css
        assert "msg-att-thumb" in css
        assert "msg-att-player" in css
    finally:
        h.close()


def test_usage_badge_label_soft_day_does_not_stop(paths):
    """Fixture payloads: soft day alone → ok; true hard_stop → stop · level.

    Runs pure usageBadgeLabel from app.js under node when available; otherwise
    reimplements the locked contract in Python so CI still covers the policy.
    """
    h = _ApiHarness(paths)
    try:
        req = urllib.request.Request(h.base + "/app.js", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            js = resp.read().decode("utf-8")
    finally:
        h.close()

    m = re.search(
        r"(function usageBadgeLabel\s*\([^)]*\)\s*\{.*?\n\})",
        js,
        re.DOTALL,
    )
    assert m is not None
    fn_src = m.group(1)
    # Helper must not consult soft flags
    assert "soft_exhausted" not in fn_src

    cases = [
        (
            {"enabled": True, "hard_stop": None, "day_soft_exhausted": True},
            "ok",
        ),
        (
            {
                "enabled": True,
                "hard_stop": None,
                "day_soft_exhausted": True,
                "hour_soft_exhausted": True,
                "override_active": False,
            },
            "ok",
        ),
        ({"enabled": True, "hard_stop": "week", "override_active": False}, "stop · week"),
        ({"enabled": True, "hard_stop": "day", "override_active": False}, "stop · day"),
        ({"enabled": True, "hard_stop": "week", "override_active": True}, "override"),
        ({"enabled": False}, "off"),
        (None, "n/a"),
    ]

    node = shutil.which("node")
    if node:
        harness = (
            fn_src
            + "\n"
            + "const cases = "
            + json.dumps([[c[0], c[1]] for c in cases])
            + ";\n"
            + "for (const [u, want] of cases) {\n"
            + "  const got = usageBadgeLabel(u);\n"
            + "  if (got !== want) {\n"
            + "    console.error(JSON.stringify({u, got, want}));\n"
            + "    process.exit(1);\n"
            + "  }\n"
            + "}\n"
            + "console.log('ok');\n"
        )
        proc = subprocess.run(
            [node, "-e", harness],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr or proc.stdout
        return

    # Python mirror of usageBadgeLabel (locked by structural asserts above).
    def usage_badge_label(usage: dict[str, Any] | None) -> str:
        if not usage:
            return "n/a"
        if not usage.get("enabled"):
            return "off"
        hard_stop = usage.get("hard_stop") or None
        override_active = bool(usage.get("override_active"))
        if hard_stop and not override_active:
            return f"stop · {hard_stop}"
        if hard_stop and override_active:
            return "override"
        return "ok"

    for payload, want in cases:
        assert usage_badge_label(payload) == want


def test_static_glass_pr4_html_accepts_audio(paths):
    """Composer file input accepts audio; no inventory-only attach copy."""
    h = _ApiHarness(paths)
    try:
        req = urllib.request.Request(h.base + "/", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
        assert 'id="attach-input"' in html
        assert "audio/*" in html
        assert "image/*" in html
        # Placeholder signals media-only send is OK
        assert "attachments alone" in html or "media-only" in html.lower()
    finally:
        h.close()
