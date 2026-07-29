# P04 — Write path sandbox

| Field | Value |
|-------|--------|
| **Status** | Stub (filled in PR3/PR4) |
| **Safety class** | **W1 only** on quarantine |
| **Prove / disprove** | H3, H4 partially, bucket C healthy |
| **Evidence** | before/after api-matrix on quarantine |

## Purpose

Confirm `put_atom` / `merge_insert` grows disk and process maps on a quarantine copy only.

## Forbidden

- Never run against live operator dir
- No compact / optimize / cleanup

## Procedure (summary)

1. Snapshot API matrix.
2. Put one synthetic atom via store (quarantine).
3. Re-run matrix: full paths +1; process `_by_id` +1.
4. Optional: many puts for version growth; check whether bare `to_arrow` ratio changes.
