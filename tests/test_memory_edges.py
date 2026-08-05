"""Hermetic EdgeStore tests: JSONL always; Lance when connect works."""

from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import (
    EDGE_SCHEMA_VERSION,
    MemorySettings,
    edges_jsonl_path,
    is_durable_edges_enabled,
    memory_meta_path,
)
from elyra.memory.edges import (
    DurableEdge,
    EdgeStore,
    JsonlEdgeStore,
    UnavailableEdgeStore,
    enforce_outgoing_budgets,
    fifo_sort_key,
    kind_outgoing_cap,
    new_edge_id,
    open_edge_store,
    plan_budget_drops,
    put_edge_with_budget,
    select_fifo_overflow,
    total_outgoing_cap,
)
from elyra.memory.errors import MemoryUnavailable
from elyra.memory.weights import (
    BASE_CREATED_WITH,
    BASE_RECALLS,
    BASE_SEMANTIC_HOP,
    DEFAULT_EXPAND_KINDS,
    EDGE_CREATED_WITH,
    EDGE_HAS_CHANNEL,
    EDGE_IN_MOMENT,
    EDGE_KINDS,
    EDGE_RECALLS,
    EDGE_SEMANTIC_HOP,
    base_weight,
    edge_weight,
    semantic_factor,
)


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


def _edge(
    *,
    src: str = "a_src",
    dst: str = "a_dst",
    kind: str = EDGE_CREATED_WITH,
    created_at: str = "2026-08-05T10:00:00Z",
    edge_id: str | None = None,
    weight: float | None = None,
    reason: str = "test",
    meta: dict | None = None,
) -> DurableEdge:
    return DurableEdge(
        edge_id=edge_id or new_edge_id(),
        src_atom_id=src,
        dst_atom_id=dst,
        edge_kind=kind,
        created_at=created_at,
        updated_at=created_at,
        weight=weight,
        reason=reason,
        meta=dict(meta or {}),
        schema_version=EDGE_SCHEMA_VERSION,
    )


# ── Pure helpers ───────────────────────────────────────────────────────────


def test_durable_edges_flag_default_off():
    assert is_durable_edges_enabled(None) is False
    assert is_durable_edges_enabled(MemorySettings()) is False
    assert is_durable_edges_enabled(
        MemorySettings(durable_edges_enabled=True)
    ) is True


def test_kind_and_total_caps_from_settings():
    cfg = MemorySettings(
        edge_created_with_max=50,
        edge_recalls_max=6,
        edge_max_per_atom=120,
    )
    assert kind_outgoing_cap(EDGE_CREATED_WITH, cfg) == 50
    assert kind_outgoing_cap(EDGE_RECALLS, cfg) == 6
    assert kind_outgoing_cap(EDGE_IN_MOMENT, cfg) == 1
    assert kind_outgoing_cap(EDGE_HAS_CHANNEL, cfg) == 5
    assert kind_outgoing_cap("sequential", cfg) is None
    assert total_outgoing_cap(cfg) == 120


def test_select_fifo_overflow_tie_break_edge_id():
    """Equal created_at → edge_id ASC drops first."""
    e1 = _edge(edge_id="e_bbb", created_at="2026-08-05T10:00:00Z", dst="d1")
    e2 = _edge(edge_id="e_aaa", created_at="2026-08-05T10:00:00Z", dst="d2")
    e3 = _edge(edge_id="e_ccc", created_at="2026-08-05T11:00:00Z", dst="d3")
    drops = select_fifo_overflow([e1, e2, e3], keep=2)
    assert [d.edge_id for d in drops] == ["e_aaa"]
    assert fifo_sort_key(e2) < fifo_sort_key(e1)


def test_plan_budget_drops_created_with_window():
    edges = [
        _edge(
            dst=f"d{i}",
            created_at=f"2026-08-05T{10 + i // 60:02d}:{i % 60:02d}:00Z",
            edge_id=f"e_{i:04d}",
        )
        for i in range(105)
    ]
    cfg = MemorySettings(edge_created_with_max=100, edge_max_per_atom=150)
    drops = plan_budget_drops(edges, cfg)
    assert len(drops) == 5
    drop_ids = {d.edge_id for d in drops}
    # Oldest five by (created_at, edge_id).
    expected = {f"e_{i:04d}" for i in range(5)}
    assert drop_ids == expected


