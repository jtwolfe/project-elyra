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
    """Temporal floor cuts sem → dk first (partial) under moderate pressure."""
    # s0.20 + d0.15 + e0.20 + g0.15 = 0.70 → temp 0.30 < floor 0.55; deficit 250
    # cut s 200→0 (take 200), still need 50 from d (150→100), e/g untouched
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


def test_split_memory_budget_v4_floor_cut_order_sem_dk_epi_gt():
    """v4 floor cut order is semantic → directed_keep → episodic → glass_tail.

    Large soft sum forces the clamp through every support band so the order
    is observable (not only sem/dk under moderate pressure).
    """
    R = 1000
    # s0.15 + d0.15 + e0.15 + g0.50 = 0.95 → temp 0.05 < floor 0.55; deficit 500
    # take s 150→0, d 150→0, e 150→0, g 500→450; temp=550
    fixed, sem, dk, epi, gt, temp = split_memory_budget_v4(
        R,
        semantic_enabled=True,
        directed_keep_active=True,
        glass_tail_active=True,
        semantic_fraction=0.15,
        directed_keep_fraction=0.15,
        episodic_fraction_with_semantic=0.15,
        glass_tail_fraction=0.50,
        temporal_min_fraction=0.55,
    )
    assert fixed == 0
    assert sem + dk + epi + gt + temp == R
    assert sem == 0, "semantic cut first"
    assert dk == 0, "directed_keep cut second"
    assert epi == 0, "episodic cut third"
    assert gt == 450, "glass_tail_soft cut last (partial)"
    assert temp == 550

    # Intermediate: stop mid-episodic (sem+dk fully gone, gt untouched).
    # s0.20 + d0.20 + e0.25 + g0.25 = 0.90 → temp 0.10; deficit 450
    # take s 200→0, d 200→0, e 250→200 (take 50), g=250; temp=550
    _f, sem2, dk2, epi2, gt2, temp2 = split_memory_budget_v4(
        R,
        semantic_enabled=True,
        directed_keep_active=True,
        glass_tail_active=True,
        semantic_fraction=0.20,
        directed_keep_fraction=0.20,
        episodic_fraction_with_semantic=0.25,
        glass_tail_fraction=0.25,
        temporal_min_fraction=0.55,
    )
    assert sem2 == 0
    assert dk2 == 0
    assert epi2 == 200
    assert gt2 == 250
    assert temp2 == 550
    assert sem2 + dk2 + epi2 + gt2 + temp2 == R


def test_split_memory_budget_v4_product_defaults_kd_v8():
    """KD-V8 product defaults: gt 0.10, epi 0.24 / with-sem 0.22; floor 0.55.

    Meal fraction stays 0.5 (DEFAULT_MEAL_BUDGET_TOKENS path); only residual
    share knobs change.
    """
    R = 100_000
    # semantic off, glass on: soft = epi 0.24 + gt 0.10 = 0.34 → temp 0.66 ≥ 0.55
    fixed, sem, dk, epi, gt, temp = split_memory_budget_v4(
        R,
        system_text="",
        orient_text="",
        semantic_enabled=False,
        directed_keep_active=False,
        glass_tail_active=True,
    )
    assert fixed == 0
    assert sem == 0 and dk == 0
    assert epi == int(R * 0.24)
    assert gt == int(R * 0.10)
    assert temp == R - epi - gt
    assert temp >= int(R * 0.55)

    # semantic + dk + glass on: 0.12+0.08+0.22+0.10 = 0.52 → temp 0.48 < 0.55
    # deficit = 7000; cut semantic first (12000→5000); dk/epi/gt untouched.
    fixed2, sem2, dk2, epi2, gt2, temp2 = split_memory_budget_v4(
        R,
        system_text="",
        orient_text="",
        semantic_enabled=True,
        directed_keep_active=True,
        glass_tail_active=True,
    )
    assert fixed2 == 0
    assert sem2 + dk2 + epi2 + gt2 + temp2 == R
    assert sem2 == 5_000
    assert dk2 == int(R * 0.08)
    assert epi2 == int(R * 0.22)
    assert gt2 == int(R * 0.10)
    assert temp2 == int(R * 0.55)


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


# ---------------------------------------------------------------------------
# PR4 / #127 — conversation-scoped glass_tail + speaker labels (KD4/KD5/KD6)
# ---------------------------------------------------------------------------


