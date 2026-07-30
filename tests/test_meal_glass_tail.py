"""S1 glass-tail band: roles, tip floor, OQ6, hybrid skip, budget v4."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.loop.context import assemble_outer_meal
from elyra.media import MediaStore
from elyra.media.prompt import index_glass, strip_meal_wire_fields
from elyra.memory.config import MemorySettings
from elyra.memory.meal import (
    GLASS_TAIL_CHANNEL,
    compose_meal,
    compose_outer_messages,
    expand_memory_meal_for_provider,
    meal_item_to_message,
    select_glass_tail,
)
from elyra.memory.store import open_memory_store
from elyra.memory.tokens import (
    split_memory_budget_v3,
    split_memory_budget_v4,
)
from elyra.memory.types import Atom, new_atom_id


FIXTURE_PNG = Path(__file__).parent / "fixtures" / "media" / "1x1.png"


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    s = open_memory_store(paths, MemorySettings(write_atoms=True, backend="jsonl"))
    yield s
    s.close()


def _atom(
    *,
    t: str,
    kind: str = "observation",
    text: str = "body",
    moment_id: str | None = "m_open",
    atom_id: str | None = None,
    media_ids: tuple[str, ...] = (),
    meta: dict | None = None,
    **kwargs,
) -> Atom:
    return Atom(
        atom_id=atom_id or new_atom_id(),
        t_start=t,
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=moment_id,
        media_ids=media_ids,
        meta=meta or {},
        **kwargs,
    )


def _glass(
    *,
    mid: str,
    role: str,
    content: str,
    attachments: list | None = None,
    created_at: str | None = None,
) -> dict:
    row: dict = {
        "id": mid,
        "role": role,
        "content": content,
        "created_at": created_at or "2026-07-30T08:00:00Z",
    }
    if attachments is not None:
        row["attachments"] = attachments
    return row


# Rockets-class glass tip (evidence/sa9b-e6d460f2 shape).
ROCKETS_GLASS = [
    _glass(
        mid="436f4ca1-time",
        role="assistant",
        content="It's **Thursday 30 July 2026, 18:46 AEST** (08:46 UTC).",
        created_at="2026-07-30T08:46:49Z",
    ),
    _glass(
        mid="04f85fc6-rockets",
        role="user",
        content="what is the coolest thing you remember about rockets?",
        created_at="2026-07-30T08:47:45Z",
    ),
    _glass(
        mid="37ec1721-fail",
        role="assistant",
        content="Not much else hanging — last open threads were the philosophy pack.",
        created_at="2026-07-30T08:48:02Z",
    ),
]


# ---------------------------------------------------------------------------
# Named S1 acceptance tests
# ---------------------------------------------------------------------------


def test_meal_glass_tail_wait_reply_includes_user_and_prior_assistant(store):
    """P2 rockets-class: tip has user question + prior assistant with true roles."""
    open_id = "m_wait"
    store.put_atom(
        _atom(
            t="2026-07-30T08:48:00Z",
            kind="observation",
            text="what is the coolest thing you remember about rockets?",
            moment_id=open_id,
            meta={"wake_message_id": "04f85fc6-rockets"},
        )
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text="ORIENT",
        now=datetime(2026, 7, 30, 8, 48, tzinfo=UTC),
        glass_rows=ROCKETS_GLASS,
        social_wake=True,
    )
    tail = [i for i in pkg.items if i.channel == GLASS_TAIL_CHANNEL]
    assert tail, "expected glass_tail items"
    bodies = [(i.role, i.content) for i in tail]
    assert any(
        r == "user" and "rockets" in c for r, c in bodies
    ), f"missing user rockets question in {bodies}"
    assert any(
        r == "assistant" and ("Thursday" in c or "18:46" in c) for r, c in bodies
    ), f"missing prior assistant time speak in {bodies}"
    assert GLASS_TAIL_CHANNEL in pkg.channels_present


def test_meal_glass_tail_user_message_includes_triggering_user(store):
    """P1: tip includes the triggering user glass row."""
    open_id = "m_user"
    wake_id = "user-wake-1"
    glass = [
        _glass(mid="a1", role="assistant", content="prior reply"),
        _glass(mid=wake_id, role="user", content="hello from user_message path"),
    ]
    store.put_atom(
        _atom(
            t="2026-07-30T09:00:00Z",
            kind="observation",
            text="hello from user_message path",
            moment_id=open_id,
            meta={"wake_message_id": wake_id},
        )
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        glass_rows=glass,
        social_wake=True,
    )
    tail = [i for i in pkg.items if i.channel == GLASS_TAIL_CHANNEL]
    assert any(
        i.role == "user" and "hello from user_message path" in i.content for i in tail
    )


def test_meal_glass_tail_roles_preserved(store):
    """True user/assistant roles on glass-tail (not host role=user collapse)."""
    glass = [
        _glass(mid="g1", role="user", content="user says hi"),
        _glass(mid="g2", role="assistant", content="assistant answers"),
    ]
    pkg = compose_meal(
        store,
        open_moment_id=None,
        budget_tokens=20_000,
        glass_rows=glass,
        social_wake=True,
    )
    tail = [i for i in pkg.items if i.channel == GLASS_TAIL_CHANNEL]
    assert len(tail) >= 2
    roles = {i.role for i in tail}
    assert "user" in roles
    assert "assistant" in roles
    msgs = [meal_item_to_message(i) for i in tail]
    assert any(m["role"] == "user" and "user says hi" in m["content"] for m in msgs)
    assert any(
        m["role"] == "assistant" and "assistant answers" in m["content"] for m in msgs
    )


def test_meal_tip_floor_under_epi_pressure(store):
    """Under high epi mass + social_wake, packed glass-tail ≥ min(4, available)."""
    now = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)
    open_id = "m_epi_pressure"
    # Flood prior moments with long speak text so epi wants residual.
    for i in range(8):
        store.put_atom(
            _atom(
                t=f"2026-07-30T10:{i:02d}:00Z",
                kind="speak",
                text=("EPI MASS " + ("fabric philosophy research " * 40)),
                moment_id=f"m_prior_{i}",
            )
        )
    store.put_atom(
        _atom(
            t="2026-07-30T12:00:00Z",
            kind="observation",
            text="wake obs",
            moment_id=open_id,
            meta={"wake_message_id": "g-user-3"},
        )
    )
    glass = [
        _glass(mid=f"g-a-{i}", role="assistant", content=f"asst turn {i}")
        for i in range(2)
    ] + [
        _glass(mid=f"g-user-{i}", role="user", content=f"user turn {i}")
        for i in range(4)
    ]
    # Alternate into chronological tip of 6 messages.
    glass = [
        _glass(mid="g0", role="user", content="u0 short"),
        _glass(mid="g1", role="assistant", content="a0 short"),
        _glass(mid="g2", role="user", content="u1 short"),
        _glass(mid="g3", role="assistant", content="a1 short"),
        _glass(mid="g4", role="user", content="u2 short"),
        _glass(mid="g5", role="assistant", content="a2 short"),
    ]
    # Soft glass share would be tiny under default if residual were huge epi;
    # floor raise steals from supports so ≥4 messages pack.
    cfg = MemorySettings(
        episodic_fraction=0.40,
        glass_tail_fraction=0.01,  # soft % alone tiny
        glass_tail_floor_messages=4,
        glass_tail_max_messages=16,
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=8_000,
        system_text="S" * 100,
        orient_text="O" * 100,
        now=now,
        settings=cfg,
        glass_rows=glass,
        social_wake=True,
    )
    tail = [i for i in pkg.items if i.channel == GLASS_TAIL_CHANNEL]
    assert len(tail) >= 4, (
        f"tip floor failed: packed={len(tail)} meta={pkg.glass_tail_meta}"
    )


def test_meal_glass_tail_order_before_temporal_orient(store):
    """IK1: glass_tail before temporal before orient."""
    open_id = "m_order"
    store.put_atom(
        _atom(
            t="2026-07-30T12:00:00Z",
            kind="observation",
            text="open now",
            moment_id=open_id,
        )
    )
    glass = [
        _glass(mid="gx", role="user", content="tip user"),
        _glass(mid="gy", role="assistant", content="tip asst"),
    ]
    msgs = compose_outer_messages(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYSTEM",
        orient_text="ORIENT",
        glass_rows=glass,
        social_wake=True,
    )
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["content"] == "ORIENT"
    mid = msgs[1:-1]
    gt_idx = next(
        (i for i, m in enumerate(mid) if "glass-tail" in (m.get("content") or "")),
        None,
    )
    temp_idx = next(
        (i for i, m in enumerate(mid) if "temporal/" in (m.get("content") or "")),
        None,
    )
    assert gt_idx is not None
    assert temp_idx is not None
    assert gt_idx < temp_idx


def test_meal_glass_tail_cap_not_unbounded(store):
    """Hard max_messages cap — no unbounded dump."""
    glass = [
        _glass(mid=f"id-{i}", role="user" if i % 2 == 0 else "assistant", content=f"m{i}")
        for i in range(40)
    ]
    items, meta = select_glass_tail(
        glass,
        cap_tokens=100_000,
        floor_messages=4,
        max_messages=6,
        social_wake=True,
    )
    assert len(items) <= 6
    assert meta["window"] == 6
    assert meta["packed"] == 6


def test_meal_glass_tail_wake_message_id_stamped(store):
    """wake_message_id stamped → msg id for hybrid skip."""
    glass = [
        _glass(mid="wake-99", role="user", content="trigger text"),
        _glass(mid="asst-1", role="assistant", content="reply"),
    ]
    items, _meta = select_glass_tail(
        glass, cap_tokens=10_000, social_wake=True, floor_messages=2
    )
    assert items
    stamped = [i for i in items if (i.meta or {}).get("wake_message_id")]
    assert stamped
    for item in stamped:
        msg = meal_item_to_message(item)
        assert msg.get("id") == item.meta["wake_message_id"]


def test_glass_tail_media_only_wake_expand(paths, store):
    """Media-only KD19: true user role tail row; media expands; no triple text."""
    media = MediaStore(paths)
    att = media.put_bytes(
        FIXTURE_PNG.read_bytes(), filename="only.png", origin="user_upload"
    )
    wake_id = "media-only-wake"
    glass = [
        _glass(mid="prior-a", role="assistant", content="ready"),
        _glass(
            mid=wake_id,
            role="user",
            content="",
            attachments=[att.to_dict()],
        ),
    ]
    open_id = "m_media"
    store.put_atom(
        _atom(
            t="2026-07-30T12:00:00Z",
            kind="observation",
            text="",
            moment_id=open_id,
            media_ids=(att.id,),
            meta={"wake_message_id": wake_id},
        )
    )
    meal = compose_outer_messages(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text="orient",
        glass_rows=glass,
        social_wake=True,
    )
    # Tail user row present with id; temporal should not also stamp same id.
    id_hits = [m for m in meal if m.get("id") == wake_id]
    assert len(id_hits) == 1, f"expected single wake id row, got {len(id_hits)}"
    assert id_hits[0]["role"] == "user"

    expanded = expand_memory_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id=wake_id,
        media_store=media,
        provider="xai",
    )
    wire = strip_meal_wire_fields(expanded)
    # Hybrid must not inject a second text copy of the wake.
    text_blobs = []
    image_parts = 0
    for m in wire:
        c = m.get("content")
        if isinstance(c, str):
            text_blobs.append(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    image_parts += 1
                if isinstance(part, dict) and part.get("type") == "text":
                    text_blobs.append(str(part.get("text") or ""))
    assert image_parts >= 1 or any("[attachments]" in t for t in text_blobs)
    # No triple: at most one meal row carries the wake id after expand.
    assert sum(1 for m in expanded if m.get("id") == wake_id) == 1


def test_legacy_memory_off_sliding_glass_unchanged():
    """Legacy assemble_outer_meal still slides glass with true roles (regression)."""
    history = [
        {"role": "user", "content": "hello", "id": "m1"},
        {"role": "assistant", "content": "hi there", "id": "m2"},
    ]
    meal = assemble_outer_meal(
        glass_history=history,
        system_text="SYS",
        orient_template="O {{NOW}}{{SELF}}{{USER}}{{WHY_NOW}}"
        "{{GOALS}}{{SKILL_CATALOG}}{{SKILL_BIAS}}",
        sliding_input_tokens=24_000,
    )
    assert meal[1] == {"role": "user", "content": "hello"}
    assert meal[2] == {"role": "assistant", "content": "hi there"}


def test_split_memory_budget_v4_inactive_matches_v3():
    """glass_tail_active=False → bit-identical to v3 (golden parity)."""
    cases = [
        dict(
            budget_tokens=10_000,
            system_text="sys",
            orient_text="orient",
            semantic_enabled=False,
            directed_keep_active=False,
            episodic_fraction=0.20,
        ),
        dict(
            budget_tokens=10_000,
            system_text="sys",
            orient_text="orient",
            semantic_enabled=True,
            directed_keep_active=True,
            semantic_fraction=0.12,
            directed_keep_fraction=0.08,
            episodic_fraction_with_semantic=0.18,
            temporal_min_fraction=0.55,
        ),
        dict(
            budget_tokens=1000,
            semantic_enabled=True,
            directed_keep_active=True,
            semantic_fraction=0.30,
            directed_keep_fraction=0.20,
            episodic_fraction_with_semantic=0.30,
            temporal_min_fraction=0.55,
        ),
        dict(budget_tokens=0, semantic_enabled=True, directed_keep_active=True),
    ]
    for kwargs in cases:
        f3, s3, d3, e3, t3 = split_memory_budget_v3(**kwargs)
        f4, s4, d4, e4, g4, t4 = split_memory_budget_v4(
            glass_tail_active=False, **kwargs
        )
        assert (f4, s4, d4, e4, g4, t4) == (f3, s3, d3, e3, 0, t3)


def test_split_memory_budget_v4_active_identity():
    """Five residual caps sum to R; soft glass ≈ int(R * fraction) before clamp."""
    R = 10_000
    # Fractions 0.10+0.08+0.15+0.08 = 0.41 → temp 0.59 ≥ floor 0.55 (no cut).
    fixed, sem, dk, epi, gt, temp = split_memory_budget_v4(
        R,
        system_text="",
        orient_text="",
        semantic_enabled=True,
        directed_keep_active=True,
        glass_tail_active=True,
        glass_tail_fraction=0.08,
        semantic_fraction=0.10,
        directed_keep_fraction=0.08,
        episodic_fraction_with_semantic=0.15,
        temporal_min_fraction=0.55,
    )
    assert fixed == 0
    assert sem + dk + epi + gt + temp == R
    assert gt == int(R * 0.08)
    assert sem == int(R * 0.10)
    assert dk == int(R * 0.08)
    assert epi == int(R * 0.15)
    assert temp >= int(R * 0.55)
    assert temp == R - sem - dk - epi - gt


def test_split_memory_budget_v4_temporal_pressure_cuts_glass_soft():
    """Temporal floor cuts sem → dk → epi → glass_soft."""
    # s0.20 + d0.15 + e0.20 + g0.15 = 0.70 → temp 0.30 < floor 0.55; deficit 250
    # cut s 200→0 (take 200), still need 50 from d (150→100), e/g untouched? deficit 250
    # s=200 take 200 → s=0 deficit 50; d take 50 → d=100; e=200; g=150; t=550
    fixed, sem, dk, epi, gt, temp = split_memory_budget_v4(
        1000,
        semantic_enabled=True,
        directed_keep_active=True,
        glass_tail_active=True,
        semantic_fraction=0.20,
        directed_keep_fraction=0.15,
        episodic_fraction_with_semantic=0.20,
        glass_tail_fraction=0.15,
        temporal_min_fraction=0.55,
    )
    assert fixed == 0
    assert sem + dk + epi + gt + temp == 1000
    assert temp >= 550
    assert sem == 0
    assert dk == 100
    assert epi == 200
    assert gt == 150
    assert temp == 550


def test_oq6_temporal_suppress_when_wake_on_glass_tail(store):
    """Temporal drops atoms whose wake_message_id is already on glass-tail."""
    open_id = "m_oq6"
    wake_id = "wake-oq6"
    glass = [
        _glass(mid="prior", role="assistant", content="asst prior"),
        _glass(mid=wake_id, role="user", content="user wake text"),
    ]
    store.put_atom(
        _atom(
            t="2026-07-30T12:00:00Z",
            kind="observation",
            text="user wake text",
            moment_id=open_id,
            meta={"wake_message_id": wake_id},
        )
    )
    store.put_atom(
        _atom(
            t="2026-07-30T12:00:01Z",
            kind="speak",
            text="model line stays",
            moment_id=open_id,
        )
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        glass_rows=glass,
        social_wake=True,
    )
    # Glass-tail carries wake id.
    assert any(
        (i.meta or {}).get("wake_message_id") == wake_id
        for i in pkg.items
        if i.channel == GLASS_TAIL_CHANNEL
    )
    # Temporal host block should not stamp the same wake id.
    temp = [i for i in pkg.items if i.channel == "temporal"]
    for item in temp:
        assert (item.meta or {}).get("wake_message_id") != wake_id
    # Speak line still present in temporal content.
    temp_blob = "\n".join(i.content for i in temp)
    assert "model line stays" in temp_blob


def test_hybrid_skips_when_message_id_on_glass_tail(store):
    """Hybrid inject skipped when wake id already on glass-tail meal messages."""
    wake_id = "hybrid-skip-wake"
    glass = [
        _glass(mid=wake_id, role="user", content="already on tail"),
    ]
    meal = compose_outer_messages(
        store,
        open_moment_id=None,
        budget_tokens=20_000,
        system_text="SYS",
        orient_text="orient",
        glass_rows=glass,
        social_wake=True,
    )
    assert any(m.get("id") == wake_id for m in meal)
    before = len(meal)
    expanded = expand_memory_meal_for_provider(
        meal,
        glass_by_id=index_glass(glass),
        wake_message_id=wake_id,
        media_store=None,
        provider="xai",
    )
    # No extra hybrid row with same id.
    assert sum(1 for m in expanded if m.get("id") == wake_id) == 1
    # Message count unchanged aside from expand content mutations.
    assert len(expanded) == before


def test_inspect_surfaces_glass_tail_meta(store):
    from elyra.memory.inspect import meal_package_to_inspect

    glass = [
        _glass(mid="i1", role="user", content="inspect me"),
        _glass(mid="i2", role="assistant", content="ok"),
    ]
    pkg = compose_meal(
        store,
        open_moment_id=None,
        budget_tokens=20_000,
        glass_rows=glass,
        social_wake=True,
    )
    snap = meal_package_to_inspect(pkg, system_text="S", orient_text="O")
    assert "glass_tail" in snap["channels_present"] or GLASS_TAIL_CHANNEL in (
        snap.get("channels") or {}
    )
    assert snap.get("glass_tail_meta") is not None
    assert snap["glass_tail_meta"].get("packed", 0) >= 1
