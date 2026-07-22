---
name: create_task
description: Create a task under a goal (title + goal_id required; default status pending).
kind: mutate
---

# create_task

Create a task under an existing goal in the goals/tasks ledger.

- Required: `goal_id`, `title`
- Optional: `status` (pending|ready|in_progress|blocked|done|cancelled; default
  `pending`), `notes`
- Creating with `status=ready` enqueues a `task_ready` wake when the host port
  is set (same dual-path contract as `update_task` → ready).
- On success, marks ledger activity (`mark_task_changed`) for continue policy.
