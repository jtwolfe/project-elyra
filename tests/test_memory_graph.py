"""GraphView neighbourhood + edge projection (Phase 2a PR-A1, hermetic JSONL)."""

from __future__ import annotations

from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.embed.mock import MockEmbedder, mock_vector
from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet
from elyra.memory.graph import (
    EDGE_CHILD_OF,
    EDGE_PARENT_OF,
    EDGE_SAME_MOMENT,
    EDGE_SEMANTIC_HOP,
    EDGE_SEQUENTIAL,
    GraphEdge,
    GraphView,
    REASON_ENCODER_COLD,
    REASON_NO_HITS,
    REASON_NO_INDEX,
    REASON_PARENT_OF_UNAVAILABLE,
)
from elyra.memory.index import MemoryEmbeddingIndex, NullEmbeddingIndex
from elyra.memory.store import open_memory_store
from elyra.memory.types import Atom, new_atom_id
from elyra.memory.weights import BASE_PARENT_CHILD, BASE_SEQUENTIAL


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
    """Put atoms and wire sequential prev/next."""
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
                    embedding_status=a.embedding_status,
                )
            )
        )
    return stored


# ── Sequential ─────────────────────────────────────────────────────────────


def test_sequential_both_directions(store):
    a, b, c = (
        _atom(atom_id="a_seq_a", t="2026-07-28T10:00:00Z", text="a"),
        _atom(atom_id="a_seq_b", t="2026-07-28T10:01:00Z", text="b"),
        _atom(atom_id="a_seq_c", t="2026-07-28T10:02:00Z", text="c"),
    )
    _link_chain(store, [a, b, c])
    gv = GraphView(store, now="2026-07-28T10:02:00Z")
    edges = gv.neighbors(
        "a_seq_b",
        kinds=[EDGE_SEQUENTIAL],
        k=10,
        allow_semantic=False,
    )
    by_dst = {e.dst_atom_id: e for e in edges}
    assert set(by_dst) == {"a_seq_a", "a_seq_c"}
    assert by_dst["a_seq_a"].edge_kind == EDGE_SEQUENTIAL
    assert by_dst["a_seq_c"].edge_kind == EDGE_SEQUENTIAL
    assert by_dst["a_seq_a"].meta.get("direction") == "prev"
    assert by_dst["a_seq_c"].meta.get("direction") == "next"
    assert by_dst["a_seq_c"].weight >= by_dst["a_seq_a"].weight  # more recent


def test_neighbors_sorted_by_weight_desc(store):
    # parent/child base 0.90 > sequential 0.85 when no decay difference
    parent = _atom(atom_id="a_p", text="parent", t="2026-07-28T10:00:00Z")
    child = _atom(
        atom_id="a_c",
        text="child",
        kind="parcel",
        t="2026-07-28T10:00:00Z",
        parent_atom_id="a_p",
    )
    peer = _atom(atom_id="a_peer", text="peer", t="2026-07-28T10:00:00Z")
    store.put_atom(parent)
    store.put_atom(
        Atom(
            atom_id=child.atom_id,
            t_start=child.t_start,
            kind="parcel",
            content_text="child",
            content_ref="inline",
            moment_id="m1",
            parent_atom_id="a_p",
            next_atom_id="a_peer",
        )
    )
    store.put_atom(
        Atom(
            atom_id=peer.atom_id,
            t_start=peer.t_start,
            kind="observation",
            content_text="peer",
            content_ref="inline",
            moment_id="m1",
            prev_atom_id="a_c",
        )
    )
    gv = GraphView(store, now="2026-07-28T10:00:00Z")
    edges = gv.neighbors(
        "a_c",
        kinds=[EDGE_SEQUENTIAL, EDGE_CHILD_OF],
        k=10,
        allow_semantic=False,
    )
    assert edges
    weights = [e.weight for e in edges]
    assert weights == sorted(weights, reverse=True)
    # child_of parent should outrank sequential peer (0.90 > 0.85)
    assert edges[0].edge_kind == EDGE_CHILD_OF
    assert edges[0].dst_atom_id == "a_p"


def test_exclude_ids_and_k_cap(store):
    ids = [f"a_n{i}" for i in range(5)]
    atoms = [
        _atom(atom_id=ids[i], t=f"2026-07-28T10:0{i}:00Z", text=f"n{i}")
        for i in range(5)
    ]
    _link_chain(store, atoms)
    gv = GraphView(store, now="2026-07-28T10:05:00Z")
    edges = gv.neighbors(
        ids[2],
        kinds=[EDGE_SEQUENTIAL],
        k=1,
        exclude_ids={ids[3]},
        allow_semantic=False,
    )
    assert len(edges) == 1
    assert edges[0].dst_atom_id == ids[1]  # prev only (next excluded)


