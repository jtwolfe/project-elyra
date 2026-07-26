"""Work-origin USER resolver for orient injection (K13/K19).

Scope: choose at most one user_id for orient USER digest.
In scope: social payload speaker; linked goal/task created_in_context; empty.
Out of scope: multi-user task assignment, last-speaker memory, Glass session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from elyra.loop.continuous_policy import SOCIAL_WAKE_KINDS

if TYPE_CHECKING:
    from elyra.goals import GoalsStore
    from elyra.users import UsersStore


class _WakeLike(Protocol):
    kind: str
    payload: dict[str, Any] | None


def resolve_orient_user(
    wake: _WakeLike,
    *,
    users: "UsersStore",
    goals: "GoalsStore | None" = None,
) -> tuple[str | None, str]:
    """Return ``(user_id_or_None, digest_text)`` for orient USER.

    digest_text is ``""`` when there is no human counterpart for this wake.
    Never invents operator; never puts Elyra in USER.
    """
    payload = wake.payload or {}
    kind = wake.kind

    # 1. Social wakes: who is speaking
    if kind in SOCIAL_WAKE_KINDS:
        uid = payload.get("user_id")
        if isinstance(uid, str) and uid.strip():
            clean = uid.strip()
            return clean, _safe_profile(users, clean)
        # Social without user_id is anomalous — empty, do not invent operator
        return None, ""

    # 2. Work wakes: linked goal/task created_in_context (PR4 fills this in)
    if goals is not None:
        ctx_uid = _created_in_context_user_from_wake(wake, goals)
        if ctx_uid:
            return ctx_uid, _safe_profile(users, ctx_uid)

    # 3. Autonomous / no social counterpart / empty ledger link
    return None, ""


def _created_in_context_user_from_wake(wake: _WakeLike, goals: Any) -> str | None:
    """Prefer task context, else goal context, only for ids present on the wake."""
    payload = wake.payload or {}
    task_id = payload.get("task_id")
    goal_id = payload.get("goal_id")

    if isinstance(task_id, str) and task_id.strip():
        find_task = getattr(goals, "find_task", None)
        get_task = getattr(goals, "get_task", None)
        found = None
        if callable(find_task):
            try:
                found = find_task(task_id.strip())
            except Exception:  # noqa: BLE001 — fail soft
                found = None
        elif callable(get_task):
            try:
                found = get_task(task_id.strip())
            except Exception:  # noqa: BLE001
                found = None
        if found:
            # find_task → (goal, task); get_task may return task only
            if isinstance(found, tuple) and len(found) == 2:
                goal, task = found
                uid = _context_user_id(task) or _context_user_id(goal)
            elif isinstance(found, dict):
                uid = _context_user_id(found)
            else:
                uid = None
            if uid:
                return uid

    if isinstance(goal_id, str) and goal_id.strip():
        get_goal = getattr(goals, "get_goal", None)
        if callable(get_goal):
            try:
                goal = get_goal(goal_id.strip())
            except Exception:  # noqa: BLE001
                goal = None
            if goal:
                uid = _context_user_id(goal)
                if uid:
                    return uid
    return None


def _context_user_id(entity: Any) -> str | None:
    if not isinstance(entity, dict):
        return None
    ctx = entity.get("created_in_context")
    if not isinstance(ctx, dict):
        return None
    uid = ctx.get("user_id")
    if isinstance(uid, str) and uid.strip():
        return uid.strip()
    return None


def _safe_profile(users: "UsersStore", user_id: str) -> str:
    try:
        return users.profile(user_id)  # current.md only
    except ValueError:
        return ""
