"""TraversalSession state machine + budgets + hygiene (Phase 2a PR-A2)."""

from __future__ import annotations

from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import (
    MemorySettings,
    is_directed_keep_enabled,
    is_directed_traversal_enabled,
)
from elyra.memory.graph import GraphView
from elyra.memory.store import open_memory_store
from elyra.memory.keep_tray import load_directed_keep_tray, tray_runtime_path
from elyra.memory.traverse import (
    ERROR_INVALID_ARGS,
    ERROR_KEEP_DISABLED,
    ERROR_NO_ACTIVE,
    ERROR_TRAVERSE_DISABLED,
    LOCAL_MAP_ASSOCIATIVE_CAP,
    LOCAL_MAP_EDGES_CAP,
    LOCAL_MAP_MOMENT_PEERS_CAP,
    LOCAL_MAP_RING_CAP,
    LOCAL_MAPS_STEP_CAP,
    TraversalRegistry,
    build_local_map,
    build_walk_summary_nl,
    inspect_atoms,
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
    t: str = "2026-07-28T10:00:00Z",
    kind: str = "observation",
    text: str = "body",
    moment_id: str | None = "m1",
    atom_id: str | None = None,
    **kwargs: Any,
) -> Atom:
    return Atom(
        atom_id=atom_id or new_atom_id(),
        t_start=t,
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=moment_id,
        **kwargs,
    )


def _link_chain(store, atoms: list[Atom]) -> list[Atom]:
    stored: list[Atom] = []
    for i, a in enumerate(atoms):
        prev_id = atoms[i - 1].atom_id if i > 0 else None
        next_id = atoms[i + 1].atom_id if i + 1 < len(atoms) else None
        stored.append(
            store.put_atom(
                Atom(
                    atom_id=a.atom_id,
                    t_start=a.t_start,
                    kind=a.kind,
                    content_text=a.content_text,
                    content_ref=a.content_ref,
                    moment_id=a.moment_id,
                    prev_atom_id=prev_id,
                    next_atom_id=next_id,
                    parent_atom_id=a.parent_atom_id,
                    meta=dict(a.meta or {}),
                )
            )
        )
    return stored


def _enabled_settings(**kwargs: Any) -> MemorySettings:
    base = dict(
        directed_traversal_enabled=True,
        write_atoms=True,
        backend="jsonl",
        traverse_max_steps=8,
        traverse_max_nodes=48,
        traverse_max_depth=3,
        traverse_max_seeds=8,
        traverse_frontier_max=16,
        traverse_max_expand_per_step=3,
        traverse_keep_max=16,
        traverse_expand_max_ms=80,
        traverse_session_ttl_s=900,
        traverse_keep_adjacent=False,  # explicit in most tests
    )
    base.update(kwargs)
    return MemorySettings(**base)


def _reg(settings: MemorySettings | None = None) -> TraversalRegistry:
    return TraversalRegistry(settings=settings or _enabled_settings())


def _chain_store(store, n: int = 5, moment_id: str = "m1") -> list[Atom]:
    atoms = [
        _atom(
            atom_id=f"a_t{i}",
            t=f"2026-07-28T10:0{i}:00Z",
            text=f"memory about topic {i} with more detail for previews",
            moment_id=moment_id,
        )
        for i in range(n)
    ]
    return _link_chain(store, atoms)


# ── Flags off inert ─────────────────────────────────────────────────────────


def test_flags_off_start_inert(store):
    settings = MemorySettings(directed_traversal_enabled=False)
    reg = _reg(settings)
    gv = GraphView(store, settings=settings)
    out = reg.start(gv, goal="find related memories")
    assert out["ok"] is False
    assert out["error_reason"] == ERROR_TRAVERSE_DISABLED
    assert reg.active_session is None
    assert reg.last_session is None
    assert reg.last_confirmed_keep is None


def test_flags_off_step_finish_abandon_inert(store):
    settings = MemorySettings(directed_traversal_enabled=False)
    reg = _reg(settings)
    gv = GraphView(store, settings=settings)
    for fn in (
        lambda: reg.step(gv, expand_ids=["a"]),
        lambda: reg.finish(gv),
        lambda: reg.abandon(),
    ):
        out = fn()
        assert out["ok"] is False
        assert out["error_reason"] == ERROR_TRAVERSE_DISABLED


def test_directed_keep_follows_traversal_oq_a1():
    off = MemorySettings(
        directed_traversal_enabled=False, directed_keep_enabled=False
    )
    assert is_directed_traversal_enabled(off) is False
    assert is_directed_keep_enabled(off) is False
    on = MemorySettings(
        directed_traversal_enabled=True, directed_keep_enabled=False
    )
    assert is_directed_traversal_enabled(on) is True
    assert is_directed_keep_enabled(on) is True  # follows
    keep_only = MemorySettings(
        directed_traversal_enabled=False, directed_keep_enabled=True
    )
    assert is_directed_keep_enabled(keep_only) is True


# ── start → step → finish ───────────────────────────────────────────────────


def test_start_step_finish_happy_path(store):
    atoms = _chain_store(store, 5)
    settings = _enabled_settings(traverse_keep_adjacent=False)
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")

    start = reg.start(
        gv,
        goal="topic memories",
        seed_atom_ids=[atoms[2].atom_id],
        moment_id="m1",
    )
    assert start["ok"] is True
    assert start["status"] == "active"
    sid = start["session_id"]
    assert atoms[2].atom_id in start["seed_ids"]
    assert start["considered_count"] >= 1
    assert "budget" in start
    assert "expand_ms_budget" in start["budget"]
    assert "wall_ms_remaining" not in start["budget"]  # KD-A18
    assert reg.active_session is not None

    # Expand from middle seed — should find sequential neighbors.
    step = reg.step(
        gv,
        session_id=sid,
        expand_ids=[atoms[2].atom_id],
        keep_ids=[atoms[2].atom_id],
        scratchpad="looking for topic 1",
    )
    assert step["ok"] is True
    assert step["status"] == "active"
    assert step["budget"]["steps_spent"] == 1
    assert atoms[2].atom_id in step["keep_set"]
    assert step["scratchpad"] == "looking for topic 1"
    # Neighbors of middle should appear in considered.
    assert step["considered_count"] >= 2

    # Keep only ids that were considered (unknown ids are ignored).
    active = reg.active_session
    assert active is not None
    considered_ids = set(active.considered.keys())
    final_keeps = [aid for aid in (atoms[2].atom_id, atoms[1].atom_id) if aid in considered_ids]
    if not final_keeps:
        final_keeps = [atoms[2].atom_id]
    fin = reg.finish(
        gv,
        session_id=sid,
        keep_ids=final_keeps,
        summary_hint="found related topic",
    )
    assert fin["ok"] is True
    assert fin["status"] == "confirmed"
    assert fin["walk_summary_nl"]
    assert "I walked through memories about" in fin["walk_summary_nl"]
    assert "found related topic" in fin["walk_summary_nl"]
    assert reg.active_session is None
    assert reg.last_session is not None
    assert reg.last_session.status == "confirmed"
    assert reg.last_confirmed_keep is not None
    assert reg.last_confirmed_keep.session_id == sid
    assert len(reg.last_confirmed_keep.keep_ids) >= 1

    # Glass view after finish: last_session has considered + kept + budgets.
    view = reg.get_graph_session_view()
    assert view.which == "last"
    assert view.session is not None
    assert view.session["considered_count"] >= 1
    assert view.session["keep_ids"]
    assert "budgets" in view.session
    assert view.session["budgets"]["steps_spent"] >= 1
    assert view.meal_keep_count >= 1


def test_finish_view_has_considered_kept_budgets(store):
    atoms = _chain_store(store, 3)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg.step(gv, expand_ids=[atoms[0].atom_id], keep_ids=[atoms[0].atom_id])
    fin = reg.finish(keep_ids=[atoms[0].atom_id])
    assert fin["ok"]
    sess = fin
    assert "considered" in sess
    assert "keep_ids" in sess
    assert "budgets" in sess
    assert sess["budgets"]["max_steps"] == 8
    assert "expand_ms_budget" in sess["budgets"]
    assert "wall_ms" not in str(sess["budgets"]).lower() or "wall_ms_remaining" not in sess["budgets"]


# ── abandon active only ─────────────────────────────────────────────────────


