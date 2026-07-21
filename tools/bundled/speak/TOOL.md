---
name: speak
description: Address a user with a message delivered to the chat glass. Use when the user needs a reply or update. Only speak writes user-visible assistant chat rows.
kind: speak
---

# speak

Deliver a product speech act to the user via glass/chat transport.

- Required: `text` — the message content.
- Optional: `user_id` — recipient (defaults to the active wake user / operator).

On success the tool result has `transport_ok: true` and counts as a speak for
the moment. On transport failure you get `ok: false` with a reason — do not
assume the user saw the message.
