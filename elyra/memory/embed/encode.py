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


def _classify_modality(name: str, mime: str) -> str | None:
    """Return ``image`` / ``audio`` / ``video`` or None if unsupported.

    Image: known png/jpeg/webp MIMEs/extensions, plus best-effort other ``image/*``.
    Audio/video: spike matrix only (wav/mp3, mp4) — no blanket ``audio/*``/``video/*``.

    ``name`` is a display/filename string (or a path that still has a useful
    suffix). Content-addressed blob paths are extensionless — do not rely on
    them alone (KD-M19).
    """
    mime_l = (mime or "").strip().lower()
    p_lower = str(name or "").lower()
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


def _attachment_mime(att: Any) -> str:
    """Prefer product ``mime``; accept legacy mime_type / content_type."""
    return str(
        getattr(att, "mime", None)
        or getattr(att, "mime_type", None)
        or getattr(att, "content_type", None)
        or ""
    ).lower()


def _attachment_byte_size(att: Any) -> int | None:
    try:
        raw = getattr(att, "byte_size", None)
        if raw is not None:
            return int(raw)
    except (TypeError, ValueError):
        pass
    return None


def resolve_one_media(
    media_store: Any,
    att_id: str,
    *,
    max_bytes: int = _DEFAULT_MEDIA_MAX_BYTES,
) -> dict[str, Any]:
    """Resolve one attachment to modality + path/bytes or a skip reason.

    Shared by encode drain and neighbors media-as-query (KD-M21). Never raises
    for missing / oversize / unknown type — returns ``skipped`` tokens matching
    ``embed_media_skipped`` style.

    Returns keys: ``modality`` (str|None), ``input`` (path str | bytes | None),
    ``skipped`` (str|None), ``mime`` (str, optional when resolved).
    """
    mid = str(att_id) if att_id is not None else ""
    if not mid:
        return {"modality": None, "input": None, "skipped": ":error"}

    get_att = getattr(media_store, "get", None) or getattr(
        media_store, "get_attachment", None
    )
    att: Any | None = None
    if callable(get_att):
        try:
            att = get_att(mid)
        except Exception:  # noqa: BLE001 — resolve is best-effort
            _LOG.debug("media get failed for media_id=%s", mid, exc_info=True)
            return {"modality": None, "input": None, "skipped": f"{mid}:error"}
        if att is None:
            return {"modality": None, "input": None, "skipped": f"{mid}:missing"}

    mime = _attachment_mime(att) if att is not None else ""
    filename = str(getattr(att, "filename", None) or "") if att is not None else ""
    kind = str(getattr(att, "kind", None) or "") if att is not None else ""
    size: int | None = _attachment_byte_size(att) if att is not None else None
    path_s: str | None = None

    # Product path: content-addressed blob (extensionless) — KD-M14.
    # Prefer blob_path(sha) when att is already loaded to avoid a second get()
    # via resolve_blob_path (thin helper remains for external callers).
    if att is not None:
        sha = getattr(att, "sha256", None) or ""
        blob_path_fn = getattr(media_store, "blob_path", None)
        if callable(blob_path_fn) and sha:
            try:
                p = Path(blob_path_fn(sha))
                if p.is_file():
                    path_s = str(p)
            except (TypeError, ValueError, OSError):
                pass
        if path_s is None:
            resolve_blob = getattr(media_store, "resolve_blob_path", None)
            if callable(resolve_blob):
                try:
                    p = resolve_blob(mid)
                    if p is not None:
                        p_path = Path(p)
                        if p_path.is_file():
                            path_s = str(p_path)
                except (TypeError, ValueError, OSError):
                    pass
        # Legacy fields on doubles that still put path on the attachment.
        if path_s is None:
            legacy = getattr(att, "path", None) or getattr(att, "local_path", None)
            if legacy and Path(str(legacy)).is_file():
                path_s = str(legacy)

    # Test doubles / legacy: path_for / resolve_path without Attachment meta.
    if path_s is None:
        get_path = getattr(media_store, "resolve_path", None) or getattr(
            media_store, "path_for", None
        )
        if callable(get_path):
            try:
                p = get_path(mid)
            except Exception:  # noqa: BLE001
                p = None
            if p and Path(str(p)).is_file():
                path_s = str(p)

    # KD-M22: when a filesystem path is known, re-stat and take max with meta
    # so under-reported att.byte_size cannot bypass the encode cap.
    if path_s is not None:
        file_sz = _file_size(path_s)
        if file_sz is not None:
            size = max(size if size is not None else 0, file_sz)

    # KD-M22: size-check BEFORE any full read_bytes.
    if size is not None and max_bytes > 0 and size > max_bytes:
        return {
            "modality": None,
            "input": None,
            "skipped": f"{mid}:oversize_bytes:{size}",
        }

    # KD-M19: classify from mime + filename; not extensionless sha path alone.
    classify_name = filename
    if not classify_name and path_s and Path(path_s).suffix:
        classify_name = path_s
    modality = _classify_modality(classify_name, mime)
    if modality is None and kind in ("image", "audio", "video"):
        modality = kind
    if modality is None:
        # path_for-only doubles with no hit: no attachment record and no path
        # → no_path (not unknown_type). Product get→None is already :missing.
        if att is None and path_s is None:
            return {
                "modality": None,
                "input": None,
                "skipped": f"{mid}:no_path",
            }
        return {
            "modality": None,
            "input": None,
            "skipped": f"{mid}:unknown_type",
        }

    if path_s is not None:
        return {
            "modality": modality,
            "input": path_s,
            "skipped": None,
            "mime": mime,
        }

    # Bytes fallback only after size cap passed (or size unknown — defense below).
    read_bytes = getattr(media_store, "read_bytes", None)
    if callable(read_bytes):
        try:
            data = bytes(read_bytes(mid))
        except FileNotFoundError:
            return {
                "modality": None,
                "input": None,
                "skipped": f"{mid}:no_path",
            }
        except Exception:  # noqa: BLE001
            _LOG.debug("read_bytes failed for media_id=%s", mid, exc_info=True)
            return {"modality": None, "input": None, "skipped": f"{mid}:error"}
        if not data:
            return {
                "modality": None,
                "input": None,
                "skipped": f"{mid}:no_path",
            }
        # Defense in depth if byte_size was missing/wrong.
        if max_bytes > 0 and len(data) > max_bytes:
            return {
                "modality": None,
                "input": None,
                "skipped": f"{mid}:oversize_bytes:{len(data)}",
            }
        return {
            "modality": modality,
            "input": data,
            "skipped": None,
            "mime": mime,
        }

    return {"modality": None, "input": None, "skipped": f"{mid}:no_path"}


