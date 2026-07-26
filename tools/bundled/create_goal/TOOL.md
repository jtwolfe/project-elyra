---
name: create_goal
description: Create a new goal in the goals/tasks ledger (title required; default status open).
kind: mutate
---

# create_goal

Create a goal in the durable goals/tasks ledger.

- Required: `title`
- Optional: `acceptance`, `status` (open|review|cancelled; default `open`),
  `created_in_context` `{user_id, goes_by?}` (host usually snapshots session user)
- Cannot create with `status=closed` — create open (or review/cancelled) then
  `update_goal` to close so soft-close warning/metric apply.
- Continuous / null `ctx.user_id` → no `created_in_context` (expected).
- On success, marks ledger activity (`mark_task_changed`) for continue policy.
