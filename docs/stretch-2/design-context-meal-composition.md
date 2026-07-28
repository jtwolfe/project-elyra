# Design: Context meal composition & in-moment slide-off

**Status:** Provisional design (percentages illustrative, not normative)
**Branch:** `grok-improvement-memory`
**Related:** [design-phase-1-temporal.md](design-phase-1-temporal.md), [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md), [philosophical-soft-guidance.md](philosophical-soft-guidance.md)

## Purpose

Describe how the **context meal** for a model call is assembled once atomized memory exists, and how **slide-off** manages growth *inside* an open moment without deleting durable memory.

Stretch 1 used a sliding recent window. Stretch 2 evolves that into a **composed, labeled package**: temporal spine plus supporting channels, deduplicated, with retrieval provenance at least partially visible to the model.

This document is for **in-implementation reasoning and tuning**. Channel shares below are a starting sketch — measure and adjust; do not hard-code them as product law.

---

## Principles

1. **Temporal spine first** — current moment, recent sequential atoms, and near-scale period summaries dominate so “when” is not lost.
2. **Supporting channels add; they do not replace** — semantic, broader episodic, procedural, and directed-keep ride on top of the spine.
3. **Deduplicate** — the same atom must not appear multiple times via different channels.
4. **Label sources** — the model should see *why* a block is present (`temporal`, `semantic`, `episodic-summary`, `procedural`, `directed-keep`, `orient`, etc.).
5. **Slide-off is meal management, not forgetting** — durable atoms stay in the store; only the working set for the call shrinks or folds.
6. **Under pressure, cut supports before the spine** — protect recent in-moment tail and temporal package.

---

## Illustrative channel mix (non-normative)

Token shares of the *memory-related* portion of the meal (excluding thin system instructions). Adjust with real budgets (e.g. ~50k meal under a larger model window).

| Channel | Illustrative share | What it is | When it appears |
|---------|-------------------|------------|-----------------|
| **Current temporal** | ~40–50% | Open moment beats/atoms, sequential neighbours, near ladder summaries (e.g. 15m / 1h) | Phase 1+ |
| **Semantic** | ~10–15% | ANN / similarity neighbours (bonded channels as available) | Phase 2+ |
| **Broader episodic** | ~10% | Coarser ladder summaries (6h → 1m) for horizon | Phase 1+ |
| **Procedural** | ~5–10% | Success-path / process prior in subspace | Phase 3+ |
| **Directed-keep / curated** | ~10% | Model-selected keeps from traversal (durable only after confirm) | Phase 2a+ |
| **Orient / self / goals** | fixed residual | Identity, active goals, thin orient | Always |

Phase 1 ships temporal + orient only; empty channels are simply omitted, not zero-filled with noise.

---

## Construction flow

```mermaid
flowchart TD
  subgraph always [Always]
    S[Thin system + orient / self / goals]
  end

  subgraph temporal [Temporal spine]
    M[Open moment working beats]
    Seq[Recent sequential atoms]
    Near[Near period summaries]
  end

  subgraph support [Supporting — as phases land]
    Sem[Semantic neighbours]
    Epi[Broader episodic summaries]
    Proc[Procedural prior]
    Keep[Directed-keep set]
  end

  Store[(Memory store)] --> Seq
  Store --> Near
  Store --> Sem
  Store --> Epi
  Store --> Proc
  Trav[Traversal session] -->|confirmed keeps only| Keep
  Moment[Open moment] --> M

  S --> Merge[Dedup by atom id / content key]
  M --> Merge
  Seq --> Merge
  Near --> Merge
  Sem --> Merge
  Epi --> Merge
  Proc --> Merge
  Keep --> Merge

  Merge --> Budget[Apply token budgets — protect spine]
  Budget --> Labels[Section labels for model]
  Labels --> Meal[Context meal]
  Meal --> LLM[Model call]
```

### Dedup

