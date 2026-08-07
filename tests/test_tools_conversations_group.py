"""Hermetic tests for create_group / update_group tools (T-G1–T-G10).

Design: conversation list fix + topology tools v1.1 §tools KD-T1–T8.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.conversations import ConversationsStore
from elyra.tools import ToolContext, ToolRegistry, resolve_bundled_tools_root
from elyra.tools.builtin.social import create_group, update_group


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
def store(paths) -> ConversationsStore:
    s = ConversationsStore(paths)
    s.ensure_layout()
    return s


@pytest.fixture
def registry(paths) -> ToolRegistry:
    return ToolRegistry(paths, bundled_root=resolve_bundled_tools_root())


def _ctx(
    paths,
    *,
    user_id: str | None = "operator",
    conversation_id: str | None = None,
    extras: dict[str, Any] | None = None,
    moment_id: str = "moment-g1",
) -> ToolContext:
    return ToolContext(
        paths=paths,
        user_id=user_id,
        conversation_id=conversation_id,
        moment_id=moment_id,
        extras=extras or {},
    )


def _assert_err(result, reason: str) -> None:
    """Pin error_reason on result and payload echo (KD-T7 / T-G3–T-G7)."""
    assert result.ok is False
    assert result.error_reason == reason
    assert result.payload.get("error_reason") == reason


# ---------------------------------------------------------------------------
# T-G1 — create_group happy path
# ---------------------------------------------------------------------------


def test_tg1_create_group_happy_path(paths, store: ConversationsStore) -> None:
    result = create_group(
        {"name": "Jim+Sam work", "members": ["jim", "sam"]},
        _ctx(paths, user_id="anette"),
    )
    assert result.ok is True
    assert result.error_reason is None
    assert result.counts_as_speak is False
    assert result.ends_moment is False

    conv = result.payload["conversation"]
    assert conv["id"].startswith("group:")
    assert conv["type"] == "group"
    assert conv["name"] == "Jim+Sam work"
    assert conv["members"] == ["jim", "sam"]
    assert conv["description"] is None
    assert conv["last_message_at"] is None
    assert result.payload["actor_user_id"] == "anette"

    loaded = store.get(conv["id"])
    assert loaded is not None
    assert loaded["members"] == ["jim", "sam"]
    assert loaded["name"] == "Jim+Sam work"


# ---------------------------------------------------------------------------
# T-G2 — never auto-add ctx.user_id / operator
# ---------------------------------------------------------------------------


def test_tg2_create_group_does_not_add_operator(
    paths, store: ConversationsStore
) -> None:
    result = create_group(
        {"name": "No operator", "members": ["jim", "sam"]},
        _ctx(paths, user_id="operator"),
    )
    assert result.ok is True
    members = result.payload["conversation"]["members"]
    assert members == ["jim", "sam"]
    assert "operator" not in members
    assert result.payload["actor_user_id"] == "operator"

    loaded = store.get(result.payload["conversation"]["id"])
    assert loaded is not None
    assert "operator" not in loaded["members"]


# ---------------------------------------------------------------------------
# T-G3 — invalid user_id
# ---------------------------------------------------------------------------


def test_tg3_invalid_user_id(paths, store: ConversationsStore) -> None:
    result = create_group(
        {"name": "Bad", "members": ["jim", "../escape"]},
        _ctx(paths),
    )
    _assert_err(result, "invalid_user_id")
    assert "detail" in result.payload
    # No group records created
    listed = store.list()
    groups = [c for c in listed if c.get("type") == "group"]
    assert groups == []


# ---------------------------------------------------------------------------
# T-G4 — empty members / missing name / invalid description
# ---------------------------------------------------------------------------


def test_tg4_fail_closed_members_name_description(
    paths, store: ConversationsStore
) -> None:
    r_empty = create_group({"name": "X", "members": []}, _ctx(paths))
    _assert_err(r_empty, "missing_members")

    r_no_members = create_group({"name": "X"}, _ctx(paths))
    _assert_err(r_no_members, "missing_members")

    r_bad_members = create_group(
        {"name": "X", "members": "jim"},  # type: ignore[dict-item]
        _ctx(paths),
    )
    _assert_err(r_bad_members, "invalid_members")

    r_missing_name = create_group({"members": ["jim"]}, _ctx(paths))
    _assert_err(r_missing_name, "missing_name")

    r_blank_name = create_group(
        {"name": "   ", "members": ["jim"]}, _ctx(paths)
    )
    _assert_err(r_blank_name, "missing_name")

    r_bad_name_type = create_group(
        {"name": 123, "members": ["jim"]},  # type: ignore[dict-item]
        _ctx(paths),
    )
    _assert_err(r_bad_name_type, "invalid_name")

    r_bad_desc = create_group(
        {"name": "X", "members": ["jim"], "description": 99},
        _ctx(paths),
    )
    _assert_err(r_bad_desc, "invalid_description")

    assert store.list() == [] or all(
        c.get("type") != "group" for c in store.list()
    )


# ---------------------------------------------------------------------------
# T-G5 — update_group rename + description (incl. null clear)
# ---------------------------------------------------------------------------


def test_tg5_update_group_rename_and_description(
    paths, store: ConversationsStore
) -> None:
    created = create_group(
        {
            "name": "Original",
            "members": ["jim", "sam"],
            "description": "first desc",
            "conversation_id": "group:tg5-room",
        },
        _ctx(paths),
    )
    assert created.ok is True
    cid = created.payload["conversation"]["id"]
    assert cid == "group:tg5-room"

    renamed = update_group(
        {"conversation_id": cid, "name": "Renamed", "description": "second"},
        _ctx(paths, user_id="operator"),
    )
    assert renamed.ok is True
    assert renamed.payload["conversation"]["name"] == "Renamed"
    assert renamed.payload["conversation"]["description"] == "second"
    assert store.get(cid)["name"] == "Renamed"  # type: ignore[index]

    cleared = update_group(
        {"conversation_id": cid, "description": None},
        _ctx(paths),
    )
    assert cleared.ok is True
    assert cleared.payload["conversation"]["description"] is None
    assert store.get(cid)["description"] is None  # type: ignore[index]


# ---------------------------------------------------------------------------
# T-G6 — members full replace + strip whitespace
# ---------------------------------------------------------------------------


def test_tg6_update_group_members_full_replace_and_strip(
    paths, store: ConversationsStore
) -> None:
    created = create_group(
        {
            "name": "Swap",
            "members": ["jim", "sam"],
            "conversation_id": "group:tg6-room",
        },
        _ctx(paths),
    )
    assert created.ok is True
    cid = "group:tg6-room"

    # Full replace: drop sam, add anette; strip " jim " → jim
    updated = update_group(
        {
            "conversation_id": cid,
            "members": [" jim ", "anette", "jim"],  # dedupe after strip
        },
        _ctx(paths, user_id="operator"),
    )
    assert updated.ok is True
    members = updated.payload["conversation"]["members"]
    assert members == ["jim", "anette"]
    assert "sam" not in members
    assert "operator" not in members
    assert store.get(cid)["members"] == ["jim", "anette"]  # type: ignore[index]


def test_tg6_create_group_strips_members(paths, store: ConversationsStore) -> None:
    result = create_group(
        {"name": "Strip", "members": [" jim ", "  sam", "jim"]},
        _ctx(paths),
    )
    assert result.ok is True
    assert result.payload["conversation"]["members"] == ["jim", "sam"]


# ---------------------------------------------------------------------------
# T-G7 — update_group stable failure reasons
# ---------------------------------------------------------------------------


def test_tg7_update_group_stable_errors(paths, store: ConversationsStore) -> None:
    store.create_group(
        name="Exists",
        members=["jim"],
        conversation_id="group:tg7-room",
    )
    store.ensure_dm("jim")

    r_missing = update_group({"name": "x"}, _ctx(paths))
    _assert_err(r_missing, "missing_conversation_id")

    r_blank = update_group(
        {"conversation_id": "   ", "name": "x"}, _ctx(paths)
    )
    _assert_err(r_blank, "missing_conversation_id")

    r_not_found = update_group(
        {"conversation_id": "group:does-not-exist-xyz", "name": "x"},
        _ctx(paths),
    )
    _assert_err(r_not_found, "conversation_not_found")

    r_dm = update_group(
        {"conversation_id": "dm:jim", "name": "polish"},
        _ctx(paths),
    )
    _assert_err(r_dm, "not_a_group")

    # Never default from ctx.conversation_id when arg omitted
    r_ctx_only = update_group(
        {"name": "from-ctx"},
        _ctx(paths, conversation_id="group:tg7-room"),
    )
    _assert_err(r_ctx_only, "missing_conversation_id")
    assert store.get("group:tg7-room")["name"] == "Exists"  # type: ignore[index]

    r_no_fields = update_group(
        {"conversation_id": "group:tg7-room"},
        _ctx(paths),
    )
    _assert_err(r_no_fields, "no_fields_to_update")

    r_null_name = update_group(
        {"conversation_id": "group:tg7-room", "name": None},
        _ctx(paths),
    )
    _assert_err(r_null_name, "invalid_name")

    r_bad_desc = update_group(
        {"conversation_id": "group:tg7-room", "description": ["x"]},
        _ctx(paths),
    )
    _assert_err(r_bad_desc, "invalid_description")


# ---------------------------------------------------------------------------
# T-G8 — registry discovers both tools; kind mutate
# ---------------------------------------------------------------------------


def test_tg8_registry_discovers_group_tools(registry: ToolRegistry) -> None:
    for name in ("create_group", "update_group"):
        assert registry.has(name), name
        pkg = registry.get(name)
        assert pkg is not None and pkg.handler is not None
        assert pkg.meta.kind == "mutate"
        assert pkg.meta.name == name
    names = registry.names()
    assert "create_group" in names
    assert "update_group" in names

    tools = {t["function"]["name"]: t for t in registry.openai_tools()}
    assert "create_group" in tools
    assert "update_group" in tools
    create_props = tools["create_group"]["function"]["parameters"]["properties"]
    assert "name" in create_props
    assert "members" in create_props
    update_props = tools["update_group"]["function"]["parameters"]["properties"]
    assert "conversation_id" in update_props
    assert "members" in update_props


# ---------------------------------------------------------------------------
# T-G9 — counts_as_speak is False (direct + via registry policy)
# ---------------------------------------------------------------------------


def test_tg9_counts_as_speak_false(
    paths, registry: ToolRegistry, store: ConversationsStore
) -> None:
    direct = create_group(
        {"name": "Speak check", "members": ["jim"]},
        _ctx(paths),
    )
    assert direct.ok is True
    assert direct.counts_as_speak is False

    via = registry.execute(
        "create_group",
        {"name": "Via reg", "members": ["sam"]},
        _ctx(paths),
    )
    assert via.ok is True
    assert via.counts_as_speak is False

    cid = via.payload["conversation"]["id"]
    upd = registry.execute(
        "update_group",
        {"conversation_id": cid, "name": "Renamed via reg"},
        _ctx(paths),
    )
    assert upd.ok is True
    assert upd.counts_as_speak is False


# ---------------------------------------------------------------------------
# T-G10 — conversation_id collision → conversation_exists
# ---------------------------------------------------------------------------


def test_tg10_create_group_collision(paths, store: ConversationsStore) -> None:
    first = create_group(
        {
            "name": "First",
            "members": ["jim"],
            "conversation_id": "group:collide-room",
        },
        _ctx(paths),
    )
    assert first.ok is True

    second = create_group(
        {
            "name": "Second",
            "members": ["sam"],
            "conversation_id": "group:collide-room",
        },
        _ctx(paths),
    )
    _assert_err(second, "conversation_exists")
    # Original preserved
    rec = store.get("group:collide-room")
    assert rec is not None
    assert rec["name"] == "First"
    assert rec["members"] == ["jim"]


# ---------------------------------------------------------------------------
# Extra pins: description omit on create; invalid conversation_id shape
# ---------------------------------------------------------------------------


def test_create_group_description_empty_strips_to_null(
    paths, store: ConversationsStore
) -> None:
    result = create_group(
        {"name": "Empty desc", "members": ["jim"], "description": "  "},
        _ctx(paths),
    )
    assert result.ok is True
    assert result.payload["conversation"]["description"] is None


def test_create_group_rejects_dm_conversation_id(paths) -> None:
    result = create_group(
        {
            "name": "No DM",
            "members": ["jim"],
            "conversation_id": "dm:jim",
        },
        _ctx(paths),
    )
    _assert_err(result, "invalid_conversation_id")


def test_create_group_null_user_id_actor(paths) -> None:
    """Pure work / continuous: null actor is fine; still not injected."""
    result = create_group(
        {"name": "Work", "members": ["jim", "sam"]},
        _ctx(paths, user_id=None),
    )
    assert result.ok is True
    assert result.payload["actor_user_id"] is None
    assert result.payload["conversation"]["members"] == ["jim", "sam"]
