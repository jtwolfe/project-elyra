# Lance debug package 1 (`docs/lance-debug1`)

**Status:** **Inspection complete** — dogfood evidence sealed (`2026-07-29-run-01`) + [BUG-DOSSIER.md](BUG-DOSSIER.md). **Product fix landed** in `elyra/memory` (`fcb5130`; design [design-fix-load-truncation.md](design-fix-load-truncation.md)). This package remains historical evidence; do not rewrite the sealed bag.

| Field | Value |
|-------|--------|
| **Intent** | Inspection and fault isolation (historical) — product fix is **outside** this package |
| **Isolation** | Evidence bag under `docs/lance-debug1/`; product code in `elyra/memory/**` |
| **Primary mechanism** | Bare `Table.to_arrow()` = default query limit **10** (H1 / H1a / H1b) → thin `_load` (H2) |
| **Sealed run** | [evidence/2026-07-29-run-01/](evidence/2026-07-29-run-01/) — n_full=**386**, n_arrow=**10**, process=**10** |
| **Product fix** | [design-fix-load-truncation.md](design-fix-load-truncation.md) → `fcb5130` full-table materialize helper + `_load` / migrate / promote; **restart required** |
| **Known-bugs** | [BUG-mem-lance-01](../known-bugs.md) **Fixed** (restart required) |
| **Origin** | `/design` run `ed40fbd4` (2026-07-29); plan `60b09de2` (inspect); plan `1c062b32` (fix + docs) |
| **Normative inspection design** | [design-inspection-plan.md](design-inspection-plan.md) |
| **Exit artifact (inspection)** | [BUG-DOSSIER.md](BUG-DOSSIER.md) |

---

## Purpose

Live dogfood showed a **disk vs process discrepancy**: on-disk `atoms.lance` holds a large corpus while after restart `LanceMemoryStore` rebuilt indexes from a thin row set (~default-limit 10). This package isolated that fault with hypotheses, safety rules, offline/in-process probes, and a sealed evidence bag — then produced `BUG-DOSSIER.md` as input to the product fix design.

**Product fix (landed):** [design-fix-load-truncation.md](design-fix-load-truncation.md) — explicit full-table materialize (`head` / `to_lance`) wired through `_load`, migrate, promote, and empty-check; dual-count health. **Operator:** restart presence/glass after deploy. **Out of scope for this package itself:** rewriting sealed evidence JSON or re-authorizing new product patches from the dossier alone.

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
├── design-inspection-plan.md    # Normative inspection design (historical)
├── design-fix-load-truncation.md  # Product fix design (implemented)
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
| **[BUG-DOSSIER.md](BUG-DOSSIER.md)** | Final bug description (inspection exit) | **PR5** |
| [design-fix-load-truncation.md](design-fix-load-truncation.md) | Product fix design | design → **implemented** `fcb5130` |

---

## Status board

| Track | Status |
|-------|--------|
| Scaffold + safety + hypotheses + buckets | **Done (PR1)** |
| P01 `api_matrix` + quarantine_copy + env_check | **Done (PR2)** |
| P02 load_parity + P08 version_sample | **Done (PR3)** |
| Adjacency P03–P07, P09 + EVIDENCE-MATRIX + consumer_compare | **Done (PR4)** |
| Dogfood evidence + BUG-DOSSIER | **Done (PR5)** — inspection complete |
| Product fix (`elyra/memory` full-table load) | **Landed** `fcb5130` — [design-fix-load-truncation.md](design-fix-load-truncation.md); known-bugs **BUG-mem-lance-01** Fixed |
| Docs closeout | **This README + known-bugs** (plan `1c062b32` PR2) |

**Hypothesis sequencing (critical path):** H1a → H1b → H2 first — **sealed supported** on `2026-07-29-run-01`. H4 demoted; H5 refuted; H10 residual closed in product by wiring migrate/promote through the same helper. Adjacency cascade documented; H8 primary refuted.

---

## How to work in this package

1. Read [SAFETY.md](SAFETY.md) and [design-inspection-plan.md](design-inspection-plan.md).
2. Prefer **quarantine full memory root** for any W1 or heavy R1; marker only at `$QUARANTINE_ROOT/.lance-debug1-quarantine`.
3. Capture evidence under `evidence/YYYY-MM-DD-run-NN/` using templates; use **relative** equality (`to_arrow ≪ count_rows`, process ≈ `to_arrow`), not fixed atom totals from design snapshots.
4. Update [HYPOTHESES.md](HYPOTHESES.md) statuses only from procedure evidence.
5. Do not rewrite sealed `evidence/**` JSON. Product changes live under `elyra/memory/**` via the fix design, not by editing the dossier.

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

- `docs/known-bugs.md` — **BUG-mem-lance-01** (Fixed); **BUG-wake-02**, **BUG-mem-gpu-01** (still open adjacency; not Lance row-loss root)
- [design-fix-load-truncation.md](design-fix-load-truncation.md) — product fix design (implemented)
- `docs/stretch-2/architecture/phase-2-semantic.md` — semantic architecture (reference)
- `docs/design/memory/spikes/lance-emb-migration.md` — historical migration notes (pre-fix `to_arrow` risk)