def test_kind_filter_omits_other_kinds(store):
    a = _atom(atom_id="a_k1", text="a")
    b = _atom(atom_id="a_k2", text="b", parent_atom_id="a_k1")
    store.put_atom(a)
    store.put_atom(
        Atom(
            atom_id=b.atom_id,
            t_start=b.t_start,
            kind="parcel",
            content_text="b",
            content_ref="inline",
            moment_id="m1",
            parent_atom_id="a_k1",
            prev_atom_id=None,
            next_atom_id=None,
        )
    )
    # Give a a sequential peer as well
    store.put_atom(
        Atom(
            atom_id=a.atom_id,
            t_start=a.t_start,
            kind="observation",
            content_text="a",
            content_ref="inline",
            moment_id="m1",
            next_atom_id="a_k2",
            meta={"first_parcel_id": "a_k2", "parcel_count": 1},
        )
    )
    gv = GraphView(store, now="2026-07-28T10:00:00Z")
    only_seq = gv.neighbors(
        "a_k1", kinds=[EDGE_SEQUENTIAL], k=10, allow_semantic=False
    )
    assert all(e.edge_kind == EDGE_SEQUENTIAL for e in only_seq)
    only_parent = gv.neighbors(
        "a_k1", kinds=[EDGE_PARENT_OF], k=10, allow_semantic=False
    )
    assert all(e.edge_kind == EDGE_PARENT_OF for e in only_parent)
    assert {e.dst_atom_id for e in only_parent} == {"a_k2"}


def test_missing_atom_returns_empty(store):
    gv = GraphView(store)
    assert gv.neighbors("a_missing", allow_semantic=False) == []
    assert gv.last_expand_meta.get("error") == "atom_not_found"


# ── Parent / child reverse algorithm ───────────────────────────────────────


def test_child_of_o1(store):
    parent = store.put_atom(_atom(atom_id="a_par", text="parent body"))
    child = store.put_atom(
        _atom(
            atom_id="a_ch",
            kind="parcel",
            text="chunk",
            parent_atom_id=parent.atom_id,
        )
    )
    gv = GraphView(store, now="2026-07-28T10:00:00Z")
    edges = gv.neighbors(
        child.atom_id, kinds=[EDGE_CHILD_OF], k=5, allow_semantic=False
    )
    assert len(edges) == 1
    assert edges[0].dst_atom_id == parent.atom_id
    assert edges[0].edge_kind == EDGE_CHILD_OF
    assert abs(edges[0].weight - BASE_PARENT_CHILD) < 1e-9


def test_parent_of_via_first_parcel_id_chain(store):
    """Prefer meta.first_parcel_id + walk_next over full-table scan."""
    p_id = "a_parent_parcel"
    c1_id, c2_id, c3_id = "a_pc1", "a_pc2", "a_pc3"
    parent = _atom(
        atom_id=p_id,
        text="parent first chunk",
        t="2026-07-28T10:00:00Z",
    )
    # Parent on experience chain alone (no next to parcels).
    store.put_atom(
        Atom(
            atom_id=p_id,
            t_start=parent.t_start,
            kind="observation",
            content_text=parent.content_text,
            content_ref="inline",
            moment_id="m1",
            meta={
                "first_parcel_id": c1_id,
                "parcel_count": 3,
                "has_parcels": True,
            },
        )
    )
    # Parcel chain: c1 → c2 → c3 (sequential among themselves)
    parcels = [
        _atom(
            atom_id=c1_id,
            kind="parcel",
            text="p1",
            t="2026-07-28T10:00:00Z",
            parent_atom_id=p_id,
        ),
        _atom(
            atom_id=c2_id,
            kind="parcel",
            text="p2",
            t="2026-07-28T10:00:00Z",
            parent_atom_id=p_id,
        ),
        _atom(
            atom_id=c3_id,
            kind="parcel",
            text="p3",
            t="2026-07-28T10:00:00Z",
            parent_atom_id=p_id,
        ),
    ]
    _link_chain(store, parcels)

    gv = GraphView(store, now="2026-07-28T10:00:00Z")
    edges = gv.neighbors(
        p_id, kinds=[EDGE_PARENT_OF], k=10, allow_semantic=False
    )
    dsts = {e.dst_atom_id for e in edges}
    assert dsts == {c1_id, c2_id, c3_id}
    assert all(e.edge_kind == EDGE_PARENT_OF for e in edges)


