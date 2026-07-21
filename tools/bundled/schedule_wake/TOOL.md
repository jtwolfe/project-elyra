---
name: schedule_wake
description: Schedule a future timer wake. Use when work should resume after a delay or at a specific time (not for waiting on a user reply — use wait_user for that).
kind: control
---

# schedule_wake

Record a durable timer. When due, presence enqueues a `timer` wake.

- Provide exactly one of:
  - `wake_at` — absolute ISO UTC timestamp
  - `delay_seconds` — relative seconds from now
- Optional: `reason` — short note for orient / payload
- Optional: `goal_id`, `task_id` — ledger linkage

Does not end the moment. Does not speak. Does not wait for the user.
