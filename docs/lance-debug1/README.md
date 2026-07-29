# Lance debug package 1 (`docs/lance-debug1`)

**Status:** **Complete** — dogfood evidence sealed (`2026-07-29-run-01`) + [BUG-DOSSIER.md](BUG-DOSSIER.md). Inspection only; **no product fix authorized**.

| Field | Value |
|-------|--------|
| **Intent** | Inspection and fault isolation only — **no product fix** |
| **Isolation** | All work product lives under `docs/lance-debug1/`; do not modify `elyra/memory/**` |
| **Primary mechanism** | Bare `Table.to_arrow()` = default query limit **10** (H1 / H1a / H1b) → thin `_load` (H2) |
| **Sealed run** | [evidence/2026-07-29-run-01/](evidence/2026-07-29-run-01/) — n_full=**386**, n_arrow=**10**, process=**10** |
| **Origin** | `/design` run `ed40fbd4` (2026-07-29); plan `60b09de2` |
| **Normative design** | [design-inspection-plan.md](design-inspection-plan.md) |
| **Exit artifact** | [BUG-DOSSIER.md](BUG-DOSSIER.md) |

---

## Purpose

Live dogfood shows a **disk vs process discrepancy**: on-disk `atoms.lance` holds a large corpus while after restart `LanceMemoryStore` rebuilds indexes from a thin row set (~default-limit 10). This package isolates that fault with hypotheses, safety rules, offline/in-process probes, and a sealed evidence bag — then produces `BUG-DOSSIER.md` as input to a **later** fix design.

**Out of scope here:** changing `_load`, product defaults, stretch-2 architecture docs, or implementing emergency patches inside these PRs.

---

## Safety (read first)

| Class | Meaning |
|-------|---------|
| **R0** | Code/docs read only |
| **R1** | Read-only lancedb client probes (prefer quarantine) |
| **R2** | Glass HTTP / logs on live process |
| **W1** | Any path that may write (including **`LanceMemoryStore` open**) — quarantine only |
| **FORBIDDEN** | drop/delete/compact/optimize/cleanup on operator data |

Full rules: **[SAFETY.md](SAFETY.md)**. Canonical quarantine marker is **only** `$QUARANTINE_ROOT/.lance-debug1-quarantine`. **No dual live connect** while the writer is open (prefer glass + idle snapshot).

---

## Package layout

```text
docs/lance-debug1/
├── README.md                    # This index
├── design-inspection-plan.md    # Normative design (keep)
├── SAFETY.md
├── OBSERVED-FACTS.md            # Snapshot-labeled facts (not absolute pass constants)
├── HYPOTHESES.md                # H1 / H1a / H1b – H12 (statuses sealed PR5)
├── FAULT-BUCKETS.md             # Buckets A–G
├── CODE-PATH-MAP.md             # Open/load/write/consumer + line pins
├── TO-ARROW-CALLERS.md          # Every to_arrow / head / count_rows site
├── API-COMPARISON.md            # Filled offline + process comparison
├── VERSION-ARCHAEOLOGY.md       # Version sampling + H10 residual
├── procedures/                  # P01–P09
├── adjacency/                   # Embed / graph / meal / glass / promote-weave
├── EVIDENCE-MATRIX.md           # Observation × hypothesis × bucket (filled)
├── evidence/                    # Sealed run dirs + templates
├── scripts/                     # Hermetic helpers
├── REPRO-RECIPES.md             # R1–R3b + R2 glass path
└── BUG-DOSSIER.md               # Final bug description (exit; not a fix auth)
```

---

## Artifact index

| Artifact | Role | PR |
|----------|------|-----|
| [design-inspection-plan.md](design-inspection-plan.md) | Normative inspection design | design |
| [SAFETY.md](SAFETY.md) | R0–W1 / FORBIDDEN, quarantine, deny-list | PR1 |
| [OBSERVED-FACTS.md](OBSERVED-FACTS.md) | Frozen dogfood snapshots (relative relations) | PR1 |
| [HYPOTHESES.md](HYPOTHESES.md) | H1–H12 status board | PR1; statuses PR5 |
| [FAULT-BUCKETS.md](FAULT-BUCKETS.md) | Buckets A–G | PR1 |
| [CODE-PATH-MAP.md](CODE-PATH-MAP.md) | Call graph + fresh line pins | PR1 |
| [TO-ARROW-CALLERS.md](TO-ARROW-CALLERS.md) | `to_arrow` / related call matrix | PR1 |
| [procedures/](procedures/) | P01–P09 (P08 optional polish) | PR1–PR4 |
| [evidence/](evidence/) | Templates + sealed run `2026-07-29-run-01` | PR1 templates; **PR5 seal** |
| [scripts/](scripts/) | Probe helpers | PR1+ |
| [API-COMPARISON.md](API-COMPARISON.md) | Structured API comparison results | PR3; filled PR5 |
| [REPRO-RECIPES.md](REPRO-RECIPES.md) | Step-by-step repros (R1–R3b) | PR2+ |
| [EVIDENCE-MATRIX.md](EVIDENCE-MATRIX.md) | Observation × hypothesis × bucket | PR4; filled PR5 |
| [VERSION-ARCHAEOLOGY.md](VERSION-ARCHAEOLOGY.md) | Version sampling + H10 residual | PR3; filled PR5 |
| [adjacency/](adjacency/) | Embed / graph / meal / glass / promote-weave cascade | PR4 |
| **[BUG-DOSSIER.md](BUG-DOSSIER.md)** | Final bug description (exit; **not** a fix auth) | **PR5** |

