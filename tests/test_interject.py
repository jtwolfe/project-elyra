"""Interjection buffer unit tests (PR12b)."""

from __future__ import annotations

from elyra.presence.interject import (
    INTERJECT_MAX_CHARS,
    INTERJECT_MAX_MESSAGES,
    REASON_BUFFER_FULL,
    InterjectBuffer,
    InterjectItem,
)


def test_try_add_and_drain():
    buf = InterjectBuffer()
    ok, reason = buf.try_add(InterjectItem("hello", "operator", "m1"))
    assert ok is True
    assert reason is None
    assert buf.depth == 1
    assert buf.chars == len("hello")

    items = buf.drain()
    assert len(items) == 1
    assert items[0].content == "hello"
    assert items[0].message_id == "m1"
    assert buf.depth == 0
    assert buf.chars == 0


def test_message_cap_full():
    buf = InterjectBuffer(max_messages=2, max_chars=10_000)
    assert buf.try_add(InterjectItem("a", "u"))[0] is True
    assert buf.try_add(InterjectItem("b", "u"))[0] is True
    ok, reason = buf.try_add(InterjectItem("c", "u"))
    assert ok is False
    assert reason == REASON_BUFFER_FULL
    assert buf.depth == 2


def test_char_cap_full():
    buf = InterjectBuffer(max_messages=8, max_chars=10)
    assert buf.try_add(InterjectItem("12345", "u"))[0] is True
    ok, reason = buf.try_add(InterjectItem("123456", "u"))  # would be 11
    assert ok is False
    assert reason == REASON_BUFFER_FULL
    assert buf.depth == 1


def test_clear():
    buf = InterjectBuffer()
    buf.try_add(InterjectItem("x", "u"))
    buf.clear()
    assert buf.depth == 0
    assert buf.chars == 0


def test_default_caps_match_design():
    assert INTERJECT_MAX_MESSAGES == 8
    assert INTERJECT_MAX_CHARS == 16_000
    buf = InterjectBuffer()
    assert buf.max_messages == 8
    assert buf.max_chars == 16_000
