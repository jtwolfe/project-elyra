# Architecture — Phase 2 Semantic Memory

**Status:** **Shipped** (2026-07-28) — Phase 2 implementation on the `execute-plan` stack / `grok-improvement-memory` (**PR1–PR9**: meal semantic + Vectors glass + optional Nemotron; see caveats). Defaults: `semantic_enabled` / `embed_enabled` / `parcels_enabled` **off** (zero behaviour change until dogfood).
**Package:** `elyra/memory/` (`embed/`, `index.py`, `parcel.py`; meal + Lance extensions; glass Vectors APIs)
**Philosophy:** [memory-atoms.pdf](../../memory-atoms.pdf)
**Design (planning):** [design-phase-2-semantic.md](../design-phase-2-semantic.md), [design-phase-2-implementation.md](../design-phase-2-implementation.md)
**Runtime contract:** [design-nemotron-runtime.md](../design-nemotron-runtime.md)
**Spikes:** [architecture/spikes/lance-emb-migration.md](spikes/lance-emb-migration.md), [architecture/spikes/nemotron-runtime.md](spikes/nemotron-runtime.md)
**Meal sketch:** [design-context-meal-composition.md](../design-context-meal-composition.md)
**Baseline activities:** [inspiration-activity-model-and-storage.md](../inspiration-activity-model-and-storage.md) §3
**Phase 1 manual:** [architecture/phase-1-temporal.md](phase-1-temporal.md)
**Program status:** [stretch-2 README](../README.md) Phase 2 close-out

This is the **post-implement concept-mapping manual** for Phase 2. It describes what shipped, how code maps to essay concepts, which activities are live, and how the system fails. It is not a re-statement of the design PR stack plan.

### Caveats (honest ship state)

| Item | State |
|------|--------|
| Encode + ANN + parcels + meal semantic + Vectors glass | **Shipped** (PR1–PR7 on execute-plan stack; PR9 this note) |
| Optional Nemotron runtime | **Shipped** (PR8) — real load when deps present; mock fallback when not |
| Feature flags | **Default off** — dogfood must opt in; Gate B before product default-on |
| Glass **Vectors** tab | **Live** (`tabs.vectors.stub=false`, phase `"2"`) — health, embedding-status list, neighbor inspect (KD18) |
| Glass **Graph** tab | **Stub** — Phase **2a** (directed traversal); **out of scope** for Phase 2 |
| Product default-on semantic | **Not** done — Gate B + operator sign-off still required |
| Default `backend` | CI / factory default remains **jsonl** (no ANN); durable vectors require `backend=lance` + `elyra[memory-lance]` |

---

## What shipped

Phase 2 adds **associative / semantic** structure as a *supporting* context channel. Phase 1 temporal/episodic behaviour is unchanged when Phase 2 flags are off.

| Concern | Module / surface |
|---------|------------------|
| Embedding status vocabulary | `elyra/memory/types.py` — `none` / `pending` / `ready` / `failed` / `skipped` |
| Bonded multi-channel vectors (pure) | `elyra/memory/embed/types.py` — `EmbeddingSet`, `CHANNELS`, `EMBED_DIM=2048` |
| Mock encoder (CI / dogfood without GPU) | `elyra/memory/embed/mock.py` — deterministic 2048-d L2 unit vectors |
| Portable open path | `elyra/memory/embed/runtime.py` — `open_encoder`; mock; optional `NemotronEmbedder` (PR8) with mock fallback |
| Encode helpers + content fingerprint | `elyra/memory/embed/encode.py` |
| Async encode queue + drain | `elyra/memory/embed/queue.py` — FIFO, dedupe, `encode_queue_max` backpressure |
| Store write hooks + `list_atoms` | `elyra/memory/store.py` Protocol; jsonl + lance implementations |
| Promote → `pending` + parcels | `elyra/memory/promote.py` (no embedder import; no `doloop.py` changes) |
| Parcel split (opt-in) | `elyra/memory/parcel.py` — before truncate when `parcels_enabled` |
| Lance emb columns + migration + preserve | `elyra/memory/lance_store.py` — `upsert_vectors`, KD19 read-merge-write |
| EmbeddingIndex + freshness | `elyra/memory/index.py` — hybrid recent-buffer, full search below threshold, idle optimize |
| Meal semantic channel | `elyra/memory/meal.py` — `select_semantic`, parcel→parent, timeout omit |
| Budget split v2 | `elyra/memory/tokens.py` — `split_memory_budget_v2` + temporal floor |
| Settings knobs | `elyra/memory/config.py` / `Settings.memory` |
| Idle drain + optimize + meal wiring | `elyra/presence/worker.py` |
| Glass Vectors tab + APIs (PR7) | Live health / status list / neighbors; `tabs.vectors.stub=false` |
| Glass Memory page | Context + Atoms + **Vectors live**; **Graph stub** (Phase 2a) |
| Inspect DTOs for vectors | `elyra/memory/inspect.py` — encoder/index health, vector rows, neighbor hits |

