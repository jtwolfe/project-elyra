"""Host ``view_media`` tool — mid-moment look at path / att_id / url.

Scope: resolve path|att_id|url into MediaStore, add to moment viewing set,
first-wins promote with media_ids (no wake_message_id), list/drop/clear ops.
URL fetch is SSRF-aware (KD-V18). Image + AV Completions perception when
provider/env gates allow (PR2 image; PR4 AV wire; tool honesty matches env).

KD-V1, V11, V13–V16, V18. Tool results stay text-only JSON (KD-V9).
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

from elyra.media.fetch import (
    FetchError,
    FetchedBytes,
    fetch_url_bytes,
    redacted_source_url,
    reject_non_media_payload,
)
from elyra.media.ingest import IngestError, ingest_sandbox_path, resolve_sandbox_file
from elyra.media.store import MediaStore, sniff_mime_and_kind, validate_att_id
from elyra.media.types import Attachment
from elyra.media.upload import MAX_FILE_BYTES, max_bytes_for_kind
from elyra.media.viewing import (
    add_viewing,
    clear_viewing,
    drop_viewing,
    list_viewing,
    list_viewing_att_ids,
)
from elyra.tools.types import ToolContext, ToolResult

_LOG = logging.getLogger(__name__)

_OPS = frozenset({"view", "list", "drop", "clear"})
_DEFAULT_OP = "view"

# Soft guidance thresholds (design KD-V18 / soft large-media).
_SOFT_SIZE_BYTES = 8_000_000
_SOFT_VIDEO_DURATION_S = 10.0
_SOFT_AUDIO_DURATION_S = 15.0

_SOFT_VIDEO_WARN = (
    "Prefer short media: video perception is reliable around ≤10 seconds; "
    "longer clips may be truncated or skipped for Completions expand."
)
_SOFT_AUDIO_WARN = (
    "Prefer short audio clips for Completions expand (soft warn >15s; hard ~30s)."
)
_SOFT_LARGE_WARN = (
    "Large media costs time and tokens; prefer sandbox paths when already local."
)
_SOFT_URL_WARN = (
    "Large downloads cost time and tokens; prefer sandbox paths when already local."
)

# Soft multi-source miss reasons (KD-V14: if only one resolves → use it).
_SOFT_MISS_REASONS = frozenset({"not_found", "unsupported_kind"})
# Always hard even when another source is present.
_HARD_ARG_REASONS = frozenset(
    {
        "invalid_att_id",
        "path_escape",
        "invalid_path",
        "is_directory",
        "file_too_large",
        "invalid_attachment",
        # URL security / integrity failures are never soft-skipped.
        "url_invalid",
        "url_ssrf_blocked",
        "url_redirect_blocked",
        "url_timeout",
        "url_too_large",
        "url_content_type_rejected",
        "url_fetch_failed",
    }
)


def view_media(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Resolve media into the moment viewing set (path and/or att_id and/or url).

    Schema: path?, att_id?, url?, op?, note?
    """
    if not isinstance(args, dict):
        return _err("invalid_args", detail="args must be an object")

    op, op_err = _parse_op(args)
    if op_err is not None:
        return _err(op_err)

    moment_id = (ctx.moment_id or "").strip()
    if not moment_id:
        return _err("no_open_moment")

    entries = _resolve_viewing_entries(ctx)
    if entries is None:
        return _err(
            "viewing_unavailable",
            detail="Host must inject ctx.extras['moment_viewing']",
        )

    if op == "list":
        return _ok(_list_payload(entries))

    if op == "clear":
        n = _clear_viewing_port(ctx, entries)
        return _ok(
            {
                "op": "clear",
                "cleared": n,
                **_list_payload(entries),
                "viewing_dirty": n > 0,
            }
        )

    if op == "drop":
        aid_raw = args.get("att_id")
        if not isinstance(aid_raw, str) or not aid_raw.strip():
            return _err("missing_source", detail="drop requires att_id")
        aid = aid_raw.strip()
        try:
            validate_att_id(aid)
        except ValueError:
            return _err("invalid_att_id", att_id=aid)
        removed = _drop_viewing_port(ctx, entries, aid)
        return _ok(
            {
                "op": "drop",
                "att_id": aid,
                "removed": removed,
                **_list_payload(entries),
                "viewing_dirty": removed,
            }
        )

    # ---- op=view ----
    if not _media_enabled():
        return _err("media_disabled")

    path, att_id, url, src_err = _parse_sources(args)
    if src_err is not None:
        return _err(src_err)

    if path is None and att_id is None and url is None:
        return _err("missing_source")

    note = _optional_note(args)
    try:
        att, source_label, source_url = _resolve_view_attachment(
            path=path,
            att_id=att_id,
            url=url,
            ctx=ctx,
        )
    except IngestError as exc:
        _LOG.info(
            "view_media resolve_failed reason=%s source=path/att detail=%s",
            exc.reason,
            (exc.detail or "-")[:120],
        )
        return _err(exc.reason, detail=exc.detail)
    except FetchError as exc:
        # Redacted URL only — never log query/fragment secrets.
        safe = redacted_source_url(url) if url else "-"
        _LOG.info(
            "view_media fetch_failed reason=%s url=%s detail=%s",
            exc.reason,
            safe or "-",
            (exc.detail or "-")[:120],
        )
        return _err(exc.reason, detail=exc.detail)
    except _ViewResolveError as exc:
        _LOG.info(
            "view_media resolve_failed reason=%s detail=%s",
            exc.reason,
            (exc.detail or "-")[:120],
        )
        return _err(exc.reason, detail=exc.detail, **exc.extra)

    # Membership + dirty (always on successful view, including re-view).
    try:
        _add_to_viewing(ctx, entries, att)
    except ValueError as exc:
        return _err("invalid_att_id", detail=str(exc))

    # First-wins breadcrumb (no wake_message_id). Soft-fail if store missing.
    promoted = _maybe_promote(
        ctx, moment_id, att, note=note, source_url=source_url
    )

    presentation, perception, skip_reason = _presentation_for(att.kind)
    soft_warnings = _soft_warnings(att, from_url=bool(source_url))
    payload: dict[str, Any] = {
        "op": "view",
        "att_id": att.id,
        "kind": att.kind,
        "mime": att.mime,
        "byte_size": att.byte_size,
        "filename": att.filename,
        "source": source_label,
        "expand_next_hop": True,
        "viewing_dirty": True,
        "presentation": presentation,
        "perception": perception,
        "promoted": promoted,
        **_list_payload(entries),
        "note": (
            "Host will force-rebuild outer before next completion and expand "
            "eligible media on the Completions wire. Tool payload has no media bytes."
        ),
    }
    if skip_reason:
        payload["skip_reason"] = skip_reason
        payload["notice"] = (
            f"Media stored and in viewing set; Completions expand is inventory-only "
            f"for this item ({skip_reason}). Provider/env gates and duration/size "
            f"caps apply on the next hop."
        )
    if soft_warnings:
        payload["soft_warnings"] = soft_warnings
    if note:
        payload["view_note"] = note
    if source_url:
        payload["source_url"] = source_url
    _LOG.info(
        "view_media op=view att_id=%s kind=%s source=%s perception=%s "
        "skip_reason=%s viewing_count=%d promoted=%s",
        att.id,
        att.kind,
        source_label,
        perception,
        skip_reason or "-",
        int(payload.get("viewing_count") or 0),
        promoted,
    )
    return _ok(payload)


