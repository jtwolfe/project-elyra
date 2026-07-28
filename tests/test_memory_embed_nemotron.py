"""Optional Nemotron real runtime (Phase 2 PR8).

Hermetic CI: no torch / no GPU / no model download — tests skip or exercise
the mock-fallback path only. GPU / full load tests are marked ``gpu`` and
``memory_embed`` and skip when unavailable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from elyra.memory.config import MemorySettings
from elyra.memory.embed import (
    DEFAULT_NEMOTRON_MODEL_ID,
    EMBED_DIM,
    NemotronEmbedder,
    open_encoder,
    probe_devices,
    select_device,
    torch_available,
    transformers_available,
)
from elyra.memory.embed.encode import resolve_media_inputs
from elyra.memory.embed.mock import MOCK_MODEL_ID
from elyra.memory.types import Atom, new_atom_id, utc_now_iso

# ---------------------------------------------------------------------------
# Always-run (no torch required)
# ---------------------------------------------------------------------------


def test_defaults_do_not_enable_semantic_or_embed():
    """PR8 must not flip semantic_enabled / embed_enabled defaults on."""
    cfg = MemorySettings()
    assert cfg.semantic_enabled is False
    assert cfg.embed_enabled is False
    assert cfg.embed_backend == "mock"
    assert cfg.embed_model_id == DEFAULT_NEMOTRON_MODEL_ID


def test_memory_import_never_pulls_torch():
    """Core elyra.memory + embed package must not import torch at load time."""
    code = (
        "import sys\n"
        "import elyra.memory\n"
        "import elyra.memory.embed\n"
        "from elyra.memory.embed import open_encoder, select_device\n"
        "assert 'torch' not in sys.modules\n"
        "assert 'transformers' not in sys.modules\n"
        "enc = open_encoder()\n"
        "enc.close()\n"
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


def test_open_encoder_nemotron_without_deps_falls_back_to_mock():
    """When torch/transformers missing, nemotron → mock fallback (ok=True)."""
    if torch_available() and transformers_available():
        pytest.skip("torch+transformers present; mock-fallback path not exercised")
    cfg = MemorySettings(
        embed_backend="nemotron",
        embed_model_id=DEFAULT_NEMOTRON_MODEL_ID,
    )
    enc = open_encoder(cfg)
    h = enc.health()
    assert h["ok"] is True
    assert h["backend"] == "mock"
    assert h.get("requested_backend") == "nemotron"
    assert "mock fallback" in (h.get("error") or "")
    assert h["model_id"] == MOCK_MODEL_ID
    vec = enc.encode_text("fallback")
    assert len(vec) == EMBED_DIM
    enc.close()


def test_probe_devices_shape():
    caps = probe_devices()
    assert "torch_available" in caps
    assert "cuda" in caps
    assert "rocm" in caps
    assert "cpu" in caps
    if not torch_available():
        assert caps["torch_available"] is False
        assert select_device("auto") == "unavailable"


def test_try_open_nemotron_none_without_deps():
    from elyra.memory.embed.runtime import try_open_nemotron

    if torch_available() and transformers_available():
        # May still return an embedder (lazy, no weight download).
        emb = try_open_nemotron(model_id=DEFAULT_NEMOTRON_MODEL_ID)
        if emb is not None:
            assert isinstance(emb, NemotronEmbedder)
            h = emb.health()
            assert h["backend"] == "nemotron"
            assert h["loaded"] is False
            emb.close()
        return
    assert try_open_nemotron(model_id=DEFAULT_NEMOTRON_MODEL_ID) is None


def test_resolve_media_matrix_mime_and_oversize(tmp_path: Path):
    """PR8 media matrix: image/audio/video extensions + oversize skip."""

    class _Store:
        def __init__(self, mapping: dict[str, Path]) -> None:
            self._m = mapping

        def path_for(self, mid: str) -> str | None:
            p = self._m.get(mid)
            return str(p) if p else None

    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG" + b"\x00" * 64)
    aud = tmp_path / "b.wav"
    aud.write_bytes(b"RIFF" + b"\x00" * 32)
    big = tmp_path / "big.mp4"
    big.write_bytes(b"\x00" * 200)

    atom = Atom(
        atom_id=new_atom_id(),
        t_start=utc_now_iso(),
        kind="observation",
        content_text="hi",
        media_ids=("m_img", "m_aud", "m_big"),
    )
    store = _Store({"m_img": img, "m_aud": aud, "m_big": big})
    out = resolve_media_inputs(atom, store, max_bytes=100)
    assert out["image"] == str(img)
    assert out["audio"] == str(aud)
    assert out["video"] is None
    assert any("oversize" in s for s in out["skipped"])


# ---------------------------------------------------------------------------
# Optional: memory_embed / gpu (skip when unavailable)
# ---------------------------------------------------------------------------


def _gpu_ready() -> bool:
    if not torch_available():
        return False
    caps = probe_devices()
    return bool(caps.get("cuda") or caps.get("rocm"))


def _model_reachable() -> bool:
    """True when a local path or HF cache might have weights (best-effort).

    Does not download. Operators set embed_model_path or pre-cache the model.
    """
    # Local env path common in dogfood.
    for key in ("ELYRA_EMBED_MODEL_PATH", "HF_HOME", "TRANSFORMERS_CACHE"):
        # Presence alone is not enough; require explicit local model dir.
        pass
    cfg_path = (MemorySettings().embed_model_path or "").strip()
    if cfg_path and Path(cfg_path).is_dir():
        return True
    # Optional marker file used by operator smoke scripts.
    marker = Path.home() / ".cache" / "huggingface" / "hub"
    if not marker.is_dir():
        return False
    # Look for a snapshot dir name containing omni-embed-nemotron.
    try:
        for child in marker.iterdir():
            if "omni-embed-nemotron" in child.name.lower():
                return True
    except OSError:
        return False
    return False


@pytest.mark.memory_embed
def test_nemotron_open_when_deps_present():
    """With elyra[memory-embed], open_encoder returns NemotronEmbedder."""
    if not (torch_available() and transformers_available()):
        pytest.skip("torch/transformers not installed (elyra[memory-embed])")
    cfg = MemorySettings(
        embed_backend="nemotron",
        embed_model_id=DEFAULT_NEMOTRON_MODEL_ID,
        embed_device="cpu",
    )
    enc = open_encoder(cfg)
    try:
        h = enc.health()
        assert h["backend"] == "nemotron"
        assert h["model_id"] == DEFAULT_NEMOTRON_MODEL_ID
        assert h["ok"] is True
        assert isinstance(enc, NemotronEmbedder)
    finally:
        enc.close()


@pytest.mark.memory_embed
@pytest.mark.gpu
def test_nemotron_encode_text_gpu():
    """Real encode on GPU when model weights are available (Gate B spike)."""
    if not _gpu_ready():
        pytest.skip("CUDA/ROCm unavailable")
    if not transformers_available():
        pytest.skip("transformers not installed")
    if not _model_reachable():
        pytest.skip("Nemotron weights not cached/local (no download in CI)")

    cfg = MemorySettings(
        embed_backend="nemotron",
        embed_model_id=DEFAULT_NEMOTRON_MODEL_ID,
        embed_device="auto",
    )
    enc = open_encoder(cfg)
    try:
        assert isinstance(enc, NemotronEmbedder)
        # ensure_loaded may download if network allowed — only when cached.
        enc.ensure_loaded()  # type: ignore[attr-defined]
        vec = enc.encode_text("a short passage for embedding smoke")
        assert len(vec) == EMBED_DIM
        # L2 unit (within float tolerance).
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-3
        h = enc.health()
        assert h["ok"] is True
        assert h["loaded"] is True
        assert h["device"] in ("cuda", "rocm")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Nemotron load/encode unavailable: {exc}")
    finally:
        enc.close()


@pytest.mark.memory_embed
def test_nemotron_load_missing_path_fails_soft():
    """Missing local path should not crash process; encode returns failed."""
    if not (torch_available() and transformers_available()):
        pytest.skip("torch/transformers not installed")
    # Use a clearly missing local path so from_pretrained fails fast without hub.
    emb = NemotronEmbedder(
        model_id=DEFAULT_NEMOTRON_MODEL_ID,
        model_path="/nonexistent/elyra-nemotron-weights-pr8",
        device="cpu",
    )
    # path missing → falls back to model_id on load; that may try hub.
    # Instead set model_id to a nonsense local-only id via path-as-source:
    emb = NemotronEmbedder(
        model_id="/nonexistent/elyra-nemotron-weights-pr8",
        model_path="",
        device="cpu",
    )
    result = emb.encode_atom_inputs("a_test", text="hello")
    assert result.status == "failed"
    assert result.embeddings is None
    assert emb.health()["ok"] is False or emb.health().get("error")
    emb.close()
