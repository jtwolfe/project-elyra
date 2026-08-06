---
name: wait_user
description: End the moment and wait for a user reply (optional multi-choice). Use after speak when you need a decision or free-text answer. Prefer speak then wait_user in the same turn.
kind: control
---

# wait_user

Arm a durable wait and stop the moment (`ends_moment`, `stop_reason=wait`).

- Required: `prompt` — question or prompt for the user.
- Optional: `choices` — multi-choice options (omit or empty for free text).
- Optional: `timeout_seconds` — wait timeout. Host default is **300s** (5 minutes). Free-text waits (empty `choices`) also default to **300s** when omitted. Prefer longer for open-ended discussion or custom typed answers.
- Optional: `user_id` — who to wait on (defaults to active wake user / operator).

## Guidance

- Multi-choice is good for collaborative forks (adopt / revise / hold).
- Free-text / "I'll type" style: leave `choices` empty and use a long timeout — do not pass short 30–120s values for thoughtful replies.
- When a speak presents **numbered or lettered collaborative forks** the human should pick among, prefer `wait_user` with those fork strings as `choices` (same wording as glass buttons). Do not invent choices that were not offered.
- On success the tool result includes `arm_wait` for the host; later tool calls in the same assistant batch are not run (skills should order speak then wait).
- Does not count as speak — call `speak` first if the user needs a visible message.

## Example (research close + numbered forks)

After a long research speak that ends with collaborative forks, e.g.:

- (1) dig Wikipedia lineage
- (2) compare Grokipedia claims
- (3) formal math path
- (4) stop / something else

Prefer:

```text
speak(...full answer + forks on glass...)
wait_user({
  prompt: "Which fork next?",
  choices: [
    "dig Wikipedia lineage",
    "compare Grokipedia claims",
    "formal math path",
    "stop / something else"
  ],
  timeout_seconds: 300
})
```

Order is **`speak` then `wait_user`**. Soft prefer when forks are real decisions — not a hard fail for purely illustrative lists.