# ---------------------------------------------------------------------------
# Resolution (KD-V14)
# ---------------------------------------------------------------------------


class _ViewResolveError(Exception):
    def __init__(
        self,
        reason: str,
        *,
        detail: str | None = None,
        **extra: Any,
    ) -> None:
        self.reason = reason
        self.detail = detail
        self.extra = extra
        super().__init__(detail or reason)


def _resolve_view_attachment(
    *,
    path: str | None,
    att_id: str | None,
    url: str | None,
    ctx: ToolContext,
) -> tuple[Attachment, str, str | None]:
    """Resolve path and/or att_id and/or url (KD-V14 soft multi-source + KD-V18).

    Returns ``(attachment, source_label, source_url_or_none)``.

    - Soft-miss (``not_found`` / ``unsupported_kind``): skip that source when
      another is present; if only one resolves, use it.
    - Hard failures (``invalid_att_id``, ``path_escape``, SSRF, size, …): always raise.
    - Path/url **hash first**: multi-source conflict compares sha **before** any
      durable put (no orphan ingest). Reuses existing meta by sha.
    """
    id_att: Attachment | None = None
    id_err: _ViewResolveError | None = None
    path_blob: tuple[bytes, str, str, str, str] | None = None  # data,fname,mime,kind,sha
    path_err: Exception | None = None
    url_blob: tuple[FetchedBytes, str, str] | None = None  # fetched, sha, safe_url

    if att_id is not None:
        try:
            id_att = _get_existing_att(att_id, ctx)
        except _ViewResolveError as exc:
            sole = path is None and url is None
            if exc.reason in _HARD_ARG_REASONS or (
                sole and exc.reason not in _SOFT_MISS_REASONS
            ):
                raise
            if sole:
                raise
            id_err = exc

    if path is not None:
        try:
            data, fname, mime, kind = _read_sandbox_media_bytes(path, ctx)
            sha = hashlib.sha256(data).hexdigest()
            path_blob = (data, fname, mime, kind, sha)
        except IngestError as exc:
            reason = exc.reason
            sole = id_att is None and url is None
            hard = (
                reason in _HARD_ARG_REASONS
                or reason.startswith("os_error:")
                or sole
            )
            if hard:
                raise
            path_err = exc
        except _ViewResolveError as exc:
            sole = id_att is None and url is None
            if exc.reason in _HARD_ARG_REASONS or sole:
                raise
            path_err = exc

    if url is not None:
        try:
            open_fn, gai_fn = _fetch_hooks(ctx)
            fetched = fetch_url_bytes(url, urlopen=open_fn, getaddrinfo=gai_fn)
            sha = hashlib.sha256(fetched.data).hexdigest()
            safe_url = redacted_source_url(fetched.final_url or url)
            # Validate payload kind/size before multi-source compare commits.
            mime, kind = sniff_mime_and_kind(
                fetched.data,
                filename=fetched.filename,
                claimed_mime=fetched.claimed_mime,
            )
            reject_non_media_payload(fetched.data, mime, kind)
            if kind == "tts_cache":
                raise FetchError(
                    "url_content_type_rejected",
                    detail="tts_cache cannot be fetched for view",
                )
            limit = max_bytes_for_kind(kind)
            if len(fetched.data) > limit:
                raise FetchError(
                    "url_too_large",
                    detail=f"{len(fetched.data)} bytes exceeds {kind} max {limit}",
                )
            url_blob = (fetched, sha, safe_url)
        except FetchError:
            # URL security/network/content failures are always hard.
            raise

    labels: list[str] = []
    shas: list[str] = []
    if path_blob is not None:
        labels.append("path")
        shas.append(path_blob[4])
    if id_att is not None:
        labels.append("att_id")
        shas.append(id_att.sha256)
    if url_blob is not None:
        labels.append("url")
        shas.append(url_blob[1])

    if not labels:
        if id_err is not None:
            raise id_err
        if isinstance(path_err, (IngestError, _ViewResolveError)):
            raise path_err
        raise _ViewResolveError("missing_source")

    if len(set(shas)) > 1:
        raise _ViewResolveError(
            "ambiguous_source",
            detail=(
                "sources resolve to different media "
                f"(sha {shas[0][:12]}… vs {shas[-1][:12]}…)"
            ),
            att_id=id_att.id if id_att is not None else None,
        )

    source_label = "+".join(labels)
    out_url = url_blob[2] if url_blob is not None else None

    # Prefer explicit att_id when present (matching sha already enforced).
    if id_att is not None:
        return id_att, source_label, out_url

    # Materialize once from path or url (reuse meta by sha when possible).
    if path_blob is not None:
        att = _materialize_path_blob(path, path_blob, ctx)
        return att, source_label, out_url

    assert url_blob is not None
    att = _materialize_url_blob(url_blob, ctx)
    return att, source_label, out_url