def test_abandon_active_retains_sticky_snapshots(store):
    atoms = _chain_store(store, 3)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")

    # First walk finish → sticky.
    reg.start(gv, goal="first", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg.step(gv, expand_ids=[atoms[0].atom_id], keep_ids=[atoms[0].atom_id])
    reg.finish(keep_ids=[atoms[0].atom_id])
    last_sid = reg.last_session.session_id
    keep_ids = list(reg.last_confirmed_keep.keep_ids)

    # New active walk then abandon.
    reg.start(gv, goal="second", seed_atom_ids=[atoms[1].atom_id], moment_id="m1")
    assert reg.active_session is not None
    active_sid = reg.active_session.session_id
    out = reg.abandon(session_id=active_sid)
    assert out["ok"] is True
    assert out["last_session_retained"] is True
    assert out["last_confirmed_retained"] is True
    assert reg.active_session is None
    assert reg.last_session is not None
    assert reg.last_session.session_id == last_sid
    assert list(reg.last_confirmed_keep.keep_ids) == keep_ids


def test_new_start_abandons_active_retains_last(store):
    atoms = _chain_store(store, 4)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")

    reg.start(gv, goal="first", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg.finish(keep_ids=[atoms[0].atom_id])
    keep_before = list(reg.last_confirmed_keep.keep_ids)
    last_before = reg.last_session.session_id

    # Active walk in progress…
    reg.start(gv, goal="active-a", seed_atom_ids=[atoms[1].atom_id], moment_id="m1")
    mid_active = reg.active_session.session_id
    # New start abandons active only.
    reg.start(gv, goal="active-b", seed_atom_ids=[atoms[2].atom_id], moment_id="m1")
    assert reg.active_session is not None
    assert reg.active_session.session_id != mid_active
    assert reg.active_session.goal == "active-b"
    assert reg.last_session.session_id == last_before
    assert list(reg.last_confirmed_keep.keep_ids) == keep_before

    # Second finish replaces both sticky snapshots.
    reg.finish(keep_ids=[atoms[2].atom_id])
    assert reg.last_session.goal == "active-b"
    assert list(reg.last_confirmed_keep.keep_ids) == [atoms[2].atom_id]


# ── idle TTL ────────────────────────────────────────────────────────────────


def test_idle_ttl_abandons_active_only(store):
    atoms = _chain_store(store, 2)
    settings = _enabled_settings(
        traverse_session_ttl_s=1, traverse_keep_adjacent=False
    )
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")

    reg.start(gv, goal="done", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg.finish(keep_ids=[atoms[0].atom_id])
    last_sid = reg.last_session.session_id

    # Pin updated_at in the past by starting with a fixed clock, then sweep.
    past = "2026-07-28T10:00:00Z"
    now_holder = {"t": past}

    def clock() -> str:
        return now_holder["t"]

    reg2 = TraversalRegistry(settings=settings, now_fn=clock)
    # Seed sticky manually by finish path.
    gv2 = GraphView(store, settings=settings, now=past)
    reg2.start(gv2, goal="sticky", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg2.finish(keep_ids=[atoms[0].atom_id])
    reg2.start(gv2, goal="idle-me", seed_atom_ids=[atoms[1].atom_id], moment_id="m1")
    assert reg2.active_session is not None
    # Advance wall clock past TTL.
    now_holder["t"] = "2026-07-28T10:00:05Z"  # 5s > ttl 1s
    dropped = reg2.sweep_idle(now=now_holder["t"])
    assert dropped is not None
    assert dropped.status == "timed_out"
    assert reg2.active_session is None
    assert reg2.last_session is not None
    assert reg2.last_confirmed_keep is not None

    # Within TTL: no drop.
    now_holder["t"] = "2026-07-28T10:00:00Z"
    reg2.start(gv2, goal="fresh", seed_atom_ids=[atoms[1].atom_id], moment_id="m1")
    now_holder["t"] = "2026-07-28T10:00:00.500Z"
    assert reg2.sweep_idle(now=now_holder["t"]) is None
    assert reg2.active_session is not None
    del last_sid  # silence lint on unused from first half


# ── expand_ms partial ───────────────────────────────────────────────────────


def test_expand_ms_partial_on_step(store):
    """Very low expand_ms still returns a session; may set expand_truncated."""
    atoms = _chain_store(store, 6)
    settings = _enabled_settings(
        traverse_expand_max_ms=1,
        traverse_keep_adjacent=False,
        traverse_max_expand_per_step=8,
    )
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    # Even with tiny budget, step must not kill the session.
    out = reg.step(gv, expand_ids=[atoms[0].atom_id])
    assert out["ok"] is True
    assert out["status"] == "active"
    assert "budget" in out
    assert out["budget"]["expand_ms_budget"] == 1


def test_start_seed_expand_ms_does_not_fail(store):
    """seed_from_text under start expand_ms: structural/explicit still succeed."""
    atoms = _chain_store(store, 3)
    settings = _enabled_settings(
        traverse_expand_max_ms=1,
        traverse_start_expand_max_ms=1,
        traverse_keep_adjacent=False,
    )
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    # No index → semantic empty with no_index; explicit seeds still work.
    out = reg.start(
        gv,
        goal="search something",
        seed_query="topic",
        seed_atom_ids=[atoms[1].atom_id],
        moment_id="m1",
    )
    assert out["ok"] is True
    assert atoms[1].atom_id in out["seed_ids"]
    # Semantic unavailable reasons may appear without failing start.
    reasons = out.get("seed_reasons") or []
    assert "explicit" in reasons or atoms[1].atom_id in out["seed_ids"]


# ── seed union ──────────────────────────────────────────────────────────────


def test_seed_union_explicit_and_temporal(store):
    atoms = _chain_store(store, 5)
    settings = _enabled_settings(traverse_max_seeds=4, traverse_keep_adjacent=False)
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    out = reg.start(
        gv,
        goal="g",
        seed_atom_ids=[atoms[2].atom_id],
        moment_id="m1",
    )
    assert out["ok"]
    seeds = out["seed_ids"]
    assert atoms[2].atom_id in seeds
    # Temporal around explicit should pull neighbors into seed set (capped).
    assert len(seeds) >= 1
    assert len(seeds) <= 4
    assert "explicit" in out["seed_reasons"] or "temporal" in out["seed_reasons"]


def test_invalid_explicit_seed_skipped(store):
    atoms = _chain_store(store, 2)
    reg = _reg(_enabled_settings())
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    out = reg.start(
        gv,
        goal="g",
        seed_atom_ids=["a_does_not_exist", atoms[0].atom_id],
        moment_id="m1",
    )
    assert out["ok"]
    assert "a_does_not_exist" not in out["seed_ids"]
    assert atoms[0].atom_id in out["seed_ids"]


# ── PR5 pure semantic start + dual slot reserve ─────────────────────────────


def test_pr5_defaults_max_seeds_and_start_ms():
    """Product defaults: traverse_max_seeds=10, start expand 250ms."""
    from elyra.memory.config import MemorySettings as MS

    s = MS()
    assert s.traverse_max_seeds == 10
    assert s.traverse_start_expand_max_ms == 250
    assert s.traverse_dual_start is True
    assert s.traverse_dual_start_n == 2
    assert s.traverse_default_seed_mode == "auto"


def test_start_semantic_deadline_uses_wait_not_start_ms(store):
    """Semantic start ANN budget = wait ceiling, not traverse_start_expand_max_ms."""
    from elyra.memory.config import effective_semantic_wait_max_ms
    from elyra.memory.embed.mock import MockEmbedder, mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet
    from elyra.memory.index import MemoryEmbeddingIndex

    atoms = _chain_store(store, 3)
    settings = _enabled_settings(
        semantic_wait_for_select=True,
        semantic_wait_max_ms=12_000,
        traverse_start_expand_max_ms=250,
        traverse_expand_max_ms=120,
        traverse_dual_start=False,
    )
    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store=store)
    for a in atoms:
        text = a.content_text or "theme"
        vec = mock_vector(f"text|{text}", dim=EMBED_DIM)
        idx.upsert(
            EmbeddingSet(
                atom_id=a.atom_id,
                dim=EMBED_DIM,
                emb_text=vec,
                emb_joint=vec,
                model_id="mock",
                encoded_at="2026-07-28T10:00:00Z",
            )
        )
    reg = _reg(settings)
    gv = GraphView(
        store,
        index=idx,
        embedder=emb,
        settings=settings,
        now="2026-07-28T10:05:00Z",
    )
    out = reg.start(
        gv,
        goal="memory about topic",
        seed_query="memory about topic",
        seed_mode="semantic_only",
        moment_id="m1",
    )
    assert out["ok"] is True
    assert out["start_ms_budget"] == 250  # structural/reporting only
    assert out["semantic_ms_budget"] == effective_semantic_wait_max_ms(settings)
    assert out["semantic_ms_budget"] == 12_000
    assert out["semantic_ms_budget"] != out["start_ms_budget"]
    assert out["budget"]["semantic_ms_budget_step"] == 12_000


def test_start_semantic_snappy_when_wait_off(store):
    """Wait disabled → traverse snappy ANN budget, not 250 start_ms."""
    from elyra.memory.config import snappy_ann_max_ms
    from elyra.memory.index import MemoryEmbeddingIndex

    _chain_store(store, 3)
    settings = _enabled_settings(
        semantic_wait_for_select=False,
        semantic_select_max_ms=40,
        traverse_expand_max_ms=120,
        traverse_start_expand_max_ms=250,
        traverse_dual_start=False,
    )
    reg = _reg(settings)
    gv = GraphView(
        store,
        index=MemoryEmbeddingIndex(store=store),
        embedder=None,  # cold → empty but budgets still reported
        settings=settings,
        now="2026-07-28T10:05:00Z",
    )
    out = reg.start(
        gv,
        goal="anything",
        seed_query="anything",
        seed_mode="semantic_only",
        moment_id="m1",
    )
    assert out["ok"] is True
    assert out["semantic_ms_budget"] == snappy_ann_max_ms(settings, "traverse")
    assert out["semantic_ms_budget"] == min(120, 40)
    assert out["semantic_ms_budget"] != 250


def test_step_at_most_one_semantic_ann_call(store):
    """Multi expand_ids → at most one semantic_hop ANN; structural multi-id."""
    atoms = _chain_store(store, 6)
    settings = _enabled_settings(
        semantic_wait_for_select=True,
        semantic_wait_max_ms=9_000,
        traverse_max_expand_per_step=5,
        traverse_keep_adjacent=False,
    )
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    start = reg.start(
        gv,
        goal="walk",
        seed_atom_ids=[atoms[1].atom_id, atoms[2].atom_id, atoms[3].atom_id],
        seed_mode="explicit_only",
        moment_id="m1",
    )
    assert start["ok"] is True
    sid = start["session_id"]
    # Expand three ids in one step — ANN bound is one shared call.
    step = reg.step(
        gv,
        session_id=sid,
        expand_ids=[atoms[1].atom_id, atoms[2].atom_id, atoms[3].atom_id],
    )
    assert step["ok"] is True
    budget = step["budget"]
    assert budget["semantic_ms_budget_step"] == 9_000
    # One ANN attempt (allow_semantic on first expand_id only).
    assert budget["semantic_ann_calls_last"] == 1
    # Structural multi-id still expands neighbors.
    assert step["considered_count"] >= 3
    assert len(step.get("newly_expanded") or []) >= 1


def test_step_wait_on_slow_ann_still_packs_edges(store, monkeypatch):
    """Wait-on slow ANN must not discard structural+semantic results via expand_ms.

    Regression for review Issue 1: dual-deadline neighbors returns edges after
    a "slow" ANN, but packing must not apply expand_ms wall-clock (which would
    drop all edges when ANN exceeds ~120ms structural budget).
    """
    from elyra.memory.graph import EDGE_SEMANTIC_HOP, EDGE_SEQUENTIAL, GraphEdge

    atoms = _chain_store(store, 5)
    settings = _enabled_settings(
        semantic_wait_for_select=True,
        semantic_wait_max_ms=5_000,
        traverse_expand_max_ms=20,  # tiny structural wall
        traverse_max_expand_per_step=5,
        traverse_keep_adjacent=False,
    )
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    start = reg.start(
        gv,
        goal="pack after slow ann",
        seed_atom_ids=[atoms[1].atom_id, atoms[2].atom_id],
        seed_mode="explicit_only",
        moment_id="m1",
    )
    assert start["ok"] is True
    sid = start["session_id"]

    # Fake neighbors: structural returns sequential edges quickly; ANN pass
    # returns a semantic_hop edge but reports large semantic_ms_spent.
    real_neighbors = gv.neighbors
    call_n = {"n": 0}

    def fake_neighbors(atom_id, **kwargs):
        call_n["n"] += 1
        allow_sem = bool(kwargs.get("allow_semantic", True))
        kinds = kwargs.get("kinds")
        # Phase B pure semantic hop call
        if allow_sem and kinds is not None and list(kinds) == [EDGE_SEMANTIC_HOP]:
            # Simulate slow ANN under wait ceiling — wall clock would exceed expand_ms.
            dst = atoms[3].atom_id
            edges = [
                GraphEdge(
                    src_atom_id=str(atom_id),
                    dst_atom_id=dst,
                    edge_kind=EDGE_SEMANTIC_HOP,
                    weight=0.9,
                    reason="semantic:joint",
                )
            ]
            gv._last_expand_meta = {  # noqa: SLF001
                "dual_deadline": True,
                "structural_ms_spent": 0,
                "semantic_ms_spent": 4500,  # >> expand_ms 20
                "structural_truncated": False,
                "semantic_truncated": False,
                "expand_truncated": False,
                "semantic_ms_budget": 5_000,
            }
            return edges
        # Phase A structural-only
        assert allow_sem is False
        # Return one sequential neighbor from real store projection when possible
        edges = real_neighbors(
            atom_id,
            k=kwargs.get("k"),
            exclude_ids=kwargs.get("exclude_ids"),
            allow_semantic=False,
            expand_deadline_ms=kwargs.get("expand_deadline_ms"),
            semantic_deadline_ms=0,
        )
        # Ensure meta reports small structural spend
        meta = dict(gv.last_expand_meta)
        meta["structural_ms_spent"] = min(5, int(meta.get("structural_ms_spent") or 5))
        meta["semantic_ms_spent"] = 0
        meta["dual_deadline"] = True
        gv._last_expand_meta = meta  # noqa: SLF001
        return edges

    monkeypatch.setattr(gv, "neighbors", fake_neighbors)

    step = reg.step(
        gv,
        session_id=sid,
        expand_ids=[atoms[1].atom_id, atoms[2].atom_id],
    )
    assert step["ok"] is True
    budget = step["budget"]
    assert budget["semantic_ann_calls_last"] == 1
    assert budget["semantic_ms_budget_step"] == 5_000
    # ANN spend reported; expand_ms spend is structural-only (not 4500).
    assert budget["semantic_ms_spent_last"] >= 4500
    assert budget["expand_ms_spent_last"] < 100  # not charged ANN wait
    newly = step.get("newly_expanded") or []
    # Must pack edges despite "slow" ANN (not empty after wait).
    assert len(newly) >= 1, newly
    # Semantic hop destination packed.
    assert atoms[3].atom_id in newly or atoms[3].atom_id in (
        step.get("keep_set") or []
    ) or any(
        n.get("atom_id") == atoms[3].atom_id
        for n in (step.get("frontier") or [])
        if isinstance(n, dict)
    ) or atoms[3].atom_id in set(
        getattr(reg.active_session, "considered", {}) or {}
    )
    # Multi-id structural phase ran (2 structural calls + 1 ANN = at least 3).
    assert call_n["n"] >= 3


def test_seed_mode_semantic_only_encoder_cold_empty_frontier(store):
    """Cold encoder + semantic_only → empty seeds; encoder_cold honesty; no temporal."""
    from elyra.memory.index import MemoryEmbeddingIndex

    _chain_store(store, 5)
    settings = _enabled_settings(
        traverse_dual_start=True,
        traverse_max_seeds=10,
    )
    reg = _reg(settings)
    # Index present but embedder None → encoder_cold (never torch load).
    gv = GraphView(
        store,
        index=MemoryEmbeddingIndex(store=store),
        embedder=None,
        settings=settings,
        now="2026-07-28T10:05:00Z",
    )
    out = reg.start(
        gv,
        goal="focused topic",
        seed_query="focused topic",
        seed_mode="semantic_only",
        moment_id="m1",
    )
    assert out["ok"] is True
    assert out["seed_mode"] == "semantic_only"
    assert out["dual_n"] == 0
    assert out["seed_ids"] == []
    assert (out.get("seed_sources") or {}).get("temporal", 0) == 0
    assert (out.get("seed_sources") or {}).get("semantic", 0) == 0
    assert out.get("semantic_reason") == "encoder_cold" or "encoder_cold" in (
        out.get("seed_reasons") or []
    )
    assert out.get("frontier") == [] or len(out.get("frontier") or []) == 0


def test_seed_mode_semantic_only_no_temporal_fill_when_no_index(store):
    atoms = _chain_store(store, 4)
    settings = _enabled_settings(traverse_max_seeds=8)
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    out = reg.start(
        gv,
        goal="g",
        seed_mode="semantic_only",
        seed_atom_ids=[atoms[0].atom_id],
        moment_id="m1",
    )
    assert out["ok"]
    src = out.get("seed_sources") or {}
    assert src.get("temporal", 0) == 0
    assert atoms[0].atom_id in out["seed_ids"]
    # Only explicit (semantic unavailable without index).
    assert set(out["seed_ids"]) == {atoms[0].atom_id}


def test_dual_start_reserves_temporal_slots(store):
    """High semantic k + dual_start → temporal in {1,2} and semantic > 0."""
    from elyra.memory.embed.mock import MockEmbedder, mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet
    from elyra.memory.index import MemoryEmbeddingIndex

    # Plant many atoms with the same vector as the query so ANN fills top-k.
    query = "shared theme"
    match = mock_vector(f"text|{query}", dim=EMBED_DIM)
    atoms = []
    for i in range(12):
        a = store.put_atom(
            _atom(
                atom_id=f"a_dual{i}",
                t=f"2026-07-28T10:{i:02d}:00Z",
                text=f"body {i} {query}",
                moment_id="m_dual",
            )
        )
        atoms.append(a)
    # Link temporal chain so seed_temporal has prev/next.
    for i, a in enumerate(atoms):
        prev_id = atoms[i - 1].atom_id if i > 0 else None
        next_id = atoms[i + 1].atom_id if i + 1 < len(atoms) else None
        store.put_atom(
            Atom(
                atom_id=a.atom_id,
                t_start=a.t_start,
                kind=a.kind,
                content_text=a.content_text,
                content_ref=a.content_ref,
                moment_id=a.moment_id,
                prev_atom_id=prev_id,
                next_atom_id=next_id,
            )
        )

    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store=store)
    for a in atoms:
        idx.upsert(
            EmbeddingSet(
                atom_id=a.atom_id,
                dim=EMBED_DIM,
                emb_text=match,
                emb_joint=match,
                model_id="mock",
                encoded_at="2026-07-28T10:00:00Z",
            )
        )

    settings = _enabled_settings(
        traverse_max_seeds=10,
        traverse_dual_start=True,
        traverse_dual_start_n=2,
        traverse_start_expand_max_ms=250,
    )
    reg = _reg(settings)
    gv = GraphView(
        store,
        index=idx,
        embedder=emb,
        settings=settings,
        now="2026-07-28T10:30:00Z",
    )
    out = reg.start(
        gv,
        goal=query,
        seed_query=query,
        seed_mode="auto",
        moment_id="m_other",  # exclude open moment? use different so ANN unrestricted
    )
    assert out["ok"] is True
    assert out["seed_mode"] == "auto"
    assert out["dual_n"] == 2
    src = out["seed_sources"]
    assert src["semantic"] > 0
    assert src["temporal"] in (1, 2)
    # Semantic must not starve dual reserve: semantic ≤ max_seeds - dual_n.
    assert src["semantic"] <= 10 - 2
    assert len(out["seed_ids"]) <= 10
    assert src["semantic"] + src["temporal"] + src.get("explicit", 0) == len(
        out["seed_ids"]
    )


def test_dual_start_off_no_reserve(store):
    from elyra.memory.embed.mock import MockEmbedder, mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet
    from elyra.memory.index import MemoryEmbeddingIndex

    query = "theme x"
    match = mock_vector(f"text|{query}", dim=EMBED_DIM)
    a1 = store.put_atom(_atom(atom_id="a_off1", text=query, moment_id="m1"))
    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store=store)
    idx.upsert(
        EmbeddingSet(
            atom_id=a1.atom_id,
            dim=EMBED_DIM,
            emb_text=match,
            emb_joint=match,
            model_id="mock",
            encoded_at="2026-07-28T10:00:00Z",
        )
    )
    settings = _enabled_settings(
        traverse_max_seeds=4,
        traverse_dual_start=False,
    )
    reg = _reg(settings)
    gv = GraphView(
        store, index=idx, embedder=emb, settings=settings, now="2026-07-28T10:05:00Z"
    )
    # Use a different open moment so ANN is not exclude_moment_id-filtered.
    out = reg.start(
        gv, goal=query, seed_query=query, seed_mode="auto", moment_id="m_other"
    )
    assert out["dual_n"] == 0
    assert out["seed_sources"]["semantic"] >= 1


def test_seed_mode_temporal_only_skips_semantic(store):
    atoms = _chain_store(store, 5)
    settings = _enabled_settings(traverse_max_seeds=3)
    reg = _reg(settings)
    # Even with a warm-looking graph, temporal_only must not call semantic path
    # in a way that fails start; sources should be temporal only.
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    out = reg.start(
        gv,
        goal="should not semantic",
        seed_mode="temporal",  # alias
        moment_id="m1",
    )
    assert out["ok"]
    assert out["seed_mode"] == "temporal_only"
    assert out["seed_sources"]["semantic"] == 0
    assert out["seed_sources"]["temporal"] >= 1
    assert len(out["seed_ids"]) <= 3
    # Atoms from the chain should appear via temporal.
    assert any(a.atom_id in out["seed_ids"] for a in atoms)


def test_seed_mode_explicit_only(store):
    atoms = _chain_store(store, 5)
    reg = _reg(_enabled_settings(traverse_max_seeds=8))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    out = reg.start(
        gv,
        goal="g",
        seed_mode="explicit_only",
        seed_atom_ids=[atoms[2].atom_id],
        moment_id="m1",
    )
    assert out["seed_ids"] == [atoms[2].atom_id]
    assert out["seed_sources"]["temporal"] == 0
    assert out["seed_sources"]["semantic"] == 0


def test_auto_collapse_to_temporal_when_semantic_empty(store):
    atoms = _chain_store(store, 6)
    settings = _enabled_settings(
        traverse_max_seeds=4,
        traverse_dual_start=True,
        traverse_dual_start_n=2,
    )
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    out = reg.start(
        gv,
        goal="no index here",
        seed_mode="auto",
        moment_id="m1",
    )
    assert out["ok"]
    # Collapse path: full temporal strip (not just dual_n).
    assert out["seed_sources"]["temporal"] >= 1
    assert out["seed_sources"]["semantic"] == 0
    assert len(out["seed_ids"]) <= 4
    assert any(a.atom_id in out["seed_ids"] for a in atoms)


def test_start_payload_includes_pr5_fields(store):
    settings = _enabled_settings()
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    out = reg.start(gv, goal="probe", seed_mode="auto", moment_id="m1")
    assert out["ok"]
    assert "seed_sources" in out
    assert "dual_n" in out
    assert "seed_mode" in out
    assert "start_ms_budget" in out
    assert out["start_ms_budget"] == 250 or out["start_ms_budget"] > 0
    assert "start_ms_spent" in out
    assert "semantic_reason" in out


def test_seed_from_query_text_hits(store):
    from elyra.memory.embed.mock import MockEmbedder, mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet
    from elyra.memory.index import MemoryEmbeddingIndex

    a1 = store.put_atom(_atom(atom_id="a_q1", text="blue sky"))
    a2 = store.put_atom(_atom(atom_id="a_q2", text="green grass"))
    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store=store)
    match = mock_vector("text|blue sky", dim=EMBED_DIM)
    other = mock_vector("text|green grass", dim=EMBED_DIM)
    for atom, vec in ((a1, match), (a2, other)):
        idx.upsert(
            EmbeddingSet(
                atom_id=atom.atom_id,
                dim=EMBED_DIM,
                emb_text=vec,
                emb_joint=vec,
                model_id="mock",
                encoded_at="2026-07-28T10:00:00Z",
            )
        )
    gv = GraphView(store, index=idx, embedder=emb)
    seeds = gv.seed_from_query("blue sky", k=5)
    assert seeds
    assert seeds[0][0] == a1.atom_id
    assert gv.last_expand_meta.get("seed") == "text"


