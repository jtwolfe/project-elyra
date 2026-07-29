# Reproduction recipes — lance-debug1

Step-by-step operator recipes. Prefer quarantine for R1; never run deny-list ops on live data.

| Recipe | Class | Proves | Scripts |
|--------|-------|--------|---------|
| **R1** | R1 | H1 / H1a / H1b (offline smoking gun) | `quarantine_copy.sh`, `env_check.py`, `api_matrix.py` |
| R2 | R2 + R1 | Process thin vs disk full (glass + snapshot) | glass HTTP + R1 on snapshot (PR3+) |
| R3+ | — | Later procedures | see procedures/ |

Normative design: [design-inspection-plan.md](design-inspection-plan.md). Safety: [SAFETY.md](SAFETY.md).

---

## Recipe R1 — Offline smoking gun (primary)

**Prereq:** venv with `elyra[memory-lance]` (lancedb **0.20.x**); dogfood (or any) memory under `data/memory/`. Prefer **Python 3.12** if 3.14 native connect is broken.

**Expect:** bare `to_arrow` rows ≈ **10**; `count_rows` / `head(n_full)` / `to_lance` agree on large N; **H1a** prefix equality; **H1b** `ok` with `path` recorded (on 0.20.0 typically `head_n_full`). Absolute N is **run-specific** — do not hardcode 361/386.

### Steps

```bash
# From repo root
python docs/lance-debug1/scripts/env_check.py

# Canonical layout: QUARANTINE_ROOT, data_dir, marker, lance URI
QROOT=/tmp/lance-q-$(date +%Y%m%d)
# Args: SRC_MEMORY_ROOT  QUARANTINE_ROOT
# Copies src → $QROOT/data/memory/ and writes $QROOT/.lance-debug1-quarantine
./docs/lance-debug1/scripts/quarantine_copy.sh data/memory "$QROOT"

export LANCE_DEBUG_DATA_DIR="$QROOT/data"
export LANCE_DEBUG_URI="$QROOT/data/memory/lance"
# Marker (only): $QROOT/.lance-debug1-quarantine

RUN=docs/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01
mkdir -p "$RUN"

python docs/lance-debug1/scripts/api_matrix.py \
  --uri "$LANCE_DEBUG_URI" \
  --table atoms \
  --out "$RUN/api-matrix.json"
```

### Interpret `api-matrix.json`

| Field | Smoking-gun pattern (0.20.0) |
|-------|------------------------------|
| `summary.n_full` | Large corpus (`count_rows`) |
| `summary.n_arrow` | **10** (default query limit) |
| `summary.n_head` | == `n_full` when head used full |
| `h1.ok` | true (`n_arrow ≪ n_full`) |
| `h1a.ok` | true (`arrow_ids == head(10)` order-sensitive) |
| `h1b.ok` / `h1b.path` | true / `head_n_full` (or `to_lance`) |
| `h1b.attempts` | typically `query_public_missing` → `private_async` (discovery) → `head_n_full` on 0.20.0 |
| `summary.h4_demoted_if_h1a_h1b` | true when both hold |

### Safety

- Prefer writer **idle/stopped** before quarantine copy; if concurrent, marker `possibly_torn=true` — do not treat torn counts as definitive H1b failures without a clean copy.
- **Deny-list:** never `compact_files` / `optimize` / `cleanup_old_versions` / `delete` / `drop_table` on operator data.
- Do not open `LanceMemoryStore` in R1 (W1 → P02 / `load_parity.py` in PR3).

### Hermetic dry-run (no dogfood)

```bash
python docs/lance-debug1/scripts/fixtures/build_tiny_atoms.py --out /tmp/tiny-lance
python docs/lance-debug1/scripts/api_matrix.py --uri /tmp/tiny-lance --out /tmp/api-matrix.json
# expect n_full=25, n_arrow=10, h1a/h1b ok
```

---

## Recipe R2 — Process restart thin world (stub; PR3+)

1. Restart Elyra; before heavy promote, capture glass `GET /api/memory` atom_count.
2. Take quarantine snapshot (idle preferred) and run **R1** on the snapshot.
3. Compare glass process count ≈ `n_arrow` ≪ snapshot `n_full`.

Details: [procedures/P03-inprocess-vs-oop.md](procedures/P03-inprocess-vs-oop.md).

---

## Related

- [procedures/P01-offline-api-matrix.md](procedures/P01-offline-api-matrix.md)
- [scripts/README.md](scripts/README.md)
- [HYPOTHESES.md](HYPOTHESES.md)
