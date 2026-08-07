"""Path parity hermetic tests for instance continuity (BUG-meal-03 S2).

Minimum exit set: P2 wait_reply tip package, P5 wait bridge tip, P7 restart
mid-wait tail from disk. P4 interject remains chain-only (non-regression).
Sticky keep assertions deferred to S3.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from elyra.config import resolve_paths
from elyra.loop.doloop import _drain_interjections
from elyra.loop.orient_slice import BIAS_TALK, format_skill_bias
from elyra.messages import list_messages
from elyra.memory.config import MemorySettings
from elyra.memory.meal import (
    GLASS_TAIL_CHANNEL,
    compose_meal,
    compose_outer_messages,
    expand_memory_meal_for_provider,
)
from elyra.memory.store import open_memory_store
from elyra.memory.types import Atom, new_atom_id
from elyra.media.prompt import index_glass
from elyra.presence.queue import WakeItem
from elyra.presence.worker import _why_now


# Rockets-class glass tip (evidence/sa9b-e6d460f2 shape).
ROCKETS_USER = "what is the coolest thing you remember about rockets?"
ROCKETS_ASSIST_TIME = "It's **Thursday 30 July 2026, 18:46 AEST** (08:46 UTC)."
ROCKETS_ASSIST_FAIL = (
    "Not much else hanging — last open threads were the philosophy pack."
)
WAIT_ID = "c13ae60a-40ed-45c6-a75a-035c1a78f05c"
WAKE_MSG_ID = "04f85fc6-rockets"


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
    meta: dict | None = None,
) -> Atom:
    return Atom(
        atom_id=new_atom_id(),
        t_start=t,
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=moment_id,
        meta=meta or {},
    )


def _glass(
    *,
    mid: str,
    role: str,
    content: str,
    created_at: str | None = None,
) -> dict:
    return {
        "id": mid,
        "role": role,
        "content": content,
        "created_at": created_at or "2026-07-30T08:00:00Z",
    }


ROCKETS_GLASS = [
    _glass(
        mid="436f4ca1-time",
        role="assistant",
        content=ROCKETS_ASSIST_TIME,
        created_at="2026-07-30T08:46:49Z",
    ),
    _glass(
        mid=WAKE_MSG_ID,
        role="user",
        content=ROCKETS_USER,
        created_at="2026-07-30T08:47:45Z",
    ),
    _glass(
        mid="37ec1721-fail",
        role="assistant",
        content=ROCKETS_ASSIST_FAIL,
        created_at="2026-07-30T08:48:02Z",
    ),
]


def _wait_reply_why_now(content: str = ROCKETS_USER, wait_id: str = WAIT_ID) -> str:
    return _why_now(
        WakeItem(
            id="W-wait",
            kind="wait_reply",
            priority=0,
            created_at="2026-07-30T08:49:00Z",
            payload={
                "wait_id": wait_id,
                "content": content,
                "message_id": WAKE_MSG_ID,
                "user_id": "operator",
            },
        )
    )


def _assert_rockets_tip(pkg) -> list:
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
    return tail


# ---------------------------------------------------------------------------
# P2 — wait_reply tip package (+ why_now snippet dual-write)
# ---------------------------------------------------------------------------


def test_path_p2_wait_reply_tip_package(store):
    """P2: tip has user + prior assistant roles; why_now carries user snippet."""
    open_id = "m_wait_p2"
    store.put_atom(
        _atom(
            t="2026-07-30T08:48:00Z",
            text=ROCKETS_USER,
            moment_id=open_id,
            meta={"wake_message_id": WAKE_MSG_ID},
        )
    )
    why = _wait_reply_why_now()
    # Framing dual-write (orient path).
    assert WAIT_ID in why
    assert "rockets" in why
    assert why.startswith("wait reply (wait_id=")
    # BIAS_TALK remains (snippet complements, does not replace).
    assert format_skill_bias("wait_reply") == BIAS_TALK

    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text=f"ORIENT\nwhy_now: {why}",
        now=datetime(2026, 7, 30, 8, 49, tzinfo=UTC),
        glass_rows=ROCKETS_GLASS,
        social_wake=True,
    )
    tail = _assert_rockets_tip(pkg)
    # True roles (not host role=user collapse).
    roles = {i.role for i in tail}
    assert "user" in roles
    assert "assistant" in roles

    outer = compose_outer_messages(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text=f"ORIENT\nwhy_now: {why}",
        package=pkg,
        glass_rows=ROCKETS_GLASS,
        social_wake=True,
    )
    # Orient carries snippet; glass-tail rows carry dialogue with true roles.
    orient = outer[-1]
    assert orient.get("role") == "user"
    assert "rockets" in (orient.get("content") or "")
    assert any(
        m.get("role") == "user" and "rockets" in (m.get("content") or "")
        and m is not orient
        for m in outer
    )
    assert any(
        m.get("role") == "assistant"
        and ("Thursday" in (m.get("content") or "") or "18:46" in (m.get("content") or ""))
        for m in outer
    )


# ---------------------------------------------------------------------------
# P5 — wait bridge: same tip across moment boundary
# ---------------------------------------------------------------------------


def test_path_p5_wait_bridge_tip_package(store):
    """P5: ends_moment + wait → later reply sees same glass tip (keep deferred S3)."""
    moment_a = "m_bridge_a"
    moment_b = "m_bridge_b"
    # Prior moment had work; glass is instance-level (not moment-scoped).
    store.put_atom(
        _atom(
            t="2026-07-30T08:40:00Z",
            kind="speak",
            text="time speak in moment A",
            moment_id=moment_a,
        )
    )
    # New moment after ends_moment + wait arm; wake lands on B.
    store.put_atom(
        _atom(
            t="2026-07-30T08:50:00Z",
            text=ROCKETS_USER,
            moment_id=moment_b,
            meta={"wake_message_id": WAKE_MSG_ID},
        )
    )
    why = _wait_reply_why_now()
    pkg_b = compose_meal(
        store,
        open_moment_id=moment_b,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text=f"why_now: {why}",
        now=datetime(2026, 7, 30, 8, 50, tzinfo=UTC),
        glass_rows=ROCKETS_GLASS,
        social_wake=True,
    )
    tail_b = _assert_rockets_tip(pkg_b)

    # Same glass tip package if composed as if still on A boundary (bridge).
    pkg_a_shape = compose_meal(
        store,
        open_moment_id=moment_a,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text=f"why_now: {why}",
        now=datetime(2026, 7, 30, 8, 50, tzinfo=UTC),
        glass_rows=ROCKETS_GLASS,
        social_wake=True,
    )
    tail_a = _assert_rockets_tip(pkg_a_shape)

    # Tip content parity across moment ids (glass-disk SoT, not open_moment).
    def tip_keys(items):
        return [(i.role, (i.content or "")[:80], (i.meta or {}).get("wake_message_id"))
                for i in items]

    assert tip_keys(tail_a) == tip_keys(tail_b)
    assert "rockets" in why


# ---------------------------------------------------------------------------
# P7 — restart mid-wait: tail rebuilds from disk glass
# ---------------------------------------------------------------------------


def test_path_p7_restart_mid_wait_tail_from_disk(paths, store):
    """P7: after 'restart', first social hop sees last N glass turns from disk."""
    import json

    # Durable glass on disk before process death (fixed ids for wake stamp).
    glass_path = paths.data_dir / "messages.jsonl"
    glass_path.parent.mkdir(parents=True, exist_ok=True)
    glass_path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in ROCKETS_GLASS) + "\n",
        encoding="utf-8",
    )

    # "Restart": no RAM meal snapshot — re-list glass from disk only.
    disk_glass = list_messages(limit=80, paths=paths)
    assert len(disk_glass) >= 2
    assert any("rockets" in (r.get("content") or "") for r in disk_glass)
    assert any(r.get("id") == WAKE_MSG_ID for r in disk_glass)

    open_id = "m_after_restart"
    store.put_atom(
        _atom(
            t="2026-07-30T09:00:00Z",
            text=ROCKETS_USER,
            moment_id=open_id,
            meta={"wake_message_id": WAKE_MSG_ID},
        )
    )
    why = _wait_reply_why_now()
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text=f"why_now: {why}",
        now=datetime(2026, 7, 30, 9, 0, tzinfo=UTC),
        glass_rows=disk_glass,
        social_wake=True,
    )
    _assert_rockets_tip(pkg)
    # Wake id stamped so hybrid skip can fire (B10).
    assert any(
        (i.meta or {}).get("wake_message_id") == WAKE_MSG_ID
        for i in pkg.items
        if i.channel == GLASS_TAIL_CHANNEL
    )


# ---------------------------------------------------------------------------
# P4 — interject remains chain-only (non-regression)
# ---------------------------------------------------------------------------


def test_interject_still_chain_only():
    """P4 / KD-INT: interject text lands on chain; outer rebuild not required."""
    chain: list[dict] = [
        {"role": "assistant", "content": "working…"},
    ]

    def drain():
        return [{"content": "quick interject note", "user_id": "operator"}]

    beats: list[dict] = []

    class _Moments:
        def append_beat(self, moment_id, beat):
            beats.append({"moment_id": moment_id, **dict(beat)})

    # _drain_interjections has no rebuild_outer parameter — chain-native only.
    _drain_interjections(
        chain,
        drain,
        _Moments(),
        "m_in_turn",
    )
    # Interject text is on the in-turn chain as user obs.
    assert any(
        m.get("role") == "user" and "quick interject note" in (m.get("content") or "")
        for m in chain
    )
    assert any(
        b.get("kind") == "interjection" and b.get("moment_id") == "m_in_turn"
        for b in beats
    )
    assert len(chain) == 2  # prior assistant + interject user


# ---------------------------------------------------------------------------
# B10 hybrid skip (named S2 acceptance; implementation from S1)
# ---------------------------------------------------------------------------


def test_hybrid_skips_when_message_id_on_glass_tail(store):
    """Hybrid inject skipped when wake id already on glass-tail meal messages."""
    wake_id = "hybrid-skip-wake-p"
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
    assert sum(1 for m in expanded if m.get("id") == wake_id) == 1
    assert len(expanded) == before


# ---------------------------------------------------------------------------
# PR4 / #127 — conversation-scoped tip + solo isolation (KD4/KD5)
# ---------------------------------------------------------------------------


def test_solo_non_social_empty_glass_tail_despite_client_dm_rows(store):
    """KD5 solo isolation: continuous/timer-shaped compose never packs social tip.

    Even when glass_rows contain a client DM session's conversation (dm:jim),
    non-social + null conversation_id → empty glass_tail (never inject session).
    """
    glass = [
        {
            "id": "j1",
            "role": "user",
            "content": "client session dm tip must not bleed",
            "user_id": "jim",
            "conversation_id": "dm:jim",
            "created_at": "2026-07-30T08:00:00Z",
        },
        {
            "id": "j2",
            "role": "assistant",
            "content": "prior dm reply",
            "user_id": "jim",
            "conversation_id": "dm:jim",
            "created_at": "2026-07-30T08:01:00Z",
        },
    ]
    # Continuous / timer / wait_timeout shaped: social_wake=False, no conversation.
    pkg = compose_meal(
        store,
        open_moment_id=None,
        budget_tokens=20_000,
        glass_rows=glass,
        social_wake=False,
        conversation_id=None,
    )
    tail = [i for i in pkg.items if i.channel == GLASS_TAIL_CHANNEL]
    assert tail == [], f"solo must not pack tip, got {[(i.role, i.content) for i in tail]}"
    assert pkg.glass_tail_meta is None or pkg.glass_tail_meta.get("packed", 0) == 0

    # After solo: social jim wake still scopes to dm:jim.
    pkg_social = compose_meal(
        store,
        open_moment_id=None,
        budget_tokens=20_000,
        glass_rows=glass,
        social_wake=True,
        conversation_id="dm:jim",
    )
    tail_s = [i for i in pkg_social.items if i.channel == GLASS_TAIL_CHANNEL]
    assert tail_s, "social jim wake must still pack dm:jim tip"
    assert pkg_social.glass_tail_meta is not None
    assert pkg_social.glass_tail_meta.get("conversation_id") == "dm:jim"
    assert pkg_social.glass_tail_meta.get("packed") == 2
    bodies = [i.content for i in tail_s]
    assert any("client session dm tip" in b or "must not bleed" in b for b in bodies)
    assert any("prior dm reply" in b for b in bodies)


def test_wait_timeout_non_social_no_glass_tail(store):
    """KD19: wait_timeout remains non-social for glass_tail (empty tip)."""
    from elyra.loop.continuous_policy import SOCIAL_WAKE_KINDS

    assert "wait_timeout" not in SOCIAL_WAKE_KINDS
    glass = [
        {
            "id": "w1",
            "role": "user",
            "content": "wait reply tip",
            "user_id": "operator",
            "conversation_id": "dm:operator",
            "created_at": "2026-07-30T08:00:00Z",
        },
    ]
    # rebuild_outer policy: wait_timeout → social_wake=False, empty tip.
    pkg = compose_meal(
        store,
        open_moment_id=None,
        budget_tokens=20_000,
        glass_rows=glass,
        social_wake=False,  # wait_timeout
        conversation_id=None,
    )
    assert not any(i.channel == GLASS_TAIL_CHANNEL for i in pkg.items)


def test_conversation_scoped_tip_excludes_other_dm(store):
    """Multi-user path: compose for dm:jim excludes sam rows (no soft fill)."""
    glass = [
        {
            "id": "j1",
            "role": "user",
            "content": "jim rockets question",
            "user_id": "jim",
            "conversation_id": "dm:jim",
            "created_at": "2026-07-30T08:00:00Z",
        },
        {
            "id": "s1",
            "role": "user",
            "content": "sam secret topic",
            "user_id": "sam",
            "conversation_id": "dm:sam",
            "created_at": "2026-07-30T08:01:00Z",
        },
        {
            "id": "j2",
            "role": "assistant",
            "content": "jim answer",
            "user_id": "jim",
            "conversation_id": "dm:jim",
            "created_at": "2026-07-30T08:02:00Z",
        },
    ]
    pkg = compose_meal(
        store,
        open_moment_id=None,
        budget_tokens=50_000,
        glass_rows=glass,
        social_wake=True,
        conversation_id="dm:jim",
    )
    tail = [i for i in pkg.items if i.channel == GLASS_TAIL_CHANNEL]
    assert tail
    blob = "\n".join(i.content for i in tail)
    assert "jim rockets" in blob or "jim answer" in blob
    assert "sam secret" not in blob
    assert pkg.glass_tail_meta is not None
    assert pkg.glass_tail_meta.get("conversation_id") == "dm:jim"
    # Semantic seed hygiene: last_user_text from scoped tip only
    assert "sam" not in (pkg.glass_tail_meta.get("last_user_text") or "")
