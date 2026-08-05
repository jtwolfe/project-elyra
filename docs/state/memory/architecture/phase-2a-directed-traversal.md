# Architecture — Phase 2a Directed Traversal

**Status:** **Code shipped (PR-A1–A5, 2026-07-29)** — GraphView, TraversalSession, directed_keep meal channel, traverse tools + skill, Graph glass. **Operator smoke dogfood verification is still pending** before calling Phase 2a product-complete or flipping defaults. Flags: `directed_traversal_enabled` / `directed_keep_enabled` **off** (OQ-A1: keep follows traversal when traversal is on).
**Package:** `elyra/memory/` (`graph.py`, `weights.py`, `traverse.py`; meal + tokens + inspect extensions; presence registry; tools; glass Graph APIs)
**Philosophy:** [memory-atoms.pdf](../../../memory-atoms.pdf)
**Design (planning):** [design-phase-2a-directed-traversal.md](../../../design/memory/design-phase-2a-directed-traversal.md) (intent sketch), [design-phase-2a-implementation.md](../../../design/memory/design-phase-2a-implementation.md) (normative implementation design + KD-A* + PR plan)
**Meal sketch:** [design-context-meal-composition.md](../../../design/memory/design-context-meal-composition.md)
**Baseline activities:** [inspiration-activity-model-and-storage.md](../../../design/memory/inspiration-activity-model-and-storage.md) §3.5
**Prior manuals:** [architecture/phase-1-temporal.md](phase-1-temporal.md), [architecture/phase-2-semantic.md](phase-2-semantic.md)
**Program status:** [stretch-2 README](../README.md) Phase 2a close-out
**Seeds:** Phase 2 rectified vector path + Phase 1 temporal spine — prefer operator smoke of Phase 2 before full multi-hop dogfood

This is the **post-implement concept-mapping manual** for Phase 2a. It describes what shipped, how code maps to essay concepts, which activities are live, invariants, and failure behaviour. It is not a re-statement of the design PR stack plan.

### Caveats (honest ship state)

| Item | State |
|------|--------|
| GraphView + weights v1 + TraversalSession | **Shipped** (PR-A1–A2) — structural walks on JSONL; semantic hops need index + warm embedder |
| Meal `directed_keep` + budget v3 | **Shipped** (PR-A3) — next `compose_meal` only (KD-A16); no soft re-outer on finish |
| Tools + `memory-traverse` skill | **Shipped** (PR-A4) — model-invoked only; fail closed when flags off |
| Glass **Graph** tab | **Live + honest** (PR-A5) — considered vs kept, budgets, walk summary; `tabs.graph.stub=false` |
| Feature flags | **Default off** — dogfood must opt in; **not** product default-on |
| Temporary state | **Session-only** (KD-A1) — **no temporary Atom rows** in the store |
| Operator smoke / full dogfood | **Still pending** — code landed; live multi-hop quality not yet signed off |
| Product default-on traversal | **Not** done — after dogfood + operator sign-off |
| Phase 3 success weights | **Not** in 2a — `phase3_multiplier` always 1.0 |

---

## What shipped

Phase 2a adds **model-guided multi-hop walk** over the memory weave as the only intentional mid-walk split-brain for retrieval. Confirmed keeps enter the meal only via the **`directed_keep`** supporting channel. Temporary walk state never becomes store atoms.

| Concern | Module / surface |
|---------|------------------|
| Projected neighbourhood + soft hops | `elyra/memory/graph.py` — `GraphView`, `GraphEdge` |
| Deterministic edge weights v1 | `elyra/memory/weights.py` — pure; Phase 3 hook = 1.0 |
| Session algorithm + registry | `elyra/memory/traverse.py` — `TraversalSession`, `TraversalRegistry`, budgets, NL summary |
| Meal directed_keep channel | `elyra/memory/meal.py` — `select_directed_keep`; order episodic → semantic → **directed_keep** → temporal |
| Budget split v3 | `elyra/memory/tokens.py` — `split_memory_budget_v3` |
| Flags + knobs | `elyra/memory/config.py` / `Settings.memory` (+ validation in `elyra/settings.py`) |
| Glass DTOs / flags helper | `elyra/memory/inspect.py` — graph overview/session; `directed_traversal_flags` |
| Worker registry + meal wire | `elyra/presence/worker.py` — `active` / `last_session` / `last_confirmed_keep`; idle TTL; moment-end clear; `graph_view()`; tool extras |
| Thin tools | `elyra/tools/builtin/memory_traverse.py` + `tools/bundled/memory_traverse_{start,step,inspect,finish,abandon}/` |
| Skill playbook | `skills/bundled/memory-traverse/SKILL.md` |
| Glass Graph tab + APIs | `elyra/runtime/api.py` + web (`app.js` / `style.css` / `index.html`) |

