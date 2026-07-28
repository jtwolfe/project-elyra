# Phase 3 — Procedural / Success-Path Memory

**Status:** Design draft (highest care)  
**Depends on:** Phases 1, 2, 2a stable and tuned

## Goal

Record trajectories from goal-like atoms to outcome-like atoms. When a later similar trajectory succeeds *more efficiently* while earlier pathway material was in context, up-weight the edges that participated. This creates a derived, continuously learnable traversal prior (the “ANN” layer discussed earlier) that is local to semantic + episodic subspaces.

This supplies **process context**.

## Why this phase is delicate

- Incorrect weighting can create feedback loops or suppress useful exploration.
- Continuous online updates must remain cheap and reversible.
- Evaluation requires synthetic or carefully curated trajectory datasets that exercise both success and failure, efficiency gains, and subspace locality.
- “ANN” behaviour must be measurable (retrieval quality, path efficiency over time) before it is trusted in the live do-loop.

## Required before implementation

1. Detailed testing plan.
2. Synthetic dataset strategy (generate or acquire).
3. Clear metrics for “efficiency gain” and “subspace similarity”.
4. Offline / background update path that cannot starve the presence loop.

## Success criteria (high bar)

- [ ] Trajectories can be recorded and linked to goals/outcomes.
- [ ] Online weight updates are correct and cheap.
- [ ] Derived prior improves path efficiency on held-out synthetic workloads without harming unrelated subspaces.
- [ ] Full test suite + evaluation report reviewed before enabling in default continuous operation.
