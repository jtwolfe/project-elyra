---
name: speak
description: Address a user with a message delivered to the chat glass. Use when the user needs a reply or update. Only speak writes user-visible assistant chat rows.
kind: speak
---

# speak

Deliver a product speech act to the user via glass/chat transport. **Only a
successful `speak` reaches glass** — free-text assistant content is not user-
visible chat.

- Required: `text` — the message content.
- Optional: `user_id` — recipient (defaults to the active wake user / operator).

On success the tool result has `transport_ok: true` and counts as a speak for
the moment. On transport failure you get `ok: false` with a reason — do not
assume the user saw the message.

On social wakes, call `speak` first (see skill `talk`). Speak before
`wait_user` in the same batch.
