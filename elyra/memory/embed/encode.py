"""Atom encode path for corpus drain (Phase 2 PR2).

Scope: resolve text/media inputs, call embedder, return EncodeResult.
In scope: text from content_text; media resolve stub (optional MediaStore);
never raise to callers (return failed EncodeResult).
Out of scope: queue policy, index upsert, Lance emb columns (PR3).
"""

from __future__ import annotations

import logging
from typing import Any

from elyra.memory.embed.runtime import encode_atom_inputs
from elyra.memory.embed.types import EncodeResult
from elyra.memory.types import Atom

_LOG = logging.getLogger(__name__)

# Kinds that are never encoded (design: skip encode).
_SKIP_KINDS: frozenset[str] = frozenset({"moment_meta"})


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


def resolve_media_inputs(
    atom: Atom,
    media_store: Any | None = None,
) -> dict[str, bytes | str | None]:
    """Resolve media_ids to modality inputs (PR2 stub).

    When ``media_store`` is None or resolution fails, returns empty media
    channels (text-only encode). Full MIME matrix lands with dogfood spike;
    PR2 only needs a soft fail path so drain never raises.
    """
    image: bytes | str | None = None
    audio: bytes | str | None = None
    video: bytes | str | None = None
    if media_store is None or not atom.media_ids:
        return {"image": image, "audio": audio, "video": video}

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
                continue
            # Best-effort modality guess from mime / extension.
            p_lower = str(path).lower()
            if mime.startswith("image/") or p_lower.endswith(
                (".png", ".jpg", ".jpeg", ".webp")
            ):
                if image is None:
                    image = str(path)
            elif mime.startswith("audio/") or p_lower.endswith((".wav", ".mp3")):
                if audio is None:
                    audio = str(path)
            elif mime.startswith("video/") or p_lower.endswith(".mp4"):
                if video is None:
                    video = str(path)
            # unknown → skip channel
        except Exception:  # noqa: BLE001 — media resolve is best-effort
            _LOG.debug("media resolve failed for media_id=%s", mid, exc_info=True)
            continue
    return {"image": image, "audio": audio, "video": video}


def encode_atom(
    embedder: Any,
    atom: Atom,
    *,
    media_store: Any | None = None,
) -> EncodeResult:
    """Encode one atom via ``embedder``. Never raises.

    Text channel uses ``content_text``. Media channels use the resolve stub
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
        media = resolve_media_inputs(atom, media_store)
        if not text_s and not any(media.values()):
            # Media-only atom whose ids did not resolve: leave retryable
            # (drain keeps pending). True empty content → permanent skip.
            if atom.media_ids:
                return EncodeResult(
                    status="failed",
                    embeddings=None,
                    error="media_unresolved",
                    channels_encoded=(),
                )
            return EncodeResult(
                status="skipped",
                embeddings=None,
                error="no modalities",
                channels_encoded=(),
            )
        # Prefer embedder.encode_atom_inputs when present (MockEmbedder).
        return encode_atom_inputs(
            embedder,
            atom.atom_id,
            text=text_s,
            image=media["image"],
            audio=media["audio"],
            video=media["video"],
        )
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
