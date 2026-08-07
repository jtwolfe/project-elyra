---
name: update_group
description: Update a group conversation name, description, or full member list.
kind: mutate
---

# update_group

Patch an existing group conversation (name, description, and/or members).

- Required: `conversation_id` (`group:…`) — **args only**; host does not default
  from the current wake room (`ctx.conversation_id`).
- Optional: `name` (non-empty), `description` (string; empty/null clears),
  `members` (full replacement list, non-empty)
- At least one of `name` / `description` / `members` must be present.
- `members` is **full replacement** — include everyone who should remain.
  To add a user, pass previous members + the new id.
- Cannot empty members; cannot convert group→DM; cannot rename a DM with this tool.
- Does **not** auto-add operator or wake user. Unknown / removed session users
  stop seeing the group after their next list poll.
