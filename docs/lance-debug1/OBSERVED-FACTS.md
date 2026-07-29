# Observed facts (snapshot-labeled)

> **Snapshot labeling:** Absolute counts below are **dogfood snapshots at stated times**, not pass/fail constants. Every evidence run re-measures into `evidence/.../meta.json` and compares **relative** relations (`to_arrow` ≈ 10 default limit; full APIs ≫ that; process ≈ thin set).

**Package status:** Facts frozen from design (2026-07-29). Update only by appending dated re-measures; do not rewrite historical snapshot numbers.

**Primary mechanism candidate (to confirm in P01):** under **lancedb 0.20.0** / **lance 0.23.2**, bare `Table.to_arrow()` materializes only a **default-limit query of 10 rows** (async path: `to_arrow` → `query().to_arrow()` with query-builder default limit 10) — not mysterious fragment truncation. `LanceMemoryStore._load()` uses bare `to_arrow().to_pylist()` — i.e. full-table load via a limited query API.

**Note:** sync `LanceTable` in 0.20.0 has **no** public `.query()` (`hasattr(table, "query") is False`); do not write probes that assume `table.query().limit(...)`.

---

## 1. Disk vs process discrepancy

### Snapshot A (design-time dogfood, earlier 2026-07-29)

- Full-table views ~**361** atoms (304 dated 2026-07-29; kinds tool/speak/obs/ledger/model/summary; ~**327** `embedding_status=ready`).

### Snapshot B (reviewer re-measure, 2026-07-29 later)

| Measure | Value (snapshot B) |
|---------|---------------------|
| `count_rows` | **386** |
| `head(10000)` | **386** |
| bare `to_arrow().num_rows` | **10** |
| Full recovery | via public sync `head(n)` / `to_lance().to_table()` (and, where available, inner async `query().limit(n)`) |
| `_versions` manifests | **1607** |
| Sync `LanceTable` public `.query()` | **absent** |

- `Table.count_rows()` reports full cardinality while bare `to_arrow()` returns **10** — **limited-query full-scan misuse** (primary H1 mechanism), not “disk lost data.”
- The 10 rows match the **prefix** of `head(20)`.
- Live `to_arrow` kind mix on snapshot B: **summary×6 + tool×4** (table **order prefix** / first 10 of `head`), **not** a haiku-only selection.
- Haiku dominance in glass/meal/traverse is a **consumer ordering / residual** effect on the thin `_by_id` set (and BUG-wake-02 adjacency), separate from which 10 rows `to_arrow` returns.

### Relative relations (use these in pass/fail)

| Relation | Expected if H1 holds |
|----------|----------------------|
| `n_arrow ≪ n_full` | typically `n_arrow == 10` |
| `n_full ≈ n_head` (when head uses full cardinality) | full APIs agree |
| process `atom_count` ≈ `n_arrow` after restart | H2 cascade |
| Absolute N (361 / 386 / …) | **run-specific**; re-measure |

---

## 2. LanceMemoryStore load path

- `_load()` uses `to_arrow().to_pylist()` exclusively (inherits default limit unless proven otherwise). See [CODE-PATH-MAP.md](CODE-PATH-MAP.md) / [TO-ARROW-CALLERS.md](TO-ARROW-CALLERS.md).
- After restart (dogfood): glass `atom_count` ~13; Atoms tab ~4 haiku tool atoms (newest-first among loaded); Context meal = episodic ladder + haiku prior-moment; Vectors `vectors_ready: 4`, ANN not built (`below_ivf_min`), meal semantic `no_index`.

---

## 3. Promote still works in a live process

- Live `put_atom` upserts to disk; moment tapes (France riots, graphing tool, continuity, …) left hundreds of atoms on disk.
- After restart those atoms remain on disk but are **absent from `_by_id`** if load only materializes the default-limit prefix (plus any rows put after open in that process).

---

## 4. Directed traversal dogfood (non-visual impact)

- Moment `4fb55533…`: `memory_traverse_start` → only **6 temporal** seeds (current speak/obs + haiku tools); `expand_ms_budget=80` but `expand_ms_spent_last` ~94s / ~45s with `expand_truncated=true`; finish kept 4 haiku browser tools.
- Model correctly chose haiku among the only candidates it saw (selection is rational on a truncated universe).
- Graph sticky `last_session` cleared on moment close (by design) — secondary.

---

## 5. Related but distinct

| Item | Role |
|------|------|
| **BUG-wake-02** | Post-restart `wait_timeout` steers into haiku from residual glass/sandbox/meal (consumer of thin memory + other residue) |
| **BUG-mem-gpu-01** | ROCm/GPU embed path (adjacent performance; inflates expand_ms) |
| Semantic expand budget vs CPU Nemotron latency | Adjacent; not root of missing atoms |

See `docs/known-bugs.md` for wake-02 / gpu-01; do not re-home them into this package.

---

## 6. Pain points (why investigation matters)

| Pain | Why it matters |
|------|----------------|
| Glass under-reports corpus size | Operator cannot trust Memory overview after restart (`atom_count` = process only) |
| Semantic / ANN starved | `vectors_ready` ≪ disk ready; `below_ivf_min` forever on thin loaded set |
| Directed traversal myopic | Temporal seeds + graph edges only over loaded atoms |
| Promote weave fractures across restart | Live process links against live `_by_id`; post-restart tails/links only among survivors |
| Migration/recovery also use `to_arrow` | `_migrate_vector_schema`, `_promote_staging_table` can inherit **same default limit** on full-table reads (future rewrite risk) |
| Version growth | Dogfood `atoms.lance/_versions` ~**1600+** manifests (snapshot B: **1607**) — write amplification; demote as root until default-limit H1a/H1b are resolved |

---

## 7. Package / dep snapshot (design-time)

| Item | Value |
|------|--------|
| `pyproject.toml` extra | `lancedb>=0.20,<0.21`, `pyarrow>=14` |
| Dogfood packages | **lancedb 0.20.0**, **lance 0.23.2** |
| Table name | `atoms` under `data/memory/lance/` |

Re-record host, git sha, and package versions in every `evidence/.../meta.json`.

---

## Append-only re-measures

| Date | Run | `n_full` | `n_arrow` | H1a | H1b path | process `atom_count` | Notes |
|------|-----|----------|-----------|-----|----------|----------------------|-------|
| 2026-07-29 | design Snapshot B | 386 | 10 | (expected true) | head / to_lance | ~10–13 after restart | Pre-package; not evidence dir |
| 2026-07-29 | **`2026-07-29-run-01`** | **386** | **10** | **true** | **`head_n_full`** | **10** | Quarantine of live operator memory; H2/H5 sealed; n_versions 1607→1611 post-W1; R2 glass down; see `evidence/2026-07-29-run-01/` |
