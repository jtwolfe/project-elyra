# Inspiration — Activity Model, Data Prototype & Storage Requirements

**Status:** Foundational inspiration (not the final architecture manual)
**Branch:** `grok-improvement-memory`
**Philosophy source:** [memory-atoms.pdf](../memory-atoms.pdf) — *What is wrong with my memory?*
**Soft conceptual influences:** [philosophical-soft-guidance.md](philosophical-soft-guidance.md) (not requirements)
**Meal composition (provisional):** [design-context-meal-composition.md](design-context-meal-composition.md)

This document records the **preliminary system activity model**, a **logical data prototype**, and **refined database requirements** developed during Stretch 2 planning. It is intentionally an *inspiration* and *constraint* document: implementation work must produce a larger, more detailed set of architecture docs that explain **how concrete structures map back to the concepts in the essay** (atoms, context, felt signal, weave of edges, patterns/shadows, consolidation).

Do not treat this file as the complete design. Treat it as the baseline that later phase manuals must refine, not abandon without reason.

For research lineage and soft judgment aids (IIT/Φ as possible *later* patch-health thinking, sheaf-like local context patches, holographic reconstructability, constructive recall), see [philosophical-soft-guidance.md](philosophical-soft-guidance.md). Those ideas **influence reasoning**; they are not storage requirements and do not turn the lists below into phase commitments beyond what phase design docs state.

---

## 1. Why this exists

Stretch 2 is large enough that code alone will not remain intelligible. Engineering principles already require docs with behaviour changes. For memory, we extend that duty:

> Every implemented phase must ship documentation that names the data structures and operations in the language of the memory-atoms philosophy, and shows the mapping from storage/API to atom, moment, edge, summary, and (later) success-path / qualia ideas.

Future readers should be able to answer:

- What is an *atom* in the running system?
- How does the rolling period ladder relate to episodic structure in the essay?
- Where do semantic “reminds me of” links live, and how do they differ from temporal sequence?
- How is a hyperedge represented if the store is a property graph?
- What activities the presence loop actually performs against storage?

---

## 2. Conceptual anchors (from the essay)

| Essay concept | Planning interpretation for Elyra |
|---------------|-----------------------------------|
| **Memory atom** | Indivisible record of a specific instance: content + context + optional felt/qualia + connections |
| **Not a warehouse of facts** | We do not store “the capital of France”; we store instances and let facts emerge as patterns over linked atoms |
| **Context** | Time, place/state, and the situation of the instance; time is a primary scaffold |
| **Weave** | Typed, weighted edges (temporal, causal/procedural, associative/semantic, contradictory, structural) and hyperedges |
| **Edge strength** | Changes with use; success and efficient re-use strengthen pathways |
| **Consolidation** | Offline/background compression into higher-scale structure (our period-summary ladder) |
| **Multimodal content** | One atom may hold or reference text, image, audio, video in combination |

**Moment (planning sense):** a **group of atoms** bound to a do-loop / presence interval. Context meals are re-gathered between moments; long moments may re-gather supporting slices every *N* hops (see meal composition doc).

**Contextual / generation-context links** (atoms that influenced creation of a new atom) are especially important for reconstructability; see soft guidance on local patches. They appear below as ordinary linking, not as a separate subsystem.

---

## 3. System activity model (preliminary)

Operations the storage layer and memory package must support. Rates are qualitative for an always-on single-worker presence.

### 3.1 Write / ingest (live path)

| Activity | When | Latency posture |
|----------|------|-----------------|
| Create atom from beat / message / tool observation / speak | Each memorable event | Must not stall do-loop |
| Attach media refs (Stretch 1 attachments) | Same | Low |
| Sequential prev/next link | Same | Low |
| Link to contextual influencers when known | Same | Low |
| Write multi-embeddings (per-modality + joint) | After create; may be async | Medium (encode cost) |
| Split oversized content into linked parcels | Large messages | Low for split; embed async |
| Record goal/outcome markers on trajectories | Sparse (Phase 3) | Low |
| Online edge-weight update on efficient success | On confirmed success | Very low |

### 3.2 Temporal / episodic

| Activity | When | Latency posture |
|----------|------|-----------------|
| Time-range query over atoms | Meal build, tools | Low–medium |
| Sequential walk | Meal, traversal | Low |
| Refresh period summary (15m → 1h → … → 1m) | Timers / rest | Background |
| Fetch active ladder summaries for “now” | Meal build | Low |
| Query prior moments for broader episodic fill | Meal build | Low–medium |

### 3.3 Meal composition (working set)

| Activity | When | Latency posture |
|----------|------|-----------------|
| Compose labeled meal (open moment + broader episodic + supports) | Model call | Low–medium |
| Dedup atoms across channels | Meal build | Low |
| Slide-off open-moment working set under budget | Long moment / pressure | Low |
| Re-gather meal on moment boundary | Moment open/close | Low–medium |
| Optional re-gather every N hops | Long moment | Low–medium |

Details: [design-context-meal-composition.md](design-context-meal-composition.md). Shares are flexible under test.

### 3.4 Semantic

| Activity | When | Latency posture |
|----------|------|-----------------|
| ANN top-k on joint and/or channel vectors | Meal, tools | Tens of ms OK |
| Filtered ANN (time window, moment, kind) | Common | Medium |
| Cross-modal query | Occasional | Medium |
| Index optimize / refresh | Schedule | Background |

### 3.5 Directed traversal

