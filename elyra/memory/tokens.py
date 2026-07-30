"""Token estimate and meal budget helpers (Phase 1–2a + glass-tail).

Scope: ``len//4`` heuristic matching ``elyra.loop.context.estimate_tokens``;
section budget split after fixed system+orient cost.
In scope: pure math, no I/O; ``split_memory_budget`` (Phase 1),
``split_memory_budget_v2`` (Phase 2 semantic), ``split_memory_budget_v3``
(Phase 2a directed_keep + temporal floor), ``split_memory_budget_v4``
(glass-tail residual share + clamp; v3-identical when inactive).
Out of scope: multimodal content estimates (media expand lives in meal).
"""

from __future__ import annotations

# Align with LoopSettings.sliding_input_tokens / DEFAULT_SLIDING_INPUT_TOKENS.
# Product path uses runtime meal_budget fraction (default 0.5 → 250k @ 500k).
DEFAULT_MEAL_BUDGET_TOKENS = 250_000

# Summary share of the episodic channel before raw fill (design select_episodic).
EPISODIC_SUMMARY_SHARE = 0.70


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ``len(text) // 4`` (same as ``loop.context``)."""
    if not text:
        return 0
    return len(text) // 4


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


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
    frac = _clamp01(episodic_fraction)
    episodic_cap = int(remaining * frac)
    temporal_cap = remaining - episodic_cap
    return fixed, episodic_cap, temporal_cap


def split_memory_budget_v2(
    budget_tokens: int,
    *,
    system_text: str = "",
    orient_text: str = "",
    semantic_enabled: bool = False,
    semantic_fraction: float = 0.12,
    episodic_fraction: float = 0.20,
    episodic_fraction_with_semantic: float = 0.18,
    temporal_min_fraction: float = 0.55,
) -> tuple[int, int, int, int]:
    """Split meal budget into fixed + semantic + episodic + temporal caps.

    Returns ``(fixed, semantic_cap, episodic_cap, temporal_cap)``.

    When ``semantic_enabled`` is false, delegates to :func:`split_memory_budget`
    Phase 1 math exactly (``semantic_cap=0``).

    When semantic is on, applies ``semantic_fraction`` and
    ``episodic_fraction_with_semantic``, then enforces a temporal floor
    (``temporal_min_fraction`` of remaining): deficit is taken from semantic
    first, then episodic (KD20). Invariant after clamp::

        semantic_cap + episodic_cap + temporal_cap == remaining
    """
    fixed = estimate_tokens(system_text) + estimate_tokens(orient_text)
    remaining = max(0, int(budget_tokens) - fixed)

    if not semantic_enabled or remaining == 0:
        _f, epi, temp = split_memory_budget(
            budget_tokens,
            system_text=system_text,
            orient_text=orient_text,
            episodic_fraction=episodic_fraction,
        )
        # Re-derive remaining from Phase 1 path so math matches exactly.
        return _f, 0, epi, temp

    sem_f = _clamp01(semantic_fraction)
    epi_f = _clamp01(episodic_fraction_with_semantic)
    t_min = _clamp01(temporal_min_fraction)

    semantic_cap = int(remaining * sem_f)
    episodic_cap = int(remaining * epi_f)
    temporal_cap = remaining - semantic_cap - episodic_cap

    # Floor enforcement — who loses first when temporal would go below floor:
    # 1) reduce semantic_cap  2) then episodic_cap  3) never cut temporal below
    # floor while remaining allows (KD20).
    floor = int(remaining * t_min)
    if temporal_cap < floor:
        deficit = floor - temporal_cap
        take = min(deficit, semantic_cap)
        semantic_cap -= take
        deficit -= take
        take = min(deficit, episodic_cap)
        episodic_cap -= take
        deficit -= take
        temporal_cap = remaining - semantic_cap - episodic_cap
        # If still under floor (t_min + fractions impossible), give all
        # residual to temporal; semantic_cap=episodic_cap=0.
        if temporal_cap < floor:
            semantic_cap = 0
            episodic_cap = 0
            temporal_cap = remaining

    return fixed, semantic_cap, episodic_cap, temporal_cap


