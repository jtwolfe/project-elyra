"""Normative beat → atom promotion (R1–R10, KD16). Hermetic; no GoalsStore."""

from __future__ import annotations

import json

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.promote import (
    CONTROL_OBS_KINDS,
    LEDGER_TOOL_NAMES,
    MAX_TOOL_ATOMS_PER_MOMENT,
    MODEL_PROMOTE_MIN_CHARS,
    TOOL_OK_PREVIEW_CHARS,
    PromoteState,
    content_hash,
    is_control_obs_kind,
    promote_beat,
    promote_wake_observation,
)
from elyra.memory.store import open_memory_store


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def settings() -> MemorySettings:
    return MemorySettings(write_atoms=True, backend="jsonl")


@pytest.fixture
def store(paths, settings):
    return open_memory_store(paths, settings)


def _ts(i: int = 0) -> str:
    return f"2026-07-28T10:{i:02d}:00Z"


# ── R1 control-plane ──────────────────────────────────────────────────────


def test_is_control_obs_kind_each_live_kind():
    for kind in CONTROL_OBS_KINDS:
        assert is_control_obs_kind(kind) is True, kind
    assert is_control_obs_kind("tool_thrash") is True
    assert is_control_obs_kind("thrash_lesson") is True
    assert is_control_obs_kind("tool_skip_identical") is True
    # belt-and-suspenders prefix
    assert is_control_obs_kind("thrash_future") is True
    assert is_control_obs_kind("interjection") is False
    assert is_control_obs_kind(None) is False
    assert is_control_obs_kind("") is False


@pytest.mark.parametrize("kind", sorted(CONTROL_OBS_KINDS))
def test_r1_control_obs_never_promotes(store, settings, kind):
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "obs",
            "kind": kind,
            "content": f"host line for {kind}",
            "ts": _ts(0),
        },
        settings=settings,
    )
    assert atom is None
    assert store.list_by_moment("m1") == []


def test_r1_thrash_prefix_skip(store, settings):
    assert (
        promote_beat(
            store,
            "m1",
            {"type": "obs", "kind": "thrash_custom", "content": "x", "ts": _ts()},
            settings=settings,
        )
        is None
    )


def test_write_atoms_false_no_op(paths, store):
    off = MemorySettings(write_atoms=False)
    assert (
        promote_beat(
            store,
            "m1",
            {
                "type": "tool",
                "name": "speak",
                "ok": True,
                "content": json.dumps({"ok": True, "text": "hi"}),
                "ts": _ts(),
            },
            settings=off,
        )
        is None
    )
    assert store.list_by_moment("m1") == []


def test_store_none_no_op(settings):
    assert (
        promote_beat(
            None,
            "m1",
            {
                "type": "tool",
                "name": "speak",
                "ok": True,
                "content": json.dumps({"ok": True, "text": "hi"}),
            },
            settings=settings,
        )
        is None
    )


# ── R2 speak (type=tool, name=speak) ───────────────────────────────────────


def test_r2_speak_ok_promotes_kind_speak(store, settings):
    content = json.dumps(
        {"ok": True, "transport_ok": True, "text": "Hello friend", "user_id": "operator"}
    )
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "tool_call_id": "tc1",
            "content": content,
            "ts": _ts(1),
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "speak"
    assert atom.content_text == "Hello friend"
    assert atom.meta.get("transport_ok") is True
    assert atom.meta.get("ok") is True
    assert atom.source_beat_type == "tool"


def test_r2_speak_failed_still_kind_speak(store, settings):
    content = json.dumps(
        {
            "ok": False,
            "transport_ok": False,
            "text": "would have said",
            "reason": "transport_failed",
        }
    )
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "speak",
            "ok": False,
            "error_reason": "transport_failed",
            "content": content,
            "ts": _ts(2),
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "speak"
    assert atom.meta.get("transport_ok") is False
    assert "would have said" in atom.content_text or "transport_failed" in atom.content_text


# ── R3 wake + interjection ─────────────────────────────────────────────────


def test_r3_interjection_promotes_observation(store, settings):
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "obs",
            "kind": "interjection",
            "content": "quick note from user",
            "ts": _ts(3),
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "observation"
    assert atom.content_text == "quick note from user"


