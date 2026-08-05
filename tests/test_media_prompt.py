"""Meal-time multimodal expand / strip (PR5 — KD6, KD20, KD25)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.loop.context import assemble_outer_meal, estimate_content_tokens
from elyra.media import MediaStore
from elyra.media.prompt import (
    append_inventory_to_content,
    expand_meal_for_provider,
    extract_text_for_attachment,
    format_inventory_block,
    index_glass,
    strip_meal_wire_fields,
)
from elyra.messages import append_message, list_messages

FIXTURE_PNG = Path(__file__).parent / "fixtures" / "media" / "1x1.png"

SYSTEM = "SYS"
ORIENT = (
    "orient {{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}"
    "{{GOALS}}{{SKILL_CATALOG}}{{SKILL_BIAS}}"
)


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    return MediaStore(paths)


def _put_png(store: MediaStore, *, filename: str = "shot.png") -> object:
    data = FIXTURE_PNG.read_bytes()
    return store.put_bytes(data, filename=filename, origin="user_upload")


def _att_dict(att) -> dict:
    return att.to_dict()


def _glass_row(
    msg_id: str,
    *,
    content: str = "",
    attachments: list | None = None,
    role: str = "user",
) -> dict:
    row: dict = {"role": role, "content": content, "id": msg_id}
    if attachments is not None:
        row["attachments"] = attachments
    return row


def _count_image_parts(messages: list[dict]) -> int:
    n = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    n += 1
    return n


def test_strip_meal_wire_fields_drops_id_and_extras():
    meal = [
        {"role": "system", "content": "s", "id": "sys-should-drop"},
        {"role": "user", "content": "hi", "id": "m1", "attachments": [{"id": "a"}]},
        {
            "role": "user",
            "content": [{"type": "text", "text": "x"}],
            "id": "m2",
        },
    ]
    wire = strip_meal_wire_fields(meal)
    assert wire == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
        {"role": "user", "content": [{"type": "text", "text": "x"}]},
    ]
    for m in wire:
        assert set(m.keys()) == {"role", "content"}
        assert "id" not in m


def test_format_inventory_block_tab_columns():
    atts = [
        {
            "id": "att_a1b2",
            "filename": "screenshot.png",
            "kind": "image",
            "mime": "image/png",
            "byte_size": 184422,
            "sandbox_relpath": "media/att_a1b2/screenshot.png",
        }
    ]
    block = format_inventory_block(atts)
    assert block.startswith("[attachments]\n")
    assert (
        "- att_a1b2\tscreenshot.png\timage\timage/png\t184422\t"
        "media/att_a1b2/screenshot.png"
    ) in block


def test_append_inventory_media_only_leading_blank():
    atts = [
        {
            "id": "att_x",
            "filename": "a.png",
            "kind": "image",
            "mime": "image/png",
            "byte_size": 10,
            "sandbox_relpath": "media/att_x/a.png",
        }
    ]
    text = append_inventory_to_content("", atts)
    assert text.startswith("\n[attachments]\n")
    assert "att_x" in text


def test_legacy_inventory_double_guard():
    legacy = "caption\n\n---\n**Attachments** (listed for Elyra; binary…):\n- x"
    atts = [
        {
            "id": "att_x",
            "filename": "a.png",
            "kind": "image",
            "mime": "image/png",
            "byte_size": 1,
            "sandbox_relpath": "-",
        }
    ]
    assert append_inventory_to_content(legacy, atts) == legacy


def test_two_rebuilds_same_vision_part_count(store):
    """KD20: expand on every rebuild is idempotent for vision counts."""
    att = _put_png(store)
    glass = [
        _glass_row("wake-1", content="", attachments=[_att_dict(att)]),
    ]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_content="",
        wake_message_id="wake-1",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    glass_by_id = index_glass(glass)
    kwargs = dict(
        glass_by_id=glass_by_id,
        wake_message_id="wake-1",
        media_store=store,
        provider="xai",
    )
    a = expand_meal_for_provider(meal, **kwargs)
    b = expand_meal_for_provider(meal, **kwargs)
    assert _count_image_parts(a) == _count_image_parts(b) == 1
    # Inventory strings identical.
    inv_a = next(
        p["text"]
        for m in a
        if isinstance(m.get("content"), list)
        for p in m["content"]
        if p.get("type") == "text"
    )
    inv_b = next(
        p["text"]
        for m in b
        if isinstance(m.get("content"), list)
        for p in m["content"]
        if p.get("type") == "text"
    )
    assert inv_a == inv_b
    assert "[attachments]" in inv_a


def test_two_media_only_rows_wake_second_no_swap(store):
    """Two consecutive media-only rows; wake=second → vision only for second."""
    att1 = _put_png(store, filename="first.png")
    att2 = store.put_bytes(
        FIXTURE_PNG.read_bytes(),
        filename="second.png",
        origin="user_upload",
    )
    # Distinct att ids (new id even if same sha).
    assert att1.id != att2.id
    glass = [
        _glass_row("m-first", content="", attachments=[_att_dict(att1)]),
        _glass_row("m-second", content="", attachments=[_att_dict(att2)]),
    ]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_content="",
        wake_message_id="m-second",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    # retain_ids keeps both history ids
    hist = meal[1:-1]
    assert [m.get("id") for m in hist] == ["m-first", "m-second"]

    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="m-second",
        media_store=store,
        provider="xai",
    )
    by_id = {m.get("id"): m for m in expanded if m.get("id")}
    first = by_id["m-first"]
    second = by_id["m-second"]

    # First: inventory string only — no image parts, no swap.
    assert isinstance(first["content"], str)
    assert "att_" in first["content"] or att1.id in first["content"]
    assert att1.id in first["content"]
    assert att2.id not in first["content"]

    # Second: multimodal with vision for second's image only.
    assert isinstance(second["content"], list)
    image_parts = [p for p in second["content"] if p.get("type") == "image_url"]
    text_parts = [p for p in second["content"] if p.get("type") == "text"]
    assert len(image_parts) == 1
    assert len(text_parts) == 1
    assert att2.id in text_parts[0]["text"]
    assert att1.id not in text_parts[0]["text"]
    url = image_parts[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    # Decoded bytes match fixture (not first-swapped).
    b64 = url.split(",", 1)[1]
    assert base64.b64decode(b64) == FIXTURE_PNG.read_bytes()


def test_wire_has_no_id_after_strip(store):
    att = _put_png(store)
    glass = [_glass_row("w1", content="see this", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="w1",
        wake_content="see this",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="w1",
        media_store=store,
        provider="xai",
    )
    wire = strip_meal_wire_fields(expanded)
    assert _count_image_parts(wire) == 1
    for m in wire:
        assert "id" not in m
        assert "attachments" not in m


def test_no_base64_in_jsonl(store, paths):
    """Glass JSONL stays string content + attachments; no base64 blobs."""
    att = _put_png(store)
    msg = append_message(
        "user",
        "",
        attachments=[_att_dict(att)],
        paths=paths,
    )
    # Expand path for model — in-memory only.
    glass = list_messages(paths=paths)
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id=msg.id,
        wake_content="",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id=msg.id,
        media_store=store,
        provider="xai",
    )
    assert _count_image_parts(expanded) == 1

    # Durable JSONL must not contain data URLs / base64 image payload.
    jsonl = (paths.data_dir / "messages.jsonl").read_text(encoding="utf-8")
    assert "base64" not in jsonl
    assert "data:image" not in jsonl
    row = json.loads(jsonl.strip().splitlines()[-1])
    assert row["content"] == ""
    assert row["attachments"][0]["id"] == att.id
    assert "data:" not in json.dumps(row)


def test_local_provider_fail_closed_no_vision(store):
    att = _put_png(store)
    glass = [_glass_row("w-local", content="", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="w-local",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="w-local",
        media_store=store,
        provider="local",
    )
    assert _count_image_parts(expanded) == 0
    wake = next(m for m in expanded if m.get("id") == "w-local")
    assert isinstance(wake["content"], str)
    assert "[attachments]" in wake["content"]
    assert "xAI" in wake["content"] or "vision" in wake["content"].lower()
    assert "base64" not in wake["content"]


def test_text_extract_tier_a_small_file(store):
    body = b"hello from notes\nline 2\n"
    att = store.put_bytes(
        body,
        filename="notes.txt",
        mime="text/plain",
        origin="user_upload",
    )
    glass = [
        _glass_row(
            "w-text",
            content="please read",
            attachments=[_att_dict(att)],
        )
    ]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="w-text",
        wake_content="please read",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="w-text",
        media_store=store,
        provider="xai",
    )
    wake = next(m for m in expanded if m.get("id") == "w-text")
    # No image parts; text content includes inventory + fenced extract.
    assert isinstance(wake["content"], str)
    assert "hello from notes" in wake["content"]
    assert "notes.txt" in wake["content"]
    assert "[attachments]" in wake["content"]
    # Non-wake would not extract — inventory only path covered elsewhere.


def test_text_extract_not_on_non_wake_history(store):
    body = b"secret history file"
    att = store.put_bytes(body, filename="hist.txt", mime="text/plain")
    glass = [
        _glass_row("old", content="old", attachments=[_att_dict(att)]),
        _glass_row("wake", content="now"),
    ]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake",
        wake_content="now",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake",
        media_store=store,
        provider="xai",
    )
    old = next(m for m in expanded if m.get("id") == "old")
    assert isinstance(old["content"], str)
    assert "[attachments]" in old["content"]
    assert "secret history file" not in old["content"]


def test_tts_cache_excluded_from_inventory(store):
    att = _put_png(store)
    tts = {
        "id": "att_tts",
        "kind": "tts_cache",
        "filename": "speak.mp3",
        "mime": "audio/mpeg",
        "byte_size": 100,
        "sandbox_relpath": "-",
    }
    glass = [
        _glass_row(
            "w1",
            content="hi",
            attachments=[_att_dict(att), tts],
        )
    ]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="w1",
        wake_content="hi",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="w1",
        media_store=store,
        provider="xai",
    )
    wake = next(m for m in expanded if m.get("id") == "w1")
    text = (
        wake["content"]
        if isinstance(wake["content"], str)
        else next(p["text"] for p in wake["content"] if p.get("type") == "text")
    )
    assert att.id in text
    assert "att_tts" not in text
    assert "tts_cache" not in text


def test_estimate_content_tokens_image_heuristic():
    n = estimate_content_tokens(
        [
            {"type": "text", "text": "abcd"},  # 1 token
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
        ]
    )
    assert n == 1 + 1024


def test_extract_text_helper_oversize_skipped(store):
    big = b"x" * (256 * 1024 + 1)
    att = store.put_bytes(big, filename="big.txt", mime="text/plain")
    assert extract_text_for_attachment(_att_dict(att), store) is None


# ---------------------------------------------------------------------------
# Viewing set expand (PR2 — KD-V4 shared image budget + carrier)
# ---------------------------------------------------------------------------


def test_viewing_expand_includes_image_without_wake(store):
    """viewing_att_ids alone → carrier full-expand with image_url (no wake)."""
    from elyra.media.viewing import VIEWING_CARRIER_ID

    att = _put_png(store, filename="viewed.png")
    meal = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "please look"},
        {"role": "user", "content": "orient"},
    ]
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id={},
        wake_message_id=None,
        viewing_att_ids=[att.id],
        media_store=store,
        provider="xai",
    )
    assert _count_image_parts(expanded) == 1
    carrier = next(m for m in expanded if m.get("id") == VIEWING_CARRIER_ID)
    assert isinstance(carrier["content"], list)
    image_parts = [p for p in carrier["content"] if p.get("type") == "image_url"]
    assert len(image_parts) == 1
    url = image_parts[0]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    b64 = url.split(",", 1)[1]
    assert base64.b64decode(b64) == FIXTURE_PNG.read_bytes()
    # Carrier sits before orient.
    assert expanded[-1].get("id") is None
    assert expanded[-1]["content"] == "orient"


def test_empty_viewing_set_status_quo_wake_only(store):
    """Empty viewing_att_ids ≡ legacy wake-only expand."""
    att = _put_png(store)
    glass = [_glass_row("wake-1", content="see", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-1",
        wake_content="see",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    kwargs = dict(
        glass_by_id=index_glass(glass),
        wake_message_id="wake-1",
        media_store=store,
        provider="xai",
    )
    base = expand_meal_for_provider(meal, **kwargs)
    empty_view = expand_meal_for_provider(meal, viewing_att_ids=[], **kwargs)
    none_view = expand_meal_for_provider(meal, viewing_att_ids=None, **kwargs)
    assert _count_image_parts(base) == _count_image_parts(empty_view) == 1
    assert _count_image_parts(none_view) == 1
    # No synthetic carrier id when viewing empty.
    assert all(m.get("id") != "_viewing_carrier" for m in empty_view)
    assert all(m.get("id") != "_viewing_carrier" for m in none_view)


def test_wake_and_viewing_shared_image_budget_cap(store):
    """3 wake images + 3 viewing images → total 4 parts (shared MAX_VISION_IMAGES)."""
    from elyra.media.prompt import MAX_VISION_IMAGES
    from elyra.media.viewing import VIEWING_CARRIER_ID

    wake_atts = [
        store.put_bytes(
            FIXTURE_PNG.read_bytes(),
            filename=f"w{i}.png",
            origin="user_upload",
        )
        for i in range(3)
    ]
    view_atts = [
        store.put_bytes(
            FIXTURE_PNG.read_bytes(),
            filename=f"v{i}.png",
            origin="user_upload",
        )
        for i in range(3)
    ]
    glass = [
        _glass_row(
            "wake-multi",
            content="multi",
            attachments=[_att_dict(a) for a in wake_atts],
        )
    ]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-multi",
        wake_content="multi",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-multi",
        viewing_att_ids=[a.id for a in view_atts],
        media_store=store,
        provider="xai",
    )
    assert _count_image_parts(expanded) == MAX_VISION_IMAGES
    # Wake-first: all 3 wake images expand; viewing gets the remaining 1.
    wake_msg = next(m for m in expanded if m.get("id") == "wake-multi")
    carrier = next(m for m in expanded if m.get("id") == VIEWING_CARRIER_ID)
    wake_imgs = [
        p for p in wake_msg["content"] if isinstance(p, dict) and p.get("type") == "image_url"
    ]
    carrier_imgs = [
        p
        for p in carrier["content"]
        if isinstance(p, dict) and p.get("type") == "image_url"
    ]
    assert len(wake_imgs) == 3
    assert len(carrier_imgs) == 1


def test_viewing_dedupes_same_att_already_on_wake(store):
    """Same att_id on wake and viewing expands once (wake owns the part)."""
    from elyra.media.viewing import VIEWING_CARRIER_ID

    att = _put_png(store)
    glass = [_glass_row("w-dup", content="x", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="w-dup",
        wake_content="x",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="w-dup",
        viewing_att_ids=[att.id],
        media_store=store,
        provider="xai",
    )
    assert _count_image_parts(expanded) == 1
    wake_msg = next(m for m in expanded if m.get("id") == "w-dup")
    assert isinstance(wake_msg["content"], list)
    wake_imgs = [p for p in wake_msg["content"] if p.get("type") == "image_url"]
    assert len(wake_imgs) == 1
    carrier = next(m for m in expanded if m.get("id") == VIEWING_CARRIER_ID)
    # Carrier may be inventory-only text (no second image part for same att).
    if isinstance(carrier["content"], list):
        c_imgs = [p for p in carrier["content"] if p.get("type") == "image_url"]
        assert c_imgs == []


def test_viewing_extract_on_full_expand_carrier(store):
    """Tier-A text extract runs on viewing carrier (full-expand row)."""
    from elyra.media.viewing import VIEWING_CARRIER_ID

    body = b"viewed notes line\n"
    att = store.put_bytes(
        body, filename="notes.txt", mime="text/plain", origin="user_upload"
    )
    meal = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "orient"},
    ]
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id={},
        viewing_att_ids=[att.id],
        media_store=store,
        provider="xai",
    )
    carrier = next(m for m in expanded if m.get("id") == VIEWING_CARRIER_ID)
    text = (
        carrier["content"]
        if isinstance(carrier["content"], str)
        else next(p["text"] for p in carrier["content"] if p.get("type") == "text")
    )
    assert "viewed notes line" in text
    assert "notes.txt" in text