def test_plan_budget_drops_does_not_affect_inbound_concept():
    """Inbound-only list is empty for budget planner when caller filters out.

    Budget helpers only see what the caller passes — write path must pass
    outgoing-from-src only (KD-E14).
    """
    out = [_edge(src="S", dst="T", kind=EDGE_RECALLS)]
    # Many "inbound" edges not included → no drops from them.
    assert plan_budget_drops(out, MemorySettings(edge_recalls_max=8)) == []


# ── Weights (durable kinds) ────────────────────────────────────────────────


def test_weights_durable_kinds_in_table():
    assert EDGE_CREATED_WITH in EDGE_KINDS
    assert EDGE_RECALLS in EDGE_KINDS
    assert EDGE_IN_MOMENT in EDGE_KINDS
    assert EDGE_HAS_CHANNEL in EDGE_KINDS
    assert base_weight(EDGE_CREATED_WITH) == BASE_CREATED_WITH == 0.72
    assert base_weight(EDGE_RECALLS) == BASE_RECALLS == 0.78
    assert EDGE_HAS_CHANNEL not in DEFAULT_EXPAND_KINDS
    assert EDGE_CREATED_WITH in DEFAULT_EXPAND_KINDS
    assert EDGE_RECALLS in DEFAULT_EXPAND_KINDS


def test_semantic_factor_recalls_parity_with_semantic_hop():
    assert semantic_factor(EDGE_RECALLS, cosine=0.8) == 0.8
    assert semantic_factor(EDGE_RECALLS, cosine=None) == 0.0
    assert semantic_factor(EDGE_RECALLS, cosine=1.5) == 1.0
    assert semantic_factor(EDGE_CREATED_WITH, cosine=None) == 1.0
    assert semantic_factor(EDGE_SEMANTIC_HOP, cosine=0.8) == 0.8
    w_r = edge_weight(EDGE_RECALLS, cosine=0.5, now=None)
    w_h = edge_weight(EDGE_SEMANTIC_HOP, cosine=0.5, now=None)
    assert abs(w_r - BASE_RECALLS * 0.5) < 1e-12
    assert abs(w_h - BASE_SEMANTIC_HOP * 0.5) < 1e-12
    # Stored weight is not authority — recompute from kind base + cosine.
    w_re = edge_weight(EDGE_RECALLS, cosine=0.9, now=None)
    assert abs(w_re - BASE_RECALLS * 0.9) < 1e-12


# ── JSONL backend ──────────────────────────────────────────────────────────


@pytest.fixture
def jsonl_store(paths) -> JsonlEdgeStore:
    s = open_edge_store(paths, MemorySettings(backend="jsonl"))
    assert isinstance(s, JsonlEdgeStore)
    return s


def test_open_jsonl_writes_edge_schema_version(paths, jsonl_store):
    meta = json.loads(memory_meta_path(paths).read_text(encoding="utf-8"))
    assert meta["edge_schema_version"] == EDGE_SCHEMA_VERSION
    h = jsonl_store.health()
    assert h["ok"] is True
    assert h["backend"] == "jsonl"
    assert h["edge_count"] == 0
    assert h["durable_edges_enabled"] is False