def _glass_conv(
    *,
    mid: str,
    role: str,
    content: str,
    user_id: str | None = None,
    conversation_id: str | None = None,
    created_at: str | None = None,
) -> dict:
    row = _glass(mid=mid, role=role, content=content, created_at=created_at)
    if user_id is not None:
        row["user_id"] = user_id
    if conversation_id is not None:
        row["conversation_id"] = conversation_id
    return row


def test_glass_tail_multi_dm_isolation():
    """Two interleaved DMs → select for dm:jim never includes sam rows."""
    glass = [
        _glass_conv(
            mid="j1",
            role="user",
            content="jim hello",
            user_id="jim",
            conversation_id="dm:jim",
            created_at="2026-07-30T08:00:00Z",
        ),
        _glass_conv(
            mid="s1",
            role="user",
            content="sam hello",
            user_id="sam",
            conversation_id="dm:sam",
            created_at="2026-07-30T08:01:00Z",
        ),
        _glass_conv(
            mid="j2",
            role="assistant",
            content="hi jim",
            user_id="jim",
            conversation_id="dm:jim",
            created_at="2026-07-30T08:02:00Z",
        ),
        _glass_conv(
            mid="s2",
            role="assistant",
            content="hi sam",
            user_id="sam",
            conversation_id="dm:sam",
            created_at="2026-07-30T08:03:00Z",
        ),
        _glass_conv(
            mid="j3",
            role="user",
            content="jim again",
            user_id="jim",
            conversation_id="dm:jim",
            created_at="2026-07-30T08:04:00Z",
        ),
    ]
    items, meta = select_glass_tail(
        glass,
        cap_tokens=100_000,
        floor_messages=2,
        max_messages=20,
        social_wake=True,
        conversation_id="dm:jim",
    )
    assert items, "expected jim glass_tail items"
    bodies = [i.content for i in items]
    assert not any("sam hello" in b or "hi sam" in b for b in bodies)
    assert any("jim hello" in b for b in bodies)
    assert any("jim again" in b for b in bodies)
    assert any("hi jim" in b for b in bodies)
    assert meta["conversation_id"] == "dm:jim"
    assert meta["available"] == 3  # j1, j2, j3
    assert meta["packed"] == 3


def test_glass_tail_group_excludes_dm_and_no_legacy_fill():
    """Group select only group rows; legacy null-cid user_id rows NOT included."""
    glass = [
        _glass_conv(
            mid="dm1",
            role="user",
            content="private jim",
            user_id="jim",
            conversation_id="dm:jim",
        ),
        _glass_conv(
            mid="g1",
            role="user",
            content="group jim says",
            user_id="jim",
            conversation_id="group:room1",
        ),
        _glass_conv(
            mid="g2",
            role="user",
            content="group sam says",
            user_id="sam",
            conversation_id="group:room1",
        ),
        # Legacy pre-cutover: null conversation_id + user_id jim — DM fill only
        {
            "id": "leg1",
            "role": "user",
            "content": "legacy jim no cid",
            "user_id": "jim",
            "created_at": "2026-07-30T08:00:00Z",
        },
    ]
    items, meta = select_glass_tail(
        glass,
        cap_tokens=100_000,
        social_wake=True,
        conversation_id="group:room1",
        floor_messages=2,
        max_messages=20,
    )
    bodies = [i.content for i in items]
    assert any("group jim says" in b for b in bodies)
    assert any("group sam says" in b for b in bodies)
    assert not any("private jim" in b for b in bodies)
    assert not any("legacy jim" in b for b in bodies)
    assert meta["available"] == 2
    assert meta["conversation_id"] == "group:room1"


