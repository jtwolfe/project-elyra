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
