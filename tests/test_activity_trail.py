"""Glass activity trail: compact beat events + live status snapshot fields."""

from __future__ import annotations

from elyra.presence.worker import (
    _activity_headline,
    compact_activity_event,
)


def test_compact_model_with_tool_calls() -> None:
    ev = compact_activity_event(
        {
            "type": "model",
            "hop": 2,
            "content": "",
            "tool_calls": [{"name": "web_search"}, {"name": "calculator"}],
        }
    )
    assert ev is not None
    assert ev["kind"] == "model_tools"
    assert "web_search" in ev["label"]
    assert ev["tools"] == ["web_search", "calculator"]


def test_compact_model_speak() -> None:
    ev = compact_activity_event(
        {"type": "model", "hop": 3, "content": "Hello there", "tool_calls": []}
    )
    assert ev is not None
    assert ev["kind"] == "model"
    assert ev["label"] == "speak"


def test_compact_tool_ok_and_err() -> None:
    ok = compact_activity_event({"type": "tool", "name": "calculator", "ok": True})
    assert ok is not None
    assert ok["kind"] == "tool"
    assert ok["label"] == "calculator"

    err = compact_activity_event(
        {
            "type": "tool",
            "name": "ghost",
            "ok": False,
            "error_reason": "module_not_found",
        }
    )
    assert err is not None
    assert err["kind"] == "tool_err"
    assert "ghost" in err["label"]


def test_headline_waiting_and_tool_sequence() -> None:
    waiting = _activity_headline(
        phase="waiting",
        recent=[],
        pending_wait={"prompt": "Confirm host cleanup please"},
        hop_count=1,
        last_tool=None,
    )
    assert waiting["state"] == "waiting"
    assert waiting["label"] == "waiting for you"
    assert "Confirm" in waiting["detail"]

    recent = [
        compact_activity_event(
            {
                "type": "model",
                "hop": 1,
                "tool_calls": [{"name": "calculator"}],
                "content": "",
            }
        ),
        compact_activity_event(
            {"type": "tool", "name": "calculator", "ok": True}
        ),
    ]
    recent = [e for e in recent if e is not None]
    after_tool = _activity_headline(
        phase="in_moment",
        recent=recent,
        pending_wait=None,
        hop_count=1,
        last_tool="calculator",
    )
    assert after_tool["state"] == "after_tool"
    assert "calculator" in after_tool["label"]
    assert "thinking" in after_tool["detail"]
