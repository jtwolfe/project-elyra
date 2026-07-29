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


def test_moment_close_clears_sticky(store):
    atoms = _chain_store(store, 2)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg.finish(keep_ids=[atoms[0].atom_id])
    assert reg.last_session is not None
    assert reg.last_confirmed_keep is not None
    reg.on_moment_close("m1")
    assert reg.active_session is None
    assert reg.last_session is None
    assert reg.last_confirmed_keep is None


def test_clear_confirmed_keep_optional_glass(store):
    atoms = _chain_store(store, 2)
    reg = _reg(_enabled_settings(traverse_keep_adjacent=False))
    gv = GraphView(store, settings=reg.settings, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    reg.finish(keep_ids=[atoms[0].atom_id])
    reg.clear_confirmed_keep(clear_glass=False)
    assert reg.last_confirmed_keep is None
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
    assert w.traversal.last_session is None
    assert w.traversal.last_confirmed_keep is None
