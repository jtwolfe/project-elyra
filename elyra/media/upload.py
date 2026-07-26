"""HTTP media upload helpers: size limits, multipart parse (PR3 / KD15).

Scope: product byte caps, Content-Length policy helpers, multipart form parse,
kind-aware size checks. Stdlib only (no cgi; email.parser for multipart).
Out of scope: HTTP handler wiring, GC, STT/TTS, vision expand.
"""

from __future__ import annotations

import re
import uuid
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import BinaryIO, NamedTuple

# KD15 product limits
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_AUDIO_BYTES = 25 * 1024 * 1024
MAX_FILE_BYTES = 48 * 1024 * 1024
MAX_MEDIA_REQUEST_BYTES = 64 * 1024 * 1024
MAX_ATTACHMENTS_PER_MESSAGE = 8
MAX_JSON_BODY_BYTES = 1 * 1024 * 1024
MAX_CONCURRENT_UPLOADS = 2

# Read body in chunks when streaming to temp (avoid single huge alloc for headers).
_STREAM_CHUNK = 64 * 1024

_BOUNDARY_RE = re.compile(r"boundary=([^;]+)", re.IGNORECASE)


class FormFile(NamedTuple):
    """One file part from multipart/form-data."""

    field_name: str
    filename: str
    data: bytes
    content_type: str | None


def max_bytes_for_kind(kind: str) -> int:
    """Per-kind upload cap (image / audio / everything else as file)."""
    if kind == "image":
        return MAX_IMAGE_BYTES
    if kind == "audio":
        return MAX_AUDIO_BYTES
    # video and file and tts_cache share the file budget
    return MAX_FILE_BYTES


def parse_content_length(header_value: str | None) -> int | None:
    """Parse Content-Length; None if missing/invalid."""
    if header_value is None or header_value == "":
        return None
    try:
        n = int(header_value)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


def extract_boundary(content_type: str) -> str | None:
    """Return multipart boundary string (unquoted) or None."""
    if not content_type or "multipart/" not in content_type.lower():
        return None
    m = _BOUNDARY_RE.search(content_type)
    if not m:
        return None
    raw = m.group(1).strip()
    if raw.startswith('"') and raw.endswith('"') and len(raw) >= 2:
        raw = raw[1:-1]
    return raw or None


def stream_to_temp(
    rfile: BinaryIO,
    length: int,
    tmp_dir: Path,
    *,
    chunk_size: int = _STREAM_CHUNK,
) -> Path:
    """Read exactly ``length`` bytes from ``rfile`` into a temp file under tmp_dir.

    Raises OSError on I/O failure. Caller deletes the temp path when done.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_dir / f".upload-{uuid.uuid4().hex}.part"
    remaining = length
    try:
        with dest.open("wb") as out:
            while remaining > 0:
                n = min(chunk_size, remaining)
                chunk = rfile.read(n)
                if not chunk:
                    break
                out.write(chunk)
                remaining -= len(chunk)
        if remaining != 0:
            dest.unlink(missing_ok=True)
            raise OSError(
                f"short body read: expected {length} bytes, missing {remaining}"
            )
        return dest
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def parse_multipart_files(body: bytes, content_type: str) -> list[FormFile]:
    """Parse multipart/form-data body; return file parts (filename present).

    Non-file fields are ignored. Uses stdlib email.parser (no cgi).
    """
    if not body:
        return []
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode(
        "utf-8", errors="replace"
    )
    msg = BytesParser(policy=policy.default).parsebytes(header + body)
    out: list[FormFile] = []
    if not msg.is_multipart():
        return out
    for part in msg.iter_parts():
        filename = part.get_filename()
        if not filename:
            continue
        name = part.get_param("name", header="content-disposition") or "file"
        if isinstance(name, tuple):
            name = str(name[0]) if name else "file"
        name = str(name)
        data = part.get_payload(decode=True)
        if data is None:
            data = b""
        elif isinstance(data, str):
            data = data.encode("utf-8", errors="replace")
        ctype = part.get_content_type()
        if ctype == "text/plain" and part.get("Content-Type") is None:
            ctype = None
        out.append(
            FormFile(
                field_name=name,
                filename=str(filename),
                data=bytes(data),
                content_type=ctype if ctype else None,
            )
        )
    return out


def parse_multipart_fields(body: bytes, content_type: str) -> dict[str, str]:
    """Parse non-file text fields from multipart/form-data."""
    if not body:
        return {}
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode(
        "utf-8", errors="replace"
    )
    msg = BytesParser(policy=policy.default).parsebytes(header + body)
    fields: dict[str, str] = {}
    if not msg.is_multipart():
        return fields
    for part in msg.iter_parts():
        if part.get_filename():
            continue
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        if isinstance(name, tuple):
            name = str(name[0]) if name else ""
        name = str(name)
        data = part.get_payload(decode=True)
        if data is None:
            text = ""
        elif isinstance(data, bytes):
            text = data.decode("utf-8", errors="replace")
        else:
            text = str(data)
        fields[name] = text
    return fields
