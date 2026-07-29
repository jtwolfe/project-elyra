# P03 — In-process vs out-of-process

| Field | Value |
|-------|--------|
| **Status** | Ready (PR4) |
| **Safety class** | **R2** + **R1** on quarantine snapshot (not dual live connect by default) |
| **Prove / disprove** | process-specific vs pure client (H1 universal); supports H2 vs H7 split |
| **Evidence** | glass JSON snapshots + quarantine `api-matrix.json` |
| **Depends on** | P01 preferred; [SAFETY.md](../SAFETY.md) dual-connect policy |
| **Adjacency** | [../adjacency/glass.md](../adjacency/glass.md) curl notes |

## Purpose

Compare **live glass** memory payloads just after restart to **offline R1** probes on a quarantine snapshot of the same disk. Show that thin process truth is already present at open (load path / H1+H2), not introduced by glass serialization alone (H7).

## Prerequisites

- [ ] Read [../SAFETY.md](../SAFETY.md) (R2, dual-connect KD12, quarantine)
- [ ] Elyra running with `backend=lance`, memory write enabled, large disk corpus
- [ ] Glass base URL known (default `http://127.0.0.1:8787` or operator port)
- [ ] Prefer capture **just after restart**, before heavy promote/encode
- [ ] Prefer idle quarantine copy for R1 (not dual-connect live URI)

## Preferred path (executable)

### 0. Restart timing

1. Restart Elyra (or note last restart time).
2. **Before** heavy promote: capture glass payloads (step 1).
3. Then idle-copy quarantine and run P01 (step 2).

### 1. R2 — Glass snapshots

See full curl notes in [../adjacency/glass.md](../adjacency/glass.md). Minimal set:

```bash
BASE="${ELYRA_GLASS_BASE:-http://127.0.0.1:8787}"
RUN=docs/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01
mkdir -p "$RUN/glass"

curl -sS "$BASE/api/memory" | tee "$RUN/glass/overview.json"
curl -sS "$BASE/api/memory/atoms?limit=200" | tee "$RUN/glass/atoms.json"
curl -sS "$BASE/api/memory/vectors" | tee "$RUN/glass/vectors.json"
curl -sS "$BASE/api/memory/context" | tee "$RUN/glass/context.json"
```

Record at least:

| Field | Source |
|-------|--------|
| `atom_count` | `GET /api/memory` overview (store health / memory flags) |
| `vectors_ready` | **`GET /api/memory/vectors`** → `index.vectors_ready` (not on overview) |
| Atom list length + kinds (newest-first) | `/api/memory/atoms` |
| Meal / semantic omit reasons | `/api/memory/context` |

### 2. R1 — Quarantine snapshot (not dual live connect)

```bash
# Prefer Elyra idle or just-after-restart before heavy promote
QROOT=/tmp/lance-q-$(date +%Y%m%d)
./docs/lance-debug1/scripts/quarantine_copy.sh data/memory "$QROOT"

export LANCE_DEBUG_DATA_DIR="$QROOT/data"
export LANCE_DEBUG_URI="$QROOT/data/memory/lance"

python docs/lance-debug1/scripts/api_matrix.py \
  --uri "$LANCE_DEBUG_URI" \
  --out "$RUN/api-matrix.json"
```

Optional W1 on same snapshot (P02):

```bash
export PYTHONPATH=.
python docs/lance-debug1/scripts/load_parity.py \
  --data-dir "$LANCE_DEBUG_DATA_DIR" \
  --api-matrix "$RUN/api-matrix.json" \
  --out "$RUN/load-parity.json"
```

### 3. Compare

| Check | Expected if H1+H2 (not glass-only) |
|-------|-------------------------------------|
| glass `atom_count` | ~ process thin set (≈ `n_arrow`, often ~10–13 after restart + a few puts) |
| snapshot `n_full` | ≫ glass count (disk still large) |
| snapshot `n_arrow` | ≈ glass order-of-magnitude (default limit 10) |
| glass list kinds | **newest-first among `_by_id`** — may be haiku tools |
| raw `to_arrow` kinds | **table-order prefix** (snapshot B: summary×6 + tool×4) — **not** required to match glass list kinds |

Fill [../EVIDENCE-MATRIX.md](../EVIDENCE-MATRIX.md) rows for “Post-restart glass atom_count ~ process thin set”.

## Dual-connect policy

| Mode | When | Tag |
|------|------|-----|
| **Default** | glass R2 + quarantine R1 | — |
| **Discouraged** | concurrent `lancedb.connect` on **live** `data/memory/lance` while writer open | only with explicit operator accept; tag evidence `multi_connect` / `possibly_torn` |

See SAFETY KD12. Do **not** use dual live connect as the preferred P03 path.

## Pass / fail

| Claim | Pass |
|-------|------|
| Thin world is process/load, not glass-only | glass ≈ thin process; snapshot `n_full` still large |
| H1 universal (client) | quarantine bare `to_arrow` thin while full APIs large |
| H7 alone as root | **refuted** when glass matches process health thin count |

## Forbidden

- Dual live connect by default
- Deny-list ops on live or quarantine
- Product patches to `_load` / glass serializers

## Safety reminders

- Class **R2** for glass; **R1** for snapshot; **W1** only if running load_parity on quarantine
- Canonical marker only at `$QUARANTINE_ROOT/.lance-debug1-quarantine`
- No production changes under `elyra/`

See design §P03 in [../design-inspection-plan.md](../design-inspection-plan.md).
