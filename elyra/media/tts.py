"""Host TTS proxy + disk cache for glass play-on-message (PR7 / KD3).

Scope: unary ``POST https://api.x.ai/v1/tts`` of **saved** message text only;
cache under ``data/media/tts/`` keyed by ``(message_id, voice_id, language,
output_profile)``. Never creates glass rows; never calls chat_completion.

In scope: synthesize, cache path/read/write, 15k char guard, empty-text refuse,
kill switch ``ELYRA_TTS=0``, local/provider fail-closed helpers.
Out of scope: STT, streaming WS, glass UI wiring.
  Rate limits enforced at the HTTP layer (``elyra.media.limits``, PR10).

"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from elyra.config import ElyraPaths, resolve_paths
from elyra.media.store import ensure_media_dirs, media_root

_LOG = logging.getLogger(__name__)

# Env kill switch: unset or "1" = enabled; "0" = disabled (design §Feature flags).
ENV_ELYRA_TTS = "ELYRA_TTS"

TTS_PATH = "/tts"
TTS_MAX_CHARS = 15_000
TTS_DEFAULT_VOICE = "eve"
TTS_DEFAULT_LANGUAGE = "en"
# Maps to codec/sample_rate/bit_rate; xAI default is mp3 @ 24k / 128 kbps.
TTS_DEFAULT_PROFILE = "mp3_24k_128"

# Cache filename token: alphanumeric + ._- only, max 80 chars (design §TTS).
_SAFE_CACHE_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Optional injectable HTTP POST for tests: (url, headers, body_bytes, timeout) → bytes
SynthesizeHttp = Callable[[str, dict[str, str], bytes, float], bytes]


class TtsError(Exception):
    """Structured TTS failure with status-safe reason code."""

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
class TtsCacheResult:
    """Audio bytes from cache or fresh synthesis."""

    audio: bytes
    content_type: str
    cache_hit: bool
    voice_id: str
    language: str
    output_profile: str
    cache_path: Path


def tts_enabled() -> bool:
    """True unless emergency kill switch ``ELYRA_TTS=0``."""
    raw = os.environ.get(ENV_ELYRA_TTS)
    if raw is None or raw.strip() == "":
        return True
    return raw.strip() not in ("0", "false", "False", "no", "off")


def safe_cache_token(value: str) -> str:
    """Path-safe single segment for cache filenames (design: ``safe()``)."""
    s = _SAFE_CACHE_RE.sub("_", (value or "").strip()).strip("._") or "x"
    return s[:80]


def tts_cache_dir(paths: ElyraPaths | None = None) -> Path:
    p = paths or resolve_paths()
    ensure_media_dirs(p)
    return media_root(p) / "tts"


def cache_filename(
    message_id: str,
    voice_id: str,
    language: str,
    output_profile: str,
) -> str:
    """``{msg}__{voice}__{lang}__{profile}.mp3`` with sanitized tokens."""
    return (
        f"{safe_cache_token(message_id)}__"
        f"{safe_cache_token(voice_id)}__"
        f"{safe_cache_token(language)}__"
        f"{safe_cache_token(output_profile)}.mp3"
    )


def cache_path_for(
    message_id: str,
    voice_id: str,
    language: str,
    output_profile: str,
    *,
    paths: ElyraPaths | None = None,
) -> Path:
    return tts_cache_dir(paths) / cache_filename(
        message_id, voice_id, language, output_profile
    )


def profile_to_output_format(output_profile: str) -> dict[str, Any] | None:
    """Map product profile id → xAI ``output_format`` object, or None for default.

    Known profile ``mp3_24k_128`` matches xAI defaults; we still send it so the
    cache key stays explicit and future profiles can diverge.
    """
    profile = (output_profile or TTS_DEFAULT_PROFILE).strip()
    if profile == "mp3_24k_128":
        return {"codec": "mp3", "sample_rate": 24000, "bit_rate": 128000}
    # Unknown profiles: omit and let xAI default (still keyed by profile string).
    return None


def validate_text_for_tts(text: str) -> str:
    """Return stripped text or raise TtsError (empty_text / text_too_long)."""
    if not isinstance(text, str) or not text.strip():
        raise TtsError("empty_text", "message content is empty")
    cleaned = text  # use full stored content (incl. internal whitespace), not strip
    if len(cleaned) > TTS_MAX_CHARS:
        raise TtsError(
            "text_too_long",
            f"text exceeds {TTS_MAX_CHARS} characters",
        )
    return cleaned


def synthesize(
    text: str,
    *,
    voice_id: str = TTS_DEFAULT_VOICE,
    language: str = TTS_DEFAULT_LANGUAGE,
    output_profile: str = TTS_DEFAULT_PROFILE,
    bearer_token: str,
    base_url: str = "https://api.x.ai/v1",
    timeout: float = 120.0,
    http_post: SynthesizeHttp | None = None,
    on_remote_success: Callable[[str], None] | None = None,
) -> bytes:
    """POST ``/tts`` and return raw audio bytes.

    Refuses empty / oversize text. Never logs bearer token.

    On network success (non-empty audio), optional ``on_remote_success`` is
    invoked with ``\"tts\"`` for usage metering. Failures never call it.
    """
    body_text = validate_text_for_tts(text)
    voice = (voice_id or TTS_DEFAULT_VOICE).strip() or TTS_DEFAULT_VOICE
    lang = (language or TTS_DEFAULT_LANGUAGE).strip() or TTS_DEFAULT_LANGUAGE
    profile = (output_profile or TTS_DEFAULT_PROFILE).strip() or TTS_DEFAULT_PROFILE
    token = (bearer_token or "").strip()
    if not token:
        raise TtsError("credential_unavailable", "missing bearer token")

    payload: dict[str, Any] = {
        "text": body_text,
        "voice_id": voice,
        "language": lang,
    }
    out_fmt = profile_to_output_format(profile)
    if out_fmt is not None:
        payload["output_format"] = out_fmt

    url = base_url.rstrip("/") + (
        TTS_PATH if TTS_PATH.startswith("/") else f"/{TTS_PATH}"
    )
    raw_body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }

    if http_post is not None:
        audio = http_post(url, headers, raw_body, timeout)
    else:
        request = urllib.request.Request(
            url,
            data=raw_body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                audio = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            # Never include Authorization values in the message.
            raise TtsError(
                f"tts_http_{exc.code}",
                f"tts HTTP {exc.code}: {detail}",
                http_status=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise TtsError(
                "tts_connection_failed",
                f"tts connection failed: {exc.reason}",
            ) from exc

    if not audio:
        raise TtsError("tts_empty_audio", "tts response had no audio bytes")
    if on_remote_success is not None:
        try:
            on_remote_success("tts")
        except Exception:  # noqa: BLE001 — metering must not fail the call
            _LOG.debug("tts.on_remote_success failed", exc_info=True)
    return audio


def read_cache(path: Path) -> bytes | None:
    """Return cached audio bytes, or None if missing/empty."""
    try:
        if not path.is_file():
            return None
        data = path.read_bytes()
        return data if data else None
    except OSError:
        return None


def write_cache(path: Path, audio: bytes) -> None:
    """Atomic write: temp sibling + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(audio)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def get_or_synthesize(
    text: str,
    *,
    message_id: str,
    voice_id: str = TTS_DEFAULT_VOICE,
    language: str = TTS_DEFAULT_LANGUAGE,
    output_profile: str = TTS_DEFAULT_PROFILE,
    bearer_token: str,
    base_url: str = "https://api.x.ai/v1",
    timeout: float = 120.0,
    paths: ElyraPaths | None = None,
    http_post: SynthesizeHttp | None = None,
    on_remote_success: Callable[[str], None] | None = None,
) -> TtsCacheResult:
    """Return cached audio or synthesize + store under cache key.

    Cache key: ``(message_id, voice_id, language, output_profile)`` (KD3).
    Cache hit (``read_cache``) → +0 media calls. Network path counts +1 only
    via ``synthesize`` (do not double-count here).
    """
    voice = (voice_id or TTS_DEFAULT_VOICE).strip() or TTS_DEFAULT_VOICE
    lang = (language or TTS_DEFAULT_LANGUAGE).strip() or TTS_DEFAULT_LANGUAGE
    profile = (output_profile or TTS_DEFAULT_PROFILE).strip() or TTS_DEFAULT_PROFILE
    mid = (message_id or "").strip()
    if not mid:
        raise TtsError("invalid_message_id", "message_id required for cache key")

    # Validate text before any network (and for empty even on cache-path lookup
    # when text is known — callers may skip when loading empty rows).
    validate_text_for_tts(text)

    cpath = cache_path_for(mid, voice, lang, profile, paths=paths)
    cached = read_cache(cpath)
    if cached is not None:
        _LOG.info(
            "tts.cache_hit message_id=%s voice=%s lang=%s profile=%s bytes=%s",
            mid,
            voice,
            lang,
            profile,
            len(cached),
        )
        return TtsCacheResult(
            audio=cached,
            content_type="audio/mpeg",
            cache_hit=True,
            voice_id=voice,
            language=lang,
            output_profile=profile,
            cache_path=cpath,
        )

    audio = synthesize(
        text,
        voice_id=voice,
        language=lang,
        output_profile=profile,
        bearer_token=bearer_token,
        base_url=base_url,
        timeout=timeout,
        http_post=http_post,
        on_remote_success=on_remote_success,
    )
    try:
        write_cache(cpath, audio)
    except OSError as exc:
        _LOG.warning("tts.cache_write_failed path=%s err=%s", cpath.name, exc)

    _LOG.info(
        "tts.generate message_id=%s voice=%s lang=%s profile=%s bytes=%s",
        mid,
        voice,
        lang,
        profile,
        len(audio),
    )
    return TtsCacheResult(
        audio=audio,
        content_type="audio/mpeg",
        cache_hit=False,
        voice_id=voice,
        language=lang,
        output_profile=profile,
        cache_path=cpath,
    )