| Activity | When | Latency posture |
|----------|------|-----------------|
| 1-hop neighbourhood by edge type | Traversal tool | Low |
| Bounded multi-hop walk | Traversal | Medium, budgeted |
| Expand hyperedge members | Occasional | Low–medium |
| Temporary candidate buffer | Traversal session | In-memory |
| Promote keep-set; discard temporary | End of traversal | Low |

### 3.6 Procedural

| Activity | When | Latency posture |
|----------|------|-----------------|
| Append trajectory | Goal/outcome | Low |
| Scoped weight updates | Efficiency gain | Very low |
| Rank by success × semantic × temporal prior | Meal / traversal | Medium |
| Offline evaluation on synthetic sets | CI / research | Batch |

### 3.7 Operational

- Restart-safe data under `ELYRA_HOME`
- Hermetic tests without mandatory external servers
- Single-writer friendly (one do-loop worker)
- Portable CPU CI; GPU only for embedding encode
- Backup ≈ copy of memory data directory

---

## 4. Logical data prototype

Logical shape only. Physical Lance schemas will be specified in implementation docs.

```text
Atom
  atom_id
  t_start, t_end?
  moment_id?
  kind             # observation | speak | tool | summary | parcel | …
  content_ref      # text path or small inline
  media_ids[]
  prev_atom_id?, next_atom_id?
  parent_atom_id?  # parcel-of / bonded subatom
  scale?           # summary ladder scale
  window_start?, window_end?
  embedding_status
  emb_text?, emb_image?, emb_audio?, emb_video?, emb_joint?
  qualia?          # later

Edge                 # binary projection of the weave
  edge_id
  src_atom_id, dst_atom_id
  edge_type          # sequential | parcel | semantic | contextual
                     # | success | structural | …
  weight
  updated_at
  meta

Hyperedge            # n-ary bond (reified)
  hyperedge_id
  edge_type
  weight
  created_at
  meta

HyperedgeMember
  hyperedge_id
  atom_id
  role?

Trajectory           # Phase 3
  trajectory_id
  goal_atom_id
  outcome_atom_id?
  atom_ids[]
  success
  efficiency_metrics
  subspace_hint
```

**Mapping note:** Essay *connections* and *hyperedges* appear here as `Edge` + `Hyperedge`/`HyperedgeMember`. Essay *content* and *context* appear as atom fields and moment membership. Essay *felt signal* is reserved (`qualia`) until a deliberate Phase 3+ design. Edge type `contextual` is the natural home for generation-context / influencer links discussed in soft guidance.

---

## 5. Refined storage requirements

### Must-have

1. Point lookup by `atom_id`
2. Temporal range queries and sequential prev/next
3. Multiple vectors per atom (~2048-d class) and ANN top-k
4. Filtered ANN (time, moment, kind)
5. Typed edges with frequent small weight updates
6. Hyperedge representation (native or reified incidence)
7. Bounded multi-hop neighbourhood access
8. Local data under `ELYRA_HOME`; no mandatory cloud
9. Format/API stable enough for long-lived operator data
10. Testable without external services

### Should-have

11. Documented ANN refresh policy under continuous insert
12. Versioning or time-travel for schema/summary evolution
13. Simple backup story
14. Python-first APIs

### Non-requirements (initial)

- Distributed multi-node cluster
- Native hypergraph type system (reification accepted)
- Sub-millisecond ANN at multi-million scale on day one
- Runtime Φ / IIT measures, sheaf cohomology engines, holographic encoding schemes (see soft guidance)

---

## 6. Preliminary storage direction (see also design-database-choices.md)

**Primary direction:** LanceDB for atoms, multimodal fields, multi-embeddings, and ANN indexes; **lance-graph** (optional Cypher layer over the same Lance tables) for multi-hop property-graph queries; **hyperedges reified** in tables.

This direction was chosen after comparing LadybugDB, FalkorDB/FalkorDBLite, CozoDB, and DuckDB+VSS against the activity model above. Details, limitations, and Python-native posture are recorded in [design-database-choices.md](design-database-choices.md).

---

## 7. Documentation obligation (extends engineering principles)

Engineering principles already require: scope, tests, minimal API, and docs when behaviour changes.

**Stretch 2 extension — concept mapping docs are part of done:**

For each phase that lands implementation:

1. **Structure map** — tables/types/APIs ↔ essay concepts (atom, moment, edge types, summary, trajectory, …).
2. **Activity map** — which live/background jobs perform which activities from §3.
3. **Invariants** — what must remain true (e.g. temporary traversal context never enters period summaries).
4. **Failure modes** — explicit behaviour when store/index/encode fails.
5. **Glossary** — if implementation names differ from essay names, list both.

Preferred location as implementation grows:

```text
docs/stretch-2/
  inspiration-activity-model-and-storage.md   # this file
  philosophical-soft-guidance.md              # influences, not requirements
  design-context-meal-composition.md          # provisional meal + slide-off
  design-nemotron-runtime.md                  # Phase 2 embed runtime
  design-*.md                                 # phase design (pre-implement)
  architecture/                               # post-implement detailed manuals
    phase-1-temporal.md
    phase-2-semantic.md
    ...
    glossary.md
```

Design docs may stay speculative; **architecture/** manuals must describe what was actually built.

---

## 8. Revision policy

- Update this inspiration doc when the activity model or requirements materially change.
- Do not delete historical rationale; append a short “Superseded decisions” section if direction shifts.
- Phase architecture manuals may be more specific; they should link back here and to [memory-atoms.pdf](../memory-atoms.pdf).
- Soft guidance may evolve without changing storage requirements; keep the boundary clear.

---

*Recorded as the planning baseline for Stretch 2 memory work. Implementation documentation is expected to grow well beyond this file.*
