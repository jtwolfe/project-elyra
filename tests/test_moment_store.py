"""Moment store: open/close, beats, recover interrupt, schema_version."""

from __future__ import annotations

import json

import pytest

from elyra.config import resolve_paths
from elyra.moment import SCHEMA_VERSION, MomentStore
from elyra.moment.types import STOP_REASONS


@pytest.fixture
def store(tmp_path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return MomentStore(paths)


def test_open_moment_creates_index_line_with_schema_version(store):
    mid = store.open_moment(
        why_now="user_message:hello",
        user_id="operator",
        wake_id="W1",
        goal_ids=["g1"],
        task_ids=["t1"],
    )
    assert mid
    meta = store.get_moment(mid)
    assert meta is not None
    assert meta["schema_version"] == SCHEMA_VERSION
    assert meta["id"] == mid
    assert meta["ended_at"] is None
    assert meta["stop_reason"] is None
    assert meta["why_now"] == "user_message:hello"
    assert meta["user_id"] == "operator"
    assert meta["wake_id"] == "W1"
    assert meta["goal_ids"] == ["g1"]
    assert meta["task_ids"] == ["t1"]
    assert meta["skills_used"] == []
    assert meta["hop_count"] == 0
    assert meta["started_at"]

    # raw index line also has schema_version
    raw_lines = store.index_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw_lines) == 1
    row = json.loads(raw_lines[0])
    assert row["schema_version"] == 1
    assert row["ended_at"] is None


def test_open_close_roundtrip(store):
    mid = store.open_moment(why_now="timer:check", user_id=None)
    assert store.list_open_moments()[0]["id"] == mid

    closed = store.close_moment(
        mid,
        "no_tools",
        hop_count=3,
        skills_used=["talk"],
    )
    assert closed["ended_at"] is not None
    assert closed["stop_reason"] == "no_tools"
    assert closed["hop_count"] == 3
    assert closed["skills_used"] == ["talk"]
    assert closed["schema_version"] == SCHEMA_VERSION
    assert store.list_open_moments() == []
    assert store.get_moment(mid)["stop_reason"] == "no_tools"


def test_append_beats_order_preserved(store):
    mid = store.open_moment(why_now="user_message:x", user_id="operator")
    store.append_beat(
        mid,
        {
            "type": "model",
            "content": "hi",
            "reasoning": "r",
            "tool_calls": [{"name": "speak", "args": {}}],
        },
    )
    store.append_beat(
        mid,
        {
            "type": "tool",
            "name": "speak",
            "args": {"text": "hello"},
            "result": {"ok": True},
            "ok": True,
        },
    )
    store.append_beat(
        mid,
        {"type": "speak", "user_id": "operator", "text": "hello", "transport_ok": True},
    )
    store.append_beat(mid, {"type": "stop", "reason": "no_tools"})

    beats = store.list_beats(mid)
    assert [b["type"] for b in beats] == ["model", "tool", "speak", "stop"]
    assert all("ts" in b for b in beats)
    assert beats[0]["content"] == "hi"
    assert beats[1]["name"] == "speak"
    assert beats[2]["transport_ok"] is True


def test_append_beat_rejects_closed_and_unknown(store):
    mid = store.open_moment(why_now="x")
    store.close_moment(mid, "wait")
    with pytest.raises(ValueError, match="already closed"):
        store.append_beat(mid, {"type": "obs", "kind": "continue", "text": "t"})
    with pytest.raises(KeyError, match="unknown moment"):
        store.append_beat("00000000-0000-0000-0000-000000000099", {"type": "obs"})


def test_append_beat_rejects_bad_type(store):
    mid = store.open_moment(why_now="x")
    with pytest.raises(ValueError, match="invalid beat type"):
        store.append_beat(mid, {"type": "not_a_beat"})
    with pytest.raises(ValueError, match="invalid beat type"):
        store.append_beat(mid, {})


def test_recover_open_moments_interrupts(store):
    a = store.open_moment(why_now="a", wake_id="wa")
    b = store.open_moment(why_now="b", wake_id="wb")
    store.append_beat(a, {"type": "model", "content": "partial", "reasoning": "", "tool_calls": []})
    store.close_moment(b, "max_hops", hop_count=32)

    assert {m["id"] for m in store.list_open_moments()} == {a}

    closed_ids = store.recover_open_moments()
    assert closed_ids == [a]
    meta = store.get_moment(a)
    assert meta["stop_reason"] == "interrupted"
    assert meta["ended_at"] is not None
    assert store.list_open_moments() == []
    # already-closed moment unchanged
    assert store.get_moment(b)["stop_reason"] == "max_hops"
    # beats preserved
    assert store.list_beats(a)[0]["content"] == "partial"


def test_recover_when_none_open_is_noop(store):
    mid = store.open_moment(why_now="x")
    store.close_moment(mid, "policy")
    assert store.recover_open_moments() == []
    assert store.get_moment(mid)["stop_reason"] == "policy"