def test_seed_from_query_encoder_cold_no_encode(store):
    """Never call encode when embedder is cold/missing."""

    class _BoomEmbedder:
        loaded = False

        def health(self):
            return {"ok": False}

        def encode_text(self, text: str):
            raise AssertionError("must not cold-load / encode when not warm")

    from elyra.memory.index import MemoryEmbeddingIndex

    idx = MemoryEmbeddingIndex(store=store)
    gv = GraphView(store, index=idx, embedder=_BoomEmbedder())
    seeds = gv.seed_from_query("anything")
    assert seeds == []
    assert gv.last_expand_meta.get("semantic_reason") == "encoder_cold"


# ── keep / inspect / summary ────────────────────────────────────────────────


def test_keep_must_be_considered(store):
    atoms = _chain_store(store, 3)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    # atoms[2] may not be considered yet if not seeded.
    out = reg.step(gv, keep_ids=[atoms[0].atom_id, "a_unknown_keep"])
    assert atoms[0].atom_id in out["keep_set"]
    assert "a_unknown_keep" not in out["keep_set"]


def test_keep_max_cap(store):
    atoms = _chain_store(store, 5)
    settings = _enabled_settings(traverse_keep_max=2, traverse_keep_adjacent=False)
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    reg.start(
        gv,
        goal="g",
        seed_atom_ids=[a.atom_id for a in atoms],
        moment_id="m1",
    )
    out = reg.step(gv, keep_ids=[a.atom_id for a in atoms])
    assert len(out["keep_set"]) <= 2


