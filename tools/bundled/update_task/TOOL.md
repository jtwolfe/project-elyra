---
name: update_task
description: Update a goal task (title, status, notes). Transition to ready enqueues a task_ready wake.
kind: mutate
---

# update_task

Patch fields on an existing task in the goals/tasks ledger.

- Required: `task_id`
- Optional: `title`, `status` (pending|ready|in_progress|blocked|done|cancelled), `notes`
- Setting `status` to `ready` always durable-enqueues a `task_ready` wake (deduped by the host).
- Does **not** end the moment; task `blocked` is ledger state only, not a moment stop.
