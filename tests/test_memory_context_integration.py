"""PR6 hard gate: memory meal drop-in + media expand parity.

When memory.enabled and store healthy:
- rebuild path uses compose_outer_messages (no full sliding glass)
- image-bearing and media-only wakes expand equivalently to legacy
- hybrid wake glass row when observation atom missing
Fallback: flag off or store down → legacy assemble + expand.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.loop.context import assemble_outer_meal
from elyra.media import MediaStore
from elyra.media.prompt import (
    expand_meal_for_provider,
    index_glass,
    strip_meal_wire_fields,
)
from elyra.memory.config import MemorySettings
from elyra.memory.meal import (
    compose_outer_messages,
    expand_memory_meal_for_provider,
    meal_item_to_message,
)
from elyra.memory.promote import promote_wake_observation
from elyra.memory.store import open_memory_store
from elyra.messages import append_message, list_messages
from elyra.presence.worker import PresenceWorker
from elyra.settings import default_settings
from elyra.llm.client import StubChatClient

FIXTURE_PNG = Path(__file__).parent / "fixtures" / "media" / "1x1.png"

SYSTEM = "SYS-memory-meal"
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
def media(paths):
    return MediaStore(paths)


@pytest.fixture
def mem_store(paths):
    s = open_memory_store(
        paths, MemorySettings(write_atoms=True, enabled=True, backend="jsonl")
    )
    yield s
    s.close()


def _put_png(store: MediaStore, *, filename: str = "shot.png"):
    return store.put_bytes(
        FIXTURE_PNG.read_bytes(), filename=filename, origin="user_upload"
    )


def _count_image_parts(messages: list[dict]) -> int:
    n = 0
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    n += 1
    return n


def _inventory_blob(messages: list[dict]) -> str:
    chunks: list[str] = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    chunks.append(str(part.get("text") or ""))
    return "\n".join(chunks)


def _legacy_expand(
    *,
    glass: list[dict],
    wake_message_id: str,
    wake_content: str,
    media_store: MediaStore,
    provider: str = "xai",
) -> list[dict]:
    meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=ORIENT,
        wake_content=wake_content,
        wake_message_id=wake_message_id,
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    expanded = expand_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id=wake_message_id,
        media_store=media_store,
        provider=provider,
    )
    return strip_meal_wire_fields(expanded)


def _memory_expand(
    *,
    mem_store,
    open_moment_id: str,
    glass: list[dict],
    wake_message_id: str,
    media_store: MediaStore,
    provider: str = "xai",
) -> list[dict]:
    meal = compose_outer_messages(
        mem_store,
        open_moment_id=open_moment_id,
        budget_tokens=24_000,
        system_text=SYSTEM,
        orient_text="orient-body",
        settings=MemorySettings(enabled=True, write_atoms=True),
    )
    expanded = expand_memory_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id=wake_message_id,
        media_store=media_store,
        provider=provider,
    )
    return strip_meal_wire_fields(expanded)


# ---------------------------------------------------------------------------
# Hard gate: image-bearing wake
# ---------------------------------------------------------------------------


def test_image_bearing_wake_memory_enabled_parity(paths, media, mem_store):
    """Image wake + memory.enabled → inventory + vision parts match legacy."""
    att = _put_png(media)
    content = "look at this seal"
    msg = append_message(
        "user",
        content,
        attachments=[att.to_dict()],
        paths=paths,
    )
    wake_id = msg.id
    glass = list_messages(paths=paths)

    open_mid = "moment-img"
    atom = promote_wake_observation(
        mem_store,
        open_mid,
        content=content,
        message_id=wake_id,
        media_ids=[att.id],
        why_now="user_message",
        settings=MemorySettings(write_atoms=True),
    )
    assert atom is not None
    assert atom.media_ids == (att.id,)
    assert atom.meta.get("wake_message_id") == wake_id

    legacy = _legacy_expand(
        glass=glass,
        wake_message_id=wake_id,
        wake_content=content,
        media_store=media,
    )
    memory = _memory_expand(
        mem_store=mem_store,
        open_moment_id=open_mid,
        glass=glass,
        wake_message_id=wake_id,
        media_store=media,
    )

    assert _count_image_parts(legacy) == 1
    assert _count_image_parts(memory) == 1
    # Inventory parity: attachment id present on both paths.
    leg_blob = _inventory_blob(legacy)
    mem_blob = _inventory_blob(memory)
    assert att.id in leg_blob
    assert att.id in mem_blob
    assert "[attachments]" in mem_blob
    # Memory path must not reintroduce full sliding glass transcript as a
    # separate history channel beyond labeled context + orient.
    roles = [m.get("role") for m in memory]
    assert roles[0] == "system"
    assert "system" in roles
    # Wire strip: no host ids
    for m in memory:
        assert "id" not in m
        assert "_memory_media_ids" not in m
    # Decoded vision bytes match fixture (same as legacy).
    def _first_b64(msgs: list[dict]) -> bytes:
        for m in msgs:
            c = m.get("content")
            if not isinstance(c, list):
                continue
            for p in c:
                if p.get("type") == "image_url":
                    url = p["image_url"]["url"]
                    return base64.b64decode(url.split(",", 1)[1])
        raise AssertionError("no image part")

    assert _first_b64(memory) == FIXTURE_PNG.read_bytes()
    assert _first_b64(legacy) == FIXTURE_PNG.read_bytes()


# ---------------------------------------------------------------------------
# Hard gate: media-only wake
# ---------------------------------------------------------------------------


def test_media_only_wake_memory_enabled_parity(paths, media, mem_store):
    """Media-only (empty text) wake: observation atom + expand still runs."""
    att = _put_png(media, filename="only.png")
    msg = append_message(
        "user",
        "",
        attachments=[att.to_dict()],
        paths=paths,
    )
    wake_id = msg.id
    assert msg.content == ""
    glass = list_messages(paths=paths)

    open_mid = "moment-media-only"
    atom = promote_wake_observation(
        mem_store,
        open_mid,
        content="",
        message_id=wake_id,
        media_ids=[att.id],
        settings=MemorySettings(write_atoms=True),
    )
    assert atom is not None
    assert atom.content_text == ""
    assert atom.media_ids == (att.id,)
    assert mem_store.list_by_moment(open_mid)

    legacy = _legacy_expand(
        glass=glass,
        wake_message_id=wake_id,
        wake_content="",
        media_store=media,
    )
    memory = _memory_expand(
        mem_store=mem_store,
        open_moment_id=open_mid,
        glass=glass,
        wake_message_id=wake_id,
        media_store=media,
    )

    assert _count_image_parts(legacy) == _count_image_parts(memory) == 1
    assert att.id in _inventory_blob(memory)
    assert "[attachments]" in _inventory_blob(memory)


def test_memory_media_ids_seed_without_glass_row(media, mem_store):
    """Atom media_ids alone (no glass row) still yield inventory expand."""
    att = _put_png(media, filename="seeded.png")
    open_mid = "moment-seed"
    promote_wake_observation(
        mem_store,
        open_mid,
        content="caption",
        message_id="ghost-wake",
        media_ids=[att.id],
        settings=MemorySettings(write_atoms=True),
    )
    meal = compose_outer_messages(
        mem_store,
        open_moment_id=open_mid,
        budget_tokens=24_000,
        system_text=SYSTEM,
        orient_text="orient",
        settings=MemorySettings(enabled=True, write_atoms=True),
    )
    # No glass_by_id — expand must seed from _memory_media_ids / media_store.
    expanded = expand_memory_meal_for_provider(
        meal,
        glass_by_id={},
        wake_message_id="ghost-wake",
        media_store=media,
        provider="xai",
    )
    wire = strip_meal_wire_fields(expanded)
    assert _count_image_parts(wire) == 1
    assert att.id in _inventory_blob(wire)


def test_hybrid_wake_row_when_atom_missing(paths, media, mem_store):
    """If wake obs atom missing, inject one glass wake row for expand."""
    att = _put_png(media, filename="hybrid.png")
    msg = append_message(
        "user",
        "hybrid caption",
        attachments=[att.to_dict()],
        paths=paths,
    )
    wake_id = msg.id
    glass = list_messages(paths=paths)
    # Empty open moment — no wake atom.
    open_mid = "moment-empty"
    meal = compose_outer_messages(
        mem_store,
        open_moment_id=open_mid,
        budget_tokens=24_000,
        system_text=SYSTEM,
        orient_text="orient-body",
        settings=MemorySettings(enabled=True),
    )
    assert not any(m.get("id") == wake_id for m in meal)

    expanded = expand_memory_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id=wake_id,
        media_store=media,
        provider="xai",
    )
    wire = strip_meal_wire_fields(expanded)
    assert _count_image_parts(wire) == 1
    assert att.id in _inventory_blob(wire)


# ---------------------------------------------------------------------------
# Fallback paths
# ---------------------------------------------------------------------------


def test_write_atoms_without_enabled_uses_legacy_glass(paths, media, mem_store):
    """write_atoms alone does not enable memory meal (KD3 gradual dogfood)."""
    att = _put_png(media)
    content = "still glass"
    msg = append_message(
        "user",
        content,
        attachments=[att.to_dict()],
        paths=paths,
    )
    wake_id = msg.id
    glass = list_messages(paths=paths)
    # Atoms may exist (write path), but meal stays glass when enabled=false.
    promote_wake_observation(
        mem_store,
        "m-write-only",
        content=content,
        message_id=wake_id,
        media_ids=[att.id],
        settings=MemorySettings(write_atoms=True, enabled=False),
    )
    legacy = _legacy_expand(
        glass=glass,
        wake_message_id=wake_id,
        wake_content=content,
        media_store=media,
    )
    assert _count_image_parts(legacy) == 1
    # Worker gate: meal inactive when enabled false.
    client = StubChatClient()
    stop = __import__("threading").Event()
    settings = replace(
        default_settings(),
        memory=MemorySettings(write_atoms=True, enabled=False, backend="jsonl"),
    )
    worker = PresenceWorker(
        paths=paths, client=client, stop_event=stop, settings=settings
    )
    assert worker._ensure_memory_store() is not None
    assert worker._memory_meal_active() is False


def test_store_down_meal_inactive(paths):
    client = StubChatClient()
    stop = __import__("threading").Event()
    settings = replace(
        default_settings(),
        memory=MemorySettings(write_atoms=True, enabled=True, backend="jsonl"),
    )
    worker = PresenceWorker(
        paths=paths, client=client, stop_event=stop, settings=settings
    )
    # Simulate open failure / store down.
    worker._memory = None
    worker._memory_open_failed = True
    worker._memory_open_attempted = True
    assert worker._memory_meal_active() is False


def test_store_unhealthy_meal_inactive(paths, mem_store):
    client = StubChatClient()
    stop = __import__("threading").Event()
    settings = replace(
        default_settings(),
        memory=MemorySettings(write_atoms=True, enabled=True, backend="jsonl"),
    )
    worker = PresenceWorker(
        paths=paths, client=client, stop_event=stop, settings=settings
    )
    bad = MagicMock()
    bad.health.return_value = {"ok": False, "error": "corrupt"}
    worker._memory = bad
    worker._memory_open_attempted = True
    assert worker._memory_meal_active() is False


def test_memory_meal_active_when_enabled_and_healthy(paths):
    client = StubChatClient()
    stop = __import__("threading").Event()
    settings = replace(
        default_settings(),
        memory=MemorySettings(write_atoms=True, enabled=True, backend="jsonl"),
    )
    worker = PresenceWorker(
        paths=paths, client=client, stop_event=stop, settings=settings
    )
    assert worker._memory_meal_active() is True
    snap = worker.status_snapshot()
    assert "memory" in snap
    assert snap["memory"]["enabled"] is True
    assert snap["memory"]["ok"] is True
    assert snap["memory"]["store_open"] is True


def test_memory_status_block_default_flags(paths):
    client = StubChatClient()
    stop = __import__("threading").Event()
    worker = PresenceWorker(
        paths=paths,
        client=client,
        stop_event=stop,
        settings=default_settings(),
    )
    snap = worker.status_snapshot()
    assert snap["memory"]["enabled"] is True
    assert snap["memory"]["write_atoms"] is True
    # Store opens lazily on worker run / first write path, not on snapshot alone.
    assert snap["memory"]["store_open"] is False
    assert snap["memory"]["ok"] is False
    # PR-E: ladder knobs always present for dogfood observability.
    ladder = snap["memory"]["ladder"]
    assert ladder["enabled"] is True
    assert ladder["summary_mode"] in ("template", "llm")
    assert "ladder_hourly_max_ms" in ladder
    assert "llm_calls_hour" in ladder


def test_compose_outer_no_full_glass_history(paths, media, mem_store):
    """Memory meal excludes unrelated glass sliding rows."""
    # Older glass chatter that must not appear when memory meal is on.
    append_message("user", "OLD_GLASS_ROW_SHOULD_NOT_APPEAR", paths=paths)
    append_message("assistant", "OLD_ASSISTANT_REPLY", paths=paths)
    att = _put_png(media)
    msg = append_message(
        "user",
        "fresh",
        attachments=[att.to_dict()],
        paths=paths,
    )
    wake_id = msg.id
    promote_wake_observation(
        mem_store,
        "m-no-glass",
        content="fresh",
        message_id=wake_id,
        media_ids=[att.id],
        settings=MemorySettings(write_atoms=True),
    )
    meal = compose_outer_messages(
        mem_store,
        open_moment_id="m-no-glass",
        budget_tokens=24_000,
        system_text=SYSTEM,
        orient_text="orient-body",
        settings=MemorySettings(enabled=True, write_atoms=True),
    )
    blob = "\n".join(str(m.get("content") or "") for m in meal)
    assert "OLD_GLASS_ROW_SHOULD_NOT_APPEAR" not in blob
    assert "OLD_ASSISTANT_REPLY" not in blob
    # Temporal labeled block present.
    assert any(
        isinstance(m.get("content"), str) and "[context:temporal" in m["content"]
        for m in meal
    )


def test_meal_item_stamps_media_and_wake_id(mem_store):
    from elyra.memory.meal import compose_meal

    promote_wake_observation(
        mem_store,
        "m-stamp",
        content="hi",
        message_id="msg-stamp",
        media_ids=["att_xyz"],
        settings=MemorySettings(write_atoms=True),
    )
    pkg = compose_meal(
        mem_store,
        open_moment_id="m-stamp",
        budget_tokens=24_000,
        system_text="s",
        orient_text="o",
        settings=MemorySettings(enabled=True),
    )
    temporal = [i for i in pkg.items if i.channel == "temporal"]
    assert temporal
    msg = meal_item_to_message(temporal[-1])
    assert msg.get("id") == "msg-stamp"
    assert "att_xyz" in (msg.get("_memory_media_ids") or [])


def test_memory_path_viewing_expand_parity(paths, media, mem_store):
    """Memory meal path forwards viewing_att_ids → image_url on carrier."""
    from elyra.media.viewing import VIEWING_CARRIER_ID

    view_att = _put_png(media)
    # No wake image — viewing alone.
    open_mid = "moment-view"
    meal = compose_outer_messages(
        mem_store,
        open_moment_id=open_mid,
        budget_tokens=24_000,
        system_text=SYSTEM,
        orient_text="orient-body",
        settings=MemorySettings(enabled=True, write_atoms=True),
    )
    expanded = expand_memory_meal_for_provider(
        meal,
        glass_by_id={},
        wake_message_id=None,
        viewing_att_ids=[view_att.id],
        media_store=media,
        provider="xai",
    )
    assert _count_image_parts(expanded) == 1
    # Carrier present before strip; check expanded (pre-strip).
    carrier = next(
        (m for m in expanded if m.get("id") == VIEWING_CARRIER_ID), None
    )
    assert carrier is not None
    assert isinstance(carrier["content"], list)
    imgs = [p for p in carrier["content"] if p.get("type") == "image_url"]
    assert len(imgs) == 1

    wire = strip_meal_wire_fields(expanded)
    assert _count_image_parts(wire) == 1
    for m in wire:
        assert "id" not in m


def test_memory_and_legacy_viewing_shared_budget(paths, media, mem_store):
    """Both meal paths share the same 4-image wake∪viewing budget behaviour."""
    from elyra.media.prompt import MAX_VISION_IMAGES, expand_meal_for_provider
    from elyra.media.prompt import index_glass as idx
    from elyra.loop.context import assemble_outer_meal

    wake_atts = [
        media.put_bytes(
            FIXTURE_PNG.read_bytes(),
            filename=f"w{i}.png",
            origin="user_upload",
        )
        for i in range(3)
    ]
    view_atts = [
        media.put_bytes(
            FIXTURE_PNG.read_bytes(),
            filename=f"v{i}.png",
            origin="user_upload",
        )
        for i in range(3)
    ]
    content = "multi"
    msg = append_message(
        "user",
        content,
        attachments=[a.to_dict() for a in wake_atts],
        paths=paths,
    )
    glass = list_messages(paths=paths)
    view_ids = [a.id for a in view_atts]

    legacy_meal = assemble_outer_meal(
        glass_history=glass,
        system_text=SYSTEM,
        orient_template=(
            "orient {{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}"
            "{{GOALS}}{{SKILL_CATALOG}}{{SKILL_BIAS}}"
        ),
        wake_message_id=msg.id,
        wake_content=content,
        retain_ids=True,
        sliding_input_tokens=24_000,
    )
    legacy = expand_meal_for_provider(
        legacy_meal,
        glass_by_id=idx(glass),
        wake_message_id=msg.id,
        viewing_att_ids=view_ids,
        media_store=media,
        provider="xai",
    )

    promote_wake_observation(
        mem_store,
        "m-budget",
        content=content,
        message_id=msg.id,
        media_ids=[a.id for a in wake_atts],
        settings=MemorySettings(write_atoms=True),
    )
    mem_meal = compose_outer_messages(
        mem_store,
        open_moment_id="m-budget",
        budget_tokens=24_000,
        system_text=SYSTEM,
        orient_text="orient-body",
        settings=MemorySettings(enabled=True, write_atoms=True),
    )
    memory = expand_memory_meal_for_provider(
        mem_meal,
        glass_by_id=idx(glass),
        wake_message_id=msg.id,
        viewing_att_ids=view_ids,
        media_store=media,
        provider="xai",
    )
    assert _count_image_parts(legacy) == MAX_VISION_IMAGES
    assert _count_image_parts(memory) == MAX_VISION_IMAGES
