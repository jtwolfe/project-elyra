"""Atom encode path for corpus drain (Phase 2 PR2 + PR8 media matrix).

Scope: resolve text/media inputs, call embedder, return EncodeResult.
In scope: text from content_text; media resolve via MediaStore with MIME
matrix + size caps; never raise to callers (return failed EncodeResult).
Out of scope: queue policy, index upsert, Lance emb columns (other PRs).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from elyra.memory.embed.runtime import encode_atom_inputs
from elyra.memory.embed.types import EncodeResult
from elyra.memory.types import Atom

_LOG = logging.getLogger(__name__)

# Kinds that are never encoded (design: skip encode).
_SKIP_KINDS: frozenset[str] = frozenset({"moment_meta"})

# v1 supported MIME / extension matrix (design media resolution table).
_IMAGE_EXTS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_AUDIO_EXTS: frozenset[str] = frozenset({".wav", ".mp3"})
_VIDEO_EXTS: frozenset[str] = frozenset({".mp4"})

_IMAGE_MIMES: frozenset[str] = frozenset(
    {"image/png", "image/jpeg", "image/jpg", "image/webp"}
)
_AUDIO_MIMES: frozenset[str] = frozenset(
    {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3"}
)
_VIDEO_MIMES: frozenset[str] = frozenset({"video/mp4"})

# Defaults match MemorySettings (used when caller does not pass caps).
_DEFAULT_MEDIA_MAX_BYTES = 8_000_000


def content_fingerprint(atom: Atom) -> str:
    """Stable fingerprint of encode-relevant content (text + media ids)."""
    text = atom.content_text if atom.content_text is not None else ""
    mids = ",".join(sorted(str(m) for m in (atom.media_ids or ()) if m))
    return f"{text}|{mids}"


def is_embeddable(atom: Atom) -> bool:
    """True when the atom has content that can be encoded."""
    if atom.kind in _SKIP_KINDS:
        return False
    text = (atom.content_text or "").strip()
    if text:
        return True
    return bool(atom.media_ids)


def _classify_modality(path: str, mime: str) -> str | None:
    """Return ``image`` / ``audio`` / ``video`` or None if unsupported.

    Image: known png/jpeg/webp MIMEs/extensions, plus best-effort other ``image/*``.
    Audio/video: spike matrix only (wav/mp3, mp4) — no blanket ``audio/*``/``video/*``.
    """
    mime_l = (mime or "").strip().lower()
    p_lower = str(path).lower()
    # Image: known set, extension, or any other image/* (best-effort).
    if (
        mime_l in _IMAGE_MIMES
        or mime_l.startswith("image/")
        or any(p_lower.endswith(ext) for ext in _IMAGE_EXTS)
    ):
        return "image"
    # Audio / video: tighter matrix (known MIME or extension only).
    if mime_l in _AUDIO_MIMES or any(p_lower.endswith(ext) for ext in _AUDIO_EXTS):
        return "audio"
    if mime_l in _VIDEO_MIMES or any(p_lower.endswith(ext) for ext in _VIDEO_EXTS):
        return "video"
    return None


def _file_size(path: str) -> int | None:
    try:
        return Path(path).stat().st_size
    except OSError:
        return None


def resolve_media_inputs(
    atom: Atom,
    media_store: Any | None = None,
    *,
    max_bytes: int = _DEFAULT_MEDIA_MAX_BYTES,
    max_seconds: int | None = 30,
) -> dict[str, Any]:
    """Resolve media_ids to modality inputs with MIME matrix + size caps.

    When ``media_store`` is None or resolution fails, returns empty media
    channels (text-only encode). Oversize / unreadable / unknown types skip
    that item (soft); reasons collected under ``skipped`` for meta.

    Returns dict with keys ``image``, ``audio``, ``video`` (path/bytes or None)
    and ``skipped`` (list of reason strings).
    """
    image: bytes | str | None = None
    audio: bytes | str | None = None
    video: bytes | str | None = None
    skipped: list[str] = []
    # max_seconds reserved for future duration probing (audio/video);
    # documented in settings; v1 enforces bytes only.
    _ = max_seconds

    if media_store is None or not atom.media_ids:
        return {"image": image, "audio": audio, "video": video, "skipped": skipped}

    get_path = getattr(media_store, "resolve_path", None) or getattr(
        media_store, "path_for", None
    )
    get_att = getattr(media_store, "get", None) or getattr(
        media_store, "get_attachment", None
    )

    for mid in atom.media_ids:
        if not mid:
            continue
        try:
            path: str | None = None
            mime: str = ""
            if callable(get_att):
                att = get_att(mid)
                if att is None:
                    skipped.append(f"{mid}:missing")
                    continue
                path = getattr(att, "path", None) or getattr(att, "local_path", None)
                mime = str(
                    getattr(att, "mime_type", None)
                    or getattr(att, "content_type", None)
                    or ""
                ).lower()
            elif callable(get_path):
                path = get_path(mid)
            if not path:
                skipped.append(f"{mid}:no_path")
                continue
            path_s = str(path)
            if not Path(path_s).is_file():
                skipped.append(f"{mid}:unreadable")
                continue
            size = _file_size(path_s)
            if size is not None and max_bytes > 0 and size > max_bytes:
                skipped.append(f"{mid}:oversize_bytes:{size}")
                continue
            modality = _classify_modality(path_s, mime)
            if modality is None:
                skipped.append(f"{mid}:unknown_type")
                continue
            if modality == "image" and image is None:
                image = path_s
            elif modality == "audio" and audio is None:
                audio = path_s
            elif modality == "video" and video is None:
                video = path_s
            else:
                # Already have this channel — keep first; note skip.
                skipped.append(f"{mid}:channel_full:{modality}")
        except Exception:  # noqa: BLE001 — media resolve is best-effort
            _LOG.debug("media resolve failed for media_id=%s", mid, exc_info=True)
            skipped.append(f"{mid}:error")
            continue
    return {"image": image, "audio": audio, "video": video, "skipped": skipped}


def encode_atom(
    embedder: Any,
    atom: Atom,
    *,
    media_store: Any | None = None,
    media_max_bytes: int = _DEFAULT_MEDIA_MAX_BYTES,
    media_max_seconds: int | None = 30,
    single_modality_joint: bool = True,
) -> EncodeResult:
    """Encode one atom via ``embedder``. Never raises.

    Text channel uses ``content_text``. Media channels use the resolve path
    when a media_store is provided. Empty content → skipped. Exceptions →
    failed EncodeResult (queue updates durable status).
    """
    try:
        if atom.kind in _SKIP_KINDS:
            return EncodeResult(
                status="skipped",
                embeddings=None,
                error="kind_skipped",
                channels_encoded=(),
            )
        text = atom.content_text if atom.content_text is not None else ""
        text_s = text.strip() or None
        media = resolve_media_inputs(
            atom,
            media_store,
            max_bytes=media_max_bytes,
            max_seconds=media_max_seconds,
        )
        media_skipped = list(media.get("skipped") or [])
        if not text_s and not any(
            media.get(k) for k in ("image", "audio", "video")
        ):
            # Media-only atom whose ids did not resolve: leave retryable
            # (drain keeps pending). True empty content → permanent skip.
            if atom.media_ids:
                return EncodeResult(
                    status="failed",
                    embeddings=None,
                    error="media_unresolved",
                    channels_encoded=(),
                    meta={"embed_media_skipped": media_skipped} if media_skipped else {},
                )
            return EncodeResult(
                status="skipped",
                embeddings=None,
                error="no modalities",
                channels_encoded=(),
            )
        result = encode_atom_inputs(
            embedder,
            atom.atom_id,
            text=text_s,
            image=media.get("image"),
            audio=media.get("audio"),
            video=media.get("video"),
            single_modality_joint=single_modality_joint,
        )
        if media_skipped:
            meta = dict(result.meta or {})
            meta["embed_media_skipped"] = media_skipped
            return EncodeResult(
                status=result.status,
                embeddings=result.embeddings,
                error=result.error,
                channels_encoded=result.channels_encoded,
                meta=meta,
            )
        return result
    except Exception as exc:  # noqa: BLE001 — never raise into do-loop / worker
        _LOG.exception("encode_atom failed atom_id=%s", getattr(atom, "atom_id", "?"))
        return EncodeResult(
            status="failed",
            embeddings=None,
            error=f"{type(exc).__name__}: {exc}"[:500],
            channels_encoded=(),
        )


__all__ = [
    "content_fingerprint",
    "encode_atom",
    "is_embeddable",
    "resolve_media_inputs",
]
