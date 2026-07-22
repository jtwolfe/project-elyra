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
