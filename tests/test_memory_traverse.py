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
from elyra.memory.traverse import (
    ERROR_NO_ACTIVE,
    ERROR_TRAVERSE_DISABLED,
    TraversalRegistry,
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
    assert "Kept 1" in nl
    assert "nice finds" in nl


# ── moment close + clear ────────────────────────────────────────────────────


def test_moment_close_clears_last_session_retains_meal_tray(store):
    """S3 / B5: moment close clears last_session (KD-A19); retains meal tray."""
    atoms = _chain_store(store, 2)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg.finish(keep_ids=[atoms[0].atom_id])
    assert reg.last_session is not None
    assert reg.last_confirmed_keep is not None
    ids_before, _ = reg.get_meal_keep_ids()
    assert atoms[0].atom_id in ids_before
    reg.on_moment_close("m1")
    assert reg.active_session is None
    assert reg.last_session is None  # KD-A19 glass walk view cleared
    # Meal keep retained (tray + thin snap for compat).
    assert reg.last_confirmed_keep is not None
    ids_after, summary = reg.get_meal_keep_ids()
    assert atoms[0].atom_id in ids_after
    assert summary is not None


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
    w._close_traversal_for_moment("mw")
    assert w.traversal.last_session is None  # KD-A19
    # Meal tray retained (B5); worker meal path delegates to registry tray.
    assert w.traversal.last_confirmed_keep is not None
    meal_ids, meal_summary = w._last_confirmed_keep_for_meal("other_moment")
    assert a.atom_id in meal_ids
    assert meal_summary