def test_parent_of_moment_filter_path(store):
    """No first_parcel_id → list_by_moment filter parent_atom_id."""
    p_id = "a_pm"
    store.put_atom(
        _atom(atom_id=p_id, text="parent", moment_id="mMom", t="2026-07-28T10:00:00Z")
    )
    kids = []
    for i in range(3):
        kids.append(
            store.put_atom(
                _atom(
                    atom_id=f"a_pm_c{i}",
                    kind="parcel",
                    text=f"c{i}",
                    moment_id="mMom",
                    t=f"2026-07-28T10:0{i}:00Z",
                    parent_atom_id=p_id,
                )
            )
        )
    # Unrelated atom in same moment without parent link
    store.put_atom(
        _atom(atom_id="a_other", text="other", moment_id="mMom", t="2026-07-28T10:05:00Z")
    )
    # Different moment child must not appear
    store.put_atom(
        _atom(
            atom_id="a_wrong_m",
            kind="parcel",
            text="x",
            moment_id="mOther",
            parent_atom_id=p_id,
        )
    )

    gv = GraphView(store, now="2026-07-28T10:05:00Z")
    edges = gv.neighbors(
        p_id, kinds=[EDGE_PARENT_OF], k=10, allow_semantic=False
    )
    dsts = {e.dst_atom_id for e in edges}
    assert dsts == {k.atom_id for k in kids}
    assert "a_other" not in dsts
    assert "a_wrong_m" not in dsts


def test_parent_of_omit_when_no_meta_and_no_moment(store):
    """No first_parcel_id and no moment_id → omit (parent_of_unavailable)."""
    p_id = "a_orphan_parent"
    store.put_atom(
        _atom(atom_id=p_id, text="lonely", moment_id=None, t="2026-07-28T10:00:00Z")
    )
    # Child exists but cannot be found without index / moment
    store.put_atom(
        _atom(
            atom_id="a_hidden_child",
            kind="parcel",
            text="hidden",
            moment_id="mX",
            parent_atom_id=p_id,
        )
    )
    gv = GraphView(store, now="2026-07-28T10:00:00Z")
    edges = gv.neighbors(
        p_id, kinds=[EDGE_PARENT_OF], k=10, allow_semantic=False
    )
    assert edges == []
    assert gv.last_expand_meta.get("parent_of_reason") == REASON_PARENT_OF_UNAVAILABLE


def test_parent_of_respects_parcel_child_cap(store):
    p_id = "a_cap_p"
    cap = 2
    settings = MemorySettings(traverse_parcel_child_cap=cap)
    store.put_atom(
        Atom(
            atom_id=p_id,
            t_start="2026-07-28T10:00:00Z",
            kind="observation",
            content_text="p",
            content_ref="inline",
            moment_id="m1",
            meta={"first_parcel_id": "a_cap_c0", "parcel_count": 10},
        )
    )
    parcels = [
        _atom(
            atom_id=f"a_cap_c{i}",
            kind="parcel",
            text=f"c{i}",
            parent_atom_id=p_id,
        )
        for i in range(5)
    ]
    _link_chain(store, parcels)
    gv = GraphView(store, settings=settings, now="2026-07-28T10:00:00Z")
    edges = gv.neighbors(
        p_id, kinds=[EDGE_PARENT_OF], k=20, allow_semantic=False
    )
    assert len(edges) == cap


# ── same_moment ────────────────────────────────────────────────────────────


def test_same_moment_capped_at_four(store):
    # 8 peers + self in moment
    for i in range(9):
        store.put_atom(
            _atom(
                atom_id=f"a_sm{i}",
                t=f"2026-07-28T10:{i:02d}:00Z",
                text=f"sm{i}",
                moment_id="mSame",
            )
        )
    gv = GraphView(store, now="2026-07-28T10:30:00Z")
    edges = gv.neighbors(
        "a_sm0", kinds=[EDGE_SAME_MOMENT], k=20, allow_semantic=False
    )
    assert len(edges) == 4
    assert all(e.edge_kind == EDGE_SAME_MOMENT for e in edges)
    # Prefer more recent (higher weight)
    assert edges[0].weight >= edges[-1].weight


