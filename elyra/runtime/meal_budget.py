"""Meal budget — fraction of model context window for product meals.

Primary knob: ``meal_budget_fraction`` of ``model_context_window_tokens``.
Derived: ``meal_budget_tokens = max(1, round(fraction × model_window))`` after
fraction is clamped to the effective max band.

Default fraction **0.5** → **250k** when the model window is 500k.
Product **slider max** defaults to **0.75** (75% of model window). Operators
may raise the ceiling to **100%** via ``elyra start --max-meal-override 100``
(percent) or by setting ``max_fraction`` in ``data/runtime/meal_budget.json``.

Persisted in ``data/runtime/meal_budget.json`` (like dev_speed / semantic_wait).
Does not mutate frozen Settings / elyra.toml.

Product paths (policy A) apply the effective token budget to **both** sliding
and in-turn caps so raising the fraction is not stuck behind a 50k sibling.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyra.llm.constants import MODEL_CONTEXT_WINDOW_TOKENS

logger = logging.getLogger(__name__)

MEAL_BUDGET_RUNTIME_REL = Path("runtime") / "meal_budget.json"

# Product defaults / clamp band (fraction of model window).
DEFAULT_FRACTION = 0.5
MIN_FRACTION = 0.10
# Default ceiling for the Glass slider (75% of model window).
DEFAULT_MAX_FRACTION = 0.75
# Absolute hard ceiling when operator overrides (100% of model window).
HARD_MAX_FRACTION = 1.0

# Back-compat alias: product default max (not the hard override ceiling).
MAX_FRACTION = DEFAULT_MAX_FRACTION


@dataclass
class MealBudgetState:
    """Runtime meal budget as a fraction of the model context window.

    ``max_fraction`` is the **slider / clamp ceiling** (default 0.75). It may
    be raised up to ``HARD_MAX_FRACTION`` (1.0) via CLI override.
    """

    fraction: float = DEFAULT_FRACTION
    max_fraction: float = DEFAULT_MAX_FRACTION


def clamp_max_fraction(value: float | int) -> float:
    """Clamp a max-fraction ceiling to [MIN_FRACTION, HARD_MAX_FRACTION]."""
    if isinstance(value, bool):
        raise TypeError("max_fraction must be a number, not bool")
    v = float(value)
    if not math.isfinite(v):
        raise ValueError("max_fraction must be a finite number")
    if v < MIN_FRACTION:
        return MIN_FRACTION
    if v > HARD_MAX_FRACTION:
        return HARD_MAX_FRACTION
    return v


def clamp_fraction(
    value: float | int,
    *,
    max_fraction: float | None = None,
) -> float:
    """Clamp fraction to [MIN_FRACTION, max_fraction] (product UX).

    ``max_fraction`` defaults to ``DEFAULT_MAX_FRACTION`` (0.75). Rejects
    non-finite values (NaN/Inf) with ``ValueError``.
    """
    if isinstance(value, bool):
        # bool is a subclass of int; never treat True/False as 1.0/0.0.
        raise TypeError("fraction must be a number, not bool")
    v = float(value)
    if not math.isfinite(v):
        raise ValueError("fraction must be a finite number")
    ceiling = (
        clamp_max_fraction(max_fraction)
        if max_fraction is not None
        else DEFAULT_MAX_FRACTION
    )
    if v < MIN_FRACTION:
        return MIN_FRACTION
    if v > ceiling:
        return ceiling
    return v


def tokens_for(
    fraction: float | int,
    model_window: int,
    *,
    max_fraction: float | None = None,
) -> int:
    """Derive meal budget tokens: round(clamped_fraction × model_window)."""
    frac = clamp_fraction(fraction, max_fraction=max_fraction)
    window = max(1, int(model_window))
    return max(1, int(round(frac * window)))


def effective_meal_budget_tokens(
    settings: Any,
    state: MealBudgetState,
) -> int:
    """Product effective meal size from runtime fraction × model window.

    Reads ``settings.loop.model_context_window_tokens`` when present; falls
    back to ``MODEL_CONTEXT_WINDOW_TOKENS`` (500k). Does not use frozen
    ``sliding_input_tokens`` / ``in_turn_max_tokens`` — those remain settings
    fallbacks for unit tests and non-product callers.
    """
    loop = getattr(settings, "loop", None)
    raw_window = (
        getattr(loop, "model_context_window_tokens", None)
        if loop is not None
        else getattr(settings, "model_context_window_tokens", None)
    )
    try:
        window = int(raw_window) if raw_window is not None else MODEL_CONTEXT_WINDOW_TOKENS
    except (TypeError, ValueError):
        window = MODEL_CONTEXT_WINDOW_TOKENS
    ceiling = clamp_max_fraction(state.max_fraction)
    return tokens_for(state.fraction, window, max_fraction=ceiling)


def meal_budget_status_block(
    state: MealBudgetState,
    *,
    model_window: int | None = None,
) -> dict[str, Any]:
    """Build the ``meal_budget`` object for /api/status."""
    window = (
        max(1, int(model_window))
        if model_window is not None
        else MODEL_CONTEXT_WINDOW_TOKENS
    )
    ceiling = clamp_max_fraction(state.max_fraction)
    frac = clamp_fraction(state.fraction, max_fraction=ceiling)
    tokens = tokens_for(frac, window, max_fraction=ceiling)
    return {
        "fraction": frac,
        "meal_budget_tokens": tokens,
        "model_window_tokens": window,
        "min_fraction": MIN_FRACTION,
        "max_fraction": ceiling,
        "default_fraction": DEFAULT_FRACTION,
        "default_max_fraction": DEFAULT_MAX_FRACTION,
        "hard_max_fraction": HARD_MAX_FRACTION,
        "max_override_active": ceiling > DEFAULT_MAX_FRACTION + 1e-12,
    }


def meal_budget_runtime_path(data_dir: Path) -> Path:
    return Path(data_dir) / MEAL_BUDGET_RUNTIME_REL


def load_meal_budget_runtime(data_dir: Path) -> MealBudgetState:
    """Load state from data/runtime/meal_budget.json; missing → product defaults."""
    state = MealBudgetState()
    path = meal_budget_runtime_path(data_dir)
    if not path.is_file():
        return state
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("meal_budget runtime load failed (%s): %s", path, exc)
        return state
    if not isinstance(raw, dict):
        return state
    ceiling = DEFAULT_MAX_FRACTION
    if "max_fraction" in raw and raw["max_fraction"] is not None:
        try:
            ceiling = clamp_max_fraction(raw["max_fraction"])
            state.max_fraction = ceiling
        except (TypeError, ValueError):
            pass
    if "fraction" in raw and raw["fraction"] is not None:
        try:
            state.fraction = clamp_fraction(raw["fraction"], max_fraction=ceiling)
        except (TypeError, ValueError):
            # bool, non-finite, or non-numeric → keep product default fraction
            state.fraction = clamp_fraction(DEFAULT_FRACTION, max_fraction=ceiling)
    else:
        state.fraction = clamp_fraction(state.fraction, max_fraction=ceiling)
    return state


def save_meal_budget_runtime(
    data_dir: Path,
    *,
    fraction: float | None = None,
    max_fraction: float | None = None,
) -> Path:
    """Persist fraction and/or max_fraction; creates parent dirs.

    Load-merges existing file so sibling fields are not clobbered when only
    one of fraction / max_fraction is provided.
    """
    path = meal_budget_runtime_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    current = load_meal_budget_runtime(data_dir)
    ceiling = (
        clamp_max_fraction(max_fraction)
        if max_fraction is not None
        else clamp_max_fraction(current.max_fraction)
    )
    frac_src = fraction if fraction is not None else current.fraction
    frac = clamp_fraction(frac_src, max_fraction=ceiling)
    body = {
        "fraction": frac,
        "max_fraction": ceiling,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(body, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def apply_max_meal_override_percent(data_dir: Path, percent: float | int) -> MealBudgetState:
    """Apply CLI ``--max-meal-override PCT`` (1–100) and return loaded state.

    Persists ``max_fraction = percent/100`` (clamped to hard max). Current
    fraction is re-clamped under the new ceiling.
    """
    if isinstance(percent, bool):
        raise TypeError("max-meal-override must be a number, not bool")
    p = float(percent)
    if not math.isfinite(p):
        raise ValueError("max-meal-override must be a finite number")
    if p < 1.0 or p > 100.0:
        raise ValueError("max-meal-override must be between 1 and 100 (percent of model window)")
    max_frac = clamp_max_fraction(p / 100.0)
    save_meal_budget_runtime(data_dir, max_fraction=max_frac)
    return load_meal_budget_runtime(data_dir)
