# Evidence run 2026-07-29-run-01

## Meta

| Field | Value |
|-------|--------|
| **Run id** | 2026-07-29-run-01 |
| **UTC start** | 2026-07-29T09:38:25Z |
| **Operator** | lance-debug1 PR5 dogfood seal |
| **Host** | LuxPrimata |
| **Git sha** | d57a200 (PR4 tip on PR5 branch) |
| **Branch** | execute-plan/60b09de2-pr-5-dogfood-evidence-bug-dossier |
| **Python** | 3.12.8 (`/home/jim/Workspace/project-elyra/.venv/bin/python3.12`) |
| **lancedb** | 0.20.0 |
| **lance** | 0.23.2 |
| **pyarrow** | 25.0.0 |
| **Data source** | quarantine of live `/home/jim/Workspace/project-elyra/data/memory` |
| **`LANCE_DEBUG_DATA_DIR`** | `/tmp/lance-q-20260729/data` |
| **`LANCE_DEBUG_URI`** | `/tmp/lance-q-20260729/data/memory/lance` |
| **Marker path** | `/tmp/lance-q-20260729/.lance-debug1-quarantine` present Y |
| **Writer idle at copy?** | Y (no high-confidence elyra writer PID; glass down) |
| **`possibly_torn`** | N |
| **Safety classes used** | R0 / R1 / W1 (R2 glass unavailable) |

## Commands run

```bash
export PY=/home/jim/Workspace/project-elyra/.venv/bin/python3.12
export PYTHONPATH=.
export SRC=/home/jim/Workspace/project-elyra/data/memory
export QROOT=/tmp/lance-q-20260729
export RUN=docs/lance-debug1/evidence/2026-07-29-run-01
mkdir -p "$RUN"

./docs/lance-debug1/scripts/quarantine_copy.sh "$SRC" "$QROOT"
export LANCE_DEBUG_DATA_DIR=$QROOT/data
export LANCE_DEBUG_URI=$LANCE_DEBUG_DATA_DIR/memory/lance

$PY docs/lance-debug1/scripts/env_check.py --json > "$RUN/env-check.json"
$PY docs/lance-debug1/scripts/api_matrix.py --uri "$LANCE_DEBUG_URI" --out "$RUN/api-matrix.json"
$PY docs/lance-debug1/scripts/load_parity.py \
  --data-dir "$LANCE_DEBUG_DATA_DIR" \
  --api-matrix "$RUN/api-matrix.json" \
  --out "$RUN/load-parity.json"
$PY docs/lance-debug1/scripts/version_sample.py \
  --uri "$LANCE_DEBUG_URI" --samples 5 --out "$RUN/version-sample.json"
$PY docs/lance-debug1/scripts/consumer_compare.py \
  --uri "$LANCE_DEBUG_URI" --weave-report --out "$RUN/consumer-compare.json"
```

## Relative results

| Measure | Value |
|---------|-------|
| `n_full` (`count_rows`) | **386** |
| `n_head` | **386** |
| `n_arrow` (bare `to_arrow`) | **10** |
| H1a prefix equality | **true** (order-sensitive) |
| H1b `{ok, path}` | **true / `head_n_full`** (private_async also 386; public query absent) |
| `n_lance` | **386** |
| `n_versions` | 1607 (api_matrix) → 1611 (post W1 open joint-repair) |
| process `atom_count` | **10** |
| process `vectors_ready` | **4** |
| skip-corrupt log count | **0** |
| glass R2 | **not available** (no process on :8787 etc.) |

### Kind histograms

| Path | kinds |
|------|-------|
| bare `to_arrow` | summary×6 + tool×4 (table-order prefix) |
| full (`head` / `to_lance`) | tool 220, speak 46, summary 36, observation 36, ledger 35, model 13 |

## Hypothesis updates

| ID | Status change | Confidence | Notes |
|----|---------------|------------|-------|
| H1 | → `supported` | high | n_arrow=10 ≪ n_full=386 |
| H1a | → `supported` | high | arrow_ids == head(10) order |
| H1b | → `supported` | high | path=head_n_full; private_async also full |
| H2 | → `supported` | high | process=10 == n_arrow; id set equal |
| H3 | → `supported` | high | 1611 versions; latest full 386; monotonic growth |
| H4 | → `demoted` | high | H1a+H1b hold; default-limit explains thinness |
| H5 | → `refuted` | high | skip_corrupt=0 ≪ gap 376 |
| H6 | → primary disconfirmed | medium | vectors_ready=4 on thin set; no independent hole probe beyond cascade |
| H7 | → primary disconfirmed | medium (R0/R2 hist) | glass reports process; R2 not live this run |
| H8 | → primary disconfirmed | high | consumer_compare: full corpus surfaces kinds/neighbors thin lacks |
| H9 | → cascade supported | high | 4 edges one-endpoint outside thin; 682 both outside |
| H10 | → residual only | high | no historical collapse; migrate sites still bare to_arrow |
| H11 | → partial (hist) | low this run | OBSERVED-FACTS expand_ms; not re-measured |
| H12 | → partial (hist) | low this run | known-bugs wake-02 consumer; not re-measured |

## Buckets tagged

- **Primary:** A (default-limit bare `to_arrow`) → B (`_load` thin `_by_id`)
- **Secondary cascade:** D, E, F, G
- **Healthy:** C (write path / version growth)
- **Adjacent (not root):** H11 BUG-mem-gpu-01; H12 BUG-wake-02

## Artifacts

- [x] `meta.json`
- [x] `counts-ids.json`
- [x] `api-matrix.json`
- [x] `load-parity.json`
- [x] `version-sample.json`
- [x] `consumer-compare.json`
- [x] `env-check.json`
- [ ] `glass-snapshots/` (R2 unavailable)
- [x] this `notes.md`

## Privacy

Counts / atom_ids / kinds / timestamps only. No full atom bodies committed.

## Notes on W1 side-effects

`load_parity.py` opens `LanceMemoryStore` on quarantine (marker required). Health reported `joint_repair_last_batch=4`, which advanced version inventory from **1607 → 1611**. That is expected W1 behavior on quarantine only; live operator data was not opened. Subsequent `consumer_compare` may see slightly different table-order prefix after those writes; **H1a still holds per-probe** (arrow == head(10) at each measurement).
