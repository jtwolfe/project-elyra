"""Pure unit tests for edge weight model v1 (Phase 2a PR-A1)."""

from __future__ import annotations

import math

from elyra.memory.weights import (
    BASE_CREATED_WITH,
    BASE_HAS_CHANNEL,
    BASE_IN_MOMENT,
    BASE_PARENT_CHILD,
    BASE_RECALLS,
    BASE_SAME_MOMENT,
    BASE_SEMANTIC_HOP,
    BASE_SEQUENTIAL,
    BASE_SUMMARY_CHILD,
    BASE_SUMMARY_SOURCE,
    BASE_SUPERSEDES,
    DEFAULT_EXPAND_KINDS,
    DEFAULT_MIN_EXPAND_WEIGHT,
    DEFAULT_TEMPORAL_HALF_LIFE_HOURS,
    EDGE_CHILD_OF,
    EDGE_CREATED_WITH,
    EDGE_HAS_CHANNEL,
    EDGE_IN_MOMENT,
    EDGE_KINDS,
    EDGE_PARENT_OF,
    EDGE_RECALLS,
    EDGE_SAME_MOMENT,
    EDGE_SEMANTIC_HOP,
    EDGE_SEQUENTIAL,
    EDGE_SUMMARY_CHILD,
    EDGE_SUMMARY_SOURCE,
    EDGE_SUPERSEDES,
    base_weight,
    clamp01,
    edge_weight,
    passes_min_weight,
    phase3_multiplier,
    semantic_factor,
    structural_bonus,
    temporal_decay,
)


def test_clamp01_bounds_and_nan():
    assert clamp01(-1.0) == 0.0
    assert clamp01(0.0) == 0.0
    assert clamp01(0.5) == 0.5
    assert clamp01(1.0) == 1.0
    assert clamp01(2.0) == 1.0
    assert clamp01(float("nan")) == 0.0


def test_base_weights_match_design_table():
    assert base_weight(EDGE_SEQUENTIAL) == BASE_SEQUENTIAL == 0.85
    assert base_weight(EDGE_PARENT_OF) == BASE_PARENT_CHILD == 0.90
    assert base_weight(EDGE_CHILD_OF) == BASE_PARENT_CHILD == 0.90
    assert base_weight(EDGE_SAME_MOMENT) == BASE_SAME_MOMENT == 0.55
    assert base_weight(EDGE_SEMANTIC_HOP) == BASE_SEMANTIC_HOP == 0.70
    assert base_weight(EDGE_SUMMARY_CHILD) == BASE_SUMMARY_CHILD == 0.88
    assert base_weight(EDGE_SUMMARY_SOURCE) == BASE_SUMMARY_SOURCE == 0.75
    assert base_weight(EDGE_SUPERSEDES) == BASE_SUPERSEDES == 0.95
    assert base_weight(EDGE_CREATED_WITH) == BASE_CREATED_WITH == 0.72
    assert base_weight(EDGE_RECALLS) == BASE_RECALLS == 0.78
    assert base_weight(EDGE_IN_MOMENT) == BASE_IN_MOMENT == 0.60
    assert base_weight(EDGE_HAS_CHANNEL) == BASE_HAS_CHANNEL == 0.50
    assert base_weight("unknown_kind") == 0.5


def test_summary_edge_tokens_frozen_and_in_kinds():
    """PR-C: frozen tokens for ladder fabric (#98 reuses summary_source)."""
    assert EDGE_SUMMARY_CHILD == "summary_child"
    assert EDGE_SUMMARY_SOURCE == "summary_source"
    assert EDGE_SUPERSEDES == "supersedes"
    assert EDGE_SUMMARY_CHILD in EDGE_KINDS
    assert EDGE_SUMMARY_SOURCE in EDGE_KINDS
    assert EDGE_SUPERSEDES in EDGE_KINDS


def test_durable_edge_tokens_and_default_expand_kinds():
    """Edges design: durable kinds + DEFAULT_EXPAND_KINDS omits has_channel."""
    assert EDGE_CREATED_WITH == "created_with"
    assert EDGE_RECALLS == "recalls"
    assert EDGE_IN_MOMENT == "in_moment"
    assert EDGE_HAS_CHANNEL == "has_channel"
    for k in (
        EDGE_CREATED_WITH,
        EDGE_RECALLS,
        EDGE_IN_MOMENT,
        EDGE_HAS_CHANNEL,
    ):
        assert k in EDGE_KINDS
    assert EDGE_HAS_CHANNEL not in DEFAULT_EXPAND_KINDS
    assert EDGE_CREATED_WITH in DEFAULT_EXPAND_KINDS
    assert DEFAULT_EXPAND_KINDS == EDGE_KINDS - {EDGE_HAS_CHANNEL}


def test_structural_bonus_v1_is_unity():
    for kind in (
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
    ):
        assert structural_bonus(kind) == 1.0


