# Version archaeology

**Status:** Template + procedure ready (PR3). Fill results from sealed dogfood runs (typically PR5).

**Script:** [`scripts/version_sample.py`](scripts/version_sample.py)  
**Procedure:** [procedures/P08-version-sampling.md](procedures/P08-version-sampling.md)  
**Safety:** **R1** only — never compact / optimize / cleanup_old_versions.

---

## Purpose

Document Lance `_versions` growth and sampled historical row counts for:

| Hypothesis | Question |
|------------|----------|
| **H3** | Does write history grow under `merge_insert`? Is latest full corpus large? |
| **H4** | (Only if H1a+H1b open) Does multi-fragment / version layout thin bare `to_arrow` beyond default limit? |
| **H10** | Is there non-monotonic historical **collapse**? Else residual migrate risk only. |

**Sequencing:** Run after P01 (H1a/H1b). If default-limit is proven, P08 is **optional polish** (KD14) — do not block provisional root-cause on H1a+H1b+H2.

---

## Safe method

1. Prefer quarantine URI from `quarantine_copy.sh`.
2. `Table.list_versions` — inventory only.
3. Sample **first / 25% / 50% / 75% / latest**.
4. Row counts via `count_rows` / `to_lance` / optional subprocess `lance.dataset` — **not** bare `to_arrow` as full count.
5. Read-only `checkout` if available and non-mutating.
6. **Deny-list:** `compact_files`, `cleanup_old_versions`, `optimize`, `delete`, `drop_table`.

```bash
python docs/lance-debug1/scripts/version_sample.py \
  --uri "$LANCE_DEBUG_URI" \
  --samples 5 \
  --out docs/lance-debug1/evidence/YYYY-MM-DD-run-01/version-sample.json
```

---

## Results template (fill per sealed run)

| Field | Value |
|-------|--------|
| Run id | `YYYY-MM-DD-run-NN` |
| URI | quarantine preferred |
| Packages | lancedb / lance / pyarrow / Python |
| `n_versions` | |
| Latest `num_rows` (full path) | |
| Sample row counts (first→latest) | |
| Non-monotonic collapses | none / list |
| `possibly_torn` quarantine? | |

### Sample table

| Index | Fraction | version_id | num_rows | path |
|-------|----------|------------|----------|------|
| 0 | 0.00 | | | |
| … | 0.25 | | | |
| … | 0.50 | | | |
| … | 0.75 | | | |
| n-1 | 1.00 | | | |

### Interpretation checklist

- [ ] H3: large version history + large latest full count
- [ ] H4: demoted if H1a+H1b hold (default-limit explains bare thinness)
- [ ] H10 historical: collapse present? (yes → document; no → unsupported for history)
- [ ] H10 residual: migrate sites still bare `to_arrow` (always note)

---

## H10 residual framing (normative)

**Active process-thin bug** (full disk APIs large, process ≈ 10 after restart) is explained by **H1 → H2** (bare `to_arrow` default limit in `_load`), not by H10.

**H10 residual** means:

1. **Historical collapse** — only if version samples show non-monotonic row-count drops (past rewrite / partial promote). If latest full APIs already show a large corpus, H10 is **not** the active thin-process mechanism.
2. **Future risk** — product call sites still use bare `to_arrow` for full-table materialization outside `_load`:
   - `_migrate_vector_schema` (Phase 1→2 recreate+copy)
   - `_promote_staging_table` (staging → atoms)
3. A later fix that only patches `_load` must still treat migrate/promote as residual rewrite risk until those sites use an explicit full-scan API (`head(n)`, `limit(n_full)`, `to_lance`, …).

See [TO-ARROW-CALLERS.md](TO-ARROW-CALLERS.md) and [HYPOTHESES.md](HYPOTHESES.md) H10.

**lance-debug1 does not patch these sites.** Residual is documentation for the dossier and a later fix design.

---

## Dogfood notes (snapshot-relative)

Design-era observation (not a pass constant): `_versions` manifests often **1000+** under live merge_insert growth. Absolute version counts and row totals are **run-specific** — record relatives (`latest full ≫ process`, monotonic vs collapse) rather than hardcoding 1607/386.

---

## Evidence index

| Run | Evidence file | H3 | H10 historical | Notes |
|-----|---------------|----|----------------|-------|
| _(pending)_ | `evidence/…/version-sample.json` | | | |

---

## Related

- [procedures/P08-version-sampling.md](procedures/P08-version-sampling.md)
- [HYPOTHESES.md](HYPOTHESES.md)
- [SAFETY.md](SAFETY.md)
- [API-COMPARISON.md](API-COMPARISON.md)
