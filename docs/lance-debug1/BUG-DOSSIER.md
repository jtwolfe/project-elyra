# BUG — LanceMemoryStore._load uses Table.to_arrow() default limit

**Package:** `docs/lance-debug1`  
**Sealed run:** `2026-07-29-run-01`  
**Status:** Definitive inspection dossier (exit artifact of plan `60b09de2`)  
**Authorization:** Merging this document does **NOT** authorize changes to `elyra/memory/**` or `_load`. Fix directions are non-normative inputs to a later fix design.

---

## Summary

After process restart, `LanceMemoryStore` rebuilds in-memory indexes (`_by_id`, moment/ladder maps, embed maps) from a **thin** row set (~**10** atoms) while the on-disk Lance table holds a **large** corpus (this run: **386** atoms). Glass Memory, vectors, context meal, graph, and directed traversal then operate on that thin process world.

**Root cause:** bare `lancedb.Table.to_arrow()` materializes only a **default-limit query of 10 rows** under lancedb **0.20.0** / lance **0.23.2**. Product `_load` treats that API as a full-table scan:

```python
# elyra/memory/lance_store.py — _load
rows = self._table.to_arrow().to_pylist()
```

Full cardinality and full materialization are available via other public APIs (`count_rows`, `head(n_full)`, `to_lance().to_table()`). Disk is intact; promote/write paths work. The fault is **wrong full-scan API** (bucket A) cascading into process indexes (bucket B).

---

## Environment

| Item | Value (run `2026-07-29-run-01`) |
|------|----------------------------------|
| Host | LuxPrimata |
| Git | `d57a200` on PR5 branch (PR4 tip) |
| Python | **3.12.8** (prefer 3.12; 3.14 native lancedb may segfault) |
| lancedb | **0.20.0** |
| lance | **0.23.2** |
| pyarrow | **25.0.0** |
| Data | Quarantine of live `/home/jim/Workspace/project-elyra/data/memory` → `/tmp/lance-q-20260729` |
| Marker | `$QUARANTINE_ROOT/.lance-debug1-quarantine` present; `possibly_torn=false` |
| Writer at copy | Idle (no high-confidence elyra writer; glass down) |
| Table | `atoms` under `…/memory/lance/` |

---

## Quantitative evidence table

Absolute N is **run-specific**; pass/fail uses **relative** relations.

| Measure | Value | Relation |
|---------|-------|----------|
| `n_full` (`count_rows`) | **386** | full corpus on disk |
| `n_head` (`head(n_full)`) | **386** | full APIs agree |
| `n_arrow` (bare `to_arrow`) | **10** | **≪** n_full (default limit) |
| `n_lance` (`to_lance`) | **386** | corroborates full |
| H1a | **true** | `arrow_ids` order-equal `head(10)` atom_ids |
| H1b | **true** | path=`head_n_full` (public `query` absent; private async also 386) |
| process `atom_count` / `len(_by_id)` | **10** | **==** n_arrow; **≪** n_full |
| process vs arrow id set | equal | H2 tracks thin set |
| skip-corrupt | **0** | **≪** gap 376 → H5 refuted |
| `vectors_ready` (process) | **4** | starved on thin load |
| `n_versions` | 1607 (R1) → 1611 (post W1 open) | large write history |
| version row samples | 10 → 103 → 197 → 320 → 386 | monotonic; **no collapse** |
| weave edges one-endpoint outside thin | **4** | H9 cascade signal |
| weave both endpoints outside thin | **682** / 692 total | most graph outside thin universe |

### Bare `to_arrow` kind mix (not haiku-only)

| Path | Kind histogram |
|------|----------------|
| bare `to_arrow` / head(10) | summary×6 + tool×4 (table-order **prefix**) |
| full corpus | tool 220, speak 46, summary 36, observation 36, ledger 35, model 13 |

Glass newest-first haiku among process maps is a **consumer ordering** effect on the thin set — not “`to_arrow` selects haiku.”

### Measured vs historical

| Claim | This run (measured) | Historical snapshot (OBSERVED-FACTS) |
|-------|---------------------|--------------------------------------|
| n_full / n_arrow | **386 / 10** | Snapshot B: 386 / 10 |
| H1a / H1b | sealed true | expected |
| process atom_count | **10** (W1 quarantine open) | ~10–13 after live restart |
| glass R2 atom_count | **not measured** (glass down) | ~13 post-restart dogfood |
| expand_ms overruns / wake-02 | not re-measured | documented adjacency |

