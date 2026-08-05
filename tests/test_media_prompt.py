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


# ---------------------------------------------------------------------------
# Completions audio/video expand (PR4 — KD-V10 / KD-V17)
# ---------------------------------------------------------------------------

FIXTURE_WAV = Path(__file__).parent / "fixtures" / "media" / "tiny.wav"
FIXTURE_MP4 = Path(__file__).parent / "fixtures" / "mm_embed" / "tiny.mp4"


def _put_wav(store: MediaStore, *, filename: str = "clip.wav") -> object:
    return store.put_bytes(
        FIXTURE_WAV.read_bytes(),
        filename=filename,
        origin="user_upload",
    )


def _put_mp4(store: MediaStore, *, filename: str = "clip.mp4") -> object:
    return store.put_bytes(
        FIXTURE_MP4.read_bytes(),
        filename=filename,
        origin="user_upload",
    )


def _count_parts(messages: list[dict], part_type: str) -> int:
    n = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == part_type:
                    n += 1
    return n


def _text_of(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text") or "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def test_probe_wav_duration_fixture():
    from elyra.media.prompt import probe_wav_duration_s

    dur = probe_wav_duration_s(FIXTURE_WAV.read_bytes())
    assert dur is not None
    assert abs(dur - 0.1) < 0.02


def test_audio_format_from_att_wav_mp3():
    from elyra.media.prompt import audio_format_from_att

    assert audio_format_from_att({"mime": "audio/wav", "filename": "a.wav"}) == "wav"
    assert audio_format_from_att({"mime": "audio/mpeg", "filename": "a.mp3"}) == "mp3"
    assert audio_format_from_att({"mime": "audio/ogg", "filename": "a.ogg"}) is None


def test_wake_audio_expand_input_audio_part(store):
    """Wake audio → input_audio part with base64 + format=wav."""
    from elyra.media.prompt import AUDIO_PART_TYPE

    att = _put_wav(store)
    glass = [_glass_row("wake-a", content="listen", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-a",
        wake_content="listen",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-a",
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, AUDIO_PART_TYPE) == 1
    wake_msg = next(m for m in expanded if m.get("id") == "wake-a")
    assert isinstance(wake_msg["content"], list)
    audio_parts = [p for p in wake_msg["content"] if p.get("type") == AUDIO_PART_TYPE]
    assert len(audio_parts) == 1
    ia = audio_parts[0]["input_audio"]
    assert ia["format"] == "wav"
    assert base64.b64decode(ia["data"]) == FIXTURE_WAV.read_bytes()
    # Inventory still present.
    assert att.id in _text_of(wake_msg)


def test_wake_video_expand_video_url_part(store):
    """Wake short video → video_url data URL (duration unknown → byte caps)."""
    from elyra.media.prompt import VIDEO_PART_TYPE

    att = _put_mp4(store)
    glass = [_glass_row("wake-v", content="watch", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-v",
        wake_content="watch",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-v",
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, VIDEO_PART_TYPE) == 1
    wake_msg = next(m for m in expanded if m.get("id") == "wake-v")
    video_parts = [p for p in wake_msg["content"] if p.get("type") == VIDEO_PART_TYPE]
    url = video_parts[0]["video_url"]["url"]
    assert url.startswith("data:video/mp4;base64,")
    b64 = url.split(",", 1)[1]
    assert base64.b64decode(b64) == FIXTURE_MP4.read_bytes()


def test_viewing_audio_expand_without_wake(store):
    """viewing_att_ids alone expands audio on the carrier row."""
    from elyra.media.prompt import AUDIO_PART_TYPE
    from elyra.media.viewing import VIEWING_CARRIER_ID

    att = _put_wav(store, filename="viewed.wav")
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
    assert _count_parts(expanded, AUDIO_PART_TYPE) == 1
    carrier = next(m for m in expanded if m.get("id") == VIEWING_CARRIER_ID)
    audio_parts = [p for p in carrier["content"] if p.get("type") == AUDIO_PART_TYPE]
    assert len(audio_parts) == 1


def test_viewing_video_expand_without_wake(store):
    from elyra.media.prompt import VIDEO_PART_TYPE
    from elyra.media.viewing import VIEWING_CARRIER_ID

    att = _put_mp4(store, filename="viewed.mp4")
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
    assert _count_parts(expanded, VIDEO_PART_TYPE) == 1
    carrier = next(m for m in expanded if m.get("id") == VIEWING_CARRIER_ID)
    assert any(p.get("type") == VIDEO_PART_TYPE for p in carrier["content"])


def test_video_duration_over_cap_fail_closed(store):
    """duration_s > 10s → no video_url part; inventory + duration_over_cap notice."""
    from elyra.media.prompt import VIDEO_PART_TYPE

    att = _put_mp4(store)
    ad = _att_dict(att)
    ad["duration_s"] = 45.0
    glass = [_glass_row("wake-long", content="long clip", attachments=[ad])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-long",
        wake_content="long clip",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-long",
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, VIDEO_PART_TYPE) == 0
    wake_msg = next(m for m in expanded if m.get("id") == "wake-long")
    text = _text_of(wake_msg)
    assert att.id in text  # inventory present
    assert "duration_over_cap" in text
    assert "video expand skipped" in text
    # No multimodal parts pretending success.
    assert isinstance(wake_msg["content"], str) or not any(
        isinstance(p, dict) and p.get("type") == VIDEO_PART_TYPE
        for p in (wake_msg["content"] if isinstance(wake_msg["content"], list) else [])
    )


def test_audio_duration_over_cap_fail_closed(store):
    from elyra.media.prompt import AUDIO_PART_TYPE

    att = _put_wav(store)
    ad = _att_dict(att)
    ad["duration_s"] = 60.0  # > 30s audio cap
    glass = [_glass_row("wake-long-a", content="long audio", attachments=[ad])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-long-a",
        wake_content="long audio",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-long-a",
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, AUDIO_PART_TYPE) == 0
    text = _text_of(next(m for m in expanded if m.get("id") == "wake-long-a"))
    assert "duration_over_cap" in text
    assert att.id in text


def test_video_read_failed_fail_closed(store, monkeypatch):
    """Missing blob → inventory + read_failed notice; no video part."""
    from elyra.media.prompt import VIDEO_PART_TYPE

    att = _put_mp4(store)

    def _boom(_aid: str) -> bytes:
        raise FileNotFoundError("gone")

    monkeypatch.setattr(store, "read_bytes", _boom)
    ad = _att_dict(att)
    glass = [_glass_row("wake-miss", content="?", attachments=[ad])]
    meal = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "?", "id": "wake-miss"},
        {"role": "user", "content": "orient"},
    ]
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-miss",
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, VIDEO_PART_TYPE) == 0
    text = _text_of(next(m for m in expanded if m.get("id") == "wake-miss"))
    assert "read_failed" in text
    assert att.id in text


