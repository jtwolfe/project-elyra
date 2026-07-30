"""why_now framing contracts (BUG-meal-03 S2 dual-write snippet)."""

from __future__ import annotations

from elyra.loop.orient_slice import BIAS_TALK, format_skill_bias
from elyra.presence.queue import WakeItem
from elyra.presence.worker import (
    _WHY_NOW_SNIPPET_MAX_CHARS,
    _snippet,
    _why_now,
)


def _wake(kind: str, **payload) -> WakeItem:
    return WakeItem(
        id="W1",
        kind=kind,
        priority=0,
        created_at="2026-07-30T08:00:00Z",
        payload=payload,
    )


def test_why_now_wait_reply_includes_user_snippet():
    """OQ7: wait_reply why_now dual-writes capped user content (not wait_id only)."""
    content = "what is the coolest thing you remember about rockets?"
    out = _why_now(
        _wake(
            "wait_reply",
            wait_id="c13ae60a-40ed-45c6-a75a-035c1a78f05c",
            content=content,
            message_id="04f85fc6-rockets",
        )
    )
    assert "wait reply (wait_id=c13ae60a-40ed-45c6-a75a-035c1a78f05c)" in out
    assert "rockets" in out
    assert content in out
    assert out == (
        "wait reply (wait_id=c13ae60a-40ed-45c6-a75a-035c1a78f05c): "
        + content
    )


def test_why_now_wait_reply_snippet_hard_capped():
    long = "R" * 500
    out = _why_now(_wake("wait_reply", wait_id="w-long", content=long))
    assert out.startswith("wait reply (wait_id=w-long): ")
    snippet = out.split(": ", 1)[1]
    assert len(snippet) <= _WHY_NOW_SNIPPET_MAX_CHARS
    assert snippet.endswith("…")
    assert len(snippet) == _WHY_NOW_SNIPPET_MAX_CHARS


def test_why_now_wait_reply_empty_content_wait_id_only():
    assert (
        _why_now(_wake("wait_reply", wait_id="w-empty", content=""))
        == "wait reply (wait_id=w-empty)"
    )
    assert (
        _why_now(_wake("wait_reply", wait_id="w-none"))
        == "wait reply (wait_id=w-none)"
    )
    assert (
        _why_now(_wake("wait_reply", wait_id="w-ws", content="   \n\t  "))
        == "wait reply (wait_id=w-ws)"
    )
    assert _why_now(_wake("wait_reply")) == "wait reply (wait_id=?)"


def test_why_now_wait_reply_collapses_whitespace():
    out = _why_now(
        _wake(
            "wait_reply",
            wait_id="w-ws",
            content="hello\n\n  rockets\tquestion",
        )
    )
    assert out == "wait reply (wait_id=w-ws): hello rockets question"


def test_snippet_helper_contract():
    assert _snippet(None) == ""
    assert _snippet("") == ""
    assert _snippet("  hi  ") == "hi"
    assert _snippet("x" * 10, max_chars=5) == "xxxx…"
    assert len(_snippet("y" * 300)) == _WHY_NOW_SNIPPET_MAX_CHARS


def test_wait_reply_bias_talk_still_present():
    """Dual-write complements skill bias; does not remove BIAS_TALK in v1."""
    assert format_skill_bias("wait_reply") == BIAS_TALK
    assert format_skill_bias("user_message") == BIAS_TALK


def test_why_now_other_kinds_unchanged():
    assert _why_now(_wake("user_message", user_id="alice")) == (
        "user message from alice"
    )
    assert _why_now(_wake("wait_timeout", wait_id="t1")) == (
        "wait timeout (wait_id=t1)"
    )
    assert _why_now(
        _wake("moment_continue", source_moment_id="M-abc")
    ) == "continue work (from moment M-abc)"
