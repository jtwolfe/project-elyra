"""Goals and tasks ledger store.

Scope: durable CRUD for goals/tasks under ``data/goals/goals.json``.
In scope: create/update/get/list; soft close warning; task_ready hook on
transition to ready; optional close-without-review counter; per-store RLock
around load-mutate-save with unique temp files.
Out of scope: wake queue enqueue, tools, presence, review-work skill body.

``on_task_ready`` is always invoked on a transition *to* ``status=ready``
(even if the worker is busy). Dedupe of wake events is the caller's
responsibility; this store does not suppress repeat ready transitions
from different prior statuses, and does not fire when already ready.

Create may not set ``status=closed`` — close goes through ``update_goal`` so
the soft-close warning / metric path always applies.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from elyra.config import ElyraPaths

GOAL_STATUSES = frozenset({"open", "review", "closed", "cancelled"})
# Create allows open/review/cancelled only; closed must use update_goal.
GOAL_CREATE_STATUSES = frozenset({"open", "review", "cancelled"})
TASK_STATUSES = frozenset(
    {"pending", "ready", "in_progress", "blocked", "done", "cancelled"}
)

SOFT_CLOSE_WARNING = (
    "prefer review-work before close; set status=review first or pass force=true"
)

OnTaskReady = Callable[[str, str], None]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _require_bool(name: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be bool, got {type(value).__name__}")
    return value


class GoalsStore:
    """JSON ledger of goals with nested tasks.

    Parameters
    ----------
    paths:
        Path roots (``data/goals/goals.json`` under ``paths.data_dir``).
    on_task_ready:
        Optional hook ``(task_id, goal_id) -> None`` called on every
        transition *to* ``ready``. Caller implements durable enqueue + dedupe.
        ``None`` is fine (hook optional).

    Thread safety: one ``threading.RLock`` serializes load-mutate-save on this
    instance. Concurrent callers on the *same* store instance will not lose
    updates or collide on temp files. Separate processes / separate store
    instances sharing a path are still multi-writer-unsafe (S1: one store).
    """

    def __init__(
        self,
        paths: ElyraPaths,
        *,
        on_task_ready: OnTaskReady | None = None,
    ) -> None:
        self._paths = paths
        self._on_task_ready = on_task_ready
        self._lock = threading.RLock()
        self.goal_close_without_review: int = 0

    @property
    def store_path(self) -> Path:
        return self._paths.data_dir / "goals" / "goals.json"

    # ── load / save ──────────────────────────────────────────────────────

    def _empty_doc(self) -> dict[str, Any]:
        return {"goals": []}

    def _load(self) -> dict[str, Any]:
        path = self.store_path
        if not path.is_file():
            return self._empty_doc()
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return self._empty_doc()
        if not raw.strip():
            return self._empty_doc()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return self._empty_doc()
        if not isinstance(data, dict):
            return self._empty_doc()
        goals = data.get("goals")
        if not isinstance(goals, list):
            data["goals"] = []
        return data

    def _save(self, doc: Mapping[str, Any]) -> None:
        """Write ledger via unique temp + replace (caller holds ``_lock``)."""
        path = self.store_path
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
        # Unique temp avoids concurrent writers stomping the same .tmp name.
        tmp = path.with_name(
            f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _find_goal(
        self, doc: Mapping[str, Any], goal_id: str
    ) -> dict[str, Any] | None:
        for g in doc.get("goals", []):
            if isinstance(g, dict) and g.get("id") == goal_id:
                return g
        return None

    def _find_task(
        self, doc: Mapping[str, Any], task_id: str
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        for g in doc.get("goals", []):
            if not isinstance(g, dict):
                continue
            tasks = g.get("tasks") or []
            if not isinstance(tasks, list):
                continue
            for t in tasks:
                if isinstance(t, dict) and t.get("id") == task_id:
                    return g, t
        return None

    # ── goals ────────────────────────────────────────────────────────────

    def create_goal(
        self,
        title: str,
        *,
        acceptance: str | None = None,
        status: str = "open",
    ) -> dict[str, Any]:
        """Create a goal. Default status ``open``.

        ``status=closed`` is rejected — use ``update_goal`` so soft-close
        warning/metric apply. Allowed: open, review, cancelled.
        """
        if status not in GOAL_CREATE_STATUSES:
            if status == "closed":
                raise ValueError(
                    "cannot create goal with status=closed; "
                    "create open (or review/cancelled) then update_goal to close"
                )
            raise ValueError(f"invalid goal status: {status!r}")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")
        now = _now()
        goal: dict[str, Any] = {
            "id": _new_id("g"),
            "title": title.strip(),
            "status": status,
            "acceptance": acceptance,
            "created_at": now,
            "updated_at": now,
            "tasks": [],
        }
        with self._lock:
            doc = self._load()
            doc.setdefault("goals", []).append(goal)
            self._save(doc)
        return dict(goal)

    def get_goal(self, goal_id: str) -> dict[str, Any] | None:
        """Return a goal dict (with nested tasks) or None."""
        with self._lock:
            doc = self._load()
            g = self._find_goal(doc, goal_id)
            return dict(g) if g is not None else None

    def list_goals(
        self,
        *,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """List goals; optional filter by status."""
        if status is not None and status not in GOAL_STATUSES:
            raise ValueError(f"invalid goal status: {status!r}")
        with self._lock:
            doc = self._load()
            out: list[dict[str, Any]] = []
            for g in doc.get("goals", []):
                if not isinstance(g, dict):
                    continue
                if status is not None and g.get("status") != status:
                    continue
                out.append(dict(g))
            return out

    def update_goal(
        self,
        goal_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        acceptance: str | None = ...,  # type: ignore[assignment]
        force: bool = False,
    ) -> dict[str, Any]:
        """Update goal fields.

        Soft close: transitioning ``open`` → ``closed`` without ``force=true``
        still closes the goal but returns a warning and increments
        ``goal_close_without_review``. ``force=true`` closes without the
        warning key (metric still counts). Closing from ``review`` is clean.

        ``force`` must be a real ``bool`` (not truthy/falsy coercion).
        """
        force = _require_bool("force", force)

        with self._lock:
            doc = self._load()
            goal = self._find_goal(doc, goal_id)
            if goal is None:
                raise KeyError(f"goal not found: {goal_id!r}")

            if status is not None and status not in GOAL_STATUSES:
                raise ValueError(f"invalid goal status: {status!r}")
            if title is not None:
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("title must be a non-empty string")
                goal["title"] = title.strip()
            if acceptance is not ...:
                goal["acceptance"] = acceptance

            warning: str | None = None
            prev_status = goal.get("status")
            if status is not None and status != prev_status:
                if prev_status == "open" and status == "closed":
                    self.goal_close_without_review += 1
                    if not force:
                        warning = SOFT_CLOSE_WARNING
                goal["status"] = status

            goal["updated_at"] = _now()
            self._save(doc)
            goal_copy = dict(goal)

        result: dict[str, Any] = {"ok": True, "goal": goal_copy}
        if warning is not None:
            result["warning"] = warning
        return result

    # ── tasks ────────────────────────────────────────────────────────────

    def create_task(
        self,
        goal_id: str,
        title: str,
        *,
        status: str = "pending",
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Create a task under ``goal_id``. Default status ``pending``.

        If created directly as ``ready``, the ``on_task_ready`` hook fires
        (transition into ready from non-existence).
        """
        if status not in TASK_STATUSES:
            raise ValueError(f"invalid task status: {status!r}")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("title must be a non-empty string")

        with self._lock:
            doc = self._load()
            goal = self._find_goal(doc, goal_id)
            if goal is None:
                raise KeyError(f"goal not found: {goal_id!r}")

            now = _now()
            task: dict[str, Any] = {
                "id": _new_id("t"),
                "goal_id": goal_id,
                "title": title.strip(),
                "status": status,
                "notes": notes,
                "created_at": now,
                "updated_at": now,
            }
            tasks = goal.setdefault("tasks", [])
            if not isinstance(tasks, list):
                goal["tasks"] = [task]
            else:
                tasks.append(task)
            goal["updated_at"] = now
            self._save(doc)
            task_copy = dict(task)

        if status == "ready":
            self._fire_task_ready(task_copy["id"], goal_id)
        return task_copy

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Return a task dict or None."""
        with self._lock:
            doc = self._load()
            found = self._find_task(doc, task_id)
            if found is None:
                return None
            _, task = found
            return dict(task)

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        status: str | None = None,
        notes: str | None = ...,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        """Update task fields.

        On transition **to** ``status=ready`` (previous status was not ready),
        always call ``on_task_ready(task_id, goal_id)`` if set. Already-ready
        does not re-fire. Dedupe is the hook's contract. Hook runs after save
        and outside the store lock.
        """
        with self._lock:
            doc = self._load()
            found = self._find_task(doc, task_id)
            if found is None:
                raise KeyError(f"task not found: {task_id!r}")
            goal, task = found

            if status is not None and status not in TASK_STATUSES:
                raise ValueError(f"invalid task status: {status!r}")
            if title is not None:
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("title must be a non-empty string")
                task["title"] = title.strip()
            if notes is not ...:
                task["notes"] = notes

            prev_status = task.get("status")
            became_ready = False
            if status is not None and status != prev_status:
                if status == "ready" and prev_status != "ready":
                    became_ready = True
                task["status"] = status

            now = _now()
            task["updated_at"] = now
            goal["updated_at"] = now
            self._save(doc)
            task_copy = dict(task)
            goal_id = task_copy["goal_id"]

        if became_ready:
            self._fire_task_ready(task_copy["id"], goal_id)

        return {"ok": True, "task": task_copy}

    def _fire_task_ready(self, task_id: str, goal_id: str) -> None:
        hook = self._on_task_ready
        if hook is not None:
            hook(task_id, goal_id)