**Not shipped in Phase 2 (by design):** directed multi-hop / temporary keep-set (Phase 2a), success-path / trajectory weights (Phase 3), historical glass→atom backfill, full hypergraph Graph UI, default-on semantic without Gate B, multi-channel ranking fusion (joint-primary only), optional 2D vector projection (KD18 non-gate).

**Deferred / follow-ups:** Gate B dogfood before flipping semantic defaults; Phase 2a Graph tab; optional 2D projection polish.

---

## 1. Structure map

Essay / planning terms ↔ concrete implementation.

| Essay / planning term | Implementation |
|----------------------|----------------|
| Associative connection (“reminds me of”) | ANN neighbours over joint (and optional channel) embeddings via `EmbeddingIndex.search`; meal channel `semantic` |
| Multimodal instance / bonded channels | `EmbeddingSet` channels `text` / `image` / `audio` / `video` / `joint` bonded to one `atom_id` — **not** separate warehouse atoms per modality |
| Recombination | Channel-level match + parent atom / parcel identity; meal labels `semantic` or `semantic/parcel→parent` |
| Supporting vs primary context | `channel=semantic` budgeted under temporal/episodic; cut first under pressure; temporal floor (`temporal_min_fraction` default 0.55) |
| Parcel | `kind=parcel` atoms with `parent_atom_id`; sequential among parcels only; parent keeps experience kind + first chunk |
| Consolidation (unchanged) | Ladder still temporal (`ladder.py`); **does not** require embeddings |
| Weave (semantic) | Query-time ANN (+ hybrid recent buffer); full typed graph product is Phase **2a** |
| Warehouse anti-pattern | Vectors are channels on instances, not detached fact rows; summaries still do not replace children |
| Temporary traversal buffer (Phase 2a) | **Not present**; must never appear in ladder sources or durable meal channels |

### Embedding status

| Status | Meaning |
|--------|---------|
| `none` | Semantic off, or never requested (Phase 1 writes remain valid) |
| `pending` | Enqueued or awaiting encode / durable upsert |
| `ready` | Active `EmbeddingIndex` holds required vectors for the atom (KD20) |
| `failed` | Encode attempted and failed (retry up to `encode_max_attempts`) |
| `skipped` | Empty content / queue overflow / encoder permanently unavailable for this atom |

**Ready rule (KD20):** `ready` only when the index holds `emb_joint` **or** (single-modality atom and that modality vector) **and** durable upsert succeeded (Lance columns or in-memory test index). Joint is **eager** when ≥2 modalities present at encode time.

### Public surfaces

