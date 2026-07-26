"""Meal-time multimodal expansion for Chat Completions (KD6, KD20, KD25).

Scope: inventory text for history attachment rows; full vision + text-extract
for the protected wake message; strip host-only fields before Completions.
In scope: expand_meal_for_provider, strip_meal_wire_fields, inventory format,
tier-A text extract for small files, local fail-closed vision skip.
Out of scope: JSONL writes, TTS, STT, Files API attach (PR9), glass UI.

Glass JSONL stays string content + attachments[]; base64 exists only in memory
on the Completions wire for the wake row.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Any, Mapping, Protocol, Sequence

_LOG = logging.getLogger(__name__)

# Vision caps (design security / product limits).
MAX_VISION_IMAGES = 4
MAX_VISION_IMAGE_BYTES_TOTAL = 20 * 1024 * 1024  # 20 MiB decoded

# Tier A text extract: small text-like files only.
TEXT_EXTRACT_MAX_BYTES = 256 * 1024  # 256 KiB

# Local / non-xAI notice when wake would have expanded images.
_LOCAL_VISION_NOTICE = (
    "[host notice: vision/image expansion requires xAI provider; "
    "showing attachment inventory only]"
)

# Legacy glass inventory prose (PR3 dual-path) — do not double-append.
_LEGACY_INVENTORY_MARKERS = (
    "\n---\n**Attachments**",
    "\n---\n**attachments**",
)

# MIME / extension allow-list for tier-A extract.
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_EXACT = frozenset(
    {
        "application/json",
        "application/xml",
        "application/javascript",
        "application/x-javascript",
        "application/typescript",
        "application/x-yaml",
        "application/yaml",
        "application/csv",
    }
)
_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".json",
        ".csv",
        ".tsv",
        ".py",
        ".js",
        ".ts",
        ".tsx",
        ".jsx",
        ".html",
        ".htm",
        ".css",
        ".xml",
        ".yaml",
        ".yml",
        ".toml",
        ".ini",
        ".cfg",
        ".conf",
        ".sh",
        ".bash",
        ".zsh",
        ".rs",
        ".go",
        ".java",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".rb",
        ".php",
        ".sql",
        ".r",
        ".lua",
        ".swift",
        ".kt",
        ".scala",
        ".env",
        ".log",
    }
)


class _MediaReadable(Protocol):
    def get(self, att_id: str) -> Any: ...

    def read_bytes(self, att_id: str) -> bytes: ...


def index_glass(
    glass_rows: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Mapping[str, Any]]:
    """Build ``id → glass row`` lookup for expand correlation (KD25)."""
    out: dict[str, Mapping[str, Any]] = {}
    if not glass_rows:
        return out
    for row in glass_rows:
        if not isinstance(row, Mapping):
            continue
        mid = row.get("id")
        if mid is None:
            continue
        out[str(mid)] = row
    return out


def strip_meal_wire_fields(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Drop host-only keys; keep ``role`` + ``content`` for Completions wire.

    ``content`` may be a string or a multimodal list of parts.
    """
    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role is None:
            continue
        out.append({"role": role, "content": content if content is not None else ""})
    return out


def _env_flag_enabled(name: str) -> bool:
    """Unset or ``1`` = enabled; ``0`` = emergency kill switch."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return True
    return raw.strip() not in ("0", "false", "False", "no", "NO")


def _has_legacy_inventory(content: str) -> bool:
    if not content:
        return False
    # Double-inventory guard: trailing legacy glass disclaimer block.
    for marker in _LEGACY_INVENTORY_MARKERS:
        if marker in content:
            return True
    return False


def _normalize_attachments(
    raw: Any,
) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if hasattr(item, "to_dict"):
            d = item.to_dict()  # type: ignore[union-attr]
            if isinstance(d, dict):
                out.append(d)
        elif isinstance(item, Mapping):
            out.append(dict(item))
    return out


def _attachments_for_message(
    msg_id: str,
    *,
    glass_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    row = glass_by_id.get(msg_id)
    if row is None:
        return []
    return _normalize_attachments(row.get("attachments"))


def _filter_model_attachments(atts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Drop tts_cache and malformed rows for meal inventory / expand."""
    out: list[dict[str, Any]] = []
    for a in atts:
        kind = str(a.get("kind") or "file")
        if kind == "tts_cache":
            continue
        aid = a.get("id")
        if not aid:
            continue
        out.append(dict(a))
    return out


def _enrich_attachment(
    att: Mapping[str, Any],
    media_store: _MediaReadable | None,
) -> dict[str, Any]:
    """Fill missing inventory fields from media meta when available."""
    d = dict(att)
    if media_store is None:
        return d
    aid = str(d.get("id") or "")
    if not aid:
        return d
    try:
        meta = media_store.get(aid)
    except Exception:  # noqa: BLE001 — inventory best-effort
        return d
    if meta is None:
        return d
    # Prefer meta for durable fields when glass snapshot is partial.
    for key in (
        "filename",
        "kind",
        "mime",
        "byte_size",
        "sandbox_relpath",
        "sha256",
    ):
        if d.get(key) in (None, "") and getattr(meta, key, None) not in (None, ""):
            d[key] = getattr(meta, key)
    return d