def split_memory_budget_v3(
    budget_tokens: int,
    *,
    system_text: str = "",
    orient_text: str = "",
    semantic_enabled: bool = False,
    directed_keep_active: bool = False,
    semantic_fraction: float = 0.12,
    directed_keep_fraction: float = 0.08,
    episodic_fraction: float = 0.20,
    episodic_fraction_with_semantic: float = 0.18,
    temporal_min_fraction: float = 0.55,
) -> tuple[int, int, int, int, int]:
    """Split meal budget: fixed + semantic + directed_keep + episodic + temporal.

    Returns
    ``(fixed, semantic_cap, directed_keep_cap, episodic_cap, temporal_cap)``.

    When ``directed_keep_active`` is false, delegates to
    :func:`split_memory_budget_v2` bit-identically (``directed_keep_cap=0``).
    Active means flag on **and** a non-empty last-confirmed keep-set (caller).

    When active, applies ``directed_keep_fraction`` of residual R. Episodic
    uses ``episodic_fraction_with_semantic`` when semantic is also on, else
    Phase-1 ``episodic_fraction``. Temporal floor cut order (KD-A7)::

        semantic → directed_keep → episodic

    Invariant after clamp::

        semantic + directed_keep + episodic + temporal == remaining
    """
    if not directed_keep_active:
        fixed, sem, epi, temp = split_memory_budget_v2(
            budget_tokens,
            system_text=system_text,
            orient_text=orient_text,
            semantic_enabled=semantic_enabled,
            semantic_fraction=semantic_fraction,
            episodic_fraction=episodic_fraction,
            episodic_fraction_with_semantic=episodic_fraction_with_semantic,
            temporal_min_fraction=temporal_min_fraction,
        )
        return fixed, sem, 0, epi, temp

    fixed = estimate_tokens(system_text) + estimate_tokens(orient_text)
    remaining = max(0, int(budget_tokens) - fixed)
    if remaining == 0:
        return fixed, 0, 0, 0, 0

    dk_f = _clamp01(directed_keep_fraction)
    t_min = _clamp01(temporal_min_fraction)

    if semantic_enabled:
        semantic_cap = int(remaining * _clamp01(semantic_fraction))
        directed_keep_cap = int(remaining * dk_f)
        episodic_cap = int(remaining * _clamp01(episodic_fraction_with_semantic))
    else:
        semantic_cap = 0
        directed_keep_cap = int(remaining * dk_f)
        episodic_cap = int(remaining * _clamp01(episodic_fraction))

    temporal_cap = remaining - semantic_cap - directed_keep_cap - episodic_cap

    # Floor: cut supports semantic → directed_keep → episodic (never steal
    # temporal below floor while residual allows).
    floor = int(remaining * t_min)
    if temporal_cap < floor:
        deficit = floor - temporal_cap
        take = min(deficit, semantic_cap)
        semantic_cap -= take
        deficit -= take
        take = min(deficit, directed_keep_cap)
        directed_keep_cap -= take
        deficit -= take
        take = min(deficit, episodic_cap)
        episodic_cap -= take
        deficit -= take
        temporal_cap = (
            remaining - semantic_cap - directed_keep_cap - episodic_cap
        )
        if temporal_cap < floor:
            semantic_cap = 0
            directed_keep_cap = 0
            episodic_cap = 0
            temporal_cap = remaining

    return fixed, semantic_cap, directed_keep_cap, episodic_cap, temporal_cap


