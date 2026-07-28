# Phase 1 — Temporal / Episodic Memory

**Status:** Design draft  
**Branch:** `grok-improvement-memory`  
**Depends on:** nothing from later phases (Stretch discipline)

## Goal

Replace (or evolve) the current sliding meal / context window with a durable, multi-scale episodic structure:

- **Moments** contain **atoms**.
- Atoms and moments are linked temporally (sequential + period membership).
- A **rolling ladder of summary atoms** is maintained at fixed scales:
  - 15 minutes
  - 1 hour
  - 6 hours
  - 1 day
  - 1 week
  - 1 month

Every 15 m the current 1 h summary is refreshed from recent material; every 1 h the 6 h summary is refreshed; and so on. This produces a natural-language (and later embeddable) map of “what was happening when”.

This phase supplies the **primary temporal context** for the do-loop and is a drop-in conceptual replacement for the existing sliding window.

## Non-goals (Phase 1)

- No vector indexes or Nemotron calls.
- No success-path / procedural weighting.
- No directed graph traversal beyond simple sequential / period membership.
- No hyperedges yet (can be added as a later extension once the basic ladder is solid).

## Core concepts

### Atom

The indivisible experiential record (content + context + optional felt signal + connections). In Phase 1 an atom at minimum carries:

- unique id
- timestamp (or interval)
- content (text; media refs may be stored but not yet embedded)
- link to its parent moment
- sequential predecessor / successor links
- membership in the period windows that currently contain it

### Moment

A do-loop from wake to stop (already exists). Phase 1 makes the relationship to its atoms explicit and durable.

### Summary atom

A special class of atom that is the consolidated natural-language (or structured) description of all atoms (and lower-scale summaries) whose timestamps fall inside a given period window. Summary atoms are first-class and can themselves be linked and later embedded.

### Rolling update rule

On each period boundary (or on a timer):

1. Collect child material (raw atoms or lower-scale summaries) whose time intervals are contained in the parent window.
2. Produce a fresh summary (LLM call or deterministic template + LLM polish — exact mechanism TBD in implementation plan).
3. Store / replace the summary atom for that scale and window.
4. Maintain temporal containment: parent interval ⊇ child intervals.

Inspired by systems such as TiMem (Temporal Memory Tree) but kept deliberately simple for Phase 1.

## Data model sketch (Phase 1 only)

```text
Atom
  id, t_start, t_end?
  content_ref or inline text
  moment_id
  prev_atom_id, next_atom_id
  period_memberships: list of (scale, window_id)

SummaryAtom (subtype or flag)
  scale: 15m | 1h | 6h | 1d | 1w | 1m
  window_start, window_end
  summary_text
  child_ids (atoms or lower summaries)

Moment
  existing fields + explicit atom_ids list or queryable by moment_id
```

Storage can begin as append-only files + simple indexes under `ELYRA_HOME/data/memory/` or a lightweight DB chosen in the database design doc. Prefer the simplest substrate that supports temporal range queries and sequential walks.

## Integration points

- **Context construction:** Current temporal context is assembled from the active moment’s recent atoms + the relevant summary atoms that cover “now” and the recent past.
- **Drop-in behaviour:** Existing code that reads the sliding window should be able to consume the new temporal package with minimal change; old window can remain as a fallback during migration.
- **Consolidation timing:** Use presence timers / rest concepts so summary refreshes do not contend with the live do-loop.

## Success criteria (Phase 1)

- [ ] Atoms are created for beats / messages and linked sequentially inside a moment.
- [ ] Rolling summaries at the six scales are produced and updated on schedule.
- [ ] Temporal context package can be retrieved for a given “now” and is usable by the do-loop.
- [ ] Unit + integration tests cover creation, linking, summary refresh, and retrieval.
- [ ] No dependency on Nemotron or procedural logic.
- [ ] Documentation and config defaults land with the code.

## Open questions for detailed design

- Exact summary generation prompt / mechanism (deterministic first vs LLM-first).
- How aggressively to prune or archive very old fine-scale atoms once coarser summaries exist.
- Whether summary atoms are written into the same store as ordinary atoms or a parallel collection.
- Migration path from the current sliding meal implementation.

## Implementation notes

Keep the package boundary clean (`elyra/memory/temporal/` or similar). Public API should be small: `record_atom`, `get_temporal_context(now)`, `refresh_summaries(scale)`. Everything else stays private.