**Not shipped in Phase 2a (by design):** Phase 3 success-path / trajectory learning, lance-graph Cypher as hard dependency, durable semantic edge materialization as meal authority, automatic hop-path traversal every `rebuild_outer`, temporary Atom rows with a “temporary flag,” full hypergraph editor / force layout, product default-on traversal.

**PR stack (code):**

| PR | What |
|----|------|
| **A1** | GraphView edge projection + weight model v1 |
| **A2** | TraversalSession start/step/finish/abandon; dual sticky snapshots; three budget clocks |
| **A3** | Meal `directed_keep` + `split_memory_budget_v3` + omit meta |
| **A4** | Tools + `memory-traverse` skill |
| **A5** | Glass Graph tab + `/api/memory/graph*` |
| **A6** | This architecture note + program docs (docs only) |

---

## 1. Structure map

Essay / planning terms ↔ concrete implementation **as shipped**.

| Essay / planning term | Implementation |
|----------------------|----------------|
| Weave / connections | `GraphView.neighbors` over projected edges + optional live `semantic_hop` |
| Active use of memory | Skill `memory-traverse` + `memory_traverse_*` tools (model-guided; not automatic) |
| Edge strength (v1) | `weights.edge_weight` — base × temporal decay × structural bonus × semantic factor × `phase3_multiplier` (1.0) |
| Temporary candidate buffer | **`TraversalSession` in-process only** — frontier, considered, scratchpad, budget counters. **Not** store atoms (KD-A1) |
| Keep-set | Ordered durable `atom_id`s confirmed by model/operator (`ConfirmedKeepSnapshot.keep_ids`) |
| Walk narrative | `walk_summary_nl` (template-first + optional `summary_hint`) |
| Context hygiene | Session status `active` → `confirmed` \| `abandoned` \| `timed_out`; abandon/TTL clear **active** only |
| Directed-keep meal channel | `MealItem.channel == "directed_keep"`; labels `directed-keep` / `directed-keep/summary` |
| Semantic “reminds me of” (soft hop) | Ephemeral `semantic_hop` via `EmbeddingIndex.search` (same resolve path as Phase 2) — not durable edge writes |
| Sequential time scaffold | Edge kind `sequential` from `prev_atom_id` / `next_atom_id` |
| Parcel bond | `parent_of` / `child_of` from `parent_atom_id` (+ reverse via first_parcel_id chain or moment filter) |
| Same-moment soft scaffold | Edge kind `same_moment` (capped, OQ-A4) |
| Procedural prior (later) | Phase 3 multiplies / adds success edges — **not** required for 2a correctness |
| Split-brain retrieval | Mid-walk thin decision surface only; **not** a second full meal rewrite |
| Glass observability | Graph tab: considered vs kept from **active else `last_session`** (KD-A19) |

**Critical mapping (KD-A1):** keep-set references **existing durable atoms**. Temporary state is the **session**. There are **no temporary Atom rows** and no atom-level “temporary flag.” Ladder contamination is avoided by construction.

### Edge kinds (shipped)

| `edge_kind` | Source | Durable? | Notes |
|-------------|--------|----------|-------|
| `sequential` | prev/next fields | Projected | Strong time scaffold |
| `parent_of` / `child_of` | `parent_atom_id` + reverse algorithm | Projected | Parcel family; reverse via meta chain or moment filter or omit |
| `same_moment` | Shared `moment_id` | Projected soft | Cap `traverse_same_moment_k` (default 4) |
| `semantic_hop` | Live `EmbeddingIndex.search` | **Ephemeral** | Needs index + warm embedder + `traverse_allow_semantic_hops` |
| `success` / procedural | Phase 3 | Future | Weight hook returns 1.0 in 2a |

### Public surfaces