def split_memory_budget_v4(
    budget_tokens: int,
    *,
    system_text: str = "",
    orient_text: str = "",
    semantic_enabled: bool = False,
    directed_keep_active: bool = False,
    glass_tail_active: bool = False,
    glass_tail_fraction: float = 0.08,
    semantic_fraction: float = 0.12,
    directed_keep_fraction: float = 0.08,
    episodic_fraction: float = 0.20,
    episodic_fraction_with_semantic: float = 0.18,
    temporal_min_fraction: float = 0.55,
) -> tuple[int, int, int, int, int, int]:
    """Split meal budget: fixed + sem + dk + epi + glass_tail + temporal.

    Returns
    ``(fixed, semantic_cap, directed_keep_cap, episodic_cap,
    glass_tail_cap, temporal_cap)``.

    When ``glass_tail_active`` is false, delegates to
    :func:`split_memory_budget_v3` bit-identically (``glass_tail_cap=0``).

    When active, soft-allocates ``glass_tail_fraction`` of residual R, then
    enforces temporal floor by cutting supports in order::

        semantic → directed_keep → episodic → glass_tail_soft

    Message floor (≥N glass rows for social wakes) is **not** applied here —
    only token caps. ``compose_meal`` / ``select_glass_tail`` raise the
    effective glass-tail budget by stealing from supports when needed.

    Invariant after clamp when active::

        semantic + directed_keep + episodic + glass_tail + temporal == remaining
    """
    if not glass_tail_active:
        fixed, sem, dk, epi, temp = split_memory_budget_v3(
            budget_tokens,
            system_text=system_text,
            orient_text=orient_text,
            semantic_enabled=semantic_enabled,
            directed_keep_active=directed_keep_active,
            semantic_fraction=semantic_fraction,
            directed_keep_fraction=directed_keep_fraction,
            episodic_fraction=episodic_fraction,
            episodic_fraction_with_semantic=episodic_fraction_with_semantic,
            temporal_min_fraction=temporal_min_fraction,
        )
        return fixed, sem, dk, epi, 0, temp

    fixed = estimate_tokens(system_text) + estimate_tokens(orient_text)
    remaining = max(0, int(budget_tokens) - fixed)
    if remaining == 0:
        return fixed, 0, 0, 0, 0, 0

    gt_f = _clamp01(glass_tail_fraction)
    dk_f = _clamp01(directed_keep_fraction)
    t_min = _clamp01(temporal_min_fraction)

    # Soft allocate from residual (same fractions as v3 for sem/dk/epi).
    if semantic_enabled:
        semantic_cap = int(remaining * _clamp01(semantic_fraction))
        episodic_cap = int(remaining * _clamp01(episodic_fraction_with_semantic))
    else:
        semantic_cap = 0
        episodic_cap = int(remaining * _clamp01(episodic_fraction))

    directed_keep_cap = (
        int(remaining * dk_f) if directed_keep_active else 0
    )
    glass_tail_cap = int(remaining * gt_f)
    temporal_cap = (
        remaining
        - semantic_cap
        - directed_keep_cap
        - episodic_cap
        - glass_tail_cap
    )

    # Temporal floor clamp: cut supports semantic → dk → epi → glass_tail_soft.
    floor = int(remaining * t_min)
    if temporal_cap < floor:
        deficit = floor - temporal_cap
        take = min(deficit, semantic_cap)
        semantic_cap -= take
        deficit -= take
        take = min(deficit, directed_keep_cap)
        directed_keep_cap -= take
        deficit -= take
        take = min(deficit, episodic_cap)
        episodic_cap -= take
        deficit -= take
        take = min(deficit, glass_tail_cap)
        glass_tail_cap -= take
        deficit -= take
        temporal_cap = (
            remaining
            - semantic_cap
            - directed_keep_cap
            - episodic_cap
            - glass_tail_cap
        )
        if temporal_cap < floor:
            # Residual cannot satisfy floor with supports — all to temporal.
            semantic_cap = 0
            directed_keep_cap = 0
            episodic_cap = 0
            glass_tail_cap = 0
            temporal_cap = remaining

    # Identity: adjust temporal last for any rounding drift.
    temporal_cap = (
        remaining
        - semantic_cap
        - directed_keep_cap
        - episodic_cap
        - glass_tail_cap
    )
    return (
        fixed,
        semantic_cap,
        directed_keep_cap,
        episodic_cap,
        glass_tail_cap,
        temporal_cap,
    )


__all__ = [
    "DEFAULT_MEAL_BUDGET_TOKENS",
    "EPISODIC_SUMMARY_SHARE",
    "estimate_tokens",
    "split_memory_budget",
    "split_memory_budget_v2",
    "split_memory_budget_v3",
    "split_memory_budget_v4",
]
