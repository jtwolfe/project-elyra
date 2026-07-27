"""Builtin ledger tools: create/list/get/update for goals and tasks.

Scope: host entries for tools/bundled ledger packages.
In scope: GoalsStore via ToolContext.goals; soft-close warning in payload;
  task→ready notifies enqueue_wake (when set); mark_task_changed on all
  mutating tools (create_goal, create_task, update_goal, update_task).
  Read tools (list_goals, get_goal, get_task) do not call mark_task_changed.
Out of scope: wake-queue dedupe implementation, continuous policy, orient
  wiring, presence worker wiring (host injects ports).

Dual task_ready notify paths
----------------------------
Two emission surfaces exist; the composition root must not wire **both** to a
raw wake enqueue without replace/dedupe:

1. **GoalsStore(on_task_ready=…)** — fires on every store transition to ready
   (including create-as-ready and direct API updates). Preferred sole emission
   site when the host owns the store lifecycle.
2. **ToolContext.enqueue_wake** — tool layer only, after a successful
   ``update_task`` / ``create_task`` when the result is (or became) ready.
   Use when the store hook is unset (tests / thin wiring).

Recommended S1 wiring: pick **one** path for durable wake enqueue. If both
fire, presence must dedupe (replace pending task_ready for the same task_id).
Enqueue after commit is best-effort at the tool layer: a failed port does not
roll back the ledger; the result stays ok with a warning so hosts can recover
without a stranded already-ready short-circuit.
"""

from __future__ import annotations

import logging
from typing import Any

from elyra.tools.types import ToolContext, ToolResult

_LOG = logging.getLogger(__name__)

# Soft recovery guidance on missing ledger ids (no HOST inject, no auto-create).
_TASK_NOT_FOUND_HINT = (
    "No task with that id. Call list_goals (or get_goal) to refresh "
    "ids, then get_task / update_task with an exact ledger id. "
    "Do not invent task ids."
)
_GOAL_NOT_FOUND_HINT = (
    "No goal with that id. Call list_goals to refresh ids, then "
    "get_goal / update_goal with an exact ledger id. "
    "Do not invent goal ids."
)


def _task_not_found(task_id: str) -> ToolResult:
    """Canonical soft payload for missing task (get_task / update_task)."""
    return ToolResult(
        ok=False,
        payload={
            "ok": False,
            "task_id": task_id,
            "error_reason": "task_not_found",
            "hint": _TASK_NOT_FOUND_HINT,
        },
        error_reason="task_not_found",
    )


def _goal_not_found(goal_id: str) -> ToolResult:
    """Canonical soft payload for missing goal (get_goal / update_goal)."""
    return ToolResult(
        ok=False,
        payload={
            "ok": False,
            "goal_id": goal_id,
            "error_reason": "goal_not_found",
            "hint": _GOAL_NOT_FOUND_HINT,
        },
        error_reason="goal_not_found",
    )


def _goals(ctx: ToolContext):
    """Return GoalsStore or a failed ToolResult when missing."""
    if ctx.goals is None:
        return None, ToolResult(
            ok=False,
            payload={},
            error_reason="goals_not_configured",
        )
    return ctx.goals, None


def _require_str_id(args: dict[str, Any], key: str) -> tuple[str | None, ToolResult | None]:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        return None, ToolResult(
            ok=False,
            payload={},
            error_reason=f"missing_{key}",
        )
    return value.strip(), None


def _require_title(args: dict[str, Any]) -> tuple[str | None, ToolResult | None]:
    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        return None, ToolResult(
            ok=False,
            payload={},
            error_reason="missing_title",
        )
    return title.strip(), None


def _mark_task_changed(ctx: ToolContext, *, what: str, id_hint: str) -> None:
    """Best-effort continue-policy / ledger_mutated activity hook."""
    if ctx.mark_task_changed is None:
        return
    try:
        ctx.mark_task_changed()
    except Exception:
        _LOG.exception(
            "mark_task_changed failed after %s id=%s",
            what,
            id_hint,
        )


