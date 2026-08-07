"""HTTP /api/conversations CRUD (C12 PR3d).

Hermetic: real ConversationsStore under tmp ElyraPaths via start_api_server.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.conversations import ConversationsStore
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
        self.conversations = ConversationsStore(paths)
        self.users = UsersStore(paths)
        self.server, self._api_thread = start_api_server(
            config,
            paths=paths,
            gate=self.gate,
            state=self.state,
            worker=self.worker,
            goals=goals,
            moments=moments,
            identity=IdentityStore(paths),
            users=self.users,
            conversations=self.conversations,
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


def _cid_path(conversation_id: str) -> str:
    """URL-encode conversation_id for path segment (colon stays readable)."""
    return "/api/conversations/" + urllib.parse.quote(conversation_id, safe=":")


# ── create group / ensure DM ─────────────────────────────────────────────────


def test_create_group_list_and_detail(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.post(
            "/api/conversations",
            {
                "name": "Dogfood",
                "members": ["jim", "operator", "sam"],
                "description": "C12 room",
            },
        )
        assert code == 201, body
        assert body["ok"] is True
        conv = body["conversation"]
        assert conv["type"] == "group"
        assert conv["name"] == "Dogfood"
        assert conv["description"] == "C12 room"
        assert conv["members"] == ["jim", "operator", "sam"]
        assert conv["id"].startswith("group:")
        assert "member_labels" in conv
        assert set(conv["member_labels"].keys()) == {"jim", "operator", "sam"}
        # Labels fall back to user_id when no profile meta.
        assert conv["member_labels"]["jim"] == "jim"

        code, listed = h.get("/api/conversations")
        assert code == 200
        ids = {c["id"] for c in listed["conversations"]}
        assert conv["id"] in ids

        code, detail = h.get(_cid_path(conv["id"]))
        assert code == 200
        assert detail["conversation"]["id"] == conv["id"]
        assert detail["conversation"]["member_labels"]["sam"] == "sam"
    finally:
        h.close()


def test_create_group_with_explicit_id(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.post(
            "/api/conversations",
            {
                "type": "group",
                "name": "Room",
                "members": ["jim", "jim", "sam"],
                "conversation_id": "group:room1",
            },
        )
        assert code == 201, body
        assert body["conversation"]["id"] == "group:room1"
        assert body["conversation"]["members"] == ["jim", "sam"]

        # Duplicate id → 400
        code, err = h.post(
            "/api/conversations",
            {
                "name": "Dup",
                "members": ["jim"],
                "conversation_id": "group:room1",
            },
        )
        assert code == 400
        assert err["ok"] is False
    finally:
        h.close()


def test_ensure_dm_idempotent(paths):
    h = _ApiHarness(paths)
    try:
        code, a = h.post(
            "/api/conversations",
            {"type": "dm", "user_id": "jim"},
        )
        assert code == 200, a
        assert a["ok"] is True
        assert a["conversation"]["id"] == "dm:jim"
        assert a["conversation"]["type"] == "dm"
        assert a["conversation"]["members"] == ["jim"]

        code, b = h.post(
            "/api/conversations",
            {"type": "dm", "user_id": "jim"},
        )
        assert code == 200
        assert b["conversation"]["id"] == "dm:jim"
        assert b["conversation"]["created_at"] == a["conversation"]["created_at"]

        code, listed = h.get("/api/conversations?type=dm")
        assert code == 200
        assert len(listed["conversations"]) == 1
    finally:
        h.close()


# ── list by member ───────────────────────────────────────────────────────────


def test_list_by_member(paths):
    h = _ApiHarness(paths)
    try:
        h.post("/api/conversations", {"type": "dm", "user_id": "jim"})
        h.post("/api/conversations", {"type": "dm", "user_id": "sam"})
        h.post(
            "/api/conversations",
            {
                "name": "Both",
                "members": ["jim", "sam"],
                "conversation_id": "group:both",
            },
        )
        h.post(
            "/api/conversations",
            {
                "name": "Jim only",
                "members": ["jim"],
                "conversation_id": "group:jonly",
            },
        )

        code, jim = h.get("/api/conversations?member=jim")
        assert code == 200
        jim_ids = {c["id"] for c in jim["conversations"]}
        assert jim_ids == {"dm:jim", "group:both", "group:jonly"}

        code, sam = h.get("/api/conversations?member=sam")
        assert code == 200
        sam_ids = {c["id"] for c in sam["conversations"]}
        assert sam_ids == {"dm:sam", "group:both"}
    finally:
        h.close()


def test_list_empty_zero_state(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/conversations")
        assert code == 200
        assert body["conversations"] == []
    finally:
        h.close()


# ── patch members / name / description ───────────────────────────────────────


def test_patch_members_name_description(paths):
    h = _ApiHarness(paths)
    try:
        code, created = h.post(
            "/api/conversations",
            {
                "name": "Old",
                "members": ["jim"],
                "conversation_id": "group:upd",
                "description": "d1",
            },
        )
        assert code == 201, created
        cid = created["conversation"]["id"]

        code, patched = h.patch(
            _cid_path(cid),
            {"name": "New", "description": "d2", "members": ["jim", "sam"]},
        )
        assert code == 200, patched
        assert patched["ok"] is True
        conv = patched["conversation"]
        assert conv["name"] == "New"
        assert conv["description"] == "d2"
        assert conv["members"] == ["jim", "sam"]
        assert set(conv["member_labels"].keys()) == {"jim", "sam"}

        code, detail = h.get(_cid_path(cid))
        assert code == 200
        assert detail["conversation"]["name"] == "New"
        assert detail["conversation"]["members"] == ["jim", "sam"]
    finally:
        h.close()


def test_patch_no_fields_400(paths):
    h = _ApiHarness(paths)
    try:
        h.post("/api/conversations", {"type": "dm", "user_id": "jim"})
        code, body = h.patch(_cid_path("dm:jim"), {})
        assert code == 400
        assert body["ok"] is False
        assert "no update" in body["error"]
    finally:
        h.close()


def test_patch_dm_members_must_match_peer(paths):
    h = _ApiHarness(paths)
    try:
        h.post("/api/conversations", {"type": "dm", "user_id": "jim"})
        code, body = h.patch(_cid_path("dm:jim"), {"members": ["sam"]})
        assert code == 400
        assert body["ok"] is False
    finally:
        h.close()


# ── 404 / validation / path jail ─────────────────────────────────────────────


def test_get_unknown_conversation_404(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get(_cid_path("group:missing"))
        assert code == 404
        assert body["ok"] is False
        assert "not found" in body["error"]

        code, body = h.patch(
            _cid_path("group:missing"),
            {"name": "x"},
        )
        assert code == 404
        assert body["ok"] is False
    finally:
        h.close()


def test_path_jail_rejects_traversal_ids(paths):
    h = _ApiHarness(paths)
    try:
        bad_ids = [
            "dm:../x",
            "dm:a/b",
            "group:../escape",
            "group:a/b",
            "room:xyz",
            "jim",
            # empty id is list route (/api/conversations/), not detail
        ]
        for bad in bad_ids:
            # Percent-encode so the server sees the raw bad form after unquote.
            path = "/api/conversations/" + urllib.parse.quote(bad, safe="")
            code, body = h.get(path)
            assert code == 400, (bad, code, body)
            assert body.get("ok") is False

            code, body = h.patch(path, {"name": "x"})
            assert code == 400, (bad, code, body)
    finally:
        h.close()


def test_member_query_rejects_invalid_user_id(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/conversations?member=../x")
        assert code == 400
        assert body["ok"] is False
    finally:
        h.close()


def test_post_rejects_invalid_members_and_missing_fields(paths):
    h = _ApiHarness(paths)
    try:
        # Empty name
        code, body = h.post(
            "/api/conversations",
            {"name": "  ", "members": ["jim"]},
        )
        assert code == 400
        assert body["ok"] is False

        # Empty members
        code, body = h.post(
            "/api/conversations",
            {"name": "X", "members": []},
        )
        assert code == 400
        assert body["ok"] is False

        # Path-traversal member (must not be stored)
        code, body = h.post(
            "/api/conversations",
            {"name": "X", "members": ["../etc"]},
        )
        assert code == 400
        assert body["ok"] is False
        assert h.conversations.list() == []

        # Non-string member
        code, body = h.post(
            "/api/conversations",
            {"name": "X", "members": [123]},
        )
        assert code == 400
        assert body["ok"] is False

        # DM without user_id
        code, body = h.post("/api/conversations", {"type": "dm"})
        assert code == 400
        assert body["ok"] is False

        # DM with bad user_id
        code, body = h.post(
            "/api/conversations",
            {"type": "dm", "user_id": "../x"},
        )
        assert code == 400
        assert body["ok"] is False

        # Invalid type string
        code, body = h.post(
            "/api/conversations",
            {"type": "channel", "name": "X", "members": ["jim"]},
        )
        assert code == 400
        assert body["ok"] is False

        # Non-string type must 400 (not fall through to group create).
        code, body = h.post(
            "/api/conversations",
            {"type": 1, "name": "Z", "members": ["jim"]},
        )
        assert code == 400
        assert body["ok"] is False
        assert h.conversations.list() == []

        code, body = h.post(
            "/api/conversations",
            {"type": True, "name": "Z", "members": ["jim"]},
        )
        assert code == 400
        assert body["ok"] is False
    finally:
        h.close()


def test_patch_rejects_invalid_members(paths):
    h = _ApiHarness(paths)
    try:
        h.post(
            "/api/conversations",
            {
                "name": "G",
                "members": ["jim"],
                "conversation_id": "group:g1",
            },
        )
        code, body = h.patch(
            _cid_path("group:g1"),
            {"members": ["../x"]},
        )
        assert code == 400
        assert body["ok"] is False
        # Membership unchanged (no unvalidated input stored).
        rec = h.conversations.get("group:g1")
        assert rec is not None
        assert rec["members"] == ["jim"]

        code, body = h.patch(
            _cid_path("group:g1"),
            {"members": []},
        )
        assert code == 400
    finally:
        h.close()


def test_member_labels_use_display_label(paths):
    h = _ApiHarness(paths)
    try:
        # Seed a provisional user so display_label returns goes_by.
        created = h.users.create_user("Jimmy", user_id="jim", provisional=True)
        assert created.get("ok") is True

        code, body = h.post(
            "/api/conversations",
            {
                "name": "Labeled",
                "members": ["jim", "sam"],
                "conversation_id": "group:lbl",
            },
        )
        assert code == 201, body
        labels = body["conversation"]["member_labels"]
        assert labels["jim"] == "Jimmy"
        assert labels["sam"] == "sam"  # no profile → user_id

        code, detail = h.get(_cid_path("group:lbl"))
        assert code == 200
        assert detail["conversation"]["member_labels"]["jim"] == "Jimmy"
    finally:
        h.close()


# ── review fixes: type case, 503, path jail create, member strip ─────────────


def test_list_type_filter_case_insensitive(paths):
    h = _ApiHarness(paths)
    try:
        h.post("/api/conversations", {"type": "dm", "user_id": "jim"})
        h.post(
            "/api/conversations",
            {"name": "G", "members": ["jim"], "conversation_id": "group:g1"},
        )
        # Uppercase type token matches POST body normalization.
        code, body = h.get("/api/conversations?type=DM")
        assert code == 200, body
        assert {c["id"] for c in body["conversations"]} == {"dm:jim"}

        code, body = h.get("/api/conversations?type=Group")
        assert code == 200, body
        assert {c["id"] for c in body["conversations"]} == {"group:g1"}
    finally:
        h.close()


def test_list_invalid_type_400(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/conversations?type=channel")
        assert code == 400
        assert body["ok"] is False
    finally:
        h.close()


def test_post_type_dm_case_insensitive(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.post(
            "/api/conversations",
            {"type": "DM", "user_id": "sam"},
        )
        assert code == 200, body
        assert body["conversation"]["id"] == "dm:sam"
    finally:
        h.close()


def test_post_conversation_id_path_jail(paths):
    h = _ApiHarness(paths)
    try:
        # Traversal group id → 400, no write.
        code, body = h.post(
            "/api/conversations",
            {
                "name": "X",
                "members": ["jim"],
                "conversation_id": "group:../x",
            },
        )
        assert code == 400
        assert body["ok"] is False
        assert h.conversations.list() == []

        # dm:… is not a valid create_group id.
        code, body = h.post(
            "/api/conversations",
            {
                "name": "X",
                "members": ["jim"],
                "conversation_id": "dm:jim",
            },
        )
        assert code == 400
        assert body["ok"] is False
        assert h.conversations.list() == []
    finally:
        h.close()


def test_post_to_conversation_detail_404(paths):
    """Detail path is PATCH/GET only — POST falls through to 404."""
    h = _ApiHarness(paths)
    try:
        code, body = h.post(
            _cid_path("group:g1"),
            {"name": "Nope", "members": ["jim"]},
        )
        assert code == 404
    finally:
        h.close()


def test_conversations_503_while_resetting(paths):
    h = _ApiHarness(paths)
    try:
        with h.worker._lock:  # noqa: SLF001
            h.worker._continuous.resetting = True  # noqa: SLF001
        code, body = h.post(
            "/api/conversations",
            {"name": "X", "members": ["jim"]},
        )
        assert code == 503, body
        assert body.get("error") == "resetting"

        code, body = h.patch(
            _cid_path("group:g1"),
            {"name": "Y"},
        )
        assert code == 503, body
        assert body.get("error") == "resetting"
        # GET list remains available (catalog-style).
        code, body = h.get("/api/conversations")
        assert code == 200
    finally:
        with h.worker._lock:  # noqa: SLF001
            h.worker._continuous.resetting = False  # noqa: SLF001
        h.close()


def test_patch_unknown_id_bad_members_is_400_not_404(paths):
    """Member validation runs before existence — bad members always 400."""
    h = _ApiHarness(paths)
    try:
        code, body = h.patch(
            _cid_path("group:missing"),
            {"members": ["../x"]},
        )
        assert code == 400, body
        assert body["ok"] is False

        # Empty members also 400 (not 404) for unknown id.
        code, body = h.patch(
            _cid_path("group:missing"),
            {"members": []},
        )
        assert code == 400, body
    finally:
        h.close()


def test_members_strip_whitespace_consistent(paths):
    """Body members strip like ?member= (same policy)."""
    h = _ApiHarness(paths)
    try:
        code, body = h.post(
            "/api/conversations",
            {
                "name": "Spaced",
                "members": ["  jim  ", "sam"],
                "conversation_id": "group:sp",
            },
        )
        assert code == 201, body
        assert body["conversation"]["members"] == ["jim", "sam"]

        code, body = h.patch(
            _cid_path("group:sp"),
            {"members": ["  jim  ", "  operator  "]},
        )
        assert code == 200, body
        assert body["conversation"]["members"] == ["jim", "operator"]

        code, listed = h.get("/api/conversations?member=%20jim%20")
        assert code == 200, listed
        assert any(c["id"] == "group:sp" for c in listed["conversations"])
    finally:
        h.close()
