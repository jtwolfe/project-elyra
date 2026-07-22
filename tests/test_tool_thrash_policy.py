"""Pure table tests for tool_thrash_policy (Phase B)."""

from __future__ import annotations

import json

from elyra.loop.continuous_policy import should_in_moment_work_nudge
from elyra.loop.tool_thrash_policy import (
    FAIL_STREAK_THRESHOLD,
    MAX_THRASH_HOSTS,
    OK_STREAK_THRESHOLD,
    THRASH_HOST,
    canonical_args,
    should_inject_thrash_host,
    thrash_detail,
    thrash_host_message,
    tool_fingerprint,
    update_thrash_streak,
)


def test_canonical_args_stable_sort_and_empty() -> None:
    assert canonical_args(None) == "{}"
    assert canonical_args({}) == "{}"
    a = canonical_args({"b": 1, "a": 2})
    b = canonical_args({"a": 2, "b": 1})
    assert a == b
    assert json.loads(a) == {"a": 2, "b": 1}


def test_canonical_args_files_map_hashes_bodies() -> None:
    raw = {"files": {"TOOL.md": "hello body", "pkg.py": "x" * 10}}
    s = canonical_args(raw)
    body = json.loads(s)
    assert "files" in body
    assert body["files"]["TOOL.md"]["len"] == len("hello body")
    assert "sha256_16" in body["files"]["TOOL.md"]
    # Content change → different fingerprint
    raw2 = {"files": {"TOOL.md": "hello body!", "pkg.py": "x" * 10}}
    assert canonical_args(raw) != canonical_args(raw2)


def test_canonical_args_path_and_speak_normalize() -> None:
    p1 = canonical_args({"path": "tools//drafts\\x"})
    p2 = canonical_args({"path": "tools/drafts/x"})
    assert p1 == p2
    t1 = canonical_args({"text": "hello   world\n"})
    t2 = canonical_args({"text": "hello world"})
    assert t1 == t2


def test_tool_fingerprint_casefold_name() -> None:
    fp1 = tool_fingerprint("Read_File", {"path": "a"})
    fp2 = tool_fingerprint("read_file", {"path": "a"})
    assert fp1 == fp2
    assert fp1.startswith("read_file|")


def test_update_thrash_streak_increments_and_resets() -> None:
    u1 = update_thrash_streak(
        prev_fp=None,
        prev_streak=0,
        tool_name="read_file",
        args={"path": "missing.md"},
        ok=False,
        error_reason="not_found",
    )
    assert u1.streak == 1
    assert u1.repeated is False
    assert u1.ok is False
    assert u1.error_reason == "not_found"
    assert u1.tool_name == "read_file"

    u2 = update_thrash_streak(
        prev_fp=u1.fingerprint,
        prev_streak=u1.streak,
        tool_name="read_file",
        args={"path": "missing.md"},
        ok=False,
        error_reason="not_found",
    )
    assert u2.fingerprint == u1.fingerprint
    assert u2.streak == 2
    assert u2.repeated is True

    u3 = update_thrash_streak(
        prev_fp=u2.fingerprint,
        prev_streak=u2.streak,
        tool_name="read_file",
        args={"path": "other.md"},
        ok=False,
        error_reason="not_found",
    )
    assert u3.fingerprint != u2.fingerprint
    assert u3.streak == 1
    assert u3.repeated is False


def test_update_thrash_streak_ok_pass_through() -> None:
    u = update_thrash_streak(
        prev_fp=None,
        prev_streak=0,
        tool_name="speak",
        args={"text": "hi"},
        ok=True,
        error_reason=None,
    )
    assert u.ok is True
    assert u.error_reason is None
    assert u.streak == 1


def test_should_inject_thrash_host_fail_threshold() -> None:
    # Below fail threshold
    d = should_inject_thrash_host(
        streak=FAIL_STREAK_THRESHOLD - 1,
        last_ok=False,
        thrash_host_sent=0,
        tool_name="read_file",
    )
    assert d.inject is False
    assert d.reason == "below_threshold"

    d = should_inject_thrash_host(
        streak=FAIL_STREAK_THRESHOLD,
        last_ok=False,
        thrash_host_sent=0,
        tool_name="read_file",
    )
    assert d.inject is True
    assert d.reason == "injected"
    assert d.kind == "thrash_fail_streak"