def test_glass_tail_legacy_dm_fill_only():
    """Legacy null conversation_id + user_id jim → included in dm:jim; not groups."""
    glass = [
        {
            "id": "leg-u",
            "role": "user",
            "content": "legacy user jim",
            "user_id": "jim",
            "created_at": "2026-07-30T08:00:00Z",
        },
        {
            "id": "leg-a",
            "role": "assistant",
            "content": "legacy asst to jim",
            "user_id": "jim",
            "created_at": "2026-07-30T08:01:00Z",
        },
        {
            "id": "leg-sam",
            "role": "user",
            "content": "legacy sam",
            "user_id": "sam",
            "created_at": "2026-07-30T08:02:00Z",
        },
        _glass_conv(
            mid="g1",
            role="user",
            content="in group",
            user_id="jim",
            conversation_id="group:x",
        ),
    ]
    jim_items, jim_meta = select_glass_tail(
        glass,
        cap_tokens=100_000,
        social_wake=True,
        conversation_id="dm:jim",
        floor_messages=1,
        max_messages=20,
    )
    bodies = [i.content for i in jim_items]
    assert any("legacy user jim" in b for b in bodies)
    assert any("legacy asst to jim" in b for b in bodies)
    assert not any("legacy sam" in b for b in bodies)
    assert not any("in group" in b for b in bodies)
    assert jim_meta["available"] == 2

    grp_items, grp_meta = select_glass_tail(
        glass,
        cap_tokens=100_000,
        social_wake=True,
        conversation_id="group:x",
        floor_messages=1,
        max_messages=20,
    )
    grp_bodies = [i.content for i in grp_items]
    assert any("in group" in b for b in grp_bodies)
    assert not any("legacy" in b for b in grp_bodies)
    assert grp_meta["available"] == 1


def test_glass_tail_null_conversation_non_social_empty():
    """KD5: null conversation + social_wake false → empty glass_tail."""
    glass = [
        _glass_conv(
            mid="j1",
            role="user",
            content="should not pack",
            user_id="jim",
            conversation_id="dm:jim",
        ),
    ]
    items, meta = select_glass_tail(
        glass,
        cap_tokens=100_000,
        social_wake=False,
        conversation_id=None,
        floor_messages=6,
        max_messages=20,
    )
    assert items == []
    assert meta["packed"] == 0
    assert meta["floor_applied"] is False
    assert meta["last_user_text"] is None
    assert meta["available"] == 0


def test_glass_tail_null_conversation_non_social_compose_empty(store):
    """compose_meal: non-social + null conversation_id → no glass_tail pack."""
    glass = [
        _glass_conv(
            mid="j1",
            role="user",
            content="foreign tip",
            user_id="jim",
            conversation_id="dm:jim",
        ),
        _glass(mid="a1", role="assistant", content="reply"),
    ]
    pkg = compose_meal(
        store,
        open_moment_id=None,
        budget_tokens=20_000,
        glass_rows=glass,
        social_wake=False,
        conversation_id=None,
    )
    tail = [i for i in pkg.items if i.channel == GLASS_TAIL_CHANNEL]
    assert tail == []
    # Zero-state: no soft fill from leftover rows
    assert pkg.glass_tail_meta is None or pkg.glass_tail_meta.get("packed", 0) == 0


def test_glass_tail_speaker_labels_group_and_floor():
    """Group user lines labeled [GoesBy (user_id)]; floor estimate uses labels."""
    glass = [
        _glass_conv(
            mid="g1",
            role="user",
            content="hello room",
            user_id="jim",
            conversation_id="group:room1",
            created_at="2026-07-30T08:00:00Z",
        ),
        _glass_conv(
            mid="g2",
            role="assistant",
            content="hi all",
            user_id=None,
            conversation_id="group:room1",
            created_at="2026-07-30T08:01:00Z",
        ),
        _glass_conv(
            mid="g3",
            role="user",
            content="sam here",
            user_id="sam",
            conversation_id="group:room1",
            created_at="2026-07-30T08:02:00Z",
        ),
    ]
    labels = {"jim": "Jim", "sam": "Sam"}
    items, meta = select_glass_tail(
        glass,
        cap_tokens=100_000,
        social_wake=True,
        conversation_id="group:room1",
        label_users=labels,
        floor_messages=3,
        max_messages=20,
    )
    assert len(items) == 3
    user_items = [i for i in items if i.role == "user"]
    assert any(i.content.startswith("[Jim (jim)] ") for i in user_items)
    assert any(i.content.startswith("[Sam (sam)] ") for i in user_items)
    # Assistant has no user-style prefix
    asst = [i for i in items if i.role == "assistant"]
    assert asst
    assert not asst[0].content.startswith("[")
    # Seed hygiene: last_user_text is raw (no label prefix)
    assert meta["last_user_text"] == "sam here"
    assert not str(meta["last_user_text"]).startswith("[")

    # Floor estimate uses labeled content (tokens ≥ raw)
    from elyra.memory.meal import estimate_glass_tail_floor_tokens

    labeled_cost = estimate_glass_tail_floor_tokens(
        glass,
        floor_messages=3,
        max_messages=20,
        conversation_id="group:room1",
        label_users=labels,
    )
    raw_cost = estimate_glass_tail_floor_tokens(
        glass,
        floor_messages=3,
        max_messages=20,
        conversation_id="group:room1",
        label_users=None,
    )
    assert labeled_cost >= raw_cost
    assert labeled_cost == meta["tokens_used"]


