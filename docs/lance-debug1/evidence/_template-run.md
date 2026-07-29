# Evidence run template

Copy to `evidence/YYYY-MM-DD-run-NN/notes.md` (or fill alongside `meta.json`).

---

## Meta

| Field | Value |
|-------|--------|
| **Run id** | YYYY-MM-DD-run-NN |
| **UTC start** | |
| **Operator** | |
| **Host** | |
| **Git sha** | |
| **Branch** | |
| **Python** | |
| **lancedb** | |
| **lance** | |
| **pyarrow** | |
| **Data source** | live / quarantine |
| **`LANCE_DEBUG_DATA_DIR`** | |
| **`LANCE_DEBUG_URI`** | |
| **Marker path** | `$QUARANTINE_ROOT/.lance-debug1-quarantine` present? Y/N |
| **Writer idle at copy?** | Y/N / N/A |
| **`possibly_torn`** | Y/N |
| **Safety classes used** | R0 / R1 / R2 / W1 |

Also write structured `meta.json` with the same fields.

---

## Commands run

```bash
# paste exact commands
```

---

## Relative results (do not treat design 361/386 as pass constants)

| Measure | Value |
|---------|-------|
| `n_full` (`count_rows`) | |
| `n_head` | |
| `n_arrow` (bare `to_arrow`) | |
| H1a prefix equality | true / false |
| H1b `{ok, path}` | |
| `n_lance` | |
| `n_versions` (sample) | |
| process `atom_count` | |
| process `vectors_ready` | |
| skip-corrupt log count | |

---

## Hypothesis updates

| ID | Status change | Confidence | Notes |
|----|---------------|------------|-------|
| H1 | | | |
| H1a | | | |
| H1b | | | |
| H2 | | | |
| … | | | |

---

## Buckets tagged

- Primary:
- Secondary:

---

## Artifacts

- [ ] `meta.json`
- [ ] `api-matrix.json`
- [ ] `load-parity.json` (if W1)
- [ ] `glass-snapshots/` (if R2)
- [ ] `severity.md`
- [ ] raw command logs (no full atom bodies unless necessary)

---

## Privacy

Prefer counts / atom_ids / kinds / timestamps. Avoid committing full atom bodies or user content.