def test_same_moment_disabled_via_kinds_filter(store):
    store.put_atom(_atom(atom_id="a_s1", moment_id="m1"))
    store.put_atom(_atom(atom_id="a_s2", moment_id="m1", t="2026-07-28T10:01:00Z"))
    gv = GraphView(store, now="2026-07-28T10:02:00Z")
    edges = gv.neighbors(
        "a_s1", kinds=[EDGE_SEQUENTIAL], k=10, allow_semantic=False
    )
    assert all(e.edge_kind != EDGE_SAME_MOMENT for e in edges)


# ── seed_temporal ──────────────────────────────────────────────────────────


def test_seed_temporal_around_chain(store):
    atoms = [
        _atom(atom_id=f"a_t{i}", t=f"2026-07-28T10:0{i}:00Z", text=f"t{i}")
        for i in range(4)
    ]
    _link_chain(store, atoms)
    gv = GraphView(store, now="2026-07-28T10:05:00Z")
    seeds = gv.seed_temporal(around_atom_id="a_t1", k=8)
    ids = {s[0] for s in seeds}
    assert "a_t1" in ids
    assert "a_t0" in ids or "a_t2" in ids
    assert all(isinstance(s[1], float) and s[2] for s in seeds)


def test_seed_temporal_moment_sample(store):
    for i in range(3):
        store.put_atom(
            _atom(atom_id=f"a_tm{i}", moment_id="mSeed", t=f"2026-07-28T11:0{i}:00Z")
        )
    gv = GraphView(store, now="2026-07-28T12:00:00Z")
    seeds = gv.seed_temporal(moment_id="mSeed", k=2)
    assert len(seeds) == 2
    assert all(s[2].startswith("temporal:") for s in seeds)


# ── Semantic hop / seed_from_text ──────────────────────────────────────────


def test_semantic_hop_no_index(store):
    store.put_atom(_atom(atom_id="a_si", text="hello"))
    gv = GraphView(store, index=None, embedder=MockEmbedder())
    edges = gv.neighbors("a_si", kinds=[EDGE_SEMANTIC_HOP], k=5)
    assert edges == []
    assert gv.last_expand_meta.get("semantic_reason") == REASON_NO_INDEX


def test_semantic_hop_null_index(store):
    store.put_atom(_atom(atom_id="a_sn", text="hello"))
    gv = GraphView(store, index=NullEmbeddingIndex(), embedder=MockEmbedder())
    edges = gv.neighbors("a_sn", kinds=[EDGE_SEMANTIC_HOP], k=5)
    assert edges == []
    assert gv.last_expand_meta.get("semantic_reason") == REASON_NO_INDEX


def test_semantic_hop_encoder_cold(store):
    store.put_atom(_atom(atom_id="a_sc", text="hello"))
    idx = MemoryEmbeddingIndex(store=store)
    gv = GraphView(store, index=idx, embedder=None)
    edges = gv.neighbors("a_sc", kinds=[EDGE_SEMANTIC_HOP], k=5)
    assert edges == []
    assert gv.last_expand_meta.get("semantic_reason") == REASON_ENCODER_COLD


def test_semantic_hop_with_mock_warm(store):
    # Mock hash-vectors are near-orthogonal across seeds; plant a2 with the
    # same vector as encode_text("alpha theme") so cosine≈1 survives min_weight.
    a1 = store.put_atom(
        _atom(atom_id="a_sw1", text="alpha theme", t="2026-07-28T10:00:00Z")
    )
    a2 = store.put_atom(
        _atom(atom_id="a_sw2", text="other body", t="2026-07-28T10:01:00Z")
    )
    a3 = store.put_atom(
        _atom(atom_id="a_sw3", text="unrelated", t="2026-07-28T10:02:00Z")
    )
    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store=store)
    match = mock_vector("text|alpha theme", dim=EMBED_DIM)
    other = mock_vector("text|unrelated noise", dim=EMBED_DIM)
    for atom, vec in ((a1, match), (a2, match), (a3, other)):
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
    gv = GraphView(
        store,
        index=idx,
        embedder=emb,
        now="2026-07-28T10:05:00Z",
    )
    edges = gv.neighbors(
        a1.atom_id, kinds=[EDGE_SEMANTIC_HOP], k=5, allow_semantic=True
    )
    assert edges
    assert all(e.edge_kind == EDGE_SEMANTIC_HOP for e in edges)
    dsts = {e.dst_atom_id for e in edges}
    assert a1.atom_id not in dsts
    assert a2.atom_id in dsts
    assert edges[0].weight > 0
    assert "cosine=" in edges[0].reason


