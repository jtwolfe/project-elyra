# Stretch 2 — Atomized Memory Substrate

**Branch:** `grok-improvement-memory` (from `main`)
**Philosophy:** [memory-atoms.pdf](../memory-atoms.pdf) — *What is wrong with my memory?*
**Planning baseline:** [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md)
**Soft conceptual guidance:** [philosophical-soft-guidance.md](philosophical-soft-guidance.md) (influences only — not phase goals)
**Context meal (provisional):** [design-context-meal-composition.md](design-context-meal-composition.md)

## Goal

Implement a durable, multi-regime memory substrate aligned with the atomized-memory philosophy: instances (atoms), temporal scaffold, weave of connections, consolidation into higher-scale structure, and later success-weighted pathways — not a warehouse of detached facts.

This substrate later supports Grok Build integration and autonomous self-improvement workflows. Those integrations are **out of scope** until the memory phases are solid.

Stretch 2 is deliberately slow and phased. Phases 2a and 3 require evidence and tuning; Phase 3 is evaluation-first.

---

## Alignment with engineering principles

All of [engineering-principles.md](../engineering-principles.md) applies. Stretch 2 **extends** them in one critical way:

### Documentation is part of the product surface

For a system this conceptual, “docs updated when behaviour changes” is not enough. Each phase must ship **concept-mapping documentation** that explains:

1. What was implemented (types, tables, APIs, jobs).
2. How those structures map to essay concepts (atom, context, edge kinds, summary/consolidation, trajectory, temporary vs durable context).
3. Which activities from the [inspiration activity model](inspiration-activity-model-and-storage.md) are live vs background.
4. Invariants and failure behaviour.

Design docs (`design-*.md`) guide implementation. **Architecture manuals** (under `architecture/` as they are written) describe what actually shipped. The inspiration doc is the baseline constraints file, not the final manual. [Philosophical soft guidance](philosophical-soft-guidance.md) records research lineage as **influence on judgment**, not as deliverables. [Context meal composition](design-context-meal-composition.md) describes labeled packages and slide-off — percentages remain provisional and flexible under test.

Other principle reminders:

- **Modular packages** — `elyra/memory/` (and submodules); do-loop/presence stay orchestrators.
- **Small units, explicit scope** — parse / compute / persist.
- **Tests are part of the feature** — Phase 3 needs synthetic evaluation, not vibes.
- **Stretch discipline** — no hypergraph or success-path machinery smuggled into Phase 1.
- **Config defaults** — data under `ELYRA_HOME`; few new env vars.
- **Portability** — Nemotron on CUDA / ROCm / CPU fallback; no single-GPU hard dependency in core imports.

---

## Memory regimes

| Regime | Role in context meal | Primary structures |
|--------|----------------------|--------------------|
| **Current temporal** | Open moment (group of atoms) | Sequential atoms inside the active moment |
| **Broader episodic** | Prior moments + ladder summaries as relevant | Period summaries; other moments |
| **Semantic** | Supporting (“reminds me of”) | Nemotron multi-embeddings; bonded channels; parcels; ANN |
| **Procedural** | Supporting process prior | Trajectories; success-weighted edges |

**Phase 2a — Directed traversal:** model-managed walk; **temporary** until confirmed; keeps enter meal via directed-keep channel only.

Composition, dedup, labels, slide-off, re-gather: [design-context-meal-composition.md](design-context-meal-composition.md).

---

## Phase overview

| Phase | Focus | Care |
|-------|--------|------|
| **1** | Temporal / episodic foundation + meal spine | Must stand alone without vectors |
| **2** | Semantic / Nemotron / ANN | Hardware portability + index freshness policy |
| **2a** | Directed traversal | Temporary context hygiene |
| **3** | Procedural / success-path | Evaluation plan + synthetic data before default-on |

---

## Storage direction (summary)

Preliminary choice: **LanceDB** for atoms, embeddings, and ANN; **lance-graph** for optional Cypher over the same tables; **reified hyperedges**. Full rationale and limitations: [design-database-choices.md](design-database-choices.md).

---

## Document map

| Document | Role |
|----------|------|
| [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md) | **Baseline inspiration** — activities, logical data prototype, storage requirements, doc obligations |
| [philosophical-soft-guidance.md](philosophical-soft-guidance.md) | **Soft guidance** — IIT/sheaf/holographic and reconstructive influences; not goals |
| [design-context-meal-composition.md](design-context-meal-composition.md) | **Provisional meal** — open moment vs episodic, slide-off, labels, dedup, re-gather |
| [design-database-choices.md](design-database-choices.md) | Storage decision, limitations, ANN policy, interface rule |
| [design-nemotron-runtime.md](design-nemotron-runtime.md) | Portable Omni-Embed-Nemotron load/encode contract (Phase 2) |
| [design-phase-1-temporal.md](design-phase-1-temporal.md) | Phase 1 design |
| [design-phase-2-semantic.md](design-phase-2-semantic.md) | Phase 2 design |
| [design-phase-2a-directed-traversal.md](design-phase-2a-directed-traversal.md) | Phase 2a design |
| [design-phase-3-procedural.md](design-phase-3-procedural.md) | Phase 3 design |
| `architecture/` (to be created as phases ship) | **Detailed post-implement manuals** mapping code ↔ philosophy |

All Stretch 2 planning docs live under **`docs/stretch-2/`** only.

---

## Definition of done (per phase)

In addition to engineering-principles “done”:

- [ ] Behaviour implemented and tested for that phase only
- [ ] Public memory APIs minimal and documented
- [ ] **Concept-mapping architecture note** written or updated (structures ↔ essay)
- [ ] Activity list updated (which §3 activities are now live)
- [ ] No dependency on later phases for correctness
- [ ] Operator-visible failure modes documented

Philosophical soft guidance is **not** a checklist item for phase done. Meal composition percentages stay tunable; Phase 1 done means temporal/episodic package + slide-off path exist, not final budget ratios.

---

## Working rules

1. Design docs before substantial implementation PRs for that phase.
2. Small, reversible PR stacks.
3. Background consolidation and index optimize never starve the do-loop.
4. Promote to `main` only after operator sign-off and live smoke.
5. Prefer clarifying the philosophy mapping over clever storage tricks that obscure it.
6. Use [philosophical-soft-guidance.md](philosophical-soft-guidance.md) for judgment calls; do not expand phase scope from it.
7. Treat [design-context-meal-composition.md](design-context-meal-composition.md) as the living sketch for meal budgets; refine with measurements and flex.

---

## Next steps

1. Keep Phase 1 design sharp against the inspiration data prototype and meal composition sketch.
2. Run storage spikes listed in `design-database-choices.md`.
3. Spike Nemotron runtime checklist before Phase 2 default-on.
4. Begin Phase 1 implementation only with store interface + tests + initial architecture note skeleton.
