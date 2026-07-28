"""Embed runtime: device stub + open_encoder (Phase 2 PR1).

Scope: device preference resolution stub, open_encoder mock path.
In scope: no torch import; mock always available; nemotron → mock fallback
until PR8 wires real weights.
Out of scope: real CUDA/ROCm probe, model load, encode queue.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from elyra.memory.config import MemorySettings
from elyra.memory.embed.mock import MOCK_MODEL_ID, MockEmbedder
from elyra.memory.embed.types import (
    EMBED_BACKENDS,
    EMBED_DEVICE_PREFS,
    EMBED_DIM,
    DeviceKind,
    EmbedBackend,
    EmbeddingSet,
    EncodeResult,
    ModalityParts,
)


@runtime_checkable
class Embedder(Protocol):
    """Portable encode contract (mock or future Nemotron)."""

    def health(self) -> dict[str, Any]:
        """``{ok, device, model_id, dim, backend, error?}``."""
        ...

    def encode_text(self, text: str) -> list[float]:
        ...

    def encode_image(self, path_or_bytes: bytes | str) -> list[float]:
        ...

    def encode_audio(self, path_or_bytes: bytes | str) -> list[float]:
        ...

    def encode_video(self, path_or_bytes: bytes | str) -> list[float]:
        ...

    def encode_joint(self, parts: ModalityParts) -> list[float]:
        ...

    def close(self) -> None:
        ...


def select_device(preference: str = "auto") -> DeviceKind:
    """Resolve device preference to a concrete ``DeviceKind`` (stub).

    PR1 does **not** probe torch/CUDA/ROCm (KD13 — no hard GPU imports).
    - ``cpu`` → ``cpu``
    - ``auto`` / ``cuda`` / ``rocm`` → ``unavailable`` until PR8 probe lands

    Callers that only need the mock encoder ignore the result; mock always
    reports ``device=cpu`` in health.
    """
    pref = (preference or "auto").strip().lower()
    if pref not in EMBED_DEVICE_PREFS:
        raise ValueError(
            f"embed device preference: expected one of "
            f"{sorted(EMBED_DEVICE_PREFS)}, got {preference!r}"
        )
    if pref == "cpu":
        return "cpu"
    # Real hardware selection deferred to PR8 (Nemotron runtime).
    return "unavailable"


def open_encoder(
    settings: MemorySettings | None = None,
    *,
    backend: str | None = None,
    device: str | None = None,
    model_id: str | None = None,
    dim: int = EMBED_DIM,
) -> Embedder:
    """Open an embedder for encode work.

    Mock path (this PR):
    - ``backend=mock`` (default) → :class:`MockEmbedder`
    - ``backend=nemotron`` without real runtime → **falls back to mock**
      (graceful; health reports backend mock + note in error field)

    Does not import torch. Safe under ``embed_enabled=false`` for tests that
    still want deterministic vectors.
    """
    cfg = settings or MemorySettings()
    be: str = backend if backend is not None else cfg.embed_backend
    be = (be or "mock").strip().lower()
    if be not in EMBED_BACKENDS:
        raise ValueError(
            f"embed_backend: expected one of {sorted(EMBED_BACKENDS)}, got {be!r}"
        )

    pref = device if device is not None else cfg.embed_device
    resolved = select_device(pref if pref else "auto")
    # Mock always runs on logical cpu; resolved hardware is informational.
    mock_device = "cpu" if resolved == "unavailable" else resolved

    mid = model_id if model_id is not None else cfg.embed_model_id
    if be == "mock":
        return MockEmbedder(
            dim=dim,
            model_id=mid or MOCK_MODEL_ID,
            device=mock_device,
        )

    # nemotron: PR8 will load real weights; until then mock fallback.
    return _UnavailableOrMockEmbedder(
        mock=MockEmbedder(
            dim=dim,
            model_id=mid or MOCK_MODEL_ID,
            device=mock_device,
        ),
        requested_backend=be,  # type: ignore[arg-type]
        device=resolved,
        model_id=mid or "",
    )


class _UnavailableOrMockEmbedder:
    """Nemotron-requested path before PR8: mock vectors + health note.

    Produces the same vectors as mock so CI and early dogfood keep working;
    health exposes ``requested_backend`` and a non-fatal note that real
    weights are not loaded.
    """

    def __init__(
        self,
        *,
        mock: MockEmbedder,
        requested_backend: EmbedBackend,
        device: DeviceKind,
        model_id: str,
    ) -> None:
        self._mock = mock
        self._requested_backend = requested_backend
        self._device = device
        self._model_id = model_id
        self._closed = False

    def health(self) -> dict[str, Any]:
        base = self._mock.health()
        base["ok"] = not self._closed
        base["backend"] = "mock"  # effective backend
        base["requested_backend"] = self._requested_backend
        base["device"] = self._device
        base["model_id"] = self._model_id or base.get("model_id")
        base["error"] = (
            "closed"
            if self._closed
            else "nemotron runtime not loaded; using mock fallback"
        )
        return base

    def encode_text(self, text: str) -> list[float]:
        return self._mock.encode_text(text)

    def encode_image(self, path_or_bytes: bytes | str) -> list[float]:
        return self._mock.encode_image(path_or_bytes)

    def encode_audio(self, path_or_bytes: bytes | str) -> list[float]:
        return self._mock.encode_audio(path_or_bytes)

    def encode_video(self, path_or_bytes: bytes | str) -> list[float]:
        return self._mock.encode_video(path_or_bytes)

    def encode_joint(self, parts: ModalityParts) -> list[float]:
        return self._mock.encode_joint(parts)

    def encode_atom_inputs(
        self,
        atom_id: str,
        *,
        text: str | None = None,
        image: bytes | str | None = None,
        audio: bytes | str | None = None,
        video: bytes | str | None = None,
        want_joint: bool | None = None,
    ) -> EncodeResult:
        return self._mock.encode_atom_inputs(
            atom_id,
            text=text,
            image=image,
            audio=audio,
            video=video,
            want_joint=want_joint,
        )

    def close(self) -> None:
        self._closed = True
        self._mock.close()


def encode_atom_inputs(
    embedder: Embedder,
    atom_id: str,
    *,
    text: str | None = None,
    image: bytes | str | None = None,
    audio: bytes | str | None = None,
    video: bytes | str | None = None,
    want_joint: bool | None = None,
) -> EncodeResult:
    """Encode modalities via embedder; prefer ``encode_atom_inputs`` if present.

    Narrow public helper so callers need not know MockEmbedder specifics.
    """
    encode_fn = getattr(embedder, "encode_atom_inputs", None)
    if callable(encode_fn):
        return encode_fn(
            atom_id,
            text=text,
            image=image,
            audio=audio,
            video=video,
            want_joint=want_joint,
        )
    # Protocol-only embedder: build EmbeddingSet from channel methods.
    parts = ModalityParts(text=text, image=image, audio=audio, video=video)
    present = parts.present_modalities()
    if not present:
        return EncodeResult(status="skipped", error="no modalities")

    emb_text = tuple(embedder.encode_text(text)) if "text" in present else None  # type: ignore[arg-type]
    emb_image = (
        tuple(embedder.encode_image(image)) if "image" in present else None  # type: ignore[arg-type]
    )
    emb_audio = (
        tuple(embedder.encode_audio(audio)) if "audio" in present else None  # type: ignore[arg-type]
    )
    emb_video = (
        tuple(embedder.encode_video(video)) if "video" in present else None  # type: ignore[arg-type]
    )
    do_joint = want_joint if want_joint is not None else len(present) >= 2
    emb_joint = tuple(embedder.encode_joint(parts)) if do_joint else None
    channels = list(present) + (["joint"] if do_joint else [])
    health = embedder.health()
    emb = EmbeddingSet(
        atom_id=atom_id,
        dim=int(health.get("dim") or EMBED_DIM),
        emb_text=emb_text,
        emb_image=emb_image,
        emb_audio=emb_audio,
        emb_video=emb_video,
        emb_joint=emb_joint,
        model_id=str(health.get("model_id") or ""),
        channels_present=tuple(channels),
    )
    return EncodeResult(
        status="ready",
        embeddings=emb,
        channels_encoded=emb.channels_present,
    )


__all__ = [
    "Embedder",
    "encode_atom_inputs",
    "open_encoder",
    "select_device",
]