---

## Status board

| Track | Status |
|-------|--------|
| Scaffold + safety + hypotheses + buckets | **Done (PR1)** |
| P01 `api_matrix` + quarantine_copy + env_check | **Done (PR2)** |
| P02 load_parity + P08 version_sample | **Done (PR3)** |
| Adjacency P03–P07, P09 + EVIDENCE-MATRIX + consumer_compare | **Done (PR4)** |
| Dogfood evidence + BUG-DOSSIER | **Done (PR5)** — Complete |

**Hypothesis sequencing (critical path):** H1a → H1b → H2 first — **sealed supported** on `2026-07-29-run-01`. H4 demoted; H5 refuted; H10 residual only. Adjacency cascade documented; H8 primary refuted.

---

## How to work in this package

1. Read [SAFETY.md](SAFETY.md) and [design-inspection-plan.md](design-inspection-plan.md).
2. Prefer **quarantine full memory root** for any W1 or heavy R1; marker only at `$QUARANTINE_ROOT/.lance-debug1-quarantine`.
3. Capture evidence under `evidence/YYYY-MM-DD-run-NN/` using templates; use **relative** equality (`to_arrow ≪ count_rows`, process ≈ `to_arrow`), not fixed atom totals from design snapshots.
4. Update [HYPOTHESES.md](HYPOTHESES.md) statuses only from procedure evidence.
5. Never patch `elyra/memory/**` in lance-debug1 PRs. Merging the dossier does **not** authorize `_load` changes.

### Scripts (PR2–PR4)

```bash
# Optional: refresh TO-ARROW-CALLERS pins from this worktree
python docs/lance-debug1/scripts/caller_grep_report.py

# R1 offline smoking gun (see REPRO-RECIPES.md R1)
python docs/lance-debug1/scripts/env_check.py
./docs/lance-debug1/scripts/quarantine_copy.sh data/memory /tmp/lance-q-YYYYMMDD
export LANCE_DEBUG_DATA_DIR=/tmp/lance-q-YYYYMMDD/data
export LANCE_DEBUG_URI=$LANCE_DEBUG_DATA_DIR/memory/lance
export PYTHONPATH=.
RUN=docs/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01
mkdir -p "$RUN"

python docs/lance-debug1/scripts/api_matrix.py \
  --uri "$LANCE_DEBUG_URI" --out "$RUN/api-matrix.json"

# W1 load parity (PR3) — marker required
python docs/lance-debug1/scripts/load_parity.py \
  --data-dir "$LANCE_DEBUG_DATA_DIR" \
  --api-matrix "$RUN/api-matrix.json" \
  --out "$RUN/load-parity.json"

# R1 version sample (PR3; optional if H1a+H1b proven)
python docs/lance-debug1/scripts/version_sample.py \
  --uri "$LANCE_DEBUG_URI" --samples 5 \
  --out "$RUN/version-sample.json"

# Optional R1 consumer thin vs full + weave (PR4; P06/P09)
python docs/lance-debug1/scripts/consumer_compare.py \
  --uri "$LANCE_DEBUG_URI" --weave-report \
  --out "$RUN/consumer-compare.json"
```

Glass R2 curl notes: [adjacency/glass.md](adjacency/glass.md).  
See [scripts/README.md](scripts/README.md), [REPRO-RECIPES.md](REPRO-RECIPES.md), [EVIDENCE-MATRIX.md](EVIDENCE-MATRIX.md).

---

## Related (read-only cross-links)

- `docs/known-bugs.md` — BUG-wake-02, BUG-mem-gpu-01 (adjacent; not Lance root)
- `docs/stretch-2/architecture/phase-2-semantic.md` — semantic architecture (reference)
- `docs/stretch-2/architecture/spikes/lance-emb-migration.md` — migration also uses `to_arrow`

Stretch-2 product docs are **not** updated until a deliberate promotion PR after the dossier is accepted.