| Surface | Purpose |
|---------|---------|
| `open_encoder(settings)` | Lazy embedder (mock / nemotron-fallback); no torch at `elyra.memory` import |
| `EncodeQueue.enqueue` / `drain` | Idle-only corpus encode with ms/item caps |
| `MemoryStore.set_write_hook` | After successful `put_atom`; primary enqueue path (KD16) |
| `MemoryStore.list_atoms` | Glass/admin + idle pending scan (status/kind filter; limit ≤ 200) |
| `LanceMemoryStore.upsert_vectors` | Patch emb columns + status without scalar wipe |
| `EmbeddingIndex.upsert` / `search` / `optimize` / `health` | Vector write, hybrid ANN, idle optimize, staleness |
| `select_semantic` / `compose_meal` | Supporting channel under `semantic_select_max_ms` |
| `split_memory_budget_v2` | Semantic + episodic + temporal caps; Phase 1 math when semantic off |

### Lance physical schema (additive)

| Column | Notes |
|--------|-------|
| Phase 1 string cols | Unchanged |
| `emb_text` … `emb_joint` | Fixed 2048-d float lists; null if absent |
| `embed_model` / `encoded_at` | Optional denorm |
| `meta.json` | `vector_schema_version=1`, `emb_dim=2048`, `vector_migrated_at` |

Logical `Atom.schema_version` stays **1** (vectors are not on the dataclass).

### Feature flags (`MemorySettings` Phase 2)

| Flag | Default | Effect |
|------|---------|--------|
| `semantic_enabled` | `false` | Meal channel + promote may set `pending`; idle hooks/scan active when on |
| `embed_enabled` | `false` | Allow load encoder + drain; without it, pending stays pending |
| `embed_backend` | `mock` | `mock` \| `nemotron` (real load optional; falls back) |
| `embed_device` | `auto` | `auto` \| `cuda` \| `rocm` \| `cpu` |
| `parcels_enabled` | `false` | Oversized split before truncate; **not** auto-enabled by semantic |
| `semantic_select_max_ms` | `50` | Hard wall clock for meal semantic select (omit on exceed) |
| `encode_query_max_ms` | `30` | Sub-budget for warm query encode inside select |
| `semantic_fraction` | `0.12` | Of residual when semantic on |
| `episodic_fraction_with_semantic` | `0.18` | Episodic share when semantic on |
| `temporal_min_fraction` | `0.55` | Floor; cuts semantic then episodic if needed |
| `semantic_horizon_hours` | `168` | ANN time window (7d default) |
| `semantic_top_k` | `12` | ANN k |
| `semantic_min_score` | `0.0` | Score floor (0 = off) |
| `ann_recent_buffer_max` | `256` | In-process hybrid buffer cap |
| `ann_full_search_below` | `2000` | Full/unindexed search when few ready vectors |
| `ann_optimize_every_n_encodes` | `64` | Idle optimize trigger |
| `ann_optimize_interval_s` | `300` | Idle optimize interval |
| `encode_max_ms_per_tick` | `100` | Idle drain budget |
| `encode_max_items_per_tick` | `4` | Idle drain item cap |
| `encode_queue_max` | `1024` | FIFO cap; drop oldest → skipped |

Rollback: `semantic_enabled=false` empties the channel immediately; `embed_enabled=false` stops load/drain; vectors remain on disk inert. Phase 1 flags (`enabled` / `write_atoms`) unchanged.

### Integration hooks

| Hook | Where | When |
|------|-------|------|
| Promote sets `pending` | `promote.promote_beat` / `promote_wake_observation` | When `semantic_enabled` and embeddable |
| Parcel split before truncate | `promote` only | When `parcels_enabled` and body over threshold |
| Store write hook enqueue | worker after `open_memory_store` | After put when semantic+embed and `pending` |
| Idle pending scan | `worker._idle_memory_encode` | Backstop for restart / missed hooks |
| Idle encode drain | outside state lock; not in-moment | `embed_enabled`; never mid-hop corpus encode |
| Idle index optimize / buffer seed | after encode tick | KD4 freshness |
| Meal query encode + ANN | `select_semantic` inside `compose_meal` / `rebuild_outer` | Only if embedder **already warm**; hard `semantic_select_max_ms` |

---

## 2. Activity map (§3 inspiration)

