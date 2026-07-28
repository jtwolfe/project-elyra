# Phase 2a — Directed Traversal

**Status:** Design draft
**Depends on:** Phase 1 + Phase 2 seeds
**Baseline:** [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md)

## Goal

Realise **model-managed retrieval**: walk the weave around semantic/temporal seeds, inspect neighbours, and **keep** only what should enter durable context.

Critical invariant from planning: traversal material is **temporary** until explicitly selected. Temporary atoms must not flow into period-summary consolidation as ordinary experience.

## Concept mapping

| Essay / planning term | Phase 2a structure |
|----------------------|-------------------|
| Weave / connections | Edge (and reified hyperedge) neighbourhood queries |
| Active use of memory | Directed expand + keep tool/API |
| Context hygiene | Temporary flag + discard on abandon/timeout |

## Care points

- Budgets: max depth, max nodes, max tokens
- Observability: what was considered vs kept (glass/logs)
- Graph access behind `elyra/memory/graph.py` (Python walk and/or lance-graph Cypher)

## Success criteria

- [ ] Expand → review → keep/discard cycle tested
- [ ] Temporary context cannot enter summary refresh
- [ ] Meal labels distinguish candidate vs selected
- [ ] Architecture note: traversal invariants + activity map
