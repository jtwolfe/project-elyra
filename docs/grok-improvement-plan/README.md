# Grok Improvement Plan

This folder holds the design documentation for migrating Project Elyra from local Gemma (llama.cpp) onto xAI Grok models under a SuperGrok Heavy subscription, and for enabling safe self-improvement via Grok Build.

## Guiding principles

1. **Do-loop and presence first.** Stretch 1 (presence, moments, goals, identity, fail-closed growth tools) is already solid. We do not rebuild memory or continuous-work before the model path and usage controls are correct.
2. **Correctness over scope.** Every change must be small, testable, and reversible. Prefer configuration and thin adapters over deep rewrites.
3. **Subscription protection.** Elyra must never consume the full SuperGrok weekly quota under normal operation. A hierarchical usage meter with hard stops is a Phase 0 requirement.
4. **Person / Instrument separation.** Elyra remains the durable person (identity, goals, moments). Grok Build is the high-capability coding instrument that Elyra can call later. Phase 0 only prepares the model path and budgets; the `grok_build` tool itself is Phase 1+.
5. **Documentation before code.** Designs live here before implementation begins on the `grok-improvement` branch.

## Folder contents

| Document | Purpose |
|----------|---------|
| [phase-0.md](phase-0.md) | Complete Phase 0 concept design (provider, usage meter, prompt adjustments, success criteria) |

Later phases (to be added when ready):

- Phase 1 — `grok_build` tool + self-improvement goal scaffolding
- Phase 2 — Self-modification continuity protocol and worktree workflow
- Phase 3 — Atomized memory substrate (drawing on the memory-atom / hypergraph model)

## Branch

All work for this plan happens on the `grok-improvement` branch (created from `main`).

## Status

- **Phase 0**: Documented (this folder). Implementation not yet started.