Which [§3 activities](../inspiration-activity-model-and-storage.md) are live after Phase 2. Phase 1 rows remain; semantic rows supersede the Phase 1 “No — Phase 2” stubs.

### 3.1 Write / ingest (delta)

| Activity | Live? | Module / notes |
|----------|-------|----------------|
| Write multi-embeddings (per-modality + joint) | **Yes** (flags) | Idle `EncodeQueue` + `EmbeddingIndex.upsert`; promote only sets `pending` |
| Split oversized content into parcels | **Yes** (`parcels_enabled`) | `parcel.py` + promote before truncate |
| Sequential prev/next (experience) | **Yes** | Unchanged; parcels excluded from moment/global tail |
| Link to contextual influencers | **No** | Later weave kinds / Phase 2a |
| Online edge-weight update | **No** | Phase 3 |

### 3.2 Temporal / episodic

Unchanged from Phase 1 — ladder, range, walks, episodic meal fill. Ladder **does not** require embeddings.

### 3.3 Meal composition (delta)

| Activity | Live? | Module / notes |
|----------|-------|----------------|
| Compose labeled meal | **Yes** | + optional semantic block when `semantic_enabled` |
| Semantic supporting channel | **Yes** (`semantic_enabled`) | `select_semantic`; order: episodic → **semantic** → temporal |
| Dedup across channels | **Yes** | Temporal/episodic win; semantic duplicates dropped (KD11) |
| Slide-off open-moment under budget | **Yes** | Unchanged — meal only, never deletes atoms |
| Cut supports before spine | **Yes** | Budget floor cuts semantic then episodic |

### 3.4 Semantic

| Activity | Live? | Module / notes |
|----------|-------|----------------|
| Encode text / media / joint | **Yes** (idle; mock default) | `embed/encode.py` + runtime; media matrix best-effort |
| ANN top-k on joint (optional channel) | **Yes** (Lance index) | `EmbeddingIndex.search`; JSONL → empty / Null index |
| Filtered ANN (time, moment, kind) | **Yes** | Horizon, exclude open moment, kinds, exclude ids |
| Index freshness under continuous insert | **Yes** | Hybrid recent-buffer + full mode + idle optimize (KD4) |
| Materialized semantic edge graph | **No** | Live ANN is meal authority (OQ5) |

### 3.5 Directed (Phase 2a)

| Activity | Live? |
|----------|-------|
| Directed multi-hop / temporary keep-set | **No** — Phase 2a; Graph tab stub |

### 3.6 Procedural (Phase 3)

| Activity | Live? |
|----------|-------|
| Trajectories / success weights | **No** — Phase 3 |

### 3.7 Operational

| Concern | Live? | Notes |
|---------|-------|-------|
| Restart-safe under `ELYRA_HOME` | **Yes** | Lance reload + buffer seed / full mode |
| Hermetic tests without GPU/torch | **Yes** | Mock encoder; fake `EmbeddingIndex` for meal tests |
| Single-writer friendly | **Yes** | Presence worker; store RLock |
| Scalar upsert preserves vectors | **Yes** | KD19 read-merge-write on Lance |
| Core imports without torch | **Yes** | Lazy `open_encoder` |

---

## 3. Invariants

Normative rules operators and later phases must preserve. Phase 1 invariants still apply; Phase 2 adds:

1. **Corpus encode is idle-only.**  
   Never run full atom→vector encode on the hop / `promote_beat` / mid-`rebuild_outer` path. Drain only when not in-moment, outside the presence state lock, under `encode_max_ms_per_tick` / `encode_max_items_per_tick`.

2. **Meal semantic select has a hard timeout.**  
   Entire query encode + ANN + pack must finish within `semantic_select_max_ms` (default 50). On exceed → empty semantic channel + `semantic_omitted_reason=timeout`. Never block the hop unbounded.

3. **Query encode only if model already warm.**  
   No cold load inside `select_semantic` (KD12). Sub-budget `encode_query_max_ms` (default 30).

