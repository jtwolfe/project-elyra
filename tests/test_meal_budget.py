"""Meal budget: fraction of model window, runtime JSON, effective tokens."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from elyra.llm.constants import MODEL_CONTEXT_WINDOW_TOKENS
from elyra.runtime.meal_budget import (
    DEFAULT_FRACTION,
    DEFAULT_MAX_FRACTION,
    HARD_MAX_FRACTION,
    MAX_FRACTION,
    MIN_FRACTION,
    MealBudgetState,
    apply_max_meal_override_percent,
    clamp_fraction,
    clamp_max_fraction,
    effective_meal_budget_tokens,
    load_meal_budget_runtime,
    meal_budget_runtime_path,
    meal_budget_status_block,
    save_meal_budget_runtime,
    tokens_for,
)
from elyra.settings import default_settings


def test_defaults_half_of_500k() -> None:
    s = MealBudgetState()
    assert s.fraction == DEFAULT_FRACTION == 0.5
    assert s.max_fraction == DEFAULT_MAX_FRACTION == 0.75
    assert MAX_FRACTION == DEFAULT_MAX_FRACTION
    assert tokens_for(0.5, 500_000) == 250_000
    assert tokens_for(DEFAULT_FRACTION, MODEL_CONTEXT_WINDOW_TOKENS) == 250_000


def test_clamp_band_default_max_75() -> None:
    assert clamp_fraction(0.01) == MIN_FRACTION == 0.10
    assert clamp_fraction(0.99) == DEFAULT_MAX_FRACTION == 0.75
    assert clamp_fraction(0.35) == 0.35
    assert tokens_for(0.01, 500_000) == tokens_for(0.10, 500_000)
    assert tokens_for(0.99, 500_000) == tokens_for(0.75, 500_000) == 375_000


def test_clamp_with_override_to_100() -> None:
    assert clamp_fraction(0.99, max_fraction=1.0) == 0.99
    assert clamp_fraction(1.2, max_fraction=1.0) == HARD_MAX_FRACTION == 1.0
    assert tokens_for(1.0, 500_000, max_fraction=1.0) == 500_000


def test_tokens_for_rounds() -> None:
    # 0.33 * 500_000 = 165_000 exact
    assert tokens_for(0.33, 500_000) == 165_000
    # odd window: round half-up style via Python round
    assert tokens_for(0.5, 100_001) == max(1, round(0.5 * 100_001))


def test_load_save_roundtrip(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    save_meal_budget_runtime(data, fraction=0.4)
    loaded = load_meal_budget_runtime(data)
    assert loaded.fraction == 0.4
    assert loaded.max_fraction == DEFAULT_MAX_FRACTION
    block = meal_budget_status_block(loaded, model_window=500_000)
    assert block["fraction"] == 0.4
    assert block["meal_budget_tokens"] == 200_000
    assert block["model_window_tokens"] == 500_000
    assert block["min_fraction"] == 0.10
    assert block["max_fraction"] == 0.75
    assert block["default_max_fraction"] == 0.75
    assert block["hard_max_fraction"] == 1.0
    assert block["max_override_active"] is False
    assert block["default_fraction"] == 0.5


def test_save_max_fraction_merge_preserves_fraction(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    save_meal_budget_runtime(data, fraction=0.4)
    save_meal_budget_runtime(data, max_fraction=1.0)
    loaded = load_meal_budget_runtime(data)
    assert loaded.fraction == 0.4
    assert loaded.max_fraction == 1.0


def test_apply_max_meal_override_percent(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    save_meal_budget_runtime(data, fraction=0.5)
    state = apply_max_meal_override_percent(data, 100)
    assert state.max_fraction == 1.0
    assert state.fraction == 0.5
    block = meal_budget_status_block(state, model_window=500_000)
    assert block["max_override_active"] is True
    assert block["max_fraction"] == 1.0


def test_apply_max_meal_override_rejects_oob() -> None:
    with pytest.raises(ValueError, match="1 and 100"):
        apply_max_meal_override_percent(Path("/tmp"), 0)
    with pytest.raises(ValueError, match="1 and 100"):
        apply_max_meal_override_percent(Path("/tmp"), 101)


def test_missing_file_uses_product_default(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    loaded = load_meal_budget_runtime(data)
    assert loaded.fraction == 0.5
    assert loaded.max_fraction == 0.75
    assert tokens_for(loaded.fraction, 500_000) == 250_000


def test_corrupt_json_falls_back_to_default(tmp_path: Path) -> None:
    data = tmp_path / "data"
    path = data / "runtime" / "meal_budget.json"
    path.parent.mkdir(parents=True)
    path.write_text("not-json{", encoding="utf-8")
    loaded = load_meal_budget_runtime(data)
    assert loaded.fraction == DEFAULT_FRACTION
    assert loaded.max_fraction == DEFAULT_MAX_FRACTION


def test_effective_budget_uses_fraction_not_frozen_sliding() -> None:
    """Policy A: fraction 0.5 → 250k even if settings sliding/in_turn were 50k."""
    settings = default_settings()
    state = MealBudgetState(fraction=0.5)
    tokens = effective_meal_budget_tokens(settings, state)
    assert tokens == 250_000
    assert tokens == tokens_for(0.5, settings.loop.model_context_window_tokens)

    state_low = MealBudgetState(fraction=0.1)
    assert effective_meal_budget_tokens(settings, state_low) == 50_000

    state_high = MealBudgetState(fraction=0.75)
    assert effective_meal_budget_tokens(settings, state_high) == 375_000

    state_full = MealBudgetState(fraction=1.0, max_fraction=1.0)
    assert effective_meal_budget_tokens(settings, state_full) == 500_000


def test_status_block_exposes_clamp_bounds() -> None:
    block = meal_budget_status_block(MealBudgetState(), model_window=500_000)
    assert block["meal_budget_tokens"] == 250_000
    assert set(block) >= {
        "fraction",
        "meal_budget_tokens",
        "model_window_tokens",
        "min_fraction",
        "max_fraction",
        "default_fraction",
        "default_max_fraction",
        "hard_max_fraction",
        "max_override_active",
    }


def test_clamp_rejects_nan_and_inf() -> None:
    with pytest.raises(ValueError, match="finite"):
        clamp_fraction(float("nan"))
    with pytest.raises(ValueError, match="finite"):
        clamp_fraction(float("inf"))
    with pytest.raises(ValueError, match="finite"):
        tokens_for(float("nan"), 500_000)
    with pytest.raises(ValueError, match="finite"):
        clamp_max_fraction(float("nan"))


def test_clamp_rejects_bool() -> None:
    with pytest.raises(TypeError, match="bool"):
        clamp_fraction(True)
    with pytest.raises(TypeError, match="bool"):
        clamp_fraction(False)


def test_load_non_dict_json_uses_default(tmp_path: Path) -> None:
    data = tmp_path / "data"
    path = meal_budget_runtime_path(data)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([0.4]), encoding="utf-8")
    loaded = load_meal_budget_runtime(data)
    assert loaded.fraction == DEFAULT_FRACTION


def test_load_bool_fraction_ignored(tmp_path: Path) -> None:
    data = tmp_path / "data"
    path = meal_budget_runtime_path(data)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"fraction": True}), encoding="utf-8")
    loaded = load_meal_budget_runtime(data)
    assert loaded.fraction == DEFAULT_FRACTION


def test_load_out_of_band_fraction_clamped(tmp_path: Path) -> None:
    data = tmp_path / "data"
    path = meal_budget_runtime_path(data)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"fraction": 0.99}), encoding="utf-8")
    loaded = load_meal_budget_runtime(data)
    assert loaded.fraction == DEFAULT_MAX_FRACTION
    path.write_text(json.dumps({"fraction": 0.01}), encoding="utf-8")
    loaded = load_meal_budget_runtime(data)
    assert loaded.fraction == MIN_FRACTION


def test_load_max_fraction_from_disk(tmp_path: Path) -> None:
    data = tmp_path / "data"
    path = meal_budget_runtime_path(data)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"fraction": 0.9, "max_fraction": 1.0}),
        encoding="utf-8",
    )
    loaded = load_meal_budget_runtime(data)
    assert loaded.max_fraction == 1.0
    assert loaded.fraction == 0.9
