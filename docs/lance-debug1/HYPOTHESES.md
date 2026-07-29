# Hypotheses (H1–H12)

Status values: `untested` | `supported` | `refuted` | `partial` | `blocked`.

**Sequencing:** Run **H1a → H1b → H2** first. If H1a+H1b+H2 are high-confidence, draft a **provisional root-cause** statement immediately; demote H4/P08 to optional polish; adjacency (H6–H9, H11–H12) still documents cascade for the dossier but must not block that provisional statement.

**Primary mechanism candidate:** bare `to_arrow()` = **default query limit 10** (confirm with H1a/H1b before weighting H4 fragment theories).

---

## Status board

| ID | Hypothesis | Primary bucket | Priority | Status | Confidence | Evidence |
|----|------------|----------------|----------|--------|------------|----------|
| **H1** | Bare `lancedb.Table.to_arrow()` returns a thin subset (~**default limit 10**) while full-read APIs (`count_rows`, `head(n)`, `to_lance().to_table()`, H1b chain) return full corpus — product uses wrong full-scan API | A | P0 | `untested` | — | P01 |
| **H1a** | Bare `to_arrow` rows equal **`head(10)` prefix** (same atom_ids / order), not a random or haiku-filtered subset | A | P0 (sub) | `untested` | — | P01 |
| **H1b** | Full row count is recoverable without bare `to_arrow`, proving the thin read is a **limit/API choice**, not missing data. Pass if **any** step of the H1b fallback chain yields `num_rows == n_full`. Prefer recording which path worked. On 0.20.0, sync `head(n_full)` is an accepted primary public proof | A | P0 (sub) | `untested` | — | P01 |
| **H2** | `LanceMemoryStore._load` inherits H1 and builds `_by_id` from that subset; `health.atom_count` reflects process only | B | P0 | `untested` | — | P02 |
| **H3** | Live `put_atom` / `merge_insert` correctly appends/updates disk (disk grows; promote works) | C (healthy) | P0 disconfirm | `untested` | — | P04, R3 |
| **H4** | Version growth / multi-fragment layout contributes to bare `to_arrow` thinness **beyond** default limit (e.g. only latest fragment) | A+C | P1 **after** H1a/H1b; demote if both hold | `untested` | — | P01 then P08 |
| **H5** | Row skip/corrupt path in `_load` drops hundreds of rows | B | P1 disconfirm | `untested` | — | P02 |
| **H6** | Embed queue / `upsert_vectors` independently lose vectors for atoms **present in `_by_id`** while disk emb ready | D | P1 disconfirm | `untested` | — | P05 |
| **H7** | Glass serialization further truncates beyond store | F | P1 disconfirm | `untested` | — | P07 |
| **H8** | Graph/traverse/meal have independent filtering that hides non-haiku atoms even when store is full | E | P1 disconfirm | `untested` | — | P06 |
| **H9** | Post-restart promote weave only links among survivors, amplifying haiku skew for subsequent sessions | G | P2 cascade | `untested` | — | P09 |
| **H10** | Migration path (`_migrate_vector_schema` / staging promote using bare `to_arrow`) is a **residual future risk** and only explains **historical** disk collapse if version archaeology shows non-monotonic row-count drop. **Does not** explain today’s full disk vs thin process when full APIs already show large corpus | A+B+C | P1 residual / historical | `untested` | — | P08 |
| **H11** | Expand_ms overruns are primarily BUG-mem-gpu-01 / CPU Nemotron, not load truncation | D (adj.) | P2 separate | `untested` | — | P05/P06 |
| **H12** | BUG-wake-02 is a consumer of residual glass + thin meal, not the cause of missing Lance rows | E/F adj. | P2 separate | `untested` | — | adjacency |

---

## Detail cards

### H1 — Default-limit / wrong full-scan API

- **Claim:** Bare `to_arrow()` is a limited query (default 10), not a full table scan.
- **Prove with:** `n_arrow ≪ n_full` (typically `n_arrow == 10`); full APIs large.
- **Refutes:** “disk lost data,” mysterious fragment tip as sole explanation (pending H1a).
- **Procedure:** P01.

### H1a — Prefix equality (not haiku filter)

- **Claim:** `arrow_ids` order-equal `head(10)` atom_ids.
- **Prove with:** order-sensitive id compare; kind hist on bare path = **table-order prefix** (snapshot B: summary×6 + tool×4), not haiku-only.
- **Refutes:** H4 “random/latest fragment only” as sole explanation; “to_arrow selects haiku.”
- **Procedure:** P01 step 5.

### H1b — Full materialize without bare `to_arrow`

- **Claim:** Full count recoverable via public API while bare `to_arrow` stays thin.
- **H1b fallback chain** (stop at first success; record `h1b_path`):

| Step | Probe | Notes |
|------|-------|-------|
| H1b-1 | `getattr(table, "query", None)` then `query().limit(n_full).to_arrow()` | Only if public `query` exists |
| H1b-2 | Optional private/async `table._table.query().limit(n_full).to_arrow()` | Discovery only; not required |
| H1b-3 | **`table.head(n_full).num_rows == n_full`** while bare `to_arrow` thin | **Primary public proof on 0.20.0** |
| H1b-4 | `table.to_lance().to_table().num_rows == n_full` (or `to_lance().count_rows()`) | Corroboration |

- **Overall pass:** any of H1b-1…4 succeeds **while** `n_arrow ≪ n_full`.
- **Do not** treat missing public `table.query()` as H1b failure.
- **Procedure:** P01.

### H2 — Load inherits thin set

- **Claim:** `_load` builds `_by_id` from bare `to_arrow` only; `health.atom_count = len(_by_id)`.
- **Prove with:** process `atom_count` ≈ `n_arrow` ≪ `n_full` after open on quarantine.
- **Procedure:** P02 (**W1**).

### H3 — Write path healthy

- **Claim:** Promote / `merge_insert` correctly grows disk; not “promote never wrote.”
- **Already partially observed** in dogfood; confirm on quarantine sandbox.
- **Procedure:** P04, Recipe R3.

### H4 — Fragment / version layout beyond default limit

- **Elevated only after H1a/H1b tested.** Demote if both hold.
- **Procedure:** P08 (optional polish if H1a+H1b hold).

### H5 — Corrupt-row skip

- **Disconfirm:** skip log count ≪ gap between `n_full` and process count.
- **Procedure:** P02.

### H6–H9, H11–H12 — Cascade / adjacency

- Default expectation: D/E/F/G symptoms are **downstream of B** (thin load) unless disproven.
- H11/H12 stay cross-linked to known-bugs; not Lance root without R1 (H1a/H1b).

### H10 — Migration residual / historical collapse

- Active process-thin bug when full APIs already show large corpus: **not** H10.
- Residual risk: future migrate/reopen using bare `to_arrow`.
- Supported only if P08 shows non-monotonic historical row-count collapse.

---

## Provisional root-cause template (after H1a+H1b+H2)

When evidence supports high confidence:

```text
Primary: Bucket A — bare to_arrow default-limit (~10) misused as full-table load
Direct product impact: Bucket B — LanceMemoryStore._load rebuilds _by_id from thin set
Mechanism: H1 (limit) + H1a (prefix) + H1b (full via head/to_lance) + H2 (load inherits)
Demoted: H4 fragment-only; H10 as explanation of current thin process
Cascade (secondary): D, E, F, G as documented
```

Do **not** implement the fix in this package; record fix **directions** only in `BUG-DOSSIER.md`.
