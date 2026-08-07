---
name: create_group
description: Create a multi-party group conversation (explicit members only; does not auto-add operator).
kind: mutate
---

# create_group

Create a multi-party group conversation on the host conversations store.

- Required: `name` (non-empty display name), `members` (non-empty list of user_ids)
- Optional: `description`, `conversation_id` (`group:…` for tests/seeds; omit to mint UUID)
- Pass **explicit** `members` user_ids (from orient Participants / users the human named).
  Prefer real identity ids (`jim`, `sam`), not display names alone.
- **Do not** assume operator is a member. Do not add yourself (Elyra) — Elyra is
  never in `members`. Host does **not** auto-add `ctx.user_id` / operator.
- After create, call `speak` with the returned `conversation_id` if the room needs
  a message; members discover the room via Glass/chat list refresh (≤5s dogfood).
- Not a substitute for `speak` / `wait_user`. Does not notify members.