def test_jsonl_put_get_list_delete_count(jsonl_store):
    e = _edge(src="S1", dst="D1", kind=EDGE_CREATED_WITH)
    stored = jsonl_store.put_edge(e)
    assert stored.edge_id == e.edge_id
    got = jsonl_store.get_edge(e.edge_id)
    assert got is not None
    assert got.src_atom_id == "S1"
    assert got.dst_atom_id == "D1"
    assert got.edge_kind == EDGE_CREATED_WITH

    from_s = jsonl_store.list_edges_from("S1")
    assert len(from_s) == 1
    to_d = jsonl_store.list_edges_to("D1")
    assert len(to_d) == 1
    both = jsonl_store.list_edges_for_atom("S1")
    assert len(both) == 1
    assert jsonl_store.count_edges_for_atom("S1") == 1
    assert jsonl_store.count_edges_for_atom(
        "S1", kind=EDGE_CREATED_WITH
    ) == 1
    assert jsonl_store.count_edges_for_atom("S1", kind=EDGE_RECALLS) == 0

    assert jsonl_store.delete_edge(e.edge_id) is True
    assert jsonl_store.get_edge(e.edge_id) is None
    assert jsonl_store.list_edges_from("S1") == []
    assert jsonl_store.count_edges_for_atom("S1") == 0
    assert jsonl_store.delete_edge(e.edge_id) is False


def test_jsonl_unique_key_update_same_budget_slot(jsonl_store):
    e1 = _edge(
        src="S",
        dst="D",
        kind=EDGE_RECALLS,
        created_at="2026-08-05T10:00:00Z",
        meta={"cosine": 0.5},
        weight=0.3,
    )
    s1 = jsonl_store.put_edge(e1)
    e2 = _edge(
        src="S",
        dst="D",
        kind=EDGE_RECALLS,
        created_at="2026-08-05T12:00:00Z",
        edge_id=new_edge_id(),
        meta={"cosine": 0.9},
        weight=0.7,
        reason="refresh",
    )
    s2 = jsonl_store.put_edge(e2)
    assert s2.edge_id == s1.edge_id  # preserve identity
    assert s2.created_at == s1.created_at  # FIFO key stable
    assert s2.meta.get("cosine") == 0.9
    assert jsonl_store.count_edges_for_atom("S", kind=EDGE_RECALLS) == 1
    assert len(jsonl_store.list_edges_from("S")) == 1


def test_jsonl_list_kinds_and_reload(paths, jsonl_store):
    jsonl_store.put_edge(
        _edge(src="S", dst="D1", kind=EDGE_CREATED_WITH, edge_id="e_cw")
    )
    jsonl_store.put_edge(
        _edge(src="S", dst="D2", kind=EDGE_RECALLS, edge_id="e_rc")
    )
    only = jsonl_store.list_edges_from("S", kinds=[EDGE_RECALLS])
    assert len(only) == 1
    assert only[0].edge_kind == EDGE_RECALLS
    jsonl_store.close()

    reopened = open_edge_store(paths, MemorySettings(backend="jsonl"))
    assert reopened.count_edges_for_atom("S") == 2
    assert edges_jsonl_path(paths).is_file()
    reopened.close()


def test_jsonl_created_with_101st_drops_oldest(jsonl_store):
    cfg = MemorySettings(
        backend="jsonl",
        edge_created_with_max=100,
        edge_max_per_atom=150,
    )
    src = "S_budget"
    for i in range(101):
        # Monotonic created_at: 10:00 for 0..59, 11:00 for 60..100.
        if i < 60:
            ts = f"2026-08-05T10:{i:02d}:00Z"
        else:
            ts = f"2026-08-05T11:{i - 60:02d}:00Z"
        e = _edge(
            src=src,
            dst=f"D{i}",
            kind=EDGE_CREATED_WITH,
            created_at=ts,
            edge_id=f"e_{i:04d}",
        )
        put_edge_with_budget(jsonl_store, e, cfg)

    n = jsonl_store.count_edges_for_atom(src, kind=EDGE_CREATED_WITH)
    assert n == 100
    assert jsonl_store.get_edge("e_0000") is None  # oldest dropped
    assert jsonl_store.get_edge("e_0100") is not None  # newest kept
    assert jsonl_store.get_edge("e_0001") is not None