def test_inspect_atoms_caps(store):
    atoms = _chain_store(store, 3)
    long = _atom(
        atom_id="a_long",
        text="X" * 5000,
        moment_id="m1",
    )
    store.put_atom(long)
    previews = inspect_atoms(
        store,
        [long.atom_id, atoms[0].atom_id, "missing"],
        settings=MemorySettings(
            traverse_inspect_chars_per_id=100,
            traverse_inspect_max_ids=2,
            traverse_inspect_max_total_chars=150,
        ),
    )
    assert len(previews) <= 2
    assert previews[0].atom_id == "a_long"
    assert len(previews[0].body) <= 100
    assert previews[0].truncated is True


def test_walk_summary_template():
    from elyra.memory.traverse import (
        BudgetState,
        ConsideredNode,
        TraversalSession,
    )

    sess = TraversalSession(
        session_id="tr_x",
        goal="haiku collection",
        status="confirmed",
        seed_ids=("a1",),
        considered={
            "a1": ConsideredNode(
                atom_id="a1",
                kind="observation",
                label="first haiku",
                preview="first haiku body",
                via_edge_kind=None,
                via_reason="seed",
                depth=0,
                weight=1.0,
            )
        },
        frontier=[],
        keep_ids=["a1"],
        scratchpad="",
        budgets=BudgetState(steps_spent=2),
        created_at="t0",
        updated_at="t1",
        seed_reasons=["explicit", "temporal"],
        edge_kind_counts={"sequential": 3},
    )
    nl = build_walk_summary_nl(sess, summary_hint="nice finds")
    assert "haiku collection" in nl
    assert "Considered 1 atoms across 2 steps" in nl
    assert "sequential=3" in nl
    assert "edges walked: sequential=3" in nl
    assert "Kept 1" in nl
    assert "nice finds" in nl


def test_walk_summary_edges_walked_none_honesty():
    """KD-P-glass §5.2: empty hist says 'edges walked: none' not bare 'none'."""
    from elyra.memory.traverse import (
        BudgetState,
        ConsideredNode,
        TraversalSession,
    )

    sess = TraversalSession(
        session_id="s-empty-edges",
        goal="quiet walk",
        status="confirmed",
        seed_ids=("a1",),
        considered={
            "a1": ConsideredNode(
                atom_id="a1",
                kind="observation",
                label="seed only",
                preview="seed",
                via_edge_kind=None,
                via_reason="seed",
                depth=0,
                weight=1.0,
            )
        },
        frontier=[],
        keep_ids=["a1"],
        scratchpad="",
        budgets=BudgetState(steps_spent=0),
        created_at="t0",
        updated_at="t1",
        seed_reasons=["explicit"],
        edge_kind_counts={},
    )
    nl = build_walk_summary_nl(sess)
    assert "edges walked: none" in nl
    assert "edges: none" not in nl


# ── moment close + clear ────────────────────────────────────────────────────


def test_moment_close_retains_last_session_and_meal_tray(store):
    """KD-P-glass §5.1: moment close abandons active only; last_session sticky.

    Meal tray retained (B5 / KD-A16). Glass last walk process-life sticky.
    """
    atoms = _chain_store(store, 2)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg.finish(keep_ids=[atoms[0].atom_id])
    assert reg.last_session is not None
    last_sid = reg.last_session.session_id
    assert reg.last_confirmed_keep is not None
    ids_before, _ = reg.get_meal_keep_ids()
    assert atoms[0].atom_id in ids_before
    reg.on_moment_close("m1")
    assert reg.active_session is None
    # KD-P-glass: last finished walk retained across moment close.
    assert reg.last_session is not None
    assert reg.last_session.session_id == last_sid
    assert reg.last_session.status == "confirmed"
    # Meal keep retained (tray + thin snap for compat).
    assert reg.last_confirmed_keep is not None
    ids_after, summary = reg.get_meal_keep_ids()
    assert atoms[0].atom_id in ids_after
    assert summary is not None
    # Graph GET default shows sticky last walk.
    view = reg.get_graph_session_view()
    assert view.which == "last"
    assert view.has_last_session is True
    assert view.session is not None
    assert view.session["session_id"] == last_sid


def test_directed_keep_survives_moment_close(store):
    """B5: confirm → close moment → meal keep still available."""
    atoms = _chain_store(store, 2)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m_a")
    reg.finish(keep_ids=[atoms[0].atom_id])
    reg.on_moment_close("m_a")
    ids, summary = reg.get_meal_keep_ids()
    assert ids == [atoms[0].atom_id]
    assert summary


def test_directed_keep_packs_across_moment_ids(store):
    """B5b: confirm in moment A, meal read under open moment B still packs."""
    atoms = _chain_store(store, 2, moment_id="m_a")
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m_a")
    reg.finish(keep_ids=[atoms[0].atom_id])
    # Snap has moment_id=m_a; meal path must NOT require open == m_a.
    snap = reg.get_last_confirmed_keep("m_b")
    assert snap is None  # equality filter still on thin snap API
    ids, _ = reg.get_meal_keep_ids()  # tray: no moment filter
    assert atoms[0].atom_id in ids


def test_confirm_then_compose_same_process_sees_union(store):
    """KD-TRAY-SOT: two confirms then meal ids see union without restart."""
    atoms = _chain_store(store, 3)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g1", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg.finish(keep_ids=[atoms[0].atom_id])
    reg.start(gv, goal="g2", seed_atom_ids=[atoms[1].atom_id], moment_id="m1")
    reg.finish(keep_ids=[atoms[1].atom_id])
    ids, _ = reg.get_meal_keep_ids()
    assert set(ids) >= {atoms[0].atom_id, atoms[1].atom_id}


def test_clear_confirmed_keep_optional_glass(store):
    atoms = _chain_store(store, 2)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg.finish(keep_ids=[atoms[0].atom_id])
    reg.clear_confirmed_keep(clear_glass=False)
    assert reg.last_confirmed_keep is None
    assert reg.get_meal_keep_ids()[0] == []  # tray cleared with operator clear
    assert reg.last_session is not None  # glass retained
    reg.clear_confirmed_keep(clear_glass=True)
    assert reg.last_session is None


# ── update_keep (#104 / KD-K1–K7) ───────────────────────────────────────────


