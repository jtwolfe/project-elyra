"""Promote gates, grants, identity tools, resolve_orient_user (PR2)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.identity.gates import (
    PromoteContext,
    evaluate_promote_gate,
    should_name_nudge,
)
from elyra.identity.grants import (
    consume_grant,
    load_active_token_set,
    load_grants,
    mint_grant,
)
from elyra.identity.layout import content_sha256
from elyra.identity.orient_user import resolve_orient_user
from elyra.identity.store import IdentityStore
from elyra.tools.builtin.identity import (
    draft_identity,
    get_identity,
    promote_identity,
)
from elyra.tools.types import ToolContext
from elyra.users import UsersStore


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


@pytest.fixture
def identity(paths) -> IdentityStore:
    store = IdentityStore(paths)
    store.ensure_layout()
    return store


@pytest.fixture
def users(paths) -> UsersStore:
    store = UsersStore(paths)
    store.ensure_layout()
    return store


def _ctx(
    paths,
    *,
    identity: IdentityStore | None = None,
    users: UsersStore | None = None,
    user_id: str | None = None,
    moment_id: str = "m1",
    wake_kind: str | None = "user_message",
) -> ToolContext:
    extras: dict[str, Any] = {}
    if identity is not None:
        extras["identity"] = identity
    if users is not None:
        extras["users"] = users
    if wake_kind is not None:
        extras["wake_kind"] = wake_kind
    return ToolContext(
        paths=paths,
        moment_id=moment_id,
        user_id=user_id,
        extras=extras,
    )


@dataclass
class FakeWake:
    kind: str
    payload: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# evaluate_promote_gate — pure
# ---------------------------------------------------------------------------


def test_gate_missing_reason():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="self",
            target_user_id=None,
            session_user_id=None,
            wake_kind=None,
            moment_id="m",
            reason="  ",
            grant_token="grant_x",
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            operator_grant_tokens=frozenset({"grant_x"}),
        )
    )
    assert r.allowed is False
    assert r.error_reason == "missing_reason"


def test_gate_draft_missing():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="self",
            target_user_id=None,
            session_user_id=None,
            wake_kind=None,
            moment_id="m",
            reason="adopt narrative self",
            grant_token="grant_x",
            has_draft=False,
            draft_sha256=None,
            expected_draft_sha256=None,
            operator_grant_tokens=frozenset({"grant_x"}),
        )
    )
    assert r.allowed is False
    assert r.error_reason == "draft_missing"


def test_gate_draft_hash_mismatch():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="self",
            target_user_id=None,
            session_user_id=None,
            wake_kind=None,
            moment_id="m",
            reason="adopt narrative self",
            grant_token="grant_x",
            has_draft=True,
            draft_sha256="aaa",
            expected_draft_sha256="bbb",
            operator_grant_tokens=frozenset({"grant_x"}),
        )
    )
    assert r.allowed is False
    assert r.error_reason == "draft_hash_mismatch"


def test_gate_self_requires_grant():
    base = dict(
        actor="self",
        target_user_id=None,
        session_user_id=None,
        wake_kind=None,
        moment_id="m",
        reason="adopt narrative self",
        has_draft=True,
        draft_sha256="abc",
        expected_draft_sha256=None,
        operator_grant_tokens=frozenset({"grant_good"}),
    )
    r = evaluate_promote_gate(PromoteContext(**base, grant_token=None))  # type: ignore[arg-type]
    assert r.error_reason == "self_grant_required"

    r2 = evaluate_promote_gate(PromoteContext(**base, grant_token="grant_bad"))  # type: ignore[arg-type]
    assert r2.error_reason == "self_grant_required"


def test_gate_self_reason_too_short():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="self",
            target_user_id=None,
            session_user_id=None,
            wake_kind=None,
            moment_id="m",
            reason="short",
            grant_token="grant_x",
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            operator_grant_tokens=frozenset({"grant_x"}),
        )
    )
    assert r.error_reason == "reason_too_short"


def test_gate_self_allow_without_grant_tests_only():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="self",
            target_user_id=None,
            session_user_id=None,
            wake_kind=None,
            moment_id="m",
            reason="adopt narrative self",
            grant_token=None,
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            allow_self_promote_without_grant=True,
        )
    )
    assert r.allowed is True


def test_gate_self_allow_with_token():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="self",
            target_user_id=None,
            session_user_id=None,
            wake_kind=None,
            moment_id="m",
            reason="adopt narrative self",
            grant_token="grant_x",
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            operator_grant_tokens=frozenset({"grant_x"}),
        )
    )
    assert r.allowed is True
    assert r.error_reason is None


def test_gate_user_missing_and_invalid():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="user",
            target_user_id=None,
            session_user_id="jim",
            wake_kind="user_message",
            moment_id="m",
            reason="user asked",
            grant_token=None,
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            target_user_exists=True,
        )
    )
    assert r.error_reason == "missing_user_id"

    r2 = evaluate_promote_gate(
        PromoteContext(
            actor="user",
            target_user_id="../etc",
            session_user_id="jim",
            wake_kind="user_message",
            moment_id="m",
            reason="user asked",
            grant_token=None,
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            target_user_exists=True,
        )
    )
    assert r2.error_reason == "invalid_user_id"


def test_gate_user_not_found():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="user",
            target_user_id="ghost",
            session_user_id="ghost",
            wake_kind="user_message",
            moment_id="m",
            reason="user asked",
            grant_token=None,
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            target_user_exists=False,
        )
    )
    assert r.error_reason == "user_not_found"


def test_gate_user_context_required_on_pure_work():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="user",
            target_user_id="jim",
            session_user_id="jim",
            wake_kind="timer",
            moment_id="m",
            reason="user asked",
            grant_token=None,
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            target_user_exists=True,
        )
    )
    assert r.error_reason == "user_promote_context_required"


def test_gate_user_wrong_session_user():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="user",
            target_user_id="sam",
            session_user_id="jim",
            wake_kind="user_message",
            moment_id="m",
            reason="user asked",
            grant_token=None,
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            target_user_exists=True,
        )
    )
    assert r.error_reason == "user_promote_wrong_user"


def test_gate_user_glass_admin_any_user():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="user",
            target_user_id="sam",
            session_user_id="jim",
            wake_kind="timer",
            moment_id="m",
            reason="admin",
            grant_token=None,
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            identity_promote_user_ok=True,
            identity_promote_any_user=True,
            target_user_exists=True,
        )
    )
    assert r.allowed is True


def test_gate_user_social_ok():
    r = evaluate_promote_gate(
        PromoteContext(
            actor="user",
            target_user_id="jim",
            session_user_id="jim",
            wake_kind="user_message",
            moment_id="m",
            reason="ok",
            grant_token=None,
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            target_user_exists=True,
        )
    )
    # reason length >= 4 required; "ok" is 2
    assert r.error_reason == "user_promote_context_required"

    r2 = evaluate_promote_gate(
        PromoteContext(
            actor="user",
            target_user_id="jim",
            session_user_id="jim",
            wake_kind="wait_reply",
            moment_id="m",
            reason="user asked",
            grant_token=None,
            has_draft=True,
            draft_sha256="abc",
            expected_draft_sha256=None,
            target_user_exists=True,
        )
    )
    assert r2.allowed is True


# ---------------------------------------------------------------------------
# should_name_nudge
# ---------------------------------------------------------------------------


def test_should_name_nudge_basic():
    meta = {"real_name_known": False, "name_nudge": {"count": 0}}
    assert should_name_nudge(meta, "m1") is True
    meta2 = {
        "real_name_known": False,
        "name_nudge": {"last_moment_id": "m1", "count": 1},
    }
    assert should_name_nudge(meta2, "m1") is False
    assert should_name_nudge(meta2, "m2") is True
    meta3 = {"real_name_known": True}
    assert should_name_nudge(meta3, "m1") is False
    meta4 = {"name_nudge": {"count": 3}}
    assert should_name_nudge(meta4, "m9") is False


# ---------------------------------------------------------------------------
# grants mint / consume
# ---------------------------------------------------------------------------


def test_mint_and_consume_grant_once(paths):
    minted = mint_grant(paths, note="test adopt")
    assert minted["ok"] is True
    token = minted["token"]
    assert token.startswith("grant_")
    assert len(token) == len("grant_") + 32

    active = load_active_token_set(paths)
    assert token in active

    c1 = consume_grant(paths, token)
    assert c1["ok"] is True
    assert c1["uses_remaining"] == 0

    c2 = consume_grant(paths, token)
    assert c2["ok"] is False
    assert c2["error"] == "grant_exhausted"

    assert token not in load_active_token_set(paths)


def test_consume_missing_and_expired(paths):
    assert consume_grant(paths, "grant_" + "0" * 32)["error"] == "grant_missing"

    # Manually write expired token
    path = paths.data_dir / "runtime" / "identity_grants.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "tokens": [
                    {
                        "token": "grant_" + "a" * 32,
                        "created_at": "2020-01-01T00:00:00+00:00",
                        "expires_at": "2020-01-02T00:00:00+00:00",
                        "uses_remaining": 1,
                        "note": "old",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    r = consume_grant(paths, "grant_" + "a" * 32)
    assert r["error"] == "grant_expired"
    assert "grant_" + "a" * 32 not in load_active_token_set(paths)


def test_env_grant_in_active_set(paths, monkeypatch):
    monkeypatch.setenv("ELYRA_SELF_PROMOTE_GRANT", "grant_envtoken_test_1234567890ab")
    active = load_active_token_set(paths)
    assert "grant_envtoken_test_1234567890ab" in active
    c = consume_grant(paths, "grant_envtoken_test_1234567890ab")
    assert c["ok"] is True
    assert c.get("source") == "env"


# ---------------------------------------------------------------------------
# resolve_orient_user
# ---------------------------------------------------------------------------


def test_resolve_orient_social_speaker(paths, users):
    users.create_user("Jim", user_id="jim", provisional=False, real_name_known=True)
    wake = FakeWake("user_message", {"user_id": "jim", "content": "hi"})
    uid, digest = resolve_orient_user(wake, users=users, goals=None)
    assert uid == "jim"
    assert digest
    assert "Jim" in digest or "jim" in digest.lower()


def test_resolve_orient_social_without_user_id_empty(paths, users):
    wake = FakeWake("user_message", {"content": "hi"})
    uid, digest = resolve_orient_user(wake, users=users, goals=None)
    assert uid is None
    assert digest == ""


def test_resolve_orient_autonomous_empty_no_operator_fallback(paths, users):
    """Pure work must not invent operator as USER (K13/K19 contract change)."""
    wake = FakeWake("timer", {"reason": "poll"})
    uid, digest = resolve_orient_user(wake, users=users, goals=None)
    assert uid is None
    assert digest == ""

    wake2 = FakeWake("moment_continue", {"source_moment_id": "m0"})
    uid2, digest2 = resolve_orient_user(wake2, users=users, goals=None)
    assert uid2 is None
    assert digest2 == ""

    # Even when operator profile exists on disk, pure work stays empty.
    op = users.profile("operator")
    assert op  # seeded
    wake3 = FakeWake("task_ready", {"task_id": "t_x"})
    uid3, digest3 = resolve_orient_user(wake3, users=users, goals=None)
    assert uid3 is None
    assert digest3 == ""
    assert digest3 != op


def test_resolve_orient_with_goals_stub_empty_context(paths, users):
    class EmptyGoals:
        def find_task(self, _tid: str):
            return None

        def get_goal(self, _gid: str):
            return None

    wake = FakeWake("task_ready", {"task_id": "t1", "goal_id": "g1"})
    uid, digest = resolve_orient_user(wake, users=users, goals=EmptyGoals())
    assert uid is None
    assert digest == ""


def test_resolve_orient_created_in_context_when_present(paths, users):
    users.create_user("Jim", user_id="jim")

    class GoalsWithCtx:
        def find_task(self, tid: str):
            if tid == "t1":
                return (
                    {"id": "g1", "created_in_context": None},
                    {
                        "id": "t1",
                        "created_in_context": {"user_id": "jim", "goes_by": "Jim"},
                    },
                )
            return None

        def get_goal(self, _gid: str):
            return None

    wake = FakeWake("task_ready", {"task_id": "t1"})
    uid, digest = resolve_orient_user(wake, users=users, goals=GoalsWithCtx())
    assert uid == "jim"
    assert digest


# ---------------------------------------------------------------------------
# tools: get / draft / promote
# ---------------------------------------------------------------------------


def test_get_identity_self_current(paths, identity):
    ctx = _ctx(paths, identity=identity)
    r = get_identity({"actor": "self"}, ctx)
    assert r.ok
    assert r.payload["which"] == "current"
    assert r.payload["body"]
    assert r.payload.get("should_name_nudge") is False


def test_draft_and_promote_self_with_grant(paths, identity):
    ctx = _ctx(paths, identity=identity)
    body = "# Self draft\n\nI am carefully revised.\n"
    d = draft_identity(
        {"actor": "self", "body": body, "reason": "compose short charter"},
        ctx,
    )
    assert d.ok
    assert identity.has_draft()

    # Deny without grant
    denied = promote_identity(
        {"actor": "self", "reason": "adopt short charter now"},
        ctx,
    )
    assert not denied.ok
    assert denied.error_reason == "self_grant_required"
    assert identity.has_draft()  # still draft

    minted = mint_grant(paths, note="adopt")
    token = minted["token"]
    ok = promote_identity(
        {
            "actor": "self",
            "reason": "adopt short charter now",
            "grant_token": token,
        },
        ctx,
    )
    assert ok.ok, ok.error_reason
    assert not identity.has_draft()
    assert "carefully revised" in identity.self_digest()

    # Second promote with same token fails (exhausted)
    identity.write_draft(
        "# again\n\nsecond draft body\n", reason="another draft for test"
    )
    again = promote_identity(
        {
            "actor": "self",
            "reason": "adopt short charter now",
            "grant_token": token,
        },
        ctx,
    )
    assert not again.ok
    assert again.error_reason in ("self_grant_required", "grant_exhausted")


def test_promote_self_does_not_consume_on_gate_deny(paths, identity):
    minted = mint_grant(paths)
    token = minted["token"]
    # No draft → gate deny before consume
    ctx = _ctx(paths, identity=identity)
    r = promote_identity(
        {"actor": "self", "reason": "adopt short charter now", "grant_token": token},
        ctx,
    )
    assert not r.ok
    assert r.error_reason == "draft_missing"
    # Token still active
    assert token in load_active_token_set(paths)
    assert load_grants(paths)["tokens"][0]["uses_remaining"] == 1


def test_user_draft_and_promote_social(paths, users):
    created = users.create_user("Jim", user_id="jim")
    assert created["ok"]
    ctx = _ctx(paths, users=users, user_id="jim", wake_kind="user_message")
    body = "# Jim\n\nCall me Papa Joe.\n"
    d = draft_identity(
        {
            "actor": "user",
            "user_id": "jim",
            "body": body,
            "meta_patch": {"goes_by": "Papa Joe"},
            "reason": "user requested address change",
        },
        ctx,
    )
    assert d.ok

    # Wrong session user
    ctx_wrong = _ctx(paths, users=users, user_id="operator", wake_kind="user_message")
    bad = promote_identity(
        {
            "actor": "user",
            "user_id": "jim",
            "reason": "user requested address change",
        },
        ctx_wrong,
    )
    assert not bad.ok
    assert bad.error_reason == "user_promote_wrong_user"

    # Pure work — context required
    ctx_work = _ctx(paths, users=users, user_id="jim", wake_kind="timer")
    bad2 = promote_identity(
        {
            "actor": "user",
            "user_id": "jim",
            "reason": "user requested address change",
        },
        ctx_work,
    )
    assert not bad2.ok
    assert bad2.error_reason == "user_promote_context_required"

    good = promote_identity(
        {
            "actor": "user",
            "user_id": "jim",
            "reason": "user requested address change",
        },
        ctx,
    )
    assert good.ok, good.error_reason
    assert "Papa Joe" in users.profile("jim")
    assert users.get_meta("jim").get("goes_by") == "Papa Joe"


def test_get_identity_user_name_nudge(paths, users):
    users.create_user("Guest", user_id="guest", provisional=True, real_name_known=False)
    ctx = _ctx(paths, users=users, user_id="guest", moment_id="m42")
    r = get_identity({"actor": "user", "user_id": "guest"}, ctx)
    assert r.ok
    assert r.payload["should_name_nudge"] is True

    d = draft_identity(
        {
            "actor": "user",
            "user_id": "guest",
            "reason": "record name ask",
            "meta_patch": {"record_name_nudge": True},
        },
        ctx,
    )
    assert d.ok, d.error_reason

    r2 = get_identity({"actor": "user", "user_id": "guest"}, ctx)
    assert r2.ok
    assert r2.payload["should_name_nudge"] is False


def test_tools_require_configured_ports(paths):
    ctx = ToolContext(paths=paths, extras={})
    assert get_identity({"actor": "self"}, ctx).error_reason == "identity_not_configured"
    assert (
        get_identity({"actor": "user", "user_id": "x"}, ctx).error_reason
        == "users_not_configured"
    )


def test_draft_hash_optimistic_lock(paths, identity):
    ctx = _ctx(paths, identity=identity)
    body = "# Self draft\n\nlock me\n"
    draft_identity({"actor": "self", "body": body, "reason": "hash test draft"}, ctx)
    sha = content_sha256(body)
    minted = mint_grant(paths)
    bad = promote_identity(
        {
            "actor": "self",
            "reason": "adopt with wrong hash xx",
            "grant_token": minted["token"],
            "expected_draft_sha256": "0" * 64,
        },
        ctx,
    )
    assert not bad.ok
    assert bad.error_reason == "draft_hash_mismatch"
    # Grant not consumed on gate deny
    assert minted["token"] in load_active_token_set(paths)

    good = promote_identity(
        {
            "actor": "self",
            "reason": "adopt with correct hash",
            "grant_token": minted["token"],
            "expected_draft_sha256": sha,
        },
        ctx,
    )
    assert good.ok, good.error_reason


def test_bundled_identity_tools_discovered(paths):
    from elyra.tools import ToolRegistry

    reg = ToolRegistry(paths)
    names = {p.meta.name for p in reg.list_packages()} if hasattr(reg, "list_packages") else set()
    # Prefer public list API if present
    if not names:
        # ToolRegistry stores packages in _by_key
        names = set(reg._by_key.keys())  # noqa: SLF001
    assert "get_identity" in names
    assert "draft_identity" in names
    assert "promote_identity" in names
