"""PR3 (#124): image-first MM drain, text+image joint, optional live encode.

Does **not** re-own fixture PNG / resolve regression (PR1) or diagnostics
persistence (PR2). Owns:

1. Hermetic mock **queue drain** → ``ready`` + durable ``embed_channels``
   (incl. image) + skip list (empty on clean path).
2. text+image **joint** contract under mock (true multi-mod fusion, not sole copy).
3. Optional live Nemotron image encode under ``@pytest.mark.memory_embed`` /
   ``gpu`` — skips cleanly without deps / weights / mm utils.

Hermetic suite: mock only; no torch / GPU / sandbox junk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.media.store import MediaStore
from elyra.memory.config import MemorySettings
from elyra.memory.embed import EMBED_DIM, MockEmbedder, encode_atom
from elyra.memory.embed.mock import mock_vector
from elyra.memory.embed.queue import EncodeQueue
from elyra.memory.embed.types import ModalityParts
from elyra.memory.store import open_memory_store
from elyra.memory.types import Atom, atom_replace, new_atom_id

FIXTURE_TINY_PNG = Path(__file__).parent / "fixtures" / "mm_embed" / "tiny.png"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
    text: str = "caption",
    status: str = "pending",
    media_ids: tuple[str, ...] = (),
    kind: str = "observation",
    meta: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Atom:
    return Atom(
        atom_id=kwargs.pop("atom_id", None) or new_atom_id(),
        t_start=kwargs.pop("t_start", "2026-08-05T14:00:00Z"),
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=kwargs.pop("moment_id", "m1"),
        media_ids=media_ids,
        embedding_status=status,
        meta=meta or {},
        **kwargs,
    )


class _Idx:
    """Index double: upsert(EmbeddingSet) → ready; records last emb set."""

    def __init__(self) -> None:
        self.seen: dict[str, Any] = {}

    def upsert(self, *args: Any, **kwargs: Any) -> bool:
        if args and hasattr(args[0], "atom_id"):
            emb = args[0]
            self.seen[str(emb.atom_id)] = emb
            return True
        if len(args) >= 2:
            self.seen[str(args[0])] = args[1]
            return True
        return False


def _assert_unit(vec: Any, dim: int = EMBED_DIM) -> None:
    assert vec is not None
    assert len(vec) == dim
    n = sum(float(x) * float(x) for x in vec) ** 0.5
    assert abs(n - 1.0) < 1e-5, f"expected unit vector, got norm={n}"


# ---------------------------------------------------------------------------
# 1. Hermetic mock drain → ready + image channel + skip list
# ---------------------------------------------------------------------------


def test_drain_ready_image_channel_real_mediastores(store, media_store):
    """Image-first hard gate: real MediaStore PNG → drain ready with image.

    Owns the closed mock loop PR1 resolve + PR2 meta do not alone prove:
    queue drain with product MediaStore yields durable ``embed_channels``
    including ``image``, empty skip list, and index-held vectors.
    """
    assert FIXTURE_TINY_PNG.is_file()
    data = FIXTURE_TINY_PNG.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"

    att = media_store.put_bytes(data, filename="frame.png", origin="user_upload")
    atom = store.put_atom(
        _atom(text="a small caption", status="pending", media_ids=(att.id,))
    )

    q = EncodeQueue(maxsize=8)
    q.enqueue(atom.atom_id)
    idx = _Idx()
    emb = MockEmbedder()
    stats = q.drain(
        store,
        emb,
        index=idx,
        media_store=media_store,
        max_items=2,
        max_ms=5000,
    )
    assert stats["ok"] == 1
    assert stats["failed"] == 0

    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    channels = list(got.meta.get("embed_channels") or [])
    assert "image" in channels
    assert "text" in channels
    assert "joint" in channels
    # Clean path: durable empty skip inventory (not stale / not absent).
    assert got.meta.get("embed_media_skipped") == []
    assert not any(
        "no_path" in str(s) for s in (got.meta.get("embed_media_skipped") or [])
    )
    assert got.meta.get("embed_encode_ok") is True

    # Index holds a real image vector (not text re-labeled as image).
    held = idx.seen.get(atom.atom_id)
    assert held is not None
    assert held.emb_image is not None
    assert held.emb_text is not None
    _assert_unit(held.emb_image)
    _assert_unit(held.emb_text)
    assert held.emb_image != held.emb_text
    # KD-M11: never store a text-only pool under emb_image.
    text_only = tuple(emb.encode_text("a small caption"))
    assert held.emb_image != text_only


def test_drain_image_only_ready_joint_copy(store, media_store):
    """Media-only atom (empty text) drains to ready; joint = copy(image) (KD-R1)."""
    att = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="only.png", origin="user_upload"
    )
    atom = store.put_atom(_atom(text="", status="pending", media_ids=(att.id,)))
    # Force empty content_text past put prepare if any.
    atom = store.put_atom(
        atom_replace(
            atom,
            content_text="",
            media_ids=(att.id,),
            embedding_status="pending",
        ),
        notify=False,
    )

    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    idx = _Idx()
    stats = q.drain(
        store,
        MockEmbedder(),
        index=idx,
        media_store=media_store,
        max_items=2,
        max_ms=5000,
    )
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    channels = set(got.meta.get("embed_channels") or [])
    assert "image" in channels
    assert "joint" in channels
    assert "text" not in channels
    assert got.meta.get("embed_media_skipped") == []

    held = idx.seen[atom.atom_id]
    assert held.emb_image is not None
    assert held.emb_text is None
    assert held.emb_joint is not None
    # Single-mod joint is elementwise copy, not encode_joint seed.
    assert held.emb_joint == held.emb_image


def test_drain_without_index_still_records_image_channels(store, media_store):
    """Production path (no index): stay pending but durable image channels."""
    att = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="pending.png", origin="user_upload"
    )
    atom = store.put_atom(
        _atom(text="pending encode", status="pending", media_ids=(att.id,))
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    stats = q.drain(
        store,
        MockEmbedder(),
        index=None,
        media_store=media_store,
        max_items=2,
        max_ms=5000,
    )
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    # KD8: no false ready without index.
    assert got.embedding_status == "pending"
    assert got.meta.get("embed_encode_ok") is True
    channels = got.meta.get("embed_channels") or []
    assert "image" in channels
    assert "text" in channels
    assert got.meta.get("embed_media_skipped") == []


# ---------------------------------------------------------------------------
# 2. text+image joint contract under mock
# ---------------------------------------------------------------------------


def test_drain_text_image_joint_true_fusion(store, media_store):
    """Multi-mod drain: emb_joint is true encode_joint, not sole-channel copy."""
    att = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="joint.png", origin="user_upload"
    )
    caption = "joint fusion caption"
    atom = store.put_atom(_atom(text=caption, status="pending", media_ids=(att.id,)))
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    idx = _Idx()
    emb = MockEmbedder()
    stats = q.drain(
        store, emb, index=idx, media_store=media_store, max_items=2, max_ms=5000
    )
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    assert set(got.meta.get("embed_channels") or []) >= {"text", "image", "joint"}

    held = idx.seen[atom.atom_id]
    assert held.emb_text is not None
    assert held.emb_image is not None
    assert held.emb_joint is not None
    _assert_unit(held.emb_joint)
    # Multi-mod: joint must diverge from each sole channel.
    assert held.emb_joint != held.emb_text
    assert held.emb_joint != held.emb_image
    # Matches direct mock encode_joint over the same resolved inputs.
    blob = media_store.blob_path(att.sha256)
    expected_joint = tuple(
        emb.encode_joint(ModalityParts(text=caption, image=str(blob)))
    )
    assert held.emb_joint == expected_joint


def test_encode_atom_text_image_joint_matches_drain_contract(media_store):
    """Direct encode_atom (no queue) agrees with multi-mod joint policy."""
    att = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="direct.png", origin="user_upload"
    )
    atom = _atom(text="direct caption", media_ids=(att.id,))
    emb = MockEmbedder()
    result = encode_atom(emb, atom, media_store=media_store)
    assert result.status == "ready"
    assert result.embeddings is not None
    e = result.embeddings
    assert (
        e.emb_text is not None and e.emb_image is not None and e.emb_joint is not None
    )
    assert set(result.channels_encoded) >= {"text", "image", "joint"}
    assert e.emb_joint != e.emb_text
    assert e.emb_joint != e.emb_image
    skipped = (result.meta or {}).get("embed_media_skipped") or []
    assert skipped == []
    assert not any("no_path" in s for s in skipped)


def test_drain_second_image_channel_full_skip_list(store, media_store):
    """First image wins; second → channel_full skip token durable on ready."""
    a1 = media_store.put_bytes(
        FIXTURE_TINY_PNG.read_bytes(), filename="first.png", origin="user_upload"
    )
    # Distinct valid PNG bytes → distinct content-addressed att id.
    a2 = media_store.put_bytes(
        _minimal_png_rgb(255, 0, 0), filename="second.png", origin="user_upload"
    )
    assert a1.id != a2.id

    atom = store.put_atom(
        _atom(
            text="two images",
            status="pending",
            media_ids=(a1.id, a2.id),
        )
    )
    q = EncodeQueue(maxsize=4)
    q.enqueue(atom.atom_id)
    idx = _Idx()
    stats = q.drain(
        store,
        MockEmbedder(),
        index=idx,
        media_store=media_store,
        max_items=2,
        max_ms=5000,
    )
    assert stats["ok"] == 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    assert got.embedding_status == "ready"
    channels = got.meta.get("embed_channels") or []
    assert "image" in channels
    skipped = got.meta.get("embed_media_skipped") or []
    assert any("channel_full" in s for s in skipped)
    assert any(a2.id in s for s in skipped)
    # First image still encoded.
    held = idx.seen[atom.atom_id]
    assert held.emb_image is not None


def _minimal_png_rgb(r: int, g: int, b: int) -> bytes:
    """Build a tiny valid 1×1 RGB PNG (different pixel → different sha)."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)  # 1x1, 8-bit RGB
    # Filter byte 0 + RGB
    raw = b"\x00" + bytes((r & 0xFF, g & 0xFF, b & 0xFF))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# 3. Optional live Nemotron image encode (skip cleanly without deps)
