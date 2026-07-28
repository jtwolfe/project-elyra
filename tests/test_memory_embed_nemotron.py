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

    Does not download. Honours ``ELYRA_EMBED_MODEL_PATH``, settings
    ``embed_model_path``, or a hub cache dir name containing omni-embed-nemotron.
    """
    import os

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


def test_nemotron_media_soft_skip_without_mm_utils():
    """Without qwen_omni_utils, media soft-skips; text still encodes (Issue 1).

    Never label a text-only pool as emb_image / emb_joint.
    """
    emb = NemotronEmbedder(
        model_id=DEFAULT_NEMOTRON_MODEL_ID,
        device="cpu",
    )
    # Simulate loaded weights without mm packing (no real model).
    emb._loaded = True  # noqa: SLF001 — test harness
    emb._mm_info_fn = None  # noqa: SLF001
    emb._load_error = None  # noqa: SLF001

    # Stub encode_text so we do not need real weights.
    emb.encode_text = lambda text: [0.0] * (EMBED_DIM - 1) + [1.0]  # type: ignore[method-assign]

    result = emb.encode_atom_inputs(
        "a1",
        text="hello",
        image="/tmp/fake.png",
    )
    assert result.status == "ready"
    assert result.embeddings is not None
    assert result.embeddings.emb_text is not None
    assert result.embeddings.emb_image is None
    assert result.embeddings.emb_joint is None
    assert "text" in result.embeddings.channels_present
    assert "image" not in result.embeddings.channels_present
    assert "joint" not in result.embeddings.channels_present
    assert any(
        "mm_utils" in s for s in (result.meta.get("embed_media_skipped") or [])
    )

    media_only = emb.encode_atom_inputs("a2", image="/tmp/fake.png")
    assert media_only.status == "skipped"
    assert media_only.error == "media_mm_utils_unavailable"

    # Direct channel encode fails closed (no fake ready vector).
    with pytest.raises(RuntimeError, match="qwen_omni_utils"):
        emb.encode_image("/tmp/fake.png")
    emb.close()


def test_to_unit_list_dim_mismatch_raises():
    """Dim mismatch must not pad/truncate into a false unit vector (Issue 4)."""
    from elyra.memory.embed.runtime import _to_unit_list

    with pytest.raises(ValueError, match="dim mismatch"):
        _to_unit_list([1.0, 0.0, 0.0], dim=EMBED_DIM)
    short = _to_unit_list([3.0, 4.0], dim=2)
    assert abs(short[0] - 0.6) < 1e-6
    assert abs(short[1] - 0.8) < 1e-6


def test_open_encoder_mock_does_not_import_torch():
    """Mock backend must not probe/import torch (Issue 5)."""
    code = (
        "import sys\n"
        "from elyra.memory.config import MemorySettings\n"
        "from elyra.memory.embed import open_encoder\n"
        "enc = open_encoder(MemorySettings(embed_backend='mock', embed_device='auto'))\n"
        "assert 'torch' not in sys.modules\n"
        "assert enc.health()['device'] == 'cpu'\n"
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


def test_drain_threads_media_max_bytes(tmp_path: Path):
    """Queue drain passes settings.embed_media_max_bytes into encode (Issue 3)."""
    from elyra.config import resolve_paths
    from elyra.memory.embed.mock import MockEmbedder
    from elyra.memory.embed.queue import EncodeQueue
    from elyra.memory.store import open_memory_store

    class _Media:
        def __init__(self, path: Path) -> None:
            self.path = path

        def path_for(self, mid: str) -> str:
            return str(self.path)

    big = tmp_path / "clip.mp4"
    big.write_bytes(b"\x00" * 200)
    media = _Media(big)

    paths = resolve_paths(tmp_path / "home")
    paths.ensure_data_dirs()
    store = open_memory_store(
        paths, MemorySettings(write_atoms=True, backend="jsonl")
    )
    atom = Atom(
        atom_id=new_atom_id(),
        t_start=utc_now_iso(),
        kind="observation",
        content_text="with media",
        media_ids=("m1",),
        embedding_status="pending",
    )
    store.put_atom(atom)
    q = EncodeQueue(maxsize=8)
    q.enqueue(atom.atom_id)
    cfg = MemorySettings(
        embed_media_max_bytes=50,  # force oversize skip of video
        encode_max_items_per_tick=4,
        encode_max_ms_per_tick=5000,
        encode_max_attempts=3,
    )
    stats = q.drain(
        store,
        MockEmbedder(),
        index=None,
        media_store=media,
        settings=cfg,
    )
    assert stats["processed"] >= 1
    got = store.get_atom(atom.atom_id)
    assert got is not None
    # Text encode succeeds; video oversize skipped — no video channel.
    if got.meta.get("embed_encode_ok"):
        ch = got.meta.get("embed_channels") or []
        assert "video" not in ch
    store.close()


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
    """Real encode on GPU when model weights are available (Gate B spike).

    Load/encode environment failures → skip. Assertion failures → fail the test
    (Issue 2: never wrap asserts in bare ``except Exception: pytest.skip``).
    """
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
        try:
            enc.ensure_loaded()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — load/OOM/import only
            pytest.skip(f"Nemotron load unavailable: {exc}")
        try:
            vec = enc.encode_text("a short passage for embedding smoke")
        except Exception as exc:  # noqa: BLE001 — runtime encode only
            pytest.skip(f"Nemotron encode unavailable: {exc}")
        # Assertions outside soft-skip try — regressions fail the test.
        assert len(vec) == EMBED_DIM
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-3
        h = enc.health()
        assert h["ok"] is True
        assert h["loaded"] is True
        assert h["device"] in ("cuda", "rocm")
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