| Surface | Purpose |
|---------|---------|
| `GraphView.neighbors` | 1-hop expand sorted by weight; optional `expand_deadline_ms` |
| `GraphView.seed_from_text` / `seed_temporal` | Semantic and structural seeds for start |
| `TraversalRegistry.start` / `step` / `finish` / `abandon` | Session state machine |
| `inspect_atoms` | Capped body previews mid-walk (KD-A17) |
| `get_graph_session_view` | Prefer active else `last_session` (glass) |
| `get_last_confirmed_keep` | Meal-thin snapshot only |
| `select_directed_keep` / `compose_meal(..., directed_keep_ids=…)` | Pack confirmed keeps on **next** meal |
| `split_memory_budget_v3` | Caps when directed_keep active; bit-identical v2 when inactive |
| Tools `memory_traverse_*` | Model interface; fail closed when disabled |
| Skill `memory-traverse` | Playbook: start → inspect → step → finish; meal timing honesty |
| Glass `GET/POST /api/memory/graph*` | Overview, session, neighbors, optional debug POST |

### Feature flags (`MemorySettings` Phase 2a)

| Flag / knob | Default | Effect |
|-------------|---------|--------|
| `directed_traversal_enabled` | **`false`** | Master switch for tools + glass POST + Graph “live” walk UX |
| `directed_keep_enabled` | **`false`** | Explicit keep channel; **OQ-A1:** effective keep follows traversal when traversal is on |
| `directed_keep_fraction` | `0.08` | Residual meal share when channel active |
| `traverse_expand_max_ms` | `80` | Per-step / seed_from_text compute soft wall (**not** multi-hop session wall) |
| `traverse_start_expand_max_ms` | `0` | `0` = same as expand_max_ms for start seed |
| `traverse_max_depth` / `max_nodes` / `max_steps` / `max_seeds` | 3 / 48 / 8 / 8 | Hard caps (settings clamp upper bounds) |
| `traverse_frontier_max` / `max_expand_per_step` / `keep_max` | 16 / 3 / 16 | Frontier and keep hygiene |
| `traverse_session_ttl_s` | `900` | **Idle TTL** on **active** only (KD-A18) |
| `traverse_keep_adjacent` | `true` | Finish may add sequential ±1 durable ids if slots remain |
| `traverse_allow_semantic_hops` | `true` | No-ops without index / cold encoder |
| `traverse_label_chars` / `preview_chars` / inspect caps | 80 / 400 / … | Thin surface bounds (KD-A17) |

Helpers: `is_directed_traversal_enabled`, `is_directed_keep_enabled` in `config.py`.

### Integration hooks

| Hook | Where | When |
|------|-------|------|
| Session registry | `PresenceWorker._traversal` (`TraversalRegistry`) | Process-local; moment-scoped sticky snapshots |
| Idle TTL sweep | presence idle tick | Abandon **active** only if idle > TTL |
| Moment end | worker moment finalize | Abandon active; **clear** `last_confirmed_keep` **and** `last_session` |
| Meal wire | `rebuild_outer` → `compose_meal` | Pass `directed_keep_ids` from `last_confirmed_keep` (KD-A16) |
| Tool extras | `_build_tool_context` | `graph_view` factory + `traversal` registry |
| GraphView factory | `worker.graph_view()` | Store + warm embedder only (never cold-load torch for hops) |

### Dual sticky snapshots (KD-A9 + KD-A19)

| Snapshot | Contents | Consumers |
|----------|----------|-----------|
| **`active_session`** | In-progress walk | Tools, glass while walking |
| **`last_session`** | **Full** finished DTO (considered vs kept + budgets + walk summary) | Glass Graph / `GET …/session` after finish |
| **`last_confirmed_keep`** | **Thin** meal slice: keep_ids, walk_summary_nl, goal, session_id, finished_at, moment_id | `select_directed_keep` only |

- New start mid-moment abandons **active only**; meal thin + glass last walk **sticky**.
- Abandon / idle TTL never wipe `last_confirmed_keep` or `last_session`.
- Second finish replaces both last snapshots (last finish wins).
- Restart loses all three (OQ-A2 — in-process only).

### Budgets — three clocks (KD-A18)

| Budget class | Knob | Measures |
|--------------|------|----------|
| **Idle TTL** | `traverse_session_ttl_s` | Wall time since last tool touch → abandon **active** |
| **Per-step expand compute** | `traverse_expand_max_ms` | Wall inside one neighbors / seed_from_text expand |
| **Tool-step cap** | `traverse_max_steps` | Number of model `step` calls |