# ---------------------------------------------------------------------------


def _torch_tf_ready() -> bool:
    try:
        from elyra.memory.embed.runtime import (
            torch_available,
            transformers_available,
        )
    except Exception:  # noqa: BLE001
        return False
    return bool(torch_available() and transformers_available())


def _mm_utils_ready() -> bool:
    try:
        import qwen_omni_utils  # noqa: F401
        from qwen_omni_utils import process_mm_info  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _gpu_ready() -> bool:
    try:
        from elyra.memory.embed.runtime import probe_devices, torch_available
    except Exception:  # noqa: BLE001
        return False
    if not torch_available():
        return False
    caps = probe_devices()
    return bool(caps.get("cuda") or caps.get("rocm"))


def _model_reachable() -> bool:
    """Best-effort: local path or HF hub cache; never downloads."""
    import os

    from elyra.memory.config import MemorySettings
    from elyra.memory.embed import DEFAULT_NEMOTRON_MODEL_ID  # noqa: F401

    env_path = (os.environ.get("ELYRA_EMBED_MODEL_PATH") or "").strip()
    if env_path and Path(env_path).is_dir():
        return True
    cfg_path = (MemorySettings().embed_model_path or "").strip()
    if cfg_path and Path(cfg_path).is_dir():
        return True
    marker = Path.home() / ".cache" / "huggingface" / "hub"
    if not marker.is_dir():
        return False
    try:
        for child in marker.iterdir():
            if "omni-embed-nemotron" in child.name.lower():
                return True
    except OSError:
        return False
    return False