def test_close_idempotent_safe(store):
    mid = store.open_moment(why_now="x")
    first = store.close_moment(mid, "error", hop_count=1)
    second = store.close_moment(mid, "interrupted", hop_count=99, skills_used=["x"])
    # second call is no-op: reason/hop stay from first close
    assert second["stop_reason"] == "error"
    assert second["hop_count"] == 1
    assert second["ended_at"] == first["ended_at"]
    assert store.get_moment(mid)["skills_used"] == []


def test_close_unknown_raises(store):
    with pytest.raises(KeyError, match="unknown moment"):
        store.close_moment("00000000-0000-0000-0000-000000000001", "error")


def test_close_rejects_invalid_stop_reason(store):
    mid = store.open_moment(why_now="x")
    with pytest.raises(ValueError, match="invalid stop_reason"):
        store.close_moment(mid, "not_a_reason")


@pytest.mark.parametrize("reason", sorted(STOP_REASONS))
def test_all_stop_reasons_accepted(store, reason):
    mid = store.open_moment(why_now=f"r:{reason}")
    store.close_moment(mid, reason)
    assert store.get_moment(mid)["stop_reason"] == reason


def test_open_duplicate_id_raises(store):
    mid = store.open_moment(why_now="a", moment_id="fixed-moment-1")
    assert mid == "fixed-moment-1"
    with pytest.raises(ValueError, match="already exists"):
        store.open_moment(why_now="b", moment_id="fixed-moment-1")


@pytest.mark.parametrize(
    "bad_id",
    ["", "..", ".", "a/b", "../x", "a\\b", " bad", "\n", "-lead"],
)
def test_moment_id_path_jail(store, bad_id):
    with pytest.raises(ValueError, match="invalid moment_id"):
        store.open_moment(why_now="x", moment_id=bad_id)
    with pytest.raises(ValueError, match="invalid moment_id"):
        store.tape_path(bad_id)


@pytest.mark.parametrize("reserved", ["index", "Index", "INDEX"])
def test_moment_id_rejects_reserved_index_stem(store, reserved):
    """``index`` would make tape_path == index_path; reject all case variants."""
    with pytest.raises(ValueError, match="invalid moment_id"):
        store.open_moment(why_now="x", moment_id=reserved)
    with pytest.raises(ValueError, match="invalid moment_id"):
        store.tape_path(reserved)
    # Accepted ids must never resolve the tape onto the index file.
    mid = store.open_moment(why_now="ok", moment_id="m-ok-1")
    assert store.tape_path(mid) != store.index_path
    assert store.tape_path(mid).name != store.index_path.name


def test_append_index_repairs_missing_trailing_newline(store):
    """Partial last index line must not glue onto the next open record."""
    mid1 = store.open_moment(why_now="first", moment_id="m-first")
    # Complete first record, then a crash mid-write fragment without trailing \\n.
    complete = store.index_path.read_bytes()
    assert complete.endswith(b"\n")
    store.index_path.write_bytes(complete + b'{"id":"partial-crash"')
    mid2 = store.open_moment(why_now="second", moment_id="m-second")
    assert store.get_moment(mid1) is not None
    assert store.get_moment(mid1)["why_now"] == "first"
    assert store.get_moment(mid2) is not None
    assert store.get_moment(mid2)["why_now"] == "second"
    store.append_beat(mid2, {"type": "obs", "kind": "continue", "text": "t"})
    assert store.list_beats(mid2)[0]["text"] == "t"


def test_recover_batches_multiple_open(store):
    ids = [
        store.open_moment(why_now=f"w{i}", moment_id=f"batch-{i}")
        for i in range(3)
    ]
    closed = store.recover_open_moments()
    assert sorted(closed) == sorted(ids)
    assert store.list_open_moments() == []
    for mid in ids:
        assert store.get_moment(mid)["stop_reason"] == "interrupted"


def test_list_beats_missing_tape_empty(store):
    mid = store.open_moment(why_now="x")
    assert store.list_beats(mid) == []


def test_works_without_prior_ensure_data_dirs(tmp_path):
    """Store creates moments dir itself if caller skipped ensure_data_dirs."""
    paths = resolve_paths(tmp_path)
    store = MomentStore(paths)
    mid = store.open_moment(why_now="bootstrap")
    store.append_beat(mid, {"type": "obs", "kind": "interjection", "text": "hi"})
    store.close_moment(mid, "no_tools")
    assert store.get_moment(mid)["stop_reason"] == "no_tools"
    assert len(store.list_beats(mid)) == 1


def test_list_moments_newest_first_with_limit(store):
    m1 = store.open_moment(why_now="older", moment_id="mom-a")
    store.close_moment(m1, "no_tools")
    m2 = store.open_moment(why_now="newer", moment_id="mom-b")
    all_rows = store.list_moments()
    assert [r["id"] for r in all_rows] == ["mom-b", "mom-a"]
    limited = store.list_moments(limit=1)
    assert len(limited) == 1
    assert limited[0]["id"] == "mom-b"
    open_only = store.list_moments(open_only=True)
    assert [r["id"] for r in open_only] == ["mom-b"]
    # Negative limit clamps to empty (never means "all").
    assert store.list_moments(limit=-1) == []
    assert store.list_moments(limit=0) == []
