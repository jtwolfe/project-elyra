"""Embed runtime: device select, open_encoder, optional Nemotron path (Phase 2 PR8).

Scope: CUDA/ROCm/CPU device preference; mock always available; real
Omni-Embed-Nemotron behind lazy torch/transformers imports.
In scope: graceful fail → mock/unavailable when deps or weights missing;
never hard-fail ``import elyra.memory`` or top-level torch pull.
Out of scope: meal policy, queue, ANN (other PRs); default-on flags.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

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
    l2_normalize,
)
from elyra.memory.types import utc_now_iso

if TYPE_CHECKING:
    from elyra.memory.config import MemorySettings

_LOG = logging.getLogger(__name__)

# Default HF model id (revision pin documented in spike note; empty = hub default).
DEFAULT_NEMOTRON_MODEL_ID = "nvidia/omni-embed-nemotron-3b"
# Optional env override for device (escape hatch; prefer MemorySettings.embed_device).
_ENV_DEVICE = "ELYRA_EMBED_DEVICE"
# Documented first precision (spike: fp16 first; bf16/f32 fallbacks in load path).
NEMOTRON_DTYPE_PREFERENCE = ("float16", "bfloat16", "float32")


@runtime_checkable
class Embedder(Protocol):
    """Portable encode contract (mock or Nemotron)."""

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


# ---------------------------------------------------------------------------
# Device probe (lazy torch; never raises ImportError to callers)
# ---------------------------------------------------------------------------


def _try_import_torch() -> Any | None:
    """Return the torch module or None if missing / broken."""
    try:
        import torch  # type: ignore[import-not-found]

        return torch
    except Exception:  # noqa: BLE001 — optional dep
        return None


def _torch_backend_flags(torch: Any) -> tuple[bool, bool]:
    """Return ``(cuda_nvidia, rocm)`` capability flags from a loaded torch."""
    try:
        cuda_avail = bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False, False
    hip = getattr(getattr(torch, "version", None), "hip", None)
    # ROCm builds expose HIP via torch.cuda; version.hip is non-None.
    if hip:
        return False, bool(cuda_avail)
    cuda_ver = getattr(getattr(torch, "version", None), "cuda", None)
    if cuda_ver and cuda_avail:
        return True, False
    # Generic cuda-available without hip/cuda version metadata.
    return bool(cuda_avail), False


def probe_devices() -> dict[str, Any]:
    """Probe optional torch for device capabilities (no model load).

    Returns keys: ``torch_available``, ``cuda``, ``rocm``, ``cpu`` (always True
    when torch is present), ``error`` (optional).
    """
    torch = _try_import_torch()
    if torch is None:
        return {
            "torch_available": False,
            "cuda": False,
            "rocm": False,
            "cpu": False,
            "error": "torch not installed",
        }
    try:
        cuda, rocm = _torch_backend_flags(torch)
        return {
            "torch_available": True,
            "cuda": cuda,
            "rocm": rocm,
            "cpu": True,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "torch_available": True,
            "cuda": False,
            "rocm": False,
            "cpu": True,
            "error": f"{type(exc).__name__}: {exc}"[:200],
        }


def select_device(preference: str = "auto") -> DeviceKind:
    """Resolve device preference to a concrete ``DeviceKind``.

    Preference order for ``auto``: CUDA → ROCm → CPU → unavailable.
    Explicit ``cuda`` / ``rocm`` require that backend; otherwise unavailable.
    ``cpu`` always returns ``cpu`` (mock path) even without torch.
    """
    pref = (preference or "auto").strip().lower()
    # Env escape hatch (design: optional ELYRA_EMBED_DEVICE).
    env = (os.environ.get(_ENV_DEVICE) or "").strip().lower()
    if env and pref == "auto":
        pref = env
    if pref not in EMBED_DEVICE_PREFS:
        raise ValueError(
            f"embed device preference: expected one of "
            f"{sorted(EMBED_DEVICE_PREFS)}, got {preference!r}"
        )
    if pref == "cpu":
        return "cpu"

    caps = probe_devices()
    if pref == "cuda":
        return "cuda" if caps.get("cuda") else "unavailable"
    if pref == "rocm":
        return "rocm" if caps.get("rocm") else "unavailable"
    # auto
    if caps.get("cuda"):
        return "cuda"
    if caps.get("rocm"):
        return "rocm"
    if caps.get("cpu") or caps.get("torch_available"):
        return "cpu"
    return "unavailable"


def _torch_device_string(kind: DeviceKind) -> str:
    """Map DeviceKind to a torch device string."""
    if kind == "cuda":
        return "cuda"
    if kind == "rocm":
        # ROCm uses the cuda device namespace in PyTorch.
        return "cuda"
    return "cpu"


def transformers_available() -> bool:
    """True when the optional transformers package imports cleanly."""
    try:
        import transformers  # noqa: F401  # type: ignore[import-not-found]

        return True
    except Exception:  # noqa: BLE001
        return False


def torch_available() -> bool:
    """True when the optional torch package imports cleanly."""
    return _try_import_torch() is not None


# ---------------------------------------------------------------------------
# Nemotron embedder (optional heavy path)
# ---------------------------------------------------------------------------


class NemotronEmbedder:
    """Omni-Embed-Nemotron runtime: lazy load, fp16-first, portable device.

    Construction never downloads weights. ``ensure_loaded`` / first encode
    performs ``from_pretrained``. Failures set ``health()["ok"]=False`` and
    raise on encode so the queue can mark atoms failed/skipped.
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_NEMOTRON_MODEL_ID,
        model_path: str = "",
        device: DeviceKind = "cpu",
        dim: int = EMBED_DIM,
        dtype_name: str = "float16",
        trust_remote_code: bool = True,
    ) -> None:
        self._model_id = (model_id or DEFAULT_NEMOTRON_MODEL_ID).strip()
        self._model_path = (model_path or "").strip()
        self._device_kind: DeviceKind = device
        self._dim = int(dim)
        self._dtype_name = dtype_name
        self._trust_remote_code = trust_remote_code
        self._closed = False
        self._load_error: str | None = None
        self._loaded = False
        self._model: Any = None
        self._processor: Any = None
        self._torch: Any = None
        self._mm_info_fn: Any | None = None  # optional qwen_omni_utils

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    def health(self) -> dict[str, Any]:
        err: str | None
        if self._closed:
            err = "closed"
        elif self._load_error:
            err = self._load_error
        else:
            err = None
        ok = (not self._closed) and (self._load_error is None)
        # Before load, still "ok" if deps present — cold load may fail later.
        # After load attempt fails, ok=False.
        if not self._loaded and self._load_error is None and not self._closed:
            ok = True
        return {
            "ok": ok,
            "device": self._device_kind,
            "model_id": self._model_id,
            "dim": self._dim,
            "backend": "nemotron",
            "loaded": self._loaded,
            "dtype": self._dtype_name,
            "model_path": self._model_path or None,
            "media_encode": bool(self._mm_info_fn) if self._loaded else None,
            "error": err,
        }

    def ensure_loaded(self) -> None:
        """Load model + processor if not already loaded. Sets load_error on fail."""
        if self._closed:
            raise RuntimeError("NemotronEmbedder is closed")
        if self._loaded:
            return
        if self._load_error is not None:
            raise RuntimeError(self._load_error)
        try:
            self._load()
        except Exception as exc:  # noqa: BLE001
            msg = f"nemotron load failed: {type(exc).__name__}: {exc}"[:500]
            self._load_error = msg
            _LOG.exception("Nemotron model load failed model_id=%s", self._model_id)
            self._unload_quiet()
            raise RuntimeError(msg) from exc

    def _resolve_source(self) -> str:
        """Local path if configured and present, else HF model id."""
        if self._model_path:
            p = Path(self._model_path).expanduser()
            if p.is_dir():
                return str(p)
            _LOG.warning(
                "embed_model_path %s not a directory; using model_id %s",
                self._model_path,
                self._model_id,
            )
        return self._model_id

    def _pick_dtype(self, torch: Any) -> Any:
        """Prefer fp16, then bf16, then float32 (CPU often wants float32)."""
        order = list(NEMOTRON_DTYPE_PREFERENCE)
        # Honour explicit preference first.
        if self._dtype_name and self._dtype_name in order:
            order = [self._dtype_name] + [d for d in order if d != self._dtype_name]
        # On CPU, float16 matmul may be slow/unavailable — try float32 first.
        if self._device_kind == "cpu":
            order = ["float32", "float16", "bfloat16"]
        for name in order:
            dt = getattr(torch, name, None)
            if dt is not None:
                self._dtype_name = name
                return dt
        self._dtype_name = "float32"
        return torch.float32

    def _load(self) -> None:
        torch = _try_import_torch()
        if torch is None:
            raise RuntimeError("torch not installed (install elyra[memory-embed])")
        try:
            from transformers import AutoModel, AutoProcessor  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                "transformers not installed (install elyra[memory-embed])"
            ) from exc

        source = self._resolve_source()
        dtype = self._pick_dtype(torch)
        torch_device = _torch_device_string(self._device_kind)
        if self._device_kind == "unavailable":
            raise RuntimeError("no usable device for Nemotron (cuda/rocm/cpu)")

        # Attention backend: prefer flash_attention_2 when CUDA; fall back.
        attn_impls: list[str | None]
        if self._device_kind in ("cuda", "rocm"):
            attn_impls = ["flash_attention_2", "sdpa", "eager", None]
        else:
            attn_impls = ["sdpa", "eager", None]

        last_err: Exception | None = None
        model = None
        for attn in attn_impls:
            try:
                kwargs: dict[str, Any] = {
                    "torch_dtype": dtype,
                    "trust_remote_code": self._trust_remote_code,
                }
                if attn is not None:
                    kwargs["attn_implementation"] = attn
                model = AutoModel.from_pretrained(source, **kwargs)
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                _LOG.debug(
                    "Nemotron from_pretrained failed attn=%s: %s", attn, exc
                )
                model = None
        if model is None:
            raise RuntimeError(
                f"AutoModel.from_pretrained failed for {source!r}: {last_err}"
            )

        model = model.to(torch_device)
        model.eval()
        processor = AutoProcessor.from_pretrained(
            source, trust_remote_code=self._trust_remote_code
        )

        mm_fn = None
        try:
            from qwen_omni_utils import process_mm_info  # type: ignore[import-not-found]

            mm_fn = process_mm_info
        except Exception:  # noqa: BLE001
            _LOG.debug(
                "qwen_omni_utils unavailable; text encode only until installed"
            )

        self._torch = torch
        self._model = model
        self._processor = processor
        self._mm_info_fn = mm_fn
        self._loaded = True
        self._load_error = None
        _LOG.info(
            "Nemotron loaded model=%s device=%s dtype=%s dim=%s",
            source,
            self._device_kind,
            self._dtype_name,
            self._dim,
        )

    def _unload_quiet(self) -> None:
        self._model = None
        self._processor = None
        self._torch = None
        self._mm_info_fn = None
        self._loaded = False

    def close(self) -> None:
        self._closed = True
        self._unload_quiet()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("NemotronEmbedder is closed")
        self.ensure_loaded()

    def _device_obj(self) -> Any:
        assert self._torch is not None and self._model is not None
        try:
            return next(self._model.parameters()).device
        except Exception:  # noqa: BLE001
            return self._torch.device(_torch_device_string(self._device_kind))

    def _messages_for(
        self,
        *,
        text: str | None = None,
        image: bytes | str | None = None,
        audio: bytes | str | None = None,
        video: bytes | str | None = None,
        role_prefix: str = "passage",
    ) -> list[dict[str, Any]]:
        """Build chat-template message list for one multimodal sample."""
        content: list[dict[str, Any]] = []
        if text is not None and str(text).strip():
            body = str(text).strip()
            # Model card uses "passage: …" for document-side text.
            if role_prefix and not body.lower().startswith(("passage:", "query:")):
                body = f"{role_prefix}: {body}"
            content.append({"type": "text", "text": body})
        if image is not None:
            content.append({"type": "image", "image": _media_ref(image)})
        if audio is not None:
            content.append({"type": "audio", "audio": _media_ref(audio)})
        if video is not None:
            content.append({"type": "video", "video": _media_ref(video)})
        if not content:
            raise ValueError("no modalities for Nemotron encode")
        return [{"role": "user", "content": content}]

    def _embed_messages(self, messages: list[dict[str, Any]]) -> list[float]:
        """Run processor + model + mean-pool + L2 normalize → 2048-d list.

        When messages include media types, ``process_mm_info`` must succeed —
        never silently fall back to a text-only pool for a media encode.
        """
        self._ensure_open()
        torch = self._torch
        model = self._model
        processor = self._processor
        assert torch is not None and model is not None and processor is not None

        texts = processor.apply_chat_template(
            messages, add_generation_prompt=False, tokenize=False
        )
        if isinstance(texts, list):
            # Some processors return list[str] for batch of 1.
            text_in = texts
        else:
            text_in = [texts]

        wants_media = _messages_want_media(messages)
        images = None
        videos = None
        audio = None
        if wants_media:
            if self._mm_info_fn is None:
                raise RuntimeError(
                    "qwen_omni_utils unavailable; cannot encode media"
                )
            try:
                audio, images, videos = self._mm_info_fn(
                    messages, use_audio_in_video=False
                )
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(
                    f"process_mm_info failed: {type(exc).__name__}: {exc}"
                ) from exc

        text_kwargs = {
            "truncation": True,
            "padding": True,
            "max_length": 8192,
        }
        videos_kwargs = {
            "min_pixels": 32 * 14 * 14,
            "max_pixels": 64 * 28 * 28,
            "use_audio_in_video": False,
        }
        proc_kwargs: dict[str, Any] = {
            "text": text_in,
            "return_tensors": "pt",
            "text_kwargs": text_kwargs,
        }
        if images is not None:
            proc_kwargs["images"] = images
        if videos is not None:
            proc_kwargs["videos"] = videos
            proc_kwargs["videos_kwargs"] = videos_kwargs
        if audio is not None:
            proc_kwargs["audio"] = audio
            proc_kwargs["audio_kwargs"] = {"max_length": 2048000}

        try:
            batch = processor(**proc_kwargs)
        except TypeError:
            if wants_media:
                raise RuntimeError(
                    "processor rejected multimodal batch; cannot text-only fallback"
                )
            # Older / simpler processors: text only.
            batch = processor(
                text=text_in,
                return_tensors="pt",
                truncation=True,
                padding=True,
                max_length=8192,
            )

        device = self._device_obj()
        batch = {
            k: (v.to(device) if hasattr(v, "to") else v) for k, v in batch.items()
        }

        with torch.inference_mode():
            out = model(**batch, output_hidden_states=True)
            if hasattr(out, "hidden_states") and out.hidden_states is not None:
                last = out.hidden_states[-1]
            elif hasattr(out, "last_hidden_state"):
                last = out.last_hidden_state
            else:
                # Some embed models return pooler / embedding tensor directly.
                emb = getattr(out, "embeddings", None) or getattr(out, "pooler_output", None)
                if emb is None:
                    # Fallback: first tensor-like in tuple/list output.
                    emb = out[0] if isinstance(out, (tuple, list)) else out
                vec = emb
                if hasattr(vec, "dim") and vec.dim() > 1:
                    vec = vec[0]
                return _to_unit_list(vec, self._dim)

            attention_mask = batch.get("attention_mask")
            if attention_mask is not None:
                mask = attention_mask[..., None].bool()
                masked = last.masked_fill(~mask, 0.0)
                denom = attention_mask.sum(dim=1).clamp(min=1)[..., None]
                pooled = masked.sum(dim=1) / denom
            else:
                pooled = last.mean(dim=1)
            pooled = torch.nn.functional.normalize(pooled, dim=-1)
            return _to_unit_list(pooled[0], self._dim)

    def media_encode_available(self) -> bool:
        """True when multimodal packing (qwen_omni_utils) is loaded."""
        return self._mm_info_fn is not None

    def _require_media_utils(self, channel: str) -> None:
        """Raise when media encode is requested without mm utilities.

        Soft-skip is handled in :meth:`encode_atom_inputs` (drop media channels).
        Direct ``encode_image`` / ``encode_audio`` / ``encode_video`` / joint-with-
        media fail closed so callers never store a text-only pool under a media
        channel name.
        """
        self._ensure_open()
        if self._mm_info_fn is None:
            raise RuntimeError(
                f"qwen_omni_utils unavailable; cannot encode {channel} "
                "(install qwen-omni-utils for multimodal; text-only still works)"
            )

    def encode_text(self, text: str) -> list[float]:
        messages = self._messages_for(text=text)
        return self._embed_messages(messages)

    def encode_image(self, path_or_bytes: bytes | str) -> list[float]:
        self._require_media_utils("image")
        messages = self._messages_for(image=path_or_bytes)
        return self._embed_messages(messages)

    def encode_audio(self, path_or_bytes: bytes | str) -> list[float]:
        self._require_media_utils("audio")
        messages = self._messages_for(audio=path_or_bytes)
        return self._embed_messages(messages)

    def encode_video(self, path_or_bytes: bytes | str) -> list[float]:
        self._require_media_utils("video")
        messages = self._messages_for(video=path_or_bytes)
        return self._embed_messages(messages)

    def encode_joint(self, parts: ModalityParts) -> list[float]:
        present = parts.present_modalities()
        if not present:
            raise ValueError("encode_joint: no modalities")
        media_channels = [c for c in present if c != "text"]
        if media_channels:
            self._require_media_utils("joint+" + "+".join(media_channels))
        messages = self._messages_for(
            text=parts.text if "text" in present else None,
            image=parts.image if "image" in present else None,
            audio=parts.audio if "audio" in present else None,
            video=parts.video if "video" in present else None,
        )
        return self._embed_messages(messages)

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
        """Encode present modalities into an EmbeddingSet (same contract as mock).

        When multimodal packing utilities are missing, **media channels soft-skip**
        (never label a text-only pool as ``emb_image`` / ``emb_joint``). Text still
        encodes. Media-only atoms without mm utils → ``skipped``.
        """
        try:
            self._ensure_open()
            skip_meta: list[str] = []
            # Soft-skip media when process_mm_info is unavailable (Issue 1).
            if not self.media_encode_available():
                if image is not None:
                    skip_meta.append("image:mm_utils_unavailable")
                    image = None
                if audio is not None:
                    skip_meta.append("audio:mm_utils_unavailable")
                    audio = None
                if video is not None:
                    skip_meta.append("video:mm_utils_unavailable")
                    video = None

            parts = ModalityParts(text=text, image=image, audio=audio, video=video)
            present = parts.present_modalities()
            if not present:
                err = (
                    "media_mm_utils_unavailable"
                    if skip_meta
                    else "no modalities"
                )
                return EncodeResult(
                    status="skipped",
                    embeddings=None,
                    error=err,
                    channels_encoded=(),
                    meta={"embed_media_skipped": skip_meta} if skip_meta else {},
                )

            emb_text = (
                tuple(self.encode_text(text)) if "text" in present else None  # type: ignore[arg-type]
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
            # Joint only over channels we actually encoded (post soft-skip).
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
            meta: dict[str, Any] = {}
            if skip_meta:
                meta["embed_media_skipped"] = skip_meta
            return EncodeResult(
                status="ready",
                embeddings=emb,
                error=None,
                channels_encoded=emb.channels_present,
                meta=meta,
            )
        except Exception as exc:  # noqa: BLE001
            return EncodeResult(
                status="failed",
                embeddings=None,
                error=f"{type(exc).__name__}: {exc}"[:500],
                channels_encoded=(),
            )


def _media_ref(path_or_bytes: bytes | str) -> str | bytes:
    """Pass filesystem paths as str; raw bytes unchanged (processor-dependent)."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        return bytes(path_or_bytes)
    return str(path_or_bytes)


def _messages_want_media(messages: list[dict[str, Any]]) -> bool:
    """True when chat messages include image/audio/video content parts."""
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in (
                    "image",
                    "audio",
                    "video",
                ):
                    return True
        elif isinstance(content, dict) and content.get("type") in (
            "image",
            "audio",
            "video",
        ):
            return True
    return False


def _to_unit_list(tensor: Any, dim: int) -> list[float]:
    """Convert a 1-d tensor-like to a Python list of ``dim`` floats (L2-normed).

    Raises ``ValueError`` on dim mismatch — never pad/truncate into a false
    unit vector (Issue 4). Callers map this to ``EncodeResult(status=failed)``.
    """
    try:
        flat = tensor.detach().float().cpu().flatten().tolist()
    except Exception:  # noqa: BLE001
        flat = [float(x) for x in list(tensor)]
    if len(flat) != dim:
        raise ValueError(
            f"embedding dim mismatch: got {len(flat)}, expected {dim}"
        )
    return list(l2_normalize(flat))


def try_open_nemotron(
    *,
    model_id: str,
    model_path: str = "",
    device_pref: str = "auto",
    dim: int = EMBED_DIM,
) -> NemotronEmbedder | None:
    """Construct a NemotronEmbedder if torch+transformers present, else None.

    Does **not** download weights. Returns None when optional deps missing or
    no usable device (and preference is not cpu with torch).
    """
    if not torch_available() or not transformers_available():
        return None
    resolved = select_device(device_pref)
    if resolved == "unavailable":
        # Last resort: allow CPU if torch is present (select_device already
        # prefers cpu when torch exists; unavailable means no torch).
        return None
    return NemotronEmbedder(
        model_id=model_id or DEFAULT_NEMOTRON_MODEL_ID,
        model_path=model_path,
        device=resolved,
        dim=dim,
        dtype_name="float16",
    )


# ---------------------------------------------------------------------------
# open_encoder + mock fallback wrapper
# ---------------------------------------------------------------------------


def open_encoder(
    settings: MemorySettings | None = None,
    *,
    backend: str | None = None,
    device: str | None = None,
    model_id: str | None = None,
    dim: int = EMBED_DIM,
) -> Embedder:
    """Open an embedder for encode work.

    - ``backend=mock`` → :class:`MockEmbedder`
    - ``backend=nemotron`` → real :class:`NemotronEmbedder` when
      torch+transformers available; else **mock fallback** (health notes
      ``requested_backend`` / error). Never raises for missing optional deps.

    Does not import torch at module import time. Safe under
    ``embed_enabled=false`` for tests that still want deterministic vectors.
    """
    # Lazy import breaks config ↔ embed package cycle (config re-exports types).
    from elyra.memory.config import MemorySettings as _MemorySettings

    cfg = settings or _MemorySettings()
    be: str = backend if backend is not None else cfg.embed_backend
    be = (be or "mock").strip().lower()
    if be not in EMBED_BACKENDS:
        raise ValueError(
            f"embed_backend: expected one of {sorted(EMBED_BACKENDS)}, got {be!r}"
        )

    pref = device if device is not None else cfg.embed_device
    pref_s = (pref if pref else "auto").strip().lower()
    if pref_s not in EMBED_DEVICE_PREFS:
        raise ValueError(
            f"embed device preference: expected one of "
            f"{sorted(EMBED_DEVICE_PREFS)}, got {pref!r}"
        )

    requested_model = (cfg.embed_model_id or "").strip()
    if model_id is not None:
        effective_mid = model_id.strip() or MOCK_MODEL_ID
    else:
        effective_mid = MOCK_MODEL_ID

    # Mock path: never probe torch (Issue 5). Mock always runs on logical cpu.
    if be == "mock":
        return MockEmbedder(
            dim=dim,
            model_id=effective_mid,
            device="cpu",
        )

    # nemotron path — probe device only here
    resolved = select_device(pref_s)
    nemo_id = (
        model_id.strip()
        if model_id is not None and model_id.strip()
        else (requested_model or DEFAULT_NEMOTRON_MODEL_ID)
    )
    real = try_open_nemotron(
        model_id=nemo_id,
        model_path=cfg.embed_model_path or "",
        device_pref=pref_s,
        dim=dim,
    )
    if real is not None:
        return real

    # Graceful mock fallback when torch/transformers/device missing.
    return _UnavailableOrMockEmbedder(
        mock=MockEmbedder(
            dim=dim,
            model_id=MOCK_MODEL_ID if model_id is None else effective_mid,
            device="cpu",
        ),
        requested_backend=be,  # type: ignore[arg-type]
        device=resolved,
        requested_model_id=nemo_id,
        effective_model_id=MOCK_MODEL_ID if model_id is None else effective_mid,
    )


class _UnavailableOrMockEmbedder:
    """Nemotron-requested path when real runtime cannot open: mock + health note.

    Produces the same vectors as mock so CI and early dogfood keep working;
    health exposes ``requested_backend`` / ``requested_model_id`` and a
    non-fatal note that real weights are not loaded. Effective ``model_id``
    stays the mock id so durable rows are not mislabeled as Nemotron.
    """

    def __init__(
        self,
        *,
        mock: MockEmbedder,
        requested_backend: EmbedBackend,
        device: DeviceKind,
        requested_model_id: str,
        effective_model_id: str,
    ) -> None:
        self._mock = mock
        self._requested_backend = requested_backend
        self._device = device
        self._requested_model_id = requested_model_id
        self._effective_model_id = effective_model_id
        self._closed = False

    def health(self) -> dict[str, Any]:
        base = self._mock.health()
        base["ok"] = not self._closed
        base["backend"] = "mock"  # effective backend
        base["requested_backend"] = self._requested_backend
        base["device"] = self._device
        base["model_id"] = self._effective_model_id
        base["requested_model_id"] = self._requested_model_id
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
        encoded_at=utc_now_iso(),
        channels_present=tuple(channels),
    )
    return EncodeResult(
        status="ready",
        embeddings=emb,
        channels_encoded=emb.channels_present,
    )


__all__ = [
    "DEFAULT_NEMOTRON_MODEL_ID",
    "NEMOTRON_DTYPE_PREFERENCE",
    "Embedder",
    "NemotronEmbedder",
    "encode_atom_inputs",
    "open_encoder",
    "probe_devices",
    "select_device",
    "torch_available",
    "transformers_available",
    "try_open_nemotron",
]