4. **Scalar `put_atom` / `update_links` preserve `emb_*`.**  
   KD19: read-merge-write (or equivalent). Phase 1 link updates must not null vectors. Vectors go through dedicated `upsert_vectors` / index upsert.

5. **`ready` means the index holds vectors.**  
   Do not mark `ready` when only status flipped without durable/in-memory vector hold (KD8 / KD20).

6. **Phase 1 remains correct with semantic off or unavailable.**  
   Flags default off → identical temporal/episodic meal and promote truncate path. Encoder/index failure → omit semantic; never break temporal meal.

7. **Semantic is supporting only.**  
   Budget via `split_memory_budget_v2`; temporal floor enforced by cutting semantic then episodic. Temporal/episodic win dedup.

8. **Enqueue via store hooks + pending scan — no `doloop.py` encode wiring.**  
   Promote sets `pending` only; all writers (promote, ladder, parcels) share hooks (KD16).

9. **Parcels: promote-time before truncate; parent on experience chain.**  
   Parcel children excluded from moment tail and temporal raw fill; ANN hits map to parent in meal (KD21). `parcels_enabled` default false (KD23).

10. **Temporary / directed context never enters ladder or durable meal.**  
    Forward invariant for Phase 2a — no temporary channel in `collect_window_sources` / durable episodic.

11. **Slide-off never deletes durable atoms.**  
    Unchanged from Phase 1.

12. **Promote is best-effort and never changes hop outcome.**  
    Unchanged; encode failures are status/meta only.

13. **JSONL has no production ANN.**  
    Semantic meal empty without Lance or injected test index. Switching backend does not migrate vectors to JSONL.

14. **Background optimize never starves the hop.**  
    Idle only; soft `ann_optimize_max_ms`.

---

## 4. Failure modes

| Failure | Severity | Behaviour |
|---------|----------|-----------|
| Torch / model missing | Low | Mock or encoder ok=false; pending→skipped; semantic omit |
| `embed_backend=nemotron` without real runtime | Low | Mock fallback; health notes fallback |
| Cold load timeout on idle | Low | Partial drain; next tick continues; hop unaffected |
| Encode exception | Low | `failed` + `meta.embed_error`; retry up to `encode_max_attempts` |
| Meal semantic select slow | Med | Exceed `semantic_select_max_ms` → omit; `semantic_omitted_reason=timeout` |
| Embedder not warm at meal | Low | Omit; `semantic_omitted_reason=encoder` (or equivalent) |
| Empty open-moment seed | Low | Omit; `empty_seed` |
| No index / JSONL backend | Low | Search `[]`; semantic empty; log once |
| Lance migration fails | Med | Index ok=false / `migration_failed`; scalar Phase 1 path if table readable |
| ANN index stale | Low | Hybrid buffer + full mode; `index_stale` health; idle optimize |
| Vector upsert fails | Low | Leave pending/failed; do not corrupt scalar atom |
| Scalar path would wipe emb (prevented) | High if regressed | KD19 tests; preserve contract |
| Queue overflow | Low | Drop oldest → `skipped` + metric |
| Store write hook raises | Low | Log; idle pending scan backstop |
| OOM on GPU | High | Catch, unload, unavailable; skip semantic; never crash worker |
| Parcel split partial put | Med | Reconcile parent meta; incomplete flags |
| Dual backend operator switch | Med | Vectors only on Lance path; no auto-migrate to JSONL |

---

## 5. Freshness + migration (as shipped)

### ANN freshness (KD4)

**Recent-buffer is a correctness mechanism**, not telemetry: hybrid search must not miss ready atoms still unindexed after continuous insert.

