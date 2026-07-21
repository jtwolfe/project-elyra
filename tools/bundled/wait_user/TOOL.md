---
name: wait_user
description: End the moment and wait for a user reply (optional multi-choice). Use after speak when you need a decision or free-text answer. Prefer speak then wait_user in the same turn.
kind: control
---

# wait_user

Arm a durable wait and stop the moment (`ends_moment`, `stop_reason=wait`).

- Required: `prompt` — question or prompt for the user.
- Optional: `choices` — multi-choice options (omit or empty for free text).
- Optional: `timeout_seconds` — wait timeout (default from settings, 120s).
- Optional: `user_id` — who to wait on (defaults to active wake user / operator).

On success the tool result includes `arm_wait` for the host; later tool calls
in the same assistant batch are not run (skills should order speak then wait).
Does not count as speak — call `speak` first if the user needs a visible message.