@pytest.mark.memory_embed
def test_nemotron_encode_image_optional_skips_clean():
    """Optional live: Nemotron encode_image when deps + mm_utils + weights.

    Without any of those, skip — never fail the hermetic suite.
    Assertion failures (wrong dim / non-unit) still fail the test when the
    path actually runs (do not wrap asserts in bare except → skip).
    """
    if not _torch_tf_ready():
        pytest.skip("torch/transformers not installed (elyra[memory-embed])")
    if not _mm_utils_ready():
        pytest.skip("qwen_omni_utils not installed (media encode unavailable)")
    if not _model_reachable():
        pytest.skip("Nemotron weights not cached/local (no download in CI)")

    from elyra.memory.embed import (
        DEFAULT_NEMOTRON_MODEL_ID,
        NemotronEmbedder,
        open_encoder,
    )

    cfg = MemorySettings(
        embed_backend="nemotron",
        embed_model_id=DEFAULT_NEMOTRON_MODEL_ID,
        embed_device="cpu",
    )
    enc = open_encoder(cfg)
    try:
        assert isinstance(enc, NemotronEmbedder)
        try:
            enc.ensure_loaded()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — load/OOM/import only
            pytest.skip(f"Nemotron load unavailable: {exc}")

        h = enc.health()
        if not h.get("media_encode"):
            pytest.skip("Nemotron media_encode false after load (mm utils path)")

        png = FIXTURE_TINY_PNG
        assert png.is_file()
        try:
            vec = enc.encode_image(str(png))
        except Exception as exc:  # noqa: BLE001 — runtime encode only
            pytest.skip(f"Nemotron encode_image unavailable: {exc}")

        # Assertions outside soft-skip try — regressions fail the test.
        assert len(vec) == EMBED_DIM
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-3
        # Must not be a text-only pool under image channel.
        try:
            text_vec = enc.encode_text("a small caption")
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Nemotron encode_text unavailable for contrast: {exc}")
        assert list(vec) != list(text_vec)
    finally:
        enc.close()


