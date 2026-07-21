"""Tests for stop-reason decision helpers."""

from __future__ import annotations

from elyra.loop.stop import (
    STOP_BLOCKED,
    STOP_ERROR,
    STOP_INTERRUPTED,
    STOP_MAX_HOPS,
    STOP_NO_TOOLS,
    STOP_POLICY,
    STOP_REASONS,
    STOP_TIME_CONTINUE_DECLINED,
    STOP_WAIT,
    STOP_WALL_CLOCK,
    is_valid_stop_reason,
    resolve_host_precheck_stop,
    stop_for_error,
    stop_for_interrupted,
    stop_for_max_hops,
    stop_for_no_tools,
    stop_for_policy,
    stop_for_time_continue_declined,
    stop_for_wait,
    stop_for_wall_clock,
    stop_from_tool_result,
)
from elyra.moment.types import STOP_REASONS as MOMENT_STOP_REASONS


def test_stop_constants_match_moment_vocabulary():
    asserted = {
        STOP_NO_TOOLS,
        STOP_WAIT,
        STOP_BLOCKED,
        STOP_POLICY,
        STOP_TIME_CONTINUE_DECLINED,
        STOP_WALL_CLOCK,
        STOP_INTERRUPTED,
        STOP_ERROR,
        STOP_MAX_HOPS,
    }
    assert asserted == set(STOP_REASONS)
    assert STOP_REASONS == MOMENT_STOP_REASONS


def test_named_stop_helpers():
    assert stop_for_no_tools() == "no_tools"
    assert stop_for_wait() == "wait"
    assert stop_for_policy() == "policy"
    assert stop_for_time_continue_declined() == "time_continue_declined"
    assert stop_for_wall_clock() == "wall_clock"
    assert stop_for_interrupted() == "interrupted"
    assert stop_for_error() == "error"
    assert stop_for_max_hops() == "max_hops"


def test_is_valid_stop_reason():
    assert is_valid_stop_reason("wall_clock")
    assert not is_valid_stop_reason("nope")
    assert not is_valid_stop_reason("")


def test_stop_from_tool_result():
    assert stop_from_tool_result(ends_moment=False, stop_reason="wait") is None
    assert stop_from_tool_result(ends_moment=True, stop_reason="wait") == "wait"
    assert (
        stop_from_tool_result(ends_moment=True, stop_reason="blocked") == "blocked"
    )
    # ends_moment without valid reason → policy
    assert stop_from_tool_result(ends_moment=True, stop_reason=None) == "policy"
    assert stop_from_tool_result(ends_moment=True, stop_reason="bogus") == "policy"


def test_resolve_host_precheck_priority_wall_clock():
    reason = resolve_host_precheck_stop(
        wall_clock_exceeded=True,
        hop=999,
        max_tool_hops=200,
        time_continue_declined=True,
    )
    assert reason == STOP_WALL_CLOCK


def test_resolve_host_precheck_max_hops():
    assert (
        resolve_host_precheck_stop(hop=200, max_tool_hops=200) == STOP_MAX_HOPS
    )
    assert resolve_host_precheck_stop(hop=199, max_tool_hops=200) is None


def test_resolve_host_precheck_time_continue_declined():
    assert (
        resolve_host_precheck_stop(time_continue_declined=True)
        == STOP_TIME_CONTINUE_DECLINED
    )


def test_resolve_host_precheck_none_when_clear():
    assert resolve_host_precheck_stop() is None
