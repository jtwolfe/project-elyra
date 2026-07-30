"""Meal budget — fraction of model context window for product meals.

Primary knob: ``meal_budget_fraction`` of ``model_context_window_tokens``.
Derived: ``meal_budget_tokens = max(1, round(fraction × model_window))`` after
fraction is clamped to the product band.

Default fraction **0.5** → **250k** when the model window is 500k.
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
MAX_FRACTION = 0.60


@dataclass
class MealBudgetState:
    """Runtime meal budget as a fraction of the model context window."""

    fraction: float = DEFAULT_FRACTION


def clamp_fraction(value: float | int) -> float:
    """Clamp to the allowed [0.10, 0.60] band (product UX).

    Rejects non-finite values (NaN/Inf) with ``ValueError`` so callers fail
    cleanly instead of producing NaN token math.
    """
    if isinstance(value, bool):
        # bool is a subclass of int; never treat True/False as 1.0/0.0.
        raise TypeError("fraction must be a number, not bool")
    v = float(value)
    if not math.isfinite(v):
        raise ValueError("fraction must be a finite number")
    if v < MIN_FRACTION:
        return MIN_FRACTION
    if v > MAX_FRACTION:
        return MAX_FRACTION
    return v


def tokens_for(fraction: float | int, model_window: int) -> int:
    """Derive meal budget tokens: round(clamped_fraction × model_window)."""
    frac = clamp_fraction(fraction)
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
    return tokens_for(state.fraction, window)


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
    frac = clamp_fraction(state.fraction)
    tokens = tokens_for(frac, window)
    return {
        "fraction": frac,
        "meal_budget_tokens": tokens,
        "model_window_tokens": window,
        "min_fraction": MIN_FRACTION,
        "max_fraction": MAX_FRACTION,
        "default_fraction": DEFAULT_FRACTION,
    }


def meal_budget_runtime_path(data_dir: Path) -> Path:
    return Path(data_dir) / MEAL_BUDGET_RUNTIME_REL


def load_meal_budget_runtime(data_dir: Path) -> MealBudgetState:
    """Load state from data/runtime/meal_budget.json; missing → product default 0.5."""
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
    if "fraction" in raw and raw["fraction"] is not None:
        try:
            state.fraction = clamp_fraction(raw["fraction"])
        except (TypeError, ValueError):
            # bool, non-finite, or non-numeric → keep product default
            pass
    return state


def save_meal_budget_runtime(
    data_dir: Path,
    *,
    fraction: float,
) -> Path:
    """Persist fraction; creates parent dirs. Always clamps before write."""
    path = meal_budget_runtime_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    frac = clamp_fraction(fraction)
    body = {
        "fraction": frac,
        "updated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    path.write_text(json.dumps(body, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
