# Stretch 2 — Atomized Memory Substrate

**Branch:** `grok-improvement-memory` (from `main`)
**Philosophy:** [memory-atoms.pdf](../../memory-atoms.pdf) — *What is wrong with my memory?*
**Planning baseline:** [inspiration-activity-model-and-storage.md](../../design/memory/inspiration-activity-model-and-storage.md)
**Soft conceptual guidance:** [philosophical-soft-guidance.md](../../stretch-2/philosophical-soft-guidance.md) (influences only — not phase goals)
**Context meal (provisional):** [design-context-meal-composition.md](../../design/memory/design-context-meal-composition.md)

> **Docs reorg (#121):** designs + spikes under [docs/design/memory/](../../design/memory/); this folder is **STATE** memory honesty + architecture manuals (PR4). Full DESIGN catalog: [docs/design/README.md](../../design/README.md).

## Goal

Implement a durable, multi-regime memory substrate aligned with the atomized-memory philosophy: instances (atoms), temporal scaffold, weave of connections, consolidation into higher-scale structure, and later success-weighted pathways — not a warehouse of detached facts.

This substrate later supports Grok Build integration and autonomous self-improvement workflows. Those integrations are **out of scope** until the memory phases are solid.

Stretch 2 is deliberately slow and phased. Phases 2a and 3 require evidence and tuning; Phase 3 is evaluation-first.

---

## Alignment with engineering principles

All of [engineering-principles.md](../../dev/engineering-principles.md) applies. Stretch 2 **extends** them in one critical way:

### Documentation is part of the product surface

For a system this conceptual, “docs updated when behaviour changes” is not enough. Each phase must ship **concept-mapping documentation** that explains:

1. What was implemented (types, tables, APIs, jobs).
2. How those structures map to essay concepts (atom, context, edge kinds, summary/consolidation, trajectory, temporary vs durable context).
3. Which activities from the [inspiration activity model](../../design/memory/inspiration-activity-model-and-storage.md) are live vs background.
4. Invariants and failure behaviour.

Design docs (`design-*.md`) guide implementation. **Architecture manuals** (under `architecture/` as they are written) describe what actually shipped. The inspiration doc is the baseline constraints file, not the final manual. [Philosophical soft guidance](../../stretch-2/philosophical-soft-guidance.md) records research lineage as **influence on judgment**, not as deliverables. [Context meal composition](../../design/memory/design-context-meal-composition.md) describes labeled packages and slide-off — percentages remain provisional and flexible under test.

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

Composition, dedup, labels, slide-off, re-gather: [design-context-meal-composition.md](../../design/memory/design-context-meal-composition.md).

---

## Phase overview

| Phase | Focus | Care | Status |
|-------|--------|------|--------|
| **1** | Temporal / episodic foundation + meal spine | Must stand alone without vectors | **Done** (2026-07-28) — see caveats below |
| **2** | Semantic / Nemotron / vector search | Portability + freshness + product-path honesty | **Code rectified (PR-R1–R5, 2026-07-29)** on ship stack (PR1–PR9); **operator smoke dogfood pending**; flags default **off** — see close-out |
| **2a** | Directed traversal | Temporary context hygiene | **Code shipped (PR-A1–A5, 2026-07-29)**; architecture note PR-A6; **operator smoke dogfood pending**; flags default **off** — see close-out |
| **3** | Procedural / success-path | Evaluation plan + synthetic data before default-on | Planned |

### Phase 2 close-out (updated 2026-07-29)

**Ship stack (PR1–PR9, 2026-07-28):** multi-embeddings (mock + optional Nemotron + Lance `emb_*`), async encode queue + store write hooks + idle drain, hybrid recent-buffer + idle optimize, opt-in parcels, meal **semantic** channel, glass **Vectors** tab, architecture note.

**Product-path rectification (PR-R1–R5, 2026-07-29) — code landed:**

| PR | What |
|----|------|
| **R1** | `auto` channel resolve + single-modality **joint = copy** + eager joint-copy repair |
| **R2** | Meal omit `no_hits` / `deduped` + `semantic_select_meta` |
| **R3** | Safe optimize/rebuild (no IVF on empty; no false `ann_index_built`) |
| **R4** | Lance-native main search; small-N **`full_lance`**; rollback `ann_search_backend=python` |
| **R5** | Vectors glass: channel auto/toggle + honest empty/rebuild UX |
| **R6** | Docs closeout (this README + architecture + known-bugs) |

**Honesty:** execute-plan complete ≠ product target. Pre-rectification dogfood showed empty joint neighbors/meal on text-only corpora (**BUG-mem-p2-01**). Code now matches product intent; **operator smoke dogfood verification is still pending** before claiming Phase 2 product-complete or default-on.

**Defaults stay safe:** `semantic_enabled` / `embed_enabled` / `parcels_enabled` **false**. Durable vectors need `backend=lance` + `elyra[memory-lance]`. JSONL remains hermetic CI (no production ANN).

**Caveats / follow-ups:**

| Topic | Notes |
|-------|--------|
| Operator smoke / full dogfood | Still needed on rectified path (mock → Nemotron ladder) |
| Glass **Vectors** tab | Live + honest (PR7 + PR-R5) — [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) |
| Glass **Graph** tab | **Live (Phase 2a PR-A5)** — [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md); was stub in Phase 2 |
| Nemotron / GPU | Runtime landed (PR8); **BUG-mem-gpu-01** open (ROCm / device); Gate B before product default-on |
| Default-on semantic | Only after rectified dogfood + Gate B + operator sign-off ([design-nemotron-runtime.md](../../design/memory/design-nemotron-runtime.md)) |

**Docs:** [design-phase-2-rectification.md](../../design/memory/design-phase-2-rectification.md) (normative fix plan), [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) (shipped + rectified map), [design-phase-2-implementation.md](../../design/memory/design-phase-2-implementation.md) (historical PR1–PR9), [known-bugs.md](../known-bugs.md) (**BUG-mem-p2-01**, **BUG-mem-gpu-01**).

### Phase 2a close-out (updated 2026-07-29)

**Ship stack (PR-A1–A5, 2026-07-29):** GraphView + weights v1, TraversalSession (budgets + dual sticky snapshots), meal **directed_keep** channel, traverse tools + `memory-traverse` skill, glass **Graph** tab.

**Docs (PR-A6):** [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md) (structure / activity / invariants / failure maps; KD-A1–A19 as shipped).

| PR | What |
|----|------|
| **A1** | `GraphView` neighbourhood, edge projection, weight model v1 |
| **A2** | `TraversalSession` start/step/finish/abandon; idle TTL + expand_ms + steps (no multi-hop wall) |
| **A3** | Meal `directed_keep` + `split_memory_budget_v3` (next `compose_meal` only) |
| **A4** | `memory_traverse_*` tools + `memory-traverse` skill playbook |
| **A5** | Glass Graph tab — considered vs kept, budgets, walk summary |
| **A6** | Architecture note + program docs (this README + sketch concept map) |

**Honesty:** execute-plan complete ≠ product target. **Temporary state is session-only** (no temporary Atom rows). Flags `directed_traversal_enabled` / `directed_keep_enabled` default **off** (OQ-A1: keep follows traversal when traversal is on). **Operator smoke dogfood verification is still pending** before claiming Phase 2a product-complete or default-on. Prefer Phase 2 rectified semantic smoke before rich multi-hop dogfood; structural JSONL walks work without ANN.

**Defaults stay safe:** traversal/keep **false**. Structural walks need an open memory store; soft semantic hops need `backend=lance` + warm encoder (same as Phase 2). JSONL remains hermetic CI structural path.

**Caveats / follow-ups:**

| Topic | Notes |
|-------|--------|
| Operator smoke / full dogfood | Still needed (structural + Lance multi-hop) |
| Meal timing | Glass immediate; outer meal on **next** `compose_meal` only (KD-A16) — skill teaches honesty |
| Restart | Sessions in-process only; sticky keep lost on restart (OQ-A2) |
| Default-on traversal | Only after dogfood + operator sign-off |
| Phase 3 | Success-path weights later — not 2a |

**Docs:** [design-phase-2a-implementation.md](../../design/memory/design-phase-2a-implementation.md) (normative design + KD-A*), [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md) (shipped map), [design-phase-2a-directed-traversal.md](../../design/memory/design-phase-2a-directed-traversal.md) (intent sketch).

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

Preliminary choice: **LanceDB** for atoms, embeddings, and ANN; **lance-graph** for optional Cypher over the same tables; **reified hyperedges**. Full rationale and limitations: [design-database-choices.md](../../design/memory/design-database-choices.md).

---

## Document map

| Document | Role |
|----------|------|
| [inspiration-activity-model-and-storage.md](../../design/memory/inspiration-activity-model-and-storage.md) | **Baseline inspiration** — activities, logical data prototype, storage requirements, doc obligations |
| [philosophical-soft-guidance.md](../../stretch-2/philosophical-soft-guidance.md) | **Soft guidance** — IIT/sheaf/holographic and reconstructive influences; not goals |
| [design-context-meal-composition.md](../../design/memory/design-context-meal-composition.md) | **Provisional meal** — open moment vs episodic, slide-off, labels, dedup, re-gather |
| [design-instance-continuity-glass-tail-directed-keep.md](../../design/memory/design-instance-continuity-glass-tail-directed-keep.md) | **Refined product draft** instance continuity for #93 — glass-tail + sticky directed keep + path parity (Ready for implement plan) |
| [design-instance-continuity-implement-plan.md](../../design/memory/design-instance-continuity-implement-plan.md) | **Implement plan** for #93 — ordered product PRs S1–S6 (glass-tail → framing → sticky keep → merge → nudge → graph UX defer) |
| [`design-instance-continuity-product-implement.md`](../../design/memory/design-instance-continuity-product-implement.md) | **Product implement design** for #93 (glass-tail, framing, sticky keep, semantic seed) — Ready to execute |
| [design-meal-formation-continuity-review-plan.md](../../design/memory/design-meal-formation-continuity-review-plan.md) | **Executable review methodology** (inspection + fault isolation) refining the #93 draft — not product code |
| [meal-continuity-review/REPORT.md](../../investigations/meal-continuity-review/REPORT.md) | **Review report** (S0 done) — fault isolation B1/B12 co-primary; B5+B5b; evidence sa9b |
| [design-database-choices.md](../../design/memory/design-database-choices.md) | Storage decision, limitations, ANN policy, interface rule |
| [design-nemotron-runtime.md](../../design/memory/design-nemotron-runtime.md) | Portable Omni-Embed-Nemotron load/encode contract (Phase 2) |
| [design-phase-1-temporal.md](../../design/memory/design-phase-1-temporal.md) | Phase 1 design (short outline) |
| [design-phase-1-implementation.md](../../design/memory/design-phase-1-implementation.md) | Phase 1 implementation design + key decisions |
| [architecture/phase-1-temporal.md](architecture/phase-1-temporal.md) | **Phase 1 architecture manual** (shipped: structure ↔ essay, activities, invariants) |
| [design-phase-2-semantic.md](../../design/memory/design-phase-2-semantic.md) | Phase 2 short sketch (points at implementation + rectification + architecture) |
| [design-phase-2-implementation.md](../../design/memory/design-phase-2-implementation.md) | **Historical** Phase 2 implementation design + PR plan (PR1–PR9) |
| [design-phase-2-rectification.md](../../design/memory/design-phase-2-rectification.md) | **Phase 2 product-path rectification** design + PR-R1–R6 |
| [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) | **Phase 2 architecture manual** (shipped + rectified: structure ↔ essay, activities, invariants) |
| [design-phase-2a-directed-traversal.md](../../design/memory/design-phase-2a-directed-traversal.md) | Phase 2a intent sketch (points at implementation + architecture) |
| [design-phase-2a-implementation.md](../../design/memory/design-phase-2a-implementation.md) | **Normative** Phase 2a implementation design + PR-A1–A6 |
| [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md) | **Phase 2a architecture manual** (shipped: structure ↔ essay, activities, invariants) |
| [design-phase-3-procedural.md](../../design/memory/design-phase-3-procedural.md) | Phase 3 design |
| `architecture/` | **Detailed post-implement manuals** mapping code ↔ philosophy (Phase 1 + Phase 2 + Phase 2a shipped) |

Memory **designs** live under **`docs/design/memory/`**; **architecture manuals** under **`docs/state/memory/architecture/`**; residual stretch-2 island: philosophical guidance. Meal continuity review: [investigations/meal-continuity-review/](../../investigations/meal-continuity-review/).

---

## Definition of done (per phase)

In addition to engineering-principles “done”:

- [x] Behaviour implemented and tested for that phase only — **Phase 1** + **Phase 2** code + **Phase 2a** code (semantic + traversal flags default off; dogfood opt-in; PR-R1–R5 + PR-A1–A5 hermetic coverage); Phase 3 open
- [x] Public memory APIs minimal and documented — Phase 1 store Protocol + glass inspect; Phase 2 index/embed + meal semantic + Vectors APIs; Phase 2a Graph APIs (`/api/memory/graph*`) + traverse tools
- [x] **Concept-mapping architecture note** written or updated (structures ↔ essay) — Phase 1: [architecture/phase-1-temporal.md](architecture/phase-1-temporal.md); Phase 2: [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md); Phase 2a: [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md)
- [x] Activity list updated (which §3 activities are now live) — Phase 1 + Phase 2 + Phase 2a maps in architecture notes
- [x] No dependency on later phases for correctness — **Phase 1** meal works without vectors; **Phase 2** meal works with semantic off or omitted; **Phase 2a** structural walks work without ANN / with flags off inert
- [x] Operator-visible failure modes documented — Phase 1 + Phase 2 + Phase 2a architecture notes

Philosophical soft guidance is **not** a checklist item for phase done. Meal composition percentages stay tunable; Phase 1 done means temporal/episodic package + slide-off path exist, not final budget ratios. Glass polish and prompt soften are **not** Phase 1 reopen criteria (see close-out caveats). Phase 2 **code** done means semantic path + Vectors glass + rectification stack + architecture note with safe defaults — **not** product default-on and **not** a substitute for operator smoke dogfood. Phase 2a **code** done means directed walk + directed_keep + Graph glass + architecture note with flags **off** — **not** product default-on and **not** a substitute for operator smoke dogfood.

---

## Working rules

1. Design docs before substantial implementation PRs for that phase.
2. Small, reversible PR stacks.
3. Background consolidation and index optimize never starve the do-loop.
4. Promote to `main` only after operator sign-off and live smoke.
5. Prefer clarifying the philosophy mapping over clever storage tricks that obscure it.
6. Use [philosophical-soft-guidance.md](../../stretch-2/philosophical-soft-guidance.md) for judgment calls; do not expand phase scope from it.
7. Treat [design-context-meal-composition.md](../../design/memory/design-context-meal-composition.md) as the living sketch for meal budgets; refine with measurements and flex.

---

## Next steps

1. **Smoke dogfood Phase 2 rectification** (flags on, `backend=lance`): neighbors/meal under `auto`, joint repair completes, rebuild notes honest — [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md), [design-phase-2-rectification.md](../../design/memory/design-phase-2-rectification.md).
2. **Smoke dogfood Phase 2a** (flags on): structural walk on JSONL; full multi-hop with Lance + semantic seeds; finish → glass considered/kept → next meal directed_keep — [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md).
3. **Gate B** checklist (mock → Nemotron quality/latency) before any product default-on of semantic — [design-nemotron-runtime.md](../../design/memory/design-nemotron-runtime.md).
4. **Phase 3** procedural / success-path evaluation-first (uses edges/sessions from 2a).
5. Keep architecture manuals updated when behaviour changes. Do not claim 2a product-complete on empty joint search or without dogfood.