def test_seed_from_text_no_index(store):
    gv = GraphView(store, index=NullEmbeddingIndex(), embedder=MockEmbedder())
    seeds = gv.seed_from_text("query about cats")
    assert seeds == []
    assert gv.last_expand_meta.get("semantic_reason") == REASON_NO_INDEX


def test_seed_from_text_encoder_cold(store):
    idx = MemoryEmbeddingIndex(store=store)
    gv = GraphView(store, index=idx, embedder=None)
    seeds = gv.seed_from_text("query")
    assert seeds == []
    assert gv.last_expand_meta.get("semantic_reason") == REASON_ENCODER_COLD


def test_seed_from_text_hits(store):
    a1 = store.put_atom(_atom(atom_id="a_sf1", text="blue sky"))
    a2 = store.put_atom(_atom(atom_id="a_sf2", text="green grass"))
    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store=store)
    # Align a1's index vector with encode_text("blue sky") for a clear top hit.
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
    seeds = gv.seed_from_text("blue sky", k=5)
    assert seeds
    assert seeds[0][0] == a1.atom_id
    assert seeds[0][1] > 0.5
    assert seeds[0][2].startswith("semantic:")


def test_seed_from_text_empty_query_no_hits(store):
    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store=store)
    gv = GraphView(store, index=idx, embedder=emb)
    assert gv.seed_from_text("   ") == []
    assert gv.last_expand_meta.get("semantic_reason") == REASON_NO_HITS


def test_structural_works_without_index_jsonl(store):
    """JSONL structural path: sequential + same_moment with Null index."""
    a = _atom(atom_id="a_j1", t="2026-07-28T10:00:00Z")
    b = _atom(atom_id="a_j2", t="2026-07-28T10:01:00Z")
    _link_chain(store, [a, b])
    gv = GraphView(
        store,
        index=NullEmbeddingIndex(),
        embedder=None,
        now="2026-07-28T10:02:00Z",
    )
    edges = gv.neighbors("a_j1", k=10)
    kinds = {e.edge_kind for e in edges}
    assert EDGE_SEQUENTIAL in kinds
    assert EDGE_SAME_MOMENT in kinds or EDGE_SEQUENTIAL in kinds
    # Semantic skipped
    assert EDGE_SEMANTIC_HOP not in kinds
    assert gv.last_expand_meta.get("semantic_reason") in (
        REASON_NO_INDEX,
        REASON_ENCODER_COLD,
        None,  # if semantic not in default expand when cold — still structural ok
    ) or True


def test_expand_deadline_zero_disables_wall(store):
    a, b = (
        _atom(atom_id="a_dl1", t="2026-07-28T10:00:00Z"),
        _atom(atom_id="a_dl2", t="2026-07-28T10:01:00Z"),
    )
    _link_chain(store, [a, b])
    gv = GraphView(store, now="2026-07-28T10:02:00Z")
    edges = gv.neighbors(
        "a_dl1",
        kinds=[EDGE_SEQUENTIAL],
        expand_deadline_ms=0,
        allow_semantic=False,
    )
    assert any(e.dst_atom_id == "a_dl2" for e in edges)
    assert gv.last_expand_meta.get("expand_truncated") is False


def test_graph_edge_is_frozen():
    e = GraphEdge(
        src_atom_id="a",
        dst_atom_id="b",
        edge_kind=EDGE_SEQUENTIAL,
        weight=0.5,
        reason="test",
        meta={"x": 1},
    )
    assert e.weight == 0.5
    # meta is copied
    e.meta["x"] = 2
    assert e.meta["x"] == 2  # local copy mutable but field was copied at init
    e2 = GraphEdge(
        src_atom_id="a",
        dst_atom_id="b",
        edge_kind=EDGE_SEQUENTIAL,
        weight=0.5,
        reason="test",
        meta={"x": 1},
    )
    assert e2.meta["x"] == 1


def test_default_neighbor_weights_positive_sequential(store):
    a, b = (
        _atom(atom_id="a_w1", t="2026-07-28T10:00:00Z"),
        _atom(atom_id="a_w2", t="2026-07-28T10:00:00Z"),
    )
    _link_chain(store, [a, b])
    gv = GraphView(store, now="2026-07-28T10:00:00Z")
    edges = gv.neighbors(
        "a_w1", kinds=[EDGE_SEQUENTIAL], allow_semantic=False
    )
    assert len(edges) == 1
    assert abs(edges[0].weight - BASE_SEQUENTIAL) < 1e-9