def test_jsonl_inbound_recalls_do_not_consume_dst_budget(jsonl_store):
    cfg = MemorySettings(edge_recalls_max=2, edge_max_per_atom=150)
    target = "T_dst"
    # Many speakers recall target — each is outgoing from speaker, not target.
    for i in range(10):
        e = _edge(
            src=f"speaker_{i}",
            dst=target,
            kind=EDGE_RECALLS,
            created_at=f"2026-08-05T10:{i:02d}:00Z",
            edge_id=f"e_in_{i}",
        )
        put_edge_with_budget(jsonl_store, e, cfg)
    assert jsonl_store.count_edges_for_atom(target, outgoing_only=True) == 0
    assert jsonl_store.count_edges_for_atom(target, outgoing_only=False) == 10
    assert len(jsonl_store.list_edges_to(target, kinds=[EDGE_RECALLS])) == 10


def test_jsonl_replace_edges_of_kind(jsonl_store):
    jsonl_store.put_edge(
        _edge(src="S", dst="old", kind=EDGE_HAS_CHANNEL, edge_id="e_old")
    )
    new_edges = [
        _edge(src="S", dst="S:text", kind=EDGE_HAS_CHANNEL, edge_id="e_t"),
        _edge(src="S", dst="S:image", kind=EDGE_HAS_CHANNEL, edge_id="e_i"),
    ]
    stored = jsonl_store.replace_edges_of_kind(
        "S", EDGE_HAS_CHANNEL, new_edges
    )
    assert len(stored) == 2
    assert jsonl_store.get_edge("e_old") is None
    kinds = {e.dst_atom_id for e in jsonl_store.list_edges_from("S")}
    assert kinds == {"S:text", "S:image"}


def test_jsonl_implements_protocol(jsonl_store):
    assert isinstance(jsonl_store, EdgeStore)


# ── Lance backend (optional) ───────────────────────────────────────────────


