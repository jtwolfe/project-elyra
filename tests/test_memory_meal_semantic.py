"""Phase 2 meal semantic channel: budget v2, select_semantic, dedup, timeout."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any, Sequence

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.embed.mock import MockEmbedder, mock_vector
from elyra.memory.embed.types import EMBED_DIM, EmbeddingSet
from elyra.memory.index import MemoryEmbeddingIndex, ScoredAtom
from elyra.memory.meal import (
    SEMANTIC_OMIT_EMPTY_SEED,
    SEMANTIC_OMIT_ENCODER,
    SEMANTIC_OMIT_MIN_SCORE,
    SEMANTIC_OMIT_NO_INDEX,
    SEMANTIC_OMIT_TIMEOUT,
    compose_meal,
    compose_outer_messages,
    select_semantic,
)
from elyra.memory.store import open_memory_store
from elyra.memory.tokens import (
    estimate_tokens,
    split_memory_budget,
    split_memory_budget_v2,
)
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
    parent_atom_id: str | None = None,
    embedding_status: str = "none",
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
        embedding_status=embedding_status,  # type: ignore[arg-type]
        **kwargs,
    )


def _emb(atom_id: str, seed: str) -> EmbeddingSet:
    return EmbeddingSet(
        atom_id=atom_id,
        emb_text=mock_vector(f"text:{seed}", dim=EMBED_DIM),
        emb_joint=mock_vector(f"joint:{seed}", dim=EMBED_DIM),
        model_id="mock",
        encoded_at="2026-07-28T12:00:00Z",
    )


# ---------------------------------------------------------------------------
# split_memory_budget_v2
# ---------------------------------------------------------------------------


def test_split_v2_phase1_parity_when_semantic_off():
    kwargs = dict(
        budget_tokens=1000,
        system_text="a" * 40,
        orient_text="b" * 40,
        episodic_fraction=0.20,
    )
    fixed1, epi1, temp1 = split_memory_budget(**kwargs)
    fixed2, sem2, epi2, temp2 = split_memory_budget_v2(
        semantic_enabled=False, **kwargs
    )
    assert fixed2 == fixed1
    assert sem2 == 0
    assert epi2 == epi1
    assert temp2 == temp1
    assert epi2 + temp2 == 1000 - fixed2


def test_split_v2_default_shares_sum_to_remaining():
    fixed, sem, epi, temp = split_memory_budget_v2(
        10_000,
        system_text="",
        orient_text="",
        semantic_enabled=True,
        semantic_fraction=0.12,
        episodic_fraction_with_semantic=0.18,
        temporal_min_fraction=0.55,
    )
    assert fixed == 0
    assert sem + epi + temp == 10_000
    assert sem == int(10_000 * 0.12)
    assert epi == int(10_000 * 0.18)
    assert temp == 10_000 - sem - epi
    # Temporal above floor.
    assert temp >= int(10_000 * 0.55)


def test_split_v2_temporal_floor_cuts_semantic_then_episodic():
    """When semantic+episodic fractions leave temporal below floor, cut supports."""
    # remaining=1000; sem 0.30 + epi 0.30 = 0.60 → temporal 0.40 < floor 0.55
    fixed, sem, epi, temp = split_memory_budget_v2(
        1000,
        semantic_enabled=True,
        semantic_fraction=0.30,
        episodic_fraction_with_semantic=0.30,
        temporal_min_fraction=0.55,
    )
    assert fixed == 0
    assert sem + epi + temp == 1000
    floor = int(1000 * 0.55)
    assert temp >= floor
    # Semantic cut first: start 300+300=600, need temp 550 → deficit 150 from sem
    assert sem == 300 - 150  # 150
    assert epi == 300
    assert temp == 550


def test_split_v2_impossible_floor_gives_all_to_temporal():
    """If t_min is 1.0, supports go to zero."""
    _f, sem, epi, temp = split_memory_budget_v2(
        500,
        semantic_enabled=True,
        semantic_fraction=0.2,
        episodic_fraction_with_semantic=0.2,
        temporal_min_fraction=1.0,
    )
    assert sem == 0
    assert epi == 0
    assert temp == 500


# ---------------------------------------------------------------------------
# select_semantic helpers / fakes
# ---------------------------------------------------------------------------


class _SlowIndex:
    """Fake index that sleeps past the hard select budget."""

    def __init__(self, sleep_s: float = 0.08, hits: list[ScoredAtom] | None = None):
        self.sleep_s = sleep_s
        self.hits = hits or []

    def search(self, query: Sequence[float], **kwargs: Any) -> list[ScoredAtom]:
        del query, kwargs
        time.sleep(self.sleep_s)
        return list(self.hits)

    def health(self) -> dict[str, Any]:
        return {"ok": True}


class _ColdEmbedder:
    """Healthy-looking embedder that is not loaded (KD12)."""

    is_loaded = False

    def health(self) -> dict[str, Any]:
        return {"ok": True, "backend": "mock", "device": "cpu"}

    def encode_text(self, text: str) -> list[float]:
        raise AssertionError("cold embedder must not encode")


class _FixedHitIndex:
    """Return predetermined scored hits regardless of query."""

    def __init__(self, hits: list[ScoredAtom]):
        self.hits = hits

    def search(self, query: Sequence[float], **kwargs: Any) -> list[ScoredAtom]:
        del query, kwargs
        return list(self.hits)

    def health(self) -> dict[str, Any]:
        return {"ok": True}


# ---------------------------------------------------------------------------
# select_semantic behaviours
# ---------------------------------------------------------------------------


def test_select_semantic_no_index(store):
    emb = MockEmbedder()
    items, reason = select_semantic(
        store,
        index=None,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="hello seed about cats")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
    )
    assert items == []
    assert reason == SEMANTIC_OMIT_NO_INDEX


def test_select_semantic_encoder_not_warm(store):
    idx = MemoryEmbeddingIndex(store)
    items, reason = select_semantic(
        store,
        index=idx,
        embedder=None,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="seed body")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
    )
    assert items == []
    assert reason == SEMANTIC_OMIT_ENCODER

    items2, reason2 = select_semantic(
        store,
        index=idx,
        embedder=_ColdEmbedder(),
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="seed body")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
    )
    assert items2 == []
    assert reason2 == SEMANTIC_OMIT_ENCODER


def test_select_semantic_empty_seed(store):
    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store)
    items, reason = select_semantic(
        store,
        index=idx,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", kind="tool", text="tool noise")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
    )
    assert items == []
    assert reason == SEMANTIC_OMIT_EMPTY_SEED


def test_select_semantic_timeout_on_slow_index(store):
    emb = MockEmbedder()
    slow = _SlowIndex(sleep_s=0.08)
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=20,
        encode_query_max_ms=15,
    )
    items, reason = select_semantic(
        store,
        index=slow,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="seed about gardens")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
    )
    assert items == []
    assert reason == SEMANTIC_OMIT_TIMEOUT


def test_select_semantic_min_score_filters_all(store):
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="related memory of gardens",
        moment_id="m_past",
        atom_id="a_past",
        embedding_status="ready",
    )
    store.put_atom(past)
    hit = ScoredAtom(atom_id="a_past", score=0.1, channel="joint", atom=past)
    idx = _FixedHitIndex([hit])
    emb = MockEmbedder()
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_min_score=0.5,
        semantic_select_max_ms=500,
    )
    items, reason = select_semantic(
        store,
        index=idx,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="talking about gardens")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
    )
    assert items == []
    assert reason == SEMANTIC_OMIT_MIN_SCORE


def test_select_semantic_packs_hits_with_label(store):
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="cats on the roof",
        moment_id="m_past",
        atom_id="a_past",
        embedding_status="ready",
    )
    store.put_atom(past)
    hit = ScoredAtom(atom_id="a_past", score=0.83, channel="joint", atom=past)
    idx = _FixedHitIndex([hit])
    emb = MockEmbedder()
    cfg = MemorySettings(semantic_enabled=True, semantic_select_max_ms=500)
    items, reason = select_semantic(
        store,
        index=idx,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="thinking of cats")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
    )
    assert reason is None
    assert len(items) == 1
    assert items[0].channel == "semantic"
    assert items[0].atom_id == "a_past"
    assert items[0].label.startswith("semantic")
    assert "0.83" in items[0].label
    assert "cats on the roof" in items[0].content


def test_select_semantic_parcel_maps_to_parent(store):
    parent = _atom(
        t="2026-07-27T09:00:00Z",
        text="parent long story part one",
        moment_id="m_past",
        atom_id="a_parent",
        embedding_status="ready",
    )
    parcel = _atom(
        t="2026-07-27T09:00:01Z",
        kind="parcel",
        text="parcel slice two",
        moment_id="m_past",
        atom_id="a_parcel",
        parent_atom_id="a_parent",
        embedding_status="ready",
    )
    store.put_atom(parent)
    store.put_atom(parcel)
    hit = ScoredAtom(
        atom_id="a_parcel", score=0.91, channel="joint", atom=parcel
    )
    idx = _FixedHitIndex([hit])
    emb = MockEmbedder()
    cfg = MemorySettings(semantic_enabled=True, semantic_select_max_ms=500)
    items, reason = select_semantic(
        store,
        index=idx,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="long story recall")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
    )
    assert reason is None
    assert len(items) == 1
    assert items[0].atom_id == "a_parent"
    assert "parcel→parent" in items[0].label
    assert "parent long story" in items[0].content
    assert items[0].meta.get("via_parcel") is True
    assert items[0].meta.get("hit_atom_id") == "a_parcel"


def test_select_semantic_dedup_exclude_ids(store):
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="already in temporal",
        moment_id="m_past",
        atom_id="a_dup",
        embedding_status="ready",
    )
    store.put_atom(past)
    hit = ScoredAtom(atom_id="a_dup", score=0.9, channel="joint", atom=past)
    idx = _FixedHitIndex([hit])
    emb = MockEmbedder()
    cfg = MemorySettings(semantic_enabled=True, semantic_select_max_ms=500)
    items, reason = select_semantic(
        store,
        index=idx,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="seed")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
        exclude_atom_ids={"a_dup"},
    )
    assert reason is None
    assert items == []


# ---------------------------------------------------------------------------
# compose_meal integration of semantic channel
# ---------------------------------------------------------------------------


def test_compose_meal_phase1_parity_semantic_flag_off(store):
    open_id = "m_openmoment01"
    store.put_atom(
        _atom(
            t="2026-07-28T14:50:00Z",
            text="wake hi",
            moment_id=open_id,
        )
    )
    pkg_off = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text="ORIENT",
        now=datetime(2026, 7, 28, 15, 0, tzinfo=UTC),
        settings=MemorySettings(semantic_enabled=False),
    )
    assert "semantic" not in pkg_off.channels_present
    assert pkg_off.semantic_omitted_reason is None
    assert any(i.channel == "temporal" for i in pkg_off.items)


def test_compose_meal_semantic_order_and_channel(store):
    open_id = "m_open"
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    store.put_atom(
        _atom(
            t="2026-07-28T14:50:00Z",
            text="open moment seed about bees",
            moment_id=open_id,
        )
    )
    # Prior moment inside episodic horizon (for episodic channel presence).
    store.put_atom(
        _atom(
            t="2026-07-28T10:00:00Z",
            kind="speak",
            text="prior moment chat",
            moment_id="m_prior",
        )
    )
    # Semantic-only candidate: outside default episodic 24h horizon, inside
    # semantic 168h horizon so it is not deduped as episodic (KD11).
    past = _atom(
        t="2026-07-25T10:00:00Z",
        text="semantic bee hive memory",
        moment_id="m_sem",
        atom_id="a_sem",
        embedding_status="ready",
    )
    store.put_atom(past)

    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store)
    # Upsert joint vector from same mock space as encode_text seed will use.
    # Use a highly related seed so cosine is high enough.
    seed_vec = emb.encode_text("open moment seed about bees")
    idx.upsert(
        EmbeddingSet(
            atom_id="a_sem",
            emb_text=tuple(seed_vec),
            emb_joint=tuple(seed_vec),
            model_id="mock",
            encoded_at=to_iso_z(now),
        )
    )

    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=500,
        semantic_min_score=0.0,
        episodic_horizon_hours=24.0,
        semantic_horizon_hours=168.0,
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
    channels = [i.channel for i in pkg.items]
    # KD10: episodic before semantic before temporal when all present.
    if "episodic" in channels and "semantic" in channels and "temporal" in channels:
        assert channels.index("episodic") < channels.index("semantic")
        assert channels.index("semantic") < channels.index("temporal")
    assert "semantic" in pkg.channels_present
    assert pkg.semantic_omitted_reason is None
    sem_items = [i for i in pkg.items if i.channel == "semantic"]
    assert any(i.atom_id == "a_sem" for i in sem_items)

    msgs = compose_outer_messages(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text="ORIENT",
        now=now,
        settings=cfg,
        package=pkg,
    )
    joined = "\n".join(str(m.get("content")) for m in msgs)
    assert "[context:semantic" in joined
    assert msgs[0]["content"] == "SYS"
    assert msgs[-1]["content"] == "ORIENT"


def test_compose_meal_dedup_temporal_wins_over_semantic(store):
    open_id = "m_open"
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    open_atom = _atom(
        t="2026-07-28T14:50:00Z",
        text="live atom also in index",
        moment_id=open_id,
        atom_id="a_open",
    )
    store.put_atom(open_atom)
    # Hit claims the open-moment atom_id — must be dropped by exclude.
    hit = ScoredAtom(
        atom_id="a_open", score=0.99, channel="joint", atom=open_atom
    )
    idx = _FixedHitIndex([hit])
    emb = MockEmbedder()
    cfg = MemorySettings(semantic_enabled=True, semantic_select_max_ms=500)
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        now=now,
        settings=cfg,
        index=idx,
        embedder=emb,
    )
    sem_ids = [i.atom_id for i in pkg.items if i.channel == "semantic"]
    assert "a_open" not in sem_ids


def test_compose_meal_semantic_timeout_still_returns_meal(store):
    open_id = "m_open"
    store.put_atom(
        _atom(
            t="2026-07-28T14:50:00Z",
            text="open seed",
            moment_id=open_id,
        )
    )
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=15,
        encode_query_max_ms=10,
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        now=datetime(2026, 7, 28, 15, 0, tzinfo=UTC),
        settings=cfg,
        index=_SlowIndex(sleep_s=0.08),
        embedder=MockEmbedder(),
    )
    assert pkg.semantic_omitted_reason == SEMANTIC_OMIT_TIMEOUT
    assert "semantic" not in pkg.channels_present
    assert any(i.channel == "temporal" for i in pkg.items)


def test_estimate_tokens_unchanged():
    assert estimate_tokens("abcd") == 1