def test_promote_wake_observation_text(store, settings):
    atom = promote_wake_observation(
        store,
        "m1",
        content="  hi there  ",
        message_id="msg_abc",
        media_ids=(),
        why_now="social",
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "observation"
    assert atom.content_text == "hi there"
    assert atom.meta.get("wake_message_id") == "msg_abc"
    assert atom.meta.get("why_now") == "social"
    assert atom.source_beat_type == "wake"


def test_promote_wake_media_only(store, settings):
    atom = promote_wake_observation(
        store,
        "m1",
        content="",
        message_id="msg_media",
        media_ids=["att_111", "att_222"],
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "observation"
    assert atom.content_text == ""
    assert atom.media_ids == ("att_111", "att_222")
    assert atom.meta.get("wake_message_id") == "msg_media"


def test_promote_wake_empty_skips(store, settings):
    assert (
        promote_wake_observation(
            store, "m1", content="  ", message_id=None, media_ids=(), settings=settings
        )
        is None
    )


def test_r3_wake_dedupe_within_window(store, settings):
    a1 = promote_wake_observation(
        store,
        "m1",
        content="same text",
        message_id="m1a",
        settings=settings,
    )
    assert a1 is not None
    # Same content + media within 2s (no media) → skip
    a2 = promote_wake_observation(
        store,
        "m1",
        content="same text",
        message_id="m1b",
        settings=settings,
    )
    assert a2 is None
    assert len(store.list_by_moment("m1")) == 1


def test_r3_interjection_dedupes_against_wake(store, settings):
    promote_wake_observation(
        store,
        "m1",
        content="glass already has this",
        message_id="wake1",
        settings=settings,
    )
    # Interjection with same text should dedupe within 2s.
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "obs",
            "kind": "interjection",
            "content": "glass already has this",
            "ts": store.list_by_moment("m1")[0].t_start,
        },
        settings=settings,
    )
    assert atom is None
    assert len(store.list_by_moment("m1")) == 1


# ── R4 tool density / ledger ───────────────────────────────────────────────


def test_r4_ledger_create_goal_one_liner(store, settings):
    payload = {
        "ok": True,
        "goal": {"id": "g_1", "title": "Ship memory", "status": "open"},
    }
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "create_goal",
            "ok": True,
            "content": json.dumps(payload),
            "ts": _ts(4),
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "ledger"
    assert atom.content_text == "goal g_1: Ship memory [open]"
    # Does not count against tool density.
    assert atom.meta.get("tool_name") == "create_goal"


def test_r4_ledger_task_one_liner(store, settings):
    payload = {
        "ok": True,
        "task": {"id": "t_9", "title": "Write tests", "status": "ready"},
    }
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "update_task",
            "ok": True,
            "content": json.dumps(payload),
            "ts": _ts(5),
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "ledger"
    assert atom.content_text == "task t_9 → ready: Write tests"


@pytest.mark.parametrize("name", sorted(LEDGER_TOOL_NAMES))
def test_r4_all_ledger_mutate_names(store, settings, name):
    if "goal" in name:
        body = {"ok": True, "goal": {"id": "g", "title": "T", "status": "open"}}
    else:
        body = {"ok": True, "task": {"id": "t", "title": "T", "status": "pending"}}
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": name,
            "ok": True,
            "content": json.dumps(body),
            "ts": _ts(6),
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "ledger"


def test_r4_read_only_ledger_is_tool_not_ledger(store, settings):
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "list_goals",
            "ok": True,
            "content": json.dumps({"ok": True, "goals": []}),
            "ts": _ts(7),
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "tool"
    assert atom.meta.get("preview") is True


def test_r4_tool_ok_preview_240(store, settings):
    long_body = "x" * 500
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "run_cmd",
            "ok": True,
            "content": long_body,
            "ts": _ts(8),
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "tool"
    assert len(atom.content_text) == TOOL_OK_PREVIEW_CHARS
    assert atom.meta.get("preview") is True
    assert atom.meta.get("truncated") is True


