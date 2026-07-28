"""Deterministic mock encoder for CI (Phase 2 PR1).

Scope: hash → 2048-d L2-normalized vectors; no torch / no network.
In scope: MockEmbedder implementing the embed runtime contract.
Out of scope: real Nemotron, queue, store upsert.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Any

from elyra.memory.embed.types import (
    EMBED_DIM,
    EmbeddingSet,
    EncodeResult,
    ModalityParts,
    l2_normalize,
)
from elyra.memory.types import utc_now_iso

MOCK_MODEL_ID = "mock/hash-embed-v1"


def mock_vector(seed: str, *, dim: int = EMBED_DIM) -> tuple[float, ...]:
    """Deterministic L2-normalized ``dim``-vector from ``seed`` string.

    Uses SHA-256 streaming expansion so the same seed always yields the same
    unit vector (hermetic tests; no torch).
    """
    if dim < 1:
        raise ValueError(f"dim must be >= 1, got {dim}")
    if not isinstance(seed, str):
        seed = str(seed)
    # Expand digest stream until we have dim floats in (-1, 1).
    out: list[float] = []
    counter = 0
    while len(out) < dim:
        block = hashlib.sha256(f"{seed}|{counter}".encode("utf-8")).digest()
        counter += 1
        # 8 bytes → one float64 bits; map to (-1, 1) via int32 pairs.
        for i in range(0, len(block) - 3, 4):
            if len(out) >= dim:
                break
            (u,) = struct.unpack_from(">I", block, i)
            # Map uint32 → (-1, 1) open interval (avoid exact ±1 clustering).
            out.append((u / 4294967295.0) * 2.0 - 1.0)
    return l2_normalize(out[:dim])


def _seed_for_bytes(prefix: str, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    return f"{prefix}|sha256:{digest}"


def _seed_for_path_or_bytes(prefix: str, path_or_bytes: bytes | str) -> str:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        return _seed_for_bytes(prefix, bytes(path_or_bytes))
    path = Path(str(path_or_bytes))
    if path.is_file():
        # Hash file contents (same seed form as raw bytes) so path vs bytes
        # of identical content produce the same mock vector.
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return f"{prefix}|sha256:{h.hexdigest()}"
    # Path missing or not a file — seed from the string itself.
    return f"{prefix}|path:{path_or_bytes}"


class MockEmbedder:
    """Fake embedder: stable hash → unit 2048-d vectors (no torch).

    Implements the embed runtime contract used by ``open_encoder`` and later
    encode-queue drain. Joint channel seeds from sorted modality seeds so order
    of parts does not change the joint vector.
    """

    def __init__(
        self,
        *,
        dim: int = EMBED_DIM,
        model_id: str = MOCK_MODEL_ID,
        device: str = "cpu",
    ) -> None:
        self._dim = dim
        self._model_id = model_id
        self._device = device
        self._closed = False

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_id(self) -> str:
        return self._model_id

    def health(self) -> dict[str, Any]:
        return {
            "ok": not self._closed,
            "device": self._device,
            "model_id": self._model_id,
            "dim": self._dim,
            "backend": "mock",
            "error": "closed" if self._closed else None,
        }

    def encode_text(self, text: str) -> list[float]:
        self._ensure_open()
        return list(mock_vector(f"text|{text}", dim=self._dim))

    def encode_image(self, path_or_bytes: bytes | str) -> list[float]:
        self._ensure_open()
        seed = _seed_for_path_or_bytes("image", path_or_bytes)
        return list(mock_vector(seed, dim=self._dim))

    def encode_audio(self, path_or_bytes: bytes | str) -> list[float]:
        self._ensure_open()
        seed = _seed_for_path_or_bytes("audio", path_or_bytes)
        return list(mock_vector(seed, dim=self._dim))

    def encode_video(self, path_or_bytes: bytes | str) -> list[float]:
        self._ensure_open()
        seed = _seed_for_path_or_bytes("video", path_or_bytes)
        return list(mock_vector(seed, dim=self._dim))

    def encode_joint(self, parts: ModalityParts) -> list[float]:
        """Joint vector from sorted present modality seeds (order-stable)."""
        self._ensure_open()
        seeds: list[str] = []
        if parts.text is not None and str(parts.text).strip():
            seeds.append(f"text|{parts.text}")
        if parts.image is not None:
            seeds.append(_seed_for_path_or_bytes("image", parts.image))
        if parts.audio is not None:
            seeds.append(_seed_for_path_or_bytes("audio", parts.audio))
        if parts.video is not None:
            seeds.append(_seed_for_path_or_bytes("video", parts.video))
        if not seeds:
            # Empty joint → deterministic zero-content unit vector.
            return list(mock_vector("joint|empty", dim=self._dim))
        seeds.sort()
        combined = "joint|" + "||".join(seeds)
        return list(mock_vector(combined, dim=self._dim))

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
        """Encode present modalities into an EmbeddingSet.

        When ``want_joint`` is None, joint is produced when ≥2 modalities are
        present (eager joint policy — KD5 / design ready rule).
        """
        self._ensure_open()
        parts = ModalityParts(text=text, image=image, audio=audio, video=video)
        present = parts.present_modalities()
        if not present:
            return EncodeResult(
                status="skipped",
                embeddings=None,
                error="no modalities",
                channels_encoded=(),
            )

        emb_text = (
            tuple(self.encode_text(text)) if "text" in present else None
        )
        emb_image = (
            tuple(self.encode_image(image))  # type: ignore[arg-type]
            if "image" in present
            else None
        )
        emb_audio = (
            tuple(self.encode_audio(audio))  # type: ignore[arg-type]
            if "audio" in present
            else None
        )
        emb_video = (
            tuple(self.encode_video(video))  # type: ignore[arg-type]
            if "video" in present
            else None
        )

        do_joint = want_joint if want_joint is not None else len(present) >= 2
        emb_joint: tuple[float, ...] | None = None
        channels: list[str] = list(present)
        if do_joint:
            emb_joint = tuple(self.encode_joint(parts))
            channels.append("joint")

        emb = EmbeddingSet(
            atom_id=atom_id,
            dim=self._dim,
            emb_text=emb_text,
            emb_image=emb_image,
            emb_audio=emb_audio,
            emb_video=emb_video,
            emb_joint=emb_joint,
            model_id=self._model_id,
            encoded_at=utc_now_iso(),
            channels_present=tuple(channels),
        )
        return EncodeResult(
            status="ready",
            embeddings=emb,
            error=None,
            channels_encoded=emb.channels_present,
        )

    def close(self) -> None:
        self._closed = True

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("MockEmbedder is closed")


__all__ = [
    "MOCK_MODEL_ID",
    "MockEmbedder",
    "mock_vector",
]
