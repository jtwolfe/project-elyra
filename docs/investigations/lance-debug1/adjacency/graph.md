# Adjacency — Graph / traverse (bucket E)

| Field | Value |
|-------|--------|
| **Bucket** | E |
| **Procedures** | [P06](../procedures/P06-graph-traverse-meal.md) |
| **Hypotheses** | **H8** (disconfirm independent non-haiku filter), **H11** (expand_ms adj.) |
| **Script** | optional [`../scripts/consumer_compare.py`](../scripts/consumer_compare.py) |
| **Default expectation** | E is **downstream of B** unless consumer bypasses store incorrectly |

## Cascade

| Consumer | Reads |
|----------|-------|
| `GraphView.neighbors` | `store.get_atom`, `list_by_moment`, walks — **process only** |
| `GraphView.seed_from_text` | embedding index seeded from process ready vectors |
| Traverse temporal seeds | `moment_tail` / `global_tail` / store lists |
| Glass graph tab | inspect helpers over same store/session |

Structural edges whose `prev`/`next`/`parent` ids are **not** in `_by_id` simply disappear from expand (`get_atom` → `None`).

## Thin vs full comparison

| Corpus | Typical seeds / neighbors |
|--------|---------------------------|
| Thin (bare `to_arrow` set) | few temporal seeds; structural neighbors only among loaded |
| Full (`head(n_full)` / to_lance) | full weave + non-haiku atoms appear |

**H8 disconfirm:** non-haiku atoms appear under full ephemeral store; thin store does not hide them via an extra filter — it simply never loaded them.

## Split order (do not conflate)

| Layer | What operator sees |
|-------|--------------------|
| Raw `to_arrow` kinds | table-order **prefix** (e.g. summary+tool) |
| Traverse keep set | model selection among **loaded** candidates (may be haiku tools) |
| expand_ms overrun | **H11** / BUG-mem-gpu-01 — not “truncation causes 94s” as row loss |

Dogfood (OBSERVED-FACTS): moment `4fb55533…` — 6 temporal seeds; expand budget 80ms vs spent ~tens of seconds; keep set haiku tools. Selection is rational on a truncated universe.

## Glass graph endpoints (R2)

```bash
BASE="${ELYRA_GLASS_BASE:-http://127.0.0.1:8787}"
curl -sS "$BASE/api/memory/graph"
curl -sS "$BASE/api/memory/graph/session"
curl -sS "$BASE/api/memory/graph/neighbors?atom_id=ATOM_ID"
# POST traverse is operator debug — prefer read-only for inspection
```

## Non-goals

- Patching `_load` to feed GraphView a full corpus
- Replacing GraphView projection weights
- Treating wake/haiku preference as Lance root (see meal.md / H12)