def test_av_expand_off_fail_closed_inventory(store, monkeypatch):
    """ELYRA_AV_EXPAND=0 → inventory + notice; no AV parts."""
    from elyra.media.prompt import AUDIO_PART_TYPE, VIDEO_PART_TYPE

    monkeypatch.setenv("ELYRA_AV_EXPAND", "0")
    att = _put_wav(store)
    glass = [_glass_row("wake-off", content="x", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-off",
        wake_content="x",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-off",
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, AUDIO_PART_TYPE) == 0
    assert _count_parts(expanded, VIDEO_PART_TYPE) == 0
    text = _text_of(next(m for m in expanded if m.get("id") == "wake-off"))
    assert "ELYRA_AV_EXPAND=0" in text
    assert att.id in text


def test_local_provider_av_fail_closed(store):
    """Non-xAI provider → inventory + local AV notice; no data URLs."""
    from elyra.media.prompt import AUDIO_PART_TYPE

    att = _put_wav(store)
    glass = [_glass_row("wake-loc", content="x", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-loc",
        wake_content="x",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-loc",
        media_store=store,
        provider="local",
    )
    assert _count_parts(expanded, AUDIO_PART_TYPE) == 0
    text = _text_of(next(m for m in expanded if m.get("id") == "wake-loc"))
    assert "audio/video expansion requires xAI" in text
    assert att.id in text


def test_shared_video_budget_one_across_wake_and_viewing(store):
    """MAX_VISION_VIDEO=1 shared: wake video takes the slot; viewing gets notice."""
    from elyra.media.prompt import MAX_VISION_VIDEO, VIDEO_PART_TYPE
    from elyra.media.viewing import VIEWING_CARRIER_ID

    wake_att = _put_mp4(store, filename="wake.mp4")
    view_att = store.put_bytes(
        FIXTURE_MP4.read_bytes(),
        filename="view.mp4",
        origin="user_upload",
    )
    glass = [
        _glass_row("wake-b", content="both", attachments=[_att_dict(wake_att)])
    ]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-b",
        wake_content="both",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-b",
        viewing_att_ids=[view_att.id],
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, VIDEO_PART_TYPE) == MAX_VISION_VIDEO
    wake_msg = next(m for m in expanded if m.get("id") == "wake-b")
    carrier = next(m for m in expanded if m.get("id") == VIEWING_CARRIER_ID)
    wake_vids = [
        p
        for p in wake_msg["content"]
        if isinstance(p, dict) and p.get("type") == VIDEO_PART_TYPE
    ]
    assert len(wake_vids) == 1
    # Viewing row: no second video part; count_cap notice.
    if isinstance(carrier["content"], list):
        c_vids = [p for p in carrier["content"] if p.get("type") == VIDEO_PART_TYPE]
        assert c_vids == []
    assert "count_cap" in _text_of(carrier)


def test_shared_audio_budget_two_cap(store):
    """Three audio atts → only MAX_VISION_AUDIO=2 parts."""
    from elyra.media.prompt import AUDIO_PART_TYPE, MAX_VISION_AUDIO

    atts = [
        store.put_bytes(
            FIXTURE_WAV.read_bytes(),
            filename=f"a{i}.wav",
            origin="user_upload",
        )
        for i in range(3)
    ]
    glass = [
        _glass_row(
            "wake-aud",
            content="many",
            attachments=[_att_dict(a) for a in atts],
        )
    ]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-aud",
        wake_content="many",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-aud",
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, AUDIO_PART_TYPE) == MAX_VISION_AUDIO
    text = _text_of(next(m for m in expanded if m.get("id") == "wake-aud"))
    assert "count_cap" in text


