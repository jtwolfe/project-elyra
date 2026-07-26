from __future__ import annotations

import json

from elyra.config import ElyraPaths
from elyra.messages import append_message, get_message, list_messages


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
