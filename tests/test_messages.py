from __future__ import annotations

import json
from pathlib import Path

from elyra.config import ElyraPaths
from elyra.messages import (
    append_message,
    get_message,
    list_messages,
    list_messages_for_conversation,
    migrate_legacy_conversation_ids,
)


def _paths(tmp_path) -> ElyraPaths:
    paths = ElyraPaths(
        home=tmp_path,
        model_dir=tmp_path / "model",
        data_dir=tmp_path / "data",
        skills_dir=tmp_path / "skills",
        tools_dir=tmp_path / "tools",
        prompts_dir=tmp_path / "prompts",
    )
    paths.ensure_data_dirs()
    return paths


def test_append_and_list(tmp_path):
    paths = _paths(tmp_path)
    append_message("user", "hello", paths=paths)
    append_message("assistant", "hi", reasoning="r", paths=paths)
    rows = list_messages(paths=paths)
    assert len(rows) == 2
    assert rows[0]["content"] == "hello"
    assert rows[1]["reasoning"] == "r"
    # Text-only rows omit attachments/meta keys (legacy-shaped).
    assert "attachments" not in rows[0]
    assert "meta" not in rows[0]
    # conversation_id omitted when unset (legacy-shaped).
    assert "conversation_id" not in rows[0]


def test_append_with_attachments_and_empty_content(tmp_path):
    paths = _paths(tmp_path)
    att = {
        "id": "att_abc",
        "kind": "image",
        "origin": "user_upload",
        "filename": "1x1.png",
        "mime": "image/png",
        "byte_size": 69,
        "sha256": "a" * 64,
        "created_at": "2026-07-26T00:00:00Z",
        "embedding_status": "none",
        "embedding_ref": None,
        "bound_message_id": None,
    }
    msg = append_message(
        "user",
        "",
        attachments=[att],
        meta={"input_mode": "mixed"},
        paths=paths,
    )
    assert msg.content == ""
    assert msg.attachments is not None and len(msg.attachments) == 1
    assert msg.meta == {"input_mode": "mixed"}

    rows = list_messages(paths=paths)
    assert len(rows) == 1
    assert rows[0]["content"] == ""
    assert rows[0]["attachments"][0]["id"] == "att_abc"
    assert rows[0]["meta"]["input_mode"] == "mixed"

    got = get_message(msg.id, paths=paths)
    assert got is not None
    assert got["id"] == msg.id
    assert got["attachments"][0]["filename"] == "1x1.png"


def test_get_message_missing_and_legacy_row(tmp_path):
    paths = _paths(tmp_path)
    # Legacy-shaped line (no attachments/meta).
    log = paths.data_dir / "messages.jsonl"
    legacy = {
        "id": "legacy-1",
        "role": "user",
        "content": "old",
        "user_id": "operator",
        "created_at": "2020-01-01T00:00:00+00:00",
        "reasoning": "",
        "moment_id": None,
    }
    log.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    assert get_message("legacy-1", paths=paths)["content"] == "old"
    assert get_message("nope", paths=paths) is None
    assert get_message("", paths=paths) is None

    rows = list_messages(paths=paths)
    assert len(rows) == 1
    assert "attachments" not in rows[0]


def test_get_message_skips_corrupt_lines(tmp_path):
    paths = _paths(tmp_path)
    log = paths.data_dir / "messages.jsonl"
    good = append_message("user", "ok", paths=paths)
    # Prepend corrupt line
    existing = log.read_text(encoding="utf-8")
    log.write_text("not-json\n" + existing, encoding="utf-8")
    assert get_message(good.id, paths=paths)["content"] == "ok"


def test_append_conversation_id_and_explicit_user_id_none(tmp_path):
    """conversation_id persisted; explicit user_id=None not coerced to operator."""
    paths = _paths(tmp_path)
    msg = append_message(
        "assistant",
        "hello group",
        user_id=None,
        conversation_id="group:room1",
        paths=paths,
    )
    assert msg.user_id is None
    assert msg.conversation_id == "group:room1"
    row = get_message(msg.id, paths=paths)
    assert row is not None
    assert row.get("user_id") is None
    assert row["conversation_id"] == "group:room1"
    # Disk JSON has null user_id, non-null conversation_id.
    raw = (paths.data_dir / "messages.jsonl").read_text(encoding="utf-8")
    parsed = json.loads(raw.strip())
    assert parsed["user_id"] is None
    assert parsed["conversation_id"] == "group:room1"


def test_append_blank_conversation_id_omitted(tmp_path):
    paths = _paths(tmp_path)
    msg = append_message(
        "user",
        "solo",
        conversation_id="  ",
        paths=paths,
    )
    assert msg.conversation_id is None
    row = get_message(msg.id, paths=paths)
    assert "conversation_id" not in row


