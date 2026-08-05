"""PR3: promote created_with + in_moment + retarget (hermetic).

Coverage:
- empty context_atom_ids → zero created_with (OQ-E1)
- tool/ledger not created_with destinations (OQ-E2); still get in_moment
- speak/observation get created_with from PromoteContext raw ids
- durable_edges_enabled off → no edges
- edge store unavailable → soft-fail (atom still promoted)
- retarget: FIFO drop → 1h tip; missing tip fail-soft; vertical fabric meta
- no glass snapshot dependency (raw list only)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.edges import (
    DurableEdge,
    UnavailableEdgeStore,
    find_1h_tip_for_target,
    list_coarser_tips_for_1h,
    new_edge_id,
    open_edge_store,
    put_edge_with_budget,
    retarget_created_with_edge,
)
from elyra.memory.graph import moment_hub_id
from elyra.memory.promote import (
    PromoteContext,
    atom_ids_from_meal_items,
    promote_beat,
    promote_wake_observation,
)
from elyra.memory.store import open_memory_store
from elyra.memory.types import (
    Atom,
    new_atom_id,
    stable_summary_id,
    to_iso_z,
    versioned_summary_id,
    window_bounds,
)
from elyra.memory.weights import EDGE_CREATED_WITH, EDGE_IN_MOMENT


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def settings() -> MemorySettings:
    return MemorySettings(
        write_atoms=True,
        backend="jsonl",
        durable_edges_enabled=True,
        edge_created_with_max=100,
        edge_created_with_write_cap=32,
        edge_retarget_enabled=True,
        edge_retarget_ensure_vertical=True,
    )


@pytest.fixture
def store(paths, settings):
    s = open_memory_store(paths, settings)
    yield s
    s.close()


@pytest.fixture
def edge_store(paths, settings):
    es = open_edge_store(paths, settings)
    yield es
    es.close()


def _put_ctx(
    store,
    *,
    kind: str = "observation",
    text: str = "ctx",
    t: str = "2026-08-05T10:00:00Z",
    moment_id: str = "m_ctx",
    atom_id: str | None = None,
) -> Atom:
    a = Atom(
        atom_id=atom_id or new_atom_id(),
        t_start=t,
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=moment_id,
    )
    return store.put_atom(a)


def _pctx(edge_store, ids: list[str] | None = None) -> PromoteContext:
    return PromoteContext(
        context_atom_ids=list(ids or ()),
        edge_store=edge_store,
    )


# ── atom_ids_from_meal_items ───────────────────────────────────────────────


def test_atom_ids_from_meal_items_raw_uncapped():
    """Multi-atom meta.atom_ids extracted fully (not glass cap 24)."""

    class _Item:
        def __init__(self, atom_id=None, meta=None):
            self.atom_id = atom_id
            self.meta = meta or {}

    ids = [f"a_{i:04d}" for i in range(40)]
    items = [
        _Item(atom_id="a_single"),
        _Item(atom_id=None, meta={"atom_ids": ids}),
    ]
    out = atom_ids_from_meal_items(items)
    assert out[0] == "a_single"
    assert len(out) == 41  # single + 40 multi
    assert out[-1] == "a_0039"


# ── empty context → zero created_with ──────────────────────────────────────


def test_empty_context_zero_created_with(store, edge_store, settings):
    """OQ-E1: empty context_atom_ids → no created_with; still in_moment."""
    atom = promote_wake_observation(
        store,
        "m1",
        content="hello world",
        message_id="msg1",
        settings=settings,
        promote_context=_pctx(edge_store, []),
    )
    assert atom is not None
    cw = edge_store.list_edges_from(atom.atom_id, kinds=[EDGE_CREATED_WITH])
    assert cw == []
    im = edge_store.list_edges_from(atom.atom_id, kinds=[EDGE_IN_MOMENT])
    assert len(im) == 1
    assert im[0].dst_atom_id == moment_hub_id("m1")


def test_missing_promote_context_no_edges(store, edge_store, settings):
    """No PromoteContext → no edge writes (even when flag on)."""
    atom = promote_wake_observation(
        store,
        "m1",
        content="hello",
        settings=settings,
        promote_context=None,
    )
    assert atom is not None
    assert edge_store.list_edges_from(atom.atom_id) == []


def test_flag_off_no_edges(paths, store, edge_store):
    off = MemorySettings(
        write_atoms=True, backend="jsonl", durable_edges_enabled=False
    )
    ctx_atom = _put_ctx(store)
    atom = promote_wake_observation(
        store,
        "m1",
        content="hello",
        settings=off,
        promote_context=_pctx(edge_store, [ctx_atom.atom_id]),
    )
    assert atom is not None
    assert edge_store.list_edges_from(atom.atom_id) == []


# ── created_with from raw ids ──────────────────────────────────────────────


def test_speak_writes_created_with_and_in_moment(store, edge_store, settings):
    c1 = _put_ctx(store, text="context one", t="2026-08-05T09:00:00Z")
    c2 = _put_ctx(store, text="context two", t="2026-08-05T09:01:00Z")
    pctx = _pctx(edge_store, [c1.atom_id, c2.atom_id])
    atom = promote_beat(
        store,
        "m_speak",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": '{"text": "I remember the meal context."}',
            "ts": "2026-08-05T10:30:00Z",
        },
        settings=settings,
        promote_context=pctx,
    )
    assert atom is not None
    assert atom.kind == "speak"
    cw = edge_store.list_edges_from(atom.atom_id, kinds=[EDGE_CREATED_WITH])
    dsts = {e.dst_atom_id for e in cw}
    assert dsts == {c1.atom_id, c2.atom_id}
    im = edge_store.list_edges_from(atom.atom_id, kinds=[EDGE_IN_MOMENT])
    assert len(im) == 1
    assert im[0].dst_atom_id == moment_hub_id("m_speak")


def test_tool_ledger_no_created_with_but_in_moment(store, edge_store, settings):
    """Tool/ledger src write no created_with; still in_moment membership."""
    c1 = _put_ctx(store)
    pctx = _pctx(edge_store, [c1.atom_id])
    tool = promote_beat(
        store,
        "m_t",
        {
            "type": "tool",
            "name": "read_file",
            "ok": True,
            "content": "file contents here",
            "ts": "2026-08-05T10:00:00Z",
        },
        settings=settings,
        promote_context=pctx,
    )
    assert tool is not None
    assert tool.kind == "tool"
    assert edge_store.list_edges_from(tool.atom_id, kinds=[EDGE_CREATED_WITH]) == []
    assert len(edge_store.list_edges_from(tool.atom_id, kinds=[EDGE_IN_MOMENT])) == 1

    ledger = promote_beat(
        store,
        "m_t",
        {
            "type": "tool",
            "name": "create_goal",
            "ok": True,
            "content": json_goal(),
            "ts": "2026-08-05T10:01:00Z",
        },
        settings=settings,
        promote_context=pctx,
    )
    assert ledger is not None
    assert ledger.kind == "ledger"
    assert (
        edge_store.list_edges_from(ledger.atom_id, kinds=[EDGE_CREATED_WITH]) == []
    )
    assert len(edge_store.list_edges_from(ledger.atom_id, kinds=[EDGE_IN_MOMENT])) == 1


def json_goal() -> str:
    import json

    return json.dumps(
        {"goal": {"id": "g1", "title": "demo", "status": "open"}}
    )


def test_tool_ledger_excluded_as_created_with_dst(store, edge_store, settings):
    """OQ-E2: tool/ledger destinations filtered out of created_with."""
    obs = _put_ctx(store, kind="observation", text="ok dst")
    tool = _put_ctx(store, kind="tool", text="not a dst")
    ledger = _put_ctx(store, kind="ledger", text="also not")
    pctx = _pctx(edge_store, [obs.atom_id, tool.atom_id, ledger.atom_id])
    speak = promote_beat(
        store,
        "m2",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": '{"text": "only obs should link"}',
            "ts": "2026-08-05T11:00:00Z",
        },
        settings=settings,
        promote_context=pctx,
    )
    assert speak is not None
    cw = edge_store.list_edges_from(speak.atom_id, kinds=[EDGE_CREATED_WITH])
    dsts = {e.dst_atom_id for e in cw}
    assert dsts == {obs.atom_id}
    assert tool.atom_id not in dsts
    assert ledger.atom_id not in dsts


def test_self_excluded_from_created_with(store, edge_store, settings):
    """Promote does not create created_with to self (re-promote id not known
    ahead of time — filter uses src after put; pre-put candidates that match
    other ids only). Self exclusion is via src_atom_id != dst."""
    c1 = _put_ctx(store)
    # context list includes c1 only — speak gets edge to c1, not itself.
    speak = promote_beat(
        store,
        "m3",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": '{"text": "hi"}',
            "ts": "2026-08-05T12:00:00Z",
        },
        settings=settings,
        promote_context=_pctx(edge_store, [c1.atom_id]),
    )
    assert speak is not None
    cw = edge_store.list_edges_from(speak.atom_id, kinds=[EDGE_CREATED_WITH])
    assert all(e.dst_atom_id != speak.atom_id for e in cw)
    assert {e.dst_atom_id for e in cw} == {c1.atom_id}


def test_edge_store_unavailable_soft_fail(store, settings):
    """Unavailable edge store must not break atom promote."""
    c1 = _put_ctx(store)
    bad = UnavailableEdgeStore("edge_backend_unavailable")
    atom = promote_wake_observation(
        store,
        "m_soft",
        content="still promote me",
        settings=settings,
        promote_context=_pctx(bad, [c1.atom_id]),
    )
    assert atom is not None
    assert atom.content_text == "still promote me"
    # Atom is on the store.
    assert store.get_atom(atom.atom_id) is not None


def test_write_cap_limits_created_with(store, edge_store, paths):
    cfg = MemorySettings(
        write_atoms=True,
        backend="jsonl",
        durable_edges_enabled=True,
        edge_created_with_write_cap=3,
    )
    ids = []
    for i in range(10):
        a = _put_ctx(
            store,
            text=f"c{i}",
            t=f"2026-08-05T09:{i:02d}:00Z",
            atom_id=f"a_cap{i:02d}",
        )
        ids.append(a.atom_id)
    speak = promote_beat(
        store,
        "m_cap",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": '{"text": "capped"}',
            "ts": "2026-08-05T10:00:00Z",
        },
        settings=cfg,
        promote_context=_pctx(edge_store, ids),
    )
    assert speak is not None
    cw = edge_store.list_edges_from(speak.atom_id, kinds=[EDGE_CREATED_WITH])
    assert len(cw) == 3
    # First three candidates in order.
    assert {e.dst_atom_id for e in cw} == set(ids[:3])


# ── Retarget (OQ-E7) ──────────────────────────────────────────────────────


def _make_1h_summary(
    store,
    *,
    t: str,
    source_ids: list[str],
    body: str = "1h tip body",
) -> Atom:
    w_start, w_end = window_bounds("1h", t)
    tip_id = stable_summary_id("1h", w_start)
    tip = Atom(
        atom_id=tip_id,
        t_start=to_iso_z(w_start),
        kind="summary",
        content_text=body,
        content_ref="inline",
        scale="1h",
        window_start=to_iso_z(w_start),
        window_end=to_iso_z(w_end),
        meta={
            "version": 1,
            "from_children": False,
            "source_atom_ids": list(source_ids),
            "child_atom_ids": list(source_ids),
        },
    )
    return store.put_atom(tip)


def _make_coarser_summary(
    store,
    *,
    scale: str,
    t: str,
    child_ids: list[str],
) -> Atom:
    w_start, w_end = window_bounds(scale, t)
    tip_id = versioned_summary_id(scale, w_start, 1)
    tip = Atom(
        atom_id=tip_id,
        t_start=to_iso_z(w_start),
        kind="summary",
        content_text=f"{scale} tip",
        content_ref="inline",
        scale=scale,
        window_start=to_iso_z(w_start),
        window_end=to_iso_z(w_end),
        meta={
            "version": 1,
            "from_children": True,
            "child_atom_ids": list(child_ids),
            "source_atom_ids": [],
        },
    )
    return store.put_atom(tip)


def test_find_1h_tip_prefers_source_membership(store):
    t = "2026-08-05T10:15:00Z"
    raw = _put_ctx(store, t=t, atom_id="a_raw_target")
    tip = _make_1h_summary(store, t=t, source_ids=[raw.atom_id])
    found = find_1h_tip_for_target(store, raw)
    assert found is not None
    assert found.atom_id == tip.atom_id


def test_retarget_created_with_to_1h_tip(store, edge_store, settings):
    """When created_with FIFO drops T, retarget src → 1h tip containing T.

    Edge created_at values are in the past so retarget (utc_now_iso) stays
    newest and is not immediately re-dropped by the post-put budget pass.
    """
    t = "2026-07-01T10:30:00Z"
    target = _put_ctx(store, t=t, atom_id="a_old_ctx", text="old context")
    tip = _make_1h_summary(store, t=t, source_ids=[target.atom_id])
    # Coarser 1d tip listing the 1h tip as child (projected fabric).
    tip_1d = _make_coarser_summary(
        store, scale="1d", t=t, child_ids=[tip.atom_id]
    )

    src_id = "a_src_speak"
    # Small created_with max so one more put drops the oldest.
    cfg = MemorySettings(
        write_atoms=True,
        backend="jsonl",
        durable_edges_enabled=True,
        edge_created_with_max=2,
        edge_retarget_enabled=True,
        edge_retarget_ensure_vertical=True,
    )
    # Past timestamps (before real now) so retarget tip survives re-enforce.
    now_base = datetime(2026, 7, 2, 12, 0, 0, tzinfo=UTC)
    # Fill window with 2 edges; first targets the old context (will age out).
    e_old = DurableEdge(
        edge_id=new_edge_id(),
        src_atom_id=src_id,
        dst_atom_id=target.atom_id,
        edge_kind=EDGE_CREATED_WITH,
        created_at=to_iso_z(now_base),
        updated_at=to_iso_z(now_base),
        reason="promote_context",
        meta={},
    )
    e_mid = DurableEdge(
        edge_id=new_edge_id(),
        src_atom_id=src_id,
        dst_atom_id=_put_ctx(store, atom_id="a_mid", t="2026-07-01T11:00:00Z").atom_id,
        edge_kind=EDGE_CREATED_WITH,
        created_at=to_iso_z(now_base + timedelta(seconds=1)),
        updated_at=to_iso_z(now_base + timedelta(seconds=1)),
        reason="promote_context",
        meta={},
    )
    edge_store.put_edge(e_old)
    edge_store.put_edge(e_mid)
    assert edge_store.count_edges_for_atom(src_id, kind=EDGE_CREATED_WITH) == 2

    # Third put → drop e_old → retarget to tip; then mid may age out next.
    e_new = DurableEdge(
        edge_id=new_edge_id(),
        src_atom_id=src_id,
        dst_atom_id=_put_ctx(store, atom_id="a_new", t="2026-07-01T11:30:00Z").atom_id,
        edge_kind=EDGE_CREATED_WITH,
        created_at=to_iso_z(now_base + timedelta(seconds=2)),
        updated_at=to_iso_z(now_base + timedelta(seconds=2)),
        reason="promote_context",
        meta={},
    )
    stored, dropped = put_edge_with_budget(
        edge_store, e_new, cfg, atom_store=store, retarget=True
    )
    assert stored.edge_id == e_new.edge_id
    # Old raw target edge gone.
    remaining = edge_store.list_edges_from(src_id, kinds=[EDGE_CREATED_WITH])
    dsts = {e.dst_atom_id for e in remaining}
    assert target.atom_id not in dsts
    # Retargeted to 1h tip (survives as newest via utc_now created_at).
    assert tip.atom_id in dsts
    retarget_edges = [e for e in remaining if e.dst_atom_id == tip.atom_id]
    assert len(retarget_edges) == 1
    assert retarget_edges[0].reason == "retarget_1h_tip"
    assert retarget_edges[0].meta.get("retarget_from") == target.atom_id
    # Vertical ensure recorded coarser tip ids (projected fabric, no invent).
    vert = retarget_edges[0].meta.get("retarget_vertical") or []
    assert tip_1d.atom_id in vert
    # Budget still ≤ max.
    assert len(remaining) <= cfg.edge_created_with_max


def test_retarget_missing_1h_tip_fail_soft(store, edge_store, settings):
    """No 1h tip → drop only; no invented summary."""
    target = _put_ctx(
        store, t="2020-01-01T03:00:00Z", atom_id="a_ancient", text="old"
    )
    dropped = DurableEdge(
        edge_id=new_edge_id(),
        src_atom_id="a_src",
        dst_atom_id=target.atom_id,
        edge_kind=EDGE_CREATED_WITH,
        created_at="2026-08-05T10:00:00Z",
        reason="promote_context",
        meta={},
    )
    result = retarget_created_with_edge(
        edge_store, store, dropped, settings=settings
    )
    assert result is None
    # No new edges invented.
    assert edge_store.list_edges_from("a_src") == []
    # No summary atoms created.
    tips = store.list_summaries(
        "1h",
        overlapping=window_bounds("1h", "2020-01-01T03:00:00Z"),
        tips_only=True,
        limit=10,
    )
    assert tips == []


def test_list_coarser_tips_no_invent(store):
    t = "2026-08-05T10:00:00Z"
    tip_1h = _make_1h_summary(store, t=t, source_ids=["a_x"])
    # Only 1d present — 1w/1m/1y missing → skip, no invent.
    tip_1d = _make_coarser_summary(
        store, scale="1d", t=t, child_ids=[tip_1h.atom_id]
    )
    found = list_coarser_tips_for_1h(store, tip_1h)
    ids = [a.atom_id for a in found]
    assert tip_1d.atom_id in ids
    assert len(ids) == 1  # only 1d


def test_promote_does_not_use_glass_snapshot_fields(store, edge_store, settings):
    """created_with uses PromoteContext.context_atom_ids only — not inspect DTO.

    Simulate a glass-capped list vs raw list: raw has more ids and is what
    promote receives.
    """
    raw_ids = []
    for i in range(5):
        a = _put_ctx(store, text=f"r{i}", atom_id=f"a_raw{i}")
        raw_ids.append(a.atom_id)
    # Glass would cap at 24 and reshape — we pass full raw list explicitly.
    speak = promote_beat(
        store,
        "m_glass",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": '{"text": "raw not glass"}',
            "ts": "2026-08-05T14:00:00Z",
        },
        settings=settings,
        promote_context=_pctx(edge_store, raw_ids),
    )
    assert speak is not None
    cw = edge_store.list_edges_from(speak.atom_id, kinds=[EDGE_CREATED_WITH])
    assert {e.dst_atom_id for e in cw} == set(raw_ids)

