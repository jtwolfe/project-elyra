# P09 — Promote weave / links

| Field | Value |
|-------|--------|
| **Status** | Stub (filled in PR4) |
| **Safety class** | **R1** |
| **Prove / disprove** | H9; bucket G |

## Purpose

On full disk materialization, count prev/next edges whose endpoints are missing from the thin `to_arrow` set — weave fracture cascade of thin load.

## Procedure (summary)

1. Build graph of prev/next from full rows.
2. Count edges with endpoint missing from thin set.
3. Identify islands; tip-contiguous vs random sample (informs residual H4 if still open).