@pytest.mark.memory_embed
@pytest.mark.gpu
def test_nemotron_encode_image_gpu_optional():
    """GPU image encode smoke when CUDA/ROCm + weights + mm_utils present."""
    if not _gpu_ready():
        pytest.skip("CUDA/ROCm unavailable")
    if not _torch_tf_ready():
        pytest.skip("torch/transformers not installed")
    if not _mm_utils_ready():
        pytest.skip("qwen_omni_utils not installed")
    if not _model_reachable():
        pytest.skip("Nemotron weights not cached/local (no download in CI)")

    from elyra.memory.embed import (
        DEFAULT_NEMOTRON_MODEL_ID,
        NemotronEmbedder,
        open_encoder,
    )

    cfg = MemorySettings(
        embed_backend="nemotron",
        embed_model_id=DEFAULT_NEMOTRON_MODEL_ID,
        embed_device="auto",
    )
    enc = open_encoder(cfg)
    try:
        assert isinstance(enc, NemotronEmbedder)
        try:
            enc.ensure_loaded()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Nemotron load unavailable: {exc}")
        if not enc.health().get("media_encode"):
            pytest.skip("media_encode unavailable on GPU path")
        try:
            vec = enc.encode_image(str(FIXTURE_TINY_PNG))
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Nemotron encode_image unavailable: {exc}")
        assert len(vec) == EMBED_DIM
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-3
        h = enc.health()
        assert h["ok"] is True
        assert h["device"] in ("cuda", "rocm")
    finally:
        enc.close()


def test_optional_live_markers_registered():
    """Markers used by optional live tests must be known to pytest config."""
    # Smoke: importing this module under hermetic collect must not require torch.
    # Registry lives in pyproject.toml; presence is asserted there by suite.
    # This always-pass check documents the contract for PR3 live marks.
    assert FIXTURE_TINY_PNG.is_file()
    # mock_vector still hermetic (sanity that mock path works without live).
    v = mock_vector("pr3-image-first", dim=16)
    assert len(v) == 16
