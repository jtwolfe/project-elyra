"""PR4: speak-time recalls + encode-ready has_channel (hermetic)."""

from __future__ import annotations

from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.edges import (
    channel_virtual_id,
    open_edge_store,
    rank_recalls_candidates,
    write_has_channel_edges,
    write_speak_recalls,
)
from elyra.memory.embed.mock import MockEmbedder
from elyra.memory.embed.queue import EncodeQueue
from elyra.memory.index import ScoredAtom
from elyra.memory.promote import (
    promote_beat,
    promote_view_observation,
    promote_wake_observation,
)
from elyra.memory.store import open_memory_store
from elyra.memory.types import Atom, new_atom_id, utc_now_iso
from elyra.memory.weights import EDGE_HAS_CHANNEL, EDGE_RECALLS


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


@pytest.fixture
def edge_store(paths):
    s = open_edge_store(paths, MemorySettings(backend="jsonl"))
    yield s
    s.close()


def _settings(**kwargs: Any) -> MemorySettings:
    base = dict(
        write_atoms=True,
        backend="jsonl",
        durable_edges_enabled=True,
        semantic_enabled=True,
        semantic_wait_for_select=True,
        semantic_wait_max_ms=15_000,
        edge_recalls_ann_k=15,
        edge_recalls_keep=5,
        edge_recalls_max=8,
        # edge_recalls_max_ms is deprecated no-op for live ANN ceiling.
        edge_recalls_skip_queue_depth=64,
        # Inline for hermetic promote tests (product default is deferred).
        edge_recalls_inline=True,
    )
    base.update(kwargs)
    return MemorySettings(**base)


class _FakeIndex:
    """Mock EmbeddingIndex.search with canned scored hits."""

    def __init__(self, hits: list[ScoredAtom] | None = None) -> None:
        self.hits = list(hits or [])
        self.last_search: dict[str, Any] = {}
        self.upserts: list[Any] = []

    def search(self, query: Any, **kwargs: Any) -> list[ScoredAtom]:
        self.last_search = {"query": query, **kwargs}
        exclude = set(kwargs.get("exclude_atom_ids") or ())
        kinds = kwargs.get("kinds")
        kind_set = set(kinds) if kinds is not None else None
        out: list[ScoredAtom] = []
        for h in self.hits:
            if h.atom_id in exclude:
                continue
            if kind_set is not None and h.atom is not None:
                if h.atom.kind not in kind_set:
                    continue
            out.append(h)
        k = int(kwargs.get("k") or 15)
        return out[:k]

    def upsert(self, embedding_set: Any) -> bool:
        self.upserts.append(embedding_set)
        return True


class _ColdEmbedder:
    def health(self) -> dict[str, Any]:
        return {"ok": False, "reason": "cold"}

    def encode_text(self, text: str) -> list[float]:
        raise AssertionError("cold embedder must not encode")


class _DeepQueue:
    def __init__(self, depth: int) -> None:
        self._depth = depth

    def qsize(self) -> int:
        return self._depth


def _atom(
    *,
    text: str = "body",
    kind: str = "speak",
    t: str = "2026-08-01T10:00:00Z",
    atom_id: str | None = None,
    moment_id: str = "m1",
) -> Atom:
    return Atom(
        atom_id=atom_id or new_atom_id(),
        t_start=t,
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=moment_id,
        embedding_status="ready",
    )


# ── Pure ranking (v1 sim → recency) ────────────────────────────────────────


def test_rank_recalls_sim_then_newest():
    """Top ~15 by sim, then newest ~5 by t_start among survivors (OQ-E3)."""
    # 20 candidates: high sim are older; a few mid-sim are newer.
    cands: list[tuple[str, float, str]] = []
    for i in range(20):
        # score decreases with i; t_start increases with i (newer later).
        cands.append(
            (
                f"a_{i:02d}",
                1.0 - i * 0.01,
                f"2026-08-01T{10 + i:02d}:00:00Z",
            )
        )
    # top 15 by sim = a_00..a_14; newest 5 among those = a_14..a_10
    chosen = rank_recalls_candidates(cands, ann_k=15, keep=5)
    ids = [c[0] for c in chosen]
    assert ids == ["a_14", "a_13", "a_12", "a_11", "a_10"]
    # Cosines preserved from sim step (not re-ranked fused).
    assert chosen[0][1] == pytest.approx(1.0 - 14 * 0.01)


