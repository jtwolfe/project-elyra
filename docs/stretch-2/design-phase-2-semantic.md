# Phase 2 — Semantic Memory (Nemotron + Multi-Embeddings)

**Status:** Design draft
**Depends on:** Phase 1 stable
**Baseline:** [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md), [design-database-choices.md](design-database-choices.md)
**Runtime:** [design-nemotron-runtime.md](design-nemotron-runtime.md)
**Meal channel:** [design-context-meal-composition.md](design-context-meal-composition.md) (semantic as supporting only)

## Goal

Add **associative / semantic** structure as *supporting* context: “this reminds me of…”, aligned with the essay’s associative connections — not a replacement for the open moment or broader episodic package.

- Omni-Embed-Nemotron (portable CPU / CUDA / ROCm) — see runtime design
- Per-modality + joint embeddings per atom (**bonded subatoms** as internal channels)
- Linked **parcels** when content exceeds safe limits
- ANN query with filters (time, moment, kind)
- Documented index freshness policy under continuous insert

## Non-goals

- Success-path weighting (Phase 3)
- Full directed traversal product (Phase 2a)
- Fine-tuning Nemotron

## Concept mapping

| Essay / planning term | Phase 2 structure |
|----------------------|-------------------|
| Associative connection | Semantic edges and/or ANN neighbours over embeddings |
| Multimodal instance | Multi-channel embedding set on one atom |
| Recombination | Channel-level match + parent atom identity |
| Supporting vs primary context | Semantic section budgeted under temporal/episodic package |

## Key design points

- Embedding set: text / image / audio / video / joint as present (~2048-d)
- Parcels: split at natural boundaries; sequential + parent links
- Indexes: Lance ANN on joint (and optional channels); recent buffer + optimize schedule
- Meal role: **supporting** context only; dedup against open moment and episodic fill

## Success criteria

- [ ] Multi-embeddings stored and queryable
- [ ] Portable encode path documented and tested (mock in CI; real GPU optional)
- [ ] Segmentation + linked retrieval tested
- [ ] ANN freshness policy written and implemented
- [ ] Architecture note updated (semantic map + activities)

## Open questions

- Ranking fusion across channels
- Lazy vs eager joint embedding
