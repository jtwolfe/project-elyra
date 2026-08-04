# `to_arrow` / related call matrix

**Worktree pins:** grepped 2026-07-29 on branch `execute-plan/60b09de2-pr-1-scaffold-lance-debug1-package`.

Regenerate candidate rows:

```bash
python docs/investigations/lance-debug1/scripts/caller_grep_report.py
# or:
rg -n "to_arrow|count_rows|\.head\(|to_lance|scanner" elyra/memory --type py
```

---

## Product call sites (`elyra/memory/lance_store.py`)

| Location | Line | API | Purpose | Full-table intent? | Risk if H1 (default limit 10) | Notes |
|----------|------|-----|---------|--------------------|-------------------------------|-------|
| `_atoms_table_is_empty` | **383–384** | `count_rows()` | Empty check (preferred) | Yes (cardinality only) | Low — correct API | Prefer this branch |
| `_atoms_table_is_empty` | **388** | bare `to_arrow` | Empty check fallback | Yes (length) | **Medium** — may report non-empty when only prefix loads if `count_rows` missing/fails | `len(self._table.to_arrow()) == 0` |
| `_promote_staging_table` | **452** | bare `to_arrow` | Staging → atoms copy | **Yes** | **High** — may promote only ~10 rows into `atoms` | `rows_rec = staging_tbl.to_arrow()` then `create_table` |
| `_migrate_vector_schema` | **518** | bare `to_arrow().to_pylist()` | Phase 1→2 recreate+copy | **Yes** | **High residual (H10)** — future rewrite from partial rows | Pre-drop snapshot built from thin set if H1 |
| `_load` | **663** | bare `to_arrow().to_pylist()` | Rebuild `_by_id` / emb maps | **Yes** | **Critical — primary restart bug path (H1→H2)** | Sole load materialization |
| `search_vectors` | **1471** | `builder.limit(fetch_k)` | Search fetch | No (bounded search) | Low for full-load bug | Intentional limit |
| `search_vectors` | **1478–1481** | `to_list` / `to_arrow` / `to_pandas` | Materialize search hits | No | Low | Result builder, not full table |

### Not full-table bare `to_arrow`

| Location | Line | API | Notes |
|----------|------|-----|-------|
| `_upsert_row` | **841–849** | `merge_insert(...)` | Write path; no full scan |
| `put_atom` | **973** | → `_upsert_row` | Live promote works (H3) |
| `delete` / row delete helpers | (nearby) | delete/filter | Deny-list for inspection scripts on live |
| `health` | **1813** | `len(self._by_id)` | Process truth only — no disk scan |

---

## Factory / open (no `to_arrow`, but load entry)

| Location | Line | API | Role |
|----------|------|-----|------|
| `open_memory_store` | `store.py` **129–156** | constructs `LanceMemoryStore` | Soft fall-back to jsonl |
| `LanceMemoryStore.__init__` | **212–214** | `_ensure_layout` → `_open_db` → **`_load`** | W1 open |
| `worker._ensure_memory_store` | `worker.py` **1160–1180** | `open_memory_store` | Single open per process |

---

## Preferred full-read probe order (scripts — not product)

Used by P01 / `api_matrix.py` (PR2). **Do not require public `table.query()`.**

| Priority | API | Record |
|----------|-----|--------|
| 1 | `table.count_rows()` | `n_full` |
| 2 | `table.head(n_full)` if feasible else `head(10000)` | `n_head`, first 20 atom_ids |
| 3 | `table.head(10)` | `prefix_10` atom_ids |
| 4 | bare `table.to_arrow()` | `n_arrow`, `arrow_ids` |
| 5 | H1a: `arrow_ids == head(10)` order-sensitive | bool |
| 6 | H1b fallback chain | `h1b_path` |
| 7 | `table.to_lance().count_rows()` / `to_table().num_rows` | `n_lance` |
| 8 | `table.list_versions()` length / sample | `n_versions` (R1 only) |
| 9 | Optional subprocess `lance.dataset` | if to_lance insufficient |
| 10 | `scanner` | **only if** `hasattr(table, "scanner")` — not on sync LanceTable 0.20.0 |

### H1b fallback chain

| Step | Probe | Success |
|------|-------|---------|
| H1b-1 | public `table.query().limit(n_full).to_arrow()` if present | `num_rows == n_full` |
| H1b-2 | optional private async query (try/except) | discovery only |
| H1b-3 | **`table.head(n_full)`** while bare `to_arrow` thin | primary on 0.20.0 |
| H1b-4 | `to_lance().to_table()` / `count_rows` | corroboration |

---

## Consumer order vs raw `to_arrow` prefix (do not confuse)

| Source | Kind / order expectation |
|--------|---------------------------|
| Bare `to_arrow` / `head(10)` | **Table order prefix** (snapshot B: summary×6 + tool×4) |
| Glass Atoms tab / newest-first lists | Consumer order among thin `_by_id` — may show haiku tools |
| Meal / traverse seeds | Process maps + residual — not proof of haiku-selected `to_arrow` |

H1a fails if probes assume haiku-only `to_arrow` kinds.

---

## Grep inventory (this worktree)

```
elyra/memory/lance_store.py:388:  return len(self._table.to_arrow()) == 0
elyra/memory/lance_store.py:384:  return int(self._table.count_rows()) == 0
elyra/memory/lance_store.py:452:  rows_rec = staging_tbl.to_arrow()
elyra/memory/lance_store.py:518:  rows = self._table.to_arrow().to_pylist()
elyra/memory/lance_store.py:663:  rows = self._table.to_arrow().to_pylist()
elyra/memory/lance_store.py:1480: elif hasattr(builder, "to_arrow"):
elyra/memory/lance_store.py:1481:     rows = builder.to_arrow().to_pylist()
```

No other `to_arrow` call sites under `elyra/memory/` at pin time (jsonl/embed `_load` are unrelated file loaders).