def test_rank_recalls_empty_and_caps():
    assert rank_recalls_candidates([], ann_k=15, keep=5) == []
    assert rank_recalls_candidates([("a", 0.9, "t")], ann_k=0, keep=5) == []
    one = rank_recalls_candidates(
        [("a", 0.5, "2026-01-01T00:00:00Z"), ("b", 0.9, "2026-01-02T00:00:00Z")],
        ann_k=15,
        keep=1,
    )
    # Among top-by-sim (both), newest is b.
    assert one == [("b", 0.9)]


# ── write_speak_recalls soft-fail + ranking ────────────────────────────────


def test_write_speak_recalls_newest_of_top_sim(store, edge_store):
    """Mock index: durable edges to newest keep among top ann_k spoken hits."""
    hits: list[ScoredAtom] = []
    # 8 spoken-ish atoms with decreasing scores, increasing t_start
    for i in range(8):
        a = _atom(
            text=f"past speak {i}",
            kind="speak" if i % 2 == 0 else "observation",
            t=f"2026-08-0{1 + (i // 3)}T{10 + i:02d}:00:00Z",
            atom_id=f"a_past_{i}",
        )
        store.put_atom(a)
        hits.append(ScoredAtom(atom_id=a.atom_id, score=0.95 - i * 0.05, atom=a))
    # Tool hit must be filtered by kinds even if present in canned list.
    tool = _atom(text="tool out", kind="tool", atom_id="a_tool", t="2026-08-05T23:00:00Z")
    store.put_atom(tool)
    hits.append(ScoredAtom(atom_id=tool.atom_id, score=0.99, atom=tool))

    idx = _FakeIndex(hits)
    emb = MockEmbedder()
    src = _atom(text="remind me of past speaks", kind="speak", atom_id="a_src")
    store.put_atom(src)

    cfg = _settings(edge_recalls_ann_k=15, edge_recalls_keep=5)
    written = write_speak_recalls(
        src_atom_id=src.atom_id,
        spoken_text=src.content_text or "",
        settings=cfg,
        edge_store=edge_store,
        index=idx,
        embedder=emb,
        store=store,
    )
    assert len(written) == 5
    assert all(e.edge_kind == EDGE_RECALLS for e in written)
    assert all(e.src_atom_id == src.atom_id for e in written)
    # kinds filter: tool never a dst
    dsts = {e.dst_atom_id for e in written}
    assert "a_tool" not in dsts
    # newest 5 of spoken hits (all 8 spoken after filter, keep 5 newest)
    # hits a_past_0..7; newest t are higher i → a_past_7..a_past_3
    assert dsts == {f"a_past_{i}" for i in range(3, 8)}
    # meta.cosine present
    for e in written:
        assert "cosine" in e.meta
        assert 0.0 <= float(e.meta["cosine"]) <= 1.0
    # search used spoken kinds
    assert set(idx.last_search.get("kinds") or []) == {"speak", "observation"}
    assert src.atom_id in (idx.last_search.get("exclude_atom_ids") or set())


def test_write_speak_recalls_soft_skip_cold(edge_store):
    written = write_speak_recalls(
        src_atom_id="a_src",
        spoken_text="hello",
        settings=_settings(),
        edge_store=edge_store,
        index=_FakeIndex([ScoredAtom(atom_id="x", score=0.9)]),
        embedder=_ColdEmbedder(),
    )
    assert written == []
    assert edge_store.list_edges_from("a_src") == []


def test_write_speak_recalls_soft_skip_encode_pressure(edge_store):
    emb = MockEmbedder()
    idx = _FakeIndex(
        [ScoredAtom(atom_id="a1", score=0.9, atom=_atom(atom_id="a1"))]
    )
    written = write_speak_recalls(
        src_atom_id="a_src",
        spoken_text="hello",
        settings=_settings(edge_recalls_skip_queue_depth=2),
        edge_store=edge_store,
        index=idx,
        embedder=emb,
        encode_queue=_DeepQueue(10),
    )
    assert written == []


