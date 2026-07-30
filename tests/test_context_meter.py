"""Context meter + glass status shape (meal vs model window)."""

from __future__ import annotations

from elyra.llm.constants import (
    DEFAULT_SLIDING_INPUT_TOKENS,
    MODEL_CONTEXT_WINDOW_TOKENS,
)
from elyra.loop import context_meter
from elyra.settings import LoopSettings, default_settings


def setup_function() -> None:
    context_meter.reset_for_tests()


def test_model_window_constant_is_grok_class() -> None:
    assert MODEL_CONTEXT_WINDOW_TOKENS == 500_000
    assert DEFAULT_SLIDING_INPUT_TOKENS == 250_000
    assert DEFAULT_SLIDING_INPUT_TOKENS < MODEL_CONTEXT_WINDOW_TOKENS


def test_loop_settings_model_window_default() -> None:
    loop = LoopSettings()
    assert loop.model_context_window_tokens == MODEL_CONTEXT_WINDOW_TOKENS
    assert default_settings().loop.model_context_window_tokens == 500_000


def test_record_and_status_block_fractions() -> None:
    context_meter.record_meal(
        12_000,
        meal_budget_tokens=250_000,
        model_window_tokens=500_000,
        hop=3,
        moment_id="m-test",
    )
    snap = context_meter.status_block()
    assert snap["meal_used_tokens"] == 12_000
    assert snap["meal_budget_tokens"] == 250_000
    assert snap["model_window_tokens"] == 500_000
    assert abs(snap["meal_used_fraction"] - (12_000 / 250_000)) < 1e-9
    assert abs(snap["window_used_fraction"] - (12_000 / 500_000)) < 1e-9
    assert snap["hop"] == 3
    assert snap["moment_id"] == "m-test"


def test_status_block_overrides_budget_from_caller() -> None:
    context_meter.record_meal(1000, meal_budget_tokens=250_000, model_window_tokens=500_000)
    snap = context_meter.status_block(
        meal_budget_tokens=12_000,
        model_window_tokens=250_000,
    )
    assert snap["meal_budget_tokens"] == 12_000
    assert snap["model_window_tokens"] == 250_000
    assert abs(snap["meal_used_fraction"] - (1000 / 12_000)) < 1e-9