- Prefer a single inclusion per `atom_id` (or stable content key for non-atom beats).
- If an atom qualifies for multiple channels, keep **one** copy and optionally note secondary reasons in the label (e.g. `temporal+semantic`) rather than repeating body text.
- Directed-keep that is already in the temporal spine should not be pasted twice.

### Source labels

Lightweight, stable section markers the model can learn, for example:

```text
[context:temporal/moment]
...
[context:temporal/summary 1h]
...
[context:semantic]
...
[context:directed-keep]
```

Exact markup is an implementation choice; clarity to the model matters more than format fashion.

---

## In-moment slide-off

As a **moment** (one do-loop) grows, the meal can exceed budget even when durable memory is stable. Slide-off manages the **in-moment working set**.

```mermaid
flowchart LR
  A[Moment grows — new beats / tool turns] --> B{Over meal budget?}
  B -->|no| C[Append to working set]
  B -->|yes| D[Dedup against memory package already in meal]
  D --> E[Rank in-moment turns for retention]
  E --> F[Slide off oldest low-value detail]
  F --> G[Optional compact summary of slid-off span]
  G --> H[Retain: recent tail + critical tool results + labeled memory package]
  H --> C
```

### Retention bias (in-moment)

Prefer to keep:

- Latest user/operator intent
- Recent assistant commitments
- Failed/successful tool results still relevant to the open goal
- Anything already promoted into durable atoms (referenced by id, not full replay)

Prefer to slide off:

- Verbose intermediate tool noise already reflected in a later summary or atom
- Repeated identical errors
- Early exploration that was superseded

### What slide-off must not do

- Delete or rewrite durable atoms in the store
- Drop the labeled temporal memory package before in-moment noise
- Promote temporary traversal candidates into durable context
- Silently merge inferred gaps into memory as facts

### Optional compact of slid-off span

When a contiguous early span slides off, a short **in-moment compact summary** may replace it inside the meal only. That summary is working-set glue, not a period-ladder summary atom (unless a separate consolidation path deliberately writes one).

---

## Relation to external `/compact` patterns

Agent CLIs (including Grok Build’s auto-compact / recap-style flows, and similar `/compact` commands elsewhere) typically:

- summarize older transcript,
- keep a recent tail,
- rehydrate critical instructions or skills.

**Elyra mapping:** treat that as inspiration for **in-moment slide-off + optional compact**, then **re-inject** the structured, labeled memory package (spine + supports). Do not replace the whole meal with one opaque narrative that erases provenance.

**Follow-up:** when integrating Grok Build, inspect local compact behaviour (trigger threshold, preservation rules, prompt) and record concrete lessons here or in an architecture note. Public notes indicate auto-compact, `/recap`/`/summarize`, and iterative compaction prompt tuning — useful parallels, not a spec to copy blindly.

---

## Phase rollout

| Phase | Meal behaviour |
|-------|----------------|
| **1** | Temporal spine + orient; slide-off of in-moment working set; drop-in replacement path for current sliding meal |
| **2** | Add semantic channel; extend dedup across temporal+semantic |
| **2a** | Add directed-keep channel; temporary traversal buffer never enters meal as durable unlabeled history |
| **3** | Add procedural prior; keep share small and scoped |

`elyra/memory/meal.py` (or equivalent) should own composition; `loop/context.py` consumes the package.

---

## Open questions

- Exact default percentages under the live meal token budget
- Trigger: token threshold vs turn count vs both
- Whether compact of slid-off spans is template-only or LLM-assisted
- How glass/UI surfaces meal composition for the operator
- How much secondary-reason labeling (`temporal+semantic`) helps vs clutters
- Lessons from local Grok Build compact source when available

---

## Non-goals for this design note

- Fixed universal percentages
- Implementing Φ-based ranking of meal sections
- Automatic promotion of model inferences into durable atoms during compact

---

*Provisional. Refine with spikes and live token accounting; keep temporal-first and labeled.*
