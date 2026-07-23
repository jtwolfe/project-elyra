# Grok Improvement Plan

This folder holds the design documentation for migrating Project Elyra from local Gemma (llama.cpp) onto xAI Grok models under a SuperGrok Heavy subscription, and for enabling safe self-improvement via Grok Build.

## Guiding principles

1. **Do-loop and presence first.** Stretch 1 (presence, moments, goals, identity, fail-closed growth tools) is already solid. We do not rebuild memory or continuous-work before the model path and usage controls are correct.
2. **Correctness over scope.** Every change must be small, testable, and reversible. Prefer configuration and thin adapters over deep rewrites.
3. **Subscription protection.** Elyra must never consume the full SuperGrok weekly quota under normal operation. A hierarchical usage meter with hard stops is a Phase 0 requirement.
4. **Person / Instrument separation.** Elyra remains the durable person (identity, goals, moments). Grok Build is the high-capability coding instrument that Elyra can call later. Phase 0 only prepares the model path and budgets; the `grok_build` tool itself is Phase 1+.
5. **Documentation before code.** Designs live here before implementation begins on the `grok-improvement` branch.
6. **Glue, not ceremony.** Metacognition (MC) is the light path that keeps goals, tasks, skills, tools (and later memory) coherent under continuous work. It is not a second mind. Name it early; give it form only after the Grok path is stable.

## Folder contents

| Document | Purpose |
|----------|---------|
| [phase-0.md](phase-0.md) | Phase 0 concept + **detailed implementation plan** (provider, usage meter, defaults, Web UI status contract, success criteria) |
| [metacognition.md](metacognition.md) | MC geometry, dual with Memory, employment plan (name now → shallow shape after Grok stable → optional package later) |

## Phase overview

| Phase | Focus |
|-------|--------|
| **Phase 0** | xAI / Grok provider path (**default ON**), hierarchical usage meter (50% weekly / day / 1-hour hard stops), light prompt fitness (done), continuous/auto **default OFF**, Web UI status visibility. **No MC implementation.** |
| **After Phase 0 stable** | Optional Stage B MC shape: ledger-aware soft bias + short Decide cadence in orient. Still no new subsystem. |
| **Phase 1** | `grok_build` tool + self-improvement goal scaffolding. MC package is **not** required; shallow shape may land here if not already done. |
| **Phase 2** | Self-modification continuity (worktree, verify, promote, controlled restart / resume). |
| **Phase 3** | Atomized memory substrate (memory-atom / hypergraph model) as equal peer to MC. |

Later (unscoped): remote Glass (Vercel + auth), TTS/STT voice.

## Operator target after Phase 0

```text
elyra start
```

→ provider **xai** (default), continuous **OFF**, usage meter ON, Web UI shows provider / model / usage remaining / hard-stop / continuous so the operator can see what is going on. Local path remains available via override.

## Branch

All work for this plan happens on the `grok-improvement` branch (created from `main`).

## Status

- **Phase 0**: Design + detailed implementation plan in [phase-0.md](phase-0.md). Prompt fitness already applied. Code implementation not yet started.
- **Metacognition**: Concept documented. Naming allowed now; form only after Grok path is stable.
