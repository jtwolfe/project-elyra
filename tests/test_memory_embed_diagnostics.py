"""PR2 (#124): persist embed_media_skipped + media_encode health + inspect.

Hermetic only (mock encoder; no torch / qwen-omni-utils). Covers KD-M3/M4:
- queue drain copies embed_media_skipped on ready / skipped / failed /
  media_unresolved paths
- MockEmbedder.health media_encode; encoder_health_block promotion
- atom_to_detail embed meta + media inventory (no secrets / no blob bytes)
- zero-state / empty skip list paths
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.media.store import MediaStore
from elyra.memory.config import MemorySettings
from elyra.memory.embed.mock import MockEmbedder
from elyra.memory.embed.queue import EncodeQueue
from elyra.memory.inspect import (
    atom_to_detail,
    atom_to_vector_row,
    encoder_health_block,
)
from elyra.memory.store import open_memory_store
from elyra.memory.types import Atom, atom_replace, new_atom_id

FIXTURE_TINY_PNG = Path(__file__).parent / "fixtures" / "mm_embed" / "tiny.png"


@pytest.fixture
def paths(tmp_path: Path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    return open_memory_store(paths, MemorySettings(write_atoms=True, backend="jsonl"))


@pytest.fixture
def media_store(paths) -> MediaStore:
    return MediaStore(paths)


def _atom(
    *,
    text: str = "hello",
    status: str = "pending",
    media_ids: tuple[str, ...] = (),
    meta: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Atom:
    return Atom(
        atom_id=kwargs.pop("atom_id", None) or new_atom_id(),
        t_start=kwargs.pop("t_start", "2026-08-05T12:00:00Z"),
        kind=kwargs.pop("kind", "observation"),
        content_text=text,
        content_ref="inline",
        moment_id=kwargs.pop("moment_id", "m1"),
        media_ids=media_ids,
        embedding_status=status,
        meta=meta or {},
        **kwargs,
    )


class _Idx:
    """Minimal index double that promotes ready on upsert."""

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    def upsert(self, *args: Any, **kwargs: Any) -> bool:
        # Accept EmbeddingSet or (atom_id, emb) forms.
        if args and hasattr(args[0], "atom_id"):
            emb = args[0]
            self.seen[str(emb.atom_id)] = emb
        elif len(args) >= 2:
            self.seen[str(args[0])] = args[1]
        return True


# ── Mock health: media_encode ─────────────────────────────────────────────


def test_mock_health_media_encode_true_when_open():
    emb = MockEmbedder()
    h = emb.health()
    assert h["ok"] is True
    assert h["backend"] == "mock"
    assert h["media_encode"] is True
    emb.close()
    h2 = emb.health()
    assert h2["ok"] is False
    assert h2["media_encode"] is False


def test_encoder_health_block_promotes_media_encode():
    emb = MockEmbedder()
    block = encoder_health_block(
        settings=MemorySettings(embed_enabled=True, embed_backend="mock"),
        embedder=emb,
        queue=EncodeQueue(maxsize=4),
        presence=None,
    )
    assert block["ok"] is True
    assert block["backend"] == "mock"
    assert block["media_encode"] is True
    assert block.get("media_encode_note") == "mock"
    # No secrets.
    blob = str(block).lower()
    assert "api_key" not in blob
    assert "xai_api" not in blob
    assert "password" not in blob


def test_encoder_health_block_no_embedder_media_encode_false():
    block = encoder_health_block(
        settings=MemorySettings(embed_enabled=True, embed_backend="mock"),
        embedder=None,
        queue=None,
        presence=None,
    )
    assert block["loaded"] is False
    assert block["media_encode"] is False
    assert block["error"] == "encoder_not_loaded"


def test_encoder_health_block_embed_disabled_zero_state():
    block = encoder_health_block(
        settings=MemorySettings(embed_enabled=False),
        embedder=None,
        queue=None,
    )
    assert block["embed_enabled"] is False
    assert block["media_encode"] is False
    assert block["error"] == "embed_disabled"
    assert block["queue_depth"] == 0


def test_encoder_health_block_derives_mock_when_health_omits_key():
    """Backward-compat: health without media_encode still surfaces for mock."""

    class _LegacyMock:
        def health(self) -> dict[str, Any]:
            return {
                "ok": True,
                "device": "cpu",
                "model_id": "legacy",
                "dim": 8,
                "backend": "mock",
                "error": None,
            }

    block = encoder_health_block(
        settings=MemorySettings(embed_enabled=True, embed_backend="mock"),
        embedder=_LegacyMock(),
        queue=None,
    )
    assert block["media_encode"] is True
    assert block["media_encode_note"] == "mock"


def test_encoder_health_block_mock_fallback_keeps_requested_backend():
    """Nemotron→mock fallback: media_encode true via mock; backend honesty."""

    class _Fallback:
        def health(self) -> dict[str, Any]:
            return {
                "ok": True,
                "device": "cpu",
                "model_id": "mock/hash-embed-v1",
                "dim": 2048,
                "backend": "mock",
                "media_encode": True,
                "requested_backend": "nemotron",
                "requested_model_id": "nvidia/omni-embed-nemotron-3b",
                "error": "nemotron runtime not loaded; using mock fallback",
            }

    block = encoder_health_block(
        settings=MemorySettings(
            embed_enabled=True,
            embed_backend="nemotron",
        ),
        embedder=_Fallback(),
        queue=None,
    )
    assert block["backend"] == "mock"
    assert block["requested_backend"] == "nemotron"
    assert block["media_encode"] is True
    assert block["media_encode_note"] == "mock"


def test_encoder_health_block_nemotron_without_media_encode_key():
    """Nemotron health omitting key → null (not false claim of mock)."""

    class _Nemo:
        def health(self) -> dict[str, Any]:
            return {
                "ok": True,
                "device": "cpu",
                "model_id": "nvidia/x",
                "dim": 2048,
                "backend": "nemotron",
                "loaded": False,
                "error": None,
            }

    block = encoder_health_block(
        settings=MemorySettings(embed_enabled=True, embed_backend="nemotron"),
        embedder=_Nemo(),
        queue=None,
    )
    assert block["backend"] == "nemotron"
    assert block["media_encode"] is None


def test_encoder_health_block_media_encode_false_note():
    class _NoMm:
        def health(self) -> dict[str, Any]:
            return {
                "ok": True,
                "device": "cpu",
                "model_id": "nvidia/x",
                "dim": 2048,
                "backend": "nemotron",
                "loaded": True,
                "media_encode": False,
                "error": None,
            }

    block = encoder_health_block(
        settings=None,
        embedder=_NoMm(),
        queue=None,
    )
    assert block["media_encode"] is False
    assert block["media_encode_note"] == "install_qwen_omni_utils"


def test_encoder_health_block_closed_mock_no_install_note():
    """Closed mock: media_encode false must not claim install qwen-omni-utils."""
    emb = MockEmbedder()
    emb.close()
    block = encoder_health_block(
        settings=MemorySettings(embed_enabled=True, embed_backend="mock"),
        embedder=emb,
        queue=None,
    )
    assert block["backend"] == "mock"
    assert block["media_encode"] is False
    assert block["error"] == "closed"
    assert block.get("media_encode_note") is None


# ── Queue drain: persist embed_media_skipped ──────────────────────────────


def test_drain_persists_media_skipped_on_ready_partial(store, media_store):
    """Partial success: text+image encode, second media oversize → skip list on ready."""
    good = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="ok.png", origin="user_upload"
    )
    huge = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="big.png", origin="user_upload"
    )
    # Force oversize on second attachment without rewriting blob.
    real_get = media_store.get

    def _get(aid: str):
        att = real_get(aid)
        if att is not None and att.id == huge.id:
            return SimpleNamespace(
                id=att.id,
                mime=att.mime,
                filename=att.filename,
                kind=att.kind,
                sha256=att.sha256,
                byte_size=50_000_000,
            )
        return att

    media_store.get = _get  # type: ignore[method-assign]

    atom = store.put_atom(
        _atom(
            text="caption",
            status="pending",
            media_ids=(good.id, huge.id),
        )
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    stats = q.drain(
        store,
        MockEmbedder(),
        index=_Idx(),
        media_store=media_store,
        max_items=2,
        settings=SimpleNamespace(
            encode_max_ms_per_tick=5000,
            encode_max_items_per_tick=4,
            encode_max_attempts=3,
            embed_media_max_bytes=1000,
            embed_media_max_seconds=30,
            embed_joint_for_single_modality=True,
        ),
    )
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    skipped = got.meta.get("embed_media_skipped") or []
    assert any("oversize" in s for s in skipped)
    assert any(huge.id in s for s in skipped)
    channels = got.meta.get("embed_channels") or []
    assert "image" in channels
    assert "text" in channels


def test_drain_persists_media_skipped_on_media_unresolved(store):
    """Missing media ids → pending + durable skip inventory."""
    mid = "att_missing_zzzz"
    atom = store.put_atom(_atom(text="", status="pending", media_ids=(mid,)))
    atom = store.put_atom(
        atom_replace(atom, content_text="", media_ids=(mid,), embedding_status="pending"),
        notify=False,
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)

    class _EmptyStore:
        def get(self, _aid: str):
            return None

    stats = q.drain(
        store, MockEmbedder(), media_store=_EmptyStore(), max_items=2
    )
    assert stats["skipped"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "pending"
    assert got.meta.get("embed_error") == "media_unresolved"
    skipped = got.meta.get("embed_media_skipped") or []
    assert any(mid in s for s in skipped)
    assert any("missing" in s for s in skipped)


def test_drain_persists_media_skipped_on_skipped_kind(store):
    """Kind-skipped (moment_meta) never invents embed_media_skipped (zero-state)."""
    atom = store.put_atom(
        _atom(text="meta only", status="pending", kind="moment_meta")
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    q.drain(store, MockEmbedder(), max_items=2)
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "skipped"
    # Zero-state: no stale skip inventory invented.
    assert got.meta.get("embed_media_skipped") in (None, [])


def test_drain_persists_media_skipped_on_failed(store, media_store):
    """Failed encode still copies resolve skip inventory (no synthetic meta).

    Boom has no encode_atom_inputs — free helper hits encode_text which raises.
    encode_atom exception path (or merge path) must still keep oversize tokens.
    """
    att = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="x.png", origin="user_upload"
    )
    real_get = media_store.get

    def _get(aid: str):
        a = real_get(aid)
        if a is not None and a.id == att.id:
            return SimpleNamespace(
                id=a.id,
                mime=a.mime,
                filename=a.filename,
                kind=a.kind,
                sha256=a.sha256,
                byte_size=99_000_000,
            )
        return a

    media_store.get = _get  # type: ignore[method-assign]

    class Boom:
        """Protocol-only embedder: channel methods raise; no encode_atom_inputs."""

        def health(self):
            return {
                "ok": True,
                "dim": 8,
                "model_id": "x",
                "backend": "mock",
                "media_encode": True,
            }

        def encode_text(self, text: str):
            raise RuntimeError("boom")

        def encode_image(self, x):
            raise RuntimeError("boom")

        def encode_audio(self, x):
            raise RuntimeError("boom")

        def encode_video(self, x):
            raise RuntimeError("boom")

        def encode_joint(self, parts):
            raise RuntimeError("boom")

    atom = store.put_atom(
        _atom(text="will fail", status="pending", media_ids=(att.id,))
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    # max_attempts=1 so first fail → failed status
    q.drain(
        store,
        Boom(),
        media_store=media_store,
        max_items=1,
        max_attempts=1,
        settings=SimpleNamespace(
            encode_max_ms_per_tick=5000,
            encode_max_items_per_tick=1,
            encode_max_attempts=1,
            embed_media_max_bytes=100,
            embed_media_max_seconds=30,
            embed_joint_for_single_modality=True,
        ),
    )
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "failed"
    # Oversize soft-skip from resolve is recorded even when forward fails.
    skipped = got.meta.get("embed_media_skipped") or []
    assert any("oversize" in s for s in skipped)
    assert any(att.id in s for s in skipped)


def test_encode_atom_exception_preserves_media_skipped(media_store):
    """Issue 4: hard raise after resolve still carries embed_media_skipped."""
    from elyra.memory.embed.encode import encode_atom

    att = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="exc.png", origin="user_upload"
    )
    real_get = media_store.get

    def _get(aid: str):
        a = real_get(aid)
        if a is not None and a.id == att.id:
            return SimpleNamespace(
                id=a.id,
                mime=a.mime,
                filename=a.filename,
                kind=a.kind,
                sha256=a.sha256,
                byte_size=99_000_000,
            )
        return a

    media_store.get = _get  # type: ignore[method-assign]

    class Boom:
        def health(self):
            return {"ok": True, "dim": 8, "model_id": "x", "backend": "mock"}

        def encode_text(self, text: str):
            raise RuntimeError("forward boom")

        def encode_image(self, x):
            raise RuntimeError("forward boom")

        def encode_audio(self, x):
            raise RuntimeError("forward boom")

        def encode_video(self, x):
            raise RuntimeError("forward boom")

        def encode_joint(self, parts):
            raise RuntimeError("forward boom")

    atom = _atom(text="caption", media_ids=(att.id,))
    result = encode_atom(
        Boom(), atom, media_store=media_store, media_max_bytes=100
    )
    assert result.status == "failed"
    assert "boom" in (result.error or "").lower()
    skipped = (result.meta or {}).get("embed_media_skipped") or []
    assert any("oversize" in s for s in skipped)
    assert any(att.id in s for s in skipped)


def test_drain_no_skip_list_when_clean_text_only(store):
    """Clean text encode durable-attaches empty skip list (glass zero-state)."""
    atom = store.put_atom(_atom(text="clean text only", status="pending"))
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    q.drain(store, MockEmbedder(), index=_Idx(), max_items=2)
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    # Always-write empty list so operators see last inventory, not stale absent.
    assert got.meta.get("embed_media_skipped") == []


def test_drain_clears_stale_skip_list_on_clean_reencode(store):
    """KD-M3 bugfix: prior partial inventory must clear on clean re-encode.

    Seeds a non-empty embed_media_skipped, then drains a pure-text atom through
    the real encode_atom path (not a faked EncodeResult). Ready + channels and
    durable ``[]``.
    """
    atom = store.put_atom(
        _atom(
            text="clean reencode after partial",
            status="pending",
            meta={"embed_media_skipped": ["att_old:oversize_bytes:999999"]},
        )
    )
    assert (store.get_atom(atom.atom_id).meta.get("embed_media_skipped") or [])  # type: ignore[union-attr]
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    stats = q.drain(store, MockEmbedder(), index=_Idx(), max_items=2)
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    assert "text" in (got.meta.get("embed_channels") or [])
    assert got.meta.get("embed_media_skipped") == []


def test_drain_clears_skip_list_when_result_has_empty_key(store):
    """Explicit empty embed_media_skipped on result clears prior inventory."""
    from elyra.memory.embed import queue as queue_mod
    from elyra.memory.embed.types import EncodeResult, EmbeddingSet
    from elyra.memory.types import utc_now_iso

    atom = store.put_atom(
        _atom(
            text="reencode",
            status="pending",
            meta={"embed_media_skipped": ["old:skip"]},
        )
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)

    emb_set = EmbeddingSet(
        atom_id=atom.atom_id,
        dim=8,
        emb_text=tuple([0.1] * 8),
        emb_joint=tuple([0.1] * 8),
        model_id="mock",
        encoded_at=utc_now_iso(),
        channels_present=("text", "joint"),
    )
    fake = EncodeResult(
        status="ready",
        embeddings=emb_set,
        error=None,
        channels_encoded=("text", "joint"),
        meta={"embed_media_skipped": []},
    )

    real_encode = queue_mod.encode_atom

    def _fake_encode(*a, **k):
        return fake

    queue_mod.encode_atom = _fake_encode  # type: ignore[assignment]
    try:
        q.drain(store, MockEmbedder(), index=_Idx(), max_items=1)
    finally:
        queue_mod.encode_atom = real_encode  # type: ignore[assignment]

    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.meta.get("embed_media_skipped") == []


# ── Inspect: atom detail + vector row ─────────────────────────────────────


def test_atom_to_detail_exposes_embed_meta_and_empty_media():
    atom = _atom(
        text="detail me",
        status="ready",
        media_ids=(),
        meta={
            "embed_channels": ["text", "joint"],
            "embed_error": None,
            "embed_media_skipped": [],
        },
    )
    detail = atom_to_detail(atom)
    assert detail["embed_channels"] == ["text", "joint"]
    assert detail["embed_error"] is None
    assert detail["embed_media_skipped"] == []
    assert detail["media"] == []
    # Zero-state empty collections, not missing keys.
    assert "embed_media_skipped" in detail
    assert "media" in detail


def test_atom_to_detail_media_inventory_without_store():
    mid = "att_abc123"
    atom = _atom(
        text="with media",
        media_ids=(mid,),
        meta={
            "embed_channels": ["text", "image", "joint"],
            "embed_media_skipped": [f"{mid}:channel_full:image"],
            "embed_error": "partial_note",
        },
    )
    detail = atom_to_detail(atom)
    assert detail["embed_channels"] == ["text", "image", "joint"]
    assert detail["embed_error"] == "partial_note"
    assert detail["embed_media_skipped"] == [f"{mid}:channel_full:image"]
    assert len(detail["media"]) == 1
    inv = detail["media"][0]
    assert inv["id"] == mid
    assert inv["url"] == f"/api/media/{mid}"
    assert inv["kind"] is None
    assert inv["mime"] is None
    # No blob / secret leakage.
    assert "sha256" not in inv
    assert "bytes" not in inv
    assert "content" not in inv


def test_atom_to_detail_media_inventory_with_store(media_store):
    att = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="inv.png", origin="user_upload"
    )
    atom = _atom(text="img", media_ids=(att.id,), meta={"embed_channels": ["image"]})
    detail = atom_to_detail(atom, media_store=media_store)
    assert len(detail["media"]) == 1
    inv = detail["media"][0]
    assert inv["id"] == att.id
    assert inv["kind"] == "image"
    assert inv["mime"] == "image/png"
    assert inv["filename"] == "inv.png"
    assert inv["url"] == f"/api/media/{att.id}"
    # No secrets / raw bytes in detail.
    dumped = str(detail)
    assert "api_key" not in dumped.lower()
    assert FIXTURE_TINY_PNG.read_bytes()[:8].hex() not in dumped


def test_atom_to_detail_zero_state_missing_meta_keys():
    atom = _atom(text="bare", status="none", meta={})
    detail = atom_to_detail(atom)
    assert detail["embed_channels"] == []
    assert detail["embed_error"] is None
    assert detail["embed_media_skipped"] == []
    assert detail["media"] == []


def test_atom_to_vector_row_includes_skip_list_when_present():
    atom = _atom(
        text="vec",
        status="ready",
        media_ids=("att_1",),
        meta={
            "embed_channels": ["text", "joint"],
            "embed_media_skipped": ["att_2:missing"],
            "embed_error": "okish",
        },
    )
    row = atom_to_vector_row(atom)
    assert row["channels"] == ["text", "joint"]
    assert row["embed_media_skipped"] == ["att_2:missing"]
    assert row["embed_error"] == "okish"
    assert row["media_count"] == 1


def test_atom_to_vector_row_omits_empty_skip_list():
    """Zero-state: no embed_media_skipped key when inventory empty/absent."""
    atom = _atom(text="vec", status="ready", meta={"embed_channels": ["text"]})
    row = atom_to_vector_row(atom)
    assert "embed_media_skipped" not in row
    assert row["channels"] == ["text"]


def test_pyproject_lists_qwen_omni_utils():
    """OQ-M1/M6: package string must appear in memory-embed extra."""
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "qwen-omni-utils" in text
    assert "memory-embed" in text
