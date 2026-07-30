# Phase 2a — Directed Traversal

**Status:** Intent sketch — **superseded for implementation** by [design-phase-2a-implementation.md](design-phase-2a-implementation.md). **Code shipped** PR-A1–A5 (2026-07-29); architecture: [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md). Flags default **off**; operator dogfood pending.
**Depends on:** Phase 1 + Phase 2 rectified seeds (prefer smoke before full multi-hop dogfood)
**Baseline:** [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md)
**Meal channel:** [design-context-meal-composition.md](design-context-meal-composition.md) (directed-keep)

## Goal

Realise **model-managed retrieval**: walk the weave around semantic/temporal seeds, inspect neighbours, and **keep** only what should enter durable context.

Critical invariant: traversal material is **temporary until confirm**. Temporary state is the **in-process `TraversalSession`** (frontier, considered, scratchpad, budgets) — **not** temporary Atom rows and **not** an atom-level “temporary flag.” Session state must not flow into period-summary consolidation or unlabeled durable meal history.

Confirmed keeps reference **existing durable atoms** and may enter the meal via the **directed-keep** channel only (deduped against open moment, episodic, and semantic).

## Concept mapping

| Essay / planning term | Phase 2a structure (as shipped) |
|----------------------|--------------------------------|
| Weave / connections | `GraphView` neighbourhood over projected edges + soft `semantic_hop` |
| Active use of memory | Skill `memory-traverse` + `memory_traverse_*` tools |
| Temporary candidate buffer | **`TraversalSession` session-only** — no store Atom rows (KD-A1) |
| Keep-set | Ordered durable `atom_id`s confirmed by model/operator |
| Context hygiene | Session status `active` → `confirmed` \| `abandoned` \| `timed_out`; abandon/TTL clear active only |
| Directed-keep meal channel | `MealItem.channel == "directed_keep"`; next `compose_meal` only (KD-A16) |
| Observability | Glass Graph tab: considered vs kept from active else `last_session` (KD-A19) |

## Care points

- Budgets: idle TTL + per-step expand_ms + tool steps (no multi-hop session wall-clock — KD-A18)
- Observability: considered vs kept (glass Graph), budgets spent, walk summary
- Graph access behind `elyra/memory/graph.py` (Python walk authority; lance-graph Cypher not required for 2a)

## Success criteria

- [x] Expand → inspect/preview → keep/discard cycle tested (PR-A1–A4)
- [x] Temporary session cannot enter summary refresh / ladder (session-only KD-A1)
- [x] Meal labels distinguish directed-keep summary + atoms; omit reasons honest
- [x] Architecture note: [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md)
- [ ] Operator smoke dogfood (flags on) before product default-on