def _materialize_path_blob(
    path: str | None,
    path_blob: tuple[bytes, str, str, str, str],
    ctx: ToolContext,
) -> Attachment:
    """Reuse-by-sha or put path bytes with origin=view."""
    _data, fname, mime, kind, sha = path_blob
    store = MediaStore(ctx.paths)
    existing = store.find_first_by_sha256(sha)
    if existing is not None and existing.kind != "tts_cache":
        return existing
    if not path:
        # Should not happen when path_blob came from path resolve.
        raise _ViewResolveError("missing_source", detail="path blob without path")
    return ingest_sandbox_path(
        path,
        paths=ctx.paths,
        sandbox=ctx.sandbox,
        filename=fname,
        kind=kind,
        origin="view",
        uploader_user_id=_uploader_user_id(ctx),
        mime=mime,
    )


def _materialize_url_blob(
    url_blob: tuple[FetchedBytes, str, str],
    ctx: ToolContext,
) -> Attachment:
    """Reuse-by-sha or put fetched URL bytes with origin=view."""
    fetched, sha, _safe = url_blob
    store = MediaStore(ctx.paths)
    existing = store.find_first_by_sha256(sha)
    if existing is not None and existing.kind != "tts_cache":
        return existing
    mime, kind = sniff_mime_and_kind(
        fetched.data,
        filename=fetched.filename,
        claimed_mime=fetched.claimed_mime,
    )
    try:
        return store.put_bytes(
            fetched.data,
            filename=fetched.filename,
            mime=mime,
            kind=kind,
            origin="view",
            uploader_user_id=_uploader_user_id(ctx),
        )
    except ValueError as exc:
        raise FetchError("url_fetch_failed", detail=str(exc)) from exc
    except OSError as exc:
        raise FetchError(
            "url_fetch_failed",
            detail=f"os_error:{type(exc).__name__}",
        ) from exc


