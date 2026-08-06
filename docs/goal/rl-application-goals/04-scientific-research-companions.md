# Scientific and research companions

| Field | Value |
|-------|--------|
| **Class** | GOAL |
| **Status** | Draft |
| **Audience** | Product |
| **Normative?** | No — aspirational application goal |
| **Related** | [00-overview.md](00-overview.md), [07-shared-requirements-and-benchmarks.md](07-shared-requirements-and-benchmarks.md), [stretch-2-north-star.md](../stretch-2-north-star.md) |

## Purpose

Describe lab and research agents that treat experiments, protocols, and results as structured experience — so past work remains traversable and procedural knowledge can improve under scientist oversight.

## Goals

- Long-horizon experimental memory: what was tried, under what conditions, with what outcome (including negative results).
- Recognition of relevant past failures or partial successes when a new problem appears.
- Protocols as skills; results and methodological links as edges in a growing fabric.
- Phase-level summaries via the ladder without erasing primary experimental atoms.
- Scientist-in-the-loop improvement of procedures, not silent self-modification of lab practice.

## Why this architecture is a fit

| Elyra mechanism | Research mapping |
|-----------------|------------------|
| Moments | Experimental runs and analysis episodes |
| Edges | Causal, methodological, and “inspired-by” links |
| Ladders | Research phases and programme chapters |
| Skills / tools | Protocols, instrument interfaces, analysis playbooks |
| Directed keep | Pinned critical results and safety constraints |
| Goals ledger | Open questions and experimental backlog |
| Draft → promote | Careful promotion of methodological self-knowledge |

## Architectural changes / extensions required

1. **Richer edge vocabulary** for experiment linkage (supports, contradicts, replicates, same_protocol, uses_instrument).
2. **Versioned experimental moments** — stable IDs for runs; clear promote rules for derived analyses.
3. **Negative-result honesty** — meal and traverse must surface failures, not only successes.
4. **Protocol-as-skill discipline** — create/verify/promote path for lab procedures aligned with existing tool/skill law.
5. **Optional structured result attachments** — atoms that reference datasets or instrument logs without dumping unbounded blobs into the meal.
6. **Collaboration multi-user** — lab members as distinct others; shared project goals with role-aware access.

## Benchmarks / success bars

- Directed traversal retrieves a relevant past experiment (including a negative result) for a new question, with atom IDs and edge kinds visible.
- Ladder summary of a research phase is acceptable to the scientist without loss of primary run atoms.
- Repeated failed approach rate drops on a defined problem class after the agent has accumulated prior runs (measured under scientist oversight).
- Protocol skill changes require verify/promote; no silent mutation of safety-critical steps.
- Multi-researcher use does not conflate who ran which experiment.

## Dependencies on near-horizon polish

- Edges creation + quantification dogfood (C14).
- Meal/traverse reliability (C13).
- Phase 3 procedural memory remains experimental until explicitly polished (C11 → v0.2).
- Multi-user identity for lab teams (C12).

## Non-goals (this document)

- Replacing LIMS, ELN, or regulatory data systems.
- Autonomous experiment execution without human gate.
- Claiming statistical validity from agent memory alone.
- Full multimodal instrument pipelines (separate capability work).