def test_list_messages_filter_then_limit_kd17(tmp_path):
    """KD17 pin: limit+10 interleaved jim/sam → list(limit=10, dm:jim) == 10 jim."""
    paths = _paths(tmp_path)
    limit = 10
    # Write limit+10 pairs (jim then sam) so jim has limit+10 rows total,
    # interleaved with sam. Global last-N of `limit` would starve jim.
    for i in range(limit + 10):
        append_message(
            "user",
            f"jim-{i}",
            user_id="jim",
            conversation_id="dm:jim",
            paths=paths,
        )
        append_message(
            "user",
            f"sam-{i}",
            user_id="sam",
            conversation_id="dm:sam",
            paths=paths,
        )
    # Global last-10 is mostly sam (or mixed) — not the pin under test.
    global_tail = list_messages(limit=limit, paths=paths)
    assert len(global_tail) == limit

    jim_rows = list_messages(limit=limit, conversation_id="dm:jim", paths=paths)
    assert len(jim_rows) == limit
    assert all(r["user_id"] == "jim" for r in jim_rows)
    assert all(r["conversation_id"] == "dm:jim" for r in jim_rows)
    # Last-N of jim's own stream: jim-(10) … jim-(19)
    assert [r["content"] for r in jim_rows] == [
        f"jim-{i}" for i in range(10, limit + 10)
    ]

    # Convenience wrapper matches.
    via_helper = list_messages_for_conversation("dm:jim", limit=limit, paths=paths)
    assert [r["id"] for r in via_helper] == [r["id"] for r in jim_rows]


def test_list_messages_legacy_dm_inclusion(tmp_path):
    """Legacy null conversation_id + user_id=jim included in dm:jim filter."""
    paths = _paths(tmp_path)
    log = paths.data_dir / "messages.jsonl"
    # Pre-cutover legacy rows (no conversation_id key).
    legacy_jim = {
        "id": "leg-jim-1",
        "role": "user",
        "content": "old jim",
        "user_id": "jim",
        "created_at": "2020-01-01T00:00:00+00:00",
        "reasoning": "",
        "moment_id": None,
    }
    legacy_jim_asst = {
        "id": "leg-jim-a",
        "role": "assistant",
        "content": "old reply",
        "user_id": "jim",
        "created_at": "2020-01-01T00:00:01+00:00",
        "reasoning": "",
        "moment_id": None,
    }
    legacy_sam = {
        "id": "leg-sam-1",
        "role": "user",
        "content": "old sam",
        "user_id": "sam",
        "created_at": "2020-01-01T00:00:02+00:00",
        "reasoning": "",
        "moment_id": None,
    }
    # Explicit null conversation_id also counts as legacy.
    null_cid_jim = {
        "id": "null-jim",
        "role": "user",
        "content": "null cid jim",
        "user_id": "jim",
        "created_at": "2020-01-01T00:00:03+00:00",
        "reasoning": "",
        "moment_id": None,
        "conversation_id": None,
    }
    with log.open("w", encoding="utf-8") as handle:
        for row in (legacy_jim, legacy_jim_asst, legacy_sam, null_cid_jim):
            handle.write(json.dumps(row) + "\n")

    # New stamped row.
    append_message(
        "user",
        "new jim",
        user_id="jim",
        conversation_id="dm:jim",
        paths=paths,
    )

    jim = list_messages(conversation_id="dm:jim", paths=paths)
    ids = [r["id"] for r in jim]
    assert "leg-jim-1" in ids
    assert "leg-jim-a" in ids
    assert "null-jim" in ids
    assert "leg-sam-1" not in ids
    assert any(r["content"] == "new jim" for r in jim)


def test_list_messages_no_legacy_group_inclusion(tmp_path):
    """Group filter must NOT pull legacy user_id rows of members."""
    paths = _paths(tmp_path)
    log = paths.data_dir / "messages.jsonl"
    legacy_jim = {
        "id": "leg-jim",
        "role": "user",
        "content": "dm history",
        "user_id": "jim",
        "created_at": "2020-01-01T00:00:00+00:00",
        "reasoning": "",
        "moment_id": None,
    }
    group_row = {
        "id": "g-1",
        "role": "user",
        "content": "in group",
        "user_id": "jim",
        "created_at": "2020-01-02T00:00:00+00:00",
        "reasoning": "",
        "moment_id": None,
        "conversation_id": "group:room1",
    }
    with log.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(legacy_jim) + "\n")
        handle.write(json.dumps(group_row) + "\n")

    rows = list_messages(conversation_id="group:room1", paths=paths)
    assert len(rows) == 1
    assert rows[0]["id"] == "g-1"
    assert rows[0]["content"] == "in group"


def test_list_messages_forensic_user_id_filter(tmp_path):
    paths = _paths(tmp_path)
    append_message("user", "j", user_id="jim", conversation_id="dm:jim", paths=paths)
    append_message("user", "s", user_id="sam", conversation_id="dm:sam", paths=paths)
    rows = list_messages(user_id="jim", paths=paths)
    assert len(rows) == 1
    assert rows[0]["content"] == "j"