def test_update_keep_merge_replace_clear_remove(paths, store):
    """merge / replace / remove_ids / empty-replace clear + thin snap sync."""
    settings = _enabled_settings(traverse_keep_adjacent=False)
    reg = TraversalRegistry(
        settings=settings,
        paths=paths,
        now_fn=lambda: "2026-07-28T12:00:00Z",
    )

    # merge pin
    out = reg.update_keep(mode="merge", atom_ids=["a1", "a2"], note="first pins")
    assert out["ok"] is True
    assert out["mode"] == "merge"
    assert set(out["atom_ids"]) == {"a1", "a2"}
    assert out["entry_count"] == 2
    assert out["walk_summary_nl"] == "first pins"
    assert out["meal_timing"] == "next_compose"
    snap = reg.last_confirmed_keep
    assert snap is not None
    assert set(snap.keep_ids) == {"a1", "a2"}
    assert snap.session_id == "keep_update"
    assert snap.walk_summary_nl == "first pins"

    # merge reinforce + add
    out2 = reg.update_keep(mode="merge", atom_ids=["a2", "a3"])
    assert out2["ok"] is True
    assert set(out2["atom_ids"]) == {"a1", "a2", "a3"}
    tray = reg.ensure_tray()
    assert tray.entry_map()["a2"].last_reinforced_at == "2026-07-28T12:00:00Z"
    assert tray.walk_summary_nl == "first pins"  # note omitted → summary retained

    # remove_ids under merge
    out3 = reg.update_keep(mode="merge", remove_ids=["a1", "missing"])
    assert out3["ok"] is True
    assert set(out3["atom_ids"]) == {"a2", "a3"}
    assert out3["removed"] == ["a1"]
    assert set(reg.last_confirmed_keep.keep_ids) == {"a2", "a3"}

    # replace with new set
    out4 = reg.update_keep(
        mode="replace", atom_ids=["b1"], note="replaced", moment_id="m9"
    )
    assert out4["ok"] is True
    assert out4["atom_ids"] == ["b1"]
    assert out4["walk_summary_nl"] == "replaced"
    assert reg.last_confirmed_keep is not None
    assert reg.last_confirmed_keep.keep_ids == ("b1",)
    assert reg.last_confirmed_keep.moment_id == "m9"

    # empty replace = clear (summary null)
    out5 = reg.update_keep(mode="replace", atom_ids=[])
    assert out5["ok"] is True
    assert out5["atom_ids"] == []
    assert out5["entry_count"] == 0
    assert out5["walk_summary_nl"] is None
    assert reg.last_confirmed_keep is None
    assert reg.get_meal_keep_ids() == ([], None)
    # disk empty
    loaded = load_directed_keep_tray(paths)
    assert loaded.entries == []
    assert loaded.walk_summary_nl is None
    assert tray_runtime_path(paths.data_dir).is_file()

    # empty replace with note keeps annotate-only summary, still no entries/snap
    reg.update_keep(mode="merge", atom_ids=["z1"])
    out6 = reg.update_keep(mode="replace", atom_ids=[], note="cleared with note")
    assert out6["ok"] is True
    assert out6["atom_ids"] == []
    assert out6["walk_summary_nl"] == "cleared with note"
    assert reg.last_confirmed_keep is None
    ids, summary = reg.get_meal_keep_ids()
    assert ids == []
    assert summary == "cleared with note"


def test_update_keep_disabled_fail_closed_no_mutate(paths):
    """Fail closed when keep off — no tray/snap mutation."""
    settings = MemorySettings(
        directed_traversal_enabled=False,
        directed_keep_enabled=False,
    )
    now = "2026-07-28T12:00:00Z"
    reg = TraversalRegistry(
        settings=settings, paths=paths, now_fn=lambda: now
    )
    # Seed RAM tray as if prior state existed (should not change on fail).
    tray = reg.ensure_tray()
    tray.merge_confirm(["seed"], now=now, walk_summary_nl="seed")
    reg._last_confirmed_keep = None  # noqa: SLF001 — intentional hermetic seed
    before_ids = list(tray.atom_ids())
    out = reg.update_keep(mode="merge", atom_ids=["new_id"])
    assert out["ok"] is False
    assert out["error_reason"] == ERROR_KEEP_DISABLED
    assert reg.ensure_tray().atom_ids() == before_ids
    assert reg.last_confirmed_keep is None


def test_update_keep_merge_noop_invalid_args(paths):
    settings = MemorySettings(directed_keep_enabled=True)
    reg = TraversalRegistry(settings=settings, paths=paths)
    out = reg.update_keep(mode="merge", atom_ids=[], remove_ids=[])
    assert out["ok"] is False
    assert out["error_reason"] == ERROR_INVALID_ARGS
    assert reg.directed_keep_tray is None or reg.ensure_tray().atom_ids() == []


def test_update_keep_no_active_session_required(paths):
    """Keep update works with no walk; keep-only flag (not traversal) is enough."""
    settings = MemorySettings(
        directed_traversal_enabled=False,
        directed_keep_enabled=True,
    )
    reg = TraversalRegistry(
        settings=settings,
        paths=paths,
        now_fn=lambda: "2026-07-28T13:00:00Z",
    )
    assert reg.active_session is None
    out = reg.update_keep(mode="replace", atom_ids=["solo"])
    assert out["ok"] is True
    assert out["atom_ids"] == ["solo"]
    assert reg.get_meal_keep_ids()[0] == ["solo"]


def test_update_keep_disk_after_clear(paths):
    settings = MemorySettings(directed_keep_enabled=True)
    reg = TraversalRegistry(
        settings=settings,
        paths=paths,
        now_fn=lambda: "2026-07-28T14:00:00Z",
    )
    reg.update_keep(mode="merge", atom_ids=["d1", "d2"], note="disk")
    assert tray_runtime_path(paths.data_dir).is_file()
    reg.update_keep(mode="replace", atom_ids=[])
    reloaded = load_directed_keep_tray(paths)
    assert reloaded.atom_ids() == []
    assert reloaded.walk_summary_nl is None
    # New registry reloads empty
    reg2 = TraversalRegistry(settings=settings, paths=paths)
    assert reg2.get_meal_keep_ids() == ([], None)


def test_update_keep_replace_without_note_nulls_summary(paths):
    """Non-empty replace is full tray replace: prior summary dropped unless note."""
    settings = MemorySettings(directed_keep_enabled=True)
    reg = TraversalRegistry(
        settings=settings,
        paths=paths,
        now_fn=lambda: "2026-07-28T14:30:00Z",
    )
    reg.update_keep(mode="merge", atom_ids=["a1", "a2"], note="prior summary")
    assert reg.ensure_tray().walk_summary_nl == "prior summary"
    out = reg.update_keep(mode="replace", atom_ids=["b1"])  # no note
    assert out["ok"] is True
    assert out["atom_ids"] == ["b1"]
    assert out["walk_summary_nl"] is None
    assert reg.ensure_tray().walk_summary_nl is None
    assert reg.last_confirmed_keep is not None
    assert reg.last_confirmed_keep.walk_summary_nl == ""


def test_update_keep_replace_with_remove_ids(paths):
    """replace base then remove_ids drops from the new set."""
    settings = MemorySettings(directed_keep_enabled=True)
    reg = TraversalRegistry(
        settings=settings,
        paths=paths,
        now_fn=lambda: "2026-07-28T14:31:00Z",
    )
    out = reg.update_keep(
        mode="replace",
        atom_ids=["r1", "r2", "r3"],
        remove_ids=["r2", "missing"],
        note="after remove",
    )
    assert out["ok"] is True
    assert set(out["atom_ids"]) == {"r1", "r3"}
    assert out["removed"] == ["r2"]
    assert out["walk_summary_nl"] == "after remove"
    assert set(reg.last_confirmed_keep.keep_ids) == {"r1", "r3"}


def test_update_keep_merge_remove_all_clears_snap_retains_summary(paths):
    """Merge that drops every pin: empty tray, summary retained, thin snap None."""
    settings = MemorySettings(directed_keep_enabled=True)
    reg = TraversalRegistry(
        settings=settings,
        paths=paths,
        now_fn=lambda: "2026-07-28T14:32:00Z",
    )
    reg.update_keep(mode="merge", atom_ids=["x1", "x2"], note="still relevant")
    out = reg.update_keep(mode="merge", remove_ids=["x1", "x2"])
    assert out["ok"] is True
    assert out["atom_ids"] == []
    assert set(out["removed"]) == {"x1", "x2"}
    assert out["walk_summary_nl"] == "still relevant"
    assert reg.last_confirmed_keep is None
    ids, summary = reg.get_meal_keep_ids()
    assert ids == []
    assert summary == "still relevant"


def test_update_keep_disabled_does_not_touch_disk(paths):
    """Fail-closed when keep off: existing disk tray file left unchanged."""
    now = "2026-07-28T14:33:00Z"
    # Seed disk with sticky pins while keep is on.
    on = MemorySettings(directed_keep_enabled=True)
    seed = TraversalRegistry(settings=on, paths=paths, now_fn=lambda: now)
    seed.update_keep(mode="merge", atom_ids=["disk_seed"], note="on disk")
    path = tray_runtime_path(paths.data_dir)
    assert path.is_file()
    before_text = path.read_text(encoding="utf-8")
    assert load_directed_keep_tray(paths).atom_ids() == ["disk_seed"]

    off = MemorySettings(
        directed_traversal_enabled=False, directed_keep_enabled=False
    )
    reg = TraversalRegistry(settings=off, paths=paths, now_fn=lambda: now)
    # Load prior disk into RAM so we can assert it is not rewritten empty/new.
    reg.ensure_tray()
    out = reg.update_keep(mode="replace", atom_ids=["should_not_persist"])
    assert out["ok"] is False
    assert out["error_reason"] == ERROR_KEEP_DISABLED
    assert path.read_text(encoding="utf-8") == before_text
    assert load_directed_keep_tray(paths).atom_ids() == ["disk_seed"]


def test_moment_close_abandons_active_retains_prior_last(store):
    """Active mid-walk abandoned on moment close; prior finished last sticky."""
    atoms = _chain_store(store, 3)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="finished", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg.finish(keep_ids=[atoms[0].atom_id])
    last_sid = reg.last_session.session_id
    reg.start(gv, goal="mid-walk", seed_atom_ids=[atoms[1].atom_id], moment_id="m1")
    assert reg.active_session is not None
    reg.on_moment_close("m1")
    assert reg.active_session is None
    assert reg.last_session is not None
    assert reg.last_session.session_id == last_sid
    assert reg.last_session.goal == "finished"


def test_budget_surface_structural_vs_semantic_honesty(store):
    """KD-P-glass §5.2: budgets expose structural_* and semantic_* aliases."""
    atoms = _chain_store(store, 3)
    reg = _reg(
        _enabled_settings(
            traverse_keep_adjacent=False,
            traverse_expand_max_ms=90,
            semantic_wait_for_select=True,
            semantic_wait_max_ms=5_000,
        )
    )
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    out = reg.start(gv, goal="honesty", seed_atom_ids=[atoms[1].atom_id], moment_id="m1")
    assert out["ok"] is True
    reg.step(gv, expand_ids=[atoms[1].atom_id])
    fin = reg.finish(keep_ids=[atoms[1].atom_id])
    assert fin["ok"] is True
    budgets = fin["budgets"]
    assert "expand_ms_budget" in budgets
    assert budgets["structural_ms_budget"] == budgets["expand_ms_budget"]
    assert "structural_ms_spent" in budgets
    assert "semantic_ms_budget" in budgets
    assert "semantic_ms_spent" in budgets
    assert "semantic_ann_calls_last" in budgets
    # Glass enrich path also aliases.
    from elyra.memory.inspect import enrich_session_for_glass

    view = reg.get_graph_session_view()
    enriched = enrich_session_for_glass(view.session)
    assert enriched is not None
    eb = enriched["budgets"]
    assert eb["structural_ms_budget"] == budgets["expand_ms_budget"]
    assert "semantic_ms_budget" in eb
    assert "semantic_ann_calls_last" in eb


