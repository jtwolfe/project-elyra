---
name: get_task
description: Get one task by id (includes parent goal_id).
kind: read
---

# get_task

Fetch a single task from the goals/tasks ledger.

- Required: `task_id`
- Returns task dict including `goal_id`, title, status, notes, timestamps
- Read-only: does **not** mark ledger activity
- On unknown `task_id`: `ok=false`, `error_reason=task_not_found`, payload echoes
  `task_id` and a soft `hint` — call `list_goals` (or `get_goal`) to refresh
  ids, then retry with an exact ledger id. Do not invent task ids.
