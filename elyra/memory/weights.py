"""Edge weight model v1 for Phase 2a directed traversal (pure functions).

Scope: deterministic temporal + structural (+ cosine for soft hops / recalls);
Phase 3 multiplier hook is a no-op (always 1.0).
Durable kinds (created_with / recalls / in_moment / has_channel) land with
EdgeStore (#98 / edges design); expand still recomputes weight (Option 1).
Out of scope: success learning, session budgets.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable, Mapping

from elyra.memory.types import parse_iso_z

# ── Kind constants (shared with graph.py / EdgeStore) ──────────────────────

EDGE_SEQUENTIAL = "sequential"
EDGE_PARENT_OF = "parent_of"
EDGE_CHILD_OF = "child_of"
EDGE_SAME_MOMENT = "same_moment"
EDGE_SEMANTIC_HOP = "semantic_hop"
# Summary ladder fabric (PR-C / #92) — project from summary meta lists.
EDGE_SUMMARY_CHILD = "summary_child"
EDGE_SUMMARY_SOURCE = "summary_source"
EDGE_SUPERSEDES = "supersedes"
# Durable edge kinds (EdgeStore; design-memory-edges-and-traversal).
EDGE_CREATED_WITH = "created_with"
EDGE_RECALLS = "recalls"
EDGE_IN_MOMENT = "in_moment"
EDGE_HAS_CHANNEL = "has_channel"

EDGE_KINDS: frozenset[str] = frozenset(
    {
        EDGE_SEQUENTIAL,
        EDGE_PARENT_OF,
        EDGE_CHILD_OF,
        EDGE_SAME_MOMENT,
        EDGE_SEMANTIC_HOP,
        EDGE_SUMMARY_CHILD,
        EDGE_SUMMARY_SOURCE,
        EDGE_SUPERSEDES,
        EDGE_CREATED_WITH,
        EDGE_RECALLS,
        EDGE_IN_MOMENT,
        EDGE_HAS_CHANNEL,
    }
)

# Default step expand omits has_channel (structural modality; opt-in via flag).
DEFAULT_EXPAND_KINDS: frozenset[str] = frozenset(
    k for k in EDGE_KINDS if k != EDGE_HAS_CHANNEL
)

# Kinds that multiply cosine at expand (live hop + durable recalls).
_COSINE_KINDS: frozenset[str] = frozenset({EDGE_SEMANTIC_HOP, EDGE_RECALLS})

# ── Defaults (design weight model v1 table) ────────────────────────────────

BASE_SEQUENTIAL = 0.85
BASE_PARENT_CHILD = 0.90
BASE_SAME_MOMENT = 0.55
BASE_SEMANTIC_HOP = 0.70
BASE_SUMMARY_CHILD = 0.88
BASE_SUMMARY_SOURCE = 0.75
BASE_SUPERSEDES = 0.95
BASE_CREATED_WITH = 0.72
BASE_RECALLS = 0.78
BASE_IN_MOMENT = 0.60
BASE_HAS_CHANNEL = 0.50

DEFAULT_TEMPORAL_HALF_LIFE_HOURS = 72.0
DEFAULT_MIN_EXPAND_WEIGHT = 0.05

_BASE_BY_KIND: Mapping[str, float] = {
    EDGE_SEQUENTIAL: BASE_SEQUENTIAL,
    EDGE_PARENT_OF: BASE_PARENT_CHILD,
    EDGE_CHILD_OF: BASE_PARENT_CHILD,
    EDGE_SAME_MOMENT: BASE_SAME_MOMENT,
    EDGE_SEMANTIC_HOP: BASE_SEMANTIC_HOP,
    EDGE_SUMMARY_CHILD: BASE_SUMMARY_CHILD,
    EDGE_SUMMARY_SOURCE: BASE_SUMMARY_SOURCE,
    EDGE_SUPERSEDES: BASE_SUPERSEDES,
    EDGE_CREATED_WITH: BASE_CREATED_WITH,
    EDGE_RECALLS: BASE_RECALLS,
    EDGE_IN_MOMENT: BASE_IN_MOMENT,
    EDGE_HAS_CHANNEL: BASE_HAS_CHANNEL,
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
    """Cosine factor for ``semantic_hop`` / ``recalls``; 1.0 otherwise.

    Cosine is clamped to ``[0, 1]``. Missing cosine on cosine-bearing kinds
    → 0.0 (drop unless caller supplies a score). Durable ``recalls`` stores
    ``meta.cosine`` at write; expand recomputes via this factor (no double
    application of stored ``weight`` as authority).
    """
    if edge_kind not in _COSINE_KINDS:
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
    "BASE_CREATED_WITH",
    "BASE_HAS_CHANNEL",
    "BASE_IN_MOMENT",
    "BASE_PARENT_CHILD",
    "BASE_RECALLS",
    "BASE_SAME_MOMENT",
    "BASE_SEMANTIC_HOP",
    "BASE_SEQUENTIAL",
    "BASE_SUMMARY_CHILD",
    "BASE_SUMMARY_SOURCE",
    "BASE_SUPERSEDES",
    "DEFAULT_EXPAND_KINDS",
    "DEFAULT_MIN_EXPAND_WEIGHT",
    "DEFAULT_TEMPORAL_HALF_LIFE_HOURS",
    "EDGE_CHILD_OF",
    "EDGE_CREATED_WITH",
    "EDGE_HAS_CHANNEL",
    "EDGE_IN_MOMENT",
    "EDGE_KINDS",
    "EDGE_PARENT_OF",
    "EDGE_RECALLS",
    "EDGE_SAME_MOMENT",
    "EDGE_SEMANTIC_HOP",
    "EDGE_SEQUENTIAL",
    "EDGE_SUMMARY_CHILD",
    "EDGE_SUMMARY_SOURCE",
    "EDGE_SUPERSEDES",
    "base_weight",
    "clamp01",
    "edge_weight",
    "passes_min_weight",
    "phase3_multiplier",
    "semantic_factor",
    "structural_bonus",
    "temporal_decay",
]
