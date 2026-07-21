"""Builtin ledger tools: update_task and update_goal.

Scope: host entries for tools/bundled/update_task and update_goal.
In scope: GoalsStore via ToolContext.goals; soft-close warning in payload;
  task→ready notifies enqueue_wake (when set) and mark_task_changed.
Out of scope: wake-queue dedupe implementation, create_goal/create_task tools,
  presence worker wiring (host injects ports).
"""

from __future__ import annotations

from typing import Any

from elyra.tools.types import ToolContext, ToolResult


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

    Always durable-enqueues ``task_ready`` when ``ctx.enqueue_wake`` is set
    (even if the worker is busy). Store ``on_task_ready`` may also fire when
    the host wired that hook; wake-queue dedupe is the caller's job.
    """
    store, err = _goals(ctx)
    if err is not None:
        return err

    task_id, id_err = _require_str_id(args, "task_id")
    if id_err is not None:
        return id_err

    prev = store.get_task(task_id)
    if prev is None:
        return ToolResult(ok=False, payload={}, error_reason="task_not_found")
    prev_status = prev.get("status")

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

    task = result.get("task") or {}
    new_status = task.get("status")

    # Continue-policy: any successful task mutation counts as activity.
    if ctx.mark_task_changed is not None:
        ctx.mark_task_changed()

    # Transition to ready → task_ready wake (tool-layer port).
    became_ready = new_status == "ready" and prev_status != "ready"
    if became_ready and ctx.enqueue_wake is not None:
        ctx.enqueue_wake(
            kind="task_ready",
            task_id=task.get("id", task_id),
            goal_id=task.get("goal_id"),
        )

    return ToolResult(ok=True, payload=result)


def update_goal(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Patch a goal; soft close (open→closed without force) includes warning.

    Payload mirrors GoalsStore.update_goal: ``ok``, ``goal``, optional
    ``warning`` when closing from open without ``force=true``.
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
    if "force" in args:
        force = args["force"]
        if not isinstance(force, bool):
            return ToolResult(
                ok=False,
                payload={},
                error_reason="force_must_be_bool",
            )
        kwargs["force"] = force

    if not kwargs:
        return ToolResult(
            ok=False,
            payload={},
            error_reason="no_fields_to_update",
        )

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