def format_inventory_block(attachments: Sequence[Mapping[str, Any]]) -> str:
    """KD6 meal-time inventory block (never written to JSONL / TTS).

    Format::

        [attachments]
        - {att_id}\\t{filename}\\t{kind}\\t{mime}\\t{byte_size}\\t{sandbox_relpath or "-"}
    """
    lines = ["[attachments]"]
    for a in attachments:
        aid = str(a.get("id") or "")
        filename = str(a.get("filename") or "file")
        kind = str(a.get("kind") or "file")
        mime = str(a.get("mime") or "application/octet-stream")
        size = a.get("byte_size")
        if size is None:
            size_s = "-"
        else:
            size_s = str(int(size))
        rel = a.get("sandbox_relpath")
        rel_s = str(rel) if rel else "-"
        lines.append(f"- {aid}\t{filename}\t{kind}\t{mime}\t{size_s}\t{rel_s}")
    return "\n".join(lines)


def append_inventory_to_content(
    content: str,
    attachments: Sequence[Mapping[str, Any]],
) -> str:
    """Append inventory block after original content (blank line separator)."""
    base = content if isinstance(content, str) else (str(content) if content else "")
    if not attachments:
        return base
    if _has_legacy_inventory(base):
        return base
    block = format_inventory_block(attachments)
    if base:
        return f"{base}\n\n{block}"
    # Media-only: leading blank line then inventory (normative example).
    return f"\n{block}"


def is_text_extractable(*, mime: str, filename: str, byte_size: int) -> bool:
    if byte_size < 0 or byte_size > TEXT_EXTRACT_MAX_BYTES:
        return False
    m = (mime or "").lower().strip()
    if any(m.startswith(p) for p in _TEXT_MIME_PREFIXES):
        return True
    if m in _TEXT_MIME_EXACT:
        return True
    name = (filename or "").lower()
    for ext in _TEXT_EXTENSIONS:
        if name.endswith(ext):
            return True
    return False


def extract_text_for_attachment(
    att: Mapping[str, Any],
    media_store: _MediaReadable | None,
) -> str | None:
    """Tier A: return fenced text with filename header, or None if not extractable."""
    if media_store is None:
        return None
    aid = str(att.get("id") or "")
    if not aid:
        return None
    filename = str(att.get("filename") or "file")
    mime = str(att.get("mime") or "")
    size = att.get("byte_size")
    try:
        size_i = int(size) if size is not None else -1
    except (TypeError, ValueError):
        size_i = -1
    # If size unknown, try read with hard cap.
    if size_i < 0:
        size_i = TEXT_EXTRACT_MAX_BYTES  # allow attempt; re-check after read
    if not is_text_extractable(mime=mime, filename=filename, byte_size=size_i):
        return None
    try:
        data = media_store.read_bytes(aid)
    except (OSError, FileNotFoundError, ValueError) as exc:
        _LOG.debug("text extract read failed for %s: %s", aid, exc)
        return None
    if len(data) > TEXT_EXTRACT_MAX_BYTES:
        return None
    # Re-check mime/filename after knowing size.
    if not is_text_extractable(mime=mime, filename=filename, byte_size=len(data)):
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
    # Fence with filename header (design: inline fenced text).
    fence_lang = ""
    lower = filename.lower()
    if lower.endswith(".json"):
        fence_lang = "json"
    elif lower.endswith(".md") or lower.endswith(".markdown"):
        fence_lang = "markdown"
    elif lower.endswith(".py"):
        fence_lang = "python"
    elif lower.endswith(".csv"):
        fence_lang = "csv"
    return f"```{fence_lang}\n# file: {filename}\n{text}\n```"


def _vision_allowed(provider: str) -> bool:
    if not _env_flag_enabled("ELYRA_MEDIA"):
        return False
    if not _env_flag_enabled("ELYRA_VISION"):
        return False
    return (provider or "").lower() == "xai"


def _build_image_parts(
    attachments: Sequence[Mapping[str, Any]],
    media_store: _MediaReadable | None,
    *,
    max_images: int = MAX_VISION_IMAGES,
    max_total_bytes: int = MAX_VISION_IMAGE_BYTES_TOTAL,
) -> list[dict[str, Any]]:
    """Build OpenAI-style image_url parts from image attachments (in-memory only)."""
    if media_store is None:
        return []
    parts: list[dict[str, Any]] = []
    total_bytes = 0
    for att in attachments:
        if len(parts) >= max_images:
            break
        kind = str(att.get("kind") or "")
        mime = str(att.get("mime") or "")
        if kind != "image" and not mime.startswith("image/"):
            continue
        aid = str(att.get("id") or "")
        if not aid:
            continue
        try:
            data = media_store.read_bytes(aid)
        except (OSError, FileNotFoundError, ValueError) as exc:
            _LOG.warning("vision blob read failed for %s: %s", aid, exc)
            continue
        if not data:
            continue
        if total_bytes + len(data) > max_total_bytes:
            _LOG.warning(
                "vision expand: total image bytes cap (%d) hit; skipping %s",
                max_total_bytes,
                aid,
            )
            break
        use_mime = mime if mime.startswith("image/") else "image/png"
        b64 = base64.b64encode(data).decode("ascii")
        parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{use_mime};base64,{b64}"},
            }
        )
        total_bytes += len(data)
    return parts


