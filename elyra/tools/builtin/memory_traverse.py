"""Builtin directed-traversal tools — thin over TraversalRegistry (Phase 2a PR-A4).

Scope: memory_traverse_start/step/inspect/finish/abandon host entries.
In scope: resolve graph_view + traversal from ToolContext.extras; fail closed
  when directed_traversal_enabled is false or ports missing; inspect caps.
Out of scope: session algorithm (traverse.py), meal directed_keep (PR-A3),
  glass Graph tab (PR-A5), automatic do-loop hop wiring.
"""

from __future__ import annotations

import logging
from typing import Any

from elyra.memory.config import is_directed_traversal_enabled
from elyra.memory.traverse import (
    ERROR_TRAVERSE_DISABLED,
    inspect_atoms,
)
from elyra.tools.types import ToolContext, ToolResult

_LOG = logging.getLogger(__name__)

ERROR_TRAVERSE_UNAVAILABLE = "traverse_unavailable"
ERROR_INVALID_ARGS = "invalid_args"
ERROR_ATOM_NOT_FOUND = "atom_not_found"

_HINT_DISABLED = (
    "Directed traversal is disabled. Set memory.directed_traversal_enabled=true "
    "to enable multi-hop walk tools."
)
_HINT_UNAVAILABLE = (
    "Traversal ports missing (graph_view / traversal). Host must inject "
    "ctx.extras['graph_view'] and ctx.extras['traversal'] (presence worker)."
)


def _err(reason: str, *, hint: str | None = None, **extra: Any) -> ToolResult:
    payload: dict[str, Any] = {"ok": False, "error_reason": reason, **extra}
    if hint is not None:
        payload["hint"] = hint
    return ToolResult(ok=False, payload=payload, error_reason=reason)


def _ok(payload: dict[str, Any]) -> ToolResult:
    body = dict(payload)
    body.setdefault("ok", True)
    return ToolResult(ok=True, payload=body)


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
        return None, _err(ERROR_TRAVERSE_UNAVAILABLE, hint=_HINT_UNAVAILABLE)
    # Bind latest memory settings when available (flag / budget changes).
    mem = _memory_settings(ctx)
    bind = getattr(reg, "bind_settings", None)
    if callable(bind) and mem is not None:
        try:
            bind(mem)
        except Exception:  # noqa: BLE001 — soft; registry keeps prior
            _LOG.debug("traversal.bind_settings failed", exc_info=True)
    return reg, None


def _resolve_graph(ctx: ToolContext) -> tuple[Any | None, ToolResult | None]:
    """Return GraphView instance (or call factory), or error ToolResult."""
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    raw = extras.get("graph_view")
    if raw is None:
        return None, _err(ERROR_TRAVERSE_UNAVAILABLE, hint=_HINT_UNAVAILABLE)
    if callable(raw) and not hasattr(raw, "neighbors"):
        try:
            graph = raw()
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("graph_view factory failed")
            return None, _err(
                ERROR_TRAVERSE_UNAVAILABLE,
                hint=_HINT_UNAVAILABLE,
                detail=f"{type(exc).__name__}: {exc}",
            )
    else:
        graph = raw
    if graph is None:
        return None, _err(
            ERROR_TRAVERSE_UNAVAILABLE,
            hint="Memory store unavailable; cannot build GraphView.",
        )
    return graph, None


def _check_enabled(ctx: ToolContext, reg: Any) -> ToolResult | None:
    """Fail closed when directed_traversal_enabled is false."""
    mem = _memory_settings(ctx)
    enabled_fn = getattr(reg, "enabled", None)
    if callable(enabled_fn):
        on = bool(enabled_fn())
    else:
        on = is_directed_traversal_enabled(mem)
    if not on:
        return _err(ERROR_TRAVERSE_DISABLED, hint=_HINT_DISABLED, status="disabled")
    return None


def _str_list(raw: Any, *, name: str) -> tuple[list[str] | None, ToolResult | None]:
    if raw is None:
        return [], None
    if isinstance(raw, str):
        s = raw.strip()
        return ([s] if s else []), None
    if not isinstance(raw, (list, tuple)):
        return None, _err(ERROR_INVALID_ARGS, detail=f"{name} must be a list of strings")
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
    s = raw.strip()
    return s or None


def _from_registry_dict(out: dict[str, Any]) -> ToolResult:
    """Map TraversalRegistry dict result → ToolResult."""
    if not isinstance(out, dict):
        return _err(ERROR_TRAVERSE_UNAVAILABLE, detail="registry returned non-dict")
    ok = bool(out.get("ok", False))
    reason = out.get("error_reason")
    payload = dict(out)
    payload["ok"] = ok
    if reason and "error_reason" not in payload:
        payload["error_reason"] = reason
    if ok:
        return ToolResult(ok=True, payload=payload)
    return ToolResult(
        ok=False,
        payload=payload,
        error_reason=str(reason) if reason else "traverse_failed",
    )


# ── Tools ───────────────────────────────────────────────────────────────────


