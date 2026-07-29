"""Phase 2a meal directed_keep: budget v3, select, dedup, flags-off parity."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.inspect import meal_package_to_inspect
from elyra.memory.meal import (
    DIRECTED_KEEP_OMIT_BUDGET,
    DIRECTED_KEEP_OMIT_DEDUPED,
    DIRECTED_KEEP_OMIT_DISABLED,
    DIRECTED_KEEP_OMIT_EMPTY,
    compose_meal,
    compose_outer_messages,
    select_directed_keep,
)
from elyra.memory.store import open_memory_store
from elyra.memory.tokens import (
    split_memory_budget_v2,
    split_memory_budget_v3,
)
from elyra.memory.types import Atom, new_atom_id


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    s = open_memory_store(paths, MemorySettings(write_atoms=True, backend="jsonl"))
    yield s
    s.close()


def _atom(
    *,
    t: str,
    kind: str = "observation",
    text: str = "body",
    moment_id: str | None = "m_open",
    atom_id: str | None = None,
    parent_atom_id: str | None = None,
    **kwargs: Any,
) -> Atom:
    return Atom(
        atom_id=atom_id or new_atom_id(),
        t_start=t,
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=moment_id,
        parent_atom_id=parent_atom_id,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# split_memory_budget_v3 golden cases
# ---------------------------------------------------------------------------


def test_split_v3_bit_identical_v2_when_dk_inactive():
    """flags / empty keep → bit-identical to split_memory_budget_v2."""
    cases = [
        dict(
            budget_tokens=10_000,
            system_text="sys",
            orient_text="orient",
            semantic_enabled=False,
            episodic_fraction=0.20,
        ),
        dict(
            budget_tokens=10_000,
            system_text="sys",
            orient_text="orient",
            semantic_enabled=True,
            semantic_fraction=0.12,
            episodic_fraction_with_semantic=0.18,
            temporal_min_fraction=0.55,
        ),
        dict(
            budget_tokens=1000,
            semantic_enabled=True,
            semantic_fraction=0.30,
            episodic_fraction_with_semantic=0.30,
            temporal_min_fraction=0.55,
        ),
        dict(budget_tokens=0, semantic_enabled=True),
    ]
    for kwargs in cases:
        f2, s2, e2, t2 = split_memory_budget_v2(**kwargs)
        f3, s3, d3, e3, t3 = split_memory_budget_v3(
            directed_keep_active=False, **kwargs
        )
        assert (f3, s3, d3, e3, t3) == (f2, s2, 0, e2, t2)


def test_split_v3_defaults_both_on_no_floor_cut():
    """defaults both on, R large → s≈0.12R d≈0.08R e≈0.18R t≈0.62R ≥ 0.55R."""
    R = 10_000
    fixed, sem, dk, epi, temp = split_memory_budget_v3(
        R,
        system_text="",
        orient_text="",
        semantic_enabled=True,
        directed_keep_active=True,
        semantic_fraction=0.12,
        directed_keep_fraction=0.08,
        episodic_fraction_with_semantic=0.18,
        temporal_min_fraction=0.55,
    )
    assert fixed == 0
    assert sem + dk + epi + temp == R
    assert sem == int(R * 0.12)
    assert dk == int(R * 0.08)
    assert epi == int(R * 0.18)
    assert temp == R - sem - dk - epi
    assert temp >= int(R * 0.55)
    # No floor cut under default fractions (0.12+0.08+0.18=0.38 < 0.45).
    assert temp == R - int(R * 0.12) - int(R * 0.08) - int(R * 0.18)


def test_split_v3_floor_cut_order_semantic_then_dk_then_epi():
    """Floor pressure: cut s first, then d, then e; t reaches floor."""
    # remaining=1000; s 0.25 + d 0.20 + e 0.20 = 0.65 → temp 0.35 < floor 0.55
    # deficit = 200. Cut semantic first (250 → 50), still need 0 more? 200-200=0
    # take 200 from s (250) → s=50, deficit=0. d and e untouched.
    fixed, sem, dk, epi, temp = split_memory_budget_v3(
        1000,
        semantic_enabled=True,
        directed_keep_active=True,
        semantic_fraction=0.25,
        directed_keep_fraction=0.20,
        episodic_fraction_with_semantic=0.20,
        temporal_min_fraction=0.55,
    )
    assert fixed == 0
    assert sem + dk + epi + temp == 1000
    floor = int(1000 * 0.55)
    assert temp >= floor
    assert sem == 250 - 200  # 50
    assert dk == 200
    assert epi == 200
    assert temp == 550


def test_split_v3_floor_cuts_through_dk_into_episodic():
    """Larger deficit drains semantic fully then directed_keep then epi."""
    # s 0.10 + d 0.10 + e 0.30 = 0.50 → temp 0.50 < floor 0.55; deficit 50
    # s takes 50 → s=50, d=100, e=300, t=550
    fixed, sem, dk, epi, temp = split_memory_budget_v3(
        1000,
        semantic_enabled=True,
        directed_keep_active=True,
        semantic_fraction=0.10,
        directed_keep_fraction=0.10,
        episodic_fraction_with_semantic=0.30,
        temporal_min_fraction=0.55,
    )
    assert sem + dk + epi + temp == 1000
    assert temp == 550
    assert sem == 50  # 100-50
    assert dk == 100
    assert epi == 300

    # Bigger deficit: s 0.05 + d 0.05 + e 0.40 = 0.50 → temp 500; floor 550
    # deficit 50 from s (50→0) then still 0? s only 50, deficit becomes 0 after s.
    # Need larger: s 0.05 + d 0.05 + e 0.45 = 0.55 → temp 450; floor 550; deficit 100
    # s 50→0 (take 50), d 50→0 (take 50), e untouched 450, t=550
    _f, sem2, dk2, epi2, temp2 = split_memory_budget_v3(
        1000,
        semantic_enabled=True,
        directed_keep_active=True,
        semantic_fraction=0.05,
        directed_keep_fraction=0.05,
        episodic_fraction_with_semantic=0.45,
        temporal_min_fraction=0.55,
    )
    assert sem2 == 0
    assert dk2 == 0
    assert epi2 == 450
    assert temp2 == 550


def test_split_v3_semantic_off_dk_on_uses_phase1_episodic_fraction():
    """semantic off, dk on: s=0; e uses episodic_fraction (0.20); d from fraction."""
    R = 1000
    fixed, sem, dk, epi, temp = split_memory_budget_v3(
        R,
        semantic_enabled=False,
        directed_keep_active=True,
        directed_keep_fraction=0.08,
        episodic_fraction=0.20,
        temporal_min_fraction=0.55,
    )
    assert fixed == 0
    assert sem == 0
    assert dk == int(R * 0.08)
    assert epi == int(R * 0.20)
    assert temp == R - dk - epi
    assert sem + dk + epi + temp == R


def test_split_v3_remaining_zero():
    fixed, sem, dk, epi, temp = split_memory_budget_v3(
        0,
        semantic_enabled=True,
        directed_keep_active=True,
    )
    assert (fixed, sem, dk, epi, temp) == (0, 0, 0, 0, 0)


def test_split_v3_impossible_floor_all_to_temporal():
    _f, sem, dk, epi, temp = split_memory_budget_v3(
        500,
        semantic_enabled=True,
        directed_keep_active=True,
        semantic_fraction=0.2,
        directed_keep_fraction=0.1,
        episodic_fraction_with_semantic=0.2,
        temporal_min_fraction=1.0,
    )
    assert sem == 0
    assert dk == 0
    assert epi == 0
    assert temp == 500


# ---------------------------------------------------------------------------
# select_directed_keep
# ---------------------------------------------------------------------------


def test_select_dk_disabled(store):
    store.put_atom(
        _atom(t="2026-07-27T10:00:00Z", text="kept", atom_id="a_k", moment_id="m1")
    )
    items, reason, meta = select_directed_keep(
        store,
        keep_ids=["a_k"],
        walk_summary="I walked about X",
        cap_tokens=500,
        enabled=False,
    )
    assert items == []
    assert reason == DIRECTED_KEEP_OMIT_DISABLED
    assert meta is not None
    assert meta["enabled"] is False


def test_select_dk_empty(store):
    items, reason, meta = select_directed_keep(
        store,
        keep_ids=[],
        walk_summary="summary",
        cap_tokens=500,
        enabled=True,
    )
    assert items == []
    assert reason == DIRECTED_KEEP_OMIT_EMPTY
    assert meta["keep_ids_in"] == 0


def test_select_dk_packs_summary_and_atoms_in_order(store):
    a1 = _atom(
        t="2026-07-27T10:00:00Z",
        text="first keep memory",
        atom_id="a1",
        moment_id="m_past",
    )
    a2 = _atom(
        t="2026-07-27T11:00:00Z",
        text="second keep memory",
        atom_id="a2",
        moment_id="m_past",
    )
    store.put_atom(a1)
    store.put_atom(a2)
    items, reason, meta = select_directed_keep(
        store,
        keep_ids=["a2", "a1"],  # keep-set order
        walk_summary="I walked through memories about cats",
        cap_tokens=2000,
        enabled=True,
    )
    assert reason is None
    assert items[0].label == "directed-keep/summary"
    assert items[0].atom_id is None
    assert items[0].channel == "directed_keep"
    assert "walked through memories" in items[0].content
    assert [i.atom_id for i in items[1:]] == ["a2", "a1"]
    assert all(i.channel == "directed_keep" for i in items)
    assert all(i.label.startswith("directed-keep") for i in items)
    assert meta["packed"] == 2
    assert meta["summary_packed"] is True


def test_select_dk_dedup_against_exclude(store):
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="already in temporal",
        atom_id="a_dup",
        moment_id="m_past",
    )
    only = _atom(
        t="2026-07-26T10:00:00Z",
        text="unique keep only",
        atom_id="a_only",
        moment_id="m_other",
    )
    store.put_atom(past)
    store.put_atom(only)
    items, reason, meta = select_directed_keep(
        store,
        keep_ids=["a_dup", "a_only"],
        walk_summary="walk",
        cap_tokens=2000,
        enabled=True,
        exclude_atom_ids={"a_dup"},
    )
    assert reason is None
    atom_ids = [i.atom_id for i in items if i.atom_id]
    assert atom_ids == ["a_only"]
    assert meta["deduped"] == 1
    assert meta["packed"] == 1


def test_select_dk_all_deduped(store):
    a = _atom(
        t="2026-07-27T10:00:00Z",
        text="dup",
        atom_id="a_dup",
        moment_id="m_past",
    )
    store.put_atom(a)
    items, reason, meta = select_directed_keep(
        store,
        keep_ids=["a_dup"],
        walk_summary="walk",
        cap_tokens=2000,
        enabled=True,
        exclude_atom_ids={"a_dup"},
    )
    assert items == []
    assert reason == DIRECTED_KEEP_OMIT_DEDUPED
    assert meta["deduped"] == 1
    assert meta["packed"] == 0


def test_select_dk_budget_zero(store):
    store.put_atom(
        _atom(t="2026-07-27T10:00:00Z", text="x", atom_id="a1", moment_id="m1")
    )
    items, reason, meta = select_directed_keep(
        store,
        keep_ids=["a1"],
        walk_summary="walk",
        cap_tokens=0,
        enabled=True,
    )
    assert items == []
    assert reason == DIRECTED_KEEP_OMIT_BUDGET
    assert meta["cap_tokens"] == 0


def test_select_dk_parcel_maps_to_parent(store):
    parent = _atom(
        t="2026-07-27T09:00:00Z",
        text="parent body long",
        atom_id="a_parent",
        moment_id="m_past",
    )
    parcel = _atom(
        t="2026-07-27T09:00:01Z",
        kind="parcel",
        text="slice",
        atom_id="a_parcel",
        moment_id="m_past",
        parent_atom_id="a_parent",
    )
    store.put_atom(parent)
    store.put_atom(parcel)
    items, reason, meta = select_directed_keep(
        store,
        keep_ids=["a_parcel"],
        walk_summary="walk",
        cap_tokens=2000,
        enabled=True,
    )
    assert reason is None
    bodies = [i for i in items if i.atom_id]
    assert len(bodies) == 1
    assert bodies[0].atom_id == "a_parent"
    assert "parcel→parent" in bodies[0].label
    assert bodies[0].meta.get("via_parcel") is True
    assert meta["packed"] == 1


# ---------------------------------------------------------------------------
# compose_meal integration
# ---------------------------------------------------------------------------


def test_compose_meal_flags_off_parity_no_dk(store):
    """Flags off / empty keep → Phase 1/2 parity (no directed_keep channel)."""
    open_id = "m_openmoment01"
    store.put_atom(
        _atom(
            t="2026-07-28T14:50:00Z",
            text="wake hi",
            moment_id=open_id,
        )
    )
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    cfg = MemorySettings(
        semantic_enabled=False,
        directed_keep_enabled=False,
        directed_traversal_enabled=False,
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text="ORIENT",
        now=now,
        settings=cfg,
    )
    assert "directed_keep" not in pkg.channels_present
    assert pkg.directed_keep_omitted_reason is None
    assert pkg.directed_keep_meta is None
    assert any(i.channel == "temporal" for i in pkg.items)


def test_compose_meal_dk_order_and_labels(store):
    open_id = "m_open"
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    store.put_atom(
        _atom(
            t="2026-07-28T14:50:00Z",
            text="open moment seed",
            moment_id=open_id,
        )
    )
    # Keep atom outside open moment so it is not temporal-deduped.
    keep = _atom(
        t="2026-07-25T10:00:00Z",
        text="curated keep about bees",
        moment_id="m_keep",
        atom_id="a_keep",
    )
    store.put_atom(keep)

    cfg = MemorySettings(
        semantic_enabled=False,
        directed_keep_enabled=True,
        directed_keep_fraction=0.08,
        episodic_horizon_hours=1.0,  # keep not in episodic
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text="ORIENT",
        now=now,
        settings=cfg,
        directed_keep_ids=["a_keep"],
        directed_keep_summary="I walked through memories about bees",
    )
    channels = [i.channel for i in pkg.items]
    # Order: episodic (maybe empty) → semantic (none) → directed_keep → temporal
    dk_idxs = [i for i, c in enumerate(channels) if c == "directed_keep"]
    temp_idxs = [i for i, c in enumerate(channels) if c == "temporal"]
    assert dk_idxs, "expected directed_keep items"
    assert temp_idxs, "expected temporal items"
    assert max(dk_idxs) < min(temp_idxs)
    assert "directed_keep" in pkg.channels_present
    assert pkg.directed_keep_omitted_reason is None
    assert pkg.directed_keep_meta is not None
    assert pkg.directed_keep_meta["packed"] == 1
    labels = [i.label for i in pkg.items if i.channel == "directed_keep"]
    assert labels[0] == "directed-keep/summary"
    assert any(lb == "directed-keep" for lb in labels)


def test_compose_meal_dk_dedupes_vs_temporal(store):
    open_id = "m_open"
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    open_atom = _atom(
        t="2026-07-28T14:50:00Z",
        text="open body",
        moment_id=open_id,
        atom_id="a_open",
    )
    store.put_atom(open_atom)
    cfg = MemorySettings(
        directed_keep_enabled=True,
        semantic_enabled=False,
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text="ORIENT",
        now=now,
        settings=cfg,
        directed_keep_ids=["a_open"],
        directed_keep_summary="walk",
    )
    assert pkg.directed_keep_omitted_reason == DIRECTED_KEEP_OMIT_DEDUPED
    assert "directed_keep" not in pkg.channels_present


def test_compose_outer_messages_includes_dk_label(store):
    open_id = "m_open"
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    store.put_atom(
        _atom(t="2026-07-28T14:50:00Z", text="open", moment_id=open_id)
    )
    store.put_atom(
        _atom(
            t="2026-07-20T10:00:00Z",
            text="keep body",
            moment_id="m_k",
            atom_id="a_k",
        )
    )
    cfg = MemorySettings(
        directed_keep_enabled=True,
        semantic_enabled=False,
        episodic_horizon_hours=1.0,
    )
    msgs = compose_outer_messages(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text="ORIENT",
        now=now,
        settings=cfg,
        directed_keep_ids=["a_k"],
        directed_keep_summary="walk summary line",
    )
    contents = [m.get("content", "") for m in msgs]
    assert any("[context:directed-keep/summary]" in c for c in contents)
    assert any("[context:directed-keep]" in c for c in contents)
    # system first, orient last
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["content"] == "ORIENT"


def test_inspect_surfaces_directed_keep_meta(store):
    open_id = "m_open"
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    store.put_atom(
        _atom(t="2026-07-28T14:50:00Z", text="open", moment_id=open_id)
    )
    store.put_atom(
        _atom(
            t="2026-07-20T10:00:00Z",
            text="keep",
            moment_id="m_k",
            atom_id="a_k",
        )
    )
    cfg = MemorySettings(
        directed_keep_enabled=True,
        semantic_enabled=False,
        episodic_horizon_hours=1.0,
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        now=now,
        settings=cfg,
        directed_keep_ids=["a_k"],
        directed_keep_summary="walk",
    )
    snap = meal_package_to_inspect(pkg, budget_tokens=50_000, source="test")
    assert snap["directed_keep_omitted_reason"] is None
    assert snap["directed_keep_meta"] is not None
    assert snap["directed_keep_meta"]["packed"] == 1
    assert "directed_keep" in snap["channel_token_totals"]


def test_compose_meal_flag_on_empty_keep_omits_empty(store):
    open_id = "m_open"
    store.put_atom(
        _atom(t="2026-07-28T14:50:00Z", text="open", moment_id=open_id)
    )
    cfg = MemorySettings(
        directed_keep_enabled=True,
        semantic_enabled=False,
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        now=datetime(2026, 7, 28, 15, 0, tzinfo=UTC),
        settings=cfg,
        directed_keep_ids=[],
    )
    # Empty keep → not active for budget; select still reports empty when flag on.
    assert pkg.directed_keep_omitted_reason == DIRECTED_KEEP_OMIT_EMPTY
    assert "directed_keep" not in pkg.channels_present


def test_compose_meal_traversal_flag_enables_keep_oq_a1(store):
    """OQ-A1: directed_traversal_enabled alone activates directed_keep."""
    open_id = "m_open"
    store.put_atom(
        _atom(t="2026-07-28T14:50:00Z", text="open", moment_id=open_id)
    )
    store.put_atom(
        _atom(
            t="2026-07-20T10:00:00Z",
            text="keep via traversal flag",
            moment_id="m_k",
            atom_id="a_k",
        )
    )
    cfg = MemorySettings(
        directed_keep_enabled=False,
        directed_traversal_enabled=True,
        semantic_enabled=False,
        episodic_horizon_hours=1.0,
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        now=datetime(2026, 7, 28, 15, 0, tzinfo=UTC),
        settings=cfg,
        directed_keep_ids=["a_k"],
        directed_keep_summary="walk",
    )
    assert "directed_keep" in pkg.channels_present
    assert pkg.directed_keep_omitted_reason is None
