# P07 — Glass serialization

| Field | Value |
|-------|--------|
| **Status** | Stub (filled in PR4) |
| **Safety class** | **R2** |
| **Prove / disprove** | H7; bucket F |

## Purpose

Confirm glass reports process truth (`store.health`) and list caps (`_ATOM_LIST_HARD_CAP=200`) cannot alone explain thin atom_count.

## Procedure (summary)

Compare glass payload fields to process health. Separate list order (newest-first) from raw `to_arrow` prefix kinds.