def _fetch_hooks(ctx: ToolContext) -> tuple[Any, Any]:
    """Optional injectable urlopen / getaddrinfo from ctx.extras (hermetic tests)."""
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    urlopen = extras.get("urlopen") or extras.get("media_fetch_urlopen")
    getaddrinfo = extras.get("getaddrinfo") or extras.get("media_fetch_getaddrinfo")
    return (
        urlopen if callable(urlopen) else None,
        getaddrinfo if callable(getaddrinfo) else None,
    )


def _read_sandbox_media_bytes(
    path: str,
    ctx: ToolContext,
) -> tuple[bytes, str, str, str]:
    """Resolve + read sandbox file with size gates; return (data, name, mime, kind)."""
    host_path = resolve_sandbox_file(path, paths=ctx.paths, sandbox=ctx.sandbox)
    fname = (host_path.name or "file").strip() or "file"
    try:
        size = host_path.stat().st_size
    except OSError as exc:
        raise IngestError(f"os_error:{type(exc).__name__}", detail=str(exc)) from exc
    if size > MAX_FILE_BYTES:
        raise IngestError(
            "file_too_large",
            detail=f"{size} bytes exceeds max {MAX_FILE_BYTES}",
        )
    try:
        data = host_path.read_bytes()
    except OSError as exc:
        raise IngestError(f"os_error:{type(exc).__name__}", detail=str(exc)) from exc

    mime, sniffed_kind = sniff_mime_and_kind(data, filename=fname)
    limit = max_bytes_for_kind(sniffed_kind)
    if len(data) > limit:
        raise IngestError(
            "file_too_large",
            detail=f"{len(data)} bytes exceeds {sniffed_kind} max {limit}",
        )
    return data, fname, mime, sniffed_kind


def _get_existing_att(att_id: str, ctx: ToolContext) -> Attachment:
    try:
        aid = validate_att_id(att_id)
    except ValueError as exc:
        raise _ViewResolveError(
            "invalid_att_id", detail=str(exc), att_id=att_id
        ) from exc
    store = MediaStore(ctx.paths)
    att = store.get(aid)
    if att is None:
        raise _ViewResolveError(
            "not_found", detail=f"attachment not found: {aid!r}", att_id=aid
        )
    if att.kind == "tts_cache":
        raise _ViewResolveError(
            "unsupported_kind",
            detail="tts_cache attachments cannot be viewed",
            att_id=aid,
        )
    return att


# ---------------------------------------------------------------------------
# Viewing set / dirty / promote ports
# ---------------------------------------------------------------------------


def _resolve_viewing_entries(ctx: ToolContext) -> dict[str, Any] | None:
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    raw = extras.get("moment_viewing")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    return raw  # type: ignore[return-value]