@lru_cache(maxsize=1)
def _lancedb_available() -> bool:
    """True when lancedb imports and connect works (subprocess probe)."""
    try:
        import lancedb  # noqa: F401, PLC0415
    except ImportError:
        return False
    code = (
        "import tempfile, lancedb\n"
        "d = tempfile.mkdtemp()\n"
        "db = lancedb.connect(d)\n"
        "db.table_names()\n"
        "print('ok')\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and "ok" in (proc.stdout or "")


lance_only = pytest.mark.skipif(
    not _lancedb_available(),
    reason="lancedb missing or connect unusable on this Python/runtime",
)


@pytest.fixture
def lance_store(paths):
    from elyra.memory.edges import LanceEdgeStore

    if not _lancedb_available():
        pytest.skip("lancedb missing or connect unusable")
    s = open_edge_store(paths, MemorySettings(backend="lance"), fail_soft=False)
    assert isinstance(s, LanceEdgeStore)
    yield s
    s.close()


@lance_only
def test_open_lance_edge_store(paths, lance_store):
    h = lance_store.health()
    assert h["ok"] is True
    assert h["backend"] == "lance"
    meta = json.loads(memory_meta_path(paths).read_text(encoding="utf-8"))
    assert meta["edge_schema_version"] == EDGE_SCHEMA_VERSION


@lance_only
def test_lance_put_get_list_delete_count(lance_store):
    e = _edge(src="LS1", dst="LD1", kind=EDGE_CREATED_WITH, meta={"k": 1})
    stored = lance_store.put_edge(e)
    assert stored.meta.get("k") == 1
    assert lance_store.get_edge(e.edge_id) is not None
    assert len(lance_store.list_edges_from("LS1")) == 1
    assert len(lance_store.list_edges_to("LD1")) == 1
    assert lance_store.count_edges_for_atom("LS1") == 1
    assert lance_store.delete_edge(e.edge_id) is True
    assert lance_store.count_edges_for_atom("LS1") == 0


@lance_only
def test_lance_unique_key_and_reload(paths, lance_store):
    e1 = _edge(src="S", dst="D", kind=EDGE_IN_MOMENT, edge_id="e_m1")
    lance_store.put_edge(e1)
    e2 = _edge(
        src="S",
        dst="D",
        kind=EDGE_IN_MOMENT,
        edge_id="e_m2",
        reason="again",
    )
    s2 = lance_store.put_edge(e2)
    assert s2.edge_id == "e_m1"
    assert lance_store.count_edges_for_atom("S") == 1
    lance_store.close()

    reopened = open_edge_store(
        paths, MemorySettings(backend="lance"), fail_soft=False
    )
    assert reopened.count_edges_for_atom("S") == 1
    got = reopened.list_edges_from("S")[0]
    assert got.reason == "again"
    reopened.close()


@lance_only
def test_lance_created_with_fifo_budget(lance_store):
    cfg = MemorySettings(
        backend="lance",
        edge_created_with_max=5,
        edge_max_per_atom=150,
    )
    src = "S_lfifo"
    for i in range(7):
        e = _edge(
            src=src,
            dst=f"D{i}",
            kind=EDGE_CREATED_WITH,
            created_at=f"2026-08-05T10:{i:02d}:00Z",
            edge_id=f"e_l{i}",
        )
        put_edge_with_budget(lance_store, e, cfg)
    assert lance_store.count_edges_for_atom(src) == 5
    assert lance_store.get_edge("e_l0") is None
    assert lance_store.get_edge("e_l1") is None
    assert lance_store.get_edge("e_l6") is not None


@lance_only
def test_lance_inbound_no_dst_budget(lance_store):
    cfg = MemorySettings(edge_recalls_max=1)
    for i in range(5):
        put_edge_with_budget(
            lance_store,
            _edge(
                src=f"sp{i}",
                dst="TARGET",
                kind=EDGE_RECALLS,
                edge_id=f"e_ri{i}",
            ),
            cfg,
        )
    assert lance_store.count_edges_for_atom("TARGET", outgoing_only=True) == 0
    assert len(lance_store.list_edges_to("TARGET")) == 5


@lance_only
def test_lance_implements_protocol(lance_store):
    assert isinstance(lance_store, EdgeStore)


# ── Factory fail-soft ──────────────────────────────────────────────────────


def test_unavailable_edge_store_reason():
    u = UnavailableEdgeStore("edge_backend_unavailable")
    assert u.health()["error"] == "edge_backend_unavailable"
    with pytest.raises(MemoryUnavailable):
        u.put_edge(_edge())
    assert u.list_edges_from("x") == []
    assert u.count_edges_for_atom("x") == 0


def test_enforce_outgoing_budgets_helper(jsonl_store):
    cfg = MemorySettings(edge_recalls_max=2)
    for i in range(4):
        jsonl_store.put_edge(
            _edge(
                src="S",
                dst=f"D{i}",
                kind=EDGE_RECALLS,
                created_at=f"2026-08-05T10:{i:02d}:00Z",
                edge_id=f"e_r{i}",
            )
        )
    dropped = enforce_outgoing_budgets(jsonl_store, "S", cfg)
    assert len(dropped) == 2
    assert jsonl_store.count_edges_for_atom("S", kind=EDGE_RECALLS) == 2


# ── Dev force edge backfill (polish1 PR4 / KD-P-backfill) ───────────────────


def test_backfill_in_moment_writes_and_idempotent(paths):
    """Missing in_moment hub edges written; re-run written≈0."""
    from elyra.memory.edges import backfill_durable_edges
    from elyra.memory.graph import moment_hub_id
    from elyra.memory.store import open_memory_store
    from elyra.memory.types import Atom, new_atom_id

    cfg = MemorySettings(
        write_atoms=True,
        backend="jsonl",
        durable_edges_enabled=True,
        edge_backfill_dev_enabled=True,
    )
    store = open_memory_store(paths, cfg)
    estore = open_edge_store(paths, cfg)
    try:
        mid = "m_bf_1"
        a = store.put_atom(
            Atom(
                atom_id=new_atom_id(),
                t_start="2026-08-05T10:00:00Z",
                kind="observation",
                content_text="hist atom a",
                content_ref="inline",
                moment_id=mid,
            )
        )
        b = store.put_atom(
            Atom(
                atom_id=new_atom_id(),
                t_start="2026-08-05T10:01:00Z",
                kind="speak",
                content_text="hist atom b",
                content_ref="inline",
                moment_id=mid,
            )
        )
        # No moment_id → skipped
        store.put_atom(
            Atom(
                atom_id=new_atom_id(),
                t_start="2026-08-05T10:02:00Z",
                kind="observation",
                content_text="orphan",
                content_ref="inline",
                moment_id="",
            )
        )

        r1 = backfill_durable_edges(store, estore, settings=cfg)
        assert r1["ok"] is True
        assert r1["scanned"] >= 3
        assert r1["written"] == 2
        assert r1["written_by_kind"].get(EDGE_IN_MOMENT) == 2
        assert r1["skipped"] >= 1
        hub = moment_hub_id(mid)
        for atom in (a, b):
            outs = estore.list_edges_from(atom.atom_id, kinds=[EDGE_IN_MOMENT])
            assert any(e.dst_atom_id == hub for e in outs)

        r2 = backfill_durable_edges(store, estore, settings=cfg)
        assert r2["ok"] is True
        assert r2["written"] == 0
        assert r2["skipped"] >= 3
    finally:
        estore.close()
        store.close()


def test_backfill_empty_store(paths):
    """Empty collection: ok, scanned=0, written=0."""
    from elyra.memory.edges import backfill_durable_edges
    from elyra.memory.store import open_memory_store

    cfg = MemorySettings(
        write_atoms=True,
        backend="jsonl",
        durable_edges_enabled=True,
        edge_backfill_dev_enabled=True,
    )
    store = open_memory_store(paths, cfg)
    estore = open_edge_store(paths, cfg)
    try:
        r = backfill_durable_edges(store, estore, settings=cfg)
        assert r["ok"] is True
        assert r["scanned"] == 0
        assert r["written"] == 0
        assert r["skipped"] == 0
        assert r["truncated"] is False
    finally:
        estore.close()
        store.close()


def test_backfill_requires_flags(paths):
    """Honest failure when durable_edges or dev flag off; no writes."""
    from elyra.memory.edges import backfill_durable_edges
    from elyra.memory.store import open_memory_store
    from elyra.memory.types import Atom, new_atom_id

    store = open_memory_store(
        paths, MemorySettings(write_atoms=True, backend="jsonl")
    )
    estore = open_edge_store(
        paths, MemorySettings(write_atoms=True, backend="jsonl")
    )
    try:
        store.put_atom(
            Atom(
                atom_id=new_atom_id(),
                t_start="2026-08-05T10:00:00Z",
                kind="observation",
                content_text="x",
                content_ref="inline",
                moment_id="m_x",
            )
        )
        # durable off (default) even if dev on
        r = backfill_durable_edges(
            store,
            estore,
            settings=MemorySettings(
                durable_edges_enabled=False,
                edge_backfill_dev_enabled=True,
            ),
        )
        assert r["ok"] is False
        assert r["error"] == "durable_edges_disabled"
        assert estore.list_edges_from(
            store.list_atoms(limit=1)[0].atom_id
        ) == []

        # dev off even if durable on
        r2 = backfill_durable_edges(
            store,
            estore,
            settings=MemorySettings(
                durable_edges_enabled=True,
                edge_backfill_dev_enabled=False,
            ),
        )
        assert r2["ok"] is False
        assert r2["error"] == "edge_backfill_dev_disabled"

        # edge_store None
        r3 = backfill_durable_edges(
            store,
            None,
            settings=MemorySettings(
                durable_edges_enabled=True,
                edge_backfill_dev_enabled=True,
            ),
        )
        assert r3["ok"] is False
        assert r3["error"] == "edge_store_unavailable"
    finally:
        estore.close()
        store.close()


def test_backfill_max_ms_truncates(paths):
    """max_ms=0 forces truncated without writes (budget path)."""
    from elyra.memory.edges import backfill_durable_edges
    from elyra.memory.store import open_memory_store
    from elyra.memory.types import Atom, new_atom_id

    cfg = MemorySettings(
        write_atoms=True,
        backend="jsonl",
        durable_edges_enabled=True,
        edge_backfill_dev_enabled=True,
    )
    store = open_memory_store(paths, cfg)
    estore = open_edge_store(paths, cfg)
    try:
        store.put_atom(
            Atom(
                atom_id=new_atom_id(),
                t_start="2026-08-05T10:00:00Z",
                kind="observation",
                content_text="x",
                content_ref="inline",
                moment_id="m_t",
            )
        )
        r = backfill_durable_edges(
            store, estore, settings=cfg, max_ms=0, max_atoms=10
        )
        assert r["ok"] is True
        assert r["truncated"] is True
        assert r["written"] == 0
        assert r["scanned"] == 0
    finally:
        estore.close()
        store.close()


def test_edge_backfill_dev_enabled_factory_default():
    """Dogfood ON; Gate B durable_edges stays factory off."""
    from elyra.memory.config import (
        is_durable_edges_enabled,
        is_edge_backfill_dev_enabled,
    )

    d = MemorySettings()
    assert d.edge_backfill_dev_enabled is True
    assert d.durable_edges_enabled is False
    assert is_edge_backfill_dev_enabled(d) is True
    assert is_durable_edges_enabled(d) is False


def test_backfill_scans_beyond_glass_list_atoms_max(paths):
    """Operator bulk path honors max_atoms > LIST_ATOMS_MAX (glass cap)."""
    from elyra.memory.edges import backfill_durable_edges
    from elyra.memory.graph import moment_hub_id
    from elyra.memory.store import LIST_ATOMS_MAX, open_memory_store
    from elyra.memory.types import Atom, new_atom_id

    cfg = MemorySettings(
        write_atoms=True,
        backend="jsonl",
        durable_edges_enabled=True,
        edge_backfill_dev_enabled=True,
    )
    store = open_memory_store(paths, cfg)
    estore = open_edge_store(paths, cfg)
    try:
        n = LIST_ATOMS_MAX + 25  # 225
        mid = "m_bulk"
        for i in range(n):
            store.put_atom(
                Atom(
                    atom_id=new_atom_id(),
                    t_start=f"2026-08-05T10:{i // 60:02d}:{i % 60:02d}Z",
                    kind="observation",
                    content_text=f"bulk {i}",
                    content_ref="inline",
                    moment_id=mid,
                )
            )
        # Glass list still capped.
        assert len(store.list_atoms(limit=1000)) == LIST_ATOMS_MAX
        # Bulk backfill scans beyond glass cap.
        r = backfill_durable_edges(
            store, estore, settings=cfg, max_atoms=n, max_ms=60_000
        )
        assert r["ok"] is True
        assert r["scanned"] == n
        assert r["written"] == n
        hub = moment_hub_id(mid)
        # Spot-check oldest atom still got a hub edge.
        oldest = store.list_atoms(newest_first=False, limit=1, glass_cap=False)[0]
        outs = estore.list_edges_from(oldest.atom_id, kinds=[EDGE_IN_MOMENT])
        assert any(e.dst_atom_id == hub for e in outs)
    finally:
        estore.close()
        store.close()


def test_backfill_unavailable_edge_store_early_out(paths):
    """UnavailableEdgeStore fails closed once — no per-atom error spam."""
    from elyra.memory.edges import UnavailableEdgeStore, backfill_durable_edges
    from elyra.memory.store import open_memory_store
    from elyra.memory.types import Atom, new_atom_id

    cfg = MemorySettings(
        write_atoms=True,
        backend="jsonl",
        durable_edges_enabled=True,
        edge_backfill_dev_enabled=True,
    )
    store = open_memory_store(paths, cfg)
    try:
        for i in range(3):
            store.put_atom(
                Atom(
                    atom_id=new_atom_id(),
                    t_start=f"2026-08-05T11:0{i}:00Z",
                    kind="observation",
                    content_text=f"x{i}",
                    content_ref="inline",
                    moment_id="m_u",
                )
            )
        bad = UnavailableEdgeStore("edge_backend_unavailable")
        r = backfill_durable_edges(store, bad, settings=cfg)
        assert r["ok"] is False
        assert r["error"] == "edge_backend_unavailable"
        assert r["scanned"] == 0
        assert r["written"] == 0
        assert r["errors"] == 0
    finally:
        store.close()
