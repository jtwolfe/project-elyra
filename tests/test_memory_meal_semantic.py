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
from elyra.memory.inspect import meal_package_to_inspect
from elyra.memory.meal import (
    SEMANTIC_OMIT_DEDUPED,
    SEMANTIC_OMIT_EMPTY_SEED,
    SEMANTIC_OMIT_ENCODER,
    SEMANTIC_OMIT_MIN_SCORE,
    SEMANTIC_OMIT_NO_HITS,
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


class _FilteringHitIndex:
    """Predetermined hits that honor exclude_atom_ids / exclude_moment_id.

    Models product MemoryEmbeddingIndex / Lance filter behaviour so pack-side
    and probe-side dedup paths can be tested without Lance.
    """

    def __init__(self, hits: list[ScoredAtom]):
        self.hits = hits
        self.last_kwargs: dict[str, Any] = {}
        self.search_calls = 0

    def search(self, query: Sequence[float], **kwargs: Any) -> list[ScoredAtom]:
        del query
        self.search_calls += 1
        self.last_kwargs = dict(kwargs)
        exclude = set(kwargs.get("exclude_atom_ids") or ())
        exclude_moment = kwargs.get("exclude_moment_id")
        out: list[ScoredAtom] = []
        for hit in self.hits:
            if hit.atom_id in exclude:
                continue
            atom = hit.atom
            if exclude_moment and atom is not None and atom.moment_id == exclude_moment:
                continue
            out.append(hit)
        return out

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "vectors_by_channel": {
                "text": 1,
                "joint": 1,
                "image": 0,
                "audio": 0,
                "video": 0,
            },
            "joint_repair_remaining": 0,
        }


# ---------------------------------------------------------------------------
# select_semantic behaviours
# ---------------------------------------------------------------------------


