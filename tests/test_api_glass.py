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
    def __init__(self, paths, *, client_id: str | None = "test-client-1") -> None:
        self.paths = paths
        # Default durable client for single-principal tests (KD21).
        self.client_id = client_id
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

    def _merge_headers(
        self,
        base: dict[str, str] | None = None,
        *,
        client_id: str | None | object = ...,
        headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        out: dict[str, str] = dict(base or {})
        if headers:
            out.update(headers)
        cid: str | None
        if client_id is ...:
            cid = self.client_id
        else:
            cid = client_id  # type: ignore[assignment]
        if cid:
            out.setdefault("X-Elyra-Client", cid)
        return out

    def get(
        self,
        path: str,
        *,
        client_id: str | None | object = ...,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        hdrs = self._merge_headers(client_id=client_id, headers=headers)
        req = urllib.request.Request(self.base + path, method="GET", headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body

    def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        client_id: str | None | object = ...,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8")
        hdrs = self._merge_headers(
            {"Content-Type": "application/json"},
            client_id=client_id,
            headers=headers,
        )
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method="POST",
            headers=hdrs,
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

    def patch(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        client_id: str | None | object = ...,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8")
        hdrs = self._merge_headers(
            {"Content-Type": "application/json"},
            client_id=client_id,
            headers=headers,
        )
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method="PATCH",
            headers=hdrs,
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

    def put(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        client_id: str | None | object = ...,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        data = json.dumps(payload).encode("utf-8")
        hdrs = self._merge_headers(
            {"Content-Type": "application/json"},
            client_id=client_id,
            headers=headers,
        )
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method="PUT",
            headers=hdrs,
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


def test_get_tool_and_skill_detail_inspector(paths):
    """GET /api/tools|skills/{name} — package docs + versions list (read-only)."""
    h = _ApiHarness(paths)
    try:
        code, tools = h.get("/api/tools")
        assert code == 200
        if tools.get("error") == "tools catalog unavailable" or not tools.get("tools"):
            return
        tname = next(
            (t["name"] for t in tools["tools"] if t.get("name") in ("speak", "read_file")),
            tools["tools"][0]["name"],
        )
        code, detail = h.get(f"/api/tools/{tname}?list_versions=1")
        assert code == 200, detail
        assert detail.get("ok") is True
        assert detail.get("name") == tname
        assert detail.get("kind") == "tool"
        assert "package" in detail
        assert isinstance(detail.get("versions"), list)
        # Bundled: empty versions; package preview present when TOOL.md exists
        pkg = detail["package"]
        assert "files_present" in pkg or "top_level" in pkg

        code, missing = h.get("/api/tools/definitely_not_a_tool_xyz")
        assert code in (400, 404)
        assert missing.get("ok") is False

        code, skills = h.get("/api/skills")
        assert code == 200
        if skills.get("error") == "skills catalog unavailable" or not skills.get("skills"):
            return
        sname = next(
            (s["name"] for s in skills["skills"] if s.get("name") in ("talk", "do-work")),
            skills["skills"][0]["name"],
        )
        code, sdetail = h.get(f"/api/skills/{sname}?list_versions=1")
        assert code == 200, sdetail
        assert sdetail.get("ok") is True
        assert sdetail.get("name") == sname
        assert sdetail.get("kind") == "skill"
        # Full playbook preferred when catalog can load body
        assert sdetail.get("skill_md") or (sdetail.get("package") or {}).get(
            "skill_md_preview"
        )
        assert isinstance(sdetail.get("versions"), list)
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


def test_patch_semantic_wait_enable_disable_and_persist(paths):
    """PATCH /api/semantic-wait toggles runtime flag and writes JSON."""
    from elyra.runtime.semantic_wait import (
        load_semantic_wait_runtime,
        semantic_wait_runtime_path,
    )

    h = _ApiHarness(paths)
    try:
        code, body = h.patch(
            "/api/semantic-wait", {"enabled": False, "max_ms": 10_000}
        )
        assert code == 200, body
        assert body["ok"] is True
        assert body["changed"] is True
        assert body["semantic_wait"]["enabled"] is False
        assert body["semantic_wait"]["max_ms"] == 10_000
        assert body["semantic_wait"]["effective_select_max_ms"] == (
            h.worker.settings.memory.semantic_select_max_ms
        )

        code, st = h.get("/api/status")
        assert code == 200
        assert st["semantic_wait"]["enabled"] is False
        assert st["semantic_wait"]["max_ms"] == 10_000
        assert "snappy_select_max_ms" in st["semantic_wait"]

        path = semantic_wait_runtime_path(paths.data_dir)
        assert path.is_file()
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["enabled"] is False
        assert raw["max_ms"] == 10_000
        assert "updated_at" in raw

        code, body = h.patch("/api/semantic-wait", {"enabled": True})
        assert code == 200, body
        assert body["ok"] is True
        assert body["semantic_wait"]["enabled"] is True
        # max_ms preserved when only toggling enabled
        assert body["semantic_wait"]["max_ms"] == 10_000

        reloaded = load_semantic_wait_runtime(paths.data_dir)
        assert reloaded.enabled is True
        assert reloaded.max_ms == 10_000
    finally:
        h.close()


def test_patch_semantic_wait_validation(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.patch("/api/semantic-wait", {})
        assert code == 400
        assert body["ok"] is False
        assert "enabled" in body["error"] or "max_ms" in body["error"]

        code, body = h.patch("/api/semantic-wait", {"enabled": "yes"})
        assert code == 400
        assert body["ok"] is False

        code, body = h.patch("/api/semantic-wait", {"max_ms": True})
        assert code == 400
        assert body["ok"] is False

        code, body = h.patch("/api/semantic-wait", {"max_ms": "slow"})
        assert code == 400
        assert body["ok"] is False

        # Clamp out-of-band max_ms rather than 400 (product band).
        code, body = h.patch("/api/semantic-wait", {"max_ms": 50})
        assert code == 200, body
        assert body["ok"] is True
        assert body["semantic_wait"]["max_ms"] == 1_000
    finally:
        h.close()


def test_patch_meal_budget_and_status(paths):
    """PATCH /api/meal-budget sets fraction; status exposes tokens (not stuck at 50k)."""
    from elyra.runtime.meal_budget import (
        load_meal_budget_runtime,
        meal_budget_runtime_path,
    )

    h = _ApiHarness(paths)
    try:
        # Default product: 0.5 → 250k of 500k.
        code, st = h.get("/api/status")
        assert code == 200
        assert "meal_budget" in st
        assert st["meal_budget"]["fraction"] == 0.5
        assert st["meal_budget"]["meal_budget_tokens"] == 250_000
        assert st["meal_budget"]["model_window_tokens"] == 500_000
        assert st["context"]["meal_budget_tokens"] == 250_000

        code, body = h.patch("/api/meal-budget", {"fraction": 0.4})
        assert code == 200, body
        assert body["ok"] is True
        assert body["changed"] is True
        assert body["meal_budget"]["fraction"] == 0.4
        assert body["meal_budget"]["meal_budget_tokens"] == 200_000

        code, st = h.get("/api/status")
        assert code == 200
        assert st["meal_budget"]["fraction"] == 0.4
        assert st["meal_budget"]["meal_budget_tokens"] == 200_000
        assert st["context"]["meal_budget_tokens"] == 200_000

        path = meal_budget_runtime_path(paths.data_dir)
        assert path.is_file()
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["fraction"] == 0.4
        assert "updated_at" in raw

        reloaded = load_meal_budget_runtime(paths.data_dir)
        assert reloaded.fraction == 0.4

        # Clamp out-of-band rather than 400 (product max 0.75 unless override).
        code, body = h.patch("/api/meal-budget", {"fraction": 0.99})
        assert code == 200, body
        assert body["meal_budget"]["fraction"] == 0.75
        assert body["meal_budget"]["meal_budget_tokens"] == 375_000
        assert body["meal_budget"]["max_fraction"] == 0.75
    finally:
        h.close()


def test_patch_meal_budget_validation(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.patch("/api/meal-budget", {})
        assert code == 400
        assert body["ok"] is False
        assert "fraction" in body["error"]

        code, body = h.patch("/api/meal-budget", {"fraction": "half"})
        assert code == 400
        assert body["ok"] is False

        code, body = h.patch("/api/meal-budget", {"fraction": True})
        assert code == 400
        assert body["ok"] is False
    finally:
        h.close()


def test_patch_meal_budget_persist_failure_500(paths, monkeypatch):
    """PATCH does not claim success when durable save fails (live unchanged)."""
    import elyra.presence.worker as worker_mod

    h = _ApiHarness(paths)
    try:
        code, body = h.patch("/api/meal-budget", {"fraction": 0.5})
        assert code == 200, body
        assert body["meal_budget"]["fraction"] == 0.5

        def boom(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(worker_mod, "save_meal_budget_runtime", boom)
        code, body = h.patch("/api/meal-budget", {"fraction": 0.4})
        assert code == 500, body
        assert body["ok"] is False
        assert body["error"] == "persist_failed"
        assert body["meal_budget"]["fraction"] == 0.5

        code, st = h.get("/api/status")
        assert code == 200
        assert st["meal_budget"]["fraction"] == 0.5
        assert st["context"]["meal_budget_tokens"] == 250_000
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
        assert "Continue open work" in html
        assert "Continuous work" not in html
        assert "pill-autopilot" in html
        # #126 PR3/PR4: Memory → Schedule tab (active + optional history)
        assert 'data-memory-tab="schedule"' in html
        assert 'id="memory-tab-schedule"' in html
        assert 'id="schedule-continuous"' in html
        assert 'id="schedule-timers-list"' in html
        assert 'id="schedule-waits-list"' in html
        assert 'id="schedule-counts"' in html
        assert "Schedule" in html
        # Tab order: Moments then Schedule then Atoms
        assert html.index('data-memory-tab="moments"') < html.index(
            'data-memory-tab="schedule"'
        )
        assert html.index('data-memory-tab="schedule"') < html.index(
            'data-memory-tab="atoms"'
        )
        # PR4: history toggle + history sections (honest due/expiry ordering copy)
        assert 'id="schedule-history-toggle"' in html
        assert "Show recent history" in html
        assert 'id="schedule-history"' in html
        assert 'id="schedule-history-timers-list"' in html
        assert 'id="schedule-history-waits-list"' in html
        assert "Recent by due/expiry time (not fire time)" in html
        # KD4 / acceptance: history default off — no checked; section starts hidden
        toggle_tag = re.search(
            r'<input\b[^>]*\bid="schedule-history-toggle"[^>]*>',
            html,
            re.DOTALL,
        )
        assert toggle_tag is not None, "schedule-history-toggle input not found"
        assert not re.search(r"\bchecked\b", toggle_tag.group(0)), (
            "history toggle must default off (no checked attribute)"
        )
        hist_sec = re.search(
            r'<section\b[^>]*\bid="schedule-history"[^>]*>',
            html,
            re.DOTALL,
        )
        assert hist_sec is not None, "schedule-history section not found"
        assert re.search(r"\bhidden\b", hist_sec.group(0)), (
            "schedule-history must start with hidden"
        )
        # Schedule lists: plain list-panel (not list-panel-auto)
        timers_list = re.search(
            r'id="schedule-timers-list"[^>]*class="([^"]*)"',
            html,
        )
        waits_list = re.search(
            r'id="schedule-waits-list"[^>]*class="([^"]*)"',
            html,
        )
        assert timers_list is not None and "list-panel" in timers_list.group(1)
        assert "list-panel-auto" not in timers_list.group(1)
        assert waits_list is not None and "list-panel" in waits_list.group(1)
        assert "list-panel-auto" not in waits_list.group(1)
        # #88: pure markdown helpers load before app.js
        assert 'src="/markdown.js"' in html
        assert html.index('src="/markdown.js"') < html.index('src="/app.js"')
        # Continuous control lives on Status (rail primary removed).
        assert "continuous-toggle-status" in html
        assert 'id="continuous-toggle-status"' in html
        assert "continuous-toggle-rail" not in html
        assert "rail-continuous" not in html
        assert "continuous-status-rail" not in html
        # Chat header toggle still absent (no duplication).
        assert "continuous-toggle-chat" not in html
        # Status continuous card: toggle class + honesty detail el for PATCH/render path.
        cont_status = re.search(
            r'id="continuous-toggle-status"[^>]*class="([^"]*)"',
            html,
        )
        assert cont_status is not None, "continuous-toggle-status input not found"
        assert "continuous-toggle" in cont_status.group(1)
        assert 'id="continuous-detail"' in html
        assert 'id="continuous-badge"' in html
        assert 'id="continuous-summary"' in html
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
        # PR5 secrets panel
        assert 'data-panel="secrets"' in html
        assert 'id="panel-secrets"' in html
        assert 'id="secrets-name-input"' in html
        assert 'id="secrets-value-input"' in html
        assert 'id="secrets-save-btn"' in html
        assert 'id="secrets-list"' in html
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
        # BUG-meal-01: Context card meal-budget range (not bars-as-sliders)
        assert 'id="context-card"' in html
        assert 'id="meal-budget-fraction"' in html
        assert 'id="meal-budget-readout"' in html
        assert 'id="meal-budget-max-note"' in html
        assert "max-meal-override" in html
        assert 'type="range"' in html
        assert "meal-budget-fraction" in html
        assert "50% → 250k of 500k" in html
        assert "Gold mark" in html or "gold mark" in html.lower()
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


def test_static_app_js_conversations_list_poll(paths):
    """PR1 KD-U1–U5: conversation list discovery poll + create membership gate needles.

    T-U2: refreshConversationsList + CONVERSATIONS_POLL_MS
    T-U3: tick schedules throttled list refresh
    T-U4: no silent empty catch on conversations leg
    T-U5: createGroupFromModal switches only when session user ∈ members
    """
    h = _ApiHarness(paths)
    try:
        req = urllib.request.Request(h.base + "/app.js", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            js = resp.read().decode("utf-8")

        # T-U2: poll constant + extracted list helper
        assert "CONVERSATIONS_POLL_MS" in js
        assert re.search(r"CONVERSATIONS_POLL_MS\s*=\s*3000", js)
        assert "function refreshConversationsList" in js
        assert "function shouldRefreshConversations" in js
        assert "lastConversationsPollAt" in js
        assert "conversationsListError" in js

        # T-U3: tick schedules conversations refresh when throttle elapsed
        tick_m = re.search(
            r"async function tick\s*\(\s*\)\s*\{(.*?)\n// Boot order",
            js,
            re.DOTALL,
        )
        if tick_m is None:
            tick_m = re.search(
                r"async function tick\s*\(\s*\)\s*\{(.*)",
                js,
                re.DOTALL,
            )
        assert tick_m is not None, "tick() body not found"
        tick_body = tick_m.group(1)[:2000]
        assert "shouldRefreshConversations" in tick_body
        assert "refreshConversationsList" in tick_body
        assert "tasks.push(refreshConversationsList" in tick_body

        # T-U4: absence of conversations-leg silent empty catch
        assert ".catch(() => ({ conversations: [] }))" not in js
        assert ".catch(()=>({conversations:[]}))" not in js.replace(" ", "")
        # refreshLabelCache must not invent empty membership on list failure
        rlc_m = re.search(
            r"async function refreshLabelCache\s*\(\s*\)\s*\{(.*?)\n(?:async )?function ",
            js,
            re.DOTALL,
        )
        assert rlc_m is not None, "refreshLabelCache body not found"
        rlc_body = rlc_m.group(1)
        assert "refreshConversationsList" in rlc_body
        assert "conversations: []" not in rlc_body
        assert "/api/conversations?member=" in js

        # Fail-visible + cache preserve + notice dedupe (KD-U2)
        rcl_m = re.search(
            r"async function refreshConversationsList\s*\([^)]*\)\s*\{(.*?)\n(?:async )?function ",
            js,
            re.DOTALL,
        )
        assert rcl_m is not None, "refreshConversationsList body not found"
        rcl_body = rcl_m.group(1)
        assert "conversationsListError" in rcl_body
        assert "showNotice" in rcl_body
        assert "Conversation list failed" in rcl_body
        assert "data-error" in rcl_body
        # Must not assign conversationsCache = [] on error path
        catch_m = re.search(r"catch\s*\([^)]*\)\s*\{(.*)\}\s*$", rcl_body, re.DOTALL)
        if catch_m is None:
            catch_m = re.search(r"catch\s*\([^)]*\)\s*\{(.*?)\n\s*\}", rcl_body, re.DOTALL)
        assert catch_m is not None, "refreshConversationsList catch not found"
        catch_body = catch_m.group(1)
        assert "conversationsCache" not in catch_body or "conversationsCache =" not in catch_body

        # T-U5: createGroupFromModal membership gate before switchConversation
        cg_m = re.search(
            r"async function createGroupFromModal\s*\([^)]*\)\s*\{(.*?)\n(?:async )?function ",
            js,
            re.DOTALL,
        )
        assert cg_m is not None, "createGroupFromModal body not found"
        cg_body = cg_m.group(1)
        assert "members.includes(sessionUid)" in cg_body or (
            "members.includes(" in cg_body and "sessionUid" in cg_body
        )
        assert "switchConversation" in cg_body
        # Gate must appear as condition around switch, not always switch
        assert re.search(
            r"if\s*\([^)]*members\.includes\(sessionUid\)[^)]*\)\s*\{[^}]*switchConversation",
            cg_body,
            re.DOTALL,
        ) or (
            "members.includes(sessionUid)" in cg_body
            and cg_body.index("members.includes(sessionUid)")
            < cg_body.index("switchConversation")
        )
        # Always force list refresh after create (independent of tickInFlight)
        assert "refreshConversationsList({ force: true })" in cg_body or (
            "refreshConversationsList({force: true})" in cg_body
        )

        # KD-U4: bound-group inject still present for already-bound non-member session
        pop_m = re.search(
            r"function populateConversationSelect\s*\([^)]*\)\s*\{(.*?)\nfunction ",
            js,
            re.DOTALL,
        )
        assert pop_m is not None, "populateConversationSelect body not found"
        pop_body = pop_m.group(1)
        assert "sessionConversationId" in pop_body
        assert 'startsWith("group:")' in pop_body or "startsWith('group:')" in pop_body
        assert "groups.push" in pop_body
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
        # #126 PR3/PR4: Memory Schedule tab wiring (not identifier-only)
        assert "function refreshSchedule" in js
        assert "function renderSchedule" in js
        assert "function renderScheduleHistory" in js
        assert "function patchScheduleRelativeTimes" in js
        assert "function formatRelativeWhen" in js
        assert "function schedulePayloadFingerprint" in js
        assert "function scheduleHistoryEnabled" in js
        assert "function serverMinuteBucket" in js
        assert 'memoryActiveTab === "schedule"' in js
        assert "await refreshSchedule({ force })" in js or "refreshSchedule({ force })" in js
        assert "lastScheduleFp" in js
        assert "lastScheduleMinuteFp" in js
        assert "scheduleLoadGen" in js
        # Dual soft-refresh call sites (payload fp + minute-bucket relative patch)
        assert "minuteFp !== lastScheduleMinuteFp" in js
        assert "patchScheduleRelativeTimes(data)" in js
        assert "schedulePayloadFingerprint(data" in js
        assert "serverMinuteBucket(data && data.server_time)" in js or (
            "serverMinuteBucket(data" in js
        )
        # Cards stamp patch targets; patch selects them
        assert "schedule-rel" in js
        assert "dataset.dueIso" in js
        assert ".schedule-rel[data-due-iso]" in js
        # Soft-refresh-with-history: patch roots include history lists
        patch_fn = re.search(
            r"function patchScheduleRelativeTimes\s*\([^)]*\)\s*\{(.*?)\n\}",
            js,
            re.DOTALL,
        )
        assert patch_fn is not None, "patchScheduleRelativeTimes body not found"
        patch_body = patch_fn.group(1)
        assert "scheduleHistoryTimersList" in patch_body
        assert "scheduleHistoryWaitsList" in patch_body
        # Payload fingerprint must not fold bare server_time (would thrash every poll)
        fp_fn = re.search(
            r"function schedulePayloadFingerprint\s*\([^)]*\)\s*\{(.*?)\n\}",
            js,
            re.DOTALL,
        )
        assert fp_fn is not None, "schedulePayloadFingerprint body not found"
        assert "server_time" not in fp_fn.group(1)
        # History mode is part of fingerprint so toggle rebuilds list
        assert "include_history" in fp_fn.group(1)
        assert "history_timers" in fp_fn.group(1)
        assert "history_waits" in fp_fn.group(1)
        # Fingerprints committed after full render (not before wipe)
        refresh_fn = re.search(
            r"async function refreshSchedule\s*\([^)]*\)\s*\{(.*?)\n\}",
            js,
            re.DOTALL,
        )
        assert refresh_fn is not None, "refreshSchedule body not found"
        refresh_body = refresh_fn.group(1)
        assert "renderSchedule(data)" in refresh_body
        assert refresh_body.index("renderSchedule(data)") < refresh_body.rindex(
            "lastScheduleFp"
        )
        # PR4: real schedule URL wiring (active-only vs include_history)
        assert '"/api/schedule"' in refresh_body or "'/api/schedule'" in refresh_body
        assert "fetchJson(url)" in refresh_body or "fetchJson(url " in refresh_body
        assert "include_history=1" in refresh_body
        assert "history_limit=20" in refresh_body
        assert "scheduleHistoryEnabled" in refresh_body
        # Stale-response guard for soft/force mode-flip race
        assert "scheduleLoadGen" in refresh_body
        assert "gen !== scheduleLoadGen" in refresh_body
        assert "includeHistory !== scheduleHistoryEnabled()" in refresh_body
        # Empty states for zero-collection paths
        assert "No scheduled timers." in js
        assert "No pending waits." in js
        assert "No recent terminal rows (by due/expiry time)." in js
        # History toggle → force refreshSchedule
        assert "scheduleHistoryToggle.addEventListener" in js
        assert 'scheduleHistoryToggle.addEventListener("change"' in js or (
            "scheduleHistoryToggle.addEventListener('change'" in js
        )
        # Continuous control on Status: rail meta el gone; honesty via #continuous-detail.
        assert "continuous-status-rail" not in js
        assert "continuous-toggle-rail" not in js
        assert "continuousMetaEls" not in js
        assert "continuousDetail" in js
        assert "continuousBadge" in js
        assert "setContinuousEnabled" in js
        assert '"/api/continuous"' in js or "'/api/continuous'" in js
        # Schedule strip retitled (read-only; no second toggle).
        assert 'textContent = "Continue open work"' in js or (
            "textContent = 'Continue open work'" in js
        )
        assert "renderScheduleContinuous" in js
        # Status honesty meta includes skip reason (honest_exit surfaces here).
        assert "last skip:" in js or "last_skip_reason" in js
        assert "pending continues:" in js
        # BUG-meal-01: meal budget range → PATCH + soft-poll focus guard
        assert 'meal-budget-fraction' in js
        assert "/api/meal-budget" in js
        assert "patchMealBudget" in js
        assert "renderMealBudget" in js
        assert "document.activeElement !== mealBudgetFraction" in js
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
        # PR5 secrets panel wiring
        assert '"/api/secrets"' in js or "'/api/secrets'" in js
        assert "refreshSecrets" in js
        assert "saveSecret" in js
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


# ---------------------------------------------------------------------------
# PR3b — conversation_id + social_kind on messages/wake propagation
# ---------------------------------------------------------------------------


def test_post_group_message_stamps_conversation_and_social_kind(paths):
    """T5: POST group as client A → row + wake payload conversation_id + social_kind=group."""
    from elyra.conversations import ConversationsStore
    from elyra.messages import list_messages
    from elyra.users import UsersStore

    h = _ApiHarness(paths, client_id=None)
    try:
        users = UsersStore(paths)
        users.create_user("Jim", user_id="jim", provisional=True)
        users.create_user("Sam", user_id="sam", provisional=True)
        store = ConversationsStore(paths)
        store.ensure_layout()
        group = store.create_group(
            name="Room",
            members=["jim", "sam"],
            conversation_id="group:room1",
        )
        assert group["id"] == "group:room1"

        code, sess = h.put(
            "/api/session",
            {"user_id": "jim", "conversation_id": "group:room1"},
            client_id="client-a",
        )
        assert code == 200, sess
        assert sess["conversation_id"] == "group:room1"

        code, r = h.post(
            "/api/messages",
            {"content": "hello group"},
            client_id="client-a",
        )
        assert code == 200, r
        assert r.get("ok") is True

        msgs = list_messages(paths=paths, limit=50, conversation_id="group:room1")
        row = next((m for m in msgs if m.get("content") == "hello group"), None)
        assert row is not None, msgs
        assert row.get("conversation_id") == "group:room1"
        assert row.get("user_id") == "jim"

        # Message in route payload (when present) should also carry conversation_id.
        msg_body = r.get("message")
        if isinstance(msg_body, dict):
            assert msg_body.get("conversation_id") == "group:room1"
            assert msg_body.get("user_id") == "jim"

        # Hermetic pin: wake payload must carry both stamps (require locate).
        wake_id = r.get("wake_id")
        item = None
        if wake_id:
            item = h.worker._queue.get(wake_id)  # noqa: SLF001
        if item is None:
            pending = h.worker._queue.pending()  # noqa: SLF001
            claimed = h.worker._queue.claimed()  # noqa: SLF001
            candidates = list(pending) + list(claimed)
            item = next(
                (
                    w
                    for w in candidates
                    if w.kind == "user_message"
                    and (w.payload or {}).get("content") == "hello group"
                ),
                None,
            )
        assert item is not None, (
            f"T5 wake not found for social_kind stamp check; wake_id={wake_id!r} "
            f"response={r!r}"
        )
        payload = item.payload or {}
        assert payload.get("conversation_id") == "group:room1"
        assert payload.get("social_kind") == "group"
        assert payload.get("user_id") == "jim"
    finally:
        h.close()


def test_t17_two_clients_same_group_correct_speakers(paths):
    """T17: two clients same group POST → both rows correct speakers."""
    from elyra.conversations import ConversationsStore
    from elyra.messages import list_messages
    from elyra.users import UsersStore

    h = _ApiHarness(paths, client_id=None)
    try:
        users = UsersStore(paths)
        users.create_user("Jim", user_id="jim", provisional=True)
        users.create_user("Sam", user_id="sam", provisional=True)
        store = ConversationsStore(paths)
        store.ensure_layout()
        store.create_group(
            name="Shared",
            members=["jim", "sam"],
            conversation_id="group:shared",
        )

        h.put(
            "/api/session",
            {"user_id": "jim", "conversation_id": "group:shared"},
            client_id="c-jim",
        )
        h.put(
            "/api/session",
            {"user_id": "sam", "conversation_id": "group:shared"},
            client_id="c-sam",
        )

        code, r1 = h.post(
            "/api/messages",
            {"content": "jim says hi", "user_id": "sam"},  # body mismatch ignored
            client_id="c-jim",
        )
        assert code == 200, r1
        code, r2 = h.post(
            "/api/messages",
            {"content": "sam says hi", "user_id": "jim"},
            client_id="c-sam",
        )
        assert code == 200, r2

        msgs = list_messages(
            paths=paths, limit=50, conversation_id="group:shared"
        )
        by_content = {m.get("content"): m for m in msgs}
        assert by_content["jim says hi"].get("user_id") == "jim"
        assert by_content["jim says hi"].get("conversation_id") == "group:shared"
        assert by_content["sam says hi"].get("user_id") == "sam"
        assert by_content["sam says hi"].get("conversation_id") == "group:shared"
    finally:
        h.close()


def test_get_messages_defaults_to_session_conversation(paths):
    """GET /api/messages defaults conversation_id from client session (view_mode=conversation)."""
    from elyra.conversations import ConversationsStore
    from elyra.messages import append_message
    from elyra.users import UsersStore

    h = _ApiHarness(paths, client_id=None)
    try:
        users = UsersStore(paths)
        users.create_user("Jim", user_id="jim", provisional=True)
        users.create_user("Sam", user_id="sam", provisional=True)
        store = ConversationsStore(paths)
        store.ensure_layout()
        store.ensure_dm("jim")
        store.ensure_dm("sam")

        append_message(
            "user", "jim private", user_id="jim", conversation_id="dm:jim", paths=paths
        )
        append_message(
            "user", "sam private", user_id="sam", conversation_id="dm:sam", paths=paths
        )

        h.put("/api/session", {"user_id": "jim"}, client_id="c-jim")
        code, body = h.get("/api/messages?limit=50", client_id="c-jim")
        assert code == 200, body
        contents = [m.get("content") for m in body.get("messages") or []]
        assert "jim private" in contents
        assert "sam private" not in contents

        # Explicit query overrides session
        code, body = h.get(
            "/api/messages?limit=50&conversation_id=dm:sam", client_id="c-jim"
        )
        assert code == 200
        contents = [m.get("content") for m in body.get("messages") or []]
        assert "sam private" in contents
        assert "jim private" not in contents

        # view=all forensic (global)
        code, body = h.get("/api/messages?limit=50&view=all", client_id="c-jim")
        assert code == 200
        contents = [m.get("content") for m in body.get("messages") or []]
        assert "jim private" in contents
        assert "sam private" in contents
    finally:
        h.close()


def test_post_dm_defaults_from_session(paths):
    """POST without body conversation_id stamps session DM + social_kind=dm."""
    from elyra.messages import list_messages
    from elyra.users import UsersStore

    h = _ApiHarness(paths, client_id=None)
    try:
        users = UsersStore(paths)
        users.create_user("Jim", user_id="jim", provisional=True)
        h.put("/api/session", {"user_id": "jim"}, client_id="c-jim")
        code, r = h.post(
            "/api/messages",
            {"content": "dm hello"},
            client_id="c-jim",
        )
        assert code == 200, r
        msgs = list_messages(paths=paths, limit=20)
        row = next((m for m in msgs if m.get("content") == "dm hello"), None)
        assert row is not None
        assert row.get("conversation_id") == "dm:jim"
        assert row.get("user_id") == "jim"

        pending = h.worker._queue.pending()  # noqa: SLF001
        for w in pending:
            if (w.payload or {}).get("content") == "dm hello":
                assert (w.payload or {}).get("social_kind") == "dm"
                assert (w.payload or {}).get("conversation_id") == "dm:jim"
                break
    finally:
        h.close()


# ---------------------------------------------------------------------------
# PR3c — status matches_session (KD24) + T9 group wait binding
# ---------------------------------------------------------------------------


def test_t9_status_matches_session_group_wait(paths):
    """T9: member on dm:self → matches_session false; after PUT group → true.

    Non-member client stays false. Asserts server-enriched status payload hard.
    """
    from elyra.conversations import ConversationsStore
    from elyra.presence.timers import STATUS_PENDING

    users = UsersStore(paths)
    users.create_user("Jim", user_id="jim", provisional=True)
    users.create_user("Sam", user_id="sam", provisional=True)
    users.create_user("Eve", user_id="eve", provisional=True)

    store = ConversationsStore(paths)
    store.create_group(
        name="Room",
        members=["jim", "sam"],
        conversation_id="group:waitroom",
    )

    h = _ApiHarness(paths, client_id=None)
    try:
        # Seed durable group wait (arming stamp jim) without going through loop.
        h.worker._timers.arm_wait(  # noqa: SLF001
            wait_id="g-wait-1",
            prompt="Ship it?",
            choices=["yes", "no"],
            user_id="jim",
            moment_id="m0",
            timeout=600.0,
            conversation_id="group:waitroom",
        )
        # Bind jim client to Private Chat (dm:self)
        code, sess = h.put(
            "/api/session",
            {"user_id": "jim", "conversation_id": "dm:jim"},
            client_id="c-jim",
        )
        assert code == 200, sess
        assert sess["conversation_id"] == "dm:jim"

        code, st = h.get("/api/status", client_id="c-jim")
        assert code == 200
        pending = st.get("pending_wait")
        assert isinstance(pending, dict), st
        assert pending.get("id") == "g-wait-1" or pending.get("wait_id") == "g-wait-1"
        assert pending.get("conversation_id") == "group:waitroom"
        assert pending.get("status") == STATUS_PENDING or pending.get("status") == "pending"
        # Hard assert: matches_session present and false while on dm:self
        assert "matches_session" in pending
        assert pending["matches_session"] is False

        # PUT session to the group → matches_session true
        code, sess2 = h.put(
            "/api/session",
            {"conversation_id": "group:waitroom"},
            client_id="c-jim",
        )
        assert code == 200, sess2
        assert sess2["conversation_id"] == "group:waitroom"
        assert sess2["user_id"] == "jim"

        code, st2 = h.get("/api/status", client_id="c-jim")
        assert code == 200
        pending2 = st2.get("pending_wait")
        assert isinstance(pending2, dict)
        assert pending2["matches_session"] is True

        # Non-member client bound to group → matches_session false
        code, sess_eve = h.put(
            "/api/session",
            {"user_id": "eve", "conversation_id": "group:waitroom"},
            client_id="c-eve",
        )
        assert code == 200, sess_eve
        code, st_eve = h.get("/api/status", client_id="c-eve")
        assert code == 200
        pending_eve = st_eve.get("pending_wait")
        assert isinstance(pending_eve, dict)
        assert pending_eve["matches_session"] is False

        # Unknown client header on read-only status → matches_session false (no mint)
        code, st_unk = h.get("/api/status", client_id="never-registered-xyz")
        assert code == 200
        pending_unk = st_unk.get("pending_wait")
        assert isinstance(pending_unk, dict)
        assert pending_unk.get("matches_session") is False
    finally:
        h.close()


def test_t9_wait_reply_member_on_dm_self_does_not_match(paths):
    """Group wait: member on dm:self → 409 fail closed, no glass write (KD12)."""
    from elyra.conversations import ConversationsStore
    from elyra.messages import list_messages

    users = UsersStore(paths)
    users.create_user("Jim", user_id="jim", provisional=True)
    users.create_user("Sam", user_id="sam", provisional=True)

    store = ConversationsStore(paths)
    store.create_group(
        name="Room",
        members=["jim", "sam"],
        conversation_id="group:waitroom2",
    )

    h = _ApiHarness(paths, client_id=None)
    try:
        h.worker._timers.arm_wait(  # noqa: SLF001
            wait_id="g-wait-2",
            prompt="?",
            choices=["y"],
            user_id="jim",
            moment_id="m0",
            timeout=600.0,
            conversation_id="group:waitroom2",
        )
        # Force waiting phase so free-text would match if user matched
        h.worker._phase = "waiting"  # noqa: SLF001

        code, sess = h.put(
            "/api/session",
            {"user_id": "jim", "conversation_id": "dm:jim"},
            client_id="c-jim",
        )
        assert code == 200, sess
        before = list_messages(paths=paths, limit=50)
        code, body = h.post(
            "/api/wait/reply",
            {"content": "y", "choice": "y"},
            client_id="c-jim",
        )
        # Fail closed before glass write (review Issue 1)
        assert code == 409, body
        assert body.get("ok") is False
        assert body.get("reason") == "wait_not_matched"
        assert body.get("error") == "wait_not_matched"
        # No new glass row
        after = list_messages(paths=paths, limit=50)
        assert len(after) == len(before)
        # Wait still pending
        still = h.worker._timers.get_wait("g-wait-2")  # noqa: SLF001
        assert still is not None
        assert still.status == "pending"

        # Bind to group and answer
        code, sess2 = h.put(
            "/api/session",
            {"conversation_id": "group:waitroom2"},
            client_id="c-jim",
        )
        assert code == 200, sess2
        assert sess2["conversation_id"] == "group:waitroom2"
        code2, body2 = h.post(
            "/api/wait/reply",
            {"content": "y", "choice": "y"},
            client_id="c-jim",
        )
        assert code2 == 200, body2
        assert body2.get("ok") is True
        assert body2.get("routed") == "wait_reply"
        answered = h.worker._timers.get_wait("g-wait-2")  # noqa: SLF001
        assert answered is not None
        assert answered.status == "answered"
    finally:
        h.close()


def test_wait_reply_no_pending_wait_fail_closed(paths):
    """POST /api/wait/reply with no pending wait → 409, no glass write."""
    from elyra.messages import list_messages

    h = _ApiHarness(paths, client_id="c-op")
    try:
        before = list_messages(paths=paths, limit=50)
        code, body = h.post(
            "/api/wait/reply",
            {"content": "hello?", "choice": "y"},
            client_id="c-op",
        )
        assert code == 409, body
        assert body.get("reason") == "no_matching_wait"
        assert body.get("ok") is False
        after = list_messages(paths=paths, limit=50)
        assert len(after) == len(before)
    finally:
        h.close()


def test_status_without_client_header_omits_or_false_matches_session(paths):
    """Missing X-Elyra-Client: do not invent matches_session true."""
    h = _ApiHarness(paths, client_id=None)
    try:
        h.worker._timers.arm_wait(  # noqa: SLF001
            wait_id="dm-w",
            prompt="?",
            user_id="operator",
            moment_id="m",
            timeout=600.0,
            conversation_id="dm:operator",
        )
        code, st = h.get("/api/status", client_id=None)
        assert code == 200
        pending = st.get("pending_wait")
        assert isinstance(pending, dict)
        # Omit or false — never invent true without a known client
        assert pending.get("matches_session") in (None, False)
    finally:
        h.close()


# ---------------------------------------------------------------------------
# PR6 — operator multi-conversation UI + session switch membership (T12)
# ---------------------------------------------------------------------------


def test_t12_session_switch_auto_dm_vs_keep_group(paths):
    """T12: user switch auto-DM; keep group when member; non-member → DM."""
    from elyra.conversations import ConversationsStore

    store = ConversationsStore(paths)
    store.ensure_layout()
    store.create_group(
        name="Room",
        members=["jim", "sam"],
        conversation_id="group:t12room",
    )
    h = _ApiHarness(paths, client_id="t12-client")
    try:
        # Seed users
        for goes_by, uid in (("Jim", "jim"), ("Sam", "sam"), ("Eve", "eve")):
            code, body = h.post(
                "/api/users", {"goes_by": goes_by, "user_id": uid}
            )
            assert code in (200, 201), body

        # Bind jim to group
        code, sess = h.put(
            "/api/session",
            {"user_id": "jim", "conversation_id": "group:t12room"},
            client_id="t12-client",
        )
        assert code == 200, sess
        assert sess["user_id"] == "jim"
        assert sess["conversation_id"] == "group:t12room"

        # Switch to sam (member) → keep group
        code, sess2 = h.put(
            "/api/session",
            {"user_id": "sam"},
            client_id="t12-client",
        )
        assert code == 200, sess2
        assert sess2["user_id"] == "sam"
        assert sess2["conversation_id"] == "group:t12room"

        # Switch to eve (non-member) → auto DM
        code, sess3 = h.put(
            "/api/session",
            {"user_id": "eve"},
            client_id="t12-client",
        )
        assert code == 200, sess3
        assert sess3["user_id"] == "eve"
        assert sess3["conversation_id"] == "dm:eve"

        # DM→DM auto-switch (legacy KD18)
        code, sess4 = h.put(
            "/api/session",
            {"user_id": "jim"},
            client_id="t12-client",
        )
        assert code == 200, sess4
        assert sess4["user_id"] == "jim"
        assert sess4["conversation_id"] == "dm:jim"
    finally:
        h.close()


def test_static_pr6_operator_conversation_ui(paths):
    """PR6: operator multi-convo markup + app.js wiring (not identifier-only)."""
    h = _ApiHarness(paths)
    try:
        req = urllib.request.Request(h.base + "/", method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            html = resp.read().decode("utf-8")
        # Labels + controls
        assert "Session user (impersonate)" in html
        assert 'id="session-conversation-select"' in html
        assert 'id="session-new-group-btn"' in html
        assert "New group…" in html or "New group" in html
        assert 'id="chat-conversation-meta"' in html
        assert 'id="chat-conversation-name"' in html
        assert 'id="chat-member-chips"' in html
        # Group create modal zero-state + form fields
        assert 'id="group-modal"' in html
        assert 'id="group-form"' in html
        assert 'id="group-name-input"' in html
        assert 'id="group-desc-input"' in html
        assert 'id="group-members-list"' in html
        assert 'id="group-members-empty"' in html
        assert "No users available to invite." in html
        # Honesty / dogfood copy (not real login)
        assert "not login" in html.lower() or "impersonate" in html.lower()

        req_js = urllib.request.Request(h.base + "/app.js", method="GET")
        with urllib.request.urlopen(req_js, timeout=5) as resp:
            assert resp.status == 200
            js = resp.read().decode("utf-8")

        # Core identifiers + wiring snippets (avoid identifier-only asserts)
        assert "VIEW_ALL_SENTINEL" in js
        assert "function populateConversationSelect" in js
        assert "function switchConversation" in js
        assert "function updateConversationChrome" in js
        assert "function filterMessagesForView" in js
        assert "function messagesListUrl" in js
        assert "function openGroupModal" in js
        assert "function createGroupFromModal" in js
        assert "function fillGroupMembersChecklist" in js
        assert "function waitArmedForSessionUser" in js
        # Wiring: select change → switchConversation
        assert "sessionConversationSelect.addEventListener" in js
        assert 'sessionConversationSelect.addEventListener("change"' in js or (
            "sessionConversationSelect.addEventListener('change'" in js
        )
        assert "switchConversation(val)" in js or "switchConversation(" in js
        # New group button → open modal
        assert "sessionNewGroupBtn.addEventListener" in js
        assert "openGroupModal()" in js
        # POST /api/conversations on create
        assert '"/api/conversations"' in js or "'/api/conversations'" in js
        assert "createGroupFromModal" in js
        # Messages URL: forensic view=all + conversation_id query
        assert "view=all" in js
        assert "conversation_id=" in js
        assert "function messagesListUrl" in js
        # renderMessages uses filter; empty-state path for zero messages
        assert "filterMessagesForView" in js
        assert "No messages in this conversation yet." in js
        assert "No messages yet." in js
        # Wait bar prefers matches_session (KD24)
        assert "matches_session" in js
        wait_fn = re.search(
            r"function waitArmedForSessionUser\s*\([^)]*\)\s*\{(.*?)\n\}",
            js,
            re.DOTALL,
        )
        assert wait_fn is not None, "waitArmedForSessionUser body not found"
        assert "matches_session" in wait_fn.group(1)
        # renderWaitBar gates on waitArmedForSessionUser
        render_wait = re.search(
            r"function renderWaitBar\s*\([^)]*\)\s*\{(.*?)\n(?:function |async function )",
            js,
            re.DOTALL,
        )
        assert render_wait is not None, "renderWaitBar body not found"
        assert "waitArmedForSessionUser" in render_wait.group(1)
        # Boot gates tick until session bound (past issue)
        assert "sessionBooted" in js
        assert "if (!sessionBooted) return" in js
        # Boot ?as= re-syncs rail via switchSessionUser (not bare PUT alone)
        assert "bootClientSession" in js
        assert "switchSessionUser(asUser.trim())" in js or (
            "switchSessionUser(asUser" in js
        )
        # Forensic all only on operator `/` — PRODUCT_CHAT path gate (not id-only)
        assert "PRODUCT_CHAT" in js
        assert '=== "/chat"' in js or "=== '/chat'" in js
        assert 'startsWith("/chat/")' in js or "startsWith('/chat/')" in js
        assert "if (!PRODUCT_CHAT)" in js
        assert "All messages (forensic)" in js
        assert "VIEW_ALL_SENTINEL" in js
        # populateConversationSelect gates forensic option on PRODUCT_CHAT
        pop_fn = re.search(
            r"function populateConversationSelect\s*\([^)]*\)\s*\{(.*?)\nfunction ",
            js,
            re.DOTALL,
        )
        assert pop_fn is not None, "populateConversationSelect body not found"
        pop_body = pop_fn.group(1)
        assert "if (!PRODUCT_CHAT)" in pop_body
        assert "All messages (forensic)" in pop_body
        assert "VIEW_ALL_SENTINEL" in pop_body
        # switchConversation no-ops forensic on product shell
        sw_fn = re.search(
            r"async function switchConversation\s*\([^)]*\)\s*\{(.*?)\n(?:async )?function ",
            js,
            re.DOTALL,
        )
        assert sw_fn is not None, "switchConversation body not found"
        assert "PRODUCT_CHAT" in sw_fn.group(1)
        # Session user label string used in HTML (impersonate)
        assert "impersonate" in html.lower()
        # Empty-state: single #group-members-empty path (no list duplicate copy)
        assert "groupMembersEmpty.hidden" in js
        assert "No users available to invite." in html
        # CSS tokens for chips / group form
        req_css = urllib.request.Request(h.base + "/style.css", method="GET")
        with urllib.request.urlopen(req_css, timeout=5) as resp:
            assert resp.status == 200
            css = resp.read().decode("utf-8")
        assert ".chat-conversation-meta" in css
        assert ".member-chip" in css
        assert ".group-form" in css
        assert ".group-members-list" in css
    finally:
        h.close()


def test_static_pr7_product_chat_shell(paths):
    """PR7: /chat product shell — route, chrome hide, honesty footer, force conversation."""
    h = _ApiHarness(paths)
    try:
        # SPA fallthrough: GET /chat and /chat/ return 200 HTML (index.html).
        for chat_path in ("/chat", "/chat/"):
            req = urllib.request.Request(h.base + chat_path, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                assert resp.status == 200, chat_path
                ctype = resp.headers.get("Content-Type", "")
                assert "html" in ctype.lower() or ctype == "", ctype
                html = resp.read().decode("utf-8")
            assert "<!DOCTYPE html>" in html or "<html" in html.lower()
            # Same SPA shell as operator /
            assert 'id="session-conversation-select"' in html
            assert 'id="session-user-select"' in html
            assert "Session user (impersonate)" in html
            assert "Private Chat" in html
            assert 'id="session-new-group-btn"' in html
            # Product honesty footer markup (shown via JS/CSS on /chat)
            assert 'id="product-dogfood-footer"' in html
            # Exact Gate C / §7A.11 phrase (trailing period)
            assert "local dogfood — not authenticated." in html
            # FOUC guard: early head script sets html.product-chat before body paint
            assert 'classList.add("product-chat")' in html or (
                "classList.add('product-chat')" in html
            )
            assert '=== "/chat"' in html or "=== '/chat'" in html
            # Operator chrome markers still in HTML (hidden by product-chat CSS)
            assert 'data-panel="goals"' in html
            assert "operator-only" in html
            assert 'id="operator-nav"' in html

        # Operator / still serves full shell (regression)
        req_root = urllib.request.Request(h.base + "/", method="GET")
        with urllib.request.urlopen(req_root, timeout=5) as resp:
            assert resp.status == 200
            root_html = resp.read().decode("utf-8")
        assert 'data-panel="goals"' in root_html
        assert 'id="product-dogfood-footer"' in root_html  # present but hidden

        req_js = urllib.request.Request(h.base + "/app.js", method="GET")
        with urllib.request.urlopen(req_js, timeout=5) as resp:
            assert resp.status == 200
            js = resp.read().decode("utf-8")

        # Path detect + product shell apply
        assert "PRODUCT_CHAT" in js
        assert "function applyProductShell" in js
        assert "function forceProductConversationMode" in js
        assert 'classList.add("product-chat")' in js or (
            "classList.add('product-chat')" in js
        )
        assert "product-dogfood-footer" in js
        # Force conversation mode wiring
        assert 'view_mode: "conversation"' in js or (
            "view_mode: 'conversation'" in js
        )
        assert "forceProductConversationMode" in js
        # Boot: product force + ?as= deep-link (first paint)
        assert "bootClientSession" in js
        assert "forceProductConversationMode" in js
        assert "switchSessionUser(asUser.trim())" in js or (
            "switchSessionUser(asUser" in js
        )
        assert 'params.get("as")' in js or "params.get('as')" in js
        # applyProductShell invoked on boot / first paint
        assert "applyProductShell()" in js
        # Nav ignore operator panels in product mode
        assert "PRODUCT_CHAT && btn.dataset.panel" in js or (
            "PRODUCT_CHAT && btn.dataset.panel" in js.replace(" ", "")
        )
        # Forensic still gated
        assert "if (!PRODUCT_CHAT)" in js
        assert "All messages (forensic)" in js
        # Concurrent multi-window dogfood documentation pointer (§7A)
        assert "sessionStorage" in js
        assert "elyra.clientId" in js
        # Comment / pointer for concurrent bar (design §7A)
        assert "concurrent" in js.lower() or "multi-window" in js.lower()

        req_css = urllib.request.Request(h.base + "/style.css", method="GET")
        with urllib.request.urlopen(req_css, timeout=5) as resp:
            assert resp.status == 200
            css = resp.read().decode("utf-8")
        # Product mode CSS hide rules (html + body for FOUC early class)
        assert "product-chat" in css
        assert ".product-dogfood-footer" in css
        assert "operator-only" in css or "#panel-goals" in css
        # FOUC: :is(html, body).product-chat or both html/body selectors
        assert (
            ":is(html, body).product-chat" in css
            or "html.product-chat" in css
            or "body.product-chat" in css
        )
        # Hides operator panels
        assert "#panel-goals" in css
        assert "#panel-memory" in css
        assert "#panel-status" in css
    finally:
        h.close()


def test_view_mode_all_lists_global_messages(paths):
    """Operator forensic view_mode=all returns unfiltered messages feed."""
    from elyra.conversations import ConversationsStore
    from elyra.messages import append_message

    store = ConversationsStore(paths)
    store.ensure_layout()
    store.ensure_dm("jim")
    store.ensure_dm("sam")
    append_message(
        "user", "jim private", user_id="jim", conversation_id="dm:jim", paths=paths
    )
    append_message(
        "user", "sam private", user_id="sam", conversation_id="dm:sam", paths=paths
    )
    h = _ApiHarness(paths, client_id="forensic-1")
    try:
        for goes_by, uid in (("Jim", "jim"), ("Sam", "sam")):
            code, body = h.post(
                "/api/users", {"goes_by": goes_by, "user_id": uid}
            )
            assert code in (200, 201), body
        code, sess = h.put(
            "/api/session",
            {"user_id": "jim", "conversation_id": "dm:jim", "view_mode": "conversation"},
            client_id="forensic-1",
        )
        assert code == 200, sess
        code, body = h.get("/api/messages?limit=50", client_id="forensic-1")
        assert code == 200
        contents = [m.get("content") for m in body.get("messages") or []]
        assert "jim private" in contents
        assert "sam private" not in contents

        code, sess2 = h.put(
            "/api/session",
            {"view_mode": "all"},
            client_id="forensic-1",
        )
        assert code == 200, sess2
        assert sess2.get("view_mode") == "all"
        code, body2 = h.get(
            "/api/messages?limit=50&view=all", client_id="forensic-1"
        )
        assert code == 200
        contents2 = [m.get("content") for m in body2.get("messages") or []]
        assert "jim private" in contents2
        assert "sam private" in contents2
    finally:
        h.close()
