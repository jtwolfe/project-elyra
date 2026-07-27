---
name: update_goal
description: Update a goal (title, status, acceptance). Soft close from open without force returns a warning.
kind: mutate
---

# update_goal

Patch fields on an existing goal in the goals/tasks ledger.

- Required: `goal_id`
- Optional: `title`, `status` (open|review|closed|cancelled), `acceptance`, `force`
- Prefer `status=review` (via review-work) before `closed`. Closing from `open` without
  `force=true` still closes but returns a warning in the tool result payload.
- `force` only bypasses the soft-close warning key; it is not a promote override.
  Meaningful only with a field change (e.g. `status=closed`); `force` alone is rejected.
- On success, marks ledger activity (`mark_task_changed`) for continue policy.
- On unknown `goal_id`: `ok=false`, `error_reason=goal_not_found`, payload echoes
  `goal_id` and a soft `hint` — call `list_goals` to refresh ids, then retry
  with an exact ledger id. Do not invent goal ids.
