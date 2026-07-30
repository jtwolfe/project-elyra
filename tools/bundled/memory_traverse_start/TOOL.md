---
name: memory_traverse_start
description: Start a temporary multi-hop memory walk. Seeds from explicit ids, semantic text, and temporal neighbourhood. Fail-closed when directed_traversal_enabled is false.
kind: read
---

# memory_traverse_start

Open a **temporary** directed-traversal session (Phase 2a). Prefer skill
`memory-traverse` for when/how to walk; this tool is the thin start entry.

- Required: `goal` — short walk goal string
- Optional: `seed_query` — semantic seed text (defaults to `goal`)
- Optional: `seed_atom_ids` — durable atom ids to seed (validated; free of expand_ms)
- Optional: `budgets` — `{max_steps, max_nodes, max_depth, max_keep}` (clamped down)

Start counts as step 0. Seeds get frontier **labels** (≤80) and **previews** (≤400).
Semantic seed may surface `encoder_cold` / `no_index` / `expand_truncated` without failing.

## Result

Thin decision surface: `session_id`, `status`, `budget`, `frontier`, `keep_set`,
`seed_reasons`, `considered_count`, `expand_truncated`.

## Errors (`ok: false`)

| `error_reason` | Meaning |
|----------------|---------|
| `traverse_disabled` | `memory.directed_traversal_enabled` is false |
| `traverse_unavailable` | Host did not inject graph_view / traversal ports |
| `invalid_args` | Missing/empty goal or bad types |