def test_write_speak_recalls_flag_off(edge_store):
    emb = MockEmbedder()
    idx = _FakeIndex(
        [ScoredAtom(atom_id="a1", score=0.9, atom=_atom(atom_id="a1"))]
    )
    written = write_speak_recalls(
        src_atom_id="a_src",
        spoken_text="hello",
        settings=_settings(durable_edges_enabled=False),
        edge_store=edge_store,
        index=idx,
        embedder=emb,
    )
    assert written == []


def test_write_speak_recalls_never_raises_on_broken_index(edge_store):
    class _Boom:
        def search(self, *a, **k):
            raise RuntimeError("ann down")

    emb = MockEmbedder()
    written = write_speak_recalls(
        src_atom_id="a_src",
        spoken_text="hello",
        settings=_settings(),
        edge_store=edge_store,
        index=_Boom(),
        embedder=emb,
    )
    assert written == []


# ── Promote call sites: speak + wake yes; view/tool no ─────────────────────


def test_promote_speak_writes_recalls(store, edge_store):
    past = _atom(text="alpha memory", kind="speak", atom_id="a_past", t="2026-07-01T00:00:00Z")
    store.put_atom(past)
    idx = _FakeIndex(
        [ScoredAtom(atom_id=past.atom_id, score=0.88, atom=past)]
    )
    emb = MockEmbedder()
    cfg = _settings()
    atom = promote_beat(
        store,
        "m_speak",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": '{"text": "remember alpha"}',
            "ts": "2026-08-05T12:00:00Z",
        },
        settings=cfg,
        edge_store=edge_store,
        embedder=emb,
        index=idx,
    )
    assert atom is not None
    assert atom.kind == "speak"
    edges = edge_store.list_edges_from(atom.atom_id, kinds=[EDGE_RECALLS])
    assert len(edges) == 1
    assert edges[0].dst_atom_id == past.atom_id
    assert edges[0].meta.get("cosine") == pytest.approx(0.88)


def test_promote_wake_writes_recalls(store, edge_store):
    past = _atom(
        text="user said beta",
        kind="observation",
        atom_id="a_obs",
        t="2026-07-02T00:00:00Z",
    )
    store.put_atom(past)
    idx = _FakeIndex(
        [ScoredAtom(atom_id=past.atom_id, score=0.77, atom=past)]
    )
    emb = MockEmbedder()
    atom = promote_wake_observation(
        store,
        "m_wake",
        content="beta again",
        message_id="msg1",
        settings=_settings(),
        edge_store=edge_store,
        embedder=emb,
        index=idx,
    )
    assert atom is not None
    edges = edge_store.list_edges_from(atom.atom_id, kinds=[EDGE_RECALLS])
    assert len(edges) == 1
    assert edges[0].dst_atom_id == past.atom_id


def test_promote_view_does_not_write_recalls(store, edge_store):
    past = _atom(text="visual", kind="observation", atom_id="a_v")
    store.put_atom(past)
    idx = _FakeIndex(
        [ScoredAtom(atom_id=past.atom_id, score=0.99, atom=past)]
    )
    emb = MockEmbedder()
    # view path has no edge/index kwargs by design — and even if we only call
    # promote_view, no recalls helper is invoked.
    atom = promote_view_observation(
        store,
        "m_view",
        media_ids=["att_x"],
        note="looking at something",
        settings=_settings(),
    )
    assert atom is not None
    # No edge store wiring on view; store empty.
    assert edge_store.list_edges_from(atom.atom_id) == []


def test_promote_tool_does_not_write_recalls(store, edge_store):
    past = _atom(text="toolish", kind="speak", atom_id="a_t")
    store.put_atom(past)
    idx = _FakeIndex(
        [ScoredAtom(atom_id=past.atom_id, score=0.99, atom=past)]
    )
    emb = MockEmbedder()
    atom = promote_beat(
        store,
        "m_tool",
        {
            "type": "tool",
            "name": "web_search",
            "ok": True,
            "content": "search results about alpha",
            "ts": "2026-08-05T12:00:00Z",
        },
        settings=_settings(),
        edge_store=edge_store,
        embedder=emb,
        index=idx,
    )
    assert atom is not None
    assert atom.kind == "tool"
    assert edge_store.list_edges_from(atom.atom_id, kinds=[EDGE_RECALLS]) == []


