# P01 — Offline API matrix

| Field | Value |
|-------|--------|
| **Status** | Stub (filled in PR2 with `api_matrix.py`) |
| **Safety class** | **R1** |
| **Prove / disprove** | H1, H1a, H1b, bucket A (demote H4 if H1a+H1b hold) |
| **Script** | `docs/lance-debug1/scripts/api_matrix.py` (PR2) |
| **Evidence** | `evidence/YYYY-MM-DD-run-NN/api-matrix.json` |

## Purpose

Compare bare `to_arrow` vs full-read APIs (`count_rows`, `head`, H1b chain, `to_lance`) against a Lance URI (prefer quarantine).

## Prerequisites

- [ ] venv with `elyra[memory-lance]` (lancedb 0.20.x)
- [ ] Quarantine preferred; live URI only if writer idle / accepted
- [ ] Read [../SAFETY.md](../SAFETY.md)

## Procedure (summary — implement in PR2)

Preferred probe order: `count_rows` → `head(n)` → bare `to_arrow` → H1a → **H1b fallback chain** → `to_lance` → optional subprocess native.

Do **not** require public `table.query()` (absent on sync LanceTable 0.20.0).

## Expected if H1 + H1a + H1b supported

- `n_arrow == 10` (or documented default limit)
- `n_full` agrees with full head / to_lance — **do not hardcode** 361/386
- H1a prefix equality true
- H1b ok with recorded `path` (on 0.20.0 typically `head_n_full` and/or `to_lance`)

## Pass / fail

| Check | Pass |
|-------|------|
| H1 | `n_arrow ≪ n_full` |
| H1a | `arrow_ids == head(10)` order-sensitive |
| H1b | any H1b-1…4 while bare thin |

See design §P01 in [../design-inspection-plan.md](../design-inspection-plan.md).
