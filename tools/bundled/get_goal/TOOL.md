---
name: get_goal
description: Get one goal by id, including nested tasks (full detail).
kind: read
---

# get_goal

Fetch a single goal and its nested tasks from the ledger.

- Required: `goal_id`
- Returns full goal dict (title, status, acceptance, timestamps, tasks)
- Read-only: does **not** mark ledger activity
- On unknown `goal_id`: `ok=false`, `error_reason=goal_not_found`, payload echoes
  `goal_id` and a soft `hint` — call `list_goals` to refresh ids, then retry
  with an exact ledger id. Do not invent goal ids.
