"""Unit tests: headless Messages-style usage → TokenUsage adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.llm.usage import TokenUsage, UsageMeter, parse_token_usage
from elyra.settings import UsageSettings
from elyra.instrument.usage_bridge import (
    adapt_headless_usage,
    extract_usage_dict,
    messages_usage_to_token_usage,
    meter_allows_call,
    record_instrument_usage,
    usage_is_incomplete_flag,
)


# Fixture-shaped headless JSON (Messages-style fields from host docs).
HEADLESS_USAGE_FIXTURE = {
    "text": "done",
    "usage": {
        "input_tokens": 1200,
        "output_tokens": 340,
        "cache_read_input_tokens": 800,
        "cache_creation_input_tokens": 50,
        "total_tokens": 2390,
        "reasoning_tokens": 100,
    },
}

HEADLESS_INCOMPLETE_FIXTURE = {
    "text": "partial",
    "usage": {
        "input_tokens": 100,
        "output_tokens": 20,
        "total_tokens": 120,
    },
    "usage_is_incomplete": True,
}


def _settings(**kwargs: object) -> UsageSettings:
    base = dict(
        enabled=True,
        weekly_allowed_tokens=1_000_000,
        weekly_allowed_fraction=0.50,
        hour_block_minutes=60,
        day_allowed_tokens=None,
        hour_allowed_tokens=None,
    )
    base.update(kwargs)
    return UsageSettings(**base)  # type: ignore[arg-type]


def test_naive_parse_token_usage_misses_messages_prompt_fields() -> None:
    """OpenAI-only parser ignores input_tokens/output_tokens/cache_* mapping.

    It may still pick up total_tokens/reasoning_tokens when present, but
    prompt/completion/cached stay 0 — adapter is required for honesty.
    """
    raw = HEADLESS_USAGE_FIXTURE["usage"]
    naive = parse_token_usage(raw)
    # total_tokens key alone is enough for parse_token_usage to return a value,
    # but Messages field names for prompt/completion/cache are lost.
    if naive is not None:
        assert naive.prompt_tokens == 0
        assert naive.completion_tokens == 0
        assert naive.cached_tokens == 0
    # Without total_tokens, pure Messages buckets return None.
    messages_only = {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_input_tokens": 3,
    }
    assert parse_token_usage(messages_only) is None


def test_messages_usage_to_token_usage_maps_fields() -> None:
    u = messages_usage_to_token_usage(HEADLESS_USAGE_FIXTURE["usage"])
    assert u is not None
    assert u.prompt_tokens == 1200
    assert u.completion_tokens == 340
    assert u.total_tokens == 2390
    assert u.reasoning_tokens == 100
    assert u.cached_tokens == 850  # read + creation
    assert u.billable_tokens == 2390  # prefers total_tokens


def test_messages_usage_openai_passthrough() -> None:
    raw = {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    u = messages_usage_to_token_usage(raw)
    assert u is not None
    assert u.prompt_tokens == 10
    assert u.completion_tokens == 5
    assert u.total_tokens == 15


def test_messages_usage_missing_returns_none() -> None:
    assert messages_usage_to_token_usage(None) is None
    assert messages_usage_to_token_usage({}) is None
    assert messages_usage_to_token_usage({"foo": 1}) is None


def test_extract_usage_dict_nested_and_bare() -> None:
    assert extract_usage_dict(HEADLESS_USAGE_FIXTURE) == HEADLESS_USAGE_FIXTURE["usage"]
    bare = {"input_tokens": 1, "output_tokens": 2}
    assert extract_usage_dict(bare) == bare
    assert extract_usage_dict({"text": "x"}) is None


def test_usage_is_incomplete_flag() -> None:
    assert usage_is_incomplete_flag(HEADLESS_INCOMPLETE_FIXTURE) is True
    assert usage_is_incomplete_flag(HEADLESS_USAGE_FIXTURE) is False
    assert usage_is_incomplete_flag(None) is False


def test_adapt_headless_usage_complete() -> None:
    result = adapt_headless_usage(HEADLESS_USAGE_FIXTURE)
    assert result.usage is not None
    assert result.raw_present is True
    assert result.usage_incomplete is False
    assert result.usage_recorded is False
    assert result.payload_usage["total_tokens"] == 2390
    assert result.payload_usage["recorded"] is False


def test_adapt_headless_usage_incomplete() -> None:
    result = adapt_headless_usage(HEADLESS_INCOMPLETE_FIXTURE)
    assert result.usage is not None
    assert result.usage.prompt_tokens == 100
    assert result.usage_incomplete is True
    assert result.payload_usage.get("usage_incomplete") is True


def test_adapt_missing_usage_does_not_invent() -> None:
    result = adapt_headless_usage({"text": "no usage here"})
    assert result.usage is None
    assert result.usage_recorded is False
    assert result.payload_usage["recorded"] is False
    assert result.payload_usage["total_tokens"] == 0


def test_record_instrument_usage_into_meter(tmp_path: Path) -> None:
    meter = UsageMeter.load(tmp_path, _settings())
    before = meter.remaining()["week"]
    result = record_instrument_usage(meter, HEADLESS_USAGE_FIXTURE)
    assert result.usage_recorded is True
    assert result.payload_usage["recorded"] is True
    after = meter.remaining()["week"]
    assert before - after == 2390


def test_record_incomplete_still_records_known(tmp_path: Path) -> None:
    meter = UsageMeter.load(tmp_path, _settings())
    before = meter.remaining()["week"]
    result = record_instrument_usage(meter, HEADLESS_INCOMPLETE_FIXTURE)
    assert result.usage_recorded is True
    assert result.usage_incomplete is True
    assert before - meter.remaining()["week"] == 120


def test_record_missing_usage_no_invent(tmp_path: Path) -> None:
    meter = UsageMeter.load(tmp_path, _settings())
    before = meter.remaining()["week"]
    result = record_instrument_usage(meter, {"text": "x"})
    assert result.usage_recorded is False
    assert meter.remaining()["week"] == before


def test_record_without_meter_adapts_only() -> None:
    result = record_instrument_usage(None, HEADLESS_USAGE_FIXTURE)
    assert result.usage is not None
    assert result.usage_recorded is False


def test_meter_allows_call(tmp_path: Path) -> None:
    assert meter_allows_call(None) is True
    meter = UsageMeter.load(
        tmp_path,
        _settings(weekly_allowed_tokens=10, weekly_allowed_fraction=1.0),
    )
    # Exhaust week.
    meter.record(TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=100))
    assert meter.can_call() is False
    assert meter_allows_call(meter) is False