def test_select_semantic_no_index(store):
    emb = MockEmbedder()
    items, reason, meta = select_semantic(
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
    assert meta is not None
    assert "elapsed_ms" in meta


def test_select_semantic_encoder_not_warm(store):
    idx = MemoryEmbeddingIndex(store)
    items, reason, meta = select_semantic(
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
    assert meta is not None

    items2, reason2, meta2 = select_semantic(
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
    assert meta2 is not None


def test_select_semantic_empty_seed(store):
    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store)
    items, reason, meta = select_semantic(
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
    assert meta is not None


def test_select_semantic_timeout_on_slow_index(store):
    emb = MockEmbedder()
    slow = _SlowIndex(sleep_s=0.08)
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=20,
        encode_query_max_ms=15,
        semantic_wait_for_select=False,
    )
    items, reason, meta = select_semantic(
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
    assert meta is not None
    assert meta.get("wait") is False


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
    items, reason, meta = select_semantic(
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
    assert meta is not None
    assert meta["raw_hits"] == 1
    assert meta["below_min"] == 1
    assert meta["packed"] == 0
    assert "channel" in meta
    assert "channel_reason" in meta


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
    items, reason, meta = select_semantic(
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
    assert meta is not None
    assert meta["packed"] == 1
    assert meta["raw_hits"] == 1
    assert meta["deduped"] == 0
    assert "channel" in meta
    assert "channel_reason" in meta
    assert "elapsed_ms" in meta


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
    items, reason, meta = select_semantic(
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
    assert meta is not None
    assert meta["packed"] == 1


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
    items, reason, meta = select_semantic(
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
    assert reason == SEMANTIC_OMIT_DEDUPED
    assert items == []
    assert meta is not None
    assert meta["raw_hits"] == 1
    assert meta["deduped"] == 1
    assert meta["packed"] == 0


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
    assert pkg_off.semantic_select_meta is None
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
    assert pkg.semantic_select_meta is not None
    assert pkg.semantic_select_meta["packed"] >= 1
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
    assert pkg.semantic_omitted_reason == SEMANTIC_OMIT_DEDUPED


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
        semantic_wait_for_select=False,
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
    # Timeout still attaches best-effort meta when available.
    assert pkg.semantic_select_meta is not None


def test_estimate_tokens_unchanged():
    assert estimate_tokens("abcd") == 1


class _ChannelRecordingIndex:
    """Records the concrete channel passed to search (KD-R16)."""

    def __init__(
        self,
        hits: list[ScoredAtom],
        *,
        vectors_by_channel: dict[str, int] | None = None,
        joint_repair_remaining: int = 0,
    ):
        self.hits = hits
        self.last_channel: str | None = None
        self.vectors_by_channel = vectors_by_channel or {
            "text": 1,
            "joint": 0,
            "image": 0,
            "audio": 0,
            "video": 0,
        }
        self.joint_repair_remaining = joint_repair_remaining

    def search(self, query: Sequence[float], **kwargs: Any) -> list[ScoredAtom]:
        del query
        self.last_channel = kwargs.get("channel")
        return list(self.hits)

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "vectors_by_channel": dict(self.vectors_by_channel),
            "joint_repair_remaining": self.joint_repair_remaining,
        }


def test_select_semantic_auto_repair_pending_uses_text(store):
    """KD-R16: auto while joint_repair_remaining > 0 resolves to text."""
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="cats on the roof",
        moment_id="m_past",
        atom_id="a_past",
        embedding_status="ready",
    )
    store.put_atom(past)
    hit = ScoredAtom(atom_id="a_past", score=0.9, channel="text", atom=past)
    idx = _ChannelRecordingIndex(
        [hit],
        vectors_by_channel={"text": 1, "joint": 0, "image": 0, "audio": 0, "video": 0},
        joint_repair_remaining=3,
    )
    emb = MockEmbedder()
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=500,
        semantic_search_channel="auto",
    )
    items, reason, meta = select_semantic(
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
    assert idx.last_channel == "text"
    assert meta is not None
    assert meta["channel"] == "text"
    assert meta["channel_reason"] == "auto_text_repair_pending"
    assert meta["joint_repair_remaining"] == 3


def test_select_semantic_auto_post_repair_uses_joint(store):
    """KD-R16: auto after repair complete with joint coverage → joint."""
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="cats on the roof",
        moment_id="m_past",
        atom_id="a_past",
        embedding_status="ready",
    )
    store.put_atom(past)
    hit = ScoredAtom(atom_id="a_past", score=0.9, channel="joint", atom=past)
    idx = _ChannelRecordingIndex(
        [hit],
        vectors_by_channel={"text": 1, "joint": 1, "image": 0, "audio": 0, "video": 0},
        joint_repair_remaining=0,
    )
    emb = MockEmbedder()
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=500,
        semantic_search_channel="auto",
    )
    items, reason, meta = select_semantic(
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
    assert idx.last_channel == "joint"
    assert meta is not None
    assert meta["channel"] == "joint"
    assert meta["channel_reason"] == "auto_joint"
    assert meta["joint_repair_remaining"] == 0


def test_select_semantic_no_hits(store):
    """Empty search results → no_hits (not silent None)."""
    idx = _FixedHitIndex([])
    emb = MockEmbedder()
    cfg = MemorySettings(semantic_enabled=True, semantic_select_max_ms=500)
    items, reason, meta = select_semantic(
        store,
        index=idx,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="seed about nothing")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
    )
    assert items == []
    assert reason == SEMANTIC_OMIT_NO_HITS
    assert meta is not None
    assert meta["raw_hits"] == 0
    assert meta["packed"] == 0
    assert meta["deduped"] == 0
    assert "channel" in meta
    assert "channel_reason" in meta


def test_select_semantic_no_hits_empty_channel_auto_empty(store):
    """auto_empty resolve with zero hits still reports no_hits + meta.channel."""
    idx = _ChannelRecordingIndex(
        [],
        vectors_by_channel={
            "text": 0,
            "joint": 0,
            "image": 0,
            "audio": 0,
            "video": 0,
        },
        joint_repair_remaining=0,
    )
    emb = MockEmbedder()
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=500,
        semantic_search_channel="auto",
    )
    items, reason, meta = select_semantic(
        store,
        index=idx,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="lonely seed")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
    )
    assert items == []
    assert reason == SEMANTIC_OMIT_NO_HITS
    assert meta["channel"] == "joint"
    assert meta["channel_reason"] == "auto_empty"
    assert idx.last_channel == "joint"


def test_compose_meal_pins_semantic_select_meta(store):
    """MealPackage carries semantic_select_meta; inspect threads it."""
    open_id = "m_open"
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    store.put_atom(
        _atom(
            t="2026-07-28T14:50:00Z",
            text="open seed",
            moment_id=open_id,
        )
    )
    # Empty index → no_hits, but meta still pinned.
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=500,
        semantic_search_channel="auto",
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        now=now,
        settings=cfg,
        index=_FixedHitIndex([]),
        embedder=MockEmbedder(),
    )
    assert pkg.semantic_omitted_reason == SEMANTIC_OMIT_NO_HITS
    assert pkg.semantic_select_meta is not None
    assert pkg.semantic_select_meta["raw_hits"] == 0
    assert "channel" in pkg.semantic_select_meta

    dto = meal_package_to_inspect(pkg)
    assert dto["semantic_omitted_reason"] == SEMANTIC_OMIT_NO_HITS
    assert dto["semantic_select_meta"] == pkg.semantic_select_meta