def resolve_media_inputs(
    atom: Atom,
    media_store: Any | None = None,
    *,
    max_bytes: int = _DEFAULT_MEDIA_MAX_BYTES,
    max_seconds: int | None = 30,
) -> dict[str, Any]:
    """Resolve media_ids to modality inputs with MIME matrix + size caps.

    Composes :func:`resolve_one_media` over ``atom.media_ids`` with first-wins
    per channel. When ``media_store`` is None or resolution fails, returns
    empty media channels (text-only encode). Oversize / unreadable / unknown
    types skip that item (soft); reasons collected under ``skipped`` for meta.

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

    for mid in atom.media_ids:
        if not mid:
            continue
        try:
            one = resolve_one_media(media_store, str(mid), max_bytes=max_bytes)
        except Exception:  # noqa: BLE001 — media resolve is best-effort
            _LOG.debug("media resolve failed for media_id=%s", mid, exc_info=True)
            skipped.append(f"{mid}:error")
            continue
        reason = one.get("skipped")
        if reason:
            skipped.append(str(reason))
            continue
        modality = one.get("modality")
        payload = one.get("input")
        if not modality or payload is None:
            skipped.append(f"{mid}:no_path")
            continue
        if modality == "image" and image is None:
            image = payload
        elif modality == "audio" and audio is None:
            audio = payload
        elif modality == "video" and video is None:
            video = payload
        else:
            # Already have this channel — keep first; note skip.
            skipped.append(f"{mid}:channel_full:{modality}")
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
        if not text_s and not any(media.get(k) for k in ("image", "audio", "video")):
            # Media-only atom whose ids did not resolve: leave retryable
            # (drain keeps pending). True empty content → permanent skip.
            if atom.media_ids:
                return EncodeResult(
                    status="failed",
                    embeddings=None,
                    error="media_unresolved",
                    channels_encoded=(),
                    meta={"embed_media_skipped": media_skipped}
                    if media_skipped
                    else {},
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
    "resolve_one_media",
]
