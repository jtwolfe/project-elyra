# Stretch 2 — Memory System

**Branch:** `grok-improvement-memory` (branched from `main`)
**Status:** Design / planning
**Depends on:** Stretch 1 (shipped), Grok path on `grok-improvement`

This stretch implements the atomized, multi-regime memory system described in [memory-atoms.pdf](../memory-atoms.pdf) and refined in the recent design conversations.

## Goals

Replace the current sliding-meal context window with a durable, multi-scale memory substrate that supports:

1. **Episodic / Temporal** — Moments contain atoms; rolling period-summary ladder (15 m → 1 h → 6 h → 1 d → 1 w → 1 m) as a natural-language temporal map.
2. **Semantic** — Nemotron multi-embeddings (per-modality + joint) with bonded sub-atoms; segmentation of oversized messages into linked parcels.
3. **Procedural / Success-path** — Goal → outcome trajectories with efficiency-based edge weighting (the delicate ANN-derived layer).

Context for the do-loop becomes a deliberate blend:

- **Temporal context** (drop-in replacement for today’s sliding meal)
- **Semantic supporting context**
- **Procedural process context**

Plus a directed-traversal capability (Phase 2a) so the model can actively explore the hypergraph around relevant seeds, with temporary flags so traversal does not permanently pollute the temporal view.

## Phase Map

| Phase | Name | Role | Risk / Care |
|-------|------|------|-------------|
| **1** | Temporal / Episodic | Atom + Moment schema, sequential links, rolling summary ladder | Foundational; must be solid |
| **2** | Semantic | Nemotron multi-embeddings, bonded sub-atoms, vector search, oversized-message segmentation | Hardware (ROCm / CUDA / CPU fallback) |
| **2a** | Directed Traversal | Model-driven hypergraph walk around semantic seeds; temporary temporal flags | Careful isolation of temporary context |
| **3** | Procedural / Success-path | Trajectory recording, efficiency weighting, continuous online updates, testing plan + synthetic data | **Most delicate** — requires extensive testing |

Later phases also integrate with Grok Build tooling and autonomous self-improvement workflows (GitHub visibility, organised work).

## Design Documents

| Doc | Purpose |
|-----|---------|
| [design-phase-1-temporal-episodic.md](design-phase-1-temporal-episodic.md) | Atom/Moment schema, period ladder, sequential links |
| [design-phase-2-semantic.md](design-phase-2-semantic.md) | Multi-embeddings, bonded sub-atoms, Nemotron integration, segmentation |
| [design-phase-2a-directed-traversal.md](design-phase-2a-directed-traversal.md) | Model-directed graph walk + temporary context flags |
| [design-phase-3-procedural.md](design-phase-3-procedural.md) | Success paths, weighting, continuous learning, test strategy |
| [design-storage-and-indexes.md](design-storage-and-indexes.md) | Database / index choices (to be researched) |
| [design-hardware-embedding-runtime.md](design-hardware-embedding-runtime.md) | Nemotron loading, ROCm / CUDA / CPU compatibility |

## Engineering Principles (non-negotiable)

All work follows [engineering-principles.md](../engineering-principles.md):

- Modular packages, no god modules
- Small units with explicit scope
- Tests are part of the feature
- Skills / prompts / tools on disk
- Config defaults first, few env vars
- Stretch discipline — do not smuggle later phases into earlier ones
- Reliability patterns (append-only where possible, explicit errors, single worker)

New modules will live under `elyra/memory/` (or similar narrow packages). Public APIs stay minimal. Every phase ships with tests.

## Next Immediate Steps

1. Flesh out the Phase 1 design doc.
2. Research and decide on storage / index technologies (separate doc).
3. Confirm hardware embedding runtime strategy.
4. Only then begin implementation of Phase 1.

## Relationship to Existing Work

- Current `moment/` store and beat tapes remain the substrate for “what happened in this do-loop”.
- Media attachment stubs (`embedding_status`, `embedding_ref`) already exist and will be extended.
- Sliding context meal will be replaced or augmented by the temporal ladder once Phase 1 is stable.
- Grok Build / self-improvement integration is planned for later in this stretch but is out of scope for the first three memory phases.
