"""ConversationsStore + reset clears conversations (PR2 / C12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.conversations import (
    ConversationsStore,
    conversation_id_to_filename,
    dm_id_for_user,
    validate_conversation_id,
)
from elyra.messages import append_message, list_messages
from elyra.runtime.reset import clear_conversations, clear_messages


@pytest.fixture
def paths(tmp_path: Path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths) -> ConversationsStore:
    s = ConversationsStore(paths)
    s.ensure_layout()
    return s


# ── id helpers / path jail ───────────────────────────────────────────────────


def test_validate_conversation_id_dm_and_group():
    assert validate_conversation_id("dm:jim") == "dm:jim"
    assert validate_conversation_id("group:abc123") == "group:abc123"
    assert validate_conversation_id("  dm:operator  ") == "dm:operator"
    hex_id = "a" * 32
    assert validate_conversation_id(f"group:{hex_id}") == f"group:{hex_id}"


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "jim",
        "dm:",
        "dm:../x",
        "dm:a/b",
        "dm:/etc",
        "group:",
        "group:../escape",
        "group:a/b",
        "group:/etc",
        "room:xyz",
        None,
        123,
    ],
)
def test_validate_conversation_id_rejects(bad):
    with pytest.raises((ValueError, TypeError, AttributeError)):
        if bad is None or isinstance(bad, int):
            # validate expects str — TypeError/ValueError both fail closed.
            validate_conversation_id(bad)  # type: ignore[arg-type]
        else:
            validate_conversation_id(bad)


def test_conversation_id_to_filename_and_path_jail(store):
    assert conversation_id_to_filename("dm:jim") == "dm_jim.json"
    path = store._record_path("dm:jim")
    assert path.name == "dm_jim.json"
    assert path.resolve().is_relative_to(store.by_id_dir.resolve())
    # Traversal-shaped ids rejected before path construction.
    with pytest.raises(ValueError):
        store._record_path("dm:../etc")
    with pytest.raises(ValueError):
        store._record_path("group:../../secret")


# ── ensure_dm / create_group / get / list ────────────────────────────────────


def test_ensure_dm_idempotent(store, paths):
    a = store.ensure_dm("jim")
    assert a["id"] == "dm:jim"
    assert a["type"] == "dm"
    assert a["members"] == ["jim"]
    assert a["name"] is None
    assert a["last_message_at"] is None
    assert (paths.data_dir / "conversations" / "by_id" / "dm_jim.json").is_file()

    b = store.ensure_dm("jim")
    assert b["id"] == a["id"]
    assert b["created_at"] == a["created_at"]
    # Still a single index entry.
    listed = store.list(type="dm")
    assert len(listed) == 1
    assert listed[0]["id"] == "dm:jim"


def test_ensure_dm_rejects_bad_user_id(store):
    with pytest.raises(ValueError, match="invalid user_id"):
        store.ensure_dm("../x")
    with pytest.raises(ValueError, match="invalid user_id"):
        store.ensure_dm("")


def test_create_group_and_get(store, paths):
    g = store.create_group(
        name="Dogfood",
        members=["jim", "operator", "sam"],
        description="C12 room",
    )
    assert g["id"].startswith("group:")
    assert g["type"] == "group"
    assert g["name"] == "Dogfood"
    assert g["description"] == "C12 room"
    assert g["members"] == ["jim", "operator", "sam"]

    loaded = store.get(g["id"])
    assert loaded is not None
    assert loaded["name"] == "Dogfood"
    # On-disk file uses : → _
    fname = conversation_id_to_filename(g["id"])
    assert (paths.data_dir / "conversations" / "by_id" / fname).is_file()


def test_create_group_with_explicit_id_and_dedupe_members(store):
    g = store.create_group(
        name="Room",
        members=["jim", "jim", "sam"],
        conversation_id="group:room1",
    )
    assert g["id"] == "group:room1"
    assert g["members"] == ["jim", "sam"]
    with pytest.raises(ValueError, match="already exists"):
        store.create_group(
            name="Dup",
            members=["jim"],
            conversation_id="group:room1",
        )


def test_create_group_rejects_empty_name_or_members(store):
    with pytest.raises(ValueError, match="name"):
        store.create_group(name="  ", members=["jim"])
    with pytest.raises(ValueError, match="members"):
        store.create_group(name="X", members=[])
    with pytest.raises(ValueError, match="invalid user_id"):
        store.create_group(name="X", members=["../x"])
    with pytest.raises(ValueError, match="group:"):
        store.create_group(
            name="X",
            members=["jim"],
            conversation_id="dm:jim",
        )


def test_list_filters_member_and_type(store):
    store.ensure_dm("jim")
    store.ensure_dm("sam")
    store.create_group(
        name="Both",
        members=["jim", "sam"],
        conversation_id="group:both",
    )
    store.create_group(
        name="Jim only",
        members=["jim"],
        conversation_id="group:jonly",
    )

    assert len(store.list()) == 4
    assert {c["id"] for c in store.list(type="dm")} == {"dm:jim", "dm:sam"}
    assert {c["id"] for c in store.list(type="group")} == {
        "group:both",
        "group:jonly",
    }
    jim_rooms = {c["id"] for c in store.list(member_user_id="jim")}
    assert jim_rooms == {"dm:jim", "group:both", "group:jonly"}
    sam_rooms = {c["id"] for c in store.list(member_user_id="sam")}
    assert sam_rooms == {"dm:sam", "group:both"}


def test_list_empty_zero_state(paths):
    """Zero-state: empty store lists [] without error."""
    s = ConversationsStore(paths)
    assert s.list() == []
    s.ensure_layout()
    assert s.list() == []
    assert s.get("dm:missing") is None
    assert s.get("group:nope") is None


def test_list_invalid_type_raises(store):
    with pytest.raises(ValueError, match="invalid conversation type"):
        store.list(type="channel")


# ── update / touch_activity / resolve_address ────────────────────────────────


def test_update_group_name_description_members(store):
    g = store.create_group(
        name="Old",
        members=["jim"],
        conversation_id="group:upd",
        description="d1",
    )
    out = store.update(
        g["id"],
        name="New",
        description="d2",
        members=["jim", "sam"],
    )
    assert out["name"] == "New"
    assert out["description"] == "d2"
    assert out["members"] == ["jim", "sam"]
    assert store.get(g["id"])["name"] == "New"


def test_update_missing_raises(store):
    with pytest.raises(KeyError):
        store.update("group:missing", name="x")


def test_update_dm_members_must_match_peer(store):
    store.ensure_dm("jim")
    with pytest.raises(ValueError, match="dm members"):
        store.update("dm:jim", members=["sam"])
    # Exact peer OK.
    out = store.update("dm:jim", members=["jim"], name="Jim DM")
    assert out["name"] == "Jim DM"
    assert out["members"] == ["jim"]


def test_touch_activity(store):
    store.ensure_dm("jim")
    store.touch_activity("dm:jim", at="2026-01-01T00:00:00+00:00")
    rec = store.get("dm:jim")
    assert rec["last_message_at"] == "2026-01-01T00:00:00+00:00"
    assert rec["updated_at"] == "2026-01-01T00:00:00+00:00"
    # Unknown conversation: no-op, no raise.
    store.touch_activity("group:ghost")


def test_resolve_address(store):
    assert store.resolve_address(conversation_id="dm:jim") == "dm:jim"
    assert store.resolve_address(user_id="sam") == "dm:sam"
    # conversation_id wins over user_id
    assert (
        store.resolve_address(conversation_id="group:room1", user_id="jim")
        == "group:room1"
    )
    assert store.resolve_address() is None
    assert store.resolve_address(conversation_id="", user_id="") is None
    assert store.resolve_address(conversation_id="  ", user_id=None) is None
    with pytest.raises(ValueError):
        store.resolve_address(conversation_id="dm:../x")
    with pytest.raises(ValueError):
        store.resolve_address(user_id="../x")


def test_dm_id_for_user():
    assert dm_id_for_user("jim") == "dm:jim"
    with pytest.raises(ValueError):
        dm_id_for_user("a/b")


# ── index persistence ────────────────────────────────────────────────────────


def test_index_json_shape(store, paths):
    store.ensure_dm("jim")
    store.create_group(name="G", members=["jim"], conversation_id="group:g1")
    index_path = paths.data_dir / "conversations" / "index.json"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    ids = {c["id"] for c in data["conversations"]}
    assert ids == {"dm:jim", "group:g1"}
    for row in data["conversations"]:
        assert "type" in row
        assert "members" in row
        assert "updated_at" in row


# ── reset clears conversations with messages (KD9) ───────────────────────────


def test_clear_conversations_with_messages(paths):
    store = ConversationsStore(paths)
    store.ensure_dm("jim")
    store.create_group(name="G", members=["jim", "sam"], conversation_id="group:g1")
    append_message(
        "user",
        "hi",
        user_id="jim",
        conversation_id="dm:jim",
        paths=paths,
    )
    assert list_messages(paths=paths)
    assert store.list()
    assert (paths.data_dir / "conversations" / "by_id" / "dm_jim.json").is_file()

    clear_messages(paths)
    clear_conversations(paths)

    assert list_messages(paths=paths) == []
    # Empty re-scaffold
    assert (paths.data_dir / "conversations" / "index.json").is_file()
    assert (paths.data_dir / "conversations" / "by_id").is_dir()
    assert not (paths.data_dir / "conversations" / "by_id" / "dm_jim.json").is_file()
    fresh = ConversationsStore(paths)
    assert fresh.list() == []
    assert fresh.get("dm:jim") is None


def test_clear_conversations_zero_state(paths):
    """clear_conversations on missing dir is safe (zero-state)."""
    result = clear_conversations(paths)
    assert result["step"] == "conversations"
    assert (paths.data_dir / "conversations" / "index.json").is_file()
    data = json.loads(
        (paths.data_dir / "conversations" / "index.json").read_text(encoding="utf-8")
    )
    assert data["conversations"] == []
