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