def test_compose_meal_deduped_reason_and_meta(store):
    """All hits already in temporal → deduped omit + meta counters."""
    open_id = "m_open"
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    open_atom = _atom(
        t="2026-07-28T14:50:00Z",
        text="live atom also in index",
        moment_id=open_id,
        atom_id="a_open",
    )
    store.put_atom(open_atom)
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
    assert pkg.semantic_omitted_reason == SEMANTIC_OMIT_DEDUPED
    assert "semantic" not in pkg.channels_present
    assert pkg.semantic_select_meta is not None
    assert pkg.semantic_select_meta["deduped"] >= 1
    assert pkg.semantic_select_meta["packed"] == 0


def test_select_semantic_deduped_real_memory_index(store):
    """Product MemoryEmbeddingIndex filters exclude → probe classifies deduped.

    Regression for KD-R6: when the only ANN neighbours are already in
    temporal/episodic, reason must be deduped (not no_hits).
    """
    open_id = "m_open"
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    open_atom = _atom(
        t="2026-07-28T14:50:00Z",
        text="open seed about bees",
        moment_id=open_id,
        atom_id="a_open",
        embedding_status="ready",
    )
    store.put_atom(open_atom)
    # Prior-moment atom that will land in episodic and be excluded from semantic.
    past = _atom(
        t="2026-07-28T10:00:00Z",
        kind="speak",
        text="prior moment chat about bees",
        moment_id="m_prior",
        atom_id="a_prior",
        embedding_status="ready",
    )
    store.put_atom(past)

    emb = MockEmbedder()
    idx = MemoryEmbeddingIndex(store)
    seed_vec = emb.encode_text("open seed about bees")
    for aid, seed in (("a_open", "open seed about bees"), ("a_prior", "prior moment chat about bees")):
        vec = emb.encode_text(seed)
        idx.upsert(
            EmbeddingSet(
                atom_id=aid,
                emb_text=tuple(vec),
                emb_joint=tuple(vec),
                model_id="mock",
                encoded_at=to_iso_z(now),
            )
        )
    # Also index open with seed-aligned vector so ANN would rank it.
    idx.upsert(
        EmbeddingSet(
            atom_id="a_open",
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
        semantic_search_channel="joint",
        episodic_horizon_hours=24.0,
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        now=now,
        settings=cfg,
        index=idx,
        embedder=emb,
    )
    assert "semantic" not in pkg.channels_present
    assert pkg.semantic_omitted_reason == SEMANTIC_OMIT_DEDUPED
    assert pkg.semantic_select_meta is not None
    assert pkg.semantic_select_meta.get("raw_hits") == 0
    assert pkg.semantic_select_meta.get("deduped", 0) >= 1
    assert pkg.semantic_select_meta.get("dedupe_probe") is True
    assert pkg.semantic_select_meta.get("packed") == 0


def test_select_semantic_deduped_filtering_index_probe(store):
    """Filtering index (honors exclude) → primary empty, probe → deduped."""
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="already packed elsewhere",
        moment_id="m_past",
        atom_id="a_dup",
        embedding_status="ready",
    )
    store.put_atom(past)
    hit = ScoredAtom(atom_id="a_dup", score=0.95, channel="joint", atom=past)
    idx = _FilteringHitIndex([hit])
    emb = MockEmbedder()
    cfg = MemorySettings(semantic_enabled=True, semantic_select_max_ms=500)
    items, reason, meta = select_semantic(
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
    assert items == []
    assert reason == SEMANTIC_OMIT_DEDUPED
    assert meta is not None
    assert meta["raw_hits"] == 0  # primary search filtered
    assert meta["deduped"] >= 1
    assert meta.get("dedupe_probe") is True
    assert idx.search_calls >= 2  # primary + probe


def test_select_semantic_mixed_min_score_and_deduped(store):
    """Some below min_score, some pack-side deduped → deduped (not min_score)."""
    keep = _atom(
        t="2026-07-27T10:00:00Z",
        text="already in temporal",
        moment_id="m_past",
        atom_id="a_dup",
        embedding_status="ready",
    )
    weak = _atom(
        t="2026-07-27T09:00:00Z",
        text="weak unrelated",
        moment_id="m_weak",
        atom_id="a_weak",
        embedding_status="ready",
    )
    store.put_atom(keep)
    store.put_atom(weak)
    hits = [
        ScoredAtom(atom_id="a_weak", score=0.1, channel="joint", atom=weak),
        ScoredAtom(atom_id="a_dup", score=0.9, channel="joint", atom=keep),
    ]
    idx = _FixedHitIndex(hits)
    emb = MockEmbedder()
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_min_score=0.5,
        semantic_select_max_ms=500,
    )
    items, reason, meta = select_semantic(
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
    assert items == []
    assert reason == SEMANTIC_OMIT_DEDUPED
    assert meta is not None
    assert meta["raw_hits"] == 2
    assert meta["below_min"] == 1
    assert meta["deduped"] == 1
    assert meta["packed"] == 0


def test_select_semantic_packed_with_deduped_counter(store):
    """Packed ≥1 with some pack-side dups → reason None; meta both counters."""
    keep = _atom(
        t="2026-07-27T10:00:00Z",
        text="fresh semantic hit",
        moment_id="m_keep",
        atom_id="a_keep",
        embedding_status="ready",
    )
    dup = _atom(
        t="2026-07-27T09:00:00Z",
        text="already temporal",
        moment_id="m_dup",
        atom_id="a_dup",
        embedding_status="ready",
    )
    store.put_atom(keep)
    store.put_atom(dup)
    hits = [
        ScoredAtom(atom_id="a_dup", score=0.99, channel="joint", atom=dup),
        ScoredAtom(atom_id="a_keep", score=0.8, channel="joint", atom=keep),
    ]
    idx = _FixedHitIndex(hits)
    emb = MockEmbedder()
    cfg = MemorySettings(semantic_enabled=True, semantic_select_max_ms=500)
    items, reason, meta = select_semantic(
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
    assert len(items) == 1
    assert items[0].atom_id == "a_keep"
    assert meta is not None
    assert meta["packed"] == 1
    assert meta["deduped"] == 1
    assert meta["raw_hits"] == 2


def test_select_semantic_zero_cap_still_has_meta(store):
    """cap_tokens=0 leaves breadcrumb meta (budget floor cut semantic share)."""
    items, reason, meta = select_semantic(
        store,
        index=_FixedHitIndex([]),
        embedder=MockEmbedder(),
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="seed")
        ],
        open_moment_id="m_open",
        cap_tokens=0,
    )
    assert items == []
    assert reason is None
    assert meta is not None
    assert meta["cap_tokens"] == 0
    assert meta["packed"] == 0
    assert meta["elapsed_ms"] == 0