def expand_meal_for_provider(
    messages: Sequence[Mapping[str, Any]],
    *,
    glass_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    wake_message_id: str | None = None,
    media_store: _MediaReadable | None = None,
    provider: str = "xai",
    expand_last_user_images: bool = False,
) -> list[dict[str, Any]]:
    """Return a **new** message list with meal-time inventory + wake vision.

    Correlates history rows via ``msg["id"]`` only (KD25). Invoked on every
    ``rebuild_outer`` (KD20). Idempotent for the same inputs.

    * All history rows with resolvable id + attachments → inventory text.
    * Full vision ``image_url`` parts + tier-A text extract: wake row only
      (``id == wake_message_id``), and only when provider is xAI with vision
      enabled.
    * Local / non-xAI: inventory + fail-closed notice on wake; no data URLs.
    * Never mutates glass JSONL; never writes base64 to store.

    ``expand_last_user_images`` is reserved (off by default in v1).
    """
    del expand_last_user_images  # v1: wake-only full expand
    glass = glass_by_id or {}
    media_on = _env_flag_enabled("ELYRA_MEDIA")
    vision_ok = _vision_allowed(provider)
    wake_id = str(wake_message_id) if wake_message_id else None
    logged_missing_id = False

    out: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, str):
            # Already expanded or non-string — pass through shallow copy.
            row = dict(msg)
            out.append(row)
            continue

        mid = msg.get("id")
        mid_s = str(mid) if mid is not None else None
        new_msg: dict[str, Any] = {"role": role, "content": content}
        if mid is not None:
            new_msg["id"] = mid

        if not media_on:
            out.append(new_msg)
            continue

        if mid_s is None:
            # system / orient / history without id: leave content as-is.
            out.append(new_msg)
            continue

        raw_atts = _attachments_for_message(mid_s, glass_by_id=glass)
        if not raw_atts:
            # History row with id but no attachments (or unresolvable).
            if mid_s not in glass and not logged_missing_id:
                _LOG.debug(
                    "expand_meal: history id %r not in glass_by_id; inventory skip",
                    mid_s,
                )
                logged_missing_id = True
            out.append(new_msg)
            continue

        atts = [
            _enrich_attachment(a, media_store)
            for a in _filter_model_attachments(raw_atts)
        ]
        if not atts:
            out.append(new_msg)
            continue

        is_wake = wake_id is not None and mid_s == wake_id
        text = append_inventory_to_content(content, atts)

        if is_wake:
            # Tier A text extracts into the text part (wake only).
            extracts: list[str] = []
            for a in atts:
                extracted = extract_text_for_attachment(a, media_store)
                if extracted:
                    extracts.append(extracted)
            if extracts:
                text = text + "\n\n" + "\n\n".join(extracts)

            if vision_ok:
                image_parts = _build_image_parts(atts, media_store)
                if image_parts:
                    new_msg["content"] = [
                        {"type": "text", "text": text},
                        *image_parts,
                    ]
                else:
                    new_msg["content"] = text
            else:
                # Local / vision kill-switch: inventory + fail-closed notice.
                has_image = any(
                    str(a.get("kind") or "") == "image"
                    or str(a.get("mime") or "").startswith("image/")
                    for a in atts
                )
                if has_image and (provider or "").lower() != "xai":
                    text = f"{text}\n\n{_LOCAL_VISION_NOTICE}"
                elif has_image and not _env_flag_enabled("ELYRA_VISION"):
                    text = (
                        f"{text}\n\n"
                        "[host notice: ELYRA_VISION=0; vision expansion skipped]"
                    )
                new_msg["content"] = text
        else:
            # Inventory only for non-wake attachment rows (user and assistant).
            new_msg["content"] = text

        out.append(new_msg)

    return out


def assemble_outer_meal_with_media(
    *,
    assemble_fn: Any,
    expand_kwargs: Mapping[str, Any] | None = None,
    strip: bool = True,
    **assemble_kwargs: Any,
) -> list[dict[str, Any]]:
    """Convenience: ``assemble(retain_ids=True) → expand → strip``.

    ``assemble_fn`` is typically :func:`elyra.loop.context.assemble_outer_meal`.
    """
    meal = assemble_fn(retain_ids=True, **assemble_kwargs)
    ek = dict(expand_kwargs or {})
    expanded = expand_meal_for_provider(meal, **ek)
    if strip:
        return strip_meal_wire_fields(expanded)
    return expanded


__all__ = [
    "MAX_VISION_IMAGE_BYTES_TOTAL",
    "MAX_VISION_IMAGES",
    "TEXT_EXTRACT_MAX_BYTES",
    "append_inventory_to_content",
    "assemble_outer_meal_with_media",
    "expand_meal_for_provider",
    "extract_text_for_attachment",
    "format_inventory_block",
    "index_glass",
    "is_text_extractable",
    "strip_meal_wire_fields",
]