def test_step_without_active_errors(store):
    reg = _reg(_enabled_settings())
    gv = GraphView(store, settings=reg.settings)
    out = reg.step(gv, expand_ids=["a"])
    assert out["ok"] is False
    assert out["error_reason"] == ERROR_NO_ACTIVE


def test_budget_steps_gate_expand(store):
    atoms = _chain_store(store, 4)
    settings = _enabled_settings(
        traverse_max_steps=1, traverse_keep_adjacent=False
    )
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    r1 = reg.step(gv, expand_ids=[atoms[0].atom_id])
    assert r1["budget"]["steps_spent"] == 1
    assert r1["budget"]["steps_remaining"] == 0
    count_after = r1["considered_count"]
    # Second expand refused (no steps remaining).
    r2 = reg.step(gv, expand_ids=[atoms[0].atom_id])
    assert r2["considered_count"] == count_after
    # Finish still allowed.
    fin = reg.finish(keep_ids=[atoms[0].atom_id])
    assert fin["ok"] is True


# ── PR6 raised budgets + HARD_MAX clamp + frontier cache ─────────────────────


def test_pr6_product_defaults_and_hard_maxes():
    """§5.1 / §5.4 items 1–2: raised product defaults + HARD_MAX constants."""
    from elyra.memory.config import (
        MemorySettings as MS,
        TRAVERSE_FRONTIER_MAX_MAX,
        TRAVERSE_MAX_DEPTH_MAX,
        TRAVERSE_MAX_EXPAND_PER_STEP_MAX,
        TRAVERSE_MAX_NODES_MAX,
        TRAVERSE_MAX_STEPS_MAX,
        TRAVERSE_NEIGHBOR_K_MAX,
        TRAVERSE_SAME_MOMENT_K_MAX,
    )

    s = MS()
    assert s.traverse_max_depth == 5
    assert s.traverse_max_nodes == 80
    assert s.traverse_max_steps == 12
    assert s.traverse_frontier_max == 24
    assert s.traverse_max_expand_per_step == 5
    assert s.traverse_keep_max == 20
    assert s.traverse_expand_max_ms == 120
    assert s.traverse_same_moment_k == 8
    assert s.traverse_semantic_k == 10
    assert s.traverse_neighbor_k == 16

    assert TRAVERSE_MAX_DEPTH_MAX == 8
    assert TRAVERSE_MAX_NODES_MAX == 160
    assert TRAVERSE_MAX_STEPS_MAX == 24
    assert TRAVERSE_FRONTIER_MAX_MAX == 48
    assert TRAVERSE_MAX_EXPAND_PER_STEP_MAX == 10
    assert TRAVERSE_SAME_MOMENT_K_MAX == 24
    assert TRAVERSE_NEIGHBOR_K_MAX == 32


def test_clamp_budget_hard_max_not_product_default():
    """§5.4 items 4+6: clamp(request or default, 1, HARD_MAX)."""
    from elyra.memory.config import TRAVERSE_MAX_NODES_MAX
    from elyra.memory.traverse import clamp_budget

    # request 100, product default 80, hard 160 → 100 (raise above product).
    assert clamp_budget(100, 80, TRAVERSE_MAX_NODES_MAX) == 100
    # request above hard → hard.
    assert clamp_budget(999, 80, TRAVERSE_MAX_NODES_MAX) == TRAVERSE_MAX_NODES_MAX
    # request None → product default.
    assert clamp_budget(None, 80, TRAVERSE_MAX_NODES_MAX) == 80
    # request 0 → lo=1.
    assert clamp_budget(0, 80, TRAVERSE_MAX_NODES_MAX) == 1


def test_budget_override_raises_above_product_default(store):
    """Hermetic: request max_nodes=100 with product 80 hard 160 → session 100."""
    atoms = _chain_store(store, 2)
    # Product defaults deliberately tight so override must raise, not min-down.
    settings = _enabled_settings(
        traverse_max_nodes=80,
        traverse_max_steps=12,
        traverse_max_depth=5,
        traverse_keep_max=20,
        traverse_frontier_max=24,
        traverse_max_expand_per_step=5,
        traverse_neighbor_k=16,
    )
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    out = reg.start(
        gv,
        goal="budget raise",
        seed_atom_ids=[atoms[0].atom_id],
        moment_id="m1",
        budget_overrides={
            "max_nodes": 100,
            "frontier_max": 30,
            "max_expand_per_step": 7,
            "neighbor_k": 20,
        },
    )
    assert out["ok"] is True
    budget = out["budget"]
    assert budget["max_nodes"] == 100
    assert budget["frontier_max"] == 30
    assert budget["max_expand_per_step"] == 7
    assert budget["neighbor_k"] == 20
    # Cap at HARD_MAX.
    out2 = reg.start(
        gv,
        goal="budget hard",
        seed_atom_ids=[atoms[0].atom_id],
        moment_id="m1",
        budget_overrides={"max_nodes": 999},
    )
    assert out2["budget"]["max_nodes"] == 160


def test_session_moment_member_cache_on_step(store, paths):
    """§5.2: expand_moment during step populates session.moment_member_cache."""
    from elyra.memory.edges import DurableEdge, new_edge_id, open_edge_store
    from elyra.memory.graph import moment_hub_id
    from elyra.memory.weights import EDGE_IN_MOMENT

    mid = "m_cache"
    a = store.put_atom(_atom(atom_id="a_c0", text="seed", moment_id=mid))
    b = store.put_atom(_atom(atom_id="a_c1", text="peer", moment_id=mid))
    c = store.put_atom(_atom(atom_id="a_c2", text="peer2", moment_id=mid))
    edge_store = open_edge_store(
        paths, MemorySettings(write_atoms=True, backend="jsonl"), fail_soft=False
    )
    try:
        hub = moment_hub_id(mid)
        for atom in (a, b, c):
            edge_store.put_edge(
                DurableEdge(
                    edge_id=new_edge_id(),
                    src_atom_id=atom.atom_id,
                    dst_atom_id=hub,
                    edge_kind=EDGE_IN_MOMENT,
                    created_at="2026-07-28T10:00:00Z",
                    updated_at="2026-07-28T10:00:00Z",
                    reason="membership",
                )
            )
        settings = _enabled_settings(durable_edges_enabled=True)
        reg = _reg(settings)
        gv = GraphView(
            store,
            settings=settings,
            edge_store=edge_store,
            now="2026-07-28T10:05:00Z",
        )
        reg.start(
            gv,
            goal="cache",
            seed_atom_ids=[a.atom_id],
            moment_id=mid,
        )
        out = reg.step(gv, expand_ids=[a.atom_id])
        assert out["ok"] is True
        active = reg.active_session
        assert active is not None
        assert mid in active.moment_member_cache
        members = set(active.moment_member_cache[mid])
        assert a.atom_id in members
        assert b.atom_id in members
        assert c.atom_id in members
        assert active.to_view().get("moment_cache_size", 0) >= 1
        # Peers reachable via step expand alone.
        newly = set(out.get("newly_expanded") or [])
        frontier_ids = {f["atom_id"] for f in (out.get("frontier") or [])}
        assert (
            b.atom_id in newly
            or c.atom_id in newly
            or b.atom_id in frontier_ids
            or c.atom_id in frontier_ids
        )
    finally:
        edge_store.close()


def test_kind_priority_in_moment_over_same_moment(store, paths):
    """Dual same_moment + in_moment for same dst → in_moment wins (§5.3)."""
    from elyra.memory.edges import DurableEdge, new_edge_id, open_edge_store
    from elyra.memory.graph import moment_hub_id
    from elyra.memory.weights import EDGE_IN_MOMENT, EDGE_SAME_MOMENT

    mid = "m_pri"
    a = store.put_atom(_atom(atom_id="a_p0", text="seed peer", moment_id=mid))
    b = store.put_atom(_atom(atom_id="a_p1", text="dual peer", moment_id=mid))
    edge_store = open_edge_store(
        paths, MemorySettings(write_atoms=True, backend="jsonl"), fail_soft=False
    )
    try:
        hub = moment_hub_id(mid)
        for src in (a, b):
            edge_store.put_edge(
                DurableEdge(
                    edge_id=new_edge_id(),
                    src_atom_id=src.atom_id,
                    dst_atom_id=hub,
                    edge_kind=EDGE_IN_MOMENT,
                    created_at="2026-07-28T10:00:00Z",
                    updated_at="2026-07-28T10:00:00Z",
                    reason="membership",
                )
            )
        settings = _enabled_settings(durable_edges_enabled=True)
        gv = GraphView(
            store,
            settings=settings,
            edge_store=edge_store,
            now="2026-07-28T10:05:00Z",
        )
        edges = gv.neighbors(a.atom_id, k=16, allow_semantic=False)
        to_b = [e for e in edges if e.dst_atom_id == b.atom_id]
        kinds = {e.edge_kind for e in to_b}
        # Collapsed to single preferred kind: in_moment (not both).
        assert EDGE_IN_MOMENT in kinds
        assert EDGE_SAME_MOMENT not in kinds
        assert len(to_b) == 1
    finally:
        edge_store.close()


# ── worker wiring ───────────────────────────────────────────────────────────


def test_worker_graph_view_and_traversal_registry(tmp_path):
    from elyra.presence.worker import PresenceWorker
    from elyra.settings import default_settings
    from unittest.mock import MagicMock
    import threading

    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    stop = threading.Event()
    # Open store with write_atoms so graph_view can work.
    settings = default_settings()
    # default memory write_atoms True
    client = MagicMock()
    w = PresenceWorker(
        paths=paths,
        client=client,
        stop_event=stop,
        settings=settings,
    )
    assert w.traversal is not None
    # Flags off → start inert.
    store = w._ensure_memory_store()
    assert store is not None
    gv = w.graph_view()
    assert gv is not None
    out = w.traversal.start(gv, goal="x")
    assert out["ok"] is False
    assert out["error_reason"] == ERROR_TRAVERSE_DISABLED

    # Enable and use registry via worker.
    from dataclasses import replace

    w.settings = replace(
        w.settings,
        memory=replace(w.settings.memory, directed_traversal_enabled=True),
    )
    w.traversal.bind_settings(w.settings.memory)
    a = store.put_atom(
        _atom(atom_id="a_w1", text="worker seed", moment_id="mw")
    )
    gv2 = w.graph_view()
    out2 = w.traversal.start(
        gv2, goal="walk", seed_atom_ids=[a.atom_id], moment_id="mw"
    )
    assert out2["ok"] is True
    w.traversal.finish(keep_ids=[a.atom_id])
    assert w.traversal.last_confirmed_keep is not None
    last_sid = w.traversal.last_session.session_id
    w._close_traversal_for_moment("mw")
    # KD-P-glass: last_session sticky across moment close (process-life).
    assert w.traversal.last_session is not None
    assert w.traversal.last_session.session_id == last_sid
    # Meal tray retained (B5); worker meal path delegates to registry tray.
    assert w.traversal.last_confirmed_keep is not None
    meal_ids, meal_summary = w._last_confirmed_keep_for_meal("other_moment")
    assert a.atom_id in meal_ids
    assert meal_summary