def test_image_path_not_regressed_with_av(store):
    """Image expand still works when AV fixtures are also present on wake."""
    img = _put_png(store)
    wav = _put_wav(store)
    glass = [
        _glass_row(
            "wake-mix",
            content="mix",
            attachments=[_att_dict(img), _att_dict(wav)],
        )
    ]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-mix",
        wake_content="mix",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-mix",
        media_store=store,
        provider="xai",
    )
    from elyra.media.prompt import AUDIO_PART_TYPE

    assert _count_image_parts(expanded) == 1
    assert _count_parts(expanded, AUDIO_PART_TYPE) == 1


def test_strip_meal_wire_fields_keeps_av_parts():
    from elyra.media.prompt import AUDIO_PART_TYPE, VIDEO_PART_TYPE

    meal = [
        {
            "role": "user",
            "id": "drop-me",
            "content": [
                {"type": "text", "text": "hi"},
                {
                    "type": AUDIO_PART_TYPE,
                    "input_audio": {"data": "YQ==", "format": "wav"},
                },
                {
                    "type": VIDEO_PART_TYPE,
                    "video_url": {"url": "data:video/mp4;base64,YQ=="},
                },
            ],
        }
    ]
    wire = strip_meal_wire_fields(meal)
    assert "id" not in wire[0]
    types = [p["type"] for p in wire[0]["content"]]
    assert types == ["text", AUDIO_PART_TYPE, VIDEO_PART_TYPE]


def test_estimate_content_tokens_av_not_base64_strlen():
    """AV parts must not contribute strlen(base64) to meal token estimates."""
    huge = "A" * 100_000
    n = estimate_content_tokens(
        [
            {"type": "text", "text": "abcd"},
            {"type": "input_audio", "input_audio": {"data": huge, "format": "wav"}},
            {
                "type": "video_url",
                "video_url": {"url": f"data:video/mp4;base64,{huge}"},
            },
        ]
    )
    # 1 text token + 2 * 1024 heuristics
    assert n == 1 + 1024 + 1024


def test_viewing_entries_duration_cap(store):
    """ViewingEntry.duration_s flows into expand duration_over_cap."""
    from elyra.media.prompt import VIDEO_PART_TYPE
    from elyra.media.viewing import VIEWING_CARRIER_ID, ViewingEntry

    att = _put_mp4(store)
    entries = {
        att.id: ViewingEntry(
            att_id=att.id,
            kind="video",
            mime="video/mp4",
            filename="long.mp4",
            byte_size=att.byte_size,
            duration_s=12.5,
        )
    }
    meal = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "orient"},
    ]
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id={},
        viewing_att_ids=[att.id],
        viewing_entries=entries,
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, VIDEO_PART_TYPE) == 0
    carrier = next(m for m in expanded if m.get("id") == VIEWING_CARRIER_ID)
    assert "duration_over_cap" in _text_of(carrier)


