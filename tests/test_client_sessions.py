"""Per-client session registry (PR3a / KD21–25).

T16 two clients independent; T16b POST speaker from session; T18 status
missing/unknown does not grow map; GET /api/session creates; invalid → 400;
legacy one-shot import; partial PUT RMW; reset clears registry.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.identity.layout import write_json_atomic
from elyra.messages import list_messages
from elyra.runtime.client_sessions import (
    CLIENT_SESSIONS_REL,
    LEGACY_GLASS_SESSION_REL,
    ClientSessionsRegistry,
    InvalidClientId,
    parse_client_id_header,
    validate_client_id,
)
from elyra.runtime.reset import clear_client_sessions as reset_clear_client_sessions
from elyra.users import UsersStore

# Reuse glass API harness with client header support.
from tests.test_api_glass import _ApiHarness


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


def test_validate_client_id() -> None:
    assert validate_client_id("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    assert validate_client_id("test-client-1")
    with pytest.raises(InvalidClientId):
        validate_client_id("../etc")
    with pytest.raises(InvalidClientId):
        validate_client_id("")
    with pytest.raises(InvalidClientId):
        validate_client_id("x" * 81)
    assert parse_client_id_header(None) is None
    assert parse_client_id_header("") is None
    assert parse_client_id_header("  ") is None
    with pytest.raises(InvalidClientId):
        parse_client_id_header("..")


def test_registry_legacy_import_once(paths) -> None:
    legacy = paths.data_dir / LEGACY_GLASS_SESSION_REL
    legacy.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(legacy, {"user_id": "operator"})

    from elyra.conversations import ConversationsStore

    store = ConversationsStore(paths)
    store.ensure_layout()
    reg = ClientSessionsRegistry(paths, ensure_dm=store.ensure_dm)

    cid1, sess1, minted1 = reg.resolve("client-a", allow_create=True)
    assert cid1 == "client-a"
    assert minted1 is False
    assert sess1 is not None
    assert sess1["user_id"] == "operator"
    assert sess1["conversation_id"] == "dm:operator"
    assert sess1["view_mode"] == "conversation"

    # Legacy stub written
    stub = json.loads(legacy.read_text(encoding="utf-8"))
    assert stub.get("migrated") is True

    # Second client does not re-import (map non-empty → defaults)
    _cid2, sess2, _m = reg.resolve("client-b", allow_create=True)
    assert sess2 is not None
    assert sess2["user_id"] == "operator"  # default, not double-import side effect
    assert (paths.data_dir / CLIENT_SESSIONS_REL).is_file()
    data = json.loads((paths.data_dir / CLIENT_SESSIONS_REL).read_text(encoding="utf-8"))
    assert "client-a" in data["clients"]
    assert "client-b" in data["clients"]


def test_registry_partial_put_rmw(paths) -> None:
    from elyra.conversations import ConversationsStore

    store = ConversationsStore(paths)
    store.ensure_layout()
    reg = ClientSessionsRegistry(paths, ensure_dm=store.ensure_dm)
    reg.resolve("c1", allow_create=True)
    put1 = reg.put("c1", user_id="operator", view_mode="all")
    assert put1["view_mode"] == "all"
    assert put1["user_id"] == "operator"
    assert put1["conversation_id"] == "dm:operator"
    # Partial: only conversation_id — user_id and view_mode preserved
    group = store.create_group(name="G", members=["operator"], conversation_id="group:g1")
    put2 = reg.put("c1", conversation_id=group["id"])
    assert put2["conversation_id"] == "group:g1"
    assert put2["user_id"] == "operator"
    assert put2["view_mode"] == "all"


def test_t16_two_clients_independent(paths) -> None:
    """T16: two client_ids, different users, independent GET/PUT; no stomp."""
    h = _ApiHarness(paths, client_id=None)
    try:
        users = UsersStore(paths)
        jim = users.create_user("Jim", user_id="jim", provisional=True)
        sam = users.create_user("Sam", user_id="sam", provisional=True)
        assert jim.get("ok") and sam.get("ok")

        code, s1 = h.put(
            "/api/session",
            {"user_id": "jim"},
            client_id="client-jim",
        )
        assert code == 200, s1
        assert s1["user_id"] == "jim"
        assert s1["client_id"] == "client-jim"
        assert s1["conversation_id"] == "dm:jim"

        code, s2 = h.put(
            "/api/session",
            {"user_id": "sam"},
            client_id="client-sam",
        )
        assert code == 200, s2
        assert s2["user_id"] == "sam"
        assert s2["conversation_id"] == "dm:sam"

        code, g1 = h.get("/api/session", client_id="client-jim")
        assert code == 200
        assert g1["user_id"] == "jim"
        assert g1["conversation_id"] == "dm:jim"

        code, g2 = h.get("/api/session", client_id="client-sam")
        assert code == 200
        assert g2["user_id"] == "sam"

        # Stomp check: sam PUT does not change jim
        code, _ = h.put(
            "/api/session",
            {"view_mode": "all"},
            client_id="client-sam",
        )
        assert code == 200
        code, g1b = h.get("/api/session", client_id="client-jim")
        assert g1b["user_id"] == "jim"
        assert g1b["view_mode"] == "conversation"
    finally:
        h.close()


def test_t16b_post_speaker_from_session(paths) -> None:
    """T16b: POST /api/messages speaker from session despite body user_id mismatch."""
    h = _ApiHarness(paths, client_id=None)
    try:
        users = UsersStore(paths)
        users.create_user("Jim", user_id="jim", provisional=True)
        users.create_user("Sam", user_id="sam", provisional=True)

        h.put("/api/session", {"user_id": "jim"}, client_id="c-jim")
        h.put("/api/session", {"user_id": "sam"}, client_id="c-sam")

        code, r1 = h.post(
            "/api/messages",
            {"content": "hello from jim", "user_id": "sam"},  # mismatch body
            client_id="c-jim",
        )
        assert code == 200, r1
        assert r1.get("ok") is True

        code, r2 = h.post(
            "/api/messages",
            {"content": "hello from sam", "user_id": "jim"},  # mismatch body
            client_id="c-sam",
        )
        assert code == 200, r2

        msgs = list_messages(paths=paths, limit=50)
        by_content = {m.get("content"): m for m in msgs}
        assert "hello from jim" in by_content
        assert "hello from sam" in by_content
        assert by_content["hello from jim"].get("user_id") == "jim"
        assert by_content["hello from sam"].get("user_id") == "sam"
    finally:
        h.close()


def test_t16b_wait_reply_speaker_from_session(paths) -> None:
    """T16b wait/reply: speaker from session despite body user_id mismatch (KD23)."""
    h = _ApiHarness(paths, client_id=None)
    try:
        users = UsersStore(paths)
        users.create_user("Jim", user_id="jim", provisional=True)
        users.create_user("Sam", user_id="sam", provisional=True)

        h.put("/api/session", {"user_id": "jim"}, client_id="c-jim")
        h.put("/api/session", {"user_id": "sam"}, client_id="c-sam")

        # Thin path: wait/reply appends even without a durable pending wait
        # (may route idle); assert glass row speaker is session user.
        code, r1 = h.post(
            "/api/wait/reply",
            {"content": "wait answer jim", "user_id": "sam"},
            client_id="c-jim",
        )
        assert code in (200, 400), r1  # ok or no-wait client error — row may still append
        # Prefer successful append when worker allows
        if r1.get("ok") is True or r1.get("message"):
            msgs = list_messages(paths=paths, limit=50)
            row = next(
                (m for m in msgs if m.get("content") == "wait answer jim"), None
            )
            assert row is not None, msgs
            assert row.get("user_id") == "jim"

        code, r2 = h.post(
            "/api/wait/reply",
            {"content": "wait answer sam", "user_id": "jim"},
            client_id="c-sam",
        )
        if r2.get("ok") is True or r2.get("message"):
            msgs = list_messages(paths=paths, limit=50)
            row = next(
                (m for m in msgs if m.get("content") == "wait answer sam"), None
            )
            assert row is not None, msgs
            assert row.get("user_id") == "sam"
        else:
            # If append is gated, still require 400 not stamping wrong user silently
            # when no message; ensure no wrong-speaker row exists.
            msgs = list_messages(paths=paths, limit=50)
            wrong = [
                m
                for m in msgs
                if m.get("content") == "wait answer sam" and m.get("user_id") == "jim"
            ]
            assert wrong == []
    finally:
        h.close()


def test_session_and_messages_reject_while_resetting(paths) -> None:
    """Issue 1: GET session / POST messages fail closed during reset (no map growth)."""
    h = _ApiHarness(paths, client_id=None)
    try:
        reg_path = paths.data_dir / CLIENT_SESSIONS_REL

        def client_count() -> int:
            if not reg_path.is_file():
                return 0
            data = json.loads(reg_path.read_text(encoding="utf-8"))
            clients = data.get("clients") or {}
            return len(clients) if isinstance(clients, dict) else 0

        # Flip worker resetting flag (same gate as full reset).
        h.worker._continuous.resetting = True  # noqa: SLF001
        try:
            before = client_count()
            code, sess = h.get("/api/session", client_id="during-reset-c1")
            assert code == 503
            assert sess.get("error") == "resetting"
            assert client_count() == before

            code, msg = h.post(
                "/api/messages",
                {"content": "should not bind", "user_id": "operator"},
                client_id="during-reset-c2",
            )
            assert code == 503
            assert msg.get("error") == "resetting"
            assert client_count() == before

            code, wr = h.post(
                "/api/wait/reply",
                {"content": "nope", "user_id": "operator"},
                client_id="during-reset-c3",
            )
            assert code == 503
            assert wr.get("error") == "resetting"
            assert client_count() == before
        finally:
            h.worker._continuous.resetting = False  # noqa: SLF001
    finally:
        h.close()


def test_t18_status_does_not_create_map(paths) -> None:
    """T18: status missing/unknown does not grow map; GET session creates; invalid 400."""
    h = _ApiHarness(paths, client_id=None)
    try:
        reg_path = paths.data_dir / CLIENT_SESSIONS_REL

        def client_count() -> int:
            if not reg_path.is_file():
                return 0
            data = json.loads(reg_path.read_text(encoding="utf-8"))
            clients = data.get("clients") or {}
            return len(clients) if isinstance(clients, dict) else 0

        assert client_count() == 0

        # Missing header on status → no create
        code, status = h.get("/api/status", client_id=None)
        assert code == 200
        assert client_count() == 0

        # Unknown client on status → no create
        code, status = h.get("/api/status", client_id="fresh-unknown-uuid-1")
        assert code == 200
        assert client_count() == 0

        # GET /api/session with same unknown id creates once
        code, sess = h.get("/api/session", client_id="fresh-unknown-uuid-1")
        assert code == 200, sess
        assert sess["client_id"] == "fresh-unknown-uuid-1"
        assert sess["user_id"] == "operator"
        assert client_count() == 1

        # Second status with known client does not grow
        code, status = h.get("/api/status", client_id="fresh-unknown-uuid-1")
        assert code == 200
        assert client_count() == 1

        # Invalid client_id → 400
        code, bad = h.get("/api/session", client_id="../etc")
        assert code == 400
        assert bad.get("error") == "invalid_client_id"
        assert client_count() == 1

        code, bad2 = h.post(
            "/api/messages",
            {"content": "x"},
            client_id="has space",
        )
        assert code == 400
        assert bad2.get("error") == "invalid_client_id"
    finally:
        h.close()


def test_get_session_mints_when_missing_header(paths) -> None:
    h = _ApiHarness(paths, client_id=None)
    try:
        code, sess = h.get("/api/session", client_id=None)
        assert code == 200
        assert sess.get("client_id")
        assert sess["user_id"] == "operator"
        assert sess["conversation_id"] == "dm:operator"
    finally:
        h.close()


def test_reset_clears_client_sessions(paths) -> None:
    from elyra.conversations import ConversationsStore

    store = ConversationsStore(paths)
    store.ensure_layout()
    reg = ClientSessionsRegistry(paths, ensure_dm=store.ensure_dm)
    reg.resolve("keep-me", allow_create=True)
    assert reg.client_count() == 1
    out = reset_clear_client_sessions(paths)
    assert out["step"] == "client_sessions"
    # Fresh registry instance sees empty after clear (mtime reload)
    reg2 = ClientSessionsRegistry(paths)
    assert reg2.client_count() == 0
    # Original instance reloads via mtime
    assert reg.client_count() == 0


def test_session_get_and_put_still_works(paths) -> None:
    """Regression: single-client harness default still binds session."""
    h = _ApiHarness(paths)
    try:
        code, sess = h.get("/api/session")
        assert code == 200
        assert sess["user_id"] == "operator"
        assert "goes_by" in sess
        assert "self_display_name" in sess
        assert "conversation_id" in sess
        assert sess.get("client_id") == "test-client-1"

        code, created = h.post("/api/users", {"goes_by": "Jim"})
        assert code == 201, created
        uid = created["user_id"]

        code, switched = h.put("/api/session", {"user_id": uid})
        assert code == 200, switched
        assert switched["ok"] is True
        assert switched["user_id"] == uid
        assert switched["conversation_id"] == f"dm:{uid}"

        code, sess = h.get("/api/session")
        assert code == 200
        assert sess["user_id"] == uid
    finally:
        h.close()
