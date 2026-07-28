"""Mock encoder: deterministic 2048-d L2 vectors; open_encoder mock path."""

from __future__ import annotations

import math

import pytest

from elyra.memory.config import MemorySettings
from elyra.memory.embed import (
    CHANNELS,
    EMBED_DIM,
    MockEmbedder,
    encode_atom_inputs,
    mock_vector,
    open_encoder,
    select_device,
    vector_l2_norm,
)
from elyra.memory.embed.mock import MOCK_MODEL_ID
from elyra.memory.embed.types import ModalityParts


def _assert_unit(vec, dim: int = EMBED_DIM) -> None:
    assert len(vec) == dim
    n = vector_l2_norm(vec)
    assert abs(n - 1.0) < 1e-6, f"expected unit vector, got norm={n}"


def test_mock_vector_deterministic_and_unit():
    a = mock_vector("hello")
    b = mock_vector("hello")
    c = mock_vector("world")
    assert a == b
    assert a != c
    _assert_unit(a)
    _assert_unit(c)


def test_mock_vector_dim_override():
    v = mock_vector("x", dim=16)
    assert len(v) == 16
    _assert_unit(v, dim=16)
    with pytest.raises(ValueError, match="dim"):
        mock_vector("x", dim=0)


def test_mock_embedder_encode_text_stable():
    enc = MockEmbedder()
    v1 = enc.encode_text("same body")
    v2 = enc.encode_text("same body")
    v3 = enc.encode_text("other body")
    assert v1 == v2
    assert v1 != v3
    _assert_unit(v1)
    h = enc.health()
    assert h["ok"] is True
    assert h["backend"] == "mock"
    assert h["dim"] == EMBED_DIM
    assert h["model_id"] == MOCK_MODEL_ID
    enc.close()
    assert enc.health()["ok"] is False
    with pytest.raises(RuntimeError, match="closed"):
        enc.encode_text("x")


def test_mock_embedder_image_audio_video_bytes():
    enc = MockEmbedder()
    img = enc.encode_image(b"\x89PNG\r\n")
    aud = enc.encode_audio(b"RIFF....")
    vid = enc.encode_video(b"\x00\x00\x00 ftyp")
    _assert_unit(img)
    _assert_unit(aud)
    _assert_unit(vid)
    assert img != aud != vid
    # Same bytes → same vector.
    assert enc.encode_image(b"\x89PNG\r\n") == img


def test_mock_embedder_path_seed(tmp_path):
    p = tmp_path / "clip.bin"
    p.write_bytes(b"media-bytes-xyz")
    enc = MockEmbedder()
    from_path = enc.encode_image(str(p))
    from_bytes = enc.encode_image(b"media-bytes-xyz")
    assert from_path == from_bytes


def test_mock_joint_order_stable():
    enc = MockEmbedder()
    a = ModalityParts(text="hello", image=b"img")
    b = ModalityParts(image=b"img", text="hello")  # different construction order
    assert enc.encode_joint(a) == enc.encode_joint(b)
    _assert_unit(enc.encode_joint(a))
    # Different content → different joint.
    c = ModalityParts(text="hello", image=b"other")
    assert enc.encode_joint(a) != enc.encode_joint(c)


def test_encode_atom_inputs_text_only_no_joint():
    enc = MockEmbedder()
    result = enc.encode_atom_inputs("a_1", text="only text")
    assert result.status == "ready"
    assert result.embeddings is not None
    assert result.embeddings.emb_text is not None
    assert result.embeddings.emb_joint is None
    assert "text" in result.channels_encoded
    assert "joint" not in result.channels_encoded
    _assert_unit(result.embeddings.emb_text)


def test_encode_atom_inputs_multimodal_eager_joint():
    enc = MockEmbedder()
    result = enc.encode_atom_inputs(
        "a_2",
        text="caption",
        image=b"png-bytes",
    )
    assert result.status == "ready"
    emb = result.embeddings
    assert emb is not None
    assert emb.emb_text is not None
    assert emb.emb_image is not None
    assert emb.emb_joint is not None
    assert set(result.channels_encoded) >= {"text", "image", "joint"}
    _assert_unit(emb.emb_joint)
    # Joint differs from single channels.
    assert emb.emb_joint != emb.emb_text
    assert emb.emb_joint != emb.emb_image


def test_encode_atom_inputs_empty_skipped():
    enc = MockEmbedder()
    result = enc.encode_atom_inputs("a_3", text="   ")
    assert result.status == "skipped"
    assert result.embeddings is None
    assert result.error == "no modalities"


def test_open_encoder_mock_default():
    enc = open_encoder()
    assert isinstance(enc, MockEmbedder)
    h = enc.health()
    assert h["backend"] == "mock"
    assert h["ok"] is True
    v = enc.encode_text("ping")
    _assert_unit(v)
    enc.close()


def test_open_encoder_respects_settings_model_id():
    cfg = MemorySettings(
        embed_backend="mock",
        embed_model_id="custom/mock-id",
        embed_device="cpu",
    )
    enc = open_encoder(cfg)
    assert enc.health()["model_id"] == "custom/mock-id"
    assert enc.health()["device"] == "cpu"
    enc.close()


def test_open_encoder_nemotron_falls_back_to_mock():
    """PR1: nemotron requested but runtime not loaded → mock fallback."""
    cfg = MemorySettings(embed_backend="nemotron")
    enc = open_encoder(cfg)
    h = enc.health()
    assert h["ok"] is True
    assert h["backend"] == "mock"
    assert h.get("requested_backend") == "nemotron"
    assert "mock fallback" in (h.get("error") or "")
    # Still produces deterministic unit vectors.
    _assert_unit(enc.encode_text("fallback works"))
    enc.close()


def test_open_encoder_invalid_backend():
    with pytest.raises(ValueError, match="embed_backend"):
        open_encoder(backend="openai")


def test_select_device_stub():
    assert select_device("cpu") == "cpu"
    assert select_device("auto") == "unavailable"
    assert select_device("cuda") == "unavailable"
    assert select_device("rocm") == "unavailable"
    with pytest.raises(ValueError, match="embed device"):
        select_device("tpu")


def test_encode_atom_inputs_helper_via_open_encoder():
    enc = open_encoder(MemorySettings(embed_backend="mock"))
    result = encode_atom_inputs(enc, "a_h", text="helper path")
    assert result.status == "ready"
    assert result.embeddings is not None
    assert result.embeddings.atom_id == "a_h"
    enc.close()


def test_channels_constant_exported():
    assert "joint" in CHANNELS
    assert len(CHANNELS) == 5


def test_mock_vector_near_orthogonal_for_distinct_seeds():
    """Sanity: different seeds should not be near-identical (hash quality)."""
    a = mock_vector("alpha")
    b = mock_vector("beta")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    # Unit vectors: |dot| << 1 for random-ish high-d vectors.
    assert abs(dot) < 0.2, f"unexpectedly aligned mock vectors: dot={dot}"
    # Self-dot ≈ 1.
    assert abs(sum(x * x for x in a) - 1.0) < 1e-6
