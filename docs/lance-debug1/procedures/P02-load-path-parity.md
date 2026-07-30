# P02 — Load path parity

| Field | Value |
|-------|--------|
| **Status** | Ready (PR3) |
| **Safety class** | **W1** (store open may write meta + joint repair) |
| **Prove / disprove** | H2, H5, bucket B |
| **Script** | [`docs/lance-debug1/scripts/load_parity.py`](../scripts/load_parity.py) |
| **Evidence** | `evidence/YYYY-MM-DD-run-NN/load-parity.json` |
| **Depends on** | P01 preferred (`api_matrix.json`); quarantine marker (KD15) |

## Purpose

Open `LanceMemoryStore` against a **marked quarantine** memory root and compare process health / `_by_id` to offline `n_arrow` / `n_full`. Prove that the thin process world is **inherited load** (H2), not mass corrupt-row skip (H5).

## Prerequisites

- [ ] Read [../SAFETY.md](../SAFETY.md) (W1, KD11, KD15, deny-list)
- [ ] Idle quarantine copy of full memory root (`quarantine_copy.sh`)
- [ ] Marker at `$QUARANTINE_ROOT/.lance-debug1-quarantine` **only**
- [ ] `data_dir` ends in `…/data`; marker = `{data_dir}/../.lance-debug1-quarantine`
- [ ] Prefer prior P01 `api-matrix.json` on the same quarantine URI
- [ ] `PYTHONPATH=.` (or installed package) so `elyra` imports resolve
- [ ] **Never** open live unmarked operator `data/`

## Procedure (executable)

### 0. Quarantine + marker

```bash
# From repo root — prefer Elyra stopped/idle
QROOT=/tmp/lance-q-$(date +%Y%m%d)
./docs/lance-debug1/scripts/quarantine_copy.sh data/memory "$QROOT"

export LANCE_DEBUG_DATA_DIR="$QROOT/data"
export LANCE_DEBUG_URI="$QROOT/data/memory/lance"
# Marker ONLY: $QROOT/.lance-debug1-quarantine
```

### 1. Offline matrix (P01) on the same snapshot

```bash
RUN=docs/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01
mkdir -p "$RUN"

python docs/lance-debug1/scripts/api_matrix.py \
  --uri "$LANCE_DEBUG_URI" \
  --out "$RUN/api-matrix.json"
```

### 2. W1 load parity

```bash
export PYTHONPATH=.

python docs/lance-debug1/scripts/load_parity.py \
  --data-dir "$LANCE_DEBUG_DATA_DIR" \
  --api-matrix "$RUN/api-matrix.json" \
  --out "$RUN/load-parity.json"
```

Design CLI alias:

```bash
python docs/lance-debug1/scripts/load_parity.py \
  --paths-root "$QROOT" \
  --api-matrix "$RUN/api-matrix.json" \
  --out "$RUN/load-parity.json"
```

Optional: `--fail-on-h2-signature` exits 2 when H2 holds (CI fixture smoking gun). Default is exit 0 on successful probe (interpret `h2.ok` in JSON).

### 3. What the script does

| Step | Action | Safety |
|------|--------|--------|
| 1 | Require marker at `{data_dir}/../.lance-debug1-quarantine` only | KD15 |
| 2 | Refuse live workspace `data_dir` (heuristic) | no live W1 |
| 3 | Load optional `api_matrix` + direct R1 `count_rows` / bare `to_arrow` / `head` on quarantine URI | R1 sub-probes |
| 4 | Build `ElyraPaths(data_dir=…)` + `MemorySettings(backend="lance")` | — |
| 5 | Construct `LanceMemoryStore` (not soft fall-back factory) | **W1** |
| 6 | Capture `health()`, `len(_by_id)`, id sample / set hash | process truth |
| 7 | Count `skipping corrupt lance row` log lines | H5 |
| 8 | Compare process vs `n_arrow` vs `n_full` | H2 |

### 4. Marker algorithm (must match SAFETY)

```text
marker = Path(data_dir).resolve().parent / ".lance-debug1-quarantine"
```

Refuse if missing — even with `ELYRA_LANCE_ALLOW_WRITE=1`. Do **not** accept markers at `data/.lance-debug1-quarantine` or `data/memory/.lance-debug1-quarantine`.

## Expected if H2

| Check | Expected |
|-------|----------|
| `process.atom_count` | ≈ `n_arrow` (typically ~10) |
| `n_full` | ≫ process |
| id set | process ids ≈ bare `to_arrow` ids (not full `head` set) |
| `h2.ok` | `true` when process tracks arrow and bare to_arrow is thin vs full |

## Expected if H5 disconfirmed

| Check | Expected |
|-------|----------|
| `skip_corrupt.count` | ≪ gap (`n_full - process`) |
| `h5.disconfirmed` | `true` |

## Forbidden

- Open against live unmarked `data/`
- Dual live connect while writer open (prefer glass R2 + this quarantine W1)
- Deny-list ops: `compact_files`, `cleanup_old_versions`, `optimize`, `delete`, `drop_table`, live migrate
- Product patches to `_load` in this package

## Pass / fail (hypotheses)

| Hypothesis | Pass |
|------------|------|
| **H2** | `atom_count ≈ n_arrow` and `atom_count ≪ n_full` after open on quarantine |
| **H5** | skip log count ≪ gap (mass missing rows not corrupt-skip) |

Absolute `n_full` is **run-specific** — do not hardcode 361/386 as pass constants.

## H10 note (not proven here)

H10 is **residual**: migration / staging promote sites still use bare `to_arrow`. P02 proves **load inheritance** of the thin set. Historical collapse archaeology is P08 (optional). See [../VERSION-ARCHAEOLOGY.md](../VERSION-ARCHAEOLOGY.md).

## Safety reminders

- Class **W1** — quarantine only (KD11, KD15)
- Canonical marker only at `$QUARANTINE_ROOT/.lance-debug1-quarantine`
- No production changes under `elyra/memory/**`

See design §P02 in [../design-inspection-plan.md](../design-inspection-plan.md).
