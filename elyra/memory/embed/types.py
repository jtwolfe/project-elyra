"""Pure embed contract types (Phase 2 PR1).

Scope: channel names, dim, EmbeddingSet, EncodeResult, DeviceKind.
In scope: pure data + small helpers (L2 norm, channel presence).
Out of scope: torch, store I/O, queue, meal.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

# Normative multi-embedding channels bonded to one atom (KD7).
CHANNELS: tuple[str, ...] = ("text", "image", "audio", "video", "joint")
CHANNEL_SET: frozenset[str] = frozenset(CHANNELS)
# Search request set includes product ``auto`` resolve (KD-R2); not a column.
SEARCH_CHANNELS: tuple[str, ...] = CHANNELS + ("auto",)
SEARCH_CHANNEL_SET: frozenset[str] = frozenset(SEARCH_CHANNELS)

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


def _media_present(value: bytes | str | None) -> bool:
    """True when media input has content (non-empty bytes or non-blank path)."""
    if value is None:
        return False
    if isinstance(value, (bytes, bytearray)):
        return len(value) > 0
    return bool(str(value).strip())


@dataclass(frozen=True)
class ModalityParts:
    """Optional per-modality inputs for joint encode."""

    text: str | None = None
    image: bytes | str | None = None  # bytes or filesystem path
    audio: bytes | str | None = None
    video: bytes | str | None = None

    def present_modalities(self) -> tuple[str, ...]:
        """Return non-joint channel names that have content.

        Text requires non-whitespace content; media requires non-empty bytes
        or a non-blank path string (empty ``b""`` is absent).
        """
        out: list[str] = []
        if self.text is not None and str(self.text).strip():
            out.append("text")
        if _media_present(self.image):
            out.append("image")
        if _media_present(self.audio):
            out.append("audio")
        if _media_present(self.video):
            out.append("video")
        return tuple(out)


def _validate_channel_vector(
    name: str, vec: tuple[float, ...] | None, dim: int
) -> None:
    """Raise ValueError if ``vec`` is present but wrong length or non-finite."""
    if vec is None:
        return
    if len(vec) != dim:
        raise ValueError(
            f"emb_{name}: expected length {dim}, got {len(vec)}"
        )
    for i, x in enumerate(vec):
        f = float(x)
        if not math.isfinite(f):
            raise ValueError(
                f"emb_{name}: non-finite value at index {i}: {x!r}"
            )


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
        if self.dim < 1:
            raise ValueError(f"dim must be >= 1, got {self.dim}")
        # Shape / finite checks before deriving presence (PR3 upsert hand-off).
        for name, vec in (
            ("text", self.emb_text),
            ("image", self.emb_image),
            ("audio", self.emb_audio),
            ("video", self.emb_video),
            ("joint", self.emb_joint),
        ):
            _validate_channel_vector(name, vec, self.dim)

        derived: list[str] = []
        for name, vec in (
            ("text", self.emb_text),
            ("image", self.emb_image),
            ("audio", self.emb_audio),
            ("video", self.emb_video),
            ("joint", self.emb_joint),
        ):
            if vec is not None:
                derived.append(name)
        derived_t = tuple(derived)

        if not self.channels_present:
            object.__setattr__(self, "channels_present", derived_t)
        else:
            # Declared channels must match non-None vectors (no phantom presence).
            declared = tuple(c for c in self.channels_present if c in CHANNEL_SET)
            if set(declared) != set(derived_t):
                raise ValueError(
                    f"channels_present {self.channels_present!r} does not match "
                    f"non-None vectors {derived_t!r}"
                )
            object.__setattr__(self, "channels_present", derived_t)

    def channel_vector(self, channel: str) -> tuple[float, ...] | None:
        """Return the vector for ``channel`` or None."""
        if channel not in CHANNEL_SET:
            raise ValueError(f"unknown embed channel: {channel!r}")
        return getattr(self, f"emb_{channel}")

    def has_any_vector(self) -> bool:
        """True when at least one channel vector is non-None (not declared-only)."""
        return (
            self.emb_text is not None
            or self.emb_image is not None
            or self.emb_audio is not None
            or self.emb_video is not None
            or self.emb_joint is not None
        )

    def is_ready(self) -> bool:
        """KD20: index may mark atom ready when joint or single-modality vector present."""
        return embeddings_are_ready(self)


def should_write_joint(
    present: Sequence[str],
    *,
    want_joint: bool | None = None,
    single_modality_joint: bool = True,
) -> bool:
    """Whether to populate ``emb_joint`` (not *how* — see joint_vector helpers).

    KD-R1: multi-mod always; single-mod when ``single_modality_joint`` (default
    True). Explicit ``want_joint`` overrides the heuristic.
    """
    if want_joint is not None:
        return bool(want_joint)
    n = len(present)
    if n >= 2:
        return True
    if n == 1 and single_modality_joint:
        return True
    return False


def joint_vector_for_modalities(
    *,
    present: Sequence[str],
    modality_vectors: Mapping[str, Sequence[float]],
    encode_joint_fn: Callable[..., Sequence[float]],
    parts: Any,
    want_joint: bool | None = None,
    single_modality_joint: bool = True,
) -> tuple[float, ...] | None:
    """Produce ``emb_joint``: single-mod → copy sole vector; multi → encode_joint.

    Never calls ``encode_joint`` when ``len(present) == 1`` so free-text
    ``encode_text`` queries stay cosine-aligned with corpus joint (KD-R1).
    """
    if not should_write_joint(
        present,
        want_joint=want_joint,
        single_modality_joint=single_modality_joint,
    ):
        return None
    if len(present) == 1:
        sole = present[0]
        vec = modality_vectors[sole]
        return tuple(float(x) for x in vec)
    return tuple(float(x) for x in encode_joint_fn(parts))


def sole_non_joint_vector(
    emb: EmbeddingSet,
) -> tuple[str, tuple[float, ...]] | None:
    """Return ``(channel, vector)`` when exactly one non-joint modality is set."""
    found: list[tuple[str, tuple[float, ...]]] = []
    for ch in ("text", "image", "audio", "video"):
        vec = emb.channel_vector(ch)
        if vec is not None:
            found.append((ch, vec))
    if len(found) == 1:
        return found[0]
    return None


def joint_copy_embedding_set(emb: EmbeddingSet) -> EmbeddingSet | None:
    """If emb is sole-modality without joint, return a copy with joint filled.

    No encoder. Returns None when not eligible (already has joint, multi-mod,
    or no vectors). Repair path (KD-R11) and tests share this helper.
    """
    if emb.emb_joint is not None:
        return None
    sole = sole_non_joint_vector(emb)
    if sole is None:
        return None
    _ch, vec = sole
    joint = tuple(float(x) for x in vec)
    return EmbeddingSet(
        atom_id=emb.atom_id,
        dim=emb.dim,
        emb_text=emb.emb_text,
        emb_image=emb.emb_image,
        emb_audio=emb.emb_audio,
        emb_video=emb.emb_video,
        emb_joint=joint,
        model_id=emb.model_id,
        encoded_at=emb.encoded_at,
    )


def embeddings_are_ready(
    emb: EmbeddingSet,
    *,
    require_joint: bool = False,
) -> bool:
    """KD20 ready rule for EmbeddingIndex upsert.

    True when ``emb_joint`` is present, **or** (legacy / repair path) exactly
    one non-joint modality vector is present. When ``require_joint=True``
    (new encodes with ``embed_joint_for_single_modality`` — OQ-R4), joint is
    mandatory; sole-modality without joint is not ready.
    """
    if emb.emb_joint is not None:
        return True
    if require_joint:
        return False
    non_joint = [c for c in emb.channels_present if c != "joint"]
    if len(non_joint) == 1 and emb.channel_vector(non_joint[0]) is not None:
        return True
    return False


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
    "SEARCH_CHANNEL_SET",
    "SEARCH_CHANNELS",
    "DeviceKind",
    "EmbedBackend",
    "EmbedDevicePref",
    "EmbeddingSet",
    "EncodeResult",
    "EncodeStatus",
    "ModalityParts",
    "embedding_set_from_mapping",
    "embeddings_are_ready",
    "joint_copy_embedding_set",
    "joint_vector_for_modalities",
    "l2_normalize",
    "should_write_joint",
    "sole_non_joint_vector",
    "vector_l2_norm",
]
