---
name: speak
description: Address a DM or group conversation on glass. Use when a human needs a reply or update. Only speak writes user-visible assistant chat rows. May attach tool-produced sandbox files or re-send prior attachment ids (caption still required).
kind: speak
---

# speak

Deliver a product speech act via glass/chat transport to a **conversation**
(`dm:<user>` or `group:<id>`). **Only a successful `speak` reaches glass** —
free-text assistant content is not user-visible chat.

- Required: `text` — the message caption/content. **Must be non-empty** even
  when attachments are present (host rejects empty text + media-only speaks).
- Optional: `conversation_id` — social address (`dm:<user>` or `group:<id>`).
  **Prefer when known.** Group wakes always need this or wake-stamped ctx;
  lost group address fails closed (`missing_conversation`) — host will **not**
  demote to `dm:<speaker>`.
- Optional: `user_id` — DM shorthand (`dm:<user_id>`) or arming stamp. On pure
  DM wakes defaults from context; **not** used as room address when the host
  social_kind is group.
- Optional: `attachments` — list of `{ path, filename?, kind? }` sandbox paths
  (e.g. after writing `tmp/plot.png`). Host ingests into the durable media store
  and projects a read-only mirror under `media/`.
- Optional: `attachment_ids` — re-send prior host attachment ids (new att_id,
  same blob sha, per-message inventory).

**Addressing notes**

- **DM:** `conversation_id=dm:<peer>` or `user_id=<peer>`; assistant row stamps
  peer `user_id` for legacy labels.
- **Group:** pass `conversation_id=group:…` (or rely on wake ctx). Assistant
  group rows use **null** peer `user_id` — conversation_id is authoritative;
  Glass labels “Elyra” via role.
- **Solo / pure work:** no social address → may fail closed if you call speak
  without target; projective speak to a room needs an explicit address.

On success the tool result has `transport_ok: true` and counts as a speak for
the moment. On transport failure you get `ok: false` with a reason — do not
assume the user saw the message.

On social wakes, call `speak` first (see skill `talk`). Speak before
`wait_user` in the same batch; keep the same conversation on both tools.

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

## Group / DM examples

```text
# Group room (prefer explicit conversation_id)
speak(
  text="Noted — I'll summarize for the room.",
  conversation_id="group:…"
)

# Private Chat DM shorthand
speak(
  text="Got it.",
  user_id="jim"
)
# equivalent: conversation_id="dm:jim"
```
