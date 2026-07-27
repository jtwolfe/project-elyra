---
name: update_task
description: Update a goal task (title, status, notes). Transition to ready enqueues a task_ready wake.
kind: mutate
---

# update_task

Patch fields on an existing task in the goals/tasks ledger.

- Required: `task_id`
- Optional: `title`, `status` (pending|ready|in_progress|blocked|done|cancelled), `notes`
- On **transition** to `status=ready` (not when already ready), durable-enqueues a
  `task_ready` wake when the host port is set (deduped by the host).
- On success, marks ledger activity (`mark_task_changed`) for continue policy.
- Does **not** end the moment; task `blocked` is ledger state only, not a moment stop.
- On unknown `task_id`: `ok=false`, `error_reason=task_not_found`, payload echoes
  `task_id` and a soft `hint` — call `list_goals` (or `get_goal`) to refresh
  ids, then retry with an exact ledger id. Do not invent task ids.