def test_should_inject_thrash_host_ok_threshold() -> None:
    d = should_inject_thrash_host(
        streak=OK_STREAK_THRESHOLD - 1,
        last_ok=True,
        thrash_host_sent=0,
        tool_name="speak",
    )
    assert d.inject is False
    assert d.reason == "below_threshold"

    d = should_inject_thrash_host(
        streak=OK_STREAK_THRESHOLD,
        last_ok=True,
        thrash_host_sent=0,
        tool_name="speak",
    )
    assert d.inject is True
    assert d.kind == "thrash_speak_repeat"

    d = should_inject_thrash_host(
        streak=OK_STREAK_THRESHOLD,
        last_ok=True,
        thrash_host_sent=0,
        tool_name="list_dir",
    )
    assert d.inject is True
    assert d.kind == "thrash_repeat"


def test_should_inject_thrash_host_budget() -> None:
    d = should_inject_thrash_host(
        streak=10,
        last_ok=False,
        thrash_host_sent=MAX_THRASH_HOSTS,
        tool_name="read_file",
    )
    assert d.inject is False
    assert d.reason == "budget"


def test_should_inject_thrash_host_no_tool() -> None:
    d = should_inject_thrash_host(
        streak=10,
        last_ok=False,
        thrash_host_sent=0,
        tool_name=None,
    )
    assert d.inject is False
    assert d.reason == "no_tool"

    d = should_inject_thrash_host(
        streak=10,
        last_ok=False,
        thrash_host_sent=0,
        tool_name="  ",
    )
    assert d.inject is False
    assert d.reason == "no_tool"


def test_thrash_host_message_normative_copy() -> None:
    msg = thrash_host_message(tool_name="read_file", streak=3, detail="not_found")
    assert msg.startswith("HOST:")
    assert "tool thrash" in msg
    assert "read_file" in msg
    assert "×3" in msg
    assert "not_found" in msg
    assert "call tools to continue" not in msg
    assert "load_skill(\"rest\")" in msg
    assert msg == THRASH_HOST.format(
        tool_name="read_file", streak=3, detail="not_found"
    )


def test_thrash_detail() -> None:
    assert thrash_detail(last_ok=True, last_error=None) == "ok"
    assert thrash_detail(last_ok=False, last_error="not_found") == "not_found"
    assert thrash_detail(last_ok=False, last_error=None) == "error"
    assert thrash_detail(last_ok=None, last_error=None) == "unknown"


def test_work_continue_thrash_recovery_gate() -> None:
    """K15: thrash_host_sent > 0 → inject=False, reason thrash_recovery."""
    d = should_in_moment_work_nudge(
        continuous_enabled=True,
        social_wake=False,
        spoke=False,
        no_speak_nudge_pending_or_needed=False,
        work_nudge_sent=0,
        max_nudges=1,
        work_context=True,
        last_hop_was_flood=False,
        thrash_host_sent=1,
    )
    assert d.inject is False
    assert d.reason == "thrash_recovery"

    # thrash_host_sent=0 still injects when workish
    d2 = should_in_moment_work_nudge(
        continuous_enabled=True,
        social_wake=False,
        spoke=False,
        no_speak_nudge_pending_or_needed=False,
        work_nudge_sent=0,
        max_nudges=1,
        work_context=True,
        last_hop_was_flood=False,
        thrash_host_sent=0,
    )
    assert d2.inject is True
    assert d2.reason == "injected"


def test_streak_table_three_fails_then_threshold() -> None:
    """End-to-end pure streak → host decision at attempt 3."""
    prev_fp = None
    prev_streak = 0
    for i in range(1, 4):
        u = update_thrash_streak(
            prev_fp=prev_fp,
            prev_streak=prev_streak,
            tool_name="read_file",
            args={"path": "tools/drafts/x/TOOL.md"},
            ok=False,
            error_reason="not_found",
        )
        prev_fp = u.fingerprint
        prev_streak = u.streak
        assert u.streak == i
        d = should_inject_thrash_host(
            streak=u.streak,
            last_ok=u.ok,
            thrash_host_sent=0,
            tool_name=u.tool_name,
        )
        if i < FAIL_STREAK_THRESHOLD:
            assert d.inject is False
        else:
            assert d.inject is True
            assert d.kind == "thrash_fail_streak"