def memory_traverse_start(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Start a temporary multi-hop memory walk session."""
    reg, err = _resolve_traversal(ctx)
    if err is not None:
        return err
    disabled = _check_enabled(ctx, reg)
    if disabled is not None:
        return disabled
    graph, gerr = _resolve_graph(ctx)
    if gerr is not None:
        return gerr

    goal = args.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        return _err(ERROR_INVALID_ARGS, detail="goal is required (non-empty string)")
    goal = goal.strip()

    seed_query = _optional_str(args.get("seed_query"))
    seed_atom_ids, serr = _str_list(args.get("seed_atom_ids"), name="seed_atom_ids")
    if serr is not None:
        return serr

    # Optional budget overrides (clamped by registry to settings max).
    budget_overrides: dict[str, int] | None = None
    raw_budgets = args.get("budgets")
    if raw_budgets is not None:
        if not isinstance(raw_budgets, dict):
            return _err(ERROR_INVALID_ARGS, detail="budgets must be an object")
        budget_overrides = {}
        for key in ("max_steps", "max_nodes", "max_depth", "max_keep"):
            if key in raw_budgets and raw_budgets[key] is not None:
                try:
                    budget_overrides[key] = int(raw_budgets[key])
                except (TypeError, ValueError):
                    return _err(
                        ERROR_INVALID_ARGS, detail=f"budgets.{key} must be an integer"
                    )

    moment_id = (ctx.moment_id or "").strip() or None
    out = reg.start(
        graph,
        goal=goal,
        seed_query=seed_query,
        seed_atom_ids=seed_atom_ids or None,
        moment_id=moment_id,
        budget_overrides=budget_overrides,
    )
    return _from_registry_dict(out)


def memory_traverse_step(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Expand selected frontier nodes and/or mark provisional keeps."""
    reg, err = _resolve_traversal(ctx)
    if err is not None:
        return err
    disabled = _check_enabled(ctx, reg)
    if disabled is not None:
        return disabled
    graph, gerr = _resolve_graph(ctx)
    if gerr is not None:
        return gerr

    session_id = _optional_str(args.get("session_id"))
    expand_ids, eerr = _str_list(args.get("expand_ids"), name="expand_ids")
    if eerr is not None:
        return eerr
    keep_ids, kerr = _str_list(args.get("keep_ids"), name="keep_ids")
    if kerr is not None:
        return kerr
    scratchpad = args.get("scratchpad")
    if scratchpad is not None and not isinstance(scratchpad, str):
        return _err(ERROR_INVALID_ARGS, detail="scratchpad must be a string")

    out = reg.step(
        graph,
        session_id=session_id,
        expand_ids=expand_ids or None,
        keep_ids=keep_ids or None,
        scratchpad=scratchpad,
    )
    return _from_registry_dict(out)


def memory_traverse_inspect(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Return capped body slices for mid-walk keep decisions (KD-A17)."""
    reg, err = _resolve_traversal(ctx)
    if err is not None:
        return err
    disabled = _check_enabled(ctx, reg)
    if disabled is not None:
        return disabled
    graph, gerr = _resolve_graph(ctx)
    if gerr is not None:
        return gerr

    atom_ids, aerr = _str_list(args.get("atom_ids"), name="atom_ids")
    if aerr is not None:
        return aerr
    if not atom_ids:
        return _err(ERROR_INVALID_ARGS, detail="atom_ids is required (non-empty list)")

    store = getattr(graph, "_store", None)
    if store is None:
        return _err(
            ERROR_TRAVERSE_UNAVAILABLE,
            hint="GraphView has no store; cannot inspect atoms.",
        )

    mem = _memory_settings(ctx)
    previews = inspect_atoms(store, atom_ids, settings=mem)
    items = [p.to_dict() for p in previews]
    missing = [p.atom_id for p in previews if p.error == "atom_not_found"]
    # Fail closed: any unknown id → ok false (no invented bodies).
    if missing:
        return ToolResult(
            ok=False,
            payload={
                "ok": False,
                "error_reason": ERROR_ATOM_NOT_FOUND,
                "atoms": items,
                "missing_ids": missing,
            },
            error_reason=ERROR_ATOM_NOT_FOUND,
        )
    return _ok({"atoms": items})


def memory_traverse_finish(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Confirm keep-set; promote last_session + last_confirmed_keep snapshots."""
    reg, err = _resolve_traversal(ctx)
    if err is not None:
        return err
    disabled = _check_enabled(ctx, reg)
    if disabled is not None:
        return disabled
    graph, gerr = _resolve_graph(ctx)
    if gerr is not None:
        return gerr

    session_id = _optional_str(args.get("session_id"))
    # Absent keep_ids → leave provisional set; present (even []) → replace.
    keep_ids: list[str] | None
    if "keep_ids" in args:
        parsed, kerr = _str_list(args.get("keep_ids"), name="keep_ids")
        if kerr is not None:
            return kerr
        keep_ids = parsed
    else:
        keep_ids = None
    summary_hint = _optional_str(args.get("summary_hint"))

    out = reg.finish(
        graph,
        session_id=session_id,
        keep_ids=keep_ids,
        summary_hint=summary_hint,
    )
    return _from_registry_dict(out)


def memory_traverse_abandon(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Abandon the active walk; sticky last_session / last_confirmed retained."""
    reg, err = _resolve_traversal(ctx)
    if err is not None:
        return err
    disabled = _check_enabled(ctx, reg)
    if disabled is not None:
        return disabled

    session_id = _optional_str(args.get("session_id"))
    reason = _optional_str(args.get("reason")) or "abandoned"
    out = reg.abandon(session_id=session_id, reason=reason)
    return _from_registry_dict(out)


__all__ = [
    "ERROR_ATOM_NOT_FOUND",
    "ERROR_INVALID_ARGS",
    "ERROR_TRAVERSE_UNAVAILABLE",
    "memory_traverse_abandon",
    "memory_traverse_finish",
    "memory_traverse_inspect",
    "memory_traverse_start",
    "memory_traverse_step",
]