def _enqueue_task_ready(
    ctx: ToolContext,
    payload: dict[str, Any],
    *,
    task_id: str,
    goal_id: str | None,
) -> None:
    """Best-effort tool-layer task_ready wake; never fails the tool after commit."""
    if ctx.enqueue_wake is None:
        return
    try:
        ctx.enqueue_wake(
            kind="task_ready",
            task_id=task_id,
            goal_id=goal_id,
        )
    except Exception as exc:
        _LOG.exception(
            "task_ready enqueue_wake failed after ready commit task_id=%s",
            task_id,
        )
        payload["warning"] = f"task_ready_enqueue_failed:{type(exc).__name__}"


def _compact_goal(goal: dict[str, Any]) -> dict[str, Any]:
    """Compact list_goals summary: id/title/status + short task list."""
    tasks_raw = goal.get("tasks") or []
    tasks: list[dict[str, Any]] = []
    if isinstance(tasks_raw, list):
        for t in tasks_raw:
            if not isinstance(t, dict):
                continue
            tasks.append(
                {
                    "id": t.get("id"),
                    "title": t.get("title"),
                    "status": t.get("status"),
                }
            )
    return {
        "id": goal.get("id"),
        "title": goal.get("title"),
        "status": goal.get("status"),
        "task_count": len(tasks),
        "tasks": tasks,
    }


def _context_from_tool_ctx(ctx: ToolContext) -> dict[str, Any] | None:
    """Snapshot social provenance for create_goal/create_task (K6).

    Returns None when ``ctx.user_id`` is null/blank (continuous / pure work) —
    expected; do not invent operator. Snapshots goes_by via UsersStore
    display_label when available.
    """
    uid = ctx.user_id
    if not isinstance(uid, str) or not uid.strip():
        return None
    clean = uid.strip()
    users = ctx.extras.get("users") if isinstance(ctx.extras, dict) else None
    if users is not None and hasattr(users, "display_label"):
        try:
            goes_by = users.display_label(clean)
        except Exception:  # noqa: BLE001 — fail soft to user_id
            goes_by = clean
    else:
        goes_by = clean
    if not isinstance(goes_by, str) or not goes_by.strip():
        goes_by = clean
    out: dict[str, Any] = {
        "user_id": clean,
        "goes_by": goes_by.strip(),
        "source": "tool",
    }
    mid = ctx.moment_id
    if isinstance(mid, str) and mid.strip():
        out["moment_id"] = mid.strip()
    return out