def test_r4_tool_fail_full_body(store, settings):
    body = ("error detail " * 50).strip()  # > 240; promote trims edges
    assert len(body) > TOOL_OK_PREVIEW_CHARS
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "run_cmd",
            "ok": False,
            "error_reason": "nonzero_exit",
            "content": body,
            "ts": _ts(9),
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "tool"
    assert atom.content_text == body
    assert len(atom.content_text) > TOOL_OK_PREVIEW_CHARS
    assert atom.meta.get("preview") is not True
    assert atom.meta.get("ok") is False
    assert atom.meta.get("error_reason") == "nonzero_exit"


def test_r4_empty_tool_content_skipped(store, settings):
    assert (
        promote_beat(
            store,
            "m1",
            {
                "type": "tool",
                "name": "run_cmd",
                "ok": True,
                "content": "   ",
                "ts": _ts(10),
            },
            settings=settings,
        )
        is None
    )


def test_r4_tool_cap_skips_ok_allows_fail(store, settings):
    state = PromoteState()
    # Fill cap with ok tools.
    for i in range(MAX_TOOL_ATOMS_PER_MOMENT):
        a = promote_beat(
            store,
            "m1",
            {
                "type": "tool",
                "name": "run_cmd",
                "ok": True,
                "content": f"ok-{i}-" + ("y" * 20),
                "ts": f"2026-07-28T11:{i // 60:02d}:{i % 60:02d}Z",
            },
            settings=settings,
            moment_tool_counts=state,
        )
        assert a is not None, i
    assert state.tool_atoms == MAX_TOOL_ATOMS_PER_MOMENT

    # Further ok tools skipped.
    skipped = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "run_cmd",
            "ok": True,
            "content": "should-skip-ok",
            "ts": "2026-07-28T12:00:00Z",
        },
        settings=settings,
        moment_tool_counts=state,
    )
    assert skipped is None

    # Failures still promote after cap.
    failed = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "run_cmd",
            "ok": False,
            "error_reason": "boom",
            "content": "failure after cap",
            "ts": "2026-07-28T12:00:01Z",
        },
        settings=settings,
        moment_tool_counts=state,
    )
    assert failed is not None
    assert failed.meta.get("ok") is False

    # Speak / ledger not counted against cap and still promote.
    speak = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": json.dumps({"ok": True, "text": "still speak"}),
            "ts": "2026-07-28T12:00:02Z",
        },
        settings=settings,
        moment_tool_counts=state,
    )
    assert speak is not None
    assert speak.kind == "speak"


def test_r4_load_skill_not_promoted(store, settings):
    assert (
        promote_beat(
            store,
            "m1",
            {
                "type": "tool",
                "name": "load_skill",
                "ok": True,
                "content": "PLAYBOOK ACTIVE: talk\n…",
                "ts": _ts(11),
            },
            settings=settings,
        )
        is None
    )


# ── R5 model ───────────────────────────────────────────────────────────────


def test_r5_model_promotes_when_long_enough(store, settings):
    text = "A" * MODEL_PROMOTE_MIN_CHARS
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "model",
            "content": text,
            "reasoning": "secret chain of thought",
            "tool_calls": [],
            "hop": 2,
            "ts": _ts(12),
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "model"
    assert atom.content_text == text
    assert "secret" not in atom.content_text
    assert "reasoning" not in atom.meta


def test_r5_model_skips_short(store, settings):
    assert (
        promote_beat(
            store,
            "m1",
            {
                "type": "model",
                "content": "short",
                "tool_calls": [],
                "ts": _ts(13),
            },
            settings=settings,
        )
        is None
    )


def test_r5_model_skips_with_tool_calls(store, settings):
    assert (
        promote_beat(
            store,
            "m1",
            {
                "type": "model",
                "content": "A" * 80,
                "tool_calls": [{"id": "1", "name": "speak"}],
                "ts": _ts(14),
            },
            settings=settings,
        )
        is None
    )


def test_r5_stop_not_promoted(store, settings):
    assert (
        promote_beat(
            store,
            "m1",
            {"type": "stop", "stop_reason": "no_tools", "ts": _ts(15)},
            settings=settings,
        )
        is None
    )


