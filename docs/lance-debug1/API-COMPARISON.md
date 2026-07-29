# API comparison (template)

**Status:** Template ready (PR3). Fill cells from sealed `api-matrix.json` / `load-parity.json` runs (typically PR5).

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

## Results template (fill per sealed run)

| Field | Value |
|-------|--------|
| Run id | `YYYY-MM-DD-run-NN` |
| URI / data_dir | quarantine preferred |
| Packages | lancedb / lance / pyarrow / Python |
| `possibly_torn` | |

### Offline matrix (P01)

| Metric | Value | Notes |
|--------|-------|-------|
| `n_full` (`count_rows`) | | run-specific |
| `n_head` | | |
| `n_arrow` (bare `to_arrow`) | | expect ~10 if H1 |
| `n_lance` | | |
| H1 (`n_arrow ≪ n_full`) | | |
| H1a (arrow == head(10) order) | | table order, not haiku-only |
| H1b path | | e.g. `head_n_full` |
| H1b attempts | | |

### Process load (P02 — W1 quarantine only)

| Metric | Value | Notes |
|--------|-------|-------|
| Marker path | `{data_dir}/../.lance-debug1-quarantine` | required |
| `health.atom_count` | | process truth |
| `len(_by_id)` | | should match atom_count |
| vs `n_arrow` | | H2: ≈ |
| vs `n_full` | | H2: ≪ |
| skip-corrupt count | | H5: ≪ gap |
| `h2.ok` | | |
| `h5.disconfirmed` | | |

### Relative equalities (use these, not fixed N)

| Relation | Observed? |
|----------|-----------|
| `n_arrow ≪ n_full` | |
| `n_arrow == 10` (or documented default) | |
| `n_full ≈ n_head` (when head used full) | |
| process `atom_count` ≈ `n_arrow` | |
| process `atom_count` ≪ `n_full` | |
| skip-corrupt ≪ (`n_full` − process) | |

---

## Interpretation (after fill)

```text
If H1a + H1b + H2:
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
| _(pending)_ | | | |

---

## Related

- [procedures/P01-offline-api-matrix.md](procedures/P01-offline-api-matrix.md)
- [procedures/P02-load-path-parity.md](procedures/P02-load-path-parity.md)
- [REPRO-RECIPES.md](REPRO-RECIPES.md)
- [VERSION-ARCHAEOLOGY.md](VERSION-ARCHAEOLOGY.md)
- [HYPOTHESES.md](HYPOTHESES.md)
