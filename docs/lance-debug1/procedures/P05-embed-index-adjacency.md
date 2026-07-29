# P05 — Embed / index adjacency

| Field | Value |
|-------|--------|
| **Status** | Stub (filled in PR4) |
| **Safety class** | R1 / R2 |
| **Prove / disprove** | H6, H11 secondary; bucket D |
| **Evidence** | adjacency notes + health fields |

## Purpose

Show that thin `vectors_ready` / `below_ivf_min` are numerically explained by load truncation (B→D), not independent vector loss (H6).

## Procedure (summary)

1. Full materialization: count ready on disk.
2. Process health: `vectors_ready`, channels, index notes.
3. `list_ready_embeddings_for_seed` length ≤ process ready.
4. Document encode queue depth if exposed; do not require GPU.
