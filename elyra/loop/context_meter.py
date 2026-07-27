"""Live meal token estimate for glass context rail.

Scope: thread-safe last assembled meal size (heuristic tokens) + budgets.
Out of scope: SuperGrok usage, provider billing, true tokenizer counts.
"""

from __future__ import annotations

import threading
from typing import Any

from elyra.llm.constants import (
    DEFAULT_SLIDING_INPUT_TOKENS,
    MODEL_CONTEXT_WINDOW_TOKENS,
)

_lock = threading.Lock()
_meal_used_tokens: int = 0
_meal_budget_tokens: int = DEFAULT_SLIDING_INPUT_TOKENS
_model_window_tokens: int = MODEL_CONTEXT_WINDOW_TOKENS
_hop: int | None = None
_moment_id: str | None = None


def record_meal(
    used_tokens: int,
    *,
    meal_budget_tokens: int | None = None,
    model_window_tokens: int | None = None,
    hop: int | None = None,
    moment_id: str | None = None,
) -> None:
    """Publish the last pre-call meal size (outer + chain)."""
    global _meal_used_tokens, _meal_budget_tokens, _model_window_tokens, _hop, _moment_id
    used = max(0, int(used_tokens))
    with _lock:
        _meal_used_tokens = used
        if meal_budget_tokens is not None:
            _meal_budget_tokens = max(1, int(meal_budget_tokens))
        if model_window_tokens is not None:
            _model_window_tokens = max(1, int(model_window_tokens))
        if hop is not None:
            _hop = int(hop)
        if moment_id is not None:
            _moment_id = moment_id


def status_block(
    *,
    meal_budget_tokens: int | None = None,
    model_window_tokens: int | None = None,
) -> dict[str, Any]:
    """Snapshot for ``GET /api/status`` ``context`` field (no secrets)."""
    with _lock:
        used = _meal_used_tokens
        budget = (
            max(1, int(meal_budget_tokens))
            if meal_budget_tokens is not None
            else _meal_budget_tokens
        )
        window = (
            max(1, int(model_window_tokens))
            if model_window_tokens is not None
            else _model_window_tokens
        )
        hop = _hop
        mid = _moment_id
    meal_frac = min(1.0, used / float(budget)) if budget > 0 else 0.0
    window_frac = min(1.0, used / float(window)) if window > 0 else 0.0
    return {
        "meal_used_tokens": used,
        "meal_budget_tokens": budget,
        "model_window_tokens": window,
        "meal_used_fraction": meal_frac,
        "window_used_fraction": window_frac,
        "hop": hop,
        "moment_id": mid,
    }


def reset_for_tests() -> None:
    """Clear meter state (unit tests only)."""
    global _meal_used_tokens, _meal_budget_tokens, _model_window_tokens, _hop, _moment_id
    with _lock:
        _meal_used_tokens = 0
        _meal_budget_tokens = DEFAULT_SLIDING_INPUT_TOKENS
        _model_window_tokens = MODEL_CONTEXT_WINDOW_TOKENS
        _hop = None
        _moment_id = None