**No multi-hop session wall-clock.** Waiting for the model must not time out the walk. Surface fields: steps/nodes/depth remaining, expand_ms last/budget, idle age — **not** `wall_ms_remaining` as a multi-hop countdown.

---

## 2. Activity map (§3 inspiration)

Which [§3 activities](../../../design/memory/inspiration-activity-model-and-storage.md) are live after Phase 2a. Phase 1/2 rows remain; directed rows supersede Phase 2 “No — Phase 2a” stubs.

### 3.1–3.4 Write / temporal / meal / semantic

Unchanged when 2a flags are off. Phase 2 semantic remains a **one-shot** supporting channel (orthogonal to directed_keep — KD-A13).

| Activity (delta) | Live? | Notes |
|------------------|-------|-------|
| Meal channel order with directed_keep | **Yes** (flags) | episodic → semantic → **directed_keep** → temporal (KD-A8) |
| Budget v3 when keep active | **Yes** | `split_memory_budget_v3`; bit-identical v2 when inactive |
| Floor cut order | **Yes** | semantic → directed_keep → episodic under temporal floor |
| Link to contextual influencers (typed expand) | **Yes** (tools) | Projected edge kinds + soft semantic hop — not durable influencer table |

### 3.5 Directed traversal

| Activity | Live? | Module / notes |
|----------|-------|----------------|
| 1-hop neighbourhood by edge type | **Yes** | `GraphView.neighbors`; glass neighbors probe |
| Bounded multi-hop walk | **Yes** (flags + tools) | `TraversalRegistry` step loop; depth/nodes/steps caps |
| Expand hyperedge members | **Partial** | Parcel parent/child projection; no reified hyperedge table |
| Temporary candidate buffer | **Yes** | In-process `TraversalSession` only — **no store rows** |
| Promote keep-set; discard temporary | **Yes** | Finish → dual snapshot; abandon/TTL discard active provisional keeps |
| Model inspect before keep | **Yes** | `memory_traverse_inspect` + expand preview (KD-A17) |
| Meal directed-keep packing | **Yes** (effective keep on) | Next `compose_meal` only (KD-A16) |
| Glass considered vs kept | **Yes** | Graph tab from active else `last_session` |
| Automatic walk every rebuild_outer | **No** | Model/tool (or glass debug POST) only (KD-A2) |

### 3.6 Procedural (Phase 3)

| Activity | Live? |
|----------|-------|
| Trajectories / success weights | **No** — Phase 3; `phase3_multiplier` no-op |

### 3.7 Operational

| Concern | Live? | Notes |
|---------|-------|-------|
| Restart-safe sessions | **No for v1** | Sessions in-process only (OQ-A2); durable atoms unchanged |
| Hermetic tests without GPU/torch | **Yes** | JSONL structural; mock warm for semantic hop tests |
| Single-writer friendly | **Yes** | Presence worker owns registry |
| Flags-off parity | **Yes** | Tools/POST fail closed; meal omits directed_keep; Phase 1/2 meal path |

---

## 3. Invariants

Normative rules operators and later phases must preserve. Phase 1 and Phase 2 invariants still apply; Phase 2a adds:

1. **Temporary state is session-only (KD-A1).**  
   No temporary Atom rows; no atom-level temporary flag. Keep-set = durable `atom_id`s only. Ladder `collect_window_sources` never reads sessions.

2. **Confirmed keeps are the only meal ingress for walks.**  
   `select_directed_keep` reads **`last_confirmed_keep` only** — never active provisional keeps, never full session DTOs.

3. **Ladder isolation.**  
   Directed material never appears as ladder raw sources or summary children. Keep does not re-promote.

4. **Invocation is model-guided (KD-A2).**  
   No automatic multi-hop inside `select_semantic` or every `rebuild_outer`. Tools + skill (or glass debug POST under same flags/budgets).

5. **Semantic hops are live ANN, not durable edges (KD-A3).**  
   `semantic_hop` is ephemeral at expand time. Meal semantic packing success is **not** required (KD-A13) — seeds via GraphView/index directly.

6. **Weights v1 are deterministic; Phase 3 hook is 1.0 (KD-A4).**  
   Do not smuggle online success learning into 2a.

7. **Thin decision surface mid-walk (KD-A5 / A17).**  
   Labels ≤80; previews ≤400 on new expands; inspect caps per id / total. No full meal rewrite mid-walk. Skill: inspect (or rely on preview) before keep.

