---
name: wait_user
description: End the moment and wait for a reply in a DM or group conversation (optional multi-choice). Use after speak when you need a decision or free-text answer. Prefer speak then wait_user in the same turn.
kind: control
---

# wait_user

Arm a durable wait and stop the moment (`ends_moment`, `stop_reason=wait`).

- Required: `prompt` — question or prompt for the user.
- Optional: `choices` — multi-choice options (omit or empty for free text).
- Optional: `timeout_seconds` — wait timeout. Host default is **300s** (5 minutes). Free-text waits (empty `choices`) also default to **300s** when omitted. Prefer longer for open-ended discussion or custom typed answers.
- Optional: `conversation_id` — social address (`dm:<user>` or `group:<id>`).
  Same resolution as `speak`. Prefer explicit id on groups (same room as the
  preceding speak). Group waits arm on the room; members only match when their
  client session is bound to that group (`matches_session`).
- Optional: `user_id` — arming / notify stamp (defaults to active wake user).
  For groups this is **not** the sole match key — membership + session binding
  apply.

## Guidance

- Multi-choice is good for collaborative pick-one options (A/B/C, adopt / revise / hold).
- Free-text / "I'll type" style: leave `choices` empty and use a long timeout — do not pass short 30–120s values for thoughtful replies.
- When a speak presents **A/B/C, (1)/(2)/(3), or short labeled options** the human should pick among, prefer `wait_user` with those option strings as `choices` (same wording as Glass buttons). Do not invent choices that were not offered.
- On success the tool result includes `arm_wait` for the host; later tool calls in the same assistant batch are not run (skills should order speak then wait).
- Does not count as speak — call `speak` first if the user needs a visible message.
- **Same conversation as speak:** if you spoke to `group:…`, arm wait on that group — do not arm a DM wait for a group question.
- **Group match:** a member viewing Private Chat (`dm:self`) does **not** satisfy a group wait until their session is on that group.
- **Multi-wait residual:** host keeps a single first-pending wait selection; dogfood one armed wait at a time (do not rely on concurrent jim-wait + operator-wait correctness).

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
  # optional: conversation_id="group:…" or "dm:jim" when not inherited from ctx
})
```

Order is **`speak` then `wait_user`**. Soft prefer when forks are real decisions — not a hard fail for purely illustrative lists.
