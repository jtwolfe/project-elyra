# Philosophical soft guidance — influences on memory design

**Status:** Soft guidance only (not a phase plan, not acceptance criteria)
**Branch:** `grok-improvement-memory`
**Primary philosophy:** [memory-atoms.pdf](../memory-atoms.pdf) — *What is wrong with my memory?*
**Related:** [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md)

This note records **conceptual influences** that shaped how we think about atomized memory. They are tools for *reasoning about form* while implementing — ways to ask “does this structure still honour the essay?” — not a backlog of features.

**Do not** treat anything here as a Stretch 2 deliverable, runtime algorithm, or definition of done.

---

## How to use this document

| Use | Avoid |
|-----|--------|
| When choosing between two shapes of a link, summary, or retrieval package, prefer the one that keeps instances + context reconstructable | Adding Φ fields, sheaf engines, or holographic algebra to the schema “because the philosophy said so” |
| When writing architecture notes, name the influence if it clarifies intent | Turning soft guidance into phase goals or tests |
| When a design feels like a fact warehouse, re-read the essay + § below | Expanding scope mid-phase to chase a metaphor |

Stretch 2 phases remain: temporal → semantic → directed traversal → procedural. This file does not add a phase.

---

## 1. Human memory (background orientation)

Cognitive science broadly supports the essay’s stance more than a filing-cabinet model:

- **Multiple systems** — episodic (events in time), semantic (generalized knowledge), procedural (how-to pathways), plus limited working memory.
- **Constructive recall** — remembering is reconstruction, not pure playback; gaps are often filled in schema-consistent ways (classically Bartlett).
- **Consolidation** — finer traces can settle into more stable, coarser structure over time (biological sleep/rest; our period-summary ladder is an engineering cousin).
- **Spreading activation** — what comes to mind is biased by link structure, strength, and recency.
- **Distributed representation** — useful memories are rarely a single local slot; partial loss need not erase all reconstructability.

**Soft guidance:** Prefer designs where a retrieved *set* of atoms and links can support reconstruction, not only a single best-matching string. Prefer keeping instances available under summaries rather than replacing experience with one compressed “fact.”

---

## 2. Integrated Information Theory (IIT) and Φ

### Influence (historical)

Early explorations considered Φ-like integration as a weight on nodes or edges in a hypergraph — a measure of how much a next hop or neighbourhood was “more than the sum of its parts.”

### What we keep

The **intuition of integration**: some patches of memory are tightly interwoven; jointly they constrain interpretation more than isolated atoms. “What is absent” between two observations can matter as much as what is stored.

### What we do not keep as design path

- Runtime Φ on every atom or edge.
- IIT as a theory of machine consciousness we must implement.
- Stretch 2 work items to compute formal Φ (combinatorially heavy and contested even in neuroscience).

### Soft guidance

- Prefer **success / efficiency / use-based** edge weights for procedural learning (testable, local, cheap).
- If, *much later*, a health or traversal metric is desired, think of a **Φ-proxy only over a retrieved context patch** (the current mnemonic subspace): e.g. crude connectivity, redundancy, or “how much does dropping one atom break the story?” — as a **boring health metric** or research probe, not as the primary ranking signal.
- Such a metric is **out of scope for Stretch 2** unless a future explicit decision says otherwise.

---

## 3. Sheaf-theoretic thinking (local patches → coherent whole)

### Influence

Sheaf theory studies how **local data on overlapping regions** can (or cannot) be glued into a **consistent global picture**. Agreement on overlaps is what makes gluing possible; failure of agreement is an obstruction.

In earlier conceptual work, sheaf language (including cohomology as a *proof/exploration* tool) helped probe whether a memory weave could support inference across incomplete experience. **Sheaf cohomology is not an implementation target.**

### What we keep