def _resolve_created_in_context(
    args: dict[str, Any], ctx: ToolContext
) -> dict[str, Any] | None:
    """Prefer explicit args.created_in_context; else tool-ctx snapshot."""
    if "created_in_context" in args:
        raw = args.get("created_in_context")
        if raw is None:
            return None
        if not isinstance(raw, dict):
            return None
        uid = raw.get("user_id")
        if not isinstance(uid, str) or not uid.strip():
            return None
        clean = uid.strip()
        out: dict[str, Any] = {"user_id": clean}
        goes_by = raw.get("goes_by")
        if isinstance(goes_by, str) and goes_by.strip():
            out["goes_by"] = goes_by.strip()
        else:
            # Fill goes_by from users when model omitted it.
            users = ctx.extras.get("users") if isinstance(ctx.extras, dict) else None
            if users is not None and hasattr(users, "display_label"):
                try:
                    label = users.display_label(clean)
                except Exception:  # noqa: BLE001
                    label = clean
                if isinstance(label, str) and label.strip():
                    out["goes_by"] = label.strip()
                else:
                    out["goes_by"] = clean
            else:
                out["goes_by"] = clean
        for key in ("moment_id", "source"):
            val = raw.get(key)
            if isinstance(val, str) and val.strip():
                out[key] = val.strip()
        if "source" not in out:
            out["source"] = "tool"
        if "moment_id" not in out:
            mid = ctx.moment_id
            if isinstance(mid, str) and mid.strip():
                out["moment_id"] = mid.strip()
        return out
    return _context_from_tool_ctx(ctx)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def create_goal(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Create a goal; call mark_task_changed on success.

    Args: title (required), acceptance?, status? (default open; not closed),
    created_in_context? (else snapshotted from ctx.user_id when non-null).
    """
    store, err = _goals(ctx)
    if err is not None:
        return err

    title, title_err = _require_title(args)
    if title_err is not None:
        return title_err

    kwargs: dict[str, Any] = {}
    if "acceptance" in args:
        kwargs["acceptance"] = args["acceptance"]
    if "status" in args and args["status"] is not None:
        kwargs["status"] = args["status"]
    cic = _resolve_created_in_context(args, ctx)
    if cic is not None:
        kwargs["created_in_context"] = cic

    try:
        goal = store.create_goal(title, **kwargs)
    except ValueError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"invalid_args:{exc}",
        )

    _mark_task_changed(ctx, what="create_goal", id_hint=str(goal.get("id", "")))
    return ToolResult(ok=True, payload={"ok": True, "goal": goal})


def create_task(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Create a task under a goal; mark_task_changed; ready → enqueue path.

    Args: goal_id, title (required); status? (default pending); notes?;
    created_in_context? (else snapshotted from ctx.user_id when non-null).
    Create-as-ready fires store on_task_ready and tool-layer enqueue_wake.
    Does not inherit parent goal context automatically.
    """
    store, err = _goals(ctx)
    if err is not None:
        return err

    goal_id, id_err = _require_str_id(args, "goal_id")
    if id_err is not None:
        return id_err

    title, title_err = _require_title(args)
    if title_err is not None:
        return title_err

    kwargs: dict[str, Any] = {}
    if "status" in args and args["status"] is not None:
        kwargs["status"] = args["status"]
    if "notes" in args:
        kwargs["notes"] = args["notes"]
    cic = _resolve_created_in_context(args, ctx)
    if cic is not None:
        kwargs["created_in_context"] = cic

    try:
        task = store.create_task(goal_id, title, **kwargs)
    except KeyError:
        return ToolResult(ok=False, payload={}, error_reason="goal_not_found")
    except ValueError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"invalid_args:{exc}",
        )

    payload: dict[str, Any] = {
        "ok": True,
        "task": task,
        # Create-as-ready is a transition into ready (store fires hook).
        "became_ready": task.get("status") == "ready",
    }

    _mark_task_changed(
        ctx, what="create_task", id_hint=str(task.get("id", ""))
    )

    if payload["became_ready"]:
        _enqueue_task_ready(
            ctx,
            payload,
            task_id=str(task.get("id", "")),
            goal_id=task.get("goal_id", goal_id),
        )

    return ToolResult(ok=True, payload=payload)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def list_goals(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """List goals compactly; optional status filter. Read-only — no mark."""
    store, err = _goals(ctx)
    if err is not None:
        return err

    status = args.get("status")
    filter_status: str | None = None
    if status is not None and status != "":
        if not isinstance(status, str):
            return ToolResult(
                ok=False,
                payload={},
                error_reason="invalid_args:status must be a string",
            )
        filter_status = status

    try:
        goals = store.list_goals(status=filter_status)
    except ValueError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"invalid_args:{exc}",
        )

    compact = [_compact_goal(g) for g in goals]
    return ToolResult(ok=True, payload={"ok": True, "goals": compact})


