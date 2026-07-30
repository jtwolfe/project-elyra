# P07 — Glass serialization

| Field | Value |
|-------|--------|
| **Status** | Ready (PR4) |
| **Safety class** | **R2** |
| **Prove / disprove** | **H7** (disconfirm further truncation beyond store) |
| **Bucket** | F |
| **Evidence** | glass JSON vs process health / P02 load-parity |
| **Adjacency** | [../adjacency/glass.md](../adjacency/glass.md) |

## Purpose

Confirm glass reports **process truth** (`store.health`) and that list hard caps / serialization cannot alone explain thin `atom_count`. Separate **list order** (newest-first haiku tools among `_by_id`) from **raw `to_arrow` table-order prefix** kinds.

## Code touchpoints (inspection only)

| Item | Location |
|------|----------|
| Glass routes | `elyra/runtime/api.py` `_get_memory_*` |
| List helper | `elyra/memory/inspect.py` `list_atoms_for_glass` |
| Hard cap | `_ATOM_LIST_HARD_CAP = 200` (`inspect.py` L40) |
| List order | newest-first via `walk_prev` from `global_tail` (or `t_start` sort) |
| Overview health | `store.health()` → `atom_count = len(_by_id)` |

## Prerequisites

- [ ] Elyra running (R2) **or** W1 load_parity JSON for process truth
- [ ] Prefer just-after-restart capture (align with P03)
- [ ] Optional: same-run api_matrix for `n_arrow` / `n_full`

## Procedure (executable)

### 1. Capture glass payloads

```bash
BASE="${ELYRA_GLASS_BASE:-http://127.0.0.1:8787}"
RUN=docs/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01
mkdir -p "$RUN/glass"

curl -sS "$BASE/api/memory" | tee "$RUN/glass/overview.json"
curl -sS "$BASE/api/memory/atoms?limit=200" | tee "$RUN/glass/atoms.json"
curl -sS "$BASE/api/memory/vectors" | tee "$RUN/glass/vectors.json"
curl -sS "$BASE/api/memory/context" | tee "$RUN/glass/context.json"
```

Full curl matrix: [../adjacency/glass.md](../adjacency/glass.md).

### 2. Compare glass vs process truth

| Check | H7 disconfirm expected |
|-------|-------------------------|
| glass `atom_count` | equals process `health.atom_count` (thin after restart) |
| glass atoms list length | ≤ min(limit, 200) **and** ≤ process count |
| process count ~10–13 | **cannot** be explained by hard cap 200 |
| glass vs `n_full` | glass ≪ `n_full` because process is thin (B), not because glass drops 350 rows |

### 3. Cap cannot be root

| Cap | Value | Why not root of ~10 vs ~386 |
|-----|-------|----------------------------|
| `_ATOM_LIST_HARD_CAP` | 200 | Cap only limits list page size; overview `atom_count` is `len(_by_id)` |
| Snippet / text caps | 240 / 4000 chars | Truncate content fields only |
| Neighbor k defaults | 12 / max 50 | Expand fan-out only |

**H7 supported only if** glass `atom_count` (or equivalent) is **strictly less** than process `health.atom_count` for the same open store without an intervening put race.

### 4. Split list order vs to_arrow prefix

| Source | Ordering | Kind expectation |
|--------|----------|------------------|
| bare `to_arrow` / H1a | table physical order prefix | summary+tool prefix (snapshot B) |
| glass Atoms tab | newest-first among process maps | may surface haiku tools |

Record both in evidence. Misreading glass haiku list as “`to_arrow` selects haiku” **fails H1a** if assumed — keep them separate.

### 5. Context meal snapshot vs recompose

If `/api/memory/context` shows a **stale** last meal vs live compose:

- Document as F nuance (snapshot vs recompose)
- Still not an explanation of disk full vs process thin

## Pass / fail (hypotheses)

| Hypothesis | Pass |
|------------|------|
| **H7 disconfirm** | glass atom_count matches process health; cap 200 cannot explain thin count |
| **H7 support** | glass further reduces count below process health (unexpected; escalate) |

## Forbidden

- Treating glass UI kind skew as proof of H1a failure
- Deny-list / live writes via glass rebuild for this procedure (not required)
- Product serializer patches in this package

See design §P07 and [../EVIDENCE-MATRIX.md](../EVIDENCE-MATRIX.md).