8. **NL summary is template-first (KD-A6).**  
   Optional model `summary_hint` only; no nested LLM on finish.

9. **Meal timing (KD-A16).**  
   Glass sees finish immediately (`last_session`). Outer meal packs keeps on **next** `compose_meal` only — no soft re-outer on finish in v1.

10. **Dual sticky snapshots (KD-A9 / A19).**  
    Abandon / TTL / new start never wipe meal thin keep or glass last walk. Glass after finish uses **full** `last_session` (considered vs kept + budgets) — never collapse glass to keep ids alone.

11. **Three budget clocks only (KD-A18).**  
    Idle TTL + per-step expand_ms + tool steps. **No** multi-hop session wall-clock that kills the walk while waiting for the model.

12. **One active session per moment.**  
    New start abandons active only; last finish wins for sticky snapshots.

13. **Flags default off (KD-A10); fail closed.**  
    Tools and glass POST return `traverse_disabled` when off. Meal omits directed_keep when effective keep off or empty.

14. **Parcel hits map to parent for keep display (KD-A14).**  
    Same spirit as semantic meal; no new reverse index — parent_of via first_parcel_id chain or moment filter or omit.

15. **JSONL structural walks work; semantic hops need Lance/index.**  
    Cold embedder / Null index → structural-only with honest reason codes (`no_index`, `encoder_cold`, …).

16. **Glass Graph is observability (KD-A11).**  
    Considered vs kept + budgets + walk summary — not a hypergraph editor.

---

## 4. Failure modes

| Failure | Severity | Behaviour |
|---------|----------|-----------|
| Flag off | Low | Tools + glass POST `traverse_disabled`; meal omits directed_keep; Graph honesty copy |
| No index / JSONL | Low | Structural walk OK; semantic hops / seed_from_text empty (`no_index`) |
| Cold embedder | Low | Skip semantic hops; structural seeds only (`encoder_cold`) — never cold-load torch in tools |
| Seed empty | Low | Start returns empty frontier + reasons; model should abandon |
| Per-step expand ms exceed | Low | Partial neighbors + `expand_truncated`; session stays active |
| Idle TTL on active | Low | Abandon **active** only; sticky last_session / last_confirmed retained |
| Invalid atom id expand/keep | Low | Ignore / note in payload; inspect fails closed on unknown ids |
| Worker restart mid-walk | Med | Active + sticky snapshots lost; model must restart walk (by design, OQ-A2) |
| Meal dedupe all keeps | Low | Omit directed_keep with reason `deduped` |
| Empty keep / disabled | Low | Omit `empty` / `disabled` |
| Budget starves directed_keep | Low | Omit `budget` |
| Tool thrash many starts | Med | One active per moment; new start abandons active only (confirmed sticky) |
| Second finish | Low | Replaces last_session + last_confirmed (last finish wins) |
| Missing tool extras | Low | `traverse_unavailable` (ports not injected) |
| Ladder regression (temp atoms) | High if regressed | Session-only design + tests: session never in `collect_window_sources` |
| Confirmed wipe on new start | High if regressed | KD-A9 tests must keep meal + glass sticky |
| Session wall-clock false timeout | High if regressed | KD-A18: no multi-hop wall |

---

## 5. Meal labels (Phase 2a)

| Label pattern | Channel | Content |
|---------------|---------|---------|
| `directed-keep/summary` | directed_keep | Walk summary NL (template ± hint); `atom_id=None` |
| `directed-keep` | directed_keep | Confirmed keep atom body (parcel→parent mapped) |
| Phase 2 `semantic` / Phase 1 temporal+episodic | unchanged | Orthogonal channels |

**Order of outer messages:** **system → episodic → semantic → directed_keep → temporal → orient** (chain by doloop).

**Omit reasons** (`MealPackage.directed_keep_omitted_reason` / `directed_keep_meta`): `disabled` | `empty` | `deduped` | `budget`.

**Dedup priority:** temporal > episodic > semantic > directed_keep (lowest among memory channels).

---

## 6. Graph tab / APIs (glass)

| Tab | Phase 2a state |
|-----|----------------|
| **Context** | Live — may show directed_keep omit/meta muted line when channel active |
| **Atoms** | Live (Phase 1) |
| **Vectors** | Live (Phase 2) — unchanged |
| **Graph** | **Live + honest (PR-A5)** — no longer stub |

Overview: `GET /api/memory` reports `tabs.graph: {stub: false, phase: "2a"}`.

