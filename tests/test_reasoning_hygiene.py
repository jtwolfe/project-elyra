"""Hermetic tests for channel-protocol marker hygiene (pure strip/detect).

No do-loop wire, no live model — pure module only (PR1).
"""

from __future__ import annotations

from elyra.llm.client import ChatCompletionResult
from elyra.llm.reasoning_hygiene import (
    CHANNEL_FLOOD_MIN_MARKERS,
    ChannelHygieneReport,
    channel_marker_count,
    is_channel_flood,
    sanitize_completion,
    strip_channel_markers,
)


def _result(
    content: str = "",
    reasoning_content: str = "",
    *,
    finish_reason: str | None = None,
) -> ChatCompletionResult:
    return ChatCompletionResult(
        content=content,
        reasoning_content=reasoning_content,
        raw_json="{}",
        finish_reason=finish_reason,
    )


# --- pure strip / detect -------------------------------------------------


def test_strip_keeps_prose_before_flood() -> None:
    prose = "Plan:\n1. List the root directory.\n2. Test tools."
    flood = "\n".join(["<|channel>thought"] * 20)
    cleaned = strip_channel_markers(prose + "\n" + flood)
    assert "<|channel>" not in cleaned
    assert "List the root directory" in cleaned
    assert "Test tools" in cleaned


def test_strip_pure_flood_is_empty() -> None:
    flood = "\n".join(["<|channel>thought"] * 50)
    assert strip_channel_markers(flood) == ""


def test_strip_single_trailer() -> None:
    text = "A public reply is ready.\n---\n<|channel>thought\n"
    cleaned = strip_channel_markers(text)
    assert cleaned == "A public reply is ready.\n---"
    assert channel_marker_count(text) == 1
    assert not is_channel_flood(text)


def test_strip_interleaved_garbage_variant() -> None:
    # Observed in content-field floods: <|channel>thought<channel|><|channel>thought
    raw = "<|channel>thought<channel|><|channel>thought<channel|>good left?\nmore"
    cleaned = strip_channel_markers(raw)
    assert "<|channel>" not in cleaned
    assert "<channel" not in cleaned.lower()
    assert "good left?" in cleaned
    assert "more" in cleaned


def test_strip_pipe_before_close_variant() -> None:
    raw = "Keep this.<|channel|>thought\n<|channel|>thought\n"
    cleaned = strip_channel_markers(raw)
    assert cleaned == "Keep this."
    assert channel_marker_count(raw) == 2


def test_strip_does_not_eat_normal_english() -> None:
    text = (
        "I thought about the channel carefully. "
        "Thoughtful design keeps private reasoning separate."
    )
    assert strip_channel_markers(text) == text
    assert channel_marker_count(text) == 0


def test_strip_none_and_empty() -> None:
    assert strip_channel_markers(None) == ""
    assert strip_channel_markers("") == ""
    assert channel_marker_count(None) == 0
    assert channel_marker_count("") == 0


def test_strip_idempotent() -> None:
    prose = "Useful plan.\n" + "\n".join(["<|channel>thought"] * 8)
    once = strip_channel_markers(prose)
    twice = strip_channel_markers(once)
    assert once == twice
    assert "<|channel>" not in once
    assert "Useful plan" in once


def test_flood_threshold() -> None:
    few = "\n".join(["<|channel>thought"] * (CHANNEL_FLOOD_MIN_MARKERS - 1))
    many = "\n".join(["<|channel>thought"] * CHANNEL_FLOOD_MIN_MARKERS)
    assert not is_channel_flood(few)
    assert is_channel_flood(many)
    assert CHANNEL_FLOOD_MIN_MARKERS == 5


def test_sanitize_completion_both_fields() -> None:
    result = _result(
        content="hello\n" + "\n".join(["<|channel>thought"] * 6),
        reasoning_content="reason first\n" + "\n".join(["<|channel>thought"] * 10),
        finish_reason="length",
    )
    cleaned, report = sanitize_completion(result)
    assert cleaned.content == "hello"
    assert cleaned.reasoning_content == "reason first"
    assert report.any_flood
    assert report.content_flood
    assert report.reasoning_flood
    assert report.any_change
    assert report.any_markers
    # original unchanged (immutable via replace)
    assert "<|channel>" in result.content
    assert "<|channel>" in result.reasoning_content
    assert cleaned is not result


def test_sanitize_no_markers_returns_same_instance() -> None:
    result = _result(content="hello", reasoning_content="thinking")
    cleaned, report = sanitize_completion(result)
    assert cleaned is result
    assert not report.any_change
    assert not report.any_markers
    assert not report.any_flood


def test_sanitize_single_trailer_not_flood() -> None:
    result = _result(
        content="A public reply is ready.\n<|channel>thought\n",
        reasoning_content="",
        finish_reason="stop",
    )
    cleaned, report = sanitize_completion(result)
    assert cleaned.content == "A public reply is ready."
    assert report.content_changed
    assert not report.content_flood
    assert report.original_content_markers == 1


def test_sanitize_pure_flood_fail_closed() -> None:
    flood = "\n".join(["<|channel>thought"] * 50)
    result = _result(content="", reasoning_content=flood, finish_reason="length")
    cleaned, report = sanitize_completion(result)
    assert cleaned.reasoning_content == ""
    assert report.reasoning_flood
    assert report.reasoning_changed


def test_channel_hygiene_report_properties() -> None:
    report = ChannelHygieneReport(
        original_content_markers=0,
        original_reasoning_markers=0,
        content_changed=False,
        reasoning_changed=False,
        content_flood=False,
        reasoning_flood=False,
    )
    assert not report.any_markers
    assert not report.any_flood
    assert not report.any_change

    report2 = ChannelHygieneReport(
        original_content_markers=3,
        original_reasoning_markers=6,
        content_changed=True,
        reasoning_changed=True,
        content_flood=False,
        reasoning_flood=True,
    )
    assert report2.any_markers
    assert report2.any_flood
    assert report2.any_change
