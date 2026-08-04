# P01 — Offline API matrix

| Field | Value |
|-------|--------|
| **Status** | Ready (PR2) |
| **Safety class** | **R1** |
| **Prove / disprove** | H1, H1a, H1b, bucket A (demote H4 if H1a+H1b hold) |
| **Script** | [`docs/investigations/lance-debug1/scripts/api_matrix.py`](../scripts/api_matrix.py) |
| **Supporting** | [`env_check.py`](../scripts/env_check.py), [`quarantine_copy.sh`](../scripts/quarantine_copy.sh) |
| **Evidence** | `evidence/YYYY-MM-DD-run-NN/api-matrix.json` |
| **Recipe** | [REPRO-RECIPES.md](../REPRO-RECIPES.md) R1 |

## Purpose

Compare bare `to_arrow` vs full-read APIs (`count_rows`, `head`, H1b chain, `to_lance`) against a Lance URI (prefer quarantine). Confirm or refute the **default query limit 10** mechanism before elevating fragment/version theories (H4/P08).

## Prerequisites

- [ ] Read [../SAFETY.md](../SAFETY.md) (allowlist / deny-list / quarantine marker)
- [ ] venv with `elyra[memory-lance]` (**lancedb 0.20.x** / lance 0.23.x)
- [ ] Prefer **Python 3.12** if 3.14 native connect segfaults (`env_check.py` records interpreter)
- [ ] Quarantine preferred; live URI only if writer idle / concurrent-read accepted
- [ ] Evidence run directory created under `evidence/`

## Procedure (executable)

### 0. Environment snapshot

```bash
# From repo root
python docs/investigations/lance-debug1/scripts/env_check.py
# or: python docs/investigations/lance-debug1/scripts/env_check.py --json
```

Record: Python version, `lancedb` / `lance` / `pyarrow` versions, `LANCE_DEBUG_*` paths.

### 1. Quarantine full memory root (preferred)

```bash
QROOT=/tmp/lance-q-$(date +%Y%m%d)
# Args: SRC_MEMORY_ROOT  QUARANTINE_ROOT
./docs/investigations/lance-debug1/scripts/quarantine_copy.sh data/memory "$QROOT"

export LANCE_DEBUG_DATA_DIR="$QROOT/data"
export LANCE_DEBUG_URI="$QROOT/data/memory/lance"
# Marker ONLY at: $QROOT/.lance-debug1-quarantine
```

- Prefer Elyra **stopped or idle** (no promote/encode writes). If concurrent, note `possibly_torn` from the marker.
- Do **not** dual-connect live URI while writer is open (default path: glass R2 + this snapshot R1).

### 2. Run API matrix (preferred probe order)

```bash
RUN=docs/investigations/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01
mkdir -p "$RUN"

python docs/investigations/lance-debug1/scripts/api_matrix.py \
  --uri "$LANCE_DEBUG_URI" \
  --table atoms \
  --out "$RUN/api-matrix.json"
```

Optional: `--subprocess-native` if `to_lance` is insufficient (runs `lance.dataset` in a child process).

### 3. What the script records (do not invent alternate order)

| Step | API | Field |
|------|-----|--------|
| 1 | `table.count_rows()` | `n_full` |
| 2 | `table.head(n_full)` if ≤ cap else `head(10000)` | `n_head`, first 20 atom_ids |
| 3 | `table.head(10)` | `prefix_10` |
| 4 | bare `table.to_arrow()` | `n_arrow`, `arrow_ids` |
| 5 | **H1a:** `arrow_ids == head(10)` order-sensitive | `h1a.ok` |
| 6 | **H1b fallback chain** (stop at first success) | `h1b.{ok,path,attempts}` |
| 7 | `table.to_lance().count_rows()` / `to_table().num_rows` | `n_lance` |
| 8 | `table.list_versions()` | `n_versions` |
| 9 | optional subprocess native | only with flag |
| 10 | `scanner` | only if `hasattr`; skip note on 0.20.0 sync Table |

### 4. H1b fallback chain (implemented exactly)

On **lancedb 0.20.0**, sync `LanceTable` has **`hasattr(table, "query") is False`**. Missing public `query` is **not** H1b failure.

| Step | Probe | Success |
|------|-------|---------|
| H1b-1 | public `table.query().limit(n_full).to_arrow()` if present | `num_rows == n_full` |
| H1b-2 | optional private async `table._table.query().limit(n_full).to_arrow()` | discovery only |
| H1b-3 | **`table.head(n_full)`** while bare `to_arrow` thin | **primary on 0.20.0** |
| H1b-4 | `to_lance().to_table()` / `count_rows` | corroboration |

JSON fragment:

```json
"h1b": {
  "ok": true,
  "path": "head_n_full",
  "n_full": 386,
  "n_arrow": 10,
  "attempts": ["query_public_missing", "private_async", "head_n_full"]
}
```

Absolute `n_full` is **run-specific** — do not hardcode 361/386 as pass constants.

### 5. Deny-list (never invoke)

Scripts must not call: `merge_insert`, `add`, `delete`, `drop_table`, `compact_files`, `cleanup_old_versions`, `optimize`. `api_matrix.py` records which deny methods exist on the Table without calling them.

### 6. Interpret & file evidence

1. Copy or leave `api-matrix.json` under the run dir.
2. Optionally fill [../evidence/_template-api-row.md](../evidence/_template-api-row.md) notes.
3. Update [../HYPOTHESES.md](../HYPOTHESES.md) statuses **from this evidence only** when the operator seals a run (typically PR5); provisional notes allowed if H1a+H1b clear.
4. If **H1a+H1b hold**, mark H4 **demoted** unless other evidence re-opens fragment theories.

## Expected if H1 + H1a + H1b supported

- `n_arrow == 10` (or documented default limit)
- `n_full == n_head` (when head used full) and/or `to_lance` full
- H1a prefix equality **true** (table order — **not** haiku-only kinds)
- H1b `ok` with recorded `path` (on 0.20.0 typically `head_n_full` and/or `to_lance`)
- `hist_arrow` matches first 10 of full table order

## Pass / fail

| Check | Pass |
|-------|------|
| H1 | `n_arrow ≪ n_full` (typically `n_arrow == 10`) |
| H1a | `arrow_ids == head(10)` order-sensitive |
| H1b | any of H1b-1…4 succeeds **while** bare `to_arrow` thin |

## Hermetic smoke (no live data)

```bash
python docs/investigations/lance-debug1/scripts/fixtures/build_tiny_atoms.py --out /tmp/tiny-lance
python docs/investigations/lance-debug1/scripts/api_matrix.py --uri /tmp/tiny-lance --out /tmp/api-matrix.json
# expect n_full=25, n_arrow=10, h1a ok, h1b path=head_n_full (0.20.0)
```

Or: `pytest tests/test_lance_debug1_api_matrix_fixture.py` (skips if connect unusable).

## Safety reminders

- Class **R1** — no `LanceMemoryStore` open (that is **W1** / P02).
- Canonical marker only at `$QUARANTINE_ROOT/.lance-debug1-quarantine`.
- No dual live connect as default while writer open.
- No product changes under `elyra/memory/**`.

See design §P01 in [../design-inspection-plan.md](../design-inspection-plan.md).
