"""Token estimate and meal budget helpers (Phase 1).

Scope: ``len//4`` heuristic matching ``elyra.loop.context.estimate_tokens``;
section budget split after fixed system+orient cost.
In scope: pure math, no I/O.
Out of scope: multimodal content estimates (media expand is PR6).
"""

from __future__ import annotations

# Align with LoopSettings.sliding_input_tokens / DEFAULT_SLIDING_INPUT_TOKENS.
DEFAULT_MEAL_BUDGET_TOKENS = 50_000

# Summary share of the episodic channel before raw fill (design select_episodic).
EPISODIC_SUMMARY_SHARE = 0.70


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ``len(text) // 4`` (same as ``loop.context``)."""
    if not text:
        return 0
    return len(text) // 4


def split_memory_budget(
    budget_tokens: int,
    *,
    system_text: str = "",
    orient_text: str = "",
    episodic_fraction: float = 0.20,
) -> tuple[int, int, int]:
    """Split meal budget into fixed + episodic + temporal caps.

    Returns ``(fixed_tokens, episodic_cap, temporal_cap)`` where::

        fixed = estimate(system) + estimate(orient)
        remaining = max(0, budget - fixed)
        episodic_cap = int(remaining * episodic_fraction)
        temporal_cap = remaining - episodic_cap

    Phase 1 allocates the full post-orient residual to temporal + episodic only.
    """
    fixed = estimate_tokens(system_text) + estimate_tokens(orient_text)
    remaining = max(0, int(budget_tokens) - fixed)
    frac = max(0.0, min(1.0, float(episodic_fraction)))
    episodic_cap = int(remaining * frac)
    temporal_cap = remaining - episodic_cap
    return fixed, episodic_cap, temporal_cap


__all__ = [
    "DEFAULT_MEAL_BUDGET_TOKENS",
    "EPISODIC_SUMMARY_SHARE",
    "estimate_tokens",
    "split_memory_budget",
]