def test_select_semantic_true_empty_channel_is_no_hits(store):
    """Filtering index with no vectors at all → no_hits (probe finds nothing)."""
    idx = _FilteringHitIndex([])  # nothing to return even unexcluded
    emb = MockEmbedder()
    cfg = MemorySettings(semantic_enabled=True, semantic_select_max_ms=500)
    items, reason, meta = select_semantic(
        store,
        index=idx,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="seed")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
        exclude_atom_ids={"a_other"},
    )
    assert items == []
    assert reason == SEMANTIC_OMIT_NO_HITS
    assert meta is not None
    assert meta["raw_hits"] == 0
    assert meta.get("deduped", 0) == 0
    assert meta.get("dedupe_probe") is True


# ---------------------------------------------------------------------------
# Wait-for-select (CPU dogfood: keep slow encode when wait on)
# ---------------------------------------------------------------------------


class _SlowEncodeEmbedder:
    """Warm embedder that sleeps during encode_text (simulates CPU Nemotron)."""

    is_loaded = True

    def __init__(self, sleep_s: float = 0.08):
        self.sleep_s = sleep_s
        self._inner = MockEmbedder()

    def health(self) -> dict[str, Any]:
        return {"ok": True, "backend": "mock", "device": "cpu"}

    def encode_text(self, text: str) -> list[float]:
        time.sleep(self.sleep_s)
        return list(self._inner.encode_text(text))