---

## Root cause

```text
Primary:   Bucket A — bare Table.to_arrow() = default query limit ~10
             misused as full-table materialization
Direct:    Bucket B — LanceMemoryStore._load rebuilds _by_id / health
             from that thin set only
Mechanism: H1 (limit) + H1a (prefix equality) + H1b (full via head/to_lance)
             + H2 (load inherits)
Demoted:   H4 fragment-only as sole explanation of bare thinness
           H10 as explanation of *current* thin process
```

### Code path (pins)

| Site | Role | Risk |
|------|------|------|
| `lance_store.py` `_load` ~L663 | `to_arrow().to_pylist()` sole load materialization | **Critical — this bug** |
| `health` | `len(self._by_id)` | process truth only |
| open path | `__init__` → `_load` | restart always thin |

Library note (0.20.0): sync `LanceTable` has **no** public `.query()`; bare `to_arrow` still behaves as default-limit query. Full public proof: `head(n_full)` and/or `to_lance().to_table()`.

---

## Why promote appeared fine

- Live `put_atom` / `merge_insert` **appends/updates disk correctly** (H3 supported).
- Version archaeology: **1611** versions; latest full count **386**; samples **monotonically increasing**.
- Mid-session process has atoms it just wrote in `_by_id`; operators see promote “working.”
- After **restart**, `_load` only reloads the default-limit prefix → hundreds of durable disk rows absent from process indexes.
- Not “promote never wrote”; not “disk lost data.”

---

## Consumer impact (cascade)

All secondary unless independently proven (they were not as primary roots).

| Consumer | Bucket | Effect when B holds |
|----------|--------|---------------------|
| Glass Memory overview | F | `atom_count` = process (~10); Atoms tab newest-first among thin set |
| Vectors / ANN | D | `vectors_ready` tiny (4); ANN may stay `below_ivf_min`; semantic `no_index` |
| Context meal | E | episodic ladder + residual prior-moment over thin universe |
| Graph / traverse | E | temporal seeds + edges only among loaded atoms; myopic keep sets |
| Promote weave post-restart | G | new links attach among survivors; disk edges with missing endpoints (R01: 4 one-side, 682 both-outside) |

`consumer_compare` on R01: full ephemeral store surfaces kinds/neighbors absent from thin → **H8 as independent primary filter refuted**.

---

## Non-causes

| Candidate | Verdict |
|-----------|---------|
| Disk lost data / promote never wrote | **Refuted** — full APIs large; H3 supported |
| Bare `to_arrow` haiku filter | **Refuted** — H1a prefix = summary×6+tool×4 |
| Fragment/version tip sole thinness (H4) | **Demoted** — H1a+H1b hold |
| Corrupt-row skip mass drop (H5) | **Refuted** — skip=0 |
| Glass serialization further truncates (H7 primary) | **Disconfirmed as root** — reports process; list cap 200 not binding at N≈10 |
| Independent graph/meal hide (H8 primary) | **Refuted** — full store surfaces non-thin neighbors/kinds |
| Embed queue independently loses vectors for loaded ids (H6 primary) | **Not supported as root** — gap dominated by unloaded ids |
| Historical migrate collapse (H10 active now) | **Unsupported** — no non-monotonic collapse; latest full large |
| BUG-wake-02 | **Adjacent consumer** of residual glass/meal/sandbox — not Lance row-loss root (H12) |
| BUG-mem-gpu-01 / expand_ms | **Adjacent performance** — not missing-row root (H11) |

---

## H10 residual

**Not** the active process-thin bug (full disk APIs already large).

**Residual future risk:** full-table materialization sites outside `_load` still use bare `to_arrow`:

- `_migrate_vector_schema` — Phase 1→2 recreate+copy
- `_promote_staging_table` — staging → atoms

A later fix that only patches `_load` must still treat migrate/promote as rewrite risk until those sites use an explicit full-scan API. See [TO-ARROW-CALLERS.md](TO-ARROW-CALLERS.md), [VERSION-ARCHAEOLOGY.md](VERSION-ARCHAEOLOGY.md).

---

## Recommended fix directions (non-normative)

> **These are directions for a later fix design only.**  
> **This package must not implement them.**  
> **Merge of this dossier does not authorize `_load` or any `elyra/memory/**` change.**

Preferred directions (any one sufficient for load; combine for residual):

