---
name: memory_traverse_start
description: Start a temporary multi-hop memory walk. Seeds from explicit ids, multimodal semantic query, and dual/temporal anchors. Fail-closed when directed_traversal_enabled is false.
kind: read
---

# memory_traverse_start

Open a **temporary** directed-traversal session (Phase 2a). Prefer skill
`memory-traverse` for when/how to walk; this tool is the thin start entry.

- Required: `goal` — short walk goal string
- Optional: `seed_query` — semantic seed text (defaults to `goal` in auto/semantic_only)
- Optional: `seed_atom_ids` — durable atom ids to seed (validated; free of expand_ms)
- Optional: `seed_media_ids` — media attachment ids for multimodal semantic seed
- Optional: `seed_mode` — `auto` (default) | `semantic_only` | `temporal_only` | `explicit_only`
- Optional: `budgets` — `{max_steps, max_nodes, max_depth, max_keep, frontier_max, max_expand_per_step, neighbor_k}` (clamped to HARD_MAX; may raise above product defaults)
- Optional: `include_noisy_kinds` — default false; when true, tool/ledger/model appear in `local_map` ring (otherwise sequential bridges only)

## Seed modes

| Mode | Semantic | Temporal |
|------|----------|----------|
| `auto` (default) | try (room after dual reserve) | dual anchors if semantic hits; strip fill if empty |
| `semantic_only` | try (text and/or media) | **never** — empty frontier OK |
| `temporal_only` | skip | strip fill |
| `explicit_only` | skip | skip |

**Nudge:** use `semantic_only` when you already know the focused topic; keep
`auto` for open-ended digs that benefit from recent temporal anchors.

Start counts as step 0. Seeds get frontier **labels** (≤80) and **previews** (≤400).
Semantic seed may surface `encoder_cold` / `no_index` / `expand_truncated` without failing.
Start never cold-loads the encoder.

## Result

Thin decision surface: `session_id`, `status`, `budget`, `frontier`, `keep_set`,
`seed_ids`, `seed_reasons`, `seed_sources`, `seed_mode`, `dual_n`,
`semantic_reason`, `start_ms_budget`, `start_ms_spent`, `considered_count`,
`expand_truncated`, **`local_map`** (host ~d2.5 map for primary seed; may be
null), **`local_maps`** (null on start).

Read `local_map` before blind expand: filtered edges/weights, ring of primary
nodes (prefer speak/observation/summary), and compass (sequential, moment
peers, ladder, associative). Noisy kinds (tool/ledger/model) are omitted from
the ring by default; sequential bridges keep a short label (`tool:name`, `ok`).

## Errors (`ok: false`)

| `error_reason` | Meaning |
|----------------|---------|
| `traverse_disabled` | `memory.directed_traversal_enabled` is false |
| `traverse_unavailable` | Host did not inject graph_view / traversal ports |
| `invalid_args` | Missing/empty goal or bad types |
