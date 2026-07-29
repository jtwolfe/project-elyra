"""Mock encoder: deterministic 2048-d L2 vectors; open_encoder mock path."""

from __future__ import annotations

import math
import subprocess
import sys
from typing import Any

import pytest

from elyra.memory.config import MEMORY_EMBED_BACKENDS, MEMORY_EMBED_DEVICES, MemorySettings
from elyra.memory.embed import (
    CHANNELS,
    EMBED_BACKENDS,
    EMBED_DEVICE_PREFS,
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


def test_encode_joint_ignores_empty_media():
    """Empty media must not pollute joint seeds (align with present_modalities)."""
    enc = MockEmbedder()
    full = ModalityParts(text="hi", image=b"png", audio=None, video=None)
    empty_audio = ModalityParts(text="hi", image=b"png", audio=b"", video=None)
    blank_path = ModalityParts(text="hi", image=b"png", audio="  ", video="")
    j_full = enc.encode_joint(full)
    assert j_full == enc.encode_joint(empty_audio)
    assert j_full == enc.encode_joint(blank_path)
    # Real empty-optional via encode_atom_inputs joint matches content-only.
    r = enc.encode_atom_inputs("a_j", text="hi", image=b"png", audio=b"")
    assert r.status == "ready"
    assert r.embeddings is not None
    assert set(r.channels_encoded) == {"text", "image", "joint"}
    assert list(r.embeddings.emb_joint) == j_full
    # Non-empty audio still changes the joint.
    with_audio = ModalityParts(text="hi", image=b"png", audio=b"wav")
    assert enc.encode_joint(with_audio) != j_full


def test_encode_atom_inputs_text_only_joint_copy():
    """KD-R1: single-modality → emb_joint is elementwise copy of emb_text."""
    enc = MockEmbedder()
    result = enc.encode_atom_inputs("a_1", text="only text")
    assert result.status == "ready"
    assert result.embeddings is not None
    assert result.embeddings.emb_text is not None
    assert result.embeddings.emb_joint is not None
    assert "text" in result.channels_encoded
    assert "joint" in result.channels_encoded
    _assert_unit(result.embeddings.emb_text)
    # Elementwise equal (not encode_joint which seeds differently).
    assert result.embeddings.emb_joint == result.embeddings.emb_text
    # encode_joint would diverge from free-text encode_text.
    from elyra.memory.embed.types import ModalityParts

    joint_encoded = enc.encode_joint(ModalityParts(text="only text"))
    assert tuple(joint_encoded) != result.embeddings.emb_joint


def test_encode_atom_inputs_text_only_joint_disabled():
    enc = MockEmbedder()
    result = enc.encode_atom_inputs(
        "a_1b", text="only text", single_modality_joint=False
    )
    assert result.status == "ready"
    assert result.embeddings is not None
    assert result.embeddings.emb_text is not None
    assert result.embeddings.emb_joint is None
    assert "joint" not in result.channels_encoded


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
    assert h["model_id"] == MOCK_MODEL_ID
    v = enc.encode_text("ping")
    _assert_unit(v)
    enc.close()


def test_open_encoder_mock_model_id_not_nemotron_pin():
    """Settings.embed_model_id is the Nemotron pin; mock vectors use MOCK_MODEL_ID."""
    cfg = MemorySettings(
        embed_backend="mock",
        embed_model_id="nvidia/omni-embed-nemotron-3b",
        embed_device="cpu",
    )
    enc = open_encoder(cfg)
    assert enc.health()["model_id"] == MOCK_MODEL_ID
    assert enc.health()["device"] == "cpu"
    enc.close()
    # Explicit model_id= override still honored.
    enc2 = open_encoder(cfg, model_id="custom/mock-id")
    assert enc2.health()["model_id"] == "custom/mock-id"
    enc2.close()


def test_open_encoder_default_settings_mock_model_id():
    enc = open_encoder(MemorySettings(embed_backend="mock"))
    assert enc.health()["model_id"] == MOCK_MODEL_ID
    enc.close()


def test_open_encoder_nemotron_falls_back_to_mock():
    """When torch/transformers missing: nemotron → mock fallback.

    When deps are present (operator box with elyra[memory-embed]), open returns
    a real NemotronEmbedder instead — covered by test_memory_embed_nemotron.
    """
    from elyra.memory.embed.runtime import (
        NemotronEmbedder,
        torch_available,
        transformers_available,
    )

    cfg = MemorySettings(
        embed_backend="nemotron",
        embed_model_id="nvidia/omni-embed-nemotron-3b",
    )
    enc = open_encoder(cfg)
    h = enc.health()
    assert h["ok"] is True
    if torch_available() and transformers_available():
        assert h["backend"] == "nemotron"
        assert isinstance(enc, NemotronEmbedder)
        assert h["model_id"] == "nvidia/omni-embed-nemotron-3b"
        # Do not call encode_text here (would try to load weights).
    else:
        assert h["backend"] == "mock"
        assert h.get("requested_backend") == "nemotron"
        assert h["model_id"] == MOCK_MODEL_ID
        assert h.get("requested_model_id") == "nvidia/omni-embed-nemotron-3b"
        assert "mock fallback" in (h.get("error") or "")
        _assert_unit(enc.encode_text("fallback works"))
    enc.close()


def test_open_encoder_invalid_backend():
    with pytest.raises(ValueError, match="embed_backend"):
        open_encoder(backend="openai")


def test_select_device_probe():
    """PR8: real probe; without torch → unavailable for auto/cuda/rocm."""
    assert select_device("cpu") == "cpu"
    # Without torch (hermetic CI), auto/cuda/rocm → unavailable.
    # With torch-only CPU, auto → cpu (covered in nemotron tests when present).
    from elyra.memory.embed.runtime import probe_devices, torch_available

    caps = probe_devices()
    if not torch_available() or not caps.get("torch_available"):
        assert select_device("auto") == "unavailable"
        assert select_device("cuda") == "unavailable"
        assert select_device("rocm") == "unavailable"
    else:
        # Torch present: auto prefers cuda → rocm → cpu.
        auto = select_device("auto")
        assert auto in ("cuda", "rocm", "cpu")
        if caps.get("cuda"):
            assert select_device("cuda") == "cuda"
        else:
            assert select_device("cuda") == "unavailable"
        if caps.get("rocm"):
            assert select_device("rocm") == "rocm"
        else:
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


def test_embed_import_does_not_load_torch():
    """KD13: package load must not pull torch (subprocess = clean modules)."""
    code = (
        "import sys\n"
        "import elyra.memory\n"
        "import elyra.memory.embed\n"
        "assert 'torch' not in sys.modules, sorted(sys.modules)[:20]\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "ok" in proc.stdout


def test_embed_allowlists_single_source():
    """config re-exports embed.types allowlists (same object, no drift)."""
    assert MEMORY_EMBED_BACKENDS is EMBED_BACKENDS
    assert MEMORY_EMBED_DEVICES is EMBED_DEVICE_PREFS
    assert MEMORY_EMBED_BACKENDS == frozenset({"mock", "nemotron"})
    assert MEMORY_EMBED_DEVICES == frozenset({"auto", "cuda", "rocm", "cpu"})


class _ProtocolOnlyEmbedder:
    """Minimal Embedder without encode_atom_inputs (fallback path)."""

    def __init__(self) -> None:
        self._inner = MockEmbedder()

    def health(self) -> dict[str, Any]:
        return self._inner.health()

    def encode_text(self, text: str) -> list[float]:
        return self._inner.encode_text(text)

    def encode_image(self, path_or_bytes: bytes | str) -> list[float]:
        return self._inner.encode_image(path_or_bytes)

    def encode_audio(self, path_or_bytes: bytes | str) -> list[float]:
        return self._inner.encode_audio(path_or_bytes)

    def encode_video(self, path_or_bytes: bytes | str) -> list[float]:
        return self._inner.encode_video(path_or_bytes)

    def encode_joint(self, parts: ModalityParts) -> list[float]:
        return self._inner.encode_joint(parts)

    def close(self) -> None:
        self._inner.close()


def test_encode_atom_inputs_protocol_fallback_text_only():
    enc = _ProtocolOnlyEmbedder()
    result = encode_atom_inputs(enc, "a_proto", text="fallback text")
    assert result.status == "ready"
    assert result.embeddings is not None
    assert result.embeddings.emb_text is not None
    # KD-R1: protocol fallback also joint-copies single modality.
    assert result.embeddings.emb_joint is not None
    assert result.embeddings.emb_joint == result.embeddings.emb_text
    assert result.embeddings.encoded_at  # set by helper
    assert "T" in result.embeddings.encoded_at
    _assert_unit(result.embeddings.emb_text)
    enc.close()


def test_encode_atom_inputs_protocol_fallback_multimodal_joint():
    enc = _ProtocolOnlyEmbedder()
    result = encode_atom_inputs(
        enc, "a_multi", text="cap", image=b"png-bytes"
    )
    assert result.status == "ready"
    emb = result.embeddings
    assert emb is not None
    assert emb.emb_text is not None
    assert emb.emb_image is not None
    assert emb.emb_joint is not None
    assert emb.encoded_at
    enc.close()


def test_encode_atom_inputs_protocol_fallback_empty_skipped():
    enc = _ProtocolOnlyEmbedder()
    result = encode_atom_inputs(enc, "a_skip", text="  ", image=b"")
    assert result.status == "skipped"
    assert result.embeddings is None
    enc.close()
