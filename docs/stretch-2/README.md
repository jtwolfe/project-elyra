# Stretch 2 — Atomized Memory Substrate

**Branch:** `grok-improvement-memory` (from `main`)

**Goal:** Implement the atomized, multi-regime memory system described in the operator essay (`docs/memory-atoms.pdf`) and subsequent design conversations. This becomes the durable substrate that later enables Grok Build integration, autonomous self-improvement workflows, and organised GitHub project visibility for Elyra.

Stretch 2 is deliberately slow and phased. Phases 2 and 3 in particular require careful tuning and evidence before proceeding.

## Alignment with Engineering Principles

- **Modular packages, no god modules.** Memory lives in its own package(s) under `elyra/memory/` (or similar). Temporal, semantic, and procedural concerns are separate modules that compose. The do-loop and presence remain thin orchestrators.
- **Small units with explicit scope.** Every encoder, summary refresher, edge updater, and traversal helper declares in/out of scope.
- **Tests are part of the feature.** Unit + contract + integration tests for every phase; synthetic datasets required before Phase 3 claims success.
- **Stretch discipline.** No half-built hypergraphs or success-path machinery in Phase 1. Leave clean hooks only.
- **Config defaults + few env vars.** Nemotron paths, index locations, period scales live under `ELYRA_HOME` + config.
- **Portability.** Embedding inference must work on CPU, modern NVIDIA, and AMD (ROCm) with graceful fallback. Implementation must not be specific to any single GPU.

## High-level architecture

Three memory regimes over the same atom substrate:

1. **Temporal / Episodic** — Moments contain atoms. Atoms and moments are temporally linked. Rolling ladder of natural-language (and embeddable) summary atoms at fixed scales (15 m, 1 h, 6 h, 1 d, 1 w, 1 m). Drop-in replacement / evolution of the current sliding meal/window.
2. **Semantic** — Nemotron multi-channel embeddings (per-modality + joint) plus bonded sub-atoms. Vector search as supporting context. Large messages are segmented into linked parcels.
3. **Procedural / Success-path** — Goal → outcome trajectories. Edges accumulate weight from efficient successful re-use within a semantic + episodic subspace. Derived traversal prior ("ANN" in earlier discussion). Extremely delicate; requires testing plan and synthetic data.

Supporting capability:

- **Phase 2a Directed traversal** — Once a hypergraph (or adjacency) exists, Elyra can actively walk neighbourhoods around semantically relevant seeds, gather atoms + high-value neighbours. Temporary context flagging during traversal; only confirmed selections persist into the permanent temporal context.

## Phase overview

| Phase | Focus | Role in context construction | Risk / care level |
|-------|--------|------------------------------|-------------------|
| **1** | Temporal / Episodic (moments, atoms, sequential links, rolling summary ladder) | Primary temporal context; drop-in for current sliding window | Medium — foundation |
| **2** | Semantic (Nemotron multi-embeddings, bonded sub-atoms, segmentation, vector search) | Supporting context | Medium–High — model loading + indexing |
| **2a** | Directed traversal over the emerging graph | Model-managed / directed retrieval | High — temporary context hygiene |
| **3** | Procedural / success-path weighting + derived ANN priors | Process context | Very High — continuous learning correctness, synthetic evaluation |

Later Stretch 2 work (after memory is solid): Grok Build tool integration and autonomous self-improvement workflow that keeps GitHub projects organised and visible while Elyra uses Grok.

## Document map

| Document | Purpose |
|----------|---------|
| [README.md](README.md) | This overview |
| [design-phase-1-temporal.md](design-phase-1-temporal.md) | Moments, atoms, sequential links, rolling ladder of summary atoms |
| [design-phase-2-semantic.md](design-phase-2-semantic.md) | Nemotron multi-embeddings, bonded sub-atoms, large-message segmentation, vector indexes |
| [design-phase-2a-directed-traversal.md](design-phase-2a-directed-traversal.md) | Hypergraph / adjacency walk, temporary context flags, selection confirmation |
| [design-phase-3-procedural.md](design-phase-3-procedural.md) | Success-path recording, efficiency-based weighting, continuous learning, testing plan |
| [design-database-choices.md](design-database-choices.md) | Storage substrate research and recommendation (to be completed next) |
| [design-nemotron-runtime.md](design-nemotron-runtime.md) | Portable loading of Omni-Embed-Nemotron (CPU / CUDA / ROCm) |

## Working rules for this branch

- All design docs land before substantial implementation PRs for that phase.
- Each phase PR stack stays small, testable, and reversible.
- Promote individual phase work onto `grok-improvement-memory`; promote the whole stretch to `main` only after operator sign-off and live smoke.
- Continuous learning (Phase 3) and any background consolidation must respect presence timers / rest concepts and never starve the do-loop.

## Next immediate steps

1. Flesh out the Phase 1 design document in detail.
2. Complete database / storage research and record decisions in `design-database-choices.md`.
3. Define the portable Nemotron runtime contract.
4. Only then begin Phase 1 implementation on this branch.