def test_temporal_decay_half_life():
    now = "2026-07-28T12:00:00Z"
    # Age = half_life → 0.5
    half = DEFAULT_TEMPORAL_HALF_LIFE_HOURS
    assert half == 72.0
    dst = "2026-07-25T12:00:00Z"  # exactly 72h earlier
    d = temporal_decay(dst, now, half_life_hours=half)
    assert abs(d - 0.5) < 1e-9

    # Age = 0 → 1.0
    assert temporal_decay(now, now, half_life_hours=half) == 1.0

    # Future dst → 1.0
    assert temporal_decay("2026-07-29T12:00:00Z", now, half_life_hours=half) == 1.0

    # Missing t_start → 1.0
    assert temporal_decay(None, now, half_life_hours=half) == 1.0

    # half_life <= 0 → 1.0
    assert temporal_decay(dst, now, half_life_hours=0) == 1.0

    # Double half-life → 0.25
    dst2 = "2026-07-22T12:00:00Z"  # 144h
    d2 = temporal_decay(dst2, now, half_life_hours=half)
    assert abs(d2 - 0.25) < 1e-9


def test_temporal_decay_exponential_formula():
    now = "2026-07-28T00:00:00Z"
    dst = "2026-07-27T00:00:00Z"  # 24h
    half = 48.0
    expected = math.pow(0.5, 24.0 / 48.0)
    assert abs(temporal_decay(dst, now, half_life_hours=half) - expected) < 1e-12


def test_semantic_factor():
    assert semantic_factor(EDGE_SEQUENTIAL, cosine=0.9) == 1.0
    assert semantic_factor(EDGE_SEMANTIC_HOP, cosine=0.8) == 0.8
    assert semantic_factor(EDGE_SEMANTIC_HOP, cosine=1.5) == 1.0
    assert semantic_factor(EDGE_SEMANTIC_HOP, cosine=-0.2) == 0.0
    assert semantic_factor(EDGE_SEMANTIC_HOP, cosine=None) == 0.0
    # Durable recalls use the same cosine path as semantic_hop.
    assert semantic_factor(EDGE_RECALLS, cosine=0.8) == 0.8
    assert semantic_factor(EDGE_RECALLS, cosine=None) == 0.0
    assert semantic_factor(EDGE_CREATED_WITH, cosine=None) == 1.0
    assert semantic_factor(EDGE_IN_MOMENT, cosine=0.5) == 1.0


def test_phase3_multiplier_always_one():
    assert phase3_multiplier() == 1.0
    assert phase3_multiplier("a", "b", EDGE_SEQUENTIAL, {"x": 1}) == 1.0


def test_edge_weight_sequential_no_decay_when_now_none():
    w = edge_weight(EDGE_SEQUENTIAL, dst_t_start="2026-01-01T00:00:00Z", now=None)
    assert abs(w - BASE_SEQUENTIAL) < 1e-12


def test_edge_weight_parent_with_decay():
    now = "2026-07-28T12:00:00Z"
    dst = "2026-07-25T12:00:00Z"  # half-life age → 0.5
    w = edge_weight(
        EDGE_PARENT_OF,
        dst_t_start=dst,
        now=now,
        half_life_hours=72.0,
    )
    assert abs(w - BASE_PARENT_CHILD * 0.5) < 1e-12


def test_edge_weight_semantic_hop_uses_cosine():
    w = edge_weight(
        EDGE_SEMANTIC_HOP,
        dst_t_start="2026-07-28T12:00:00Z",
        now="2026-07-28T12:00:00Z",
        cosine=0.5,
    )
    assert abs(w - BASE_SEMANTIC_HOP * 1.0 * 0.5) < 1e-12


def test_edge_weight_clamped_to_unit_interval():
    # Injected phase3 that would push above 1 → still clamp01
    w = edge_weight(
        EDGE_PARENT_OF,
        now=None,
        phase3_fn=lambda *_a, **_k: 10.0,
    )
    assert w == 1.0


def test_edge_weight_phase3_fn_injected():
    w = edge_weight(
        EDGE_SEQUENTIAL,
        now=None,
        phase3_fn=lambda *_a, **_k: 0.5,
    )
    assert abs(w - BASE_SEQUENTIAL * 0.5) < 1e-12


def test_passes_min_weight():
    assert passes_min_weight(DEFAULT_MIN_EXPAND_WEIGHT)
    assert not passes_min_weight(DEFAULT_MIN_EXPAND_WEIGHT - 1e-9)
    assert passes_min_weight(0.01, min_weight=0.01)
    assert not passes_min_weight(0.009, min_weight=0.01)


def test_same_moment_base_weaker_than_sequential():
    assert base_weight(EDGE_SAME_MOMENT) < base_weight(EDGE_SEQUENTIAL)
    assert base_weight(EDGE_SEQUENTIAL) < base_weight(EDGE_PARENT_OF)
