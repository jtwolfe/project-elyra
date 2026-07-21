"""Tests for time-based continue inject and wall-clock policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from elyra.loop.continue_policy import (
    DEFAULT_CONTINUE_IDLE_MINUTES,
    DEFAULT_CONTINUE_MAX_INJECTS,
    DEFAULT_MOMENT_WALL_CLOCK_MINUTES,
    continue_host_message,
    should_inject_continue,
    should_stop_time_continue_declined,
    should_stop_wall_clock,
)
from elyra.settings import LoopSettings, default_settings


def test_defaults_match_settings():
    s = default_settings().loop
    assert DEFAULT_CONTINUE_IDLE_MINUTES == s.continue_idle_minutes == 8
    assert DEFAULT_CONTINUE_MAX_INJECTS == s.continue_max_injects == 3
    assert DEFAULT_MOMENT_WALL_CLOCK_MINUTES == s.moment_wall_clock_minutes == 45


def test_continue_host_message_format():
    text = continue_host_message(8)
    assert text.startswith("HOST:")
    assert "8 minutes idle" in text
    assert "continue" in text


def test_should_inject_continue_when_idle_and_budget():
    last = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    now = last + timedelta(minutes=8)
    assert should_inject_continue(last, injects_so_far=0, now=now) is True
    assert should_inject_continue(last, injects_so_far=2, now=now) is True


def test_should_not_inject_before_idle_threshold():
    last = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    now = last + timedelta(minutes=7, seconds=59)
    assert should_inject_continue(last, injects_so_far=0, now=now) is False


def test_continue_injects_capped():
    last = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    now = last + timedelta(minutes=60)
    # Default max injects = 3 → injects_so_far 0..2 ok, 3+ blocked
    assert should_inject_continue(last, injects_so_far=0, now=now) is True
    assert should_inject_continue(last, injects_so_far=2, now=now) is True
    assert should_inject_continue(last, injects_so_far=3, now=now) is False
    assert should_inject_continue(last, injects_so_far=10, now=now) is False


def test_continue_max_injects_override():
    last = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    now = last + timedelta(minutes=30)
    assert (
        should_inject_continue(
            last, injects_so_far=1, now=now, continue_max_injects=1
        )
        is False
    )
    assert (
        should_inject_continue(
            last, injects_so_far=0, now=now, continue_max_injects=1
        )
        is True
    )


def test_should_stop_time_continue_declined_when_capped_and_idle():
    last = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    now = last + timedelta(minutes=8)
    assert (
        should_stop_time_continue_declined(last, injects_so_far=3, now=now) is True
    )
    # Under cap → do not stop (may inject instead)
    assert (
        should_stop_time_continue_declined(last, injects_so_far=2, now=now) is False
    )
    # Capped but not yet idle again
    soon = last + timedelta(minutes=1)
    assert (
        should_stop_time_continue_declined(last, injects_so_far=3, now=soon) is False
    )


def test_wall_clock_stop():
    started = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
    # Default 45 minutes
    assert (
        should_stop_wall_clock(started, started + timedelta(minutes=44)) is False
    )
    assert (
        should_stop_wall_clock(started, started + timedelta(minutes=45)) is True
    )
    assert (
        should_stop_wall_clock(started, started + timedelta(minutes=100)) is True
    )


def test_wall_clock_custom_minutes():
    started = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
    now = started + timedelta(minutes=10)
    assert (
        should_stop_wall_clock(started, now, moment_wall_clock_minutes=5) is True
    )
    assert (
        should_stop_wall_clock(started, now, moment_wall_clock_minutes=15) is False
    )


def test_policy_reads_loop_settings():
    settings = LoopSettings(
        continue_idle_minutes=2,
        continue_max_injects=1,
        moment_wall_clock_minutes=10,
    )
    last = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
    now = last + timedelta(minutes=2)
    assert should_inject_continue(last, 0, now, settings=settings) is True
    assert should_inject_continue(last, 1, now, settings=settings) is False
    assert should_stop_time_continue_declined(last, 1, now, settings=settings)
    assert should_stop_wall_clock(
        last, last + timedelta(minutes=10), settings=settings
    )
