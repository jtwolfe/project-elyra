---
name: memory_traverse_finish
description: Confirm the walk keep-set and emit a walk summary. Meal directed_keep packs on the next outer rebuild (not necessarily same hop). Glass sees the session immediately.
kind: read
---

# memory_traverse_finish

Confirm the active session → sticky `last_session` (glass) + `last_confirmed_keep` (meal-thin).

- Optional: `session_id`
- Optional: `keep_ids` — final ordered keep-set (omit = provisional set; empty list clears)
- Optional: `summary_hint` — appended to template NL summary

**Meal timing (KD-A16):** confirmed keeps appear in the **outer meal** on the next
`compose_meal` / re-outer / moment boundary — **not** guaranteed same hop after this tool.

**Glass stickiness:** last finished walk is **process-life** only (survives moment
close; abandon drops active only). Not disk-sticky across process restart.

Optional sequential ±1 keep-adjacent when `traverse_keep_adjacent` is true.

## Errors

| `error_reason` | Meaning |
|----------------|---------|
| `traverse_disabled` | Feature flag off |
| `traverse_unavailable` | Ports missing |
| `no_active_session` | Nothing active to finish |
| `unknown_session` | session_id mismatch |