# ── R6 empty moment ────────────────────────────────────────────────────────


def test_r6_empty_when_only_control(store, settings):
    for kind in ("continue", "tool_thrash", "thrash_lesson"):
        promote_beat(
            store,
            "m_empty",
            {"type": "obs", "kind": kind, "content": "x", "ts": _ts()},
            settings=settings,
        )
    assert store.list_by_moment("m_empty") == []


# ── R7 sequential linking ──────────────────────────────────────────────────


def test_r7_sequential_links_within_moment(store, settings):
    a1 = promote_wake_observation(
        store,
        "m1",
        content="first wake",
        message_id="w1",
        settings=settings,
    )
    a2 = promote_beat(
        store,
        "m1",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": json.dumps({"ok": True, "text": "reply"}),
            "ts": _ts(20),
        },
        settings=settings,
    )
    assert a1 is not None and a2 is not None
    a1b = store.get_atom(a1.atom_id)
    a2b = store.get_atom(a2.atom_id)
    assert a1b is not None and a2b is not None
    assert a2b.prev_atom_id == a1.atom_id
    assert a1b.next_atom_id == a2.atom_id
    assert a2b.next_atom_id is None


def test_r7_link_across_moments_default(store, settings):
    a1 = promote_wake_observation(
        store,
        "mA",
        content="moment A",
        message_id="wa",
        settings=settings,
    )
    a2 = promote_wake_observation(
        store,
        "mB",
        content="moment B",
        message_id="wb",
        settings=settings,
    )
    assert a1 is not None and a2 is not None
    a2b = store.get_atom(a2.atom_id)
    a1b = store.get_atom(a1.atom_id)
    assert a2b is not None and a1b is not None
    assert a2b.prev_atom_id == a1.atom_id
    assert a1b.next_atom_id == a2.atom_id


def test_r7_no_cross_moment_when_disabled(paths):
    cfg = MemorySettings(write_atoms=True, link_across_moments=False)
    s = open_memory_store(paths, cfg)
    a1 = promote_wake_observation(
        s, "mA", content="A only", message_id="a", settings=cfg
    )
    a2 = promote_wake_observation(
        s, "mB", content="B only", message_id="b", settings=cfg
    )
    assert a1 is not None and a2 is not None
    a2b = s.get_atom(a2.atom_id)
    assert a2b is not None
    assert a2b.prev_atom_id is None


# ── R8 idempotency ─────────────────────────────────────────────────────────


def test_r8_idempotent_re_promote(store, settings):
    beat = {
        "type": "tool",
        "name": "speak",
        "ok": True,
        "content": json.dumps({"ok": True, "text": "once only"}),
        "ts": "2026-07-28T15:00:00Z",
    }
    a1 = promote_beat(store, "m1", beat, settings=settings)
    a2 = promote_beat(store, "m1", beat, settings=settings)
    assert a1 is not None
    assert a2 is None
    speaks = store.list_by_moment("m1", kinds=["speak"])
    assert len(speaks) == 1


def test_r8_wake_idempotent_same_message_id(store, settings):
    a1 = promote_wake_observation(
        store,
        "m1",
        content="hello",
        message_id="fixed_id",
        settings=settings,
    )
    # Change text slightly but same message_id → still no-op by wake key.
    # Note: content dedupe also applies; either path must prevent double write.
    a2 = promote_wake_observation(
        store,
        "m1",
        content="hello",
        message_id="fixed_id",
        settings=settings,
    )
    assert a1 is not None
    assert a2 is None
    assert len(store.list_by_moment("m1")) == 1


# ── R9 never raise ─────────────────────────────────────────────────────────


def test_r9_promote_never_raises_on_bad_beat(store, settings):
    assert promote_beat(store, "m1", None, settings=settings) is None  # type: ignore[arg-type]
    assert promote_beat(store, "m1", {"type": "tool"}, settings=settings) is None
    assert promote_beat(store, "", {"type": "model", "content": "x" * 50}, settings=settings) is None


def test_content_hash_stable():
    assert content_hash("abc") == content_hash("abc")
    assert len(content_hash("abc")) == 16
    assert content_hash("a") != content_hash("b")
