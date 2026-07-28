# Phase 1 — Temporal / Episodic Memory

**Status:** **Done** (2026-07-28) — PR1–PR9 on `grok-improvement-memory`; `enabled`/`write_atoms` default on; optional Lance; glass Memory page. Deferred polish in [known-bugs.md](../known-bugs.md). See [architecture/phase-1-temporal.md](architecture/phase-1-temporal.md) and [README.md](README.md) Phase 1 close-out.
**Branch:** `grok-improvement-memory`
**Philosophy:** [memory-atoms.pdf](../memory-atoms.pdf)
**Baseline:** [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md)
**Meal composition:** [design-context-meal-composition.md](design-context-meal-composition.md)
**Architecture (shipped):** [architecture/phase-1-temporal.md](architecture/phase-1-temporal.md)
**Implementation design:** [design-phase-1-implementation.md](design-phase-1-implementation.md)

## Goal

Replace (or evolve) the sliding meal / context window with a durable episodic structure:

- **Moments** are **groups of atoms** bound to a do-loop / presence interval.
- Atoms are linked **sequentially** in time.
- A **rolling ladder of summary atoms** consolidates experience at fixed scales: 15m, 1h, 6h, 1d, 1w, 1m.

This is the essay’s temporal scaffold and consolidation idea in product form. It supplies **primary temporal context** for the do-loop and must work **without** embeddings or success-path logic.

Phase 1 is also the first landing of **meal composition**:

- **Current temporal** = open moment (its atoms / working material).
- **Broader episodic** = prior moments and summaries, as relevant and as budget allows.
- **Slide-off** under pressure inside a long moment.
- **Re-gather** between moments; optional every *N* hops if a moment runs long.

See [design-context-meal-composition.md](design-context-meal-composition.md).

## Non-goals

- Nemotron / vector indexes ([design-nemotron-runtime.md](design-nemotron-runtime.md) is Phase 2)
- Success-path weighting
- Directed multi-hop traversal product
- Native hyperedges (sequential + period membership only)
- Final frozen token percentages for all future channels

## Concept mapping

**Shipped map:** [architecture/phase-1-temporal.md](architecture/phase-1-temporal.md) (structure map, activity map, invariants, failure modes, glossary, JSONL restart/compaction).

| Essay / planning term | Phase 1 structure |
|----------------------|-------------------|
| Memory atom | `Atom` record (content + time + moment + sequential links) |
| Moment as lived interval | Group of atoms (+ ephemeral beats until promoted) |
| Context (time) | `t_start` / `t_end`, moment membership, period windows |
| Consolidation | Period summary atoms refreshed up the ladder |
| Weave (temporal only) | `prev_atom_id` / `next_atom_id`; later phases add edge table types |
| Warehouse anti-pattern | Do not collapse many instances into one “fact row” |
| Working context vs durable memory | Meal + slide-off vs store; slide-off does not delete atoms |

## Core concepts

### Atom

Minimum fields for Phase 1: id, timestamps, content ref, moment id, prev/next, kind. Stubs allowed for embedding status and qualia.

### Moment

A **group of atoms** for one do-loop. Existing presence/do-loop container; relationship to atoms made explicit and queryable.

### Summary atom

Special atom (or flagged kind) whose body consolidates child atoms / lower-scale summaries for a window. First-class and later embeddable.

### Rolling update rule

On period boundaries (or timers): collect children → produce summary → store/replace → preserve temporal containment. Prefer presence timers / rest over a second scheduler.

### Temporal meal package + slide-off

- Build **labeled** sections: open moment + broader episodic (prior moments / summaries as relevant).
- Re-gather on moment boundaries; consider hop-periodic re-gather for long moments.
- When over budget, **slide off** oldest low-value open-moment detail; optionally replace with a short in-moment compact summary.
- Do not delete durable atoms as part of slide-off.
- Dedup within the package (same atom once).

Details and diagrams: [design-context-meal-composition.md](design-context-meal-composition.md).

## Module boundaries

```text
elyra/memory/
  types.py      # pure data
  store.py      # persistence interface
  temporal.py   # sequential + range queries
  ladder.py     # summary refresh
  meal.py       # compose temporal/episodic (later multi-channel) package + slide-off helpers
```

`loop/context.py` consumes the meal package; it does not own storage.

## Success criteria

- [x] Atoms created and sequentially linked inside moments
- [x] Moments queryable as groups of atoms
- [x] Ladder refreshes at least at fine scales in tests
- [x] Temporal + broader episodic package usable by do-loop (drop-in path)
- [x] In-moment slide-off under budget without deleting durable atoms
- [x] Feature flag or clean fallback if store unavailable
- [x] Unit + integration tests; **no Nemotron dependency**
- [x] Architecture note: structure map + activity map for Phase 1 activities → [architecture/phase-1-temporal.md](architecture/phase-1-temporal.md)

## Open questions

- Summary generation: template-first vs LLM-first
- Pruning/archival of fine-scale atoms under coarse summaries
- Backfill of historical moments into atoms
- Default meal token budget, flex bands, and slide-off trigger
- Default *N* for hop re-gather (or moment-boundary-only at first)
- Whether in-moment compact summaries are template-only or LLM-assisted