# ── PR1b: deferred path, promote not blocked, wait ceiling, deprecation ────


def test_promote_default_defers_not_inline(store, edge_store):
    """Product default edge_recalls_inline=false → enqueue only; no edges yet."""
    past = _atom(text="alpha memory", kind="speak", atom_id="a_past")
    store.put_atom(past)
    idx = _FakeIndex(
        [ScoredAtom(atom_id=past.atom_id, score=0.88, atom=past)]
    )
    emb = MockEmbedder()
    queued: list[tuple[str, str]] = []

    def _enqueue(*, src_atom_id: str, spoken_text: str) -> None:
        queued.append((src_atom_id, spoken_text))

    atom = promote_beat(
        store,
        "m_defer",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": '{"text": "remember alpha"}',
            "ts": "2026-08-05T12:00:00Z",
        },
        settings=_settings(edge_recalls_inline=False),
        edge_store=edge_store,
        embedder=emb,
        index=idx,
        enqueue_speak_recalls=_enqueue,
    )
    assert atom is not None
    # Promote returned without writing edges (deferred).
    assert edge_store.list_edges_from(atom.atom_id, kinds=[EDGE_RECALLS]) == []
    assert len(queued) == 1
    assert queued[0][0] == atom.atom_id
    assert "remember alpha" in queued[0][1]


def test_promote_not_blocked_by_slow_enqueue_hook(store, edge_store):
    """Promote must not wait for ANN; enqueue hook that is slow still returns."""
    import time as _time

    past = _atom(text="x", kind="speak", atom_id="a_p")
    store.put_atom(past)
    slow_calls = {"n": 0}

    def _slow_enqueue(**kwargs: Any) -> None:
        slow_calls["n"] += 1
        # Even a "slow" enqueue must not do ANN; we only sleep a tiny amount
        # to show promote path itself is enqueue-and-return.
        _time.sleep(0.01)

    t0 = _time.monotonic()
    atom = promote_beat(
        store,
        "m_fast",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": '{"text": "hi"}',
            "ts": "2026-08-05T12:00:00Z",
        },
        settings=_settings(edge_recalls_inline=False),
        edge_store=edge_store,
        embedder=MockEmbedder(),
        index=_FakeIndex([ScoredAtom(atom_id=past.atom_id, score=0.9, atom=past)]),
        enqueue_speak_recalls=_slow_enqueue,
    )
    elapsed_ms = (_time.monotonic() - t0) * 1000.0
    assert atom is not None
    assert slow_calls["n"] == 1
    # Well under any wait ceiling (15s); enqueue path is not ANN.
    assert elapsed_ms < 500
    assert edge_store.list_edges_from(atom.atom_id, kinds=[EDGE_RECALLS]) == []


def test_write_speak_recalls_uses_wait_helper_not_edge_recalls_max_ms(edge_store):
    """edge_recalls_max_ms is deprecated no-op; wait helper / max_ms is authority."""
    emb = MockEmbedder()
    past = _atom(atom_id="a1", text="past")
    idx = _FakeIndex([ScoredAtom(atom_id="a1", score=0.9, atom=past)])
    # Wait off → snappy recalls deadline 0 → skip even if edge_recalls_max_ms huge.
    written = write_speak_recalls(
        src_atom_id="a_src",
        spoken_text="hello",
        settings=_settings(
            semantic_wait_for_select=False,
            edge_recalls_max_ms=500,  # would have been live ceiling pre-PR1b
            edge_recalls_inline=False,
        ),
        edge_store=edge_store,
        index=idx,
        embedder=emb,
    )
    assert written == []
    # Explicit max_ms overrides helper and ignores edge_recalls_max_ms.
    written2 = write_speak_recalls(
        src_atom_id="a_src",
        spoken_text="hello",
        settings=_settings(
            semantic_wait_for_select=False,
            edge_recalls_max_ms=0,
        ),
        edge_store=edge_store,
        index=idx,
        embedder=emb,
        max_ms=5_000,
    )
    assert len(written2) == 1


