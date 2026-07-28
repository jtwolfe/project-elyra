"""Pure embed contract types (Phase 2 PR1).

Scope: channel names, dim, EmbeddingSet, EncodeResult, DeviceKind.
In scope: pure data + small helpers (L2 norm, channel presence).
Out of scope: torch, store I/O, queue, meal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

# Normative multi-embedding channels bonded to one atom (KD7).
CHANNELS: tuple[str, ...] = ("text", "image", "audio", "video", "joint")
CHANNEL_SET: frozenset[str] = frozenset(CHANNELS)

# Omni-Embed-Nemotron output dim (verify against pinned revision at spike).
EMBED_DIM = 2048

DeviceKind = Literal["cuda", "rocm", "cpu", "unavailable"]
DEVICE_KINDS: frozenset[str] = frozenset({"cuda", "rocm", "cpu", "unavailable"})

# Preference / settings allowlist (includes auto for selection).
EmbedDevicePref = Literal["auto", "cuda", "rocm", "cpu"]
EMBED_DEVICE_PREFS: frozenset[str] = frozenset({"auto", "cuda", "rocm", "cpu"})

EmbedBackend = Literal["mock", "nemotron"]
EMBED_BACKENDS: frozenset[str] = frozenset({"mock", "nemotron"})

# Encode outcome statuses (subset of Atom.embedding_status for result records).
EncodeStatus = Literal["ready", "failed", "skipped"]
ENCODE_STATUSES: frozenset[str] = frozenset({"ready", "failed", "skipped"})


def l2_normalize(vec: Sequence[float], *, eps: float = 1e-12) -> tuple[float, ...]:
    """Return L2-normalized copy of ``vec`` as a tuple.

    Zero / near-zero vectors become a unit vector along dim 0 so callers always
    get a finite unit vector (mock encoder never returns the zero vector).
    """
    if not vec:
        raise ValueError("vector must be non-empty")
    s = math.sqrt(sum(float(x) * float(x) for x in vec))
    if s < eps:
        out = [0.0] * len(vec)
        out[0] = 1.0
        return tuple(out)
    inv = 1.0 / s
    return tuple(float(x) * inv for x in vec)


def vector_l2_norm(vec: Sequence[float]) -> float:
    """Return the L2 norm of ``vec``."""
    return math.sqrt(sum(float(x) * float(x) for x in vec))


@dataclass(frozen=True)
class ModalityParts:
    """Optional per-modality inputs for joint encode."""

    text: str | None = None
    image: bytes | str | None = None  # bytes or filesystem path
    audio: bytes | str | None = None
    video: bytes | str | None = None

    def present_modalities(self) -> tuple[str, ...]:
        """Return non-joint channel names that have content."""
        out: list[str] = []
        if self.text is not None and str(self.text).strip():
            out.append("text")
        if self.image is not None:
            out.append("image")
        if self.audio is not None:
            out.append("audio")
        if self.video is not None:
            out.append("video")
        return tuple(out)


@dataclass(frozen=True)
class EmbeddingSet:
    """Bonded multi-channel vectors for one atom (or parcel).

    Vectors are L2-normalized float tuples of length ``dim`` (default 2048).
    Absent channels are ``None``. No store I/O — pure data only.
    """

    atom_id: str
    dim: int = EMBED_DIM
    emb_text: tuple[float, ...] | None = None
    emb_image: tuple[float, ...] | None = None
    emb_audio: tuple[float, ...] | None = None
    emb_video: tuple[float, ...] | None = None
    emb_joint: tuple[float, ...] | None = None
    model_id: str = ""
    encoded_at: str = ""
    channels_present: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Derive channels_present if caller left it empty.
        if not self.channels_present:
            present: list[str] = []
            for name, vec in (
                ("text", self.emb_text),
                ("image", self.emb_image),
                ("audio", self.emb_audio),
                ("video", self.emb_video),
                ("joint", self.emb_joint),
            ):
                if vec is not None:
                    present.append(name)
            object.__setattr__(self, "channels_present", tuple(present))

    def channel_vector(self, channel: str) -> tuple[float, ...] | None:
        """Return the vector for ``channel`` or None."""
        if channel not in CHANNEL_SET:
            raise ValueError(f"unknown embed channel: {channel!r}")
        return getattr(self, f"emb_{channel}")

    def has_any_vector(self) -> bool:
        return bool(self.channels_present)


@dataclass(frozen=True)
class EncodeResult:
    """Result of an encode attempt for one atom.

    PR1/PR2: ``status`` may be ready/failed/skipped at the *encode* layer;
    Atom.embedding_status stays pending until an EmbeddingIndex upserts
    (PR3 marks atom ready). ``ready`` here means vectors were produced.
    """

    status: EncodeStatus | str
    embeddings: EmbeddingSet | None = None
    error: str | None = None
    channels_encoded: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "meta", dict(self.meta))
        if not self.channels_encoded and self.embeddings is not None:
            object.__setattr__(
                self, "channels_encoded", self.embeddings.channels_present
            )


def embedding_set_from_mapping(
    atom_id: str,
    data: Mapping[str, Any],
    *,
    dim: int = EMBED_DIM,
    model_id: str = "",
    encoded_at: str = "",
) -> EmbeddingSet:
    """Build EmbeddingSet from a channel→sequence mapping (test/helper)."""

    def _vec(key: str) -> tuple[float, ...] | None:
        raw = data.get(key)
        if raw is None:
            return None
        return tuple(float(x) for x in raw)

    return EmbeddingSet(
        atom_id=atom_id,
        dim=dim,
        emb_text=_vec("text") or _vec("emb_text"),
        emb_image=_vec("image") or _vec("emb_image"),
        emb_audio=_vec("audio") or _vec("emb_audio"),
        emb_video=_vec("video") or _vec("emb_video"),
        emb_joint=_vec("joint") or _vec("emb_joint"),
        model_id=model_id,
        encoded_at=encoded_at,
    )


__all__ = [
    "CHANNEL_SET",
    "CHANNELS",
    "DEVICE_KINDS",
    "EMBED_BACKENDS",
    "EMBED_DEVICE_PREFS",
    "EMBED_DIM",
    "ENCODE_STATUSES",
    "DeviceKind",
    "EmbedBackend",
    "EmbedDevicePref",
    "EmbeddingSet",
    "EncodeResult",
    "EncodeStatus",
    "ModalityParts",
    "embedding_set_from_mapping",
    "l2_normalize",
    "vector_l2_norm",
]
