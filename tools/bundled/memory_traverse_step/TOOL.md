---
name: memory_traverse_step
description: Expand selected frontier memory nodes and/or mark provisional keeps. Returns updated thin surface with previews for newly expanded atoms.
kind: read
---

# memory_traverse_step

One tool step of an active directed traversal.

- Optional: `session_id` — defaults to the sole active session
- Optional: `expand_ids` — frontier atoms to expand (cap per step)
- Optional: `keep_ids` — provisional keep set merge (must be considered)
- Optional: `scratchpad` — model notes (clipped)
- Optional: `include_noisy_kinds` — default false; include tool/ledger/model in maps

Newly expanded destinations include **preview** (≤400 chars). Prefer
`memory_traverse_inspect` before keep when the 80-char label is insufficient.

When focus moves (`expand_ids`), the host returns **`local_map`** for the first
successfully expanded id and optional **`local_maps`** (≤3) when multiple
expand sources succeed. Prefer reading the map before further expand.

Does **not** call `compose_meal` or rebuild outer context.

## Errors

| `error_reason` | Meaning |
|----------------|---------|
| `traverse_disabled` | Feature flag off |
| `traverse_unavailable` | Ports missing |
| `no_active_session` | No active walk |
| `unknown_session` | session_id mismatch |
| `invalid_args` | Bad argument types |