def test_select_semantic_wait_on_keeps_slow_encode(store):
    """Wait on: encode >50ms still packs hits; no post-encode timeout discard."""
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="gardens in summer",
        moment_id="m_past",
        atom_id="a_past",
        embedding_status="ready",
    )
    store.put_atom(past)
    hit = ScoredAtom(atom_id="a_past", score=0.9, channel="joint", atom=past)
    idx = _FixedHitIndex([hit])
    emb = _SlowEncodeEmbedder(sleep_s=0.08)
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=50,
        encode_query_max_ms=30,
        semantic_wait_for_select=True,
        semantic_wait_max_ms=5000,
    )
    items, reason, meta = select_semantic(
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
    assert reason is None
    assert len(items) == 1
    assert items[0].atom_id == "a_past"
    assert meta is not None
    assert meta["wait"] is True
    assert meta["deadline_ms"] == 5000
    assert meta["packed"] == 1
    assert meta["elapsed_ms"] >= 50


def test_select_semantic_wait_off_slow_encode_timeout(store):
    """Wait off: same slow encode is discarded as timeout (snappy omit)."""
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="gardens in summer",
        moment_id="m_past",
        atom_id="a_past",
        embedding_status="ready",
    )
    store.put_atom(past)
    hit = ScoredAtom(atom_id="a_past", score=0.9, channel="joint", atom=past)
    idx = _FixedHitIndex([hit])
    emb = _SlowEncodeEmbedder(sleep_s=0.08)
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=50,
        encode_query_max_ms=30,
        semantic_wait_for_select=False,
    )
    items, reason, meta = select_semantic(
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
    assert reason == SEMANTIC_OMIT_TIMEOUT
    assert meta is not None
    assert meta["wait"] is False


def test_select_semantic_wait_on_still_fail_fast_empty_seed(store):
    """Wait on does not change empty_seed / cold encoder fail-fast."""
    emb = _SlowEncodeEmbedder(sleep_s=0.5)
    idx = _FixedHitIndex([])
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_wait_for_select=True,
        semantic_wait_max_ms=15000,
    )
    t0 = time.perf_counter()
    items, reason, meta = select_semantic(
        store,
        index=idx,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", kind="tool", text="tool noise")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
    )
    elapsed = time.perf_counter() - t0
    assert items == []
    assert reason == SEMANTIC_OMIT_EMPTY_SEED
    assert meta is not None
    assert meta["wait"] is True
    # Must not sleep on encode for empty seed.
    assert elapsed < 0.2


def test_select_semantic_wait_on_still_fail_fast_cold_encoder(store):
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_wait_for_select=True,
        semantic_wait_max_ms=15000,
    )
    items, reason, meta = select_semantic(
        store,
        index=_FixedHitIndex([]),
        embedder=_ColdEmbedder(),
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="seed body")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
    )
    assert items == []
    assert reason == SEMANTIC_OMIT_ENCODER
    assert meta is not None
    assert meta["wait"] is True