def get_goal(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Return full goal + nested tasks. Read-only — no mark_task_changed."""
    store, err = _goals(ctx)
    if err is not None:
        return err

    goal_id, id_err = _require_str_id(args, "goal_id")
    if id_err is not None:
        return id_err

    goal = store.get_goal(goal_id)
    if goal is None:
        return _goal_not_found(goal_id)
    return ToolResult(ok=True, payload={"ok": True, "goal": goal})


def get_task(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Return task dict (includes goal_id). Read-only — no mark_task_changed."""
    store, err = _goals(ctx)
    if err is not None:
        return err

    task_id, id_err = _require_str_id(args, "task_id")
    if id_err is not None:
        return id_err

    task = store.get_task(task_id)
    if task is None:
        return _task_not_found(task_id)
    return ToolResult(ok=True, payload={"ok": True, "task": task})


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------


def update_task(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Patch a task; on transition to ready, notify wake + continue ports.

    Uses store-returned ``became_ready`` (in-lock transition flag), not a
    separate pre-read of status.

    Side effects after a durable commit are best-effort:
    - ``mark_task_changed`` exceptions are logged and swallowed.
    - ``enqueue_wake`` exceptions are logged; the tool still returns
      ``ok=True`` with the task payload and a ``warning`` so the model/host
      sees success and can recover. A raising port must not strand work by
      reporting handler failure while the task is already ``ready`` (retry
      with status=ready would not re-fire became_ready).
    """
    store, err = _goals(ctx)
    if err is not None:
        return err

    task_id, id_err = _require_str_id(args, "task_id")
    if id_err is not None:
        return id_err

    kwargs: dict[str, Any] = {}
    if "title" in args and args["title"] is not None:
        kwargs["title"] = args["title"]
    if "status" in args and args["status"] is not None:
        kwargs["status"] = args["status"]
    if "notes" in args:
        kwargs["notes"] = args["notes"]

    if not kwargs:
        return ToolResult(
            ok=False,
            payload={},
            error_reason="no_fields_to_update",
        )

    try:
        result = store.update_task(task_id, **kwargs)
    except KeyError:
        return _task_not_found(task_id)
    except ValueError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"invalid_args:{exc}",
        )

    payload: dict[str, Any] = dict(result)
    task = payload.get("task") or {}
    # Authoritative in-lock flag from GoalsStore (not a tool-layer TOCTOU read).
    became_ready = bool(payload.get("became_ready"))

    # Continue-policy: any successful task mutation counts as activity.
    _mark_task_changed(
        ctx, what="update_task", id_hint=str(task.get("id", task_id))
    )

    # Transition to ready → task_ready wake (tool-layer port). Best-effort:
    # never fail the tool after a durable ready commit.
    if became_ready:
        _enqueue_task_ready(
            ctx,
            payload,
            task_id=str(task.get("id", task_id)),
            goal_id=task.get("goal_id"),
        )

    return ToolResult(ok=True, payload=payload)


def update_goal(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Patch a goal; soft close (open→closed without force) includes warning.

    Payload mirrors GoalsStore.update_goal: ``ok``, ``goal``, optional
    ``warning`` when closing from open without ``force=true``.

    ``force`` is only meaningful with a status (or other field) change; force
    alone is rejected as ``no_fields_to_update``.

    On success, calls ``mark_task_changed`` (normative for ledger_mutated /
    continue-idle activity tracking).
    """
    store, err = _goals(ctx)
    if err is not None:
        return err

    goal_id, id_err = _require_str_id(args, "goal_id")
    if id_err is not None:
        return id_err

    kwargs: dict[str, Any] = {}
    if "title" in args and args["title"] is not None:
        kwargs["title"] = args["title"]
    if "status" in args and args["status"] is not None:
        kwargs["status"] = args["status"]
    if "acceptance" in args:
        kwargs["acceptance"] = args["acceptance"]

    force_val: bool | None = None
    if "force" in args:
        force = args["force"]
        if not isinstance(force, bool):
            return ToolResult(
                ok=False,
                payload={},
                error_reason="force_must_be_bool",
            )
        force_val = force

    # force alone does not change goal fields — reject empty semantic updates.
    if not kwargs:
        return ToolResult(
            ok=False,
            payload={},
            error_reason="no_fields_to_update",
        )

    if force_val is not None:
        kwargs["force"] = force_val

    try:
        result = store.update_goal(goal_id, **kwargs)
    except KeyError:
        return _goal_not_found(goal_id)
    except TypeError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"invalid_args:{exc}",
        )
    except ValueError as exc:
        return ToolResult(
            ok=False,
            payload={},
            error_reason=f"invalid_args:{exc}",
        )

    goal = (result or {}).get("goal") or {}
    _mark_task_changed(
        ctx, what="update_goal", id_hint=str(goal.get("id", goal_id))
    )

    # Soft-close warning lives in store result; surface unchanged in payload.
    return ToolResult(ok=True, payload=result)
