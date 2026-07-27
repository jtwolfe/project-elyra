---
name: speak
description: Address a user with a message delivered to the chat glass. Use when the user needs a reply or update. Only speak writes user-visible assistant chat rows. May attach tool-produced sandbox files or re-send prior attachment ids (caption still required).
kind: speak
---

# speak

Deliver a product speech act to the user via glass/chat transport. **Only a
successful `speak` reaches glass** — free-text assistant content is not user-
visible chat.

- Required: `text` — the message caption/content. **Must be non-empty** even
  when attachments are present (host rejects empty text + media-only speaks).
- Optional: `user_id` — recipient (defaults to the active wake user / operator).
- Optional: `attachments` — list of `{ path, filename?, kind? }` sandbox paths
  (e.g. after writing `tmp/plot.png`). Host ingests into the durable media store
  and projects a read-only mirror under `media/`.
- Optional: `attachment_ids` — re-send prior host attachment ids (new att_id,
  same blob sha, per-message inventory).

On success the tool result has `transport_ok: true` and counts as a speak for
the moment. On transport failure you get `ok: false` with a reason — do not
assume the user saw the message.

On social wakes, call `speak` first (see skill `talk`). Speak before
`wait_user` in the same batch.

## Attachments (tool-produced media)

```text
# After writing tmp/plot.png
speak(
  text="Here is the plot from the run.",
  attachments=[{"path": "tmp/plot.png"}]
)
# Do not call speak with empty text + only attachments — host rejects.
```

Re-send a prior attachment by id:

```text
speak(
  text="Here is that plot again.",
  attachment_ids=["att_…"]
)
```