| Topic | Rule |
|-------|------|
| **Populate** | Every successful `EmbeddingIndex.upsert` pushes/replaces buffer entry (joint vector preferred) |
| **Cap** | `ann_recent_buffer_max` (default 256); oldest `encoded_at` first |
| **Persistence** | In-process only — not a separate on-disk log |
| **On open / restart** | If `vectors_ready < ann_full_search_below` → full/unindexed mode; else seed buffer from last N ready rows; schedule optimize if stale |
| **Hybrid search** | Main ANN top-k **union** brute-force cosine over buffer; merge by score; apply filters to both legs |
| **Optimize** | Idle only: every N encodes and/or interval; never mid-hop; trim buffer when safe |
| **Staleness** | `health()["index_stale"]` when buffer non-empty, encodes exceed threshold, or seed incomplete |

Hard meal cap remains `semantic_select_max_ms` for the whole select path.

### Lance emb migration (as shipped)

1. Open `data/memory/lance/`; inspect schema.
2. If emb columns missing → additive migration (backup/staging path with crash recovery for interrupted drop).
3. Write `meta.json`: `vector_schema_version=1`, `emb_dim=2048`, `vector_migrated_at`.
4. Existing rows: null vectors; `embedding_status` unchanged until queued.
5. Fail closed: migration error → index not ok; scalar store still serves Phase 1 when possible.
6. No automatic dual-write to JSONL.

**Preserve contract:** after encode → `ready` with non-null `emb_joint`, promoting a new atom that `update_links` the previous must leave previous vectors and `ready` intact.

---

## 6. Meal labels (Phase 2)

Messages render with a `[context:…]` header. Phase 1 labels unchanged; semantic adds:

| Label pattern | Channel | Content |
|---------------|---------|---------|
| `semantic` | semantic | Supporting neighbour body (score optional in label) |
| `semantic/parcel→parent` | semantic | Hit was on a parcel; parent body shown |
| `episodic/summary {scale}` | episodic | (Phase 1) |
| `episodic/prior-moment {short_id}` | episodic | (Phase 1) |
| `temporal/compact` | temporal | Meal-only slid-off glue |
| `temporal/moment {short_id}` | temporal | Open-moment atoms |

**Order of outer messages:** **system → episodic → semantic → temporal → orient** (chain appended by doloop).

Budget: residual after system+orient. When semantic off → Phase 1 `split_memory_budget`. When on → `split_memory_budget_v2` with temporal floor.

`MealPackage.semantic_omitted_reason` (observability): e.g. `timeout` | `encoder` | `empty_seed` | `no_index` | min-score exhaustion — inspectable via meal snapshot / Context tab when semantic on.

---

## 7. Vectors tab / Graph (glass)

| Tab | Phase 2 state |
|-----|----------------|
| **Context** | Live (Phase 1) — meal labels include semantic when channel non-empty |
| **Atoms** | Live (Phase 1) — atom browser |
| **Vectors** | **Live (PR7 / KD18)** — encoder + index health, embedding-status list, neighbor inspect |
| **Graph** | **Stub** — Phase **2a** directed traversal / typed edges. **Out of scope** for Phase 2 |

Overview: `GET /api/memory` reports `tabs.vectors: {stub: false, phase: "2"}` and `tabs.graph: {stub: true, phase: "2a"}`.

### Vectors APIs (read-only)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/memory/vectors` | Encoder health (device, model, backend, queue depth) + index health (`vectors_ready`, `index_stale`, recent buffer) |
| `GET /api/memory/vectors/atoms?status=…&limit=50` | Atoms filtered by `embedding_status` via `list_atoms` (default 50, max 200) |
| `GET /api/memory/vectors/neighbors?atom_id=…` or `?q=…` | Top-k scored neighbours; fail soft to empty when encoder/index unavailable |

**Invariants:** read-only; no secrets; **no raw 2048-d vector dumps** in responses by default; Graph remains stub. Optional 2D projection is **not** a Phase 2 gate.

---

## 8. Glossary

