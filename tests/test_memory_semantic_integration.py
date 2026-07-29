"""Phase 2 semantic meal integration: flag parity + mock index end-to-end."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.embed.mock import MockEmbedder
from elyra.memory.embed.types import EmbeddingSet
from elyra.memory.index import MemoryEmbeddingIndex, NullEmbeddingIndex
from elyra.memory.meal import compose_meal, compose_outer_messages
from elyra.memory.store import open_memory_store
from elyra.memory.tokens import split_memory_budget, split_memory_budget_v2
from elyra.memory.types import Atom, new_atom_id, to_iso_z


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
    embedding_status: str = "none",
) -> Atom:
    return Atom(
        atom_id=atom_id or new_atom_id(),
        t_start=t,
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=moment_id,
        embedding_status=embedding_status,  # type: ignore[arg-type]
    )


def _seed_open_and_past(store, *, open_id: str = "m_open"):
    store.put_atom(
        _atom(
            t="2026-07-28T14:50:00Z",
            text="open discussion of sailboats",
            moment_id=open_id,
        )
    )
    # Outside episodic 24h raw horizon; inside semantic 168h.
    past = _atom(
        t="2026-07-25T08:00:00Z",
        text="earlier sailboat race memory",
        moment_id="m_past_sail",
        atom_id="a_sail",
        embedding_status="ready",
    )
    store.put_atom(past)
    return past


def test_flags_off_identical_budget_and_channels(store):
    """semantic_enabled=false → Phase 1 budget math + no semantic channel."""
    open_id = "m_open"
    _seed_open_and_past(store, open_id=open_id)
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    system = "SYSTEM PROMPT"
    orient = "ORIENT SLICE"

    cfg_off = MemorySettings(
        semantic_enabled=False,
        embed_enabled=False,
        episodic_fraction=0.20,
    )
    fixed1, epi1, temp1 = split_memory_budget(
        50_000,
        system_text=system,
        orient_text=orient,
        episodic_fraction=0.20,
    )
    fixed2, sem2, epi2, temp2 = split_memory_budget_v2(
        50_000,
        system_text=system,
        orient_text=orient,
        semantic_enabled=False,
        episodic_fraction=0.20,
    )
    assert (fixed2, sem2, epi2, temp2) == (fixed1, 0, epi1, temp1)

    # Even if caller passes index/embedder, flag off must ignore them.
    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store)
    idx.upsert(
        EmbeddingSet(
            atom_id="a_sail",
            emb_joint=tuple(emb.encode_text("earlier sailboat race memory")),
            emb_text=tuple(emb.encode_text("earlier sailboat race memory")),
            model_id="mock",
            encoded_at=to_iso_z(now),
        )
    )

    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text=system,
        orient_text=orient,
        now=now,
        settings=cfg_off,
        index=idx,
        embedder=emb,
    )
    assert "semantic" not in pkg.channels_present
    assert pkg.semantic_omitted_reason is None
    assert any(i.channel == "temporal" for i in pkg.items)

    pkg2 = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text=system,
        orient_text=orient,
        now=now,
        settings=cfg_off,
        index=None,
        embedder=None,
    )
    # Same channels and item count structure (flag-off ignores index).
    assert pkg.channels_present == pkg2.channels_present
    assert len(pkg.items) == len(pkg2.items)


def test_semantic_on_with_injected_memory_index(store):
    """semantic_enabled + mock embedder + MemoryEmbeddingIndex → semantic items."""
    open_id = "m_open"
    _seed_open_and_past(store, open_id=open_id)
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store)
    # Align corpus vector with query encode of open seed for high cosine.
    q = emb.encode_text("open discussion of sailboats")
    idx.upsert(
        EmbeddingSet(
            atom_id="a_sail",
            emb_joint=tuple(q),
            emb_text=tuple(q),
            model_id="mock",
            encoded_at=to_iso_z(now),
        )
    )

    cfg = MemorySettings(
        semantic_enabled=True,
        embed_enabled=True,
        embed_backend="mock",
        semantic_select_max_ms=500,
        encode_query_max_ms=200,
        semantic_horizon_hours=168.0,
        semantic_min_score=0.0,
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text="ORIENT",
        now=now,
        settings=cfg,
        index=idx,
        embedder=emb,
    )
    assert "semantic" in pkg.channels_present
    assert pkg.semantic_omitted_reason is None
    sem = [i for i in pkg.items if i.channel == "semantic"]
    assert any(i.atom_id == "a_sail" for i in sem)

    # Order: any episodic block before semantic before temporal (KD10).
    order = [i.channel for i in pkg.items]
    if "semantic" in order and "temporal" in order:
        assert order.index("semantic") < order.index("temporal")

    msgs = compose_outer_messages(
        store,
        open_moment_id=open_id,
        package=pkg,
        system_text="SYS",
        orient_text="ORIENT",
        settings=cfg,
    )
    body = "\n".join(str(m.get("content")) for m in msgs)
    assert "[context:semantic" in body
    assert "sailboat" in body.lower() or "a_sail" in body or "earlier" in body


def test_jsonl_null_index_semantic_empty(store):
    """JSONL production path: NullEmbeddingIndex → empty semantic, no crash."""
    open_id = "m_open"
    _seed_open_and_past(store, open_id=open_id)
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=100,
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        now=now,
        settings=cfg,
        index=NullEmbeddingIndex(),
        embedder=MockEmbedder(),
    )
    assert "semantic" not in pkg.channels_present
    # PR-R2 / KD-R6: zero hits is an honest omit reason (not silent None).
    assert pkg.semantic_omitted_reason == "no_hits"
    assert pkg.semantic_select_meta is not None
    assert pkg.semantic_select_meta.get("raw_hits") == 0
    assert any(i.channel == "temporal" for i in pkg.items)


def test_semantic_on_without_embedder_omits_encoder(store):
    open_id = "m_open"
    _seed_open_and_past(store, open_id=open_id)
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    cfg = MemorySettings(semantic_enabled=True, semantic_select_max_ms=100)
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        now=now,
        settings=cfg,
        index=MemoryEmbeddingIndex(store),
        embedder=None,
    )
    assert pkg.semantic_omitted_reason == "encoder"
    assert "semantic" not in pkg.channels_present
    assert any(i.channel == "temporal" for i in pkg.items)
