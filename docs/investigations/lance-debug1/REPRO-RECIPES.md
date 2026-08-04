# Reproduction recipes — lance-debug1

Step-by-step operator recipes. Prefer quarantine for R1; never run deny-list ops on live data.

| Recipe | Class | Proves | Scripts |
|--------|-------|--------|---------|
| **R1** | R1 | H1 / H1a / H1b (offline smoking gun) | `quarantine_copy.sh`, `env_check.py`, `api_matrix.py` |
| **R2** | R2 + R1 | Process thin vs disk full (glass + snapshot) | glass HTTP + R1 on snapshot (P03) |
| **R3** | **W1** | H2 / H5 (load inherits thin set) | `load_parity.py` (+ prior api_matrix) |
| R3b | R1 | H3 / H10 residual (optional if H1a+H1b) | `version_sample.py` |
| R4 | R1 | H8/H9 cascade disconfirm (optional) | `consumer_compare.py` |

Normative design: [design-inspection-plan.md](design-inspection-plan.md). Safety: [SAFETY.md](SAFETY.md).

---

## Recipe R1 — Offline smoking gun (primary)

**Prereq:** venv with `elyra[memory-lance]` (lancedb **0.20.x**); dogfood (or any) memory under `data/memory/`. Prefer **Python 3.12** if 3.14 native connect is broken.

**Expect:** bare `to_arrow` rows ≈ **10**; `count_rows` / `head(n_full)` / `to_lance` agree on large N; **H1a** prefix equality; **H1b** `ok` with `path` recorded (on 0.20.0 typically `head_n_full`). Absolute N is **run-specific** — do not hardcode 361/386.

### Steps

```bash
# From repo root
python docs/investigations/lance-debug1/scripts/env_check.py

# Canonical layout: QUARANTINE_ROOT, data_dir, marker, lance URI
QROOT=/tmp/lance-q-$(date +%Y%m%d)
# Args: SRC_MEMORY_ROOT  QUARANTINE_ROOT
# Copies src → $QROOT/data/memory/ and writes $QROOT/.lance-debug1-quarantine
./docs/investigations/lance-debug1/scripts/quarantine_copy.sh data/memory "$QROOT"

export LANCE_DEBUG_DATA_DIR="$QROOT/data"
export LANCE_DEBUG_URI="$QROOT/data/memory/lance"
# Marker (only): $QROOT/.lance-debug1-quarantine

RUN=docs/investigations/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01
mkdir -p "$RUN"

python docs/investigations/lance-debug1/scripts/api_matrix.py \
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
python docs/investigations/lance-debug1/scripts/fixtures/build_tiny_atoms.py --out /tmp/tiny-lance
python docs/investigations/lance-debug1/scripts/api_matrix.py --uri /tmp/tiny-lance --out /tmp/api-matrix.json
# expect n_full=25, n_arrow=10, h1a/h1b ok
```

---

## Recipe R2 — Process restart thin world (glass + offline; P03)

1. Restart Elyra; **before heavy promote**, capture glass payloads.
2. Take quarantine snapshot (idle preferred) and run **R1** on the snapshot (**not** dual live connect).
3. Compare glass process count ≈ `n_arrow` ≪ snapshot `n_full`.
4. Note glass Atoms **newest-first** kinds may be haiku tools while bare `to_arrow` kinds are table-order prefix (summary+tool) — split expectation.

```bash
BASE="${ELYRA_GLASS_BASE:-http://127.0.0.1:8787}"
RUN=docs/investigations/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01
mkdir -p "$RUN/glass"
curl -sS "$BASE/api/memory" | tee "$RUN/glass/overview.json"
curl -sS "$BASE/api/memory/atoms?limit=200" | tee "$RUN/glass/atoms.json"
curl -sS "$BASE/api/memory/vectors" | tee "$RUN/glass/vectors.json"
curl -sS "$BASE/api/memory/context" | tee "$RUN/glass/context.json"
# then R1 quarantine + api_matrix as in Recipe R1
```

Details: [procedures/P03-inprocess-vs-oop.md](procedures/P03-inprocess-vs-oop.md), [adjacency/glass.md](adjacency/glass.md).

---

## Recipe R3 — Load path parity (W1 quarantine; P02)

**Safety:** **W1** — marker required at `$QUARANTINE_ROOT/.lance-debug1-quarantine` only. Never open live unmarked store.

```bash
# After R1 quarantine + api_matrix on the same snapshot:
export PYTHONPATH=.
export LANCE_DEBUG_DATA_DIR=/tmp/lance-q-YYYYMMDD/data
RUN=docs/investigations/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01

python docs/investigations/lance-debug1/scripts/load_parity.py \
  --data-dir "$LANCE_DEBUG_DATA_DIR" \
  --api-matrix "$RUN/api-matrix.json" \
  --out "$RUN/load-parity.json"
```

**Expect (H2):** `process.atom_count ≈ n_arrow` (typically ~10); `n_full ≫ process`; skip-corrupt ≪ gap (H5 disconfirm).

Details: [procedures/P02-load-path-parity.md](procedures/P02-load-path-parity.md), [API-COMPARISON.md](API-COMPARISON.md).

---

## Recipe R3b — Version sample (R1 optional; P08)

**When:** After H1a/H1b; optional polish if default-limit proven. **Never** compact/optimize/cleanup.

```bash
python docs/investigations/lance-debug1/scripts/version_sample.py \
  --uri "$LANCE_DEBUG_URI" \
  --samples 5 \
  --out "$RUN/version-sample.json"
```

**H10 residual:** migration sites still use bare `to_arrow`; historical collapse only if samples are non-monotonic. Active process-thin when full APIs already large is **not** H10.

Details: [procedures/P08-version-sampling.md](procedures/P08-version-sampling.md), [VERSION-ARCHAEOLOGY.md](VERSION-ARCHAEOLOGY.md).

---

## Recipe R4 — Consumer thin vs full (optional R1; P06/P09)

**When:** After R1; documents H8/H9 cascade. Does **not** block provisional H1a+H2 dossier. **No** product `_load` patch.

```bash
export PYTHONPATH=.
python docs/investigations/lance-debug1/scripts/consumer_compare.py \
  --uri "$LANCE_DEBUG_URI" \
  --weave-report \
  --out "$RUN/consumer-compare.json"
```

**Expect:** full corpus has kinds/neighbors absent from thin set (`h8_primary_disconfirmed`); weave `one_endpoint_outside_thin` often &gt; 0 (H9 cascade signal). Split: thin kind hist = table prefix, not glass newest-first haiku.

Details: [procedures/P06-graph-traverse-meal.md](procedures/P06-graph-traverse-meal.md), [procedures/P09-promote-weave-links.md](procedures/P09-promote-weave-links.md), [adjacency/](adjacency/), [EVIDENCE-MATRIX.md](EVIDENCE-MATRIX.md).

---

## Related

- [procedures/P01-offline-api-matrix.md](procedures/P01-offline-api-matrix.md)
- [procedures/P02-load-path-parity.md](procedures/P02-load-path-parity.md)
- [procedures/P03-inprocess-vs-oop.md](procedures/P03-inprocess-vs-oop.md)–[P07](procedures/P07-glass-serialization.md), [P09](procedures/P09-promote-weave-links.md)
- [procedures/P08-version-sampling.md](procedures/P08-version-sampling.md)
- [adjacency/glass.md](adjacency/glass.md) (curl notes)
- [scripts/README.md](scripts/README.md)
- [HYPOTHESES.md](HYPOTHESES.md)
- [EVIDENCE-MATRIX.md](EVIDENCE-MATRIX.md)
