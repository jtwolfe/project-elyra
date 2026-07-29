# Lance debug package 1 (`docs/lance-debug1`)

**Status:** Scaffolded (PR1) — inspection package layout + safety rules in place; probes not yet executed.

| Field | Value |
|-------|--------|
| **Intent** | Inspection and fault isolation only — **no product fix** |
| **Isolation** | All work product lives under `docs/lance-debug1/`; do not modify `elyra/memory/**` |
| **Primary mechanism candidate** | Bare `Table.to_arrow()` = default query limit **10** (H1 / H1a / H1b) |
| **Origin** | `/design` run `ed40fbd4` (2026-07-29); plan `60b09de2` |
| **Normative design** | [design-inspection-plan.md](design-inspection-plan.md) |

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
├── HYPOTHESES.md                # H1 / H1a / H1b – H12
├── FAULT-BUCKETS.md             # Buckets A–G
├── CODE-PATH-MAP.md             # Open/load/write/consumer + line pins
├── TO-ARROW-CALLERS.md          # Every to_arrow / head / count_rows site
├── procedures/                  # P01–P09 (stubs until later PRs)
├── evidence/                    # Run dirs + templates
├── scripts/                     # Hermetic helpers (colocated)
└── (later) API-COMPARISON.md, REPRO-RECIPES.md, EVIDENCE-MATRIX.md,
            VERSION-ARCHAEOLOGY.md, adjacency/, BUG-DOSSIER.md
```

---

## Artifact index

| Artifact | Role | PR |
|----------|------|-----|
| [design-inspection-plan.md](design-inspection-plan.md) | Normative inspection design | design |
| [SAFETY.md](SAFETY.md) | R0–W1 / FORBIDDEN, quarantine, deny-list | PR1 |
| [OBSERVED-FACTS.md](OBSERVED-FACTS.md) | Frozen dogfood snapshots (relative relations) | PR1 |
| [HYPOTHESES.md](HYPOTHESES.md) | H1–H12 status board | PR1 |
| [FAULT-BUCKETS.md](FAULT-BUCKETS.md) | Buckets A–G | PR1 |
| [CODE-PATH-MAP.md](CODE-PATH-MAP.md) | Call graph + fresh line pins | PR1 |
| [TO-ARROW-CALLERS.md](TO-ARROW-CALLERS.md) | `to_arrow` / related call matrix | PR1 |
| [procedures/](procedures/) | P01–P09 procedure stubs | PR1 stubs; fill PR2–PR4 |
| [evidence/](evidence/) | Templates + per-run evidence | PR1 templates; fill PR5 |
| [scripts/](scripts/) | Probe helpers (`caller_grep_report` in PR1) | PR1+ |
| `API-COMPARISON.md` | Structured API comparison results | PR3 |
| `REPRO-RECIPES.md` | Step-by-step repros | PR2 |
| `EVIDENCE-MATRIX.md` | Observation × hypothesis × bucket | PR4 |
| `VERSION-ARCHAEOLOGY.md` | Version sampling plan/results | PR3 |
| `adjacency/` | Embed / graph / meal / glass cascade | PR4 |
| `BUG-DOSSIER.md` | Final bug description (exit; **not** a fix auth) | PR5 |

---

## Status board

| Track | Status |
|-------|--------|
| Scaffold + safety + hypotheses + buckets | **Done (PR1)** |
| P01 `api_matrix` + quarantine_copy + env_check | Pending PR2 |
| P02 load_parity + P08 version_sample | Pending PR3 |
| Adjacency P03–P07, P09 | Pending PR4 |
| Dogfood evidence + BUG-DOSSIER | Pending PR5 |

**Hypothesis sequencing (critical path):** H1a → H1b → H2 first. If high-confidence, provisional root-cause may be drafted early; H4/P08 demoted if default-limit proven.

---

## How to work in this package

1. Read [SAFETY.md](SAFETY.md) and [design-inspection-plan.md](design-inspection-plan.md).
2. Prefer **quarantine full memory root** for any W1 or heavy R1; marker only at `$QUARANTINE_ROOT/.lance-debug1-quarantine`.
3. Capture evidence under `evidence/YYYY-MM-DD-run-NN/` using templates; use **relative** equality (`to_arrow ≪ count_rows`, process ≈ `to_arrow`), not fixed atom totals from design snapshots.
4. Update [HYPOTHESES.md](HYPOTHESES.md) statuses only from procedure evidence.
5. Never patch `elyra/memory/**` in lance-debug1 PRs. Merging the dossier does **not** authorize `_load` changes.

### Scripts (when present)

```bash
# Optional: refresh TO-ARROW-CALLERS pins from this worktree
python docs/lance-debug1/scripts/caller_grep_report.py

# Later PRs (not in PR1):
# ./docs/lance-debug1/scripts/quarantine_copy.sh data/memory /tmp/lance-q-YYYYMMDD
# python docs/lance-debug1/scripts/api_matrix.py --uri "$LANCE_DEBUG_URI" --out ...
# python docs/lance-debug1/scripts/load_parity.py --data-dir "$LANCE_DEBUG_DATA_DIR" ...
```

See [scripts/README.md](scripts/README.md).

---

## Related (read-only cross-links)

- `docs/known-bugs.md` — BUG-wake-02, BUG-mem-gpu-01 (adjacent; not Lance root)
- `docs/stretch-2/architecture/phase-2-semantic.md` — semantic architecture (reference)
- `docs/stretch-2/architecture/spikes/lance-emb-migration.md` — migration also uses `to_arrow`

Stretch-2 product docs are **not** updated until a deliberate promotion PR after the dossier is accepted.