def test_select_semantic_wait_kwargs_override_settings(store):
    """Explicit wait_for_completion kwargs win over MemorySettings."""
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="cats",
        moment_id="m_past",
        atom_id="a_past",
        embedding_status="ready",
    )
    store.put_atom(past)
    hit = ScoredAtom(atom_id="a_past", score=0.88, channel="joint", atom=past)
    emb = _SlowEncodeEmbedder(sleep_s=0.08)
    # Settings say wait off; kwargs force wait on with short ceiling that still
    # covers 80ms encode.
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=20,
        encode_query_max_ms=10,
        semantic_wait_for_select=False,
        semantic_wait_max_ms=1000,
    )
    items, reason, meta = select_semantic(
        store,
        index=_FixedHitIndex([hit]),
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="thinking of cats")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
        wait_for_completion=True,
        wait_max_ms=5000,
    )
    assert reason is None
    assert len(items) == 1
    assert meta is not None
    assert meta["wait"] is True
    assert meta["deadline_ms"] == 5000


def test_select_semantic_wait_on_packs_when_encode_past_deadline(store):
    """Wait on: encode past absolute deadline still search+packs good vector.

    deadline_ms bypasses product clamp so we can exercise the ceiling-edge
    case without sleeping a full second (MIN wait band is 1000ms).
    """
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="late encode keep",
        moment_id="m_past",
        atom_id="a_late",
        embedding_status="ready",
    )
    store.put_atom(past)
    hit = ScoredAtom(atom_id="a_late", score=0.95, channel="joint", atom=past)
    emb = _SlowEncodeEmbedder(sleep_s=0.08)
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=50,
        encode_query_max_ms=30,
        semantic_wait_for_select=True,
        semantic_wait_max_ms=15_000,
    )
    items, reason, meta = select_semantic(
        store,
        index=_FixedHitIndex([hit]),
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="seed late encode")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
        wait_for_completion=True,
        deadline_ms=50,  # encode 80ms already past; still pack
    )
    assert reason is None
    assert len(items) == 1
    assert items[0].atom_id == "a_late"
    assert meta is not None
    assert meta["wait"] is True
    assert meta["deadline_ms"] == 50
    assert meta["packed"] == 1
    assert meta["elapsed_ms"] >= 50


def test_select_semantic_wait_on_dedupe_probe_past_deadline(store):
    """Wait + late encode + exclude-filtered empty search → still probe deduped.

    Without the wait probe gate, over_deadline would skip the empty-channel
    probe and mis-report no_hits after a paid encode.
    """
    past = _atom(
        t="2026-07-27T10:00:00Z",
        text="already packed elsewhere",
        moment_id="m_past",
        atom_id="a_dup",
        embedding_status="ready",
    )
    store.put_atom(past)
    hit = ScoredAtom(atom_id="a_dup", score=0.95, channel="joint", atom=past)
    idx = _FilteringHitIndex([hit])
    emb = _SlowEncodeEmbedder(sleep_s=0.08)
    cfg = MemorySettings(
        semantic_enabled=True,
        semantic_select_max_ms=50,
        encode_query_max_ms=30,
        semantic_wait_for_select=True,
        semantic_wait_max_ms=15_000,
    )
    items, reason, meta = select_semantic(
        store,
        index=idx,
        embedder=emb,
        open_moment_atoms=[
            _atom(t="2026-07-28T12:00:00Z", text="seed late dedupe probe")
        ],
        open_moment_id="m_open",
        cap_tokens=500,
        settings=cfg,
        exclude_atom_ids={"a_dup"},
        wait_for_completion=True,
        deadline_ms=50,  # encode already past; probe must still run
    )
    assert items == []
    assert reason == SEMANTIC_OMIT_DEDUPED
    assert meta is not None
    assert meta["wait"] is True
    assert meta["raw_hits"] == 0
    assert meta["deduped"] >= 1
    assert meta.get("dedupe_probe") is True
    assert idx.search_calls >= 2  # primary + probe
