"""Host ``view_media`` tool — mid-moment look at path / att_id (PR3).

Scope: resolve path|att_id into MediaStore, add to moment viewing set, first-wins
promote with media_ids (no wake_message_id), list/drop/clear ops.

Out of scope: URL body fetch (PR5 → ``url_not_yet_wired``), AV Completions
parts (PR4 — perception honesty stays fail-closed for audio/video).

KD-V1, V11, V13–V16. Tool results stay text-only JSON (KD-V9).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from elyra.media.ingest import IngestError, ingest_sandbox_path
from elyra.media.store import MediaStore, validate_att_id
from elyra.media.types import Attachment
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

# Image Completions expand is live (PR2). AV wire parts land in PR4.
_AV_EXPAND_WIRED = False


def view_media(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Resolve media into the moment viewing set (path and/or att_id).

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
        n = clear_viewing(entries)
        _set_viewing_dirty(ctx)
        return _ok(
            {
                "op": "clear",
                "cleared": n,
                **_list_payload(entries),
                "viewing_dirty": True,
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
        removed = drop_viewing(entries, aid)
        if removed:
            _set_viewing_dirty(ctx)
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

    # URL body fetch is PR5; schema keeps url for forward-compat.
    if url is not None:
        return _err(
            "url_not_yet_wired",
            detail=(
                "URL fetch+view lands in a follow-on PR; use path or att_id for now."
            ),
            url=url,
        )

    if path is None and att_id is None:
        return _err("missing_source")

    note = _optional_note(args)
    try:
        att, source_label = _resolve_view_attachment(
            path=path,
            att_id=att_id,
            ctx=ctx,
        )
    except IngestError as exc:
        return _err(exc.reason, detail=exc.detail)
    except _ViewResolveError as exc:
        return _err(exc.reason, detail=exc.detail, **exc.extra)

    # Membership + dirty (always on successful view, including re-view).
    try:
        _add_to_viewing(ctx, entries, att)
    except ValueError as exc:
        return _err("invalid_att_id", detail=str(exc))

    # First-wins breadcrumb (no wake_message_id). Soft-fail if store missing.
    promoted = _maybe_promote(ctx, moment_id, att, note=note)

    presentation, perception, skip_reason = _presentation_for(att.kind)
    soft_warnings = _soft_warnings(att)
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
            f"for this item ({skip_reason}). Image wire is live; audio/video wire "
            f"ships in a follow-on PR."
        )
    if soft_warnings:
        payload["soft_warnings"] = soft_warnings
    if note:
        payload["view_note"] = note
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
    ctx: ToolContext,
) -> tuple[Attachment, str]:
    """Resolve path and/or att_id to one Attachment; raise on conflict/miss."""
    path_att: Attachment | None = None
    id_att: Attachment | None = None
    sources_used: list[str] = []

    if att_id is not None:
        id_att = _get_existing_att(att_id, ctx)
        sources_used.append("att_id")

    if path is not None:
        user_id = _uploader_user_id(ctx)
        path_att = ingest_sandbox_path(
            path,
            paths=ctx.paths,
            sandbox=ctx.sandbox,
            origin="view",
            uploader_user_id=user_id,
        )
        sources_used.append("path")

    if path_att is not None and id_att is not None:
        if path_att.id == id_att.id or path_att.sha256 == id_att.sha256:
            # Same durable media — prefer the explicit att_id (stable id).
            return id_att, "path+att_id"
        raise _ViewResolveError(
            "ambiguous_source",
            detail=(
                f"path and att_id resolve to different media "
                f"(sha {path_att.sha256[:12]}… vs {id_att.sha256[:12]}…)"
            ),
            path_att_id=path_att.id,
            att_id=id_att.id,
        )

    if id_att is not None:
        return id_att, "att_id"
    if path_att is not None:
        return path_att, "path"
    raise _ViewResolveError("missing_source")


def _get_existing_att(att_id: str, ctx: ToolContext) -> Attachment:
    try:
        aid = validate_att_id(att_id)
    except ValueError as exc:
        raise _ViewResolveError("invalid_att_id", detail=str(exc), att_id=att_id) from exc
    store = MediaStore(ctx.paths)
    att = store.get(aid)
    if att is None:
        raise _ViewResolveError("not_found", detail=f"attachment not found: {aid!r}", att_id=aid)
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

        atom = promote_view_observation(
            store,
            moment_id,
            media_ids=[att.id],
            note=note,
            settings=mem_settings,
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

    Image wire expand is live. Audio/video Completions parts are PR4 — do not
    claim perception:true until those builders ship.
    """
    k = (kind or "").lower()
    if k == "image":
        return "image_url", True, None
    if k in ("audio", "video"):
        if _AV_EXPAND_WIRED:
            # Reserved for PR4 flip; keep branch for honesty continuity.
            pres = "input_audio" if k == "audio" else "input_video"
            return pres, True, None
        return "inventory", False, "av_expand_not_yet_wired"
    # file / unknown — inventory only on expand
    return "inventory", False, "unsupported_kind_for_vision"


def _soft_warnings(att: Attachment) -> list[str]:
    warns: list[str] = []
    kind = (att.kind or "").lower()
    if kind == "video":
        warns.append(_SOFT_VIDEO_WARN)
    elif kind == "audio":
        warns.append(_SOFT_AUDIO_WARN)
    if int(att.byte_size or 0) > _SOFT_SIZE_BYTES:
        warns.append(_SOFT_LARGE_WARN)
    # duration_s not on Attachment yet — ViewingEntry may carry it later.
    return warns


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