1. **`_load` full materialize via explicit API**
   - `table.head(n)` where `n = table.count_rows()` (or a documented safe cap with paging), **or**
   - `table.to_lance().to_table()` / equivalent full dataset read, **or**
   - if/when public `query()` exists: `query().limit(n_full).to_arrow()` (do not rely on bare `to_arrow`).
2. **Do not** use bare `to_arrow()` as a full-table scan anywhere full intent is required.
3. **Also update residual sites:** `_migrate_vector_schema`, `_promote_staging_table` (and empty-check fallback if it uses bare `to_arrow` length).
4. **Tests:** open store on a fixture/table with **>10** rows; assert `health.atom_count == count_rows` and id-set equality with full materialize path.
5. **Optional ops:** version growth / compaction policy is separate from this correctness bug; do not couple.

Out of scope for the fix PR unless separately justified: changing glass caps, meal policy, traverse budgets, wake-02, or GPU embed (adjacent).

---

## Reproduction

Primary recipe: [REPRO-RECIPES.md](REPRO-RECIPES.md) **R1** (quarantine + `api_matrix` + `load_parity`).

```bash
# Prefer Python 3.12 + project venv with elyra[memory-lance]
export PY=…/python3.12 PYTHONPATH=.
./docs/lance-debug1/scripts/quarantine_copy.sh /path/to/data/memory /tmp/lance-q-YYYYMMDD
export LANCE_DEBUG_DATA_DIR=/tmp/lance-q-YYYYMMDD/data
export LANCE_DEBUG_URI=$LANCE_DEBUG_DATA_DIR/memory/lance
RUN=docs/lance-debug1/evidence/$(date +%Y-%m-%d)-run-NN
mkdir -p "$RUN"

$PY docs/lance-debug1/scripts/api_matrix.py --uri "$LANCE_DEBUG_URI" --out "$RUN/api-matrix.json"
# Expect: n_arrow ≈ 10; n_full large; h1a.ok; h1b.path=head_n_full

$PY docs/lance-debug1/scripts/load_parity.py \
  --data-dir "$LANCE_DEBUG_DATA_DIR" \
  --api-matrix "$RUN/api-matrix.json" \
  --out "$RUN/load-parity.json"
# Expect: process_atom_count ≈ n_arrow ≪ n_full; h2.ok; h5 disconfirmed
```

Hermetic smoke (no operator data): `scripts/fixtures/build_tiny_atoms.py` then `api_matrix` on the fixture URI (validates probe plumbing; dogfood N is operator-specific).

---

## Evidence index

| Artifact | Path |
|----------|------|
| Run meta / notes | [evidence/2026-07-29-run-01/](evidence/2026-07-29-run-01/) |
| counts / ids only | [evidence/2026-07-29-run-01/counts-ids.json](evidence/2026-07-29-run-01/counts-ids.json) |
| P01 api_matrix | [evidence/2026-07-29-run-01/api-matrix.json](evidence/2026-07-29-run-01/api-matrix.json) |
| P02 load_parity | [evidence/2026-07-29-run-01/load-parity.json](evidence/2026-07-29-run-01/load-parity.json) |
| P08 version_sample | [evidence/2026-07-29-run-01/version-sample.json](evidence/2026-07-29-run-01/version-sample.json) |
| P06/P09 consumer_compare | [evidence/2026-07-29-run-01/consumer-compare.json](evidence/2026-07-29-run-01/consumer-compare.json) |
| Hypothesis board | [HYPOTHESES.md](HYPOTHESES.md) |
| API comparison | [API-COMPARISON.md](API-COMPARISON.md) |
| Evidence matrix | [EVIDENCE-MATRIX.md](EVIDENCE-MATRIX.md) |
| Call sites | [TO-ARROW-CALLERS.md](TO-ARROW-CALLERS.md) |
| Safety | [SAFETY.md](SAFETY.md) |
| Design (normative inspection) | [design-inspection-plan.md](design-inspection-plan.md) |

---

## Acceptance (inspection package)

- [x] H1 / H1a / H1b / H2 high-confidence from sealed evidence  
- [x] H4 demoted; H5 refuted; H10 residual documented  
- [x] Cascade D–G documented; H8 primary refuted  
- [x] No product code change under `elyra/memory/**`  
- [x] Evidence bag: counts/ids only (no full atom bodies)  
- [x] **Merge does not authorize `_load` change**
