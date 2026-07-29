# P02 — Load path parity

| Field | Value |
|-------|--------|
| **Status** | Stub (filled in PR3 with `load_parity.py`) |
| **Safety class** | **W1** (store open may write meta + joint repair) |
| **Prove / disprove** | H2, H5, bucket B |
| **Script** | `docs/lance-debug1/scripts/load_parity.py` (PR3) |
| **Evidence** | `evidence/YYYY-MM-DD-run-NN/load-parity.json` |

## Purpose

Open `LanceMemoryStore` against a **marked quarantine** memory root and compare process health / `_by_id` to offline `n_arrow` / `n_full`.

## Prerequisites

- [ ] Idle quarantine copy of full memory root
- [ ] Marker at `$QUARANTINE_ROOT/.lance-debug1-quarantine` only
- [ ] `data_dir` ends in `…/data`; marker = `{data_dir}/../.lance-debug1-quarantine`
- [ ] Read [../SAFETY.md](../SAFETY.md)

## Procedure (summary — implement in PR3)

1. `quarantine_copy.sh` → marker.
2. Build `ElyraPaths(data_dir=$QUARANTINE_ROOT/data)` + `MemorySettings(backend="lance")`.
3. Construct store / `open_memory_store`.
4. Compare `health.atom_count` vs `n_arrow` vs `n_full`; id sets; skip-corrupt log count.

## Expected if H2

`atom_count ≈ n_arrow` (≈ 10); `n_full ≫ process`; skip count ≪ gap.

## Forbidden

- Open against live unmarked `data/`
- Dual live connect while writer open
