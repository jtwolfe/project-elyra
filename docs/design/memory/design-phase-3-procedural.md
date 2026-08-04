# Phase 3 — Procedural / Success-Path Memory

**Class:** DESIGN
**Status:** Design draft (highest care)
**Depends on:** Phases 1–2a stable enough to record real trajectories
**Baseline:** [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md)
**Meal channel:** [design-context-meal-composition.md](design-context-meal-composition.md) (procedural prior, small share)

## Goal

Record **goal → outcome** trajectories. When a similar goal is later solved more efficiently while prior pathway material was in context, **up-weight** participating edges inside the relevant semantic + episodic subspace. That weighted weave is **process context** — the derived traversal prior discussed in planning (not a second vector ANN index).

Aligns with the essay’s edge-strength dynamics and learning-from-patterns ideas: procedures are pathways over instances, not stored skill blobs detached from experience.

In the meal, procedural material is a **small supporting channel**, never a replacement for the open moment.

## Why delicate

- Wrong weights create self-reinforcing bad procedures
- Success labels are noisy in an open agent
- Requires synthetic/evaluation harness before default-on continuous updates

## Concept mapping

| Essay / planning term | Phase 3 structure |
|----------------------|-------------------|
| Edge strength / use | `weight` updates on success edges |
| Causal / procedural connection | `edge_type=success` (and related) + trajectories |
| Patterns over atoms | Pathways retrieved as process context |
| Qualia (future) | Optional modulator; not required for first landing |

## Required before implementation claims success

1. Testing plan and metrics (efficiency, subspace locality, non-collapse)
2. Synthetic or curated trajectory datasets
3. Feature flag / shadow mode for weight updates
4. Architecture manual section on update rules and failure modes

## Success criteria (high bar)

- [ ] Trajectories recorded
- [ ] Online updates cheap and scoped
- [ ] Eval report on synthetic workloads reviewed
- [ ] Process context optional and budgeted in meal/traversal
- [ ] Explicit go/no-go before default continuous operation
