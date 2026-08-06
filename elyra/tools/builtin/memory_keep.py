"""Builtin directed-keep update tool — thin over TraversalRegistry (KD-K1/K7).

Scope: memory_keep_update host entry. Mutates sticky tray + thin snap via
  ``TraversalRegistry.update_keep``; no active walk required.
Fail closed when directed keep is disabled or traversal registry missing.
Out of scope: skill/prompt wording (C5), graph pin UI, soft-recall.
"""

from __future__ import annotations

import logging
from typing import Any

from elyra.memory.config import is_directed_keep_enabled
from elyra.memory.traverse import ERROR_INVALID_ARGS, ERROR_KEEP_DISABLED
from elyra.tools.types import ToolContext, ToolResult

_LOG = logging.getLogger(__name__)

ERROR_KEEP_UNAVAILABLE = "keep_unavailable"
ERROR_INVALID_ARGS_TOOL = ERROR_INVALID_ARGS  # alias for callers/tests

_HINT_DISABLED = (
    "Directed keep is disabled. Set memory.directed_keep_enabled=true "
    "(or memory.directed_traversal_enabled=true — keep follows OQ-A1)."
)
_HINT_UNAVAILABLE = (
    "Keep tray registry missing. Host must inject ctx.extras['traversal'] "
    "(TraversalRegistry) for sticky directed-keep updates."
)


def _err(reason: str, *, hint: str | None = None, **extra: Any) -> ToolResult:
    payload: dict[str, Any] = {"ok": False, "error_reason": reason, **extra}
    if hint is not None:
        payload["hint"] = hint
    return ToolResult(ok=False, payload=payload, error_reason=reason)


def _memory_settings(ctx: ToolContext) -> Any | None:
    settings = ctx.settings
    if settings is None:
        return None
    return getattr(settings, "memory", None)


def _resolve_traversal(ctx: ToolContext) -> tuple[Any | None, ToolResult | None]:
    """Return TraversalRegistry from extras, or an error ToolResult."""
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    reg = extras.get("traversal")
    if reg is None:
        return None, _err(ERROR_KEEP_UNAVAILABLE, hint=_HINT_UNAVAILABLE)
    mem = _memory_settings(ctx)
    bind = getattr(reg, "bind_settings", None)
    if callable(bind) and mem is not None:
        try:
            bind(mem)
        except Exception:  # noqa: BLE001 — soft; registry keeps prior
            _LOG.debug("traversal.bind_settings failed", exc_info=True)
    # Always rebind paths from tool context for next tray save/load.
    bind_paths = getattr(reg, "bind_paths", None)
    if callable(bind_paths) and ctx.paths is not None:
        try:
            bind_paths(ctx.paths)
        except Exception:  # noqa: BLE001
            _LOG.debug("traversal.bind_paths failed", exc_info=True)
    return reg, None


def _check_keep_enabled(ctx: ToolContext, reg: Any) -> ToolResult | None:
    """Fail closed when directed keep is disabled (no mutate)."""
    mem = _memory_settings(ctx)
    # Prefer registry settings after bind; fall back to ctx.
    settings = getattr(reg, "settings", None) or mem
    if not is_directed_keep_enabled(settings):
        return _err(
            ERROR_KEEP_DISABLED,
            hint=_HINT_DISABLED,
            status="disabled",
        )
    return None


def _str_list(raw: Any, *, name: str) -> tuple[list[str] | None, ToolResult | None]:
    if raw is None:
        return [], None
    if isinstance(raw, str):
        s = raw.strip()
        return ([s] if s else []), None
    if not isinstance(raw, (list, tuple)):
        return None, _err(
            ERROR_INVALID_ARGS, detail=f"{name} must be a list of strings"
        )
    out: list[str] = []
    for item in raw:
        if item is None:
            continue
        if not isinstance(item, str):
            return None, _err(
                ERROR_INVALID_ARGS, detail=f"{name} items must be strings"
            )
        s = item.strip()
        if s:
            out.append(s)
    return out, None


def _optional_str(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return None
    return raw  # preserve empty string so registry can null summary


def _from_registry_dict(out: dict[str, Any]) -> ToolResult:
    if not isinstance(out, dict):
        return _err(ERROR_KEEP_UNAVAILABLE, detail="registry returned non-dict")
    ok = bool(out.get("ok", False))
    reason = out.get("error_reason")
    payload = dict(out)
    payload["ok"] = ok
    if ok:
        return ToolResult(ok=True, payload=payload)
    return ToolResult(
        ok=False,
        payload=payload,
        error_reason=str(reason) if reason else "keep_update_failed",
    )


def memory_keep_update(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Merge, replace, remove, or clear sticky directed-keep tray pins."""
    reg, err = _resolve_traversal(ctx)
    if err is not None:
        return err
    disabled = _check_keep_enabled(ctx, reg)
    if disabled is not None:
        return disabled

    mode_raw = args.get("mode", "merge")
    if mode_raw is None:
        mode = "merge"
    elif not isinstance(mode_raw, str):
        return _err(ERROR_INVALID_ARGS, detail="mode must be a string (merge|replace)")
    else:
        mode = mode_raw.strip().lower() or "merge"
    if mode not in ("merge", "replace"):
        return _err(ERROR_INVALID_ARGS, detail="mode must be merge|replace")

    atom_ids, aerr = _str_list(args.get("atom_ids"), name="atom_ids")
    if aerr is not None:
        return aerr
    remove_ids, rerr = _str_list(args.get("remove_ids"), name="remove_ids")
    if rerr is not None:
        return rerr

    note: str | None
    if "note" not in args:
        note = None
    else:
        raw_note = args.get("note")
        if raw_note is not None and not isinstance(raw_note, str):
            return _err(ERROR_INVALID_ARGS, detail="note must be a string")
        note = _optional_str(raw_note)

    # Pre-validate no-op merge so fail path never touches durable state.
    if mode == "merge" and not atom_ids and not remove_ids:
        return _err(
            ERROR_INVALID_ARGS,
            detail="merge requires atom_ids and/or remove_ids",
        )

    moment_id = (ctx.moment_id or "").strip() or None
    out = reg.update_keep(
        mode=mode,
        atom_ids=atom_ids or None,
        remove_ids=remove_ids or None,
        note=note,
        moment_id=moment_id,
    )
    return _from_registry_dict(out)


__all__ = [
    "ERROR_INVALID_ARGS",
    "ERROR_KEEP_DISABLED",
    "ERROR_KEEP_UNAVAILABLE",
    "memory_keep_update",
]
