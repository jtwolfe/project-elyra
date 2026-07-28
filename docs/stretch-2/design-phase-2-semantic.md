# Phase 2 — Semantic Memory (Nemotron + Multi-Embeddings)

**Status:** **Shipped** (2026-07-28) — short outline only; implementers and operators use the documents below.
**Depends on:** Phase 1 Done
**Implementation design (normative for code):** [design-phase-2-implementation.md](design-phase-2-implementation.md)
**Architecture note (what shipped):** [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md)
**Baseline:** [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md), [design-database-choices.md](design-database-choices.md)
**Runtime:** [design-nemotron-runtime.md](design-nemotron-runtime.md)
**Meal channel:** [design-context-meal-composition.md](design-context-meal-composition.md) (semantic as supporting only)

This sketch is **superseded for implementation and post-ship mapping** by:

1. **[design-phase-2-implementation.md](design-phase-2-implementation.md)** — full design, key decisions, PR plan, failure modes, flag matrix.
2. **[architecture/phase-2-semantic.md](architecture/phase-2-semantic.md)** — structure ↔ essay, activity map, invariants, freshness/migration as shipped, Vectors/Graph caveats.

Prefer those two docs over the outline below. The outline remains as a historical short map of intent.

## Goal

Add **associative / semantic** structure as *supporting* context: “this reminds me of…”, aligned with the essay’s associative connections — not a replacement for the open moment or broader episodic package.

- Omni-Embed-Nemotron (portable CPU / CUDA / ROCm) — see runtime design; **mock-first** in CI until Gate B
- Per-modality + joint embeddings per atom (**bonded channels** on one instance)
- Linked **parcels** when content exceeds safe limits (opt-in flag)
- ANN query with filters (time, moment, kind)
- Documented index freshness policy under continuous insert
- Meal semantic channel under hard `semantic_select_max_ms`

## Non-goals

- Success-path weighting (Phase 3)
- Full directed traversal product / Graph tab (Phase 2a)
- Fine-tuning Nemotron

## Concept mapping

| Essay / planning term | Phase 2 structure |
|----------------------|-------------------|
| Associative connection | ANN neighbours over embeddings (meal channel `semantic`) |
| Multimodal instance | Multi-channel `EmbeddingSet` on one atom |
| Recombination | Channel-level match + parent atom / parcel identity |
| Supporting vs primary context | Semantic section budgeted under temporal/episodic package |

## Key design points (summary)

- Embedding set: text / image / audio / video / joint as present (~2048-d)
- Parcels: split at natural boundaries; sequential + parent links; default **off**
- Indexes: Lance ANN on joint (and optional channels); recent buffer + optimize schedule
- Meal role: **supporting** context only; dedup against open moment and episodic fill
- Flags default **off** (`semantic_enabled`, `embed_enabled`, `parcels_enabled`)

## Success criteria

- [x] Multi-embeddings stored and queryable (Lance + mock; flags off by default)
- [x] Portable encode path documented and tested (mock in CI; real GPU optional / Gate B)
- [x] Segmentation + linked retrieval tested (parcels + parcel→parent in meal)
- [x] ANN freshness policy written and implemented (hybrid buffer + idle optimize)
- [x] Architecture note: [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md)

## Open questions

Resolved in [design-phase-2-implementation.md](design-phase-2-implementation.md) Key Decisions / Open Questions (operator 2026-07-28): joint-primary ranking (eager joint when multi-modal); no multi-channel fusion in v1; no light semantic edge rows in Phase 2.
