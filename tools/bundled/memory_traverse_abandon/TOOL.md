---
name: memory_traverse_abandon
description: Discard the active memory walk. Does not clear last finished glass walk or meal keep snapshot.
kind: read
---

# memory_traverse_abandon

Abandon **active** session only (KD-A9 / KD-A19):

- Provisional keep discarded
- `last_session` and `last_confirmed_keep` **retained** (process-life glass sticky;
  not disk-durable across restart)

- Optional: `session_id`
- Optional: `reason` — default `abandoned`

## Errors

| `error_reason` | Meaning |
|----------------|---------|
| `traverse_disabled` | Feature flag off |
| `traverse_unavailable` | Ports missing |
| `no_active_session` | Nothing active |
| `unknown_session` | session_id mismatch |
