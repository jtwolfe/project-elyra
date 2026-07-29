"""Edge weight model v1 for Phase 2a directed traversal (pure functions).

Scope: deterministic temporal + structural (+ cosine for soft hops);
Phase 3 multiplier hook is a no-op (always 1.0).
Out of scope: success learning, durable edge table, session budgets.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable, Mapping

from elyra.memory.types import parse_iso_z

# ── Kind constants (shared with graph.py) ──────────────────────────────────

EDGE_SEQUENTIAL = "sequential"
EDGE_PARENT_OF = "parent_of"
EDGE_CHILD_OF = "child_of"
EDGE_SAME_MOMENT = "same_moment"
EDGE_SEMANTIC_HOP = "semantic_hop"

EDGE_KINDS: frozenset[str] = frozenset(
    {
        EDGE_SEQUENTIAL,
        EDGE_PARENT_OF,
        EDGE_CHILD_OF,
        EDGE_SAME_MOMENT,
        EDGE_SEMANTIC_HOP,
    }
)

# ── Defaults (design weight model v1 table) ────────────────────────────────

BASE_SEQUENTIAL = 0.85
BASE_PARENT_CHILD = 0.90
BASE_SAME_MOMENT = 0.55
BASE_SEMANTIC_HOP = 0.70

DEFAULT_TEMPORAL_HALF_LIFE_HOURS = 72.0
DEFAULT_MIN_EXPAND_WEIGHT = 0.05

_BASE_BY_KIND: Mapping[str, float] = {
    EDGE_SEQUENTIAL: BASE_SEQUENTIAL,
    EDGE_PARENT_OF: BASE_PARENT_CHILD,
    EDGE_CHILD_OF: BASE_PARENT_CHILD,
    EDGE_SAME_MOMENT: BASE_SAME_MOMENT,
    EDGE_SEMANTIC_HOP: BASE_SEMANTIC_HOP,
}


def clamp01(value: float) -> float:
    """Clamp a float to ``[0.0, 1.0]``."""
    if value != value:  # NaN
        return 0.0
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def base_weight(edge_kind: str) -> float:
    """Prior base weight for ``edge_kind`` (unknown → 0.5)."""
    return float(_BASE_BY_KIND.get(edge_kind, 0.5))


def structural_bonus(edge_kind: str) -> float:
    """Structural multiplier for ``edge_kind`` (v1: all 1.0; bases hold priors).

    Extension hook for later adjacency / success-path tuning without changing
    the base table.
    """
    del edge_kind
    return 1.0


def temporal_decay(
    t_start: datetime | str | None,
    now: datetime | str,
    *,
    half_life_hours: float = DEFAULT_TEMPORAL_HALF_LIFE_HOURS,
) -> float:
    """Exponential decay on destination age: ``0.5 ** (age_h / half_life)``.

    Future timestamps (negative age) yield 1.0. Missing / unparsable ``t_start``
    yields 1.0 (no penalty). ``half_life_hours <= 0`` yields 1.0.
    """
    if half_life_hours <= 0:
        return 1.0
    if t_start is None:
        return 1.0
    try:
        dst = parse_iso_z(t_start)
        now_dt = parse_iso_z(now)
    except (TypeError, ValueError):
        return 1.0
    age_hours = (now_dt - dst).total_seconds() / 3600.0
    if age_hours <= 0:
        return 1.0
    return float(math.pow(0.5, age_hours / float(half_life_hours)))


def semantic_factor(
    edge_kind: str,
    cosine: float | None = None,
) -> float:
    """Cosine factor for ``semantic_hop``; 1.0 for structural kinds.

    Cosine is clamped to ``[0, 1]``. Missing cosine on semantic_hop → 0.0
    (drop unless caller supplies a score).
    """
    if edge_kind != EDGE_SEMANTIC_HOP:
        return 1.0
    if cosine is None:
        return 0.0
    return clamp01(float(cosine))


def phase3_multiplier(
    src_atom_id: str | None = None,
    dst_atom_id: str | None = None,
    edge_kind: str | None = None,
    trajectory_ctx: Any | None = None,
) -> float:
    """Phase 3 success-path multiplier. Always 1.0 in 2a (no-op)."""
    del src_atom_id, dst_atom_id, edge_kind, trajectory_ctx
    return 1.0


# Optional injectable multiplier (tests / future Phase 3 wiring).
Phase3MultiplierFn = Callable[
    [str | None, str | None, str | None, Any | None], float
]


def edge_weight(
    edge_kind: str,
    *,
    dst_t_start: datetime | str | None = None,
    now: datetime | str | None = None,
    cosine: float | None = None,
    half_life_hours: float = DEFAULT_TEMPORAL_HALF_LIFE_HOURS,
    src_atom_id: str | None = None,
    dst_atom_id: str | None = None,
    trajectory_ctx: Any | None = None,
    phase3_fn: Phase3MultiplierFn | None = None,
) -> float:
    """Full v1 weight: clamp01(base * decay * structural * semantic * phase3).

    When ``now`` is None, temporal decay is skipped (factor 1.0) so pure unit
    tests can ignore time without inventing a clock.
    """
    base = base_weight(edge_kind)
    if now is None:
        decay = 1.0
    else:
        decay = temporal_decay(
            dst_t_start, now, half_life_hours=half_life_hours
        )
    struct = structural_bonus(edge_kind)
    sem = semantic_factor(edge_kind, cosine)
    if phase3_fn is not None:
        p3 = float(
            phase3_fn(src_atom_id, dst_atom_id, edge_kind, trajectory_ctx)
        )
    else:
        p3 = phase3_multiplier(
            src_atom_id, dst_atom_id, edge_kind, trajectory_ctx
        )
    return clamp01(base * decay * struct * sem * p3)


def passes_min_weight(
    weight: float,
    *,
    min_weight: float = DEFAULT_MIN_EXPAND_WEIGHT,
) -> bool:
    """True when weight is at or above the expand floor."""
    return float(weight) >= float(min_weight)


__all__ = [
    "BASE_PARENT_CHILD",
    "BASE_SAME_MOMENT",
    "BASE_SEMANTIC_HOP",
    "BASE_SEQUENTIAL",
    "DEFAULT_MIN_EXPAND_WEIGHT",
    "DEFAULT_TEMPORAL_HALF_LIFE_HOURS",
    "EDGE_CHILD_OF",
    "EDGE_KINDS",
    "EDGE_PARENT_OF",
    "EDGE_SAME_MOMENT",
    "EDGE_SEMANTIC_HOP",
    "EDGE_SEQUENTIAL",
    "base_weight",
    "clamp01",
    "edge_weight",
    "passes_min_weight",
    "phase3_multiplier",
    "semantic_factor",
    "structural_bonus",
    "temporal_decay",
]
