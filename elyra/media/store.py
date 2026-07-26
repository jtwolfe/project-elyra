"""Content-addressed attachment store under ``data/media/`` (KD1, KD14).

Scope: blob + meta persistence, bind, path helpers, stdlib MIME sniff,
sandbox RO projection (PR2).
In scope: sha-addressed blobs, meta JSON with bound_message_id, temp+rename
writes, ensure dirs, read/delete helpers used by reset/tests, project mirror.
Out of scope: HTTP upload, GC (PR10), TTS cache writes, vision expand.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths, resolve_paths
from elyra.media.types import (
    ATTACHMENT_KINDS,
    ATTACHMENT_ORIGINS,
    ROLE_HINTS,
    Attachment,
)

_LOG = logging.getLogger(__name__)

MEDIA_DIRNAME = "media"
# att ids: att_ + uuid hex, or generic safe segment for future flexibility.
_ATT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def media_root(paths: ElyraPaths) -> Path:
    return paths.data_dir / MEDIA_DIRNAME


def ensure_media_dirs(paths: ElyraPaths | None = None) -> Path:
    """Create ``data/media/{blobs,meta,tts,by_message,tmp}``; return media root."""
    p = paths or resolve_paths()
    root = media_root(p)
    for sub in ("blobs", "meta", "tts", "by_message", "tmp"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def safe_filename(name: str) -> str:
    """Single-segment sanitized filename for sandbox_relpath / display."""
    base = Path(name).name if name else "file"
    base = _SAFE_FILENAME_RE.sub("_", base).strip("._") or "file"
    return base[:180]


def validate_att_id(att_id: str) -> str:
    if not isinstance(att_id, str) or not _ATT_ID_RE.fullmatch(att_id):
        raise ValueError(f"invalid attachment id: {att_id!r}")
    path = Path(att_id)
    if path.is_absolute() or len(path.parts) != 1:
        raise ValueError(f"invalid attachment id: {att_id!r}")
    return att_id


def new_att_id() -> str:
    return "att_" + uuid.uuid4().hex


# Sniff source tags: "magic" = confident magic table (prefer over claimed mime).
SniffSource = str  # "magic" | "ambiguous" | "extension" | "claim" | "default"


def sniff_mime_kind_source(
    data: bytes,
    *,
    filename: str | None = None,
    claimed_mime: str | None = None,
) -> tuple[str, str, SniffSource]:
    """Stdlib magic-byte table → (mime, kind, source).

    ``source == "magic"`` means the magic table confidently identified the type;
    callers should prefer that mime over a client-claimed Content-Type.
    ``ambiguous`` covers containers where claim/filename may disambiguate.
    """
    # Confident magic hits — never trust client Content-Type alone for storage.
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "image", "magic"
    if len(data) >= 3 and data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "image", "magic"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif", "image", "magic"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "image", "magic"
    if data.startswith(b"%PDF"):
        return "application/pdf", "file", "magic"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav", "audio", "magic"

    # Ambiguous containers — claim/filename may refine.
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        # WebM/Matroska EBML header (audio vs video).
        name = (filename or "").lower()
        if name.endswith((".webm",)):
            if claimed_mime and claimed_mime.startswith("video/"):
                return "video/webm", "video", "ambiguous"
            return "audio/webm", "audio", "ambiguous"
        if claimed_mime and claimed_mime.startswith("video/"):
            return claimed_mime, "video", "ambiguous"
        if claimed_mime and claimed_mime.startswith("audio/"):
            return claimed_mime, "audio", "ambiguous"
        return "audio/webm", "audio", "ambiguous"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        # ISO BMFF (mp4/m4a)
        if claimed_mime and claimed_mime.startswith("audio/"):
            return claimed_mime, "audio", "ambiguous"
        return "video/mp4", "video", "ambiguous"

    # Extension / claim fallbacks (not magic-confident).
    name = (filename or "").lower()
    ext_map: dict[str, tuple[str, str]] = {
        ".png": ("image/png", "image"),
        ".jpg": ("image/jpeg", "image"),
        ".jpeg": ("image/jpeg", "image"),
        ".gif": ("image/gif", "image"),
        ".webp": ("image/webp", "image"),
        ".pdf": ("application/pdf", "file"),
        ".txt": ("text/plain", "file"),
        ".md": ("text/markdown", "file"),
        ".json": ("application/json", "file"),
        ".csv": ("text/csv", "file"),
        ".wav": ("audio/wav", "audio"),
        ".mp3": ("audio/mpeg", "audio"),
        ".ogg": ("audio/ogg", "audio"),
        ".webm": ("audio/webm", "audio"),
        ".mp4": ("video/mp4", "video"),
    }
    for ext, pair in ext_map.items():
        if name.endswith(ext):
            return pair[0], pair[1], "extension"

    if claimed_mime:
        mime = claimed_mime
        if mime.startswith("image/"):
            return mime, "image", "claim"
        if mime.startswith("audio/"):
            return mime, "audio", "claim"
        if mime.startswith("video/"):
            return mime, "video", "claim"
        return mime, "file", "claim"

    return "application/octet-stream", "file", "default"


def sniff_mime_and_kind(
    data: bytes,
    *,
    filename: str | None = None,
    claimed_mime: str | None = None,
) -> tuple[str, str]:
    """Stdlib magic-byte table → (mime, kind). Unknown → claimed or octet-stream/file."""
    mime, kind, _source = sniff_mime_kind_source(
        data, filename=filename, claimed_mime=claimed_mime
    )
    return mime, kind


def blob_relpath(sha256: str) -> Path:
    """Relative path under blobs/: ``<sha[:2]>/<sha>``."""
    if not _SHA256_RE.fullmatch(sha256):
        raise ValueError(f"invalid sha256: {sha256!r}")
    return Path(sha256[:2]) / sha256


def _atomic_write_bytes(dest: Path, data: bytes, *, tmp_dir: Path) -> None:
    """Write via temp file under tmp_dir then replace; clean temp on failure."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f".{uuid.uuid4().hex}.part"
    try:
        tmp.write_bytes(data)
        tmp.replace(dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _atomic_write_text(dest: Path, text: str, *, tmp_dir: Path) -> None:
    _atomic_write_bytes(dest, text.encode("utf-8"), tmp_dir=tmp_dir)


class MediaStore:
    """Host-truth attachment store: content-addressed blobs + per-id meta."""

    def __init__(self, paths: ElyraPaths | None = None) -> None:
        self._paths = paths or resolve_paths()

    @property
    def paths(self) -> ElyraPaths:
        return self._paths

    @property
    def root(self) -> Path:
        return media_root(self._paths)

    @property
    def blobs_dir(self) -> Path:
        return self.root / "blobs"

    @property
    def meta_dir(self) -> Path:
        return self.root / "meta"

    @property
    def tmp_dir(self) -> Path:
        return self.root / "tmp"

    def ensure_dirs(self) -> None:
        ensure_media_dirs(self._paths)

    def blob_path(self, sha256: str) -> Path:
        return self.blobs_dir / blob_relpath(sha256)

    def meta_path(self, att_id: str) -> Path:
        safe = validate_att_id(att_id)
        root = self.meta_dir.resolve()
        path = (root / f"{safe}.json").resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"invalid attachment id: {att_id!r}")
        return path

    def put_bytes(
        self,
        data: bytes,
        *,
        filename: str,
        mime: str | None = None,
        kind: str | None = None,
        origin: str = "user_upload",
        role_hint: str = "primary",
        uploader_user_id: str | None = "operator",
        att_id: str | None = None,
        bound_message_id: str | None = None,
    ) -> Attachment:
        """Store bytes (dedupe by sha256) + write meta; return Attachment.

        Uses temp+rename so partial writes never leave a final path. On meta
        write failure after a new blob was committed, the content-addressed
        blob is left (safe to share); no half-written meta remains.

        When magic sniff is confident, stored ``mime`` prefers magic over a
        client-claimed Content-Type. Explicit ``kind=`` still overrides kind
        (product "treat image as file"). Reusing ``att_id`` is idempotent only
        when the sha matches the existing meta; otherwise ``ValueError``.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        data = bytes(data)
        if origin not in ATTACHMENT_ORIGINS:
            raise ValueError(f"invalid origin: {origin!r}")
        if role_hint not in ROLE_HINTS:
            role_hint = "primary"

        self.ensure_dirs()
        sha = hashlib.sha256(data).hexdigest()
        sniffed_mime, sniffed_kind, sniff_source = sniff_mime_kind_source(
            data, filename=filename, claimed_mime=mime
        )
        # Prefer magic-confident mime for durable storage (PR3 Content-Type).
        if sniff_source == "magic":
            final_mime = sniffed_mime
        else:
            final_mime = mime or sniffed_mime
        final_kind = kind or sniffed_kind
        if final_kind not in ATTACHMENT_KINDS:
            raise ValueError(f"invalid kind: {final_kind!r}")

        if att_id is not None:
            aid = validate_att_id(att_id)
            existing = self.get(aid)
            if existing is not None:
                if existing.sha256 == sha:
                    # Idempotent put: same id + same bytes. Re-project so a
                    # missing mirror (failed first project / clear_sandbox)
                    # heals without a new att id (L3 disposable mirror).
                    self._best_effort_project(existing)
                    return existing
                raise ValueError(
                    f"attachment id already exists with different bytes: {aid!r}"
                )
        else:
            aid = new_att_id()

        blob = self.blob_path(sha)
        if not blob.is_file():
            _atomic_write_bytes(blob, data, tmp_dir=self.tmp_dir)

        fname = safe_filename(filename)
        att = Attachment(
            id=aid,
            kind=final_kind,
            origin=origin,
            filename=fname,
            mime=final_mime,
            byte_size=len(data),
            sha256=sha,
            created_at=_now(),
            role_hint=role_hint,
            # Sandbox mirror path; projected best-effort after meta write.
            sandbox_relpath=f"media/{aid}/{fname}",
            embedding_status="none",
            embedding_ref=None,
            bound_message_id=bound_message_id,
            uploader_user_id=uploader_user_id,
        )
        self._write_meta(att)
        self._best_effort_project(att)
        return att

    def _best_effort_project(self, att: Attachment) -> None:
        """Project blob into sandbox media mirror; log OSError, never raise.

        Failure must not leave meta inconsistent — blob+meta are durable truth;
        mirror is disposable and re-projectable (PR2 / L3).
        """
        try:
            from elyra.media.project import project_attachment

            blob = self.blob_path(att.sha256)
            project_attachment(att, blob, paths=self._paths)
        except OSError as exc:
            _LOG.warning("media projection failed for %s: %s", att.id, exc)

    def _write_meta(self, att: Attachment) -> None:
        path = self.meta_path(att.id)
        payload = json.dumps(att.to_dict(), ensure_ascii=False, indent=2) + "\n"
        _atomic_write_text(path, payload, tmp_dir=self.tmp_dir)

    def get(self, att_id: str) -> Attachment | None:
        """Load meta by id; None if missing or corrupt."""
        try:
            path = self.meta_path(att_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return None
        if not isinstance(raw, dict) or "id" not in raw:
            return None
        try:
            return Attachment.from_dict(raw)
        except (KeyError, TypeError, ValueError):
            return None

    def bind_message(self, att_id: str, message_id: str) -> Attachment:
        """Set ``bound_message_id`` (idempotent if already bound to same id).

        Raises FileNotFoundError if meta missing; ValueError if ``message_id``
        is empty/whitespace or already bound to a different message.
        """
        if not isinstance(message_id, str) or not message_id.strip():
            raise ValueError(f"invalid message_id: {message_id!r}")
        att = self.get(att_id)
        if att is None:
            raise FileNotFoundError(f"attachment not found: {att_id!r}")
        if att.bound_message_id is not None and att.bound_message_id != message_id:
            raise ValueError(
                f"attachment {att_id!r} already bound to {att.bound_message_id!r}"
            )
        if att.bound_message_id == message_id:
            return att
        att.bound_message_id = message_id
        self._write_meta(att)
        return att

    def set_xai_file(
        self,
        att_id: str,
        *,
        xai_file_id: str,
        xai_file_expires_at: str | None = None,
    ) -> Attachment:
        """Persist xAI Files API id + optional expiry ISO on attachment meta (PR9).

        Raises FileNotFoundError if meta missing; ValueError if ``xai_file_id``
        is empty/whitespace.
        """
        if not isinstance(xai_file_id, str) or not xai_file_id.strip():
            raise ValueError(f"invalid xai_file_id: {xai_file_id!r}")
        att = self.get(att_id)
        if att is None:
            raise FileNotFoundError(f"attachment not found: {att_id!r}")
        att.xai_file_id = xai_file_id.strip()
        att.xai_file_expires_at = xai_file_expires_at
        self._write_meta(att)
        return att

    def read_bytes(self, att_id: str) -> bytes:
        """Read blob bytes for attachment id. Raises FileNotFoundError if missing."""
        att = self.get(att_id)
        if att is None:
            raise FileNotFoundError(f"attachment not found: {att_id!r}")
        blob = self.blob_path(att.sha256)
        if not blob.is_file():
            raise FileNotFoundError(f"blob missing for {att_id!r}: {att.sha256}")
        return blob.read_bytes()

    def list_meta_ids(self) -> list[str]:
        """Return attachment ids that have meta files (unordered)."""
        if not self.meta_dir.is_dir():
            return []
        out: list[str] = []
        for child in self.meta_dir.iterdir():
            if child.is_file() and child.suffix == ".json":
                out.append(child.stem)
        return out

    def delete_attachment(self, att_id: str, *, remove_blob_if_orphan: bool = True) -> bool:
        """Remove meta; optionally remove blob when no other meta references it.

        Returns True if meta existed. Used by tests/GC; reset wipes whole tree.
        """
        att = self.get(att_id)
        if att is None:
            return False
        sha = att.sha256
        path = self.meta_path(att_id)
        path.unlink(missing_ok=True)
        if remove_blob_if_orphan and sha:
            still_used = False
            for other_id in self.list_meta_ids():
                other = self.get(other_id)
                if other is not None and other.sha256 == sha:
                    still_used = True
                    break
            if not still_used:
                blob = self.blob_path(sha)
                blob.unlink(missing_ok=True)
                # Best-effort remove empty shard dir.
                try:
                    blob.parent.rmdir()
                except OSError:
                    pass
        return True


def put_bytes(
    data: bytes,
    *,
    filename: str,
    paths: ElyraPaths | None = None,
    **kwargs: Any,
) -> Attachment:
    """Module-level convenience wrapper around :meth:`MediaStore.put_bytes`."""
    return MediaStore(paths).put_bytes(data, filename=filename, **kwargs)


def get_attachment(
    att_id: str, *, paths: ElyraPaths | None = None
) -> Attachment | None:
    return MediaStore(paths).get(att_id)


def bind_attachment_message(
    att_id: str,
    message_id: str,
    *,
    paths: ElyraPaths | None = None,
) -> Attachment:
    return MediaStore(paths).bind_message(att_id, message_id)