def _add_to_viewing(
    ctx: ToolContext,
    entries: dict[str, Any],
    att: Attachment,
) -> None:
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    mark = extras.get("mark_viewing")
    if callable(mark):
        mark(
            att.id,
            kind=att.kind,
            mime=att.mime,
            filename=att.filename,
            byte_size=att.byte_size,
        )
        return
    add_viewing(
        entries,
        att.id,
        kind=att.kind,
        mime=att.mime,
        filename=att.filename,
        byte_size=att.byte_size,
    )
    _set_viewing_dirty(ctx)


def _drop_viewing_port(
    ctx: ToolContext,
    entries: dict[str, Any],
    att_id: str,
) -> bool:
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    port = extras.get("drop_viewing")
    if callable(port):
        try:
            return bool(port(att_id))
        except Exception:  # noqa: BLE001
            _LOG.exception("drop_viewing port failed")
            return False
    removed = drop_viewing(entries, att_id)
    if removed:
        _set_viewing_dirty(ctx)
    return removed


def _clear_viewing_port(ctx: ToolContext, entries: dict[str, Any]) -> int:
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    port = extras.get("clear_viewing")
    if callable(port):
        try:
            return int(port())
        except Exception:  # noqa: BLE001
            _LOG.exception("clear_viewing port failed")
            return 0
    n = clear_viewing(entries)
    if n > 0:
        _set_viewing_dirty(ctx)
    return n


def _set_viewing_dirty(ctx: ToolContext) -> None:
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    setter = extras.get("set_viewing_dirty")
    if callable(setter):
        try:
            setter()
        except Exception:  # noqa: BLE001 — never fail tool on dirty stamp
            _LOG.exception("set_viewing_dirty failed")
        return
    # Mutable bag fallback for hermetic tests.
    flag = extras.get("viewing_dirty")
    if isinstance(flag, list):
        if flag:
            flag[0] = True
        else:
            flag.append(True)
    elif isinstance(flag, dict):
        flag["dirty"] = True


def _maybe_promote(
    ctx: ToolContext,
    moment_id: str,
    att: Attachment,
    *,
    note: str | None,
    source_url: str | None = None,
) -> bool:
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    store = extras.get("memory_store")
    if store is None:
        return False
    mem_settings = None
    if ctx.settings is not None:
        mem_settings = getattr(ctx.settings, "memory", None)
    try:
        from elyra.memory.promote import promote_view_observation

        pctx = None
        pctx_fn = extras.get("promote_context_fn")
        if callable(pctx_fn):
            try:
                pctx = pctx_fn()
            except Exception:  # noqa: BLE001
                _LOG.exception("view_media promote_context_fn failed")
                pctx = None
        if pctx is None and extras.get("promote_context") is not None:
            pctx = extras.get("promote_context")
        atom = promote_view_observation(
            store,
            moment_id,
            media_ids=[att.id],
            note=note,
            source_url=source_url,
            settings=mem_settings,
            promote_context=pctx,
        )
        return atom is not None
    except Exception:  # noqa: BLE001
        _LOG.exception("view_media promote failed moment_id=%s", moment_id)
        return False


def _list_payload(entries: dict[str, Any]) -> dict[str, Any]:
    ids = list_viewing_att_ids(entries)
    items = [e.to_dict() for e in list_viewing(entries)]
    return {
        "viewing": ids,
        "viewing_count": len(ids),
        "viewing_items": items,
    }


# ---------------------------------------------------------------------------
# Presentation honesty / soft warnings
# ---------------------------------------------------------------------------


def _presentation_for(kind: str) -> tuple[str, bool, str | None]:
    """Return (presentation, perception, skip_reason).

    Image wire expand is live (PR2). Audio/video Completions parts are live
    when ``ELYRA_AV_EXPAND`` is on (PR4 default). Tool cannot know the live
    provider; reports wire-ready when media+AV env gates allow. Per-item
    duration/size caps still fail-closed on expand with skip notices.
    """
    k = (kind or "").lower()
    if k == "image":
        return "image_url", True, None
    if k in ("audio", "video"):
        if not _media_enabled():
            return "inventory", False, "media_disabled"
        if not _av_expand_env_enabled():
            return "inventory", False, "av_expand_disabled"
        # Match prompt.py wire part type names.
        pres = "input_audio" if k == "audio" else "video_url"
        return pres, True, None
    # file / unknown — inventory only on expand
    return "inventory", False, "unsupported_kind_for_vision"