def test_glass_tail_dm_short_label_when_map_provided():
    """DM: short [GoesBy] form when label_users provided; raw when absent."""
    glass = [
        _glass_conv(
            mid="d1",
            role="user",
            content="hey",
            user_id="jim",
            conversation_id="dm:jim",
        ),
    ]
    items_labeled, _ = select_glass_tail(
        glass,
        cap_tokens=10_000,
        social_wake=True,
        conversation_id="dm:jim",
        label_users={"jim": "Jim"},
        floor_messages=1,
    )
    assert items_labeled[0].content == "[Jim] hey"

    items_raw, _ = select_glass_tail(
        glass,
        cap_tokens=10_000,
        social_wake=True,
        conversation_id="dm:jim",
        label_users=None,
        floor_messages=1,
    )
    assert items_raw[0].content == "hey"


def test_list_messages_plus_select_glass_tail_integration(paths):
    """Integration: list_messages filter-then-last-N + select_glass_tail isolation."""
    from elyra.messages import append_message, list_messages

    # Interleave more than limit: jim/sam DM rows; limit must not starve jim.
    limit = 10
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
    # Also a group row that must not bleed into DM select
    append_message(
        "user",
        "group-noise",
        user_id="jim",
        conversation_id="group:noise",
        paths=paths,
    )

    jim_rows = list_messages(limit=limit, conversation_id="dm:jim", paths=paths)
    assert len(jim_rows) == limit
    assert all(
        r.get("conversation_id") == "dm:jim" or r.get("user_id") == "jim"
        for r in jim_rows
    )
    assert not any("sam-" in (r.get("content") or "") for r in jim_rows)
    assert not any("group-noise" in (r.get("content") or "") for r in jim_rows)

    items, meta = select_glass_tail(
        jim_rows,
        cap_tokens=100_000,
        social_wake=True,
        conversation_id="dm:jim",
        floor_messages=4,
        max_messages=20,
    )
    assert items
    assert meta["conversation_id"] == "dm:jim"
    bodies = [i.content for i in items]
    assert not any("sam-" in b for b in bodies)
    assert not any("group-noise" in b for b in bodies)
    assert all("jim-" in b for b in bodies)

    # Zero-state / empty tip: empty disk → empty select
    empty_items, empty_meta = select_glass_tail(
        [],
        cap_tokens=100_000,
        social_wake=True,
        conversation_id="dm:jim",
        floor_messages=4,
    )
    assert empty_items == []
    assert empty_meta["packed"] == 0
    assert empty_meta["available"] == 0


def test_glass_tail_no_soft_global_fill():
    """Strict scope: thin tip is honest; other-conversation rows never fill floor."""
    glass = [
        # Only 1 jim row — sam rows must not soft-fill floor of 6
        _glass_conv(
            mid="j1",
            role="user",
            content="only jim",
            user_id="jim",
            conversation_id="dm:jim",
        ),
    ] + [
        _glass_conv(
            mid=f"s{i}",
            role="user",
            content=f"sam fill {i}",
            user_id="sam",
            conversation_id="dm:sam",
        )
        for i in range(10)
    ]
    items, meta = select_glass_tail(
        glass,
        cap_tokens=100_000,
        social_wake=True,
        conversation_id="dm:jim",
        floor_messages=6,
        max_messages=20,
    )
    assert len(items) == 1
    assert "only jim" in items[0].content
    assert meta["available"] == 1
    assert meta["packed"] == 1
    # Honest thin tip: never soft-fill from other conversations to meet floor
    assert not any("sam fill" in i.content for i in items)
    # Floor target clamped to available (1), so shortfall may be false — still
    # must not invent foreign rows.
    assert meta["window"] == 1
