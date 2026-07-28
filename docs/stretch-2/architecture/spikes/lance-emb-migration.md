# Gate A — Lance emb column migration (lancedb 0.20.x)

| Field | Value |
|-------|--------|
| **Spike** | Gate A (merge gate for PR3) |
| **Pinned dep** | `lancedb>=0.20,<0.21` (`elyra[memory-lance]`) |
| **Measured on** | lancedb **0.20.0** + pyarrow **24.x** (API inspection; connect may segfault on unsupported Python builds — CI/default stays jsonl) |
| **Date** | 2026-07-28 |
| **Shipped in** | `elyra/memory/lance_store.py` (`_migrate_vector_schema`, KD19 preserve, `upsert_vectors`) |

## Goal

Add durable multi-channel embedding columns to existing Phase 1 `atoms` tables without dropping scalar data, and guarantee scalar `put_atom` / `update_links` **never null emb columns** (KD19).

## Physical schema (target)

| Column | Type | Notes |
|--------|------|-------|
| (Phase 1 `_STRING_COLS` + `schema_version`) | utf8 / int64 | unchanged |
| `emb_text` | `fixed_size_list<float32>[2048]` | `lancedb.vector(2048)` |
| `emb_image` | same | |
| `emb_audio` | same | |
| `emb_video` | same | |
| `emb_joint` | same | primary search channel |
| `embed_model` | utf8 nullable | pin / mock id |
| `encoded_at` | utf8 nullable | UTC Z |

`meta.json` (not Lance):

```json
{
  "schema_version": 1,
  "backend": "lance",
  "vector_schema_version": 1,
  "emb_dim": 2048,
  "embed_model": "nvidia/omni-embed-nemotron-3b",
  "created_at": "…",
  "vector_migrated_at": "…"
}
```

Logical `Atom.schema_version` stays **1**. Vector layout epoch is **only** `vector_schema_version`.

## API findings (lancedb 0.20.0)

| API | Signature / behaviour | Suitability |
|-----|----------------------|-------------|
| `lancedb.vector(dim)` | `pa.list_(float32, dim)` fixed-size list | **Use** for emb column types |
| `table.add_columns(transforms: Dict[str, str])` | SQL expressions only (`{"col": "expr"}`) | **Not used** — cannot introduce typed fixed-size list null columns reliably |
| `table.alter_columns` | rename/nullability style changes | Not used for emb add |
| `table.merge_insert(on).when_matched_update_all()` | Full-row replace on match | Phase 1 write path; **nulls omitted emb cols** if not re-supplied |
| `table.update(where=, values=)` | Column-scoped update | Viable for scalar-only patches; we still centralize on merge_insert + merge emb |
| `db.drop_table` + `create_table` | Full recreate | **Chosen migration path** |
| `table.search(query, vector_column_name=…)` | ANN / brute | PR3 uses in-process cosine; ANN index create is PR4 |

## Chosen migration algorithm (open-time)

Implemented in `LanceMemoryStore._migrate_vector_schema`:

1. Connect to `data/memory/lance/`; open or create `atoms`.
2. **New empty table:** create with full scalar + emb schema; set `vector_schema_version=1`.
3. **Existing table:**
   - If all `_EMB_ALL_COLS` present and `meta.json.vector_schema_version >= 1` → continue.
   - Else log once: operator backup recommended (`copy data/memory/lance` before upgrade).
   - Read all rows via `table.to_arrow().to_pylist()`.
   - Write durable JSONL snapshot under `data/memory/lance_migration_bak/atoms-<ts>.jsonl`.
   - Create staging table `atoms__migrating` with target schema + rows, then drop `atoms`, create final `atoms` from the same rows, drop staging (narrows crash window vs drop-first).
   - Write `meta.json`: `vector_schema_version=1`, `emb_dim=2048`, `embed_model`, `vector_migrated_at`.
4. **Fail-closed:** on exception → log; `vector_schema_ok=False`, `vector_error=migration_failed:…`; best-effort reopen `atoms`, else promote staging, else restore from JSONL bak; **scalar** Protocol methods still run when the table remains readable; `open_embedding_index` always returns `LanceEmbeddingIndex` so `health()["ok"]=false` with `error` surfaced (never a healthy Null masking migration failure).
5. No dual-write to JSONL. Switching `backend=jsonl` does not keep vectors.

**Operator restore if both Lance tables and process state are lost:**

1. Prefer a full directory copy of `data/memory/lance` taken before upgrade.
2. Else rehydrate from the newest `data/memory/lance_migration_bak/atoms-*.jsonl` (open store again after placing rows via a one-off restore, or delete broken lance dir and restore bak then re-open — store will rebuild schema from bak on failed-migration restore path when bak path is known).

**Why not side table:** co-row emb columns match design default; in-process `_emb_by_id` is only a merge cache, not a second durable authority.

## KD19 preserve contract (scalar path)

`merge_insert(…).when_matched_update_all()` replaces the whole matched row. A scalar-only row dict **would null** emb columns on every promote `update_links(prev)`.

**Implementation:**

1. Side map `LanceMemoryStore._emb_by_id: atom_id → {emb_*, embed_model, encoded_at}` loaded on open and updated on `upsert_vectors`.
2. Every `_upsert_row` (used by `put_atom` and `update_links`) calls `_attach_emb_columns` to copy the side map into the merge payload (nulls only when no vectors exist).
3. Dedicated `upsert_vectors(atom_id, EmbeddingSet)` writes emb columns + sets `embedding_status=ready` when KD20 is satisfied — without requiring promote to carry vectors.

**Acceptance (PR3 gate):** encode / upsert vectors for atom A → put atom B with `prev=A` + `update_links(A, next=B)` → A still has non-null `emb_joint` and `embedding_status=ready`.

## Search (PR3 minimal)

- Brute-force cosine over `_emb_by_id` with filters (`t_start` range, `moment_id`, `kinds`, excludes).
- No IVF/PQ index create; no recent-buffer hybrid (PR4).
- `EmbeddingIndex.optimize` is a stub returning `optimized=False`.

## Operator notes

- Prefer a Python build with a working `lancedb` wheel before `memory.backend=lance`. Native connect crashes are uncatchable in-process; factory documents this.
- Backup `data/memory/lance` before first Phase 2 open on dogfood data.
- After migration, existing rows stay `embedding_status=none` until encode queue + index upsert.
- Dim pin: **2048** (`EMBED_DIM`); changing dim requires a new `vector_schema_version` and a deliberate rewrite (not automatic).

## Test gates

| Gate | Coverage |
|------|----------|
| Phase 1 table → open Phase 2 store | migration + scalar round-trip |
| `upsert_vectors` → reopen | vectors present |
| encode A → link B as next of A | A vectors preserved |
| migration failure | index `ok=false`; scalar usable when table readable |
| JSONL health | `vectors=false` |
| `MemoryEmbeddingIndex` | CI ready path without Lance |
