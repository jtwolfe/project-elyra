# Phase 1 — Temporal / Episodic Memory

**Status:** Design draft
**Branch:** `grok-improvement-memory`
**Philosophy:** [memory-atoms.pdf](../memory-atoms.pdf)
**Baseline:** [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md)

## Goal

Replace (or evolve) the sliding meal / context window with a durable episodic structure:

- **Moments** contain **atoms** (instances, not abstracted facts).
- Atoms are linked **sequentially** in time.
- A **rolling ladder of summary atoms** consolidates experience at fixed scales: 15m, 1h, 6h, 1d, 1w, 1m.

This is the essay’s temporal scaffold and consolidation idea in product form. It supplies **primary temporal context** for the do-loop and must work **without** embeddings or success-path logic.

## Non-goals

- Nemotron / vector indexes
- Success-path weighting
- Directed multi-hop traversal product
- Native hyperedges (sequential + period membership only)

## Concept mapping (required in architecture manual when implemented)

| Essay / planning term | Phase 1 structure |
|----------------------|-------------------|
| Memory atom | `Atom` record (content + time + moment + sequential links) |
| Context (time) | `t_start` / `t_end`, moment membership, period windows |
| Consolidation | Period summary atoms refreshed up the ladder |
| Weave (temporal only) | `prev_atom_id` / `next_atom_id`; later phases add edge table types |
| Warehouse anti-pattern | Do not collapse many instances into one “fact row” |

## Core concepts

### Atom

Minimum fields for Phase 1: id, timestamps, content ref, moment id, prev/next, kind. Stubs allowed for embedding status and qualia.

### Moment

Existing do-loop container; relationship to atoms made explicit and queryable.

### Summary atom

Special atom (or flagged kind) whose body consolidates child atoms / lower-scale summaries for a window. First-class and later embeddable.

### Rolling update rule

On period boundaries (or timers): collect children → produce summary → store/replace → preserve temporal containment. Prefer presence timers / rest over a second scheduler.

## Module boundaries

```text
elyra/memory/
  types.py      # pure data
  store.py      # persistence interface
  temporal.py   # sequential + range queries
  ladder.py     # summary refresh
  meal.py       # temporal context package for the do-loop
```

`loop/context.py` consumes the meal package; it does not own storage.

## Success criteria

- [ ] Atoms created and sequentially linked inside moments
- [ ] Ladder refreshes at least at fine scales in tests
- [ ] Temporal context package usable by do-loop (drop-in path)
- [ ] Feature flag or clean fallback if store unavailable
- [ ] Unit + integration tests; **no Nemotron dependency**
- [ ] Architecture note: structure map + activity map for Phase 1 activities

## Open questions

- Summary generation: template-first vs LLM-first
- Pruning/archival of fine-scale atoms under coarse summaries
- Backfill of historical moments into atoms
