# API comparison

**Status:** Filled from sealed run `2026-07-29-run-01` (PR5 dogfood).

**Scripts:**  
- R1: [`scripts/api_matrix.py`](scripts/api_matrix.py) (P01)  
- W1: [`scripts/load_parity.py`](scripts/load_parity.py) (P02)

**Safety:** R1 probes prefer quarantine; W1 requires marker at `$QUARANTINE_ROOT/.lance-debug1-quarantine` only.

---

## Purpose

Structured comparison of full-read vs default-limit APIs against process load, so H1 / H1a / H1b / H2 can be sealed without hardcoding absolute atom totals.

---

## Preferred probe order (R1 — do not invent alternate order)

| Step | API | Field | Full-table intent? |
|------|-----|-------|--------------------|
| 1 | `table.count_rows()` | `n_full` | Cardinality |
| 2 | `table.head(n_full)` (or capped) | `n_head`, id sample | Yes (bounded) |
| 3 | `table.head(10)` | prefix ids | Prefix |
| 4 | bare `table.to_arrow()` | `n_arrow`, arrow ids | **Misused as full** if H1 |
| 5 | H1a: `arrow_ids == head(10)` | `h1a.ok` | Prefix equality |
| 6 | H1b chain | `h1b.{ok,path,attempts}` | Full via public path |
| 7 | `to_lance().count_rows` / `to_table` | `n_lance` | Corroboration |
| 8 | `list_versions` | `n_versions` | Archaeology (P08) |

H1b chain (stop at first success; public `table.query` **not** required on 0.20.0):

1. public `query().limit(n_full).to_arrow()` if present  
2. optional private async (discovery)  
3. **`head(n_full)`** — primary public proof on lancedb 0.20.0  
4. `to_lance` corroboration  

---

## Results — run `2026-07-29-run-01`

| Field | Value |
|-------|--------|
| Run id | `2026-07-29-run-01` |
| URI / data_dir | `/tmp/lance-q-20260729/data/memory/lance` (quarantine of live operator memory) |
| Packages | lancedb **0.20.0** / lance **0.23.2** / pyarrow **25.0.0** / Python **3.12.8** |
| `possibly_torn` | **false** |

### Offline matrix (P01)

| Metric | Value | Notes |
|--------|-------|-------|
| `n_full` (`count_rows`) | **386** | run-specific |
| `n_head` | **386** | head(n_full) uncapped |
| `n_arrow` (bare `to_arrow`) | **10** | default-limit query |
| `n_lance` | **386** | count_rows + to_table_num_rows |
| H1 (`n_arrow ≪ n_full`) | **true** | 10 ≪ 386 |
| H1a (arrow == head(10) order) | **true** | table order, not haiku-only |
| H1b path | **`head_n_full`** | also private_async → 386 |
| H1b attempts | `query_public_missing`, `private_async`, `head_n_full` | public `query` absent on sync Table |
| arrow kind hist | summary×6 + tool×4 | prefix |
| full kind hist | tool 220, speak 46, summary 36, observation 36, ledger 35, model 13 | |
| `n_versions` (api_matrix) | **1607** | pre-W1 |

### Process load (P02 — W1 quarantine only)

| Metric | Value | Notes |
|--------|-------|-------|
| Marker path | `/tmp/lance-q-20260729/.lance-debug1-quarantine` | present |
| `health.atom_count` | **10** | process truth |
| `len(_by_id)` | **10** | matches atom_count |
| vs `n_arrow` | **equal** (gap 0) | H2: tracks arrow |
| vs `n_full` | **≪** (gap 376) | H2: thin vs full |
| skip-corrupt count | **0** | H5: ≪ gap |
| `h2.ok` | **true** | |
| `h5.disconfirmed` | **true** | |
| `vectors_ready` | **4** | on thin loaded set |
| W1 side-effect | joint_repair_last_batch=4 | versions 1607→1611 on quarantine only |

### Relative equalities (use these, not fixed N)

| Relation | Observed? |
|----------|-----------|
| `n_arrow ≪ n_full` | **yes** (10 ≪ 386) |
| `n_arrow == 10` (or documented default) | **yes** |
| `n_full ≈ n_head` (when head used full) | **yes** (386 == 386) |
| process `atom_count` ≈ `n_arrow` | **yes** (10 == 10) |
| process `atom_count` ≪ `n_full` | **yes** (10 ≪ 386) |
| skip-corrupt ≪ (`n_full` − process) | **yes** (0 ≪ 376) |

---

## Interpretation (sealed)

```text
H1a + H1b + H2 high-confidence:
  Primary: bare to_arrow default-limit misused as full-table load
  Direct impact: LanceMemoryStore._load rebuilds _by_id from thin set
  Demote: H4 fragment-only; H10 as explanation of current thin process
  Residual: H10 migrate/promote still bare to_arrow (see VERSION-ARCHAEOLOGY.md)
```

Do **not** implement `_load` fixes in this package. Directions only in `BUG-DOSSIER.md` (PR5).

---

## Evidence index

| Run | api-matrix.json | load-parity.json | Notes |
|-----|-----------------|------------------|-------|
| `2026-07-29-run-01` | [evidence/2026-07-29-run-01/api-matrix.json](evidence/2026-07-29-run-01/api-matrix.json) | [evidence/2026-07-29-run-01/load-parity.json](evidence/2026-07-29-run-01/load-parity.json) | quarantine; H1/H1a/H1b/H2 sealed |

---

## Related

- [procedures/P01-offline-api-matrix.md](procedures/P01-offline-api-matrix.md)
- [procedures/P02-load-path-parity.md](procedures/P02-load-path-parity.md)
- [REPRO-RECIPES.md](REPRO-RECIPES.md)
- [VERSION-ARCHAEOLOGY.md](VERSION-ARCHAEOLOGY.md)
- [HYPOTHESES.md](HYPOTHESES.md)
- [BUG-DOSSIER.md](BUG-DOSSIER.md)
