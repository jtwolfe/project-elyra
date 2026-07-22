---
name: create_goal
description: Create a new goal in the goals/tasks ledger (title required; default status open).
kind: mutate
---

# create_goal

Create a goal in the durable goals/tasks ledger.

- Required: `title`
- Optional: `acceptance`, `status` (open|review|cancelled; default `open`)
- Cannot create with `status=closed` — create open (or review/cancelled) then
  `update_goal` to close so soft-close warning/metric apply.
- On success, marks ledger activity (`mark_task_changed`) for continue policy.
