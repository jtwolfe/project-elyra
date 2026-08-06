---
name: memory_keep_update
description: Merge, replace, remove, or clear sticky directed-keep pins for the next outer meal. Context management — not a walk. No active traverse session required.
kind: read
---

# memory_keep_update

Update the **instance sticky directed-keep tray** (meal channel `directed_keep`)
without starting or finishing a traversal walk.

- Optional: `mode` — `merge` (default) or `replace`
- Optional: `atom_ids` — atoms to pin/keep (reinforce if already pinned under merge)
- Optional: `remove_ids` — atom ids to drop from the tray
- Optional: `note` — short summary for the meal (`walk_summary_nl`, ≤240 recommended)

## Semantics

| mode | atom_ids | remove_ids | Result |
|------|----------|------------|--------|
| `merge` (default) | non-empty | optional | Union pin/reinforce; then drop `remove_ids` |
| `merge` | empty | non-empty | Remove only |
| `merge` | empty | empty | `invalid_args` (no-op refused) |
| `replace` | non-empty | optional | Tray becomes `atom_ids` only (then remove) |
| `replace` | empty | * | **Clear tray**: empty entries; `walk_summary_nl=null` unless `note` provided |

**Abandon ≠ clear.** Finishing a walk still merges into the tray; abandoning a walk
**retains** keep. Use empty `mode=replace` to intentionally clear pins.

**Meal timing (KD-A16):** tray is sticky on disk immediately. Outer meal packs
`directed_keep` on the **next** `compose_meal` / re-outer — not necessarily same hop.
Success payload includes `meal_timing: "next_compose"`.

## Errors

| `error_reason` | Meaning |
|----------------|---------|
| `keep_disabled` | Directed keep flag off (fail closed; no mutate) |
| `keep_unavailable` | Traversal registry / tray ports missing |
| `invalid_args` | Bad mode, types, or merge with no ids |

## Flags

Requires effective directed keep (`memory.directed_keep_enabled` or traversal-on
following OQ-A1). Does **not** require an active walk or traversal tools alone when
keep is explicitly enabled.
