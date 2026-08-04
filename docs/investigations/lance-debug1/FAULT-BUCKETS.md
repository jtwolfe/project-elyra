# Fault buckets A–G

Each finding in evidence runs must tag one or more buckets. Primary root cause may be **A** alone with **B** as the direct product impact path.

**Sequencing:** Disambiguate default query limit (H1a/H1b → bucket A) before elevating version/fragment theories (H4 → A+C / P08).

---

## Bucket A — lancedb client API (incl. wrong API for full scan)

**Scope:** Incomplete full-table materialization via lancedb 0.20.x `Table` methods — primarily **API misuse of a limited query**, secondarily true client defects.

| Signal | Interpretation |
|--------|----------------|
| `count_rows()` ≫ `len(to_arrow())` and `len(to_arrow()) == 10` | **Default query limit** (leading hypothesis) or other partial materialization |
| `to_arrow` row ids/order == `head(10)` **prefix** | Default-limit / table-order prefix — **not** random fragment tip / haiku filter |
| H1b fallback succeeds: full materialize via public API while bare `to_arrow` stays ~10 | Confirms limit/wrong-API throttle; full scan available without bare `to_arrow` |
| `head(N)` / `to_lance().to_table()` full while bare `to_arrow` thin | Method semantics differ; product must not use bare `to_arrow` for full load |
| Source: async `to_arrow` → `query().to_arrow()` default limit 10; sync `head(n)` is limit(n) under the hood | Library contract, not undocumented fragment magic |
| Sync `LanceTable` has no public `.query()` on 0.20.0 | H1b must not depend solely on `table.query()` |
| Only after H1a/H1b fail: no public full materialize path at all | True wrapper defect (secondary branch) |
| Reproducible on quarantine with same package versions | Not process-local memory corruption |

**Code touchpoints (read-only analysis):** all `to_arrow` sites in `lance_store.py` (see [TO-ARROW-CALLERS.md](TO-ARROW-CALLERS.md)). Fix direction for product (dossier only): stop using bare `to_arrow()` for full-table load; use `head(n)` / `limit(n)` / `to_lance().to_table()` with explicit full cardinality.

**Hypotheses:** H1, H1a, H1b (primary); H4/H10 partial.

---

## Bucket B — LanceMemoryStore load / index rebuild

**Scope:** `_load`, secondary indexes, health reporting.

| Signal | Interpretation |
|--------|----------------|
| Disk full, `len(_by_id)` thin after open | Load path materializes subset |
| `health()["atom_count"]` matches thin set | Health is process-truth only (by design today — still a product footgun) |
| `_emb_by_id` only for loaded rows | Vector seed inherits load truncation |
| Corrupt-row skip path does not explain gap | Skip logs would need ~350 warnings |

**Critical code (line pins: this worktree):**

- `_load` L654–679: `rows = self._table.to_arrow().to_pylist()` then rebuild indexes
- `health` L1794–1826: `atom_count = len(self._by_id)`

**Hypotheses:** H2 (primary), H5 (disconfirm).

---

## Bucket C — Write path / versioning / compaction

**Scope:** `merge_insert` upserts, version explosion, fragment layout. **Only elevated after H1a/H1b (default-limit) are tested.**

| Signal | Interpretation |
|--------|----------------|
| ~1 version per put × hundreds of atoms → ~1600+ versions | Expected under merge_insert-per-row; may stress readers / ops |
| Historical version N has non-decreasing row counts | Writes are durable; not silent drop |
| `to_arrow` prefix == `head(10)` **and** H1b chain recovers full | **Demotes** “latest fragment only” / random fragment theories for bare `to_arrow` |
| Only if H1b fails: compaction/fragment layout still needed | Layout/read interaction remains open |
| Quarantine compact-then-read changes behavior | Compaction-related (still client/layout) — **never** compact live operator data |

**Note:** Write path working in live process is **already observed**; bucket C is about whether write *shape* contributes to read behavior, not whether promote fails mid-session. Version archaeology (P08) runs **after** default-limit disambiguation.

**Hypotheses:** H3 (healthy write), H4 (after H1a/H1b), H10 historical.

---

## Bucket D — Embed / index adjacency

**Scope:** Encode queue, `upsert_vectors`, `LanceEmbeddingIndex` seed/optimize, `below_ivf_min`.

| Signal | Interpretation |
|--------|----------------|
| Disk ready large, process `vectors_ready` tiny | Load truncation (B) cascading to D — secondary |
| ANN notes `below_ivf_min:emb_joint:N` with small N | Correct given thin corpus; not independent root |
| Encode queue backlog after restart for missing ids | Atoms not in `_by_id` → cannot upsert_vectors |
| BUG-mem-gpu-01 slow encode | Explains expand_ms overruns, **not** missing atoms |

Default knobs (`elyra/memory/config.py`): `ann_ivf_min_vectors=256`, `ann_full_search_below=2000`, `ann_recent_buffer_max=256`.

**Hypotheses:** H6 (disconfirm independent loss), H11 (adj.).

---

## Bucket E — GraphView / traverse / meal consumers

**Scope:** Behavior when store is thin; expand budgets; semantic omit reasons.

| Signal | Interpretation |
|--------|----------------|
| Temporal seeds only current + haiku tools | Consumers correctly reflect `_by_id` |
| `expand_truncated` + expand_ms_spent ≫ budget | Encoder latency (GPU/CPU) — **distinct** from truncation |
| Meal episodic haiku prior-moment | Meal reads store/ladder over thin set |
| `no_index` semantic | Index/seed empty relative to product expectations |

**Conclusion form:** E is almost always **downstream** of B unless a consumer bypasses store incorrectly (prove with full vs thin corpus comparison).

**Hypotheses:** H8 (disconfirm independent filter), H12 (wake residual).

---

## Bucket F — Glass API serialization

**Scope:** `runtime/api.py` `_get_memory_*`, `inspect.py` list caps.

| Signal | Interpretation |
|--------|----------------|
| Glass `atom_count` matches `store.health()` thin count | Glass honest about process; not an extra truncation |
| List hard caps (`_ATOM_LIST_HARD_CAP=200`) | Cannot alone explain 13 vs 361 |
| Context meal snapshot stale vs recompose | Snapshot vs live compose nuance — document if present |

**Hypotheses:** H7 (disconfirm further truncation).

---

## Bucket G — Promote sequential weave / links

**Scope:** `_link_and_put` using `moment_tail` / `global_tail` over `_by_id`; link integrity after restart.

| Signal | Interpretation |
|--------|----------------|
| Disk has atoms with prev/next pointing outside loaded set | Weave broken in-process after thin load |
| Live promote continues linking among full live `_by_id` | G healthy during session |
| After restart, new promotes attach to thin tail only | Cascading weave fracture (secondary of B) |

**Hypotheses:** H9 (cascade).

---

## Interaction map

```text
A (default-limit to_arrow)
  └─► B (_load thin _by_id / health)
        ├─► D (vectors_ready / ANN starve)
        ├─► E (traverse / meal / graph myopia)
        ├─► F (glass reports process truth)
        └─► G (post-restart weave fracture)
C write healthy (H3) — may still produce version growth; not primary thin-read unless H1a/H1b fail
H4/H10 — residual / after H1a/H1b only
```

| If primary is… | Then secondary usually… | Do not elevate as root until… |
|----------------|-------------------------|-------------------------------|
| A+B | D, E, F, G | disconfirmed as independent |
| A alone (no B) | impossible if product uses bare `to_arrow` in `_load` | — |
| C only | — | H1a+H1b refuted |