def test_viewing_entry_duration_wins_over_store_meta(store):
    """ViewingEntry.duration_s overrides store/glass under-cap stamp (Issue 1)."""
    from elyra.media.prompt import VIDEO_PART_TYPE
    from elyra.media.viewing import VIEWING_CARRIER_ID, ViewingEntry, viewing_att_dicts

    att = _put_mp4(store)

    class _MetaWithDuration:
        def to_dict(self):
            d = _att_dict(att)
            d["duration_s"] = 5.0  # under-cap lie from store
            return d

    class _StoreWrap:
        def get(self, aid: str):
            if aid == att.id:
                return _MetaWithDuration()
            return store.get(aid)

        def read_bytes(self, aid: str) -> bytes:
            return store.read_bytes(aid)

    wrap = _StoreWrap()
    entries = {
        att.id: ViewingEntry(
            att_id=att.id,
            kind="video",
            mime="video/mp4",
            filename="long.mp4",
            byte_size=att.byte_size,
            duration_s=45.0,
        )
    }
    # Unit: ViewingEntry wins on the attachment dict.
    rows = viewing_att_dicts(entries, wrap)
    assert rows[0]["duration_s"] == 45.0

    meal = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "orient"},
    ]
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id={},
        viewing_att_ids=[att.id],
        viewing_entries=entries,
        media_store=wrap,
        provider="xai",
    )
    assert _count_parts(expanded, VIDEO_PART_TYPE) == 0
    carrier = next(m for m in expanded if m.get("id") == VIEWING_CARRIER_ID)
    assert "duration_over_cap" in _text_of(carrier)


def test_video_duration_unknown_notice_on_stub_mp4(store):
    """Stub mp4 without mvhd expands under byte caps + duration_unknown soft notice."""
    from elyra.media.prompt import VIDEO_PART_TYPE

    att = _put_mp4(store)  # fixtures/mm_embed/tiny.mp4 — ftyp only
    glass = [_glass_row("wake-unk", content="?", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-unk",
        wake_content="?",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-unk",
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, VIDEO_PART_TYPE) == 1
    text = _text_of(next(m for m in expanded if m.get("id") == "wake-unk"))
    assert "duration_unknown" in text
    assert att.id in text


def test_plain_dict_viewing_entries_safe(store):
    """Plain-dict viewing_entries must not crash expand (Issue 3)."""
    from elyra.media.prompt import VIDEO_PART_TYPE
    from elyra.media.viewing import VIEWING_CARRIER_ID

    att = _put_mp4(store)
    entries = {
        att.id: {
            "att_id": att.id,
            "kind": "video",
            "mime": "video/mp4",
            "filename": "plain.mp4",
            "byte_size": att.byte_size,
            "duration_s": 45.0,
        }
    }
    meal = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "orient"},
    ]
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id={},
        viewing_att_ids=[att.id],
        viewing_entries=entries,
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, VIDEO_PART_TYPE) == 0
    carrier = next(m for m in expanded if m.get("id") == VIEWING_CARRIER_ID)
    assert "duration_over_cap" in _text_of(carrier)


def _mp4_with_mvhd_duration_s(seconds: float, *, timescale: int = 1000) -> bytes:
    """Minimal ftyp+moov/mvhd blob for hermetic duration probe tests."""
    import struct

    def box(typ: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", 8 + len(payload)) + typ + payload

    duration_ticks = int(round(seconds * timescale))
    # mvhd v0: ver+flags(4) + ctime(4) + mtime(4) + timescale(4) + duration(4)
    mvhd = (
        bytes([0, 0, 0, 0])
        + struct.pack(">II", 0, 0)
        + struct.pack(">I", timescale)
        + struct.pack(">I", duration_ticks)
        + b"\x00" * 80
    )
    moov = box(b"moov", box(b"mvhd", mvhd))
    ftyp = box(b"ftyp", b"isom\x00\x00\x00\x00isomiso2mp41")
    return ftyp + moov


def test_probe_mp4_mvhd_duration_over_cap_expand(store):
    """Expand path uses probe_mp4 duration (no stamped duration_s) for hard cap."""
    from elyra.media.prompt import VIDEO_PART_TYPE, probe_mp4_duration_s

    blob = _mp4_with_mvhd_duration_s(15.0)
    assert probe_mp4_duration_s(blob) == 15.0
    att = store.put_bytes(blob, filename="long15.mp4", origin="user_upload")
    # No duration_s on attachment dict — probe only.
    glass = [_glass_row("wake-probe", content="long", attachments=[_att_dict(att)])]
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_message_id="wake-probe",
        wake_content="long",
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id="wake-probe",
        media_store=store,
        provider="xai",
    )
    assert _count_parts(expanded, VIDEO_PART_TYPE) == 0
    text = _text_of(next(m for m in expanded if m.get("id") == "wake-probe"))
    assert "duration_over_cap" in text
    assert "duration_unknown" not in text
