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

| Phase | Focus | Care | Status |
|-------|--------|------|--------|
| **1** | Temporal / episodic foundation + meal spine | Must stand alone without vectors | **Done** (2026-07-28) — see caveats below |
| **2** | Semantic / Nemotron / ANN | Hardware portability + index freshness policy | **Done / shipped** (2026-07-28) — flags default **off**; see caveats below |
| **2a** | Directed traversal | Temporary context hygiene | Planned |
| **3** | Procedural / success-path | Evaluation plan + synthetic data before default-on | Planned |

### Phase 2 close-out (2026-07-28)

**Operator-complete on execute-plan stack / `grok-improvement-memory` (PR1–PR9):** multi-embeddings (mock + optional Nemotron + Lance `emb_*`), async encode queue + store write hooks + idle drain, ANN hybrid recent-buffer + idle optimize, opt-in parcels, meal **semantic** channel (`select_semantic`, budget v2, timeout omit), glass **Vectors** tab (health + status list + neighbors; `tabs.vectors.stub=false`), architecture note.

**Defaults stay safe:** `semantic_enabled` / `embed_enabled` / `parcels_enabled` **false** until dogfood. Durable ANN needs `backend=lance` + `elyra[memory-lance]`. JSONL remains hermetic CI (no production ANN).

**Caveats / follow-ups (not Phase 2 reopen blockers):**

| Topic | Notes |
|-------|--------|
| Glass **Vectors** tab | **Filled (PR7 / KD18)** — live health, embedding status, neighbor inspect; optional 2D projection non-gate — [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) |
| Glass **Graph** tab | Phase **2a** — out of scope (stub remains) |
| Nemotron runtime | **Landed (PR8)** — real load when deps present; mock fallback when not; Gate B before product default-on |
| Default-on semantic | Only after Gate B spike checklist + operator sign-off ([design-nemotron-runtime.md](design-nemotron-runtime.md)) |

**Docs:** [design-phase-2-implementation.md](design-phase-2-implementation.md) (implementation design), [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) (shipped map).

### Phase 1 close-out (2026-07-28)

**Operator-complete on `grok-improvement-memory`:** atoms, promote, ladder (template), labeled meal + slide-off, defaults `memory.enabled` / `write_atoms` on, optional **Lance** backend (`backend=lance` + `elyra[memory-lance]`), glass **Memory** page (context meal inspector, atoms list, Vectors/Graph stubs), architecture note.

**Not Phase 1 blockers** (tracked in [known-bugs.md](../known-bugs.md); polish after or parallel to Phase 2):

| ID | Topic |
|----|--------|
| BUG-glass-01 / 02 | Moments beautify; move Moments under Memory |
| BUG-mem-ui-01 / 02 / 03 | Context/Atoms beautify; summary-generation review; inspector flash |
| BUG-chat-01 | Chat equation / math rendering |
| BUG-status-01 / 02 / 03 | Status scroll; dev-speed dual control; hard-stop override OFF stickiness |
| BUG-prompt-01 | Soften system prompt walls (**review after memory up** — now eligible, not blocking Phase 2) |
| BUG-wake-01 / BUG-usage-01 | Pre-existing deferrals (wake storms; usage pacing) |

**Still out of Phase 1 by design:** Nemotron / ANN, directed traversal, success-path weights, historical glass→atom backfill, rich Vectors/Graph UIs (stubs only).

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
| [design-phase-1-temporal.md](design-phase-1-temporal.md) | Phase 1 design (short outline) |
| [design-phase-1-implementation.md](design-phase-1-implementation.md) | Phase 1 implementation design + key decisions |
| [architecture/phase-1-temporal.md](architecture/phase-1-temporal.md) | **Phase 1 architecture manual** (shipped: structure ↔ essay, activities, invariants) |
| [design-phase-2-semantic.md](design-phase-2-semantic.md) | Phase 2 short sketch (points at implementation design + architecture note) |
| [design-phase-2-implementation.md](design-phase-2-implementation.md) | **Phase 2 implementation design + PR plan** (shipped stack reference) |
| [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) | **Phase 2 architecture manual** (shipped: structure ↔ essay, activities, invariants) |
| [design-phase-2a-directed-traversal.md](design-phase-2a-directed-traversal.md) | Phase 2a design |
| [design-phase-3-procedural.md](design-phase-3-procedural.md) | Phase 3 design |
| `architecture/` | **Detailed post-implement manuals** mapping code ↔ philosophy (Phase 1 + Phase 2 shipped) |

All Stretch 2 planning docs live under **`docs/stretch-2/`** only.

---

## Definition of done (per phase)

In addition to engineering-principles “done”:

- [x] Behaviour implemented and tested for that phase only — **Phase 1** + **Phase 2** (semantic flags default off; dogfood opt-in); 2a/3 open
- [x] Public memory APIs minimal and documented — Phase 1 store Protocol + glass inspect; Phase 2 index/embed + meal semantic + Vectors APIs (`/api/memory/vectors*`)
- [x] **Concept-mapping architecture note** written or updated (structures ↔ essay) — Phase 1: [architecture/phase-1-temporal.md](architecture/phase-1-temporal.md); Phase 2: [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md)
- [x] Activity list updated (which §3 activities are now live) — Phase 1 + Phase 2 maps in architecture notes
- [x] No dependency on later phases for correctness — **Phase 1** meal works without vectors; **Phase 2** meal works with semantic off or omitted
- [x] Operator-visible failure modes documented — Phase 1 + Phase 2 architecture notes

Philosophical soft guidance is **not** a checklist item for phase done. Meal composition percentages stay tunable; Phase 1 done means temporal/episodic package + slide-off path exist, not final budget ratios. Glass polish and prompt soften are **not** Phase 1 reopen criteria (see close-out caveats). Phase 2 done means semantic path + **Vectors glass gate (KD18)** + architecture note with safe defaults — **not** product default-on and **not** Graph/2a.

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

1. Dogfood Phase 1 with `write_atoms` + `enabled` (defaults on; see [architecture/phase-1-temporal.md](architecture/phase-1-temporal.md)).
2. Dogfood Phase 2 ladder (mock → Nemotron → `semantic_enabled`) with `backend=lance`; flags stay off until Gate B — [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md).
3. Phase 2a directed traversal → fill **Graph** tab; Phase 3 procedural eval-first.
4. Keep architecture manuals updated when behaviour changes.
