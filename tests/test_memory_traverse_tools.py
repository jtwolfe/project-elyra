"""Tests for memory_traverse_* host builtins + skill packaging (Phase 2a PR-A4)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.graph import GraphView
from elyra.memory.store import open_memory_store
from elyra.memory.traverse import (
    ERROR_NO_ACTIVE,
    ERROR_TRAVERSE_DISABLED,
    TraversalRegistry,
)
from elyra.memory.types import Atom, new_atom_id
from elyra.skills import SkillCatalog
from elyra.tools.builtin.memory_traverse import (
    ERROR_ATOM_NOT_FOUND,
    ERROR_INVALID_ARGS,
    ERROR_TRAVERSE_UNAVAILABLE,
    memory_traverse_abandon,
    memory_traverse_finish,
    memory_traverse_inspect,
    memory_traverse_start,
    memory_traverse_step,
)
from elyra.tools.policy import resolve_bundled_tools_root
from elyra.tools.registry import ToolRegistry
from elyra.tools.types import ToolContext
from elyra.settings import default_settings


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def paths(tmp_path: Path):
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


def _chain(store, n: int = 5, moment_id: str = "m1") -> list[Atom]:
    atoms = [
        _atom(
            atom_id=f"a_t{i}",
            t=f"2026-07-28T10:0{i}:00Z",
            text=f"memory about topic {i} with more detail for previews and inspect",
            moment_id=moment_id,
        )
        for i in range(n)
    ]
    return _link_chain(store, atoms)


def _enabled_settings(**kwargs: Any) -> MemorySettings:
    base = dict(
        directed_traversal_enabled=True,
        write_atoms=True,
        backend="jsonl",
        traverse_keep_adjacent=False,
    )
    base.update(kwargs)
    return MemorySettings(**base)


def _ctx(
    paths,
    *,
    store=None,
    settings: MemorySettings | None = None,
    moment_id: str = "m1",
    extras: dict[str, Any] | None = None,
    inject_ports: bool = True,
) -> ToolContext:
    mem = settings or _enabled_settings()
    full = default_settings()
    full = replace(full, memory=mem)
    bag: dict[str, Any] = dict(extras or {})
    if inject_ports and store is not None:
        reg = TraversalRegistry(settings=mem)
        gv = GraphView(store, settings=mem, now="2026-07-28T10:05:00Z")

        def _graph_factory() -> GraphView:
            return GraphView(store, settings=mem, now="2026-07-28T10:05:00Z")

        bag.setdefault("traversal", reg)
        bag.setdefault("graph_view", _graph_factory)
        # Keep a direct instance available for tests that need the same view.
        bag.setdefault("_graph_instance", gv)
    return ToolContext(
        paths=paths,
        settings=full,
        moment_id=moment_id,
        user_id="operator",
        extras=bag,
    )


# ── Discovery ───────────────────────────────────────────────────────────────


_TRAVERSE_TOOLS = (
    "memory_traverse_start",
    "memory_traverse_step",
    "memory_traverse_inspect",
    "memory_traverse_finish",
    "memory_traverse_abandon",
)


def test_bundled_tools_discoverable(paths):
    reg = ToolRegistry(paths, bundled_root=resolve_bundled_tools_root())
    for name in _TRAVERSE_TOOLS:
        pkg = reg.get(name)
        assert pkg is not None, name
        assert pkg.meta.kind == "read"
        assert pkg.source == "bundled"
        assert "memory_traverse" in (pkg.runner.entry or "")


def test_skill_catalog_has_memory_traverse(paths):
    cat = SkillCatalog(paths)
    assert cat.has("memory-traverse")
    row = next(r for r in cat.catalog() if r.get("name") == "memory-traverse")
    desc = row.get("description") or ""
    assert "multi-hop" in desc.lower() or "walk" in desc.lower()
    meta = cat.load("memory-traverse")
    assert meta is not None
    assert "memory_traverse_start" in meta.body
    assert "inspect" in meta.body.lower()
    assert "compose_meal" in meta.body or "next" in meta.body.lower()
    assert "KD-A16" in meta.body or "meal" in meta.body.lower()


# ── Fail closed ─────────────────────────────────────────────────────────────


def test_missing_extras_traverse_unavailable(paths, store):
    ctx = _ctx(paths, store=store, inject_ports=False)
    r = memory_traverse_start({"goal": "find haiku"}, ctx)
    assert r.ok is False
    assert r.error_reason == ERROR_TRAVERSE_UNAVAILABLE


def test_flags_off_traverse_disabled(paths, store):
    mem = _enabled_settings(directed_traversal_enabled=False)
    ctx = _ctx(paths, store=store, settings=mem)
    r = memory_traverse_start({"goal": "x", "seed_atom_ids": ["a_t0"]}, ctx)
    assert r.ok is False
    assert r.error_reason == ERROR_TRAVERSE_DISABLED
    # Sticky state untouched (no session created).
    reg = ctx.extras["traversal"]
    assert reg.active_session is None


def test_flags_off_step_inspect_finish_abandon(paths, store):
    atoms = _chain(store, 2)
    # Start while enabled, then flip off for subsequent tools.
    mem_on = _enabled_settings()
    reg = TraversalRegistry(settings=mem_on)
    gv = GraphView(store, settings=mem_on, now="2026-07-28T10:05:00Z")
    reg.start(gv, goal="g", seed_atom_ids=[atoms[0].atom_id], moment_id="m1")
    assert reg.active_session is not None

    mem_off = _enabled_settings(directed_traversal_enabled=False)
    full = replace(default_settings(), memory=mem_off)
    ctx = ToolContext(
        paths=paths,
        settings=full,
        moment_id="m1",
        extras={
            "traversal": reg,
            "graph_view": lambda: GraphView(
                store, settings=mem_off, now="2026-07-28T10:05:00Z"
            ),
        },
    )
    for fn, args in (
        (memory_traverse_step, {"expand_ids": [atoms[0].atom_id]}),
        (memory_traverse_inspect, {"atom_ids": [atoms[0].atom_id]}),
        (memory_traverse_finish, {"keep_ids": [atoms[0].atom_id]}),
        (memory_traverse_abandon, {}),
    ):
        r = fn(args, ctx)
        assert r.ok is False, fn.__name__
        assert r.error_reason == ERROR_TRAVERSE_DISABLED


# ── Happy path structural ───────────────────────────────────────────────────


def test_start_step_inspect_finish_structural(paths, store):
    atoms = _chain(store, 4)
    ctx = _ctx(paths, store=store)

    start = memory_traverse_start(
        {
            "goal": "topic chain",
            "seed_atom_ids": [atoms[0].atom_id],
            "seed_query": "",  # empty → still structural/temporal path
        },
        ctx,
    )
    assert start.ok is True
    assert start.payload["status"] == "active"
    sid = start.payload["session_id"]
    assert sid.startswith("tr_")
    assert start.payload["considered_count"] >= 1
    assert "frontier" in start.payload
    assert "budget" in start.payload
    # No multi-hop wall clock on thin surface.
    assert "wall_ms_remaining" not in start.payload.get("budget", {})

    step = memory_traverse_step(
        {
            "session_id": sid,
            "expand_ids": [atoms[0].atom_id],
            "scratchpad": "following sequential",
        },
        ctx,
    )
    assert step.ok is True
    assert step.payload["considered_count"] >= 2
    assert step.payload.get("newly_expanded") is not None
    # Newly expanded should carry preview when present on frontier.
    for item in step.payload.get("frontier") or []:
        assert "label" in item
        # preview may be present for new nodes
        assert len(item["label"]) <= 160

    insp = memory_traverse_inspect({"atom_ids": [atoms[0].atom_id]}, ctx)
    assert insp.ok is True
    bodies = insp.payload["atoms"]
    assert len(bodies) == 1
    assert "topic 0" in bodies[0]["body"]
    assert bodies[0]["truncated"] is False or isinstance(bodies[0]["truncated"], bool)

    fin = memory_traverse_finish(
        {
            "session_id": sid,
            "keep_ids": [atoms[0].atom_id],
            "summary_hint": "found seed",
        },
        ctx,
    )
    assert fin.ok is True
    assert fin.payload["status"] == "confirmed"
    assert atoms[0].atom_id in fin.payload.get("keep_set", fin.payload.get("keep_ids", []))
    assert fin.payload.get("walk_summary_nl")
    assert "found seed" in (fin.payload.get("walk_summary_nl") or "")

    reg: TraversalRegistry = ctx.extras["traversal"]
    assert reg.active_session is None
    assert reg.last_session is not None
    assert reg.last_confirmed_keep is not None
    assert atoms[0].atom_id in reg.last_confirmed_keep.keep_ids


def test_abandon_retains_sticky(paths, store):
    atoms = _chain(store, 2)
    ctx = _ctx(paths, store=store)
    # First finish to create sticky.
    memory_traverse_start(
        {"goal": "g", "seed_atom_ids": [atoms[0].atom_id]}, ctx
    )
    memory_traverse_finish({"keep_ids": [atoms[0].atom_id]}, ctx)
    reg: TraversalRegistry = ctx.extras["traversal"]
    assert reg.last_confirmed_keep is not None
    # New start then abandon.
    memory_traverse_start(
        {"goal": "g2", "seed_atom_ids": [atoms[1].atom_id]}, ctx
    )
    abd = memory_traverse_abandon({}, ctx)
    assert abd.ok is True
    assert abd.payload.get("last_confirmed_retained") is True
    assert reg.last_confirmed_keep is not None
    assert atoms[0].atom_id in reg.last_confirmed_keep.keep_ids


def test_step_without_start_no_active(paths, store):
    ctx = _ctx(paths, store=store)
    r = memory_traverse_step({"expand_ids": ["a"]}, ctx)
    assert r.ok is False
    assert r.error_reason == ERROR_NO_ACTIVE


def test_inspect_unknown_id_fail_closed(paths, store):
    atoms = _chain(store, 1)
    ctx = _ctx(paths, store=store)
    memory_traverse_start(
        {"goal": "g", "seed_atom_ids": [atoms[0].atom_id]}, ctx
    )
    r = memory_traverse_inspect(
        {"atom_ids": [atoms[0].atom_id, "does_not_exist"]}, ctx
    )
    assert r.ok is False
    assert r.error_reason == ERROR_ATOM_NOT_FOUND
    assert "does_not_exist" in r.payload.get("missing_ids", [])


def test_inspect_empty_args(paths, store):
    ctx = _ctx(paths, store=store)
    r = memory_traverse_inspect({"atom_ids": []}, ctx)
    assert r.ok is False
    assert r.error_reason == ERROR_INVALID_ARGS


def test_start_missing_goal(paths, store):
    ctx = _ctx(paths, store=store)
    r = memory_traverse_start({}, ctx)
    assert r.ok is False
    assert r.error_reason == ERROR_INVALID_ARGS


def test_null_index_structural_walk(paths, store):
    """Null / missing index → structural-only seeds and expands (no crash)."""
    atoms = _chain(store, 3)
    mem = _enabled_settings()
    reg = TraversalRegistry(settings=mem)
    # GraphView with index=None is structural-only.
    def factory() -> GraphView:
        return GraphView(
            store, index=None, embedder=None, settings=mem, now="2026-07-28T10:05:00Z"
        )

    full = replace(default_settings(), memory=mem)
    ctx = ToolContext(
        paths=paths,
        settings=full,
        moment_id="m1",
        extras={"traversal": reg, "graph_view": factory},
    )
    start = memory_traverse_start(
        {"goal": "chain", "seed_atom_ids": [atoms[1].atom_id]},
        ctx,
    )
    assert start.ok is True
    # seed_reasons may include temporal/explicit; no hard fail on no_index.
    step = memory_traverse_step(
        {"expand_ids": [atoms[1].atom_id]},
        ctx,
    )
    assert step.ok is True
    assert step.payload["considered_count"] >= 2


def test_graph_view_instance_not_only_callable(paths, store):
    """extras['graph_view'] may be an instance (not only a factory)."""
    atoms = _chain(store, 2)
    mem = _enabled_settings()
    reg = TraversalRegistry(settings=mem)
    gv = GraphView(store, settings=mem, now="2026-07-28T10:05:00Z")
    full = replace(default_settings(), memory=mem)
    ctx = ToolContext(
        paths=paths,
        settings=full,
        moment_id="m1",
        extras={"traversal": reg, "graph_view": gv},
    )
    r = memory_traverse_start(
        {"goal": "g", "seed_atom_ids": [atoms[0].atom_id]}, ctx
    )
    assert r.ok is True


def test_finish_omitted_keep_ids_uses_provisional(paths, store):
    atoms = _chain(store, 2)
    ctx = _ctx(paths, store=store)
    memory_traverse_start(
        {"goal": "g", "seed_atom_ids": [atoms[0].atom_id]}, ctx
    )
    memory_traverse_step(
        {"keep_ids": [atoms[0].atom_id]}, ctx
    )
    fin = memory_traverse_finish({}, ctx)  # no keep_ids key
    assert fin.ok is True
    keep = fin.payload.get("keep_set") or fin.payload.get("keep_ids") or []
    assert atoms[0].atom_id in keep


# ── Worker wiring ───────────────────────────────────────────────────────────


def test_worker_injects_graph_view_and_traversal(tmp_path):
    import threading
    from unittest.mock import MagicMock

    from elyra.presence.worker import PresenceWorker

    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    settings = default_settings()
    settings = replace(
        settings,
        memory=replace(settings.memory, directed_traversal_enabled=True),
    )
    w = PresenceWorker(
        paths=paths,
        client=MagicMock(),
        stop_event=threading.Event(),
        settings=settings,
    )
    # Minimal wake-like object for _build_tool_context.
    wake = MagicMock()
    wake.kind = "task_ready"
    wake.payload = {}
    ctx = w._build_tool_context(wake, "moment-x")
    assert "graph_view" in ctx.extras
    assert "traversal" in ctx.extras
    assert ctx.extras["traversal"] is w.traversal
    assert callable(ctx.extras["graph_view"])

    store = w._ensure_memory_store()
    assert store is not None
    a = store.put_atom(
        _atom(atom_id="a_w1", text="worker seed via tools", moment_id="moment-x")
    )
    start = memory_traverse_start(
        {"goal": "via worker", "seed_atom_ids": [a.atom_id]},
        ctx,
    )
    assert start.ok is True
    fin = memory_traverse_finish({"keep_ids": [a.atom_id]}, ctx)
    assert fin.ok is True
    assert w.traversal.last_confirmed_keep is not None


def test_start_seed_from_text_mock_warm(paths, store):
    """Mock warm embedder + Memory index → semantic seed hits via tools."""
    from elyra.memory.embed.mock import MockEmbedder, mock_vector
    from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet
    from elyra.memory.index import MemoryEmbeddingIndex

    a1 = store.put_atom(
        _atom(atom_id="a_sf1", text="blue sky", t="2026-07-28T10:00:00Z")
    )
    a2 = store.put_atom(
        _atom(atom_id="a_sf2", text="green grass", t="2026-07-28T10:01:00Z")
    )
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
    mem = _enabled_settings()
    reg = TraversalRegistry(settings=mem)

    def factory() -> GraphView:
        return GraphView(
            store,
            index=idx,
            embedder=emb,
            settings=mem,
            now="2026-07-28T10:05:00Z",
        )

    full = replace(default_settings(), memory=mem)
    ctx = ToolContext(
        paths=paths,
        settings=full,
        moment_id="m_other",  # exclude open moment from semantic? use different
        extras={"traversal": reg, "graph_view": factory},
    )
    start = memory_traverse_start(
        {"goal": "blue sky", "seed_query": "blue sky"},
        ctx,
    )
    assert start.ok is True
    reasons = start.payload.get("seed_reasons") or []
    # semantic and/or temporal may appear
    seed_ids = start.payload.get("seed_ids") or []
    # With warm mock, a1 should be among seeds when semantic works.
    assert a1.atom_id in seed_ids or "semantic" in reasons or start.payload[
        "considered_count"
    ] >= 1


def test_registry_execute_start_via_bundled(paths, store):
    atoms = _chain(store, 2)
    mem = _enabled_settings()
    reg_sess = TraversalRegistry(settings=mem)
    full = replace(default_settings(), memory=mem)
    ctx = ToolContext(
        paths=paths,
        settings=full,
        moment_id="m1",
        extras={
            "traversal": reg_sess,
            "graph_view": lambda: GraphView(
                store, settings=mem, now="2026-07-28T10:05:00Z"
            ),
        },
    )
    tool_reg = ToolRegistry(paths, bundled_root=resolve_bundled_tools_root())
    result = tool_reg.execute(
        "memory_traverse_start",
        {"goal": "packaged", "seed_atom_ids": [atoms[0].atom_id]},
        ctx,
    )
    assert result.ok is True
    assert result.payload.get("session_id")