### Graph APIs

| Endpoint | Purpose |
|----------|---------|
| `GET /api/memory/graph` | Flags, has_active / has_last_session, meal keep count, edge-kind legend, honesty |
| `GET /api/memory/graph/session` | Active if present else **`last_session`** — considered vs kept, budgets, walk summary (KD-A19). Optional `?which=active\|last\|meal` |
| `GET /api/memory/graph/neighbors?atom_id=` | 1-hop multi-kind expand (structural ± semantic) |
| `POST /api/memory/graph/traverse` | Optional operator debug start/step/finish/inspect — **same flags + budgets as tools**; fail closed when off (OQ-A3) |

**Session card budgets:** steps/nodes/depth remaining+spent, expand_ms last/budget, idle age — **not** multi-hop wall-clock countdown.

**Empty / disabled honesty:** flag off, no session, JSONL structural-only, cold encoder — stated clearly (Vectors honesty pattern).

---

## 7. Key decisions (KD-A1–A19 as shipped)

| ID | Decision | Shipped as |
|----|----------|------------|
| **KD-A1** | Session-only temporary state; no temp Atom rows | `TraversalSession` in registry; store never gets walk candidates |
| **KD-A2** | Model tools + skill; not automatic rebuild walks | Tools + skill; no doloop auto-walk |
| **KD-A3** | Projected structural edges; live semantic_hop | `graph.py` + index; no edge table |
| **KD-A4** | Deterministic weights; phase3 = 1.0 | `weights.py` |
| **KD-A5** | Thin surface + preview/inspect | Tool payloads + `inspect_atoms` |
| **KD-A6** | Template NL summary ± hint | `build_walk_summary_nl` |
| **KD-A7** | Meal channel fraction 0.08; floor cut s→d→e | `split_memory_budget_v3` defaults |
| **KD-A8** | Message order epi → sem → **dk** → temporal | `compose_outer_messages` |
| **KD-A9** | Sticky last_confirmed + last_session; abandon active only | `TraversalRegistry` |
| **KD-A10** | Flags default off; keep may follow traversal | Both false OOB; OQ-A1 follow |
| **KD-A11** | Graph = observability | Graph tab UX scope |
| **KD-A12** | No lance-graph Cypher gate | Python `GraphView` authority |
| **KD-A13** | Independent of meal semantic fill | Seeds via GraphView; meal semantic orthogonal |
| **KD-A14** | Parcel→parent; parent_of reverse algorithm | graph projection + meal map |
| **KD-A15** | Architecture note required for done | **This document** |
| **KD-A16** | Meal on next compose only | Worker rebuild_outer wire; skill honesty |
| **KD-A17** | Normative inspect + preview before keep | Tool + skill playbook |
| **KD-A18** | Idle TTL + expand_ms + steps; no session wall | Budgets / settings |
| **KD-A19** | Glass uses full last_session; meal uses thin keep | Dual snapshot split |

Operator locks OQ-A1–A7 (2026-07-29) accepted as shipped defaults (keep follows traversal; no restart persist; glass debug POST; same_moment capped; tool-step naming; no LLM summary; no soft re-outer).

---

## 8. Glossary

| Term | Meaning in Phase 2a |
|------|---------------------|
| **GraphView** | Façade for 1-hop neighbourhood and seeds over store (+ optional index) |
| **GraphEdge** | Projected or ephemeral edge: kind, weight, reason, meta |
| **TraversalSession** | In-process walk state (active/confirmed/abandoned/timed_out) |
| **TraversalRegistry** | Worker-owned registry: active + last_session + last_confirmed_keep |
| **Considered** | Atoms seen during the walk (with via-edge / depth) |
| **Frontier** | Ranked candidates for next expand |
| **Keep-set** | Ordered durable atom ids confirmed for meal |
| **ConfirmedKeepSnapshot** | Meal-thin keep ids + walk summary (not full session) |
| **Directed-keep channel** | Supporting meal package section for confirmed keeps |
| **Semantic hop** | Live ANN neighbour treated as ephemeral edge — not written |
| **Expand budget** | Per-step wall-clock for graph compute (not multi-hop session wall) |
| **Idle TTL** | Abandon active if no tool touch within `traverse_session_ttl_s` |
| **Thin decision surface** | Tool JSON: frontier labels/previews, budgets, scratchpad — not full meal |
| **Walk summary NL** | Template string for glass + meal directed-keep summary line |