def _av_expand_env_enabled() -> bool:
    """``ELYRA_AV_EXPAND`` default-on (matches ``prompt._env_flag_enabled``)."""
    raw = os.environ.get("ELYRA_AV_EXPAND")
    if raw is None or raw == "":
        return True
    return raw.strip() not in ("0", "false", "False", "no", "NO")


def _soft_warnings(
    att: Attachment,
    *,
    from_url: bool = False,
    duration_s: float | None = None,
) -> list[str]:
    """Soft large/long-media guidance (never hard-fails the view)."""
    warns: list[str] = []
    kind = (att.kind or "").lower()
    if kind == "video":
        warns.append(_SOFT_VIDEO_WARN)
    elif kind == "audio":
        warns.append(_SOFT_AUDIO_WARN)
    size = int(att.byte_size or 0)
    if size > _SOFT_SIZE_BYTES:
        warns.append(_SOFT_LARGE_WARN)
    elif from_url and kind in ("audio", "video"):
        # Always caution URL AV even under soft size threshold.
        if _SOFT_URL_WARN not in warns:
            warns.append(_SOFT_URL_WARN)
    if duration_s is not None:
        try:
            d = float(duration_s)
        except (TypeError, ValueError):
            d = None
        if d is not None:
            if kind == "video" and d > _SOFT_VIDEO_DURATION_S and _SOFT_VIDEO_WARN not in warns:
                warns.append(_SOFT_VIDEO_WARN)
            if kind == "audio" and d > _SOFT_AUDIO_DURATION_S and _SOFT_AUDIO_WARN not in warns:
                warns.append(_SOFT_AUDIO_WARN)
    # De-dupe preserve order
    out: list[str] = []
    seen: set[str] = set()
    for w in warns:
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def _media_enabled() -> bool:
    raw = os.environ.get("ELYRA_MEDIA")
    if raw is None or raw == "":
        return True
    return raw.strip() not in ("0", "false", "False", "no", "NO")


# ---------------------------------------------------------------------------
# Arg parsing helpers
# ---------------------------------------------------------------------------


def _parse_op(args: dict[str, Any]) -> tuple[str, str | None]:
    if "op" not in args or args.get("op") is None:
        return _DEFAULT_OP, None
    raw = args["op"]
    if not isinstance(raw, str) or not raw.strip():
        return "", "invalid_op"
    op = raw.strip().lower()
    if op not in _OPS:
        return "", "invalid_op"
    return op, None


def _parse_sources(
    args: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (path, att_id, url, error_reason). Empty strings → treated absent."""
    path = _optional_str(args, "path")
    if path is False:
        return None, None, None, "invalid_path"
    att_id = _optional_str(args, "att_id")
    if att_id is False:
        return None, None, None, "invalid_att_id"
    url = _optional_str(args, "url")
    if url is False:
        return None, None, None, "url_invalid"
    return path, att_id, url, None


def _optional_str(args: dict[str, Any], key: str) -> str | None | bool:
    """None if absent/blank; False if wrong type; strip str otherwise."""
    if key not in args or args.get(key) is None:
        return None
    raw = args[key]
    if not isinstance(raw, str):
        return False
    text = raw.strip()
    return text or None


def _optional_note(args: dict[str, Any]) -> str | None:
    raw = args.get("note")
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    return text or None


def _uploader_user_id(ctx: ToolContext) -> str:
    if ctx.user_id is not None and str(ctx.user_id).strip():
        return str(ctx.user_id).strip()
    return "operator"


def _ok(payload: dict[str, Any]) -> ToolResult:
    body = dict(payload)
    body.setdefault("ok", True)
    return ToolResult(ok=True, payload=body)


def _err(reason: str, **extra: Any) -> ToolResult:
    payload: dict[str, Any] = {"ok": False, "reason": reason, **extra}
    # Drop None values for cleaner tool JSON.
    payload = {k: v for k, v in payload.items() if v is not None}
    return ToolResult(ok=False, payload=payload, error_reason=reason)


__all__ = ["view_media"]