- **Contextual edges are load-bearing.** When a new atom is created, links to atoms that were in the context that shaped it preserve a local “view.” Those views are the patches.
- **Incomplete experience is normal.** Two patches may sit on either side of a gap (“I don’t have a tool for this” … later … “I used tool X for this”). A model may *propose* a global story consistent with both patches (e.g. that X was obtained or created in between) and then *search* for confirming atoms.
- **Consistency matters more than forced completion.** Reconstruction should remain distinguishable from retrieved memory (epistemic status), when that distinction is cheap to preserve.

### Soft guidance

- When implementing linking rules, bias toward retaining **generation-context / influencer** links, not only sequential time order.
- When implementing retrieval packages, prefer returning a **neighbourhood (patch)** over a lone atom when the extra material is still within budget.
- When implementing traversal or meal assembly, allow the model to notice gaps; do not require the store to materialize every inferred middle as fact automatically.
- Do **not** add a sheaf library, cohomology routines, or “section” types to the schema for Stretch 2.

---

## 4. Holographic / distributed reconstructability

### Influence

Holographic and holonomic metaphors (and, separately, distributed neural representation research) suggest that useful structure is often **spread out**: a sufficient fragment carries traces of a larger whole, and the system tolerates partial loss better than a single-point store.

There is also a loose analogy in **LLM behaviour**: a response often carries **linguistic traces of the context that produced it** (talk of ducks and penguins usually implies context that licensed those topics). That observation is conceptual — it is not a storage algorithm.

### What we keep

- Summaries **compress**; they should not be the only home of an experience.
- Rich local linkage makes reconstruction from partial retrieval more plausible.
- Resilience: losing or omitting one atom should not, by design, erase all access to a cluster of related experience.

### Soft guidance

- Prefer **instance + links + optional summary** over **summary alone**.
- Prefer retrieval that surfaces enough neighbouring structure for a reader (human or model) to see *why* something is relevant.
- Do **not** implement holographic reduced representations, wave-interference encodings, or physics-style holographic duals as part of Stretch 2.

---

## 5. Reconstructive use of memory (concept only)

Human-like use of the weave includes:

1. Retrieve a subspace (atoms + edges), not only top-1 text.
2. Notice temporal or narrative gaps between patches.
3. Propose an intermediate explanation in language.
4. Optionally verify by further search or traversal.
5. Keep “inferred” distinct from “retrieved” when practical.

**Soft guidance:** Structures that make steps 1–2 natural (context edges, sequential order, budgeted neighbourhoods) are aligned with the essay. Automatic writing of inferred atoms into durable memory without checks is *not* implied and should be treated with caution if ever considered later.

---

## 6. Open concepts (named, not scheduled)

These appear in the essay or early design talk; they remain **intentionally soft**:

| Concept | Note |
|---------|------|
| **Felt signal / qualia** | Part of the essay’s atom; stub only until a deliberate later design |
| **Contradiction edges** | Part of a rich weave; not required to land in Phase 1 |
| **Facts as patterns over atoms** | Emergent reading of the graph, not a stored universal row |
| **Φ-proxy patch health** | Possible future diagnostic; not Stretch 2 scope |
| **Epistemic status tags** | Useful if reconstruction becomes common; optional clarity aid |

---

## 7. Relationship to other Stretch 2 docs

| Document | Relationship |
|----------|----------------|
| [memory-atoms.pdf](../memory-atoms.pdf) | Authoritative philosophical essay |
| [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md) | Operational baseline (activities, data prototype, storage needs) |
| [design-context-meal-composition.md](design-context-meal-composition.md) | Product design for labeled meals and slide-off — may *reflect* reconstructive / patch ideas without implementing them as philosophy runtime |
| [design-phase-*.md](README.md) | Concrete phase design; this file does not override them |
| Architecture manuals (future) | May cite this note when explaining *why* a shape was chosen |

If soft guidance and a phase design ever appear to conflict, **phase design and engineering principles win for what ships**; update this note if the influence story needs correction.

---

*Influences for judgment, not a roadmap.*
