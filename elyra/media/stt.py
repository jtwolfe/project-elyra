"""xAI Speech-to-Text host client (PR6 / KD4, KD9, KD18).

Scope: unary REST ``POST {base}/stt`` with multipart (model + optional language
before file), defensive JSON parse, structured errors, emergency ELYRA_STT kill
switch helper. Bearer injected by caller — never logged.
Out of scope: WebSocket streaming STT, TTS, rate limits (PR10), glass UI.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping
from urllib import error as urllib_error
from urllib import request as urllib_request

_LOG = logging.getLogger(__name__)

DEFAULT_STT_BASE_URL = "https://api.x.ai/v1"
DEFAULT_STT_PATH = "/stt"
DEFAULT_STT_MODEL = "grok-stt"
DEFAULT_STT_TIMEOUT_S = 120.0

# Env emergency kill switch (KD24): unset or "1" = enabled.
_ENV_STT = "ELYRA_STT"
_FALSEY = frozenset({"0", "false", "off", "no", ""})


class SttError(Exception):
    """Structured STT failure (status-safe reason; no secrets)."""

    def __init__(
        self,
        reason: str,
        message: str = "",
        *,
        http_status: int | None = None,
    ) -> None:
        self.reason = reason
        self.http_status = http_status
        super().__init__(message or reason)


@dataclass(frozen=True)
class SttResult:
    """Parsed xAI STT response (KD4)."""

    text: str
    language: str | None = None
    duration_s: float | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


def stt_enabled(env: Mapping[str, str] | None = None) -> bool:
    """True unless ELYRA_STT is explicitly off (0/false/off/no)."""
    environ = env if env is not None else os.environ
    raw = environ.get(_ENV_STT)
    if raw is None:
        return True
    return str(raw).strip().lower() not in _FALSEY


def stt_url(base_url: str = DEFAULT_STT_BASE_URL, path: str = DEFAULT_STT_PATH) -> str:
    """Join base (includes /v1) with path (e.g. /stt) without doubling /v1."""
    base = (base_url or DEFAULT_STT_BASE_URL).rstrip("/")
    p = path if path.startswith("/") else f"/{path}"
    return base + p


def parse_stt_response(data: Any) -> SttResult:
    """Defensive parse of xAI STT JSON.

    Prefer ``text``; accept common alternates. Empty/whitespace text → error.
    Never requires ``words`` / extras.
    """
    if not isinstance(data, dict):
        raise SttError("stt_invalid_json", "STT response is not a JSON object")

    text = data.get("text")
    if text is None:
        # Alternates seen in some gateways / future shapes.
        for key in ("transcript", "transcription", "result"):
            alt = data.get(key)
            if isinstance(alt, str):
                text = alt
                break
            if isinstance(alt, dict) and isinstance(alt.get("text"), str):
                text = alt["text"]
                break
    if not isinstance(text, str):
        raise SttError("stt_empty_text", "STT response missing text field")
    if not text.strip():
        raise SttError("stt_empty_text", "STT returned empty transcript")

    language: str | None = None
    lang_raw = data.get("language")
    if isinstance(lang_raw, str) and lang_raw.strip():
        language = lang_raw.strip()

    duration_s: float | None = None
    dur = data.get("duration")
    if dur is None:
        dur = data.get("duration_s")
    if isinstance(dur, (int, float)) and not isinstance(dur, bool):
        duration_s = float(dur)
    elif isinstance(dur, str):
        try:
            duration_s = float(dur)
        except ValueError:
            duration_s = None

    return SttResult(text=text, language=language, duration_s=duration_s, raw=dict(data))


def encode_stt_multipart(
    file_bytes: bytes,
    *,
    filename: str,
    mime: str,
    model: str = DEFAULT_STT_MODEL,
    language: str | None = None,
    extra_fields: Mapping[str, str] | None = None,
) -> tuple[bytes, str]:
    """Build multipart body with option fields BEFORE file (xAI streamable note).

    Returns ``(body, content_type_header)``.
    """
    boundary = f"----elyraStt{uuid.uuid4().hex}"
    parts: list[bytes] = []

    def _field(name: str, value: str) -> None:
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )

    _field("model", model)
    if language is not None and str(language).strip():
        _field("language", str(language).strip())
    if extra_fields:
        for k, v in extra_fields.items():
            if k in ("model", "language", "file"):
                continue
            _field(str(k), str(v))

    safe_name = (filename or "audio.bin").replace('"', "_")
    ctype = mime or "application/octet-stream"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; '
        f'filename="{safe_name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8")
    parts.append(head + bytes(file_bytes) + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def transcribe(
    file_bytes: bytes,
    *,
    filename: str,
    mime: str,
    bearer_token: str,
    base_url: str = DEFAULT_STT_BASE_URL,
    stt_path: str = DEFAULT_STT_PATH,
    model: str = DEFAULT_STT_MODEL,
    language: str | None = None,
    timeout: float = DEFAULT_STT_TIMEOUT_S,
    urlopen: Any = None,
    on_remote_success: Callable[[str], None] | None = None,
) -> SttResult:
    """POST multipart audio to xAI STT; return parsed transcript.

    ``urlopen`` is injectable for tests (defaults to ``urllib.request.urlopen``).
    Raises ``SttError`` with structured ``reason`` (stt_http_N, stt_empty_text, …).
    Never logs bearer token or full raw body.

    On network success (HTTP 2xx + parse ok), optional ``on_remote_success`` is
    invoked with ``\"stt\"`` for usage metering. Failures never call it.
    """
    if not isinstance(file_bytes, (bytes, bytearray)):
        raise SttError("stt_invalid_audio", "audio must be bytes")
    data = bytes(file_bytes)
    if not data:
        raise SttError("stt_invalid_audio", "empty audio")
    token = (bearer_token or "").strip()
    if not token:
        raise SttError("credential_unavailable", "missing bearer token")

    body, content_type = encode_stt_multipart(
        data,
        filename=filename or "audio.bin",
        mime=mime or "application/octet-stream",
        model=model or DEFAULT_STT_MODEL,
        language=language,
    )
    url = stt_url(base_url, stt_path)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
    }
    req = urllib_request.Request(url, data=body, headers=headers, method="POST")
    opener = urlopen if urlopen is not None else urllib_request.urlopen

    try:
        with opener(req, timeout=timeout) as resp:
            raw_bytes = resp.read()
            status = getattr(resp, "status", None) or getattr(resp, "code", 200)
    except urllib_error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            detail = ""
        code = int(exc.code)
        _LOG.warning("stt.fail http=%s detail=%s", code, detail[:80] if detail else "")
        raise SttError(
            f"stt_http_{code}",
            f"STT HTTP {code}",
            http_status=code,
        ) from exc
    except urllib_error.URLError as exc:
        reason = getattr(exc, "reason", None) or str(exc)
        _LOG.warning("stt.fail connection: %s", reason)
        raise SttError("stt_connection_failed", f"STT connection failed: {reason}") from exc
    except TimeoutError as exc:
        raise SttError("stt_timeout", "STT request timed out") from exc
    except OSError as exc:
        raise SttError("stt_connection_failed", f"STT I/O error: {exc}") from exc

    if status is not None and int(status) >= 400:
        raise SttError(
            f"stt_http_{int(status)}",
            f"STT HTTP {status}",
            http_status=int(status),
        )

    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SttError("stt_invalid_json", "STT response is not valid JSON") from exc

    result = parse_stt_response(payload)
    _LOG.info(
        "stt.ok bytes=%s text_len=%s duration=%s",
        len(data),
        len(result.text),
        result.duration_s,
    )
    if on_remote_success is not None:
        try:
            on_remote_success("stt")
        except Exception:  # noqa: BLE001 — metering must not fail the call
            _LOG.debug("stt.on_remote_success failed", exc_info=True)
    return result
