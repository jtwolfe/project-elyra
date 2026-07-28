# Phase 1 — Temporal / Episodic Memory

**Status:** Design draft
**Branch:** `grok-improvement-memory`
**Philosophy:** [memory-atoms.pdf](../memory-atoms.pdf)
**Baseline:** [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md)
**Meal composition:** [design-context-meal-composition.md](design-context-meal-composition.md)

## Goal

Replace (or evolve) the sliding meal / context window with a durable episodic structure:

- **Moments** contain **atoms** (instances, not abstracted facts).
- Atoms are linked **sequentially** in time.
- A **rolling ladder of summary atoms** consolidates experience at fixed scales: 15m, 1h, 6h, 1d, 1w, 1m.

This is the essay’s temporal scaffold and consolidation idea in product form. It supplies **primary temporal context** for the do-loop and must work **without** embeddings or success-path logic.

Phase 1 is also the first landing of **meal composition**: a labeled temporal package plus **in-moment slide-off** under budget pressure. Supporting channels (semantic, procedural, directed-keep) are omitted until later phases; see [design-context-meal-composition.md](design-context-meal-composition.md).

## Non-goals

- Nemotron / vector indexes
- Success-path weighting
- Directed multi-hop traversal product
- Native hyperedges (sequential + period membership only)
- Final frozen token percentages for all future channels

## Concept mapping (required in architecture manual when implemented)

| Essay / planning term | Phase 1 structure |
|----------------------|-------------------|
| Memory atom | `Atom` record (content + time + moment + sequential links) |
| Context (time) | `t_start` / `t_end`, moment membership, period windows |
| Consolidation | Period summary atoms refreshed up the ladder |
| Weave (temporal only) | `prev_atom_id` / `next_atom_id`; later phases add edge table types |
| Warehouse anti-pattern | Do not collapse many instances into one “fact row” |
| Working context vs durable memory | Meal + slide-off vs store; slide-off does not delete atoms |

## Core concepts

### Atom

Minimum fields for Phase 1: id, timestamps, content ref, moment id, prev/next, kind. Stubs allowed for embedding status and qualia.

### Moment

Existing do-loop container; relationship to atoms made explicit and queryable.

### Summary atom

Special atom (or flagged kind) whose body consolidates child atoms / lower-scale summaries for a window. First-class and later embeddable.

### Rolling update rule

On period boundaries (or timers): collect children → produce summary → store/replace → preserve temporal containment. Prefer presence timers / rest over a second scheduler.

### Temporal meal package + slide-off

- Build a **labeled** temporal section (moment working set, sequential neighbours, near summaries) for `loop/context.py`.
- When the open moment exceeds budget, **slide off** oldest low-value in-moment detail; optionally replace with a short in-moment compact summary.
- Do not delete durable atoms as part of slide-off.
- Dedup within the temporal package (same atom once).

Details and diagrams: [design-context-meal-composition.md](design-context-meal-composition.md).

## Module boundaries

```text
elyra/memory/
  types.py      # pure data
  store.py      # persistence interface
  temporal.py   # sequential + range queries
  ladder.py     # summary refresh
  meal.py       # compose temporal (later multi-channel) package + slide-off helpers
```

`loop/context.py` consumes the meal package; it does not own storage.

## Success criteria

- [ ] Atoms created and sequentially linked inside moments
- [ ] Ladder refreshes at least at fine scales in tests
- [ ] Temporal context package usable by do-loop (drop-in path)
- [ ] In-moment slide-off under budget without deleting durable atoms
- [ ] Feature flag or clean fallback if store unavailable
- [ ] Unit + integration tests; **no Nemotron dependency**
- [ ] Architecture note: structure map + activity map for Phase 1 activities

## Open questions

- Summary generation: template-first vs LLM-first
- Pruning/archival of fine-scale atoms under coarse summaries
- Backfill of historical moments into atoms
- Default meal token budget and slide-off trigger (tokens vs turns)
- Whether in-moment compact summaries are template-only or LLM-assisted