| Term | Meaning in Phase 2 |
|------|--------------------|
| **EmbeddingSet** | Bonded multi-channel 2048-d vectors for one atom/parcel |
| **Joint embedding** | Single vector over present modalities; primary ANN channel |
| **Bonded channels** | Per-modality vectors on one instance — not separate atoms |
| **Parcel** | Size-split child atom (`kind=parcel`); parent remains experience chain member |
| **EncodeQueue** | In-process FIFO of pending atom_ids; idle drain only |
| **EmbeddingIndex** | Façade for upsert/search/optimize/health over Lance or memory/null backends |
| **Recent buffer** | In-process vectors for hybrid search correctness under continuous insert |
| **Index stale** | Health signal: buffer non-empty, optimize due, or seed incomplete |
| **Semantic channel** | Supporting meal package section; not the open-moment spine |
| **Mock encoder** | Deterministic hash→unit vector path for CI and GPU-free dogfood |
| **Warm embedder** | Already loaded; required for meal-time query encode |
| **Gate B** | Spike checklist before product default-on of semantic flags |

---

## 9. Tests (shipped coverage)

| File | Focus |
|------|-------|
| `tests/test_memory_embed_types.py` | EmbeddingSet dim; channel helpers |
| `tests/test_memory_embed_mock.py` | Deterministic vectors; L2 norm; joint path |
| `tests/test_memory_embed_queue.py` | enqueue/drain caps; dedupe; overflow → skipped |
| `tests/test_memory_index.py` | Hybrid merge; filters; preserve emb; restart seed/full |
| `tests/test_memory_parcel.py` | Split before truncate; parent on chain; default off parity |
| `tests/test_memory_meal_semantic.py` | Budget v2 floor; dedup; timeout omit; parcel→parent |
| `tests/test_memory_semantic_integration.py` | Flags off = Phase 1 parity; semantic on + mock + fake index |
| `tests/test_memory_vectors_api.py` | Vectors overview + atoms status + neighbors; `stub:false`; fail closed |
| Phase 1 suite | Still green with semantic defaults off |

Hermetic CI: **no** torch, **no** GPU, **no** network. Lance tests skip-if-unavailable. Optional Nemotron path behind markers / missing-deps mock fallback.

---

## 10. Related docs

| Document | Role |
|----------|------|
| [design-phase-2-implementation.md](../design-phase-2-implementation.md) | Implementation design, KDs, PR plan (PR1–PR9) |
| [design-phase-2-semantic.md](../design-phase-2-semantic.md) | Short phase outline (points here + implementation design) |
| [design-nemotron-runtime.md](../design-nemotron-runtime.md) | Portable encode contract; Gate B checklist |
| [spikes/lance-emb-migration.md](spikes/lance-emb-migration.md) | Lance emb migration spike (Gate A) |
| [spikes/nemotron-runtime.md](spikes/nemotron-runtime.md) | Nemotron runtime spike notes |
| [design-database-choices.md](../design-database-choices.md) | Lance ANN, interface rule |
| [design-context-meal-composition.md](../design-context-meal-composition.md) | Supporting channel + cut order |
| [architecture/phase-1-temporal.md](phase-1-temporal.md) | Phase 1 shipped manual |
| [design-phase-2a-directed-traversal.md](../design-phase-2a-directed-traversal.md) | Phase 2a boundary (Graph) |
| [inspiration-activity-model-and-storage.md](../inspiration-activity-model-and-storage.md) | §3 activity baseline |
| [philosophical-soft-guidance.md](../philosophical-soft-guidance.md) | Judgment influences only |

When behaviour changes, update **this** architecture note (and activity map) as part of done — design docs stay historical unless a decision is revised.

---

## 11. Follow-on packaging

| Work | Role |
|------|------|
| **Gate B / default-on** | Dogfood mock → Nemotron → `semantic_enabled`; flip defaults only after operator sign-off |
| **Optional 2D projection** | Non-gate polish for Vectors tab (KD18) |
| **Phase 2a** | Directed traversal → implement **Graph** tab for real |
| **Phase 3** | Procedural / success-path (evaluation-first) |

Phase 2 ship surface is meal semantic + ANN + **Vectors glass gate (KD18)** + architecture note. Graph/hypergraph UI is Phase 2a. Flags stay off until dogfood proves latency and quality under `semantic_select_max_ms`.