def test_list_messages_empty_and_unlimited(tmp_path):
    paths = _paths(tmp_path)
    assert list_messages(paths=paths) == []
    assert list_messages(conversation_id="dm:jim", paths=paths) == []
    for i in range(5):
        append_message(
            "user",
            f"m{i}",
            user_id="jim",
            conversation_id="dm:jim",
            paths=paths,
        )
    # limit <= 0 → all matching
    all_rows = list_messages(limit=0, conversation_id="dm:jim", paths=paths)
    assert len(all_rows) == 5
    all_neg = list_messages(limit=-1, conversation_id="dm:jim", paths=paths)
    assert len(all_neg) == 5


def test_migrate_legacy_conversation_ids(tmp_path):
    paths = _paths(tmp_path)
    log = paths.data_dir / "messages.jsonl"
    rows = [
        {
            "id": "a",
            "role": "user",
            "content": "x",
            "user_id": "jim",
            "created_at": "2020-01-01T00:00:00+00:00",
            "reasoning": "",
            "moment_id": None,
        },
        {
            "id": "b",
            "role": "user",
            "content": "y",
            "user_id": "sam",
            "created_at": "2020-01-01T00:00:01+00:00",
            "reasoning": "",
            "moment_id": None,
            "conversation_id": "dm:sam",
        },
        {
            "id": "c",
            "role": "system",
            "content": "sys",
            "user_id": None,
            "created_at": "2020-01-01T00:00:02+00:00",
            "reasoning": "",
            "moment_id": None,
        },
    ]
    log.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    result = migrate_legacy_conversation_ids(paths=paths)
    assert result["ok"] is True
    assert result["rewritten"] == 1
    assert result["total"] == 3
    loaded = list_messages(limit=0, paths=paths)
    by_id = {r["id"]: r for r in loaded}
    assert by_id["a"]["conversation_id"] == "dm:jim"
    assert by_id["b"]["conversation_id"] == "dm:sam"
    # system with null user_id stays without conversation_id
    assert by_id["c"].get("conversation_id") is None


def test_migrate_empty_log(tmp_path):
    paths = _paths(tmp_path)
    result = migrate_legacy_conversation_ids(paths=paths)
    assert result["ok"] is True
    assert result["rewritten"] == 0
    assert result["total"] == 0


def test_migrate_skips_invalid_user_id(tmp_path):
    """Malformed historical user_id is not stamped as conversation_id."""
    paths = _paths(tmp_path)
    log = paths.data_dir / "messages.jsonl"
    rows = [
        {
            "id": "bad",
            "role": "user",
            "content": "weird",
            "user_id": "../escape",
            "created_at": "2020-01-01T00:00:00+00:00",
            "reasoning": "",
            "moment_id": None,
        },
        {
            "id": "good",
            "role": "user",
            "content": "ok",
            "user_id": "jim",
            "created_at": "2020-01-01T00:00:01+00:00",
            "reasoning": "",
            "moment_id": None,
        },
    ]
    log.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    result = migrate_legacy_conversation_ids(paths=paths)
    assert result["ok"] is True
    assert result["rewritten"] == 1
    assert result["skipped_invalid_user_id"] == 1
    loaded = {r["id"]: r for r in list_messages(limit=0, paths=paths)}
    assert loaded["good"]["conversation_id"] == "dm:jim"
    assert loaded["bad"].get("conversation_id") is None
    assert loaded["bad"]["user_id"] == "../escape"


def test_migrate_aborts_if_log_grows_before_replace(tmp_path, monkeypatch):
    """Concurrent growth during migrate → abort without replace (no data loss)."""
    paths = _paths(tmp_path)
    append_message(
        "user",
        "pre",
        user_id="jim",
        paths=paths,
    )
    log = paths.data_dir / "messages.jsonl"
    extra = {
        "id": "concurrent",
        "role": "user",
        "content": "during-migrate",
        "user_id": "sam",
        "created_at": "2020-01-01T00:00:00+00:00",
        "reasoning": "",
        "moment_id": None,
    }

    orig_write_text = Path.write_text

    def grow_then_write(self, data, encoding=None, errors=None, newline=None):
        # When writing the migrate tmp file, simulate another process appending.
        if ".tmp" in self.name and "messages.jsonl" in self.name:
            with log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(extra) + "\n")
        return orig_write_text(
            self, data, encoding=encoding, errors=errors, newline=newline
        )

    monkeypatch.setattr(Path, "write_text", grow_then_write)
    result = migrate_legacy_conversation_ids(paths=paths)
    assert result["ok"] is False
    assert result["error"] == "messages_changed"
    # Both pre and concurrent rows still present (no destructive replace).
    rows = list_messages(limit=0, paths=paths)
    contents = {r["content"] for r in rows}
    assert "pre" in contents
    assert "during-migrate" in contents
    # Pre row still legacy (no conversation_id stamp from aborted migrate).
    pre = next(r for r in rows if r["content"] == "pre")
    assert pre.get("conversation_id") is None