# ── Host d2.5 local_map + kind filters (polish1 KD-P2 / PR2) ─────────────────


def _mixed_chain(store) -> list[Atom]:
    """Observation / tool / speak / ledger / model sequential spine for filters."""
    atoms = [
        _atom(
            atom_id="lm_obs",
            t="2026-07-28T10:00:00Z",
            kind="observation",
            text="user asked about the garden plan",
            moment_id="m_lm",
        ),
        _atom(
            atom_id="lm_tool",
            t="2026-07-28T10:01:00Z",
            kind="tool",
            text='{"raw":"huge json dump should not appear"}',
            moment_id="m_lm",
            meta={"tool_name": "run_cmd"},
        ),
        _atom(
            atom_id="lm_speak",
            t="2026-07-28T10:02:00Z",
            kind="speak",
            text="I remember planting tomatoes last week",
            moment_id="m_lm",
        ),
        _atom(
            atom_id="lm_ledger",
            t="2026-07-28T10:03:00Z",
            kind="ledger",
            text='{"ledger":"noise"}',
            moment_id="m_lm",
            meta={"tool_name": "todo"},
        ),
        _atom(
            atom_id="lm_model",
            t="2026-07-28T10:04:00Z",
            kind="model",
            text='{"thinking":"raw model chain"}',
            moment_id="m_lm",
        ),
        _atom(
            atom_id="lm_obs2",
            t="2026-07-28T10:05:00Z",
            kind="observation",
            text="follow-up about watering schedule",
            moment_id="m_lm",
        ),
    ]
    return _link_chain(store, atoms)


def test_start_includes_local_map_for_primary_seed(store):
    atoms = _chain_store(store, 5)
    settings = _enabled_settings()
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    start = reg.start(
        gv,
        goal="topic",
        seed_atom_ids=[atoms[2].atom_id],
        seed_mode="explicit_only",
        moment_id="m1",
    )
    assert start["ok"] is True
    assert "local_map" in start
    assert start["local_maps"] is None
    lm = start["local_map"]
    assert lm is not None
    assert lm["focus"]["atom_id"] == atoms[2].atom_id
    assert "edges" in lm and "ring" in lm and "compass" in lm
    assert "filters" in lm and lm["filters"]["include_noisy"] is False
    # Sequential neighbors present.
    edge_dsts = {e["dst"] for e in lm["edges"]}
    assert atoms[1].atom_id in edge_dsts or atoms[3].atom_id in edge_dsts
    # Caps.
    assert len(lm["edges"]) <= LOCAL_MAP_EDGES_CAP
    assert len(lm["ring"]) <= LOCAL_MAP_RING_CAP
    assert len(lm["compass"]["moment_peers"]) <= LOCAL_MAP_MOMENT_PEERS_CAP
    assert len(lm["compass"]["associative"]) <= LOCAL_MAP_ASSOCIATIVE_CAP
    # Compass sequential from focus prev/next.
    seq = lm["compass"]["sequential"]
    assert "prev" in seq or "next" in seq


def test_local_map_filters_noisy_kinds_default(store):
    atoms = _mixed_chain(store)
    settings = _enabled_settings()
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:10:00Z")
    # Focus speak: sequential neighbors are tool (prev) and ledger (next).
    start = reg.start(
        gv,
        goal="garden",
        seed_atom_ids=["lm_speak"],
        seed_mode="explicit_only",
        moment_id="m_lm",
        include_noisy_kinds=False,
    )
    lm = start["local_map"]
    assert lm is not None
    ring_ids = {n["atom_id"] for n in lm["ring"]}
    ring_kinds = {n.get("kind") for n in lm["ring"]}
    # Noisy kinds must not appear on the ring by default.
    assert "tool" not in ring_kinds
    assert "ledger" not in ring_kinds
    assert "model" not in ring_kinds
    assert "lm_tool" not in ring_ids
    assert "lm_ledger" not in ring_ids
    # Sequential bridges to noisy dsts are listed with bridge_noisy.
    bridge_edges = [e for e in lm["edges"] if e.get("bridge_noisy")]
    assert bridge_edges, "expected sequential bridges to noisy neighbors"
    for e in bridge_edges:
        assert e["edge_kind"] == "sequential"
        assert e["dst_kind"] in ("tool", "ledger", "model")
        # Hygiene labels — not raw JSON.
        assert "{" not in (e.get("dst_label") or "")
        if e["dst_kind"] == "tool":
            assert (e.get("dst_label") or "").startswith("tool:")
        if e["dst_kind"] == "ledger":
            assert (e.get("dst_label") or "").startswith("ledger:")
    omitted = set(lm["filters"]["noisy_kinds_omitted"])
    assert omitted & {"tool", "ledger", "model"}


def test_local_map_include_noisy_kinds(store):
    _mixed_chain(store)
    settings = _enabled_settings()
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:10:00Z")
    start = reg.start(
        gv,
        goal="garden",
        seed_atom_ids=["lm_speak"],
        seed_mode="explicit_only",
        moment_id="m_lm",
        include_noisy_kinds=True,
    )
    lm = start["local_map"]
    assert lm is not None
    assert lm["filters"]["include_noisy"] is True
    ring_kinds = {n.get("kind") for n in lm["ring"]}
    # With include_noisy, tool/ledger may sit on the ring.
    assert ring_kinds & {"tool", "ledger", "model"}


def test_local_map_non_sequential_noisy_edges_omitted(store):
    """Non-sequential edges to tool/ledger are dropped when filtering."""
    # Build speak + tool with same_moment path only (no sequential between them
    # for the focus pair we care about). Use mixed chain focus obs2 which has
    # sequential prev=model only.
    _mixed_chain(store)
    settings = _enabled_settings()
    gv = GraphView(store, settings=settings, now="2026-07-28T10:10:00Z")
    # Direct unit: build_local_map on speak filters created_with-like noise via
    # only keeping sequential bridges — model is sequential next of ledger, not of speak.
    lm = build_local_map(
        gv, "lm_speak", include_noisy=False, expand_deadline_ms=80
    )
    assert lm is not None
    for e in lm["edges"]:
        if e.get("dst_kind") in ("tool", "ledger", "model"):
            assert e["edge_kind"] == "sequential"
            assert e.get("bridge_noisy") is True


def test_step_local_map_and_local_maps(store):
    atoms = _chain_store(store, 6)
    settings = _enabled_settings(traverse_max_expand_per_step=3)
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    start = reg.start(
        gv,
        goal="multi",
        seed_atom_ids=[a.atom_id for a in atoms[:3]],
        seed_mode="explicit_only",
        moment_id="m1",
    )
    assert start["local_map"] is not None
    sid = start["session_id"]
    # Expand two seeds → local_map for first + local_maps length 2.
    step = reg.step(
        gv,
        session_id=sid,
        expand_ids=[atoms[0].atom_id, atoms[1].atom_id],
    )
    assert step["ok"] is True
    assert step["local_map"] is not None
    assert step["local_map"]["focus"]["atom_id"] == atoms[0].atom_id
    maps = step["local_maps"]
    assert maps is not None
    assert 1 < len(maps) <= LOCAL_MAPS_STEP_CAP
    assert maps[0]["focus_id"] == atoms[0].atom_id
    assert maps[1]["focus_id"] == atoms[1].atom_id
    assert maps[0]["map"]["focus"]["atom_id"] == atoms[0].atom_id


def test_step_single_expand_local_maps_null(store):
    atoms = _chain_store(store, 4)
    settings = _enabled_settings()
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    start = reg.start(
        gv,
        goal="single",
        seed_atom_ids=[atoms[1].atom_id],
        seed_mode="explicit_only",
        moment_id="m1",
    )
    step = reg.step(
        gv,
        session_id=start["session_id"],
        expand_ids=[atoms[1].atom_id],
    )
    assert step["local_map"] is not None
    assert step["local_maps"] is None  # single expand → null, not 1-length array


def test_local_map_disabled_frontier_only(store):
    atoms = _chain_store(store, 3)
    settings = _enabled_settings(traverse_local_map_enabled=False)
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    start = reg.start(
        gv,
        goal="off",
        seed_atom_ids=[atoms[0].atom_id],
        seed_mode="explicit_only",
        moment_id="m1",
    )
    assert start["ok"] is True
    assert start["local_map"] is None
    assert start["frontier"]  # frontier still present
    step = reg.step(
        gv,
        session_id=start["session_id"],
        expand_ids=[atoms[0].atom_id],
    )
    assert step["local_map"] is None
    assert step["local_maps"] is None


