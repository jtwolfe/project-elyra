# Design: Database & Index Choices

**Class:** DESIGN
**Status:** Preliminary decision (Spike-validated before Phase 2 locks)
**Branch:** `grok-improvement-memory`
**Depends on:** [inspiration-activity-model-and-storage.md](inspiration-activity-model-and-storage.md)

---

## Decision (preliminary)

| Concern | Choice |
|---------|--------|
| Atom store, multimodal fields, multi-embeddings, ANN | **LanceDB** (Lance format under `ELYRA_HOME`) |
| Multi-hop graph query over edges | **lance-graph** (Cypher over Lance tables) when needed; Python adjacency acceptable early |
| Hyperedges | **Reified** (hyperedge row + membership rows), not a native hypergraph DB |
| Procedural weights | Columns on edge / hyperedge tables; online updates are row updates |
| Application language | **Python-native** APIs (`lancedb`, `lance-graph` via PyO3) |

Phase 1 may implement behind a narrow `elyra/memory/store` interface with Lance (or a temporary simple backend) without requiring lance-graph on day one.

---

## Why this direction

Against the activity model (ingest, temporal range, filtered ANN, bounded multi-hop, sparse weight updates):

- **LanceDB** is the strongest fit for multimodal atoms, multiple vectors per row, and ANN indexes.
- **lance-graph** adds Cypher traversal without a second server or a second atom store.
- Dedicated graph DBs (Ladybug, Falkor, Cozo) were evaluated; they trade away multimodal/ANN depth or add maturity/license/process cost relative to keeping vectors authoritative in Lance.

Full comparison notes live in planning conversation history; this file is the decision record.

---

## Python-native posture

- `pip install lancedb` — primary SDK for tables, vectors, indexes.
- `pip install lance-graph` — optional Cypher (`CypherQuery`, `GraphConfig`).
- Elyra code stays Python; Rust is an implementation detail of dependencies.
- Pin versions of `lancedb`, `lance-graph`, and `pyarrow` in project deps.
- Confirm supported Python version (lance-graph has required 3.11+ in packaging notes) against Elyra’s baseline before merge.

---

## Known limitations (accept consciously)

1. **Property graph, not hypergraph** — n-ary bonds are reified; Cypher walks the binary projection.
2. **lance-graph maturity** — younger than LanceDB; keep graph access behind `elyra/memory/graph.py` so traversal can fall back to Python edge walks.
3. **ANN freshness (OSS)** — appended vectors may be unindexed until optimize; design a recent buffer + scheduled refresh (presence/rest timers).
4. **Graph peak performance** — budgeted 1–3 hop walks are the design target; deep analytical graph mining may later justify a second engine.
5. **App-level consistency** — vector index state and edge weight updates are coordinated by the memory package, not a distributed transaction manager.

---

## ANN policy (required design before Phase 2 default-on)

- Primary indexes: Lance vector indexes on joint (and optionally per-modality) columns.
- Continuous ingest: document whether meal-time search uses full search (includes unindexed) or hybrid recent-buffer + main index.
- Optimize/refresh: background job; never block the do-loop.
- Procedural “ANN” is **not** a second vector index; it is weighted edge priors over a semantic + temporal neighbourhood.

---

## Interface rule

```text
elyra/memory/store.py     # atom CRUD, temporal queries — backend swappable
elyra/memory/index.py     # embedding write + ANN query
elyra/memory/graph.py     # neighbourhood / Cypher-or-Python walk
```

Do not scatter raw Lance or Cypher calls through `loop/` or `presence/`.

---

## Spike checklist (before treating this decision as final)

- [ ] Insert N synthetic atoms with 2 vector columns; filtered ANN by time window
- [ ] Edge table + 2-hop expand (Python and, if available, Cypher)
- [ ] Simulate continuous append + index optimize behaviour
- [ ] Confirm wheel install on operator Linux (and CI CPU)
- [ ] Record results under `docs/stretch-2/architecture/` or a spike note linked from this file

## Relation to Phase 1 PR8 / glass Memory page

- **PR8** (see [design-phase-1-implementation.md](design-phase-1-implementation.md)): implement Lance as a drop-in `MemoryStore` for **current Phase 1 atom fields only** — the first concrete step on this decision. Vector columns and ANN stay Phase 2.
- **PR9** Glass Memory page does **not** depend on Lance; it inspects meal + atoms via read APIs. Vector/Graph **tabs** stay stubs until Phase 2 / 2a fill them.

---

## Relation to philosophy doc

Storage is a vehicle. The essay’s atoms, weave, and consolidation must remain the explanatory frame in architecture manuals. Lance table names are implementation details subordinate to that frame.