def test_write_speak_recalls_wait_on_uses_effective_ceiling(edge_store):
    """When wait on, default max_ms comes from semantic_wait_max_ms band."""
    from elyra.memory.config import semantic_ann_deadline_ms

    cfg = _settings(semantic_wait_for_select=True, semantic_wait_max_ms=12_000)
    assert semantic_ann_deadline_ms(cfg, "recalls") == 12_000
    emb = MockEmbedder()
    past = _atom(atom_id="a1")
    written = write_speak_recalls(
        src_atom_id="a_src",
        spoken_text="hello",
        settings=cfg,
        edge_store=edge_store,
        index=_FakeIndex([ScoredAtom(atom_id="a1", score=0.9, atom=past)]),
        embedder=emb,
        # max_ms omitted → wait helper
    )
    assert len(written) == 1


def test_write_speak_recalls_skip_metrics_cold(edge_store):
    skip: dict[str, int] = {}
    written = write_speak_recalls(
        src_atom_id="a_src",
        spoken_text="hello",
        settings=_settings(),
        edge_store=edge_store,
        index=_FakeIndex([ScoredAtom(atom_id="x", score=0.9)]),
        embedder=_ColdEmbedder(),
        skip_metrics=skip,
    )
    assert written == []
    assert skip.get("encoder_cold") == 1


# ── has_channel on encode ready ────────────────────────────────────────────


def test_channel_virtual_id():
    assert channel_virtual_id("a_1", "text") == "a_1:text"
    assert channel_virtual_id("a_1:text", "text") == "a_1:text"


def test_write_has_channel_edges_ready_channels(edge_store):
    cfg = _settings()
    written = write_has_channel_edges(
        edge_store,
        "a_ch",
        ["text", "joint", "bogus", "text"],
        settings=cfg,
    )
    assert {e.meta.get("channel") for e in written} == {"text", "joint"}
    assert all(e.edge_kind == EDGE_HAS_CHANNEL for e in written)
    assert {e.dst_atom_id for e in written} == {"a_ch:text", "a_ch:joint"}
    # idempotent re-write same channels
    again = write_has_channel_edges(
        edge_store, "a_ch", ["text", "joint"], settings=cfg
    )
    assert len(again) == 2
    assert edge_store.count_edges_for_atom("a_ch", kind=EDGE_HAS_CHANNEL) == 2


def test_write_has_channel_flag_off(edge_store):
    assert (
        write_has_channel_edges(
            edge_store,
            "a_ch",
            ["text"],
            settings=_settings(durable_edges_enabled=False),
        )
        == []
    )


def test_encode_drain_writes_has_channel_on_ready(store, edge_store):
    atom = store.put_atom(
        Atom(
            atom_id=new_atom_id(),
            t_start=utc_now_iso(),
            kind="observation",
            content_text="encode me please",
            content_ref="inline",
            moment_id="m_enc",
            embedding_status="pending",
        )
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    idx = _FakeIndex()
    emb = MockEmbedder()
    stats = q.drain(
        store,
        emb,
        index=idx,
        max_items=2,
        settings=_settings(),
        edge_store=edge_store,
    )
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    edges = edge_store.list_edges_from(atom.atom_id, kinds=[EDGE_HAS_CHANNEL])
    assert edges, "expected has_channel edges after ready"
    channels = {e.meta.get("channel") for e in edges}
    # Mock text encode typically yields text + joint (single-modality joint).
    assert channels <= {"text", "image", "audio", "video", "joint"}
    assert "text" in channels or "joint" in channels
    for e in edges:
        assert e.dst_atom_id == channel_virtual_id(atom.atom_id, e.meta["channel"])


def test_encode_drain_no_has_channel_when_flag_off(store, edge_store):
    atom = store.put_atom(
        Atom(
            atom_id=new_atom_id(),
            t_start=utc_now_iso(),
            kind="observation",
            content_text="no edges please",
            content_ref="inline",
            moment_id="m_enc2",
            embedding_status="pending",
        )
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    q.drain(
        store,
        MockEmbedder(),
        index=_FakeIndex(),
        max_items=2,
        settings=_settings(durable_edges_enabled=False),
        edge_store=edge_store,
    )
    assert edge_store.list_edges_from(atom.atom_id) == []