def test_build_local_map_caps_edges_and_ring(store):
    # Long sequential chain to pressure edge/ring caps.
    n = 40
    atoms = [
        _atom(
            atom_id=f"cap_{i}",
            t=f"2026-07-28T11:{i:02d}:00Z",
            kind="observation" if i % 2 == 0 else "speak",
            text=f"cap body {i}",
            moment_id="m_cap",
        )
        for i in range(n)
    ]
    _link_chain(store, atoms)
    settings = _enabled_settings(traverse_neighbor_k=32)
    gv = GraphView(store, settings=settings, now="2026-07-28T12:00:00Z")
    # Focus middle — sequential only yields 2 edges, so also need same_moment peers.
    # same_moment projects up to traverse_same_moment_k — still small.
    # Unit-level: call build_local_map and assert caps are respected even if
    # under-full (hard invariant).
    lm = build_local_map(
        gv,
        atoms[n // 2].atom_id,
        include_noisy=False,
        expand_deadline_ms=200,
        neighbor_k=32,
    )
    assert lm is not None
    assert len(lm["edges"]) <= LOCAL_MAP_EDGES_CAP
    assert len(lm["ring"]) <= LOCAL_MAP_RING_CAP
    assert lm["focus"]["atom_id"] == atoms[n // 2].atom_id
    # Prefer primary kinds on ring.
    for node in lm["ring"]:
        assert node.get("kind") in (
            "speak",
            "observation",
            "summary",
            "parcel",
            None,
        ) or node.get("kind") not in ("tool", "ledger", "model")


def test_local_map_prefer_primary_kinds_on_ring(store):
    atoms = _mixed_chain(store)
    settings = _enabled_settings()
    gv = GraphView(store, settings=settings, now="2026-07-28T10:10:00Z")
    lm = build_local_map(gv, "lm_obs", include_noisy=True, expand_deadline_ms=80)
    assert lm is not None
    # With include_noisy, ring still ranks speak/observation ahead of tool when both present.
    ring = lm["ring"]
    if len(ring) >= 2:
        # First ring entries should prefer primary when mixed weights similar.
        primary_idxs = [
            i
            for i, n in enumerate(ring)
            if n.get("kind") in ("speak", "observation", "summary")
        ]
        noisy_idxs = [
            i for i, n in enumerate(ring) if n.get("kind") in ("tool", "ledger", "model")
        ]
        if primary_idxs and noisy_idxs:
            assert min(primary_idxs) < min(noisy_idxs)


def test_start_empty_seeds_local_map_null(store):
    settings = _enabled_settings()
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    start = reg.start(
        gv,
        goal="empty",
        seed_mode="explicit_only",
        seed_atom_ids=["missing_id"],
        moment_id="m1",
    )
    assert start["ok"] is True
    assert start["local_map"] is None
    assert start["local_maps"] is None


def test_build_local_map_deadline_zero_truncated_not_unlimited(store):
    """Issue 1: expand_deadline_ms=0 must not mean GraphView unlimited.

    Focus-only map with map_truncated=true and empty edges/ring — never a full
    d1+d2 expand with structural_ms_budget=0 and map_truncated=false.
    """
    atoms = _chain_store(store, 5)
    settings = _enabled_settings()
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    focus = atoms[2].atom_id

    full = build_local_map(
        gv, focus, include_noisy=False, expand_deadline_ms=80, neighbor_k=16
    )
    assert full is not None
    assert len(full["edges"]) >= 1  # sequential neighbors exist

    zero = build_local_map(
        gv, focus, include_noisy=False, expand_deadline_ms=0, neighbor_k=16
    )
    assert zero is not None
    assert zero["focus"]["atom_id"] == focus
    assert zero["edges"] == []
    assert zero["ring"] == []
    assert zero["meta"]["map_truncated"] is True
    assert zero["meta"].get("budget_exhausted") is True
    assert zero["meta"].get("structural_ms_budget") == 0
    # Free compass prev/next from atom fields is OK (no expand).
    seq = zero["compass"]["sequential"]
    assert "prev" in seq or "next" in seq


def test_start_passes_remaining_zero_when_seed_spent(store, monkeypatch):
    """When start structural spent >= start_ms, remaining_struct=0 is passed.

    Integration: explicit seed + monkeypatch expand_ms_spent accounting via
    ``_now_ms`` around a dummy seed_from_query spend is brittle; instead
    force spent by patching the remaining computation inputs — after start
    builds seeds, replace expand_ms_spent effect by intercepting build and
    verifying start would pass max(0, start_ms - spent).

    Hermetic path: monkeypatch ``build_local_map`` and force
    ``expand_ms_spent`` high by making start's seed timing report large
    elapsed when semantic path runs with explicit seeds co-present under auto.
    Simpler: patch remaining at call site by wrapping start's use of
    expand_ms_spent — set start_ms=100 and expand_ms_spent via attribute
    on a custom seed that advances monotonic clock.
    """
    atoms = _chain_store(store, 3)
    settings = _enabled_settings(traverse_start_expand_max_ms=100)
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")

    captured: list[Any] = []
    real_build = build_local_map

    def _capture(graph, focus_id, **kwargs):
        captured.append(kwargs.get("expand_deadline_ms"))
        return real_build(graph, focus_id, **kwargs)

    monkeypatch.setattr("elyra.memory.traverse.build_local_map", _capture)

    # Alternating clock: seed block does t0=_now_ms(); ...; spent=now-t0.
    # Odd calls → 0, even → 10_000 so spent ≈ 10_000 ≥ start_ms (100).
    clock = {"n": 0}

    def _fake_now():
        clock["n"] += 1
        return 0.0 if clock["n"] % 2 == 1 else 10_000.0

    monkeypatch.setattr("elyra.memory.traverse._now_ms", _fake_now)

    start = reg.start(
        gv,
        goal="topic garden",
        seed_atom_ids=[atoms[1].atom_id],
        seed_mode="auto",
        moment_id="m1",
    )
    assert start["ok"] is True
    assert start.get("seed_ids")  # at least explicit seed
    assert captured, "build_local_map must be called for primary seed"
    # remaining_struct = max(0, start_ms - expand_ms_spent) → 0 when spent large
    assert captured[0] == 0
    lm = start["local_map"]
    assert lm is not None
    assert lm["meta"]["map_truncated"] is True
    assert lm["edges"] == []
    assert lm["meta"].get("structural_ms_budget") == 0


def test_local_maps_capped_at_three(store):
    """local_maps length ≤ 3 even when more expand_ids succeed."""
    atoms = _chain_store(store, 8)
    settings = _enabled_settings(traverse_max_expand_per_step=8)
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    seed_ids = [a.atom_id for a in atoms[:5]]
    start = reg.start(
        gv,
        goal="cap maps",
        seed_atom_ids=seed_ids,
        seed_mode="explicit_only",
        moment_id="m1",
    )
    step = reg.step(
        gv,
        session_id=start["session_id"],
        expand_ids=seed_ids,  # 5 expands
    )
    assert step["ok"] is True
    maps = step["local_maps"]
    assert maps is not None
    assert len(maps) <= LOCAL_MAPS_STEP_CAP
    assert len(maps) == LOCAL_MAPS_STEP_CAP  # first 3 of 5


def test_local_map_non_seq_noisy_created_with_omitted(store, paths):
    """Non-sequential durable edge to tool is omitted under default filter."""
    from elyra.memory.edges import DurableEdge, new_edge_id, open_edge_store
    from elyra.memory.weights import EDGE_CREATED_WITH

    speak = store.put_atom(
        _atom(
            atom_id="ns_speak",
            kind="speak",
            text="hello",
            moment_id="m_ns",
            t="2026-07-28T10:00:00Z",
        )
    )
    tool = store.put_atom(
        _atom(
            atom_id="ns_tool",
            kind="tool",
            text='{"raw":true}',
            moment_id="m_ns",
            t="2026-07-28T10:01:00Z",
            meta={"tool_name": "run_cmd"},
        )
    )
    # No sequential link — only created_with speak → tool.
    edge_store = open_edge_store(
        paths, MemorySettings(write_atoms=True, backend="jsonl"), fail_soft=False
    )
    try:
        edge_store.put_edge(
            DurableEdge(
                edge_id=new_edge_id(),
                src_atom_id=speak.atom_id,
                dst_atom_id=tool.atom_id,
                edge_kind=EDGE_CREATED_WITH,
                created_at="2026-07-28T10:00:00Z",
                updated_at="2026-07-28T10:00:00Z",
                reason="test",
            )
        )
        settings = _enabled_settings(durable_edges_enabled=True)
        gv = GraphView(
            store,
            settings=settings,
            edge_store=edge_store,
            now="2026-07-28T10:05:00Z",
        )
        # Sanity: neighbors includes created_with to tool.
        raw = gv.neighbors(speak.atom_id, k=16, allow_semantic=False)
        assert any(
            e.dst_atom_id == tool.atom_id and e.edge_kind == EDGE_CREATED_WITH
            for e in raw
        )
        lm = build_local_map(
            gv, speak.atom_id, include_noisy=False, expand_deadline_ms=80
        )
        assert lm is not None
        # No edge to tool (non-seq noisy omitted).
        assert not any(e["dst"] == tool.atom_id for e in lm["edges"])
        assert tool.atom_id not in {n["atom_id"] for n in lm["ring"]}
        assert "tool" in set(lm["filters"]["noisy_kinds_omitted"])
    finally:
        edge_store.close()


def test_build_local_map_caps_force_truncation(store):
    """Synthesize >16 edges via prefetched rows → map_truncated + cap lengths."""
    from elyra.memory.graph import GraphEdge

    atoms = _chain_store(store, 3)
    focus = atoms[1]
    settings = _enabled_settings()
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")

    # Fabricate 20 observation neighbors as prefetched edges.
    fake_atoms = []
    for i in range(20):
        a = store.put_atom(
            _atom(
                atom_id=f"fake_peer_{i}",
                kind="observation",
                text=f"peer {i}",
                moment_id="m1",
                t=f"2026-07-28T12:{i:02d}:00Z",
            )
        )
        fake_atoms.append(a)
    prefetched = [
        GraphEdge(
            src_atom_id=focus.atom_id,
            dst_atom_id=a.atom_id,
            edge_kind="same_moment",
            weight=1.0 - (i * 0.01),
            reason="test",
        )
        for i, a in enumerate(fake_atoms)
    ]
    lm = build_local_map(
        gv,
        focus.atom_id,
        include_noisy=False,
        expand_deadline_ms=200,
        prefetched_edges=prefetched,
    )
    assert lm is not None
    assert len(lm["edges"]) == LOCAL_MAP_EDGES_CAP
    assert len(lm["ring"]) == LOCAL_MAP_RING_CAP
    assert lm["meta"]["map_truncated"] is True


def test_step_map_reuses_prefetched_under_shared_budget(store, monkeypatch):
    """Issue 2: step maps share remaining budget; d1 from Phase-A edges."""
    atoms = _chain_store(store, 5)
    settings = _enabled_settings(
        traverse_max_expand_per_step=3,
        traverse_expand_max_ms=50,
    )
    reg = _reg(settings)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:05:00Z")
    start = reg.start(
        gv,
        goal="shared",
        seed_atom_ids=[a.atom_id for a in atoms[:3]],
        seed_mode="explicit_only",
        moment_id="m1",
    )
    deadlines: list[Any] = []
    pref_flags: list[bool] = []
    real_build = build_local_map

    def _spy(graph, focus_id, **kwargs):
        deadlines.append(kwargs.get("expand_deadline_ms"))
        pref_flags.append(kwargs.get("prefetched_edges") is not None)
        return real_build(graph, focus_id, **kwargs)

    monkeypatch.setattr("elyra.memory.traverse.build_local_map", _spy)
    step = reg.step(
        gv,
        session_id=start["session_id"],
        expand_ids=[atoms[0].atom_id, atoms[1].atom_id],
    )
    assert step["ok"] is True
    assert step["local_map"] is not None
    assert deadlines  # maps built
    assert all(pref_flags)  # all maps got prefetched Phase-A edges
    # Deadlines are remaining after expand (0..) not a fresh full expand_ms each.
    for d in deadlines:
        assert d is None or d <= settings.traverse_expand_max_ms
