---
name: list_goals
description: List goals from the ledger (compact: id, title, status, short tasks). Optional status filter.
kind: read
---

# list_goals

List goals in the durable goals/tasks ledger (compact summaries).

- Optional: `status` (open|review|closed|cancelled) — filter to that status
- Returns id, title, status, task_count, and short task summaries (id/title/status)
- Read-only: does **not** mark ledger activity
- For full goal detail (acceptance, full task notes), use `get_goal`
