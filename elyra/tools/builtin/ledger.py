"""Builtin ledger tools: update_task and update_goal.

Scope: host entries for tools/bundled/update_task and update_goal.
In scope: GoalsStore via ToolContext.goals; soft-close warning in payload;
  task→ready notifies enqueue_wake (when set) and mark_task_changed.
Out of scope: wake-queue dedupe implementation, create_goal/create_task tools,
  presence worker wiring (host injects ports).

Dual task_ready notify paths
----------------------------
Two emission surfaces exist; the composition root must not wire **both** to a
raw wake enqueue without replace/dedupe:

1. **GoalsStore(on_task_ready=…)** — fires on every store transition to ready
   (including create-as-ready and direct API updates). Preferred sole emission
   site when the host owns the store lifecycle.
2. **ToolContext.enqueue_wake** — tool layer only, after a successful
   ``update_task`` when the store result reports ``became_ready=True``. Use
   when the store hook is unset (tests / thin wiring).

Recommended S1 wiring: pick **one** path for durable wake enqueue. If both
fire, presence must dedupe (replace pending task_ready for the same task_id).
Enqueue after commit is best-effort at the tool layer (see update_task): a
failed port does not roll back the ledger; the result stays ok with a warning
so hosts can recover without a stranded already-ready short-circuit.
"""

from __future__ import annotations

import logging
from typing import Any

from elyra.tools.types import ToolContext, ToolResult

_LOG = logging.getLogger(__name__)


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
        return ToolResult(ok=False, payload={}, error_reason="task_not_found")
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
    if ctx.mark_task_changed is not None:
        try:
            ctx.mark_task_changed()
        except Exception:
            _LOG.exception(
                "mark_task_changed failed after task update task_id=%s",
                task.get("id", task_id),
            )

    # Transition to ready → task_ready wake (tool-layer port). Best-effort:
    # never fail the tool after a durable ready commit.
    if became_ready and ctx.enqueue_wake is not None:
        try:
            ctx.enqueue_wake(
                kind="task_ready",
                task_id=task.get("id", task_id),
                goal_id=task.get("goal_id"),
            )
        except Exception as exc:
            _LOG.exception(
                "task_ready enqueue_wake failed after ready commit task_id=%s",
                task.get("id", task_id),
            )
            payload["warning"] = f"task_ready_enqueue_failed:{type(exc).__name__}"

    return ToolResult(ok=True, payload=payload)


def update_goal(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Patch a goal; soft close (open→closed without force) includes warning.

    Payload mirrors GoalsStore.update_goal: ``ok``, ``goal``, optional
    ``warning`` when closing from open without ``force=true``.

    ``force`` is only meaningful with a status (or other field) change; force
    alone is rejected as ``no_fields_to_update``.
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
        return ToolResult(ok=False, payload={}, error_reason="goal_not_found")
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

    # Soft-close warning lives in store result; surface unchanged in payload.
    return ToolResult(ok=True, payload=result)
