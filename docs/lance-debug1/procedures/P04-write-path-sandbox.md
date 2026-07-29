# P04 — Write path sandbox

| Field | Value |
|-------|--------|
| **Status** | Ready (PR4) |
| **Safety class** | **W1 only** on marked quarantine |
| **Prove / disprove** | H3 (write healthy), H4 partially (optional version growth), bucket C |
| **Evidence** | before/after `api-matrix.json` + optional process health after store open |
| **Depends on** | quarantine marker (KD15); prefer P01 baseline |

## Purpose

Confirm `put_atom` / `merge_insert` **grows disk** and process maps on a quarantine copy only. Disconfirm “promote never wrote” as an explanation of the disk-vs-process gap when full APIs already show a large corpus (H3 healthy).

## Prerequisites

- [ ] Read [../SAFETY.md](../SAFETY.md) (W1, deny-list, marker)
- [ ] Idle quarantine full memory root with marker
- [ ] Baseline P01 `api-matrix.json` on that quarantine URI
- [ ] `PYTHONPATH=.` so `elyra` imports resolve
- [ ] **Never** run against live operator `data/`

## Procedure (executable)

### 0. Quarantine + baseline

```bash
QROOT=/tmp/lance-q-$(date +%Y%m%d)-write
./docs/lance-debug1/scripts/quarantine_copy.sh data/memory "$QROOT"

export LANCE_DEBUG_DATA_DIR="$QROOT/data"
export LANCE_DEBUG_URI="$QROOT/data/memory/lance"
export PYTHONPATH=.

RUN=docs/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01
mkdir -p "$RUN"

python docs/lance-debug1/scripts/api_matrix.py \
  --uri "$LANCE_DEBUG_URI" \
  --out "$RUN/api-matrix-before-write.json"
```

Record `n_full_before`, `n_arrow_before`, `n_versions_before` (if present).

### 1. Synthetic put (W1 store open)

Open `LanceMemoryStore` on the marked quarantine and put **one** synthetic atom (or a thin harness that only calls `put_atom`). Marker required — same rule as `load_parity.py`:

```text
marker = Path(data_dir).resolve().parent / ".lance-debug1-quarantine"
```

Example sketch (operator notebook / one-off; not a product entrypoint):

```python
from pathlib import Path
from elyra.config import ElyraPaths
from elyra.memory.config import MemorySettings
from elyra.memory.lance_store import LanceMemoryStore
from elyra.memory.types import Atom, utc_now_iso

data_dir = Path("$LANCE_DEBUG_DATA_DIR")  # …/data
marker = data_dir.resolve().parent / ".lance-debug1-quarantine"
assert marker.is_file(), marker

paths = ElyraPaths(data_dir=str(data_dir))
store = LanceMemoryStore(paths, MemorySettings(backend="lance"))
atom = Atom(
    atom_id="lance-debug1-probe-write-001",
    t_start=utc_now_iso(),
    kind="observation",
    content_text="P04 write-path sandbox synthetic atom",
    moment_id="lance-debug1-probe-moment",
)
store.put_atom(atom)
print("process_count", len(store._by_id))
print("health", store.health().get("atom_count"))
store.close()  # if available; else process exit
```

**Forbidden:** `compact_files`, `cleanup_old_versions`, `optimize`, `delete`, `drop_table`, live migrate.

### 2. Re-measure API matrix

```bash
python docs/lance-debug1/scripts/api_matrix.py \
  --uri "$LANCE_DEBUG_URI" \
  --out "$RUN/api-matrix-after-write.json"
```

### 3. Optional — many puts / version growth

If investigating whether bare `to_arrow` thinness **ratio** changes with version count (H4 residual after H1a/H1b):

1. Put N synthetic atoms (still quarantine only).
2. Re-run api_matrix; record `n_versions`, `n_full`, `n_arrow`.
3. If H1a still holds (`arrow_ids == head(10)`), demote “fragment tip only” as sole explanation.

Do **not** compact to “fix” layout.

## Expected if H3 (healthy write)

| Check | Expected |
|-------|----------|
| `n_full_after` | ≥ `n_full_before` + 1 (full APIs see the put) |
| process `_by_id` after put | +1 for the synthetic id |
| bare `to_arrow` | may stay ~10 if still default-limit — **does not** prove write failure |
| Live dogfood promote | already observed mid-session; this procedure seals quarantine proof |

## Pass / fail (hypotheses)

| Hypothesis | Pass |
|------------|------|
| **H3** | Full-read paths gain the synthetic atom; put succeeds on quarantine |
| **H4** | Only elevated if H1a/H1b already fail; optional version growth re-check here |

## Forbidden

- Live operator `data_dir`
- Deny-list compaction / cleanup / optimize / delete
- Interpreting thin bare `to_arrow` after put as “write lost the row” when `count_rows` / `head(n_full)` grew

## Safety reminders

- Class **W1** — quarantine + marker only
- Prefer separate `$QROOT` from pure R1 evidence runs if you want an unmutated snapshot for dossier
- No `elyra/` product changes

See design §P04 in [../design-inspection-plan.md](../design-inspection-plan.md).
