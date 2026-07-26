"""xAI Files API client for document tier B (PR9 / KD5).

Scope: multipart upload to ``POST {base}/files``, parse file id + expiry,
persist onto attachment meta. Completions wire-attach is **off by default**
(unproven with tool-calling Completions; xAI doc attach is Responses-shaped).
In scope: upload_bytes, ensure_xai_file_id, wire-attach helpers gated by flag.
Out of scope: Responses API migration, live smoke, GC of remote files.

Design lock (PR9): text-extract always; Files upload + ``xai_file_id`` storage
may land even when Completions attach is disabled; if attach is not smoke-
proven with tools → extract + inventory only (``file pdf not_inlined``).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request

_LOG = logging.getLogger(__name__)

# Default TTL for uploaded session docs (24h). xAI range: 3600..2592000.
DEFAULT_EXPIRES_AFTER_SECONDS = 24 * 60 * 60

# Completions file-part attach: OFF until smoke proves tools still work (KD5).
# Emergency opt-in only; not a product launch flag.
_ATTACH_ENV = "ELYRA_XAI_FILES_ATTACH"


def completions_file_attach_enabled() -> bool:
    """True only when ``ELYRA_XAI_FILES_ATTACH=1`` (default off — KD5 hard fallback)."""
    raw = os.environ.get(_ATTACH_ENV)
    if raw is None or raw == "":
        return False
    return raw.strip() in ("1", "true", "True", "yes", "YES")


class _MediaStoreLike(Protocol):
    def get(self, att_id: str) -> Any: ...

    def read_bytes(self, att_id: str) -> bytes: ...

    def set_xai_file(
        self,
        att_id: str,
        *,
        xai_file_id: str,
        xai_file_expires_at: str | None = None,
    ) -> Any: ...


@dataclass(frozen=True)
class XaiFileUploadResult:
    """Parsed Files API upload response (subset)."""

    id: str
    filename: str | None = None
    bytes: int | None = None
    expires_at: int | None = None  # unix seconds or None
    purpose: str | None = None
    raw: dict[str, Any] | None = None

    def expires_at_iso(self) -> str | None:
        if self.expires_at is None:
            return None
        try:
            return (
                datetime.fromtimestamp(int(self.expires_at), tz=UTC)
                .isoformat()
                .replace("+00:00", "Z")
            )
        except (OSError, OverflowError, ValueError):
            return None


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + (path if path.startswith("/") else f"/{path}")


def _multipart_body(
    fields: list[tuple[str, str]],
    *,
    file_field: str,
    filename: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> tuple[bytes, str]:
    """Build multipart/form-data body. Field order preserved (expires_after before file)."""
    boundary = f"----elyra{uuid.uuid4().hex}"
    crlf = b"\r\n"
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(f"--{boundary}".encode("ascii"))
        parts.append(
            f'Content-Disposition: form-data; name="{name}"'.encode("utf-8")
        )
        parts.append(b"")
        parts.append(value.encode("utf-8"))
    # File part last (xAI requires expires_after before file).
    parts.append(f"--{boundary}".encode("ascii"))
    disp = (
        f'Content-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"'
    )
    parts.append(disp.encode("utf-8"))
    parts.append(f"Content-Type: {content_type}".encode("utf-8"))
    parts.append(b"")
    parts.append(data)
    parts.append(f"--{boundary}--".encode("ascii"))
    parts.append(b"")
    body = crlf.join(parts)
    return body, boundary


def parse_upload_response(data: Mapping[str, Any]) -> XaiFileUploadResult:
    """Map Files API JSON object to :class:`XaiFileUploadResult`."""
    fid = data.get("id")
    if not fid or not isinstance(fid, str):
        raise ValueError("files upload response missing string id")
    expires_at = data.get("expires_at")
    exp_i: int | None
    if expires_at is None:
        exp_i = None
    else:
        try:
            exp_i = int(expires_at)
        except (TypeError, ValueError):
            exp_i = None
    size = data.get("bytes")
    try:
        size_i = int(size) if size is not None else None
    except (TypeError, ValueError):
        size_i = None
    return XaiFileUploadResult(
        id=fid,
        filename=str(data["filename"]) if data.get("filename") is not None else None,
        bytes=size_i,
        expires_at=exp_i,
        purpose=str(data["purpose"]) if data.get("purpose") is not None else None,
        raw=dict(data),
    )


class XaiFilesClient:
    """stdlib HTTP client for xAI ``POST /v1/files`` (OpenAI-compatible).

    Prefer constructing via :meth:`from_config` with bearer + base_url.
    ``urlopen`` is injectable for hermetic tests.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.x.ai/v1",
        files_path: str = "/files",
        bearer_token: str,
        connect_timeout: float = 10.0,
        read_timeout: float = 120.0,
        urlopen: Callable[..., Any] | None = None,
    ) -> None:
        if not bearer_token or not isinstance(bearer_token, str):
            raise ValueError("bearer_token must be a non-empty string")
        self._base_url = base_url.rstrip("/")
        self._files_path = files_path if files_path.startswith("/") else f"/{files_path}"
        self._bearer = bearer_token
        self._timeout = max(float(connect_timeout), float(read_timeout))
        self._urlopen = urlopen or urllib_request.urlopen

    @classmethod
    def from_config(
        cls,
        config: Any,
        *,
        bearer_token: str,
        urlopen: Callable[..., Any] | None = None,
    ) -> XaiFilesClient:
        """Build from :class:`elyra.llm.config.XaiClientConfig` (or duck type)."""
        base = getattr(config, "base_url", "https://api.x.ai/v1")
        path = getattr(config, "files_path", "/files")
        connect = float(getattr(config, "connect_timeout", 10.0))
        read = float(getattr(config, "read_timeout", 120.0))
        return cls(
            base_url=str(base),
            files_path=str(path),
            bearer_token=bearer_token,
            connect_timeout=connect,
            read_timeout=read,
            urlopen=urlopen,
        )

    @property
    def files_url(self) -> str:
        return _join(self._base_url, self._files_path)

    def upload_bytes(
        self,
        data: bytes,
        *,
        filename: str,
        purpose: str = "assistants",
        expires_after: int | None = DEFAULT_EXPIRES_AFTER_SECONDS,
        content_type: str = "application/octet-stream",
    ) -> XaiFileUploadResult:
        """Upload raw bytes; return parsed file metadata.

        Never logs bearer token. Raises ``RuntimeError`` on HTTP/transport
        failure (detail truncated; no Authorization values).
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes")
        data = bytes(data)
        fname = (filename or "file").replace('"', "_")
        fields: list[tuple[str, str]] = []
        # expires_after MUST appear before file (xAI multipart ordering).
        if expires_after is not None:
            fields.append(("expires_after", str(int(expires_after))))
        fields.append(("purpose", purpose or "assistants"))
        body, boundary = _multipart_body(
            fields,
            file_field="file",
            filename=fname,
            data=data,
            content_type=content_type or "application/octet-stream",
        )
        headers = {
            "Authorization": f"Bearer {self._bearer}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        }
        req = urllib_request.Request(
            self.files_url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with self._urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"files HTTP {exc.code}: {detail[:500]}") from exc
        except urllib_error.URLError as exc:
            raise RuntimeError(f"files connection failed: {exc.reason}") from exc

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("files response is not JSON") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("files response is not a JSON object")
        return parse_upload_response(parsed)


def _is_expired(expires_at_iso: str | None, *, skew_seconds: int = 60) -> bool:
    if not expires_at_iso:
        return False  # permanent / unknown → treat as still valid
    try:
        s = expires_at_iso.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        exp = datetime.fromisoformat(s)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
    except ValueError:
        return True
    return datetime.now(UTC) >= (exp - timedelta(seconds=skew_seconds))


def ensure_xai_file_id(
    att_id: str,
    *,
    media_store: _MediaStoreLike,
    client: XaiFilesClient,
    expires_after: int | None = DEFAULT_EXPIRES_AFTER_SECONDS,
    force: bool = False,
) -> str | None:
    """Upload attachment to xAI Files if needed; persist ``xai_file_id`` on meta.

    Returns file id on success, ``None`` on failure (logged; never raises for
    network errors so expand can fall back to extract+inventory).
    """
    try:
        att = media_store.get(att_id)
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("ensure_xai_file_id get failed for %s: %s", att_id, exc)
        return None
    if att is None:
        return None

    existing = getattr(att, "xai_file_id", None) or (
        att.get("xai_file_id") if isinstance(att, Mapping) else None
    )
    expires = getattr(att, "xai_file_expires_at", None) or (
        att.get("xai_file_expires_at") if isinstance(att, Mapping) else None
    )
    if existing and not force and not _is_expired(expires if isinstance(expires, str) else None):
        return str(existing)

    filename = str(
        getattr(att, "filename", None)
        or (att.get("filename") if isinstance(att, Mapping) else None)
        or "file"
    )
    mime = str(
        getattr(att, "mime", None)
        or (att.get("mime") if isinstance(att, Mapping) else None)
        or "application/octet-stream"
    )
    try:
        data = media_store.read_bytes(att_id)
    except (OSError, FileNotFoundError, ValueError) as exc:
        _LOG.warning("ensure_xai_file_id read failed for %s: %s", att_id, exc)
        return None
    try:
        result = client.upload_bytes(
            data,
            filename=filename,
            content_type=mime,
            expires_after=expires_after,
        )
    except (RuntimeError, ValueError, TypeError) as exc:
        _LOG.warning("xAI Files upload failed for %s: %s", att_id, exc)
        return None

    exp_iso = result.expires_at_iso()
    try:
        media_store.set_xai_file(
            att_id,
            xai_file_id=result.id,
            xai_file_expires_at=exp_iso,
        )
    except (OSError, ValueError, FileNotFoundError) as exc:
        _LOG.warning("persist xai_file_id failed for %s: %s", att_id, exc)
        # Still return id — caller may use it even if meta write failed.
        return result.id
    return result.id


def completions_file_part(file_id: str) -> dict[str, Any]:
    """Hypothetical Completions multimodal part for a Files API id.

    **Not used on the wire by default** (``completions_file_attach_enabled`` is
    False). Kept so a future smoke-proven attach path has a single shape.
    xAI document attach is documented for Responses (``input_file``), not
    tool-calling Completions — do not enable without live verification.
    """
    return {
        "type": "file",
        "file": {"file_id": file_id},
    }


def is_files_tier_candidate(
    *,
    mime: str,
    filename: str,
    kind: str = "file",
) -> bool:
    """True for PDF / document-like attachments that may use Files tier B."""
    m = (mime or "").lower().strip()
    name = (filename or "").lower()
    k = (kind or "").lower()
    if k in ("image", "audio", "video", "tts_cache"):
        return False
    if m == "application/pdf" or name.endswith(".pdf"):
        return True
    if k == "file" and m and not m.startswith("image/") and not m.startswith("audio/"):
        # Large / binary docs (not already tier-A text) — candidate for Files.
        if m.startswith("text/"):
            return False
        if m in (
            "application/json",
            "application/xml",
            "application/javascript",
            "application/yaml",
            "application/x-yaml",
            "application/csv",
        ):
            return False
        return True
    return False


__all__ = [
    "DEFAULT_EXPIRES_AFTER_SECONDS",
    "XaiFileUploadResult",
    "XaiFilesClient",
    "completions_file_attach_enabled",
    "completions_file_part",
    "ensure_xai_file_id",
    "is_files_tier_candidate",
    "parse_upload_response",
]
