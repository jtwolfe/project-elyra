---
name: create_task
description: Create a task under a goal (title + goal_id required; default status pending).
kind: mutate
---

# create_task

Create a task under an existing goal in the goals/tasks ledger.

- Required: `goal_id`, `title`
- Optional: `status` (pending|ready|in_progress|blocked|done|cancelled; default
  `pending`), `notes`, `created_in_context` `{user_id, goes_by?}`
- Creating with `status=ready` enqueues a `task_ready` wake when the host port
  is set (same dual-path contract as `update_task` → ready).
- Does not inherit parent goal context; host snapshots from session user when set.
- Continuous / null `ctx.user_id` → no `created_in_context` (expected).
- On success, marks ledger activity (`mark_task_changed`) for continue policy.