---

## 9. Tests (shipped coverage)

| File | Focus |
|------|-------|
| `tests/test_memory_graph.py` | Edge projection; neighbors; parent reverse; semantic hop empty reasons |
| `tests/test_memory_weights.py` | Pure weight math; clamp; phase3 no-op |
| `tests/test_memory_traverse.py` | Session start→step→finish; sticky snapshots; budgets; TTL; hygiene |
| `tests/test_memory_meal_directed_keep.py` | Pack/dedupe/omit; v3 golden cases; flags-off parity |
| `tests/test_memory_traverse_tools.py` | Tools fail-closed; extras wiring; inspect; structural + mock warm |
| `tests/test_memory_graph_api.py` | Graph APIs; last_session after finish; POST fail-closed; budget parity |
| `tests/test_settings.py` | Traverse knob validation / clamps |

Hermetic CI: **no** torch, **no** GPU, **no** network. Lance tests skip-if-unavailable.

---

## 10. Related docs

| Document | Role |
|----------|------|
| [design-phase-2a-implementation.md](../../../design/memory/design-phase-2a-implementation.md) | **Normative** implementation design, KDs, PR plan |
| [design-phase-2a-directed-traversal.md](../../../design/memory/design-phase-2a-directed-traversal.md) | Intent sketch (concept map = session-only) |
| [architecture/phase-2-semantic.md](phase-2-semantic.md) | Phase 2 seeds + Vectors; Graph was stub until 2a |
| [architecture/phase-1-temporal.md](phase-1-temporal.md) | Temporal spine + ladder isolation baseline |
| [design-context-meal-composition.md](../../../design/memory/design-context-meal-composition.md) | Directed-keep channel sketch |
| [design-database-choices.md](../../../design/memory/design-database-choices.md) | `graph.py` interface rule; Cypher optional later |
| [design-phase-3-procedural.md](../../../design/memory/design-phase-3-procedural.md) | Success weights later |
| [inspiration-activity-model-and-storage.md](../../../design/memory/inspiration-activity-model-and-storage.md) | §3.5 activity baseline |
| [tools-and-skills.md](../../tools-and-skills.md) | Skill vs tool split |
| [philosophical-soft-guidance.md](../../../stretch-2/philosophical-soft-guidance.md) | Judgment influences only |

When behaviour changes, update **this** architecture note (and activity map) as part of done — historical design docs stay historical unless a decision is revised.

---

## 11. Follow-on packaging

| Work | Role |
|------|------|
| **Operator smoke dogfood** | Flags on; structural JSONL path first; full path with Lance + semantic for multi-hop seeds; verify finish → glass → next meal directed_keep |
| **Edges + traverse + polish1 dogfood** | Durable fabric + pure semantic start + raised budgets + polish1 wait/map/sticky — [edges-traversal-dogfood.md](../edges-traversal-dogfood.md) (#98/#120/#103/#105; polish1); code on `working`/`main`; **live dogfood partial**; polish2 [#125](https://github.com/jtwolfe/project-elyra/issues/125); `durable_edges_enabled` default **off** |
| **Phase 2 dogfood / Gate B** | Prefer rectified semantic seeds before claiming rich multi-hop quality |
| **Product default-on traversal** | **Not** automatic — only after dogfood quality + operator sign-off |
| **Phase 3** | Procedural / success-path eval-first; hang weights on edges/sessions |
| **Optional** | lance-graph Cypher behind same façade; durable edge table; restart-sticky keep (rejected for v1) |

### Dogfood path (honest)

```text
# Structural-only (always available with memory store):
memory.directed_traversal_enabled = true   # keep follows via OQ-A1
# Full multi-hop with soft semantic seeds:
memory.backend = "lance"
memory.embed_enabled = true
memory.semantic_enabled = true
memory.directed_traversal_enabled = true
# + elyra[memory-lance]; warm encoder for semantic hops
```

Rollback: set `directed_traversal_enabled=false` (and explicit `directed_keep_enabled=false` if set) → tools/POST fail closed; meal channel empty; Graph shows disabled honesty. Durable atoms and Phase 1/2 paths unchanged.

**Phase 2a product surface:** model-guided walk + temporary session hygiene + directed_keep meal channel + Graph glass + architecture note, with **flags default off** and **dogfood still pending**. Phase 3 remains evaluation-first.
