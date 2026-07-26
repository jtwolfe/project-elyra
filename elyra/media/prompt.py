"""Meal-time multimodal expansion for Chat Completions (KD5, KD6, KD20, KD25).

Scope: inventory text for history attachment rows; full vision + text-extract
for the protected wake message; strip host-only fields before Completions.
In scope: expand_meal_for_provider, strip_meal_wire_fields, inventory format,
tier-A text extract (always, incl. best-effort PDF), local fail-closed vision
skip, Files tier B optional upload hook, Completions file attach gated off.
Out of scope: JSONL writes, TTS, STT, Responses API rewrite, glass UI.

Glass JSONL stays string content + attachments[]; base64 exists only in memory
on the Completions wire for the wake row.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Any, Mapping, Protocol, Sequence

_LOG = logging.getLogger(__name__)

# Vision caps (design security / product limits).
MAX_VISION_IMAGES = 4
MAX_VISION_IMAGE_BYTES_TOTAL = 20 * 1024 * 1024  # 20 MiB decoded

# Tier A text extract: small text-like files + best-effort PDF (PR9).
TEXT_EXTRACT_MAX_BYTES = 256 * 1024  # 256 KiB
PDF_EXTRACT_MAX_BYTES = 48 * 1024 * 1024  # KD15 file cap for PDF best-effort

# Local / non-xAI notice when wake would have expanded images.
_LOCAL_VISION_NOTICE = (
    "[host notice: vision/image expansion requires xAI provider; "
    "showing attachment inventory only]"
)

# PR9: PDF / doc not inlined into Completions text (extract failed + no attach).
_NOT_INLINED_NOTICE = "[host notice: file pdf not_inlined]"

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


class _XaiFilesClientLike(Protocol):
    def upload_bytes(
        self,
        data: bytes,
        *,
        filename: str,
        purpose: str = "assistants",
        expires_after: int | None = ...,
        content_type: str = "application/octet-stream",
    ) -> Any: ...


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
        "xai_file_id",
        "xai_file_expires_at",
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


def is_pdf_attachment(*, mime: str, filename: str, kind: str = "") -> bool:
    m = (mime or "").lower().strip()
    name = (filename or "").lower()
    if m == "application/pdf" or name.endswith(".pdf"):
        return True
    if (kind or "").lower() == "file" and name.endswith(".pdf"):
        return True
    return False


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


# Parenthesized PDF string literals: (Hello) / (Hello \(world\))
_PDF_PAREN_STRING_RE = re.compile(
    rb"\(((?:\\.|[^\\()]+)*)\)\s*Tj",
    re.IGNORECASE,
)
_PDF_HEX_STRING_RE = re.compile(rb"<([0-9A-Fa-f\s]+)>\s*Tj")


def extract_pdf_text_best_effort(data: bytes) -> str | None:
    """Best-effort PDF text without new hard deps (PR9 / KD21).

    Extracts ``(...) Tj`` and ``<hex> Tj`` show-string operators. Not a full
    PDF parser — enough for simple text PDFs / fixtures; returns None when
    nothing useful is found.
    """
    if not data or not data.startswith(b"%PDF"):
        return None
    if len(data) > PDF_EXTRACT_MAX_BYTES:
        return None
    chunks: list[str] = []
    for m in _PDF_PAREN_STRING_RE.finditer(data):
        raw = m.group(1)
        # Unescape common PDF string escapes.
        try:
            unescaped = (
                raw.replace(b"\\n", b"\n")
                .replace(b"\\r", b"\r")
                .replace(b"\\t", b"\t")
                .replace(b"\\(", b"(")
                .replace(b"\\)", b")")
                .replace(b"\\\\", b"\\")
            )
            text = unescaped.decode("latin-1", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        text = text.strip()
        if text:
            chunks.append(text)
    for m in _PDF_HEX_STRING_RE.finditer(data):
        hex_s = re.sub(rb"\s+", b"", m.group(1))
        if len(hex_s) % 2:
            continue
        try:
            raw = bytes.fromhex(hex_s.decode("ascii"))
            text = raw.decode("latin-1", errors="replace").strip()
        except (ValueError, UnicodeError):
            continue
        if text:
            chunks.append(text)
    if not chunks:
        # No show-string operators found — refuse crude whole-file string scrape
        # (would inline PDF structural tokens as "content"). Caller notes not_inlined.
        return None
    text = "\n".join(chunks).strip()
    return text or None


def _fence_extract(filename: str, text: str, *, fence_lang: str = "") -> str:
    return f"```{fence_lang}\n# file: {filename}\n{text}\n```"


def extract_text_for_attachment(
    att: Mapping[str, Any],
    media_store: _MediaReadable | None,
) -> str | None:
    """Tier A: return fenced text with filename header, or None if not extractable.

    Always attempts supported text MIME and best-effort PDF (PR9). Size caps
    apply; no new hard dependencies.
    """
    if media_store is None:
        return None
    aid = str(att.get("id") or "")
    if not aid:
        return None
    filename = str(att.get("filename") or "file")
    mime = str(att.get("mime") or "")
    kind = str(att.get("kind") or "")
    size = att.get("byte_size")
    try:
        size_i = int(size) if size is not None else -1
    except (TypeError, ValueError):
        size_i = -1

    is_pdf = is_pdf_attachment(mime=mime, filename=filename, kind=kind)

    if is_pdf:
        if size_i > PDF_EXTRACT_MAX_BYTES:
            return None
        try:
            data = media_store.read_bytes(aid)
        except (OSError, FileNotFoundError, ValueError) as exc:
            _LOG.debug("pdf extract read failed for %s: %s", aid, exc)
            return None
        if len(data) > PDF_EXTRACT_MAX_BYTES:
            return None
        pdf_text = extract_pdf_text_best_effort(data)
        if not pdf_text:
            return None
        return _fence_extract(filename, pdf_text)

    # Text-like files.
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
    return _fence_extract(filename, text, fence_lang=fence_lang)


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


def _needs_not_inlined_notice(
    att: Mapping[str, Any],
    *,
    extracted: bool,
) -> bool:
    """PDF/docs without successful extract and without Completions file attach."""
    if extracted:
        return False
    mime = str(att.get("mime") or "")
    filename = str(att.get("filename") or "")
    kind = str(att.get("kind") or "file")
    if is_pdf_attachment(mime=mime, filename=filename, kind=kind):
        return True
    # Non-image binary files that were not text-extracted.
    if kind == "file" and not mime.startswith("text/") and mime not in _TEXT_MIME_EXACT:
        if kind not in ("image", "audio", "video"):
            # Only note when it looks like a document candidate.
            lower = filename.lower()
            if lower.endswith((".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx")):
                return True
    return False


def expand_meal_for_provider(
    messages: Sequence[Mapping[str, Any]],
    *,
    glass_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    wake_message_id: str | None = None,
    media_store: _MediaReadable | None = None,
    provider: str = "xai",
    expand_last_user_images: bool = False,
    xai_files_client: _XaiFilesClientLike | None = None,
    upload_files_to_xai: bool = False,
) -> list[dict[str, Any]]:
    """Return a **new** message list with meal-time inventory + wake vision.

    Correlates history rows via ``msg["id"]`` only (KD25). Invoked on every
    ``rebuild_outer`` (KD20). Idempotent for the same inputs (aside from
    optional Files upload side effects when ``upload_files_to_xai`` is set).

    * All history rows with resolvable id + attachments → inventory text.
    * Full vision ``image_url`` parts + tier-A text extract: wake row only
      (``id == wake_message_id``), and only when provider is xAI with vision
      enabled.
    * PDF/docs: always attempt extract; on failure inventory notes
      ``file pdf not_inlined`` (PR9 / KD5). Completions Files attach is off
      unless ``ELYRA_XAI_FILES_ATTACH=1`` **and** a stored ``xai_file_id``
      exists (unproven with tools — default extract+inventory only).
    * Optional ``upload_files_to_xai`` + ``xai_files_client``: persist
      ``xai_file_id`` even when wire-attach is off.
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

    from elyra.media.xai_files import (
        completions_file_attach_enabled,
        completions_file_part,
        ensure_xai_file_id,
        is_files_tier_candidate,
    )

    attach_enabled = completions_file_attach_enabled()
    ensure_fn = ensure_xai_file_id
    file_part_fn = completions_file_part
    is_candidate_fn = is_files_tier_candidate

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
            # Tier A text extracts into the text part (wake only) — always.
            extracts: list[str] = []
            extracted_ids: set[str] = set()
            file_parts: list[dict[str, Any]] = []
            for a in atts:
                extracted = extract_text_for_attachment(a, media_store)
                if extracted:
                    extracts.append(extracted)
                    extracted_ids.add(str(a.get("id") or ""))
                # Optional Files upload (tier B storage) — does not require attach.
                if (
                    upload_files_to_xai
                    and xai_files_client is not None
                    and media_store is not None
                    and ensure_fn is not None
                    and is_candidate_fn is not None
                    and is_candidate_fn(
                        mime=str(a.get("mime") or ""),
                        filename=str(a.get("filename") or ""),
                        kind=str(a.get("kind") or "file"),
                    )
                ):
                    aid = str(a.get("id") or "")
                    if aid:
                        fid = ensure_fn(
                            aid,
                            media_store=media_store,  # type: ignore[arg-type]
                            client=xai_files_client,  # type: ignore[arg-type]
                        )
                        if fid:
                            a = dict(a)
                            a["xai_file_id"] = fid
                # Completions attach only when smoke-gated env is on (default off).
                if (
                    attach_enabled
                    and file_part_fn is not None
                    and a.get("xai_file_id")
                    and is_candidate_fn is not None
                    and is_candidate_fn(
                        mime=str(a.get("mime") or ""),
                        filename=str(a.get("filename") or ""),
                        kind=str(a.get("kind") or "file"),
                    )
                ):
                    file_parts.append(file_part_fn(str(a["xai_file_id"])))

            if extracts:
                text = text + "\n\n" + "\n\n".join(extracts)

            # not_inlined notice for PDFs/docs without extract and without attach parts.
            not_inlined = False
            for a in atts:
                aid = str(a.get("id") or "")
                if _needs_not_inlined_notice(a, extracted=aid in extracted_ids):
                    # If we actually attached a file part, content is "inlined" via Files.
                    if attach_enabled and a.get("xai_file_id") and file_parts:
                        continue
                    not_inlined = True
                    break
            if not_inlined and _NOT_INLINED_NOTICE not in text:
                text = f"{text}\n\n{_NOT_INLINED_NOTICE}"

            extra_parts: list[dict[str, Any]] = list(file_parts)
            if vision_ok:
                image_parts = _build_image_parts(atts, media_store)
                extra_parts = image_parts + extra_parts

            if extra_parts:
                new_msg["content"] = [
                    {"type": "text", "text": text},
                    *extra_parts,
                ]
            else:
                if not vision_ok:
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
    "PDF_EXTRACT_MAX_BYTES",
    "TEXT_EXTRACT_MAX_BYTES",
    "append_inventory_to_content",
    "assemble_outer_meal_with_media",
    "expand_meal_for_provider",
    "extract_pdf_text_best_effort",
    "extract_text_for_attachment",
    "format_inventory_block",
    "index_glass",
    "is_pdf_attachment",
    "is_text_extractable",
    "strip_meal_wire_fields",
]
