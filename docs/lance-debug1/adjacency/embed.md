# Adjacency — Embed / index (bucket D)

| Field | Value |
|-------|--------|
| **Bucket** | D |
| **Procedures** | [P05](../procedures/P05-embed-index-adjacency.md) |
| **Hypotheses** | **H6** (disconfirm independent vector loss), **H11** (expand_ms / GPU adj.) |
| **Default expectation** | D symptoms **numerically explained** by B (thin load) |

## Cascade

All vector **process** APIs read `_emb_by_id` / ready seed lists built only for atoms that survived `_load` into `_by_id`:

| API | Process truth |
|-----|---------------|
| `upsert_vectors` | requires atom already in `_by_id` |
| `list_ready_embeddings_for_seed` | scans `_emb_by_id` ∩ ready — **not disk** |
| `LanceEmbeddingIndex` open seed | process ready only |
| `health()["vectors_ready"]` | count over process emb maps |
| ANN IVF build | skipped when `vectors_ready < ann_ivf_min_vectors` (default **256**) |

```text
disk ready ~ large (full APIs)
  → bare to_arrow loads ~10 rows
  → _emb_by_id only for those rows (+ mid-session upserts)
  → vectors_ready tiny
  → below_ivf_min / no_index
  → meal semantic omit / traverse semantic_reason=no_index
```

## H6 — disconfirm path (procedure check, not product fix)

**Claim to disconfirm:** embed queue / `upsert_vectors` independently lose vectors for atoms **present in `_by_id`** while disk emb is ready.

| Result | Meaning |
|--------|---------|
| Ready holes only for ids **outside** `_by_id` | **H6 disconfirmed** — cascade of B |
| Ready holes for ids **inside** `_by_id` with disk ready | **H6 supported** — independent D bug |

Default dogfood expectation: **disconfirmed**.

## H11 — expand_ms / BUG-mem-gpu-01

| Observation | Interpretation |
|-------------|----------------|
| `expand_ms_spent` ≫ budget | encode latency (CPU Nemotron / ROCm miss) |
| Missing atoms on disk full APIs | **not** explained by H11 |
| Thin seeds + slow expand | both can co-exist; different root classes |

Cross-link: `docs/state/known-bugs.md` **BUG-mem-gpu-01**. Do not re-home GPU fix into lance-debug1.

## Knobs (reference)

From `elyra/memory/config.py` (inspection):

| Knob | Default |
|------|---------|
| `ann_ivf_min_vectors` | 256 |
| `ann_full_search_below` | 2000 |
| `ann_recent_buffer_max` | 256 |

## Evidence fields to capture

- `disk_ready` vs `process.vectors_ready`
- Index notes: `below_ivf_min`, `ann_index_built`, `no_index`
- H6 table: ready-on-disk ∩ `_by_id` missing process emb? (yes/no count)
- Optional: encode queue depth if exposed

## Non-goals

- Rebuilding ANN on live
- Changing encode queue / IVF thresholds
- Product patches under `elyra/memory/embed/**`
