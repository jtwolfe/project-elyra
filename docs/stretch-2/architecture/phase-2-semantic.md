# Architecture — Phase 2 Semantic Memory

**Status:** **Product-intent rectified (code)** — Phase 2 ship **PR1–PR9** (2026-07-28) + **rectification PR-R1–R5** (2026-07-29) + **continuous encode** stack (embed-async PR1–PR4, 2026-08-03). Plumbing and product path match locked intent (joint-primary via joint-for-single + repair, `auto` channel, Lance-native main search, honest meal/Vectors, **background corpus drain while PE runs**). **Operator smoke dogfood verification is still pending** before calling Phase 2 product-complete or flipping defaults. Flags: `semantic_enabled` / `embed_enabled` / `parcels_enabled` **off**.
**Package:** `elyra/memory/` (`embed/`, `index.py`, `parcel.py`; meal + Lance extensions; glass Vectors APIs)
**Philosophy:** [memory-atoms.pdf](../../memory-atoms.pdf)
**Design (planning):** [design-phase-2-semantic.md](../../design/memory/design-phase-2-semantic.md), [design-phase-2-implementation.md](../../design/memory/design-phase-2-implementation.md) (historical ship stack)
**Rectification design (normative for R1–R5):** [design-phase-2-rectification.md](../../design/memory/design-phase-2-rectification.md)
**Continuous encode (normative for drain/worker/gate):** [design-embed-async-encode-worker.md](../../design/embed/design-embed-async-encode-worker.md) (KD-E1–E18)
**Runtime contract:** [design-nemotron-runtime.md](../../design/memory/design-nemotron-runtime.md)
**Spikes:** [architecture/spikes/lance-emb-migration.md](../../design/memory/spikes/lance-emb-migration.md), [architecture/spikes/nemotron-runtime.md](../../design/memory/spikes/nemotron-runtime.md)
**Meal sketch:** [design-context-meal-composition.md](../../design/memory/design-context-meal-composition.md)
**Baseline activities:** [inspiration-activity-model-and-storage.md](../../design/memory/inspiration-activity-model-and-storage.md) §3
**Phase 1 manual:** [architecture/phase-1-temporal.md](phase-1-temporal.md)
**Program status:** [stretch-2 README](../README.md) Phase 2 close-out
**Bugs:** [known-bugs.md](../../known-bugs.md) **BUG-mem-p2-01** (fixed in code / residual dogfood), **BUG-mem-gpu-01** (open — packaging/Tensile; product continuous-encode path shipped)

This is the **post-implement concept-mapping manual** for Phase 2. It describes what shipped (including rectification), how code maps to essay concepts, which activities are live, and how the system fails. It is not a re-statement of the design PR stack plan.

### Caveats (honest ship state)

| Item | State |
|------|--------|
| Encode + index + parcels + meal semantic + Vectors glass | **Shipped** (PR1–PR9) + **rectified** (PR-R1–R5) — product path no longer joint-empty by default |
| Continuous corpus encode (EncodeWorker + EmbedderGate + priority lanes) | **Shipped** (embed-async PR1–PR4) — drain while PE runs when `semantic_enabled` + `embed_enabled`; idle-only is **rollback only** (`encode_worker_enabled=false`) |
| Rectification stack (channel / joint / Lance-native / meal omit / glass honesty) | **Code landed** PR-R1–R5; **operator smoke dogfood still pending** |
| Optional Nemotron runtime | **Shipped** (PR8) — real load when deps present; mock fallback when not; GPU packaging path **BUG-mem-gpu-01** still open |
| Feature flags | **Default off** — dogfood must opt in; Gate B before product default-on |
| Glass **Vectors** tab | **Live + honest** — channel auto/toggle, resolved channel, repair remaining, optimize notes, empty-state reasons (PR7 + PR-R5) |
| Glass **Graph** tab | **Stub** — Phase **2a** (directed traversal); **out of scope** for Phase 2 |
| Product default-on semantic | **Not** done — rectified dogfood + Gate B + operator sign-off still required |
| Default `backend` | CI / factory default remains **jsonl** (no ANN); durable vectors require `backend=lance` + `elyra[memory-lance]` |
| Small-N ANN IVF | Expected **not** built below `ann_ivf_min_vectors` (256); **`full_lance`** (or python rollback) is success path — not a product error |

---

## What shipped

Phase 2 adds **associative / semantic** structure as a *supporting* context channel. Phase 1 temporal/episodic behaviour is unchanged when Phase 2 flags are off.

| Concern | Module / surface |
|---------|------------------|
| Embedding status vocabulary | `elyra/memory/types.py` — `none` / `pending` / `ready` / `failed` / `skipped` |
| Bonded multi-channel vectors (pure) | `elyra/memory/embed/types.py` — `EmbeddingSet`, `CHANNELS`, `EMBED_DIM=2048`; shared `should_write_joint` / `joint_vector_for_modalities` |
| Mock encoder (CI / dogfood without GPU) | `elyra/memory/embed/mock.py` — deterministic 2048-d L2 unit vectors; **joint-for-single = copy** (KD-R1) |
| Portable open path | `elyra/memory/embed/runtime.py` — `open_encoder`; mock; optional `NemotronEmbedder` (PR8) with mock fallback; same joint policy as mock |
| Encode helpers + content fingerprint | `elyra/memory/embed/encode.py` |
| Thread-safe encode queue + priority lanes | `elyra/memory/embed/queue.py` — RLock; P1 `atom_create` > P2 `catchup`; dedupe/promote; overflow drops P2 then P1; `encode_queue_max` |
| EmbedderGate + GatedEmbedder | `elyra/memory/embed/gate.py` — exclusive forward; **lookup > bulk** between atoms; meal/graph/API free-text use gated handle only |
| Continuous EncodeWorker | `elyra/memory/embed/worker.py` — presence-owned daemon; budgeted drain ticks; Event wake + poll |
| Store write hooks + `list_atoms` | `elyra/memory/store.py` Protocol; jsonl + lance; concurrent presence + encode worker under store/index locks |
| Promote → `pending` + parcels | `elyra/memory/promote.py` (no embedder import; no `doloop.py` changes) |
| Parcel split (opt-in) | `elyra/memory/parcel.py` — before truncate when `parcels_enabled` |
| Lance emb columns + migration + preserve | `elyra/memory/lance_store.py` — `upsert_vectors`, KD19 read-merge-write; **eager joint-copy repair**; **Lance-native `search_vectors`** |
| EmbeddingIndex + freshness | `elyra/memory/index.py` — `resolve_search_channel`, hybrid recent-buffer, honest `search_mode`, safe optimize (KD-R3) |
| Meal semantic channel | `elyra/memory/meal.py` — `select_semantic` via auto→concrete; omit `no_hits` / `deduped`; `semantic_select_meta`; **wait-for-select default ON** (CPU dogfood: keep slow encodes under `semantic_wait_max_ms`, runtime toggle); query encode via **GatedEmbedder** |
| Budget split v2 | `elyra/memory/tokens.py` — `split_memory_budget_v2` + temporal floor |
| Settings knobs | `elyra/memory/config.py` / `Settings.memory` (+ rectification + continuous-encode knobs; validated in `elyra/settings.py`) |
| Encode ownership + optimize + meal wiring | `elyra/presence/worker.py` — `encode_owner` single-owner; continuous worker start/stop/restart/gap drain; idle drain **rollback only**; idle joint-repair + ANN optimize; rebuild notes |
| Glass Vectors tab + APIs (PR7 + PR-R5) | Health honesty, channel auto/toggle, neighbors resolved channel, rebuild `notes[]` |
| Glass Memory page | Context + Atoms + **Vectors live**; **Graph stub** (Phase 2a) |
| Inspect DTOs for vectors | `elyra/memory/inspect.py` — encoder/index health, vector rows, neighbor hits, score_kind cosine |

### Rectification (PR-R1–R5) — product path fixes

Dogfood on the initial PR1–PR9 stack showed a **dead semantic surface**: default `channel=joint` against text-only corpus (`emb_text` only, no `emb_joint`), empty neighbors/meal semantic, and optimize aimed at empty joint. Rectification restores locked product intent without Phase 2a/3 scope.

| PR | Fix |
|----|-----|
| **PR-R1** | `auto` channel resolve + joint-for-single **copy** + eager joint-copy repair (open + idle) |
| **PR-R2** | Meal omit `no_hits` / `deduped` + `MealPackage.semantic_select_meta` |
| **PR-R3** | Optimize/rebuild: skip IVF when n=0 or below min; **no false `ann_index_built`**; `notes[]` |
| **PR-R4** | Lance-native main-leg search; small-N **`full_lance`**; sole rollback `ann_search_backend=python` |
| **PR-R5** | Vectors glass: default `auto`, channel control, empty-state honesty, rebuild notes UX |
| **PR-R6** | This architecture + README / known-bugs close-out (docs only) |

**Not shipped in Phase 2 (by design):** directed multi-hop / temporary keep-set (Phase 2a), success-path / trajectory weights (Phase 3), historical glass→atom backfill, full hypergraph Graph UI, default-on semantic without Gate B, multi-channel ranking fusion / multi-try search (joint-primary only), optional 2D vector projection (KD18 non-gate), packaging/Tensile device-matrix close for **BUG-mem-gpu-01** (product continuous-encode path is separate — see design embed-async).

**Deferred / follow-ups:** operator smoke + full dogfood on rectified + continuous-encode path; Gate B before flipping semantic defaults; Phase 2a Graph tab; optional 2D projection polish; packaging matrix for modern ROCm / CUDA / CPU.

---

## 1. Structure map

Essay / planning terms ↔ concrete implementation.

| Essay / planning term | Implementation |
|----------------------|----------------|
| Associative connection (“reminds me of”) | Vector neighbours over **resolved** channel embeddings via `EmbeddingIndex.search`; meal channel `semantic` |
| Multimodal instance / bonded channels | `EmbeddingSet` channels `text` / `image` / `audio` / `video` / `joint` bonded to one `atom_id` — **not** separate warehouse atoms per modality |
| Joint as primary search key | After rectification: single-mod writes **`emb_joint = copy(sole)`**; multi-mod uses true `encode_joint`; mid-migration rows repaired by joint-copy (no encoder) |
| Recombination | Channel-level match + parent atom / parcel identity; meal labels `semantic` or `semantic/parcel→parent` |
| Supporting vs primary context | `channel=semantic` budgeted under temporal/episodic; cut first under pressure; temporal floor (`temporal_min_fraction` default 0.55) |
| Parcel | `kind=parcel` atoms with `parent_atom_id`; sequential among parcels only; parent keeps experience kind + first chunk |
| Consolidation (unchanged) | Ladder still temporal (`ladder.py`); **does not** require embeddings |
| Weave (semantic) | Query-time vector search (+ hybrid recent buffer); full typed graph product is Phase **2a** |
| Warehouse anti-pattern | Vectors are channels on instances, not detached fact rows; summaries still do not replace children |
| Temporary traversal buffer (Phase 2a) | **Not present**; must never appear in ladder sources or durable meal channels |

### Embedding status

| Status | Meaning |
|--------|---------|
| `none` | Semantic off, or never requested (Phase 1 writes remain valid) |
| `pending` | Enqueued or awaiting encode / durable upsert |
| `ready` | Active `EmbeddingIndex` holds required vectors for the atom (KD20 / OQ-R4) |
| `failed` | Encode attempted and failed (retry up to `encode_max_attempts`) |
| `skipped` | Empty content / queue overflow / encoder permanently unavailable for this atom |

**Ready rule (KD20 + OQ-R4):** `ready` when the index holds vectors. With `embed_joint_for_single_modality=true` (default), **new encodes require non-null `emb_joint`**. Legacy ready rows with sole modality and null joint remain accepted until **eager joint-copy repair** fills them. Multi-mod joint is true fusion (`encode_joint`); single-mod joint is a **byte-identical L2 unit copy** of the sole modality — never `encode_joint` when n==1 (keeps free-text `encode_text` queries cosine-aligned with corpus joint).

### Channel selection (KD-R2 / KD-R16)

| Term | Meaning |
|------|---------|
| **Channel** | Concrete column: `text` \| `image` \| `audio` \| `video` \| `joint` (`CHANNEL_SET`) |
| **Request** | Explicit channel **or** `auto` (`SEARCH_CHANNEL_SET`) |
| **Resolve** | Pure `resolve_search_channel(request, vectors_by_channel, joint_repair_remaining)` → `(concrete, reason)` |
| **Product default** | `settings.semantic_search_channel` → **`auto`** (meal + glass neighbors) |

**`auto` resolve (single-channel — no multi-try fallback):**

1. If `joint_repair_remaining > 0` → prefer **text** (or first sole modality with coverage) — reason e.g. `auto_text_repair_pending` (do not lock onto incomplete joint).
2. Else if joint count > 0 → **`joint`** (`auto_joint`).
3. Else sole modality with coverage (normally text).
4. Product paths call resolve **once** with the health snapshot, then `search(concrete)` — meal meta and glass echo `channel` + `channel_reason` from that same resolve (KD-R16). Never pass `"auto"` into column lookup / `CHANNEL_SET` early-return.

### Public surfaces

| Surface | Purpose |
|---------|---------|
| `open_encoder(settings)` | Lazy embedder (mock / nemotron-fallback); no torch at `elyra.memory` import; **loader** role only for cold open |
| `EncodeQueue.enqueue` / `drain` | Priority-lane corpus queue (P1 create / P2 catchup); budgeted drain under ms/item caps |
| `EmbedderGate` / `GatedEmbedder` | Serialize shared-embedder forwards; **lookup > bulk** between atoms; meal/graph/API free-text use gated handle |
| `EncodeWorker` | Presence-owned continuous drain daemon when `encode_owner=worker` |
| `MemoryStore.set_write_hook` | After successful `put_atom`; primary enqueue path (KD16) |
| `MemoryStore.list_atoms` | Glass/admin + pending scan backstop (status/kind filter; limit ≤ 200) |
| `LanceMemoryStore.upsert_vectors` | Patch emb columns + status without scalar wipe |
| `LanceMemoryStore.repair_joint_copies` / open+idle repair | Eager joint-copy for ready sole-modality rows (KD-R11); **stays idle-only** (not on EncodeWorker) |
| `resolve_search_channel` | Pure auto→concrete policy (product authority for `channel_reason`) |
| `EmbeddingIndex.upsert` / `search` / `optimize` / `health` | Vector write, hybrid search, safe optimize, honesty fields |
| `select_semantic` / `compose_meal` | Supporting channel under `semantic_select_max_ms`; resolve then search; query encode via gate |
| `split_memory_budget_v2` | Semantic + episodic + temporal caps; Phase 1 math when semantic off |
| `encode_worker_health_block` / Vectors encoder health | Process-local worker owner/alive/drain totals/gate waits (no secrets) |

### Lance physical schema (additive)

| Column | Notes |
|--------|-------|
| Phase 1 string cols | Unchanged |
| `emb_text` … `emb_joint` | Fixed 2048-d float lists; null if absent |
| `embed_model` / `encoded_at` | Optional denorm |
| `meta.json` | `vector_schema_version=1`, `emb_dim=2048`, `vector_migrated_at` |

Logical `Atom.schema_version` stays **1** (vectors are not on the dataclass).

### Feature flags (`MemorySettings` Phase 2 + rectification)

| Flag | Default | Effect |
|------|---------|--------|
| `semantic_enabled` | `false` | Meal channel + promote may set `pending`; hooks/scan active when on |
| `embed_enabled` | `false` | Allow load encoder + drain; without it, pending stays pending |
| `embed_backend` | `mock` | `mock` \| `nemotron` (real load optional; falls back) |
| `embed_device` | `auto` | `auto` \| `cuda` \| `rocm` \| `cpu` |
| `parcels_enabled` | `false` | Oversized split before truncate; **not** auto-enabled by semantic |
| `semantic_select_max_ms` | `50` | Hard wall clock for meal semantic select (omit on exceed) |
| `encode_query_max_ms` | `30` | Sub-budget for warm query encode inside select |
| `semantic_fraction` | `0.12` | Of residual when semantic on |
| `episodic_fraction_with_semantic` | `0.18` | Episodic share when semantic on |
| `temporal_min_fraction` | `0.55` | Floor; cuts semantic then episodic if needed |
| `semantic_horizon_hours` | `168` | Search time window (7d default) |
| `semantic_top_k` | `12` | Top-k |
| `semantic_min_score` | `0.0` | Score floor (0 = off) |
| `semantic_search_channel` | `"auto"` | Meal + product default request channel (KD-R2) |
| `embed_joint_for_single_modality` | `true` | Single-mod encode writes joint = copy of sole (KD-R1) |
| `joint_repair_max_per_open` | `500` | Cap joint-copy repairs during store open |
| `joint_repair_max_per_tick` | `64` | Cap joint-copy repairs per idle tick |
| `ann_search_backend` | `"lance_native"` | Main-leg engine: `lance_native` \| `python` (**sole** search rollback knob) |
| `ann_ivf_min_vectors` | `256` | Skip IVF create when non-null vectors on target col &lt; this |
| `ann_index_channels` | `("joint",)` | Columns considered for `create_index` |
| `ann_recent_buffer_max` | `256` | In-process hybrid buffer cap |
| `ann_full_search_below` | `2000` | Full/unindexed search when few ready vectors |
| `ann_optimize_every_n_encodes` | `64` | Idle optimize trigger |
| `ann_optimize_interval_s` | `300` | Idle optimize interval |
| `encode_max_ms_per_tick` | `100` | Drain budget per EncodeWorker (or idle) tick |
| `encode_max_items_per_tick` | `4` | Drain item cap per tick |
| `encode_queue_max` | `1024` | Priority-queue cap; drop oldest P2 then P1 → skipped |
| `encode_worker_enabled` | `true` | **false → owner=idle** (legacy idle-only drain rollback only) |
| `encode_worker_poll_s` | `0.35` | EncodeWorker Event wait timeout between ticks |
| `encode_worker_max_restarts` | `3` | Per-window thrash budget (not permanent give-up) |
| `encode_worker_restart_window_s` | `60` | Restart thrash accounting window |
| `encode_worker_restart_backoff_max_s` | `30` | Cap exponential restart backoff |

Rollback: `semantic_enabled=false` empties the channel immediately; `embed_enabled=false` stops load/drain; `encode_worker_enabled=false` reverts corpus drain to **idle-only** (operator rollback); vectors remain on disk inert. Search engine rollback: **`ann_search_backend=python`**. Phase 1 flags (`enabled` / `write_atoms`) unchanged. New knobs are allowlisted/coerced in `elyra/settings.py`. Continuous encode activates only when operator enables Phase 2 encode (`semantic_enabled` + `embed_enabled`); factory defaults stay off (KD9).

### Integration hooks

| Hook | Where | When |
|------|-------|------|
| Promote sets `pending` | `promote.promote_beat` / `promote_wake_observation` | When `semantic_enabled` and embeddable |
| Parcel split before truncate | `promote` only | When `parcels_enabled` and body over threshold |
| Store write hook enqueue | worker after `open_memory_store` | After put when semantic+embed and `pending` → P1 `atom_create` lane + wake Event |
| Continuous EncodeWorker drain | `EncodeWorker` daemon (owner=`worker`) | While PE up + semantic+embed + `encode_worker_enabled`; **including busy moments**; budgeted ticks |
| Pending scan / catch-up | EncodeWorker tick (or idle path if rollback) | Backstop for restart / missed hooks / `none`→`pending` → P2 `catchup` |
| Idle encode drain | `worker._idle_memory_encode` | **Only when `encode_owner=idle`** (rollback); no-ops when owner=`worker` (incl. restart gaps) |
| Gap drain on worker death | presence loop (finalize + idle path) | Busy-safe recovery while continuous enabled; **never** permanent idle while flag on |
| Joint-copy repair | store open + idle continue | Ready sole-mod without joint; never hop / never inside `select_semantic` / **not** on EncodeWorker (OQ-E3) |
| Idle index optimize / buffer seed | after idle path | KD4 freshness; KD-R3 guards; **stays idle-only** (OQ-E4) |
| Meal/graph/API query encode | `select_semantic` / graph hop / Vectors free-text | Only if embedder **already warm**; **GatedEmbedder** lookup priority over bulk; hard budgets |

---

## 2. Activity map (§3 inspiration)

Which [§3 activities](../../design/memory/inspiration-activity-model-and-storage.md) are live after Phase 2. Phase 1 rows remain; semantic rows supersede the Phase 1 “No — Phase 2” stubs.

### 3.1 Write / ingest (delta)

| Activity | Live? | Module / notes |
|----------|-------|----------------|
| Write multi-embeddings (per-modality + joint) | **Yes** (flags) | Continuous `EncodeWorker` + priority `EncodeQueue` + `EmbeddingIndex.upsert`; promote only sets `pending` |
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
| Semantic supporting channel | **Yes** (`semantic_enabled`) | `select_semantic` (auto→concrete); order: episodic → **semantic** → temporal |
| Honest semantic omit reasons | **Yes** | `timeout` / `encoder` / `empty_seed` / `no_index` / `min_score` / **`deduped`** / **`no_hits`** + `semantic_select_meta` |
| Dedup across channels | **Yes** | Temporal/episodic win; semantic duplicates dropped (KD11); all-deduped → omit `deduped` |
| Slide-off open-moment under budget | **Yes** | Unchanged — meal only, never deletes atoms |
| Cut supports before spine | **Yes** | Budget floor cuts semantic then episodic |

### 3.4 Semantic

| Activity | Live? | Module / notes |
|----------|-------|----------------|
| Encode text / media / joint | **Yes** (continuous worker; mock default) | `embed/encode.py` + runtime + gate; single-mod joint = copy; multi-mod true joint; progress during busy moments |
| Eager joint-copy repair (mid-migration) | **Yes** | Open + idle; no encoder; health `joint_repair_remaining` |
| Vector top-k on resolved channel | **Yes** (Lance path) | `resolve_search_channel` → `EmbeddingIndex.search`; JSONL → Null index |
| Filtered search (time, moment, kind) | **Yes** | Horizon, exclude open moment, kinds, exclude ids (parity on lance + python) |
| Lance-native main leg | **Yes** | `ann_search_backend=lance_native` → `table.search`; small-N `full_lance` |
| Index freshness under continuous insert | **Yes** | Hybrid recent-buffer + full mode + idle optimize (KD4); safe skip when n small |
| Materialized semantic edge graph | **No** | Live search is meal authority (OQ5) |

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
| Single logical PE process; concurrent presence + encode | **Yes** | Presence do-loop + EncodeWorker under store/index locks; no second OS process writer |
| Scalar upsert preserves vectors | **Yes** | KD19 read-merge-write on Lance |
| Core imports without torch | **Yes** | Lazy `open_encoder` |

---

## 3. Invariants

Normative rules operators and later phases must preserve. Phase 1 invariants still apply; Phase 2 adds:

1. **Corpus encode is continuous background under a single owner (KD-E1 / KD-E7 / KD-E10).**  
   Never run full atom→vector encode on the hop / `promote_beat` / mid-`rebuild_outer` path (enqueue-only; KD-E2). Default product drain is a presence-owned **`EncodeWorker`** (`encode_owner=worker`) that makes progress **while PE is up**, including busy moments, under `encode_max_ms_per_tick` / `encode_max_items_per_tick`. **`encode_owner ∈ {none, idle, worker}`** — only one drain owner at a time; idle path no-ops when owner=`worker` (including restart gaps). Idle-only drain is **operator rollback only** (`encode_worker_enabled=false` → owner=`idle`), not the product default and not a permanent fallback after worker death. Lookup (meal / graph / API free-text) uses a **warm** embedder under **`EmbedderGate`** with **lookup > bulk** between atoms (never mid-forward kill). Design: [design-embed-async-encode-worker.md](../../design/embed/design-embed-async-encode-worker.md).

2. **Meal semantic select has a hard timeout.**  
   Entire query encode + ANN + pack must finish within `semantic_select_max_ms` (default 50). On exceed → empty semantic channel + `semantic_omitted_reason=timeout`. Never block the hop unbounded.

3. **Query encode only if model already warm.**  
   No cold load inside `select_semantic` (KD12). Sub-budget `encode_query_max_ms` (default 30).

4. **Scalar `put_atom` / `update_links` preserve `emb_*`.**  
   KD19: read-merge-write (or equivalent). Phase 1 link updates must not null vectors. Vectors go through dedicated `upsert_vectors` / index upsert.

5. **`ready` means the index holds vectors.**  
   Do not mark `ready` when only status flipped without durable/in-memory vector hold (KD8 / KD20). New encodes with joint-for-single on require `emb_joint` (OQ-R4).

6. **Phase 1 remains correct with semantic off or unavailable.**  
   Flags default off → identical temporal/episodic meal and promote truncate path. Encoder/index failure → omit semantic; never break temporal meal.

7. **Semantic is supporting only.**  
   Budget via `split_memory_budget_v2`; temporal floor enforced by cutting semantic then episodic. Temporal/episodic win dedup; all-deduped → honest omit `deduped`.

8. **Enqueue via store hooks + pending scan — no `doloop.py` encode wiring.**  
   Promote sets `pending` only; all writers (promote, ladder, parcels) share hooks (KD16).

9. **Parcels: promote-time before truncate; parent on experience chain.**  
   Parcel children excluded from moment tail and temporal raw fill; search hits map to parent in meal (KD21). `parcels_enabled` default false (KD23).

10. **Temporary / directed context never enters ladder or durable meal.**  
    Forward invariant for Phase 2a — no temporary channel in `collect_window_sources` / durable episodic.

11. **Slide-off never deletes durable atoms.**  
    Unchanged from Phase 1.

12. **Promote is best-effort and never changes hop outcome.**  
    Unchanged; encode failures are status/meta only.

13. **JSONL has no production ANN.**  
    Semantic meal empty without Lance or injected test index (`no_index`). Switching backend does not migrate vectors to JSONL.

14. **Background optimize never starves the hop.**  
    Idle only; soft `ann_optimize_max_ms`. Never claim `ann_index_built` or trim buffer when IVF skipped (n=0 / below min).

15. **Product search resolves `auto` before column lookup.**  
    Never treat `"auto"` as a durable column; never multi-try channel fallback on the product path. While joint repair remains, auto prefers text.

16. **Joint-copy repair is open + idle only.**  
    Never mid-hop / never inside `select_semantic`.

---

## 4. Failure modes

| Failure | Severity | Behaviour |
|---------|----------|-----------|
| Torch / model missing | Low | Mock or encoder ok=false; pending→skipped; semantic omit |
| `embed_backend=nemotron` without real runtime | Low | Mock fallback; health notes fallback |
| Cold load during continuous worker | Low | Load outside gate + outside long open-lock; consumer ensure returns None (omit); next drain tick continues; hop unaffected |
| Encode exception | Low | `failed` + `meta.embed_error`; deferred retry (not same-tick thrash); up to `encode_max_attempts` |
| EncodeWorker death while busy | Med | Presence monitors every loop; restart with backoff; busy gap drain; **desired owner stays worker** (never permanent idle while continuous on) |
| Meal semantic select slow | Med | Exceed `semantic_select_max_ms` → omit; `semantic_omitted_reason=timeout` |
| Lookup starved under media bulk | Med | Gate between-atom yield; wait mode may still omit; gate wait metrics on Vectors health |
| Embedder not warm at meal | Low | Omit; `semantic_omitted_reason=encoder` (or equivalent); never wait on ~18s cold load |
| Empty open-moment seed | Low | Omit; `empty_seed` |
| No index / JSONL backend | Low | Search `[]`; semantic omit `no_index` (not `no_hits`) |
| Resolved channel has no candidates | Low | Meal omit **`no_hits`**; Vectors show reason + channel searched |
| Hits all already in temporal/episodic | Low | Meal omit **`deduped`** (distinct from no_hits) |
| Mid-migration incomplete joint | Med if regressed | Repair + `auto_text_repair_pending`; do not auto-lock sparse joint |
| Optimize n=0 / below IVF min | Low | Skip create_index; `optimized=false`; `ann_index_built` **unchanged**; `notes[]` explain; search still works via full mode |
| Lance migration fails | Med | Index ok=false / `migration_failed`; scalar Phase 1 path if table readable |
| Index stale | Low | Hybrid buffer + full mode; `index_stale` health; idle optimize |
| Lance search failure | Low | Fallback python cosine; honest search_mode; filters preserved |
| Vector upsert fails | Low | Leave pending/failed; do not corrupt scalar atom |
| Scalar path would wipe emb (prevented) | High if regressed | KD19 tests; preserve contract |
| Queue overflow | Low | Drop oldest P2 then P1 → `skipped` + metric |
| Store write hook raises | Low | Log; worker/idle pending scan backstop |
| OOM on GPU | High | Catch, unload, unavailable; skip semantic; never crash PE |
| GPU / ROCm packaging / Tensile miss | Med | **BUG-mem-gpu-01** still Open for packaging/device matrix; continuous-encode **product path** is separate (worker + gate shipped) |
| Parcel split partial put | Med | Reconcile parent meta; incomplete flags |
| Dual backend operator switch | Med | Vectors only on Lance path; no auto-migrate to JSONL |

---

## 5. Search engine, freshness, repair (as shipped + rectified)

### Main-leg search (KD-R4 / OQ-R6)

| Layer | Behaviour |
|-------|-----------|
| **Concrete channel only** | `search_vectors` / column lookup never receives `"auto"` |
| **`ann_search_backend=lance_native`** (default) | Prefer LanceDB `table.search(..., vector_column_name=emb_{channel})` with cosine metric |
| **Score formula (pinned)** | If Lance returns cosine **distance** `d` ∈ `[0, 2]`, product score = **`1.0 - d`** (clamp finite). Parity fixtures vs python cosine on fixed mock vectors |
| **Small N / pre-IVF** | `search_mode=full_lance` — correct full scan via Lance; **not** a product error |
| **Large N + index built** | `search_mode=hybrid` (main IVF/ANN leg + recent buffer) |
| **`ann_search_backend=python`** | In-process cosine over `_emb_by_id` — sole operator rollback; `search_mode=full_python` |
| **Lance failure** | Log once; fallback to python; may report `hybrid_python_fallback` / python mode |
| **Filters** | kind, time, moment, exclude open moment / ids, ready-only — same semantics on both engines (post-filter when not pushed down) |
| **JSONL / Null index** | Never calls `table.search` or `create_index` |

Honest `search_mode` values: `full_python` \| `full_lance` \| `hybrid` \| `hybrid_python_fallback`. Never claim hybrid IVF when using full scan.

### Hybrid freshness (KD4 + KD-R5)

**Recent-buffer is a correctness mechanism**, not telemetry: hybrid search must not miss ready atoms still unindexed after continuous insert.

| Topic | Rule |
|-------|------|
| **Populate** | Every successful `EmbeddingIndex.upsert` pushes/replaces buffer entry (**joint** when present after KD-R1/repair) |
| **Cap** | `ann_recent_buffer_max` (default 256); oldest `encoded_at` first |
| **Persistence** | In-process only — not a separate on-disk log |
| **On open / restart** | Joint-copy repair batch; if `vectors_ready < ann_full_search_below` → full mode; else seed buffer; schedule optimize if stale |
| **Hybrid search** | Main leg top-k **union** buffer cosine on **resolved** channel; merge by score; apply filters to both legs |
| **Buffer channel match** | Buffer entry must match resolved channel; repair re-pushes joint when joint filled |
| **Optimize (KD-R3)** | Idle only; per `ann_index_channels` (default joint); **skip** when n=0 or n &lt; `ann_ivf_min_vectors`; **never** set `ann_index_built` or trim buffer on skip; return `notes[]` |
| **Staleness** | `health()["index_stale"]` when buffer non-empty, encodes exceed threshold, or seed incomplete |

Hard meal cap remains `semantic_select_max_ms` for the whole select path.

### Eager joint-copy repair (KD-R11)

| Topic | Rule |
|-------|------|
| **Eligibility** | `embedding_status=ready` **and** `emb_joint` null **and** exactly one non-joint modality non-null |
| **Action** | `emb_joint = copy(sole vector)`; upsert; update `meta.embed_channels`; re-push buffer as joint |
| **When** | Store open (bounded `joint_repair_max_per_open`) + idle continue (`joint_repair_max_per_tick`); **not** hop / meal / rebuild |
| **Health** | `vectors_by_channel`, `joint_repair_remaining`, `joint_repair_last_batch` |
| **While remaining > 0** | `auto` resolves to text (or sole modality) — incomplete joint is not product default |

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

`MealPackage.semantic_omitted_reason` (observability): priority when pack empty — `timeout` | `encoder` | `no_index` | `empty_seed` | `min_score` | **`deduped`** | **`no_hits`**. Distinct `no_hits` (resolved channel empty / no candidates) from silent “looks like semantic off.”

`MealPackage.semantic_select_meta` (additive dict, may be `None`): e.g. `channel`, `channel_reason`, `raw_hits`, `deduped`, `packed`, `elapsed_ms`, `joint_repair_remaining` — threaded to inspect / Context muted line.

---

## 7. Vectors tab / Graph (glass)

| Tab | Phase 2 state |
|-----|----------------|
| **Context** | Live — meal labels + semantic omit/meta when channel empty or filled |
| **Atoms** | Live (Phase 1) — atom browser |
| **Vectors** | **Live + honest (PR7 / KD18 + PR-R5)** — channel auto/toggle, health honesty, neighbors, rebuild notes |
| **Graph** | **Stub** — Phase **2a** directed traversal / typed edges. **Out of scope** for Phase 2 |

Overview: `GET /api/memory` reports `tabs.vectors: {stub: false, phase: "2"}` and `tabs.graph: {stub: true, phase: "2a"}`.

### Vectors APIs (read-only)

| Endpoint | Purpose |
|----------|---------|
| `GET /api/memory/vectors` | Encoder health + index health: `vectors_ready`, `vectors_by_channel`, `joint_repair_remaining`, `search_mode`, `ann_index_built`, `last_optimize_notes`, buffer |
| `GET /api/memory/vectors/atoms?status=…&limit=50` | Atoms filtered by `embedding_status` via `list_atoms` (default 50, max 200); embed channel chips in UI |
| `GET /api/memory/vectors/neighbors?atom_id=…` or `?q=…` | Default **`channel=auto`**; resolve once → query vector for **concrete** only → `search(concrete)`; response `query.channel` / `resolved_channel` / `channel_reason`; score_kind `cosine`; empty → `omitted_reason` (e.g. `no_hits`) |
| `POST /api/memory/vectors/rebuild` | Idle optimize; always 200 with `optimized` bool + **`notes: list[str]`** (never silent fake success) |

**Query vector alignment:** explicit channel uses that channel only (no soft cross-channel fallback). `auto` resolves first, then loads vector for the **resolved** channel only (fixes pre-rectification query-on-text / search-on-joint mismatch).

**Glass honesty copy:** small corpus with `ann_index_built=false` is framed as “IVF not built — full scan still used,” not “search broken.”

**Invariants:** read-only; no secrets; **no raw 2048-d vector dumps** in responses by default; Graph remains stub. Optional 2D projection is **not** a Phase 2 gate.

---

## 8. Glossary

| Term | Meaning in Phase 2 |
|------|--------------------|
| **EmbeddingSet** | Bonded multi-channel 2048-d vectors for one atom/parcel |
| **Joint embedding** | Primary search key column: true multi-mod fusion **or** single-mod copy of sole modality |
| **Bonded channels** | Per-modality vectors on one instance — not separate atoms |
| **`auto` channel** | Resolve request → one concrete column (repair-pending safety); not multi-try fusion |
| **Joint-copy repair** | Fill null `emb_joint` from sole modality without encoder (open + idle) |
| **Parcel** | Size-split child atom (`kind=parcel`); parent remains experience chain member |
| **EncodeQueue** | In-process priority queue of pending atom_ids (P1 create / P2 catchup); drained by EncodeWorker (or idle rollback) |
| **EncodeWorker** | Presence-owned daemon that owns bulk corpus drain while `encode_owner=worker` |
| **encode_owner** | `none` \| `idle` \| `worker` — single drain owner protocol (KD-E7) |
| **EmbedderGate** | Exclusive shared-embedder forward lock; lookup priority over bulk between atoms |
| **GatedEmbedder** | Only public encode handle for meal/graph/API free-text (`encode_text` under lookup gate) |
| **EmbeddingIndex** | Façade for upsert/search/optimize/health over Lance or memory/null backends |
| **Recent buffer** | In-process vectors for hybrid search correctness under continuous insert |
| **`full_lance`** | Small-N / pre-IVF main-leg via Lance unindexed vector search (success path) |
| **Index stale** | Health signal: buffer non-empty, optimize due, or seed incomplete |
| **Semantic channel** | Supporting meal package section; not the open-moment spine |
| **Mock encoder** | Deterministic hash→unit vector path for CI and GPU-free dogfood |
| **Warm embedder** | Already loaded; required for meal-time query encode (consumer never blocks on cold load) |
| **Gate B** | Spike checklist before product default-on of semantic flags |

---

## 9. Tests (shipped coverage)

| File | Focus |
|------|-------|
| `tests/test_memory_embed_types.py` | EmbeddingSet dim; channel helpers; joint helpers |
| `tests/test_memory_embed_mock.py` | Deterministic vectors; L2 norm; joint-for-single **copy** |
| `tests/test_memory_embed_queue.py` | enqueue/drain caps; priority lanes; concurrent enqueue+drain; overflow → skipped |
| `tests/test_memory_embed_worker.py` | EncodeWorker continuous drain; owner; gate; busy death recovery; non-blocking ensure |
| `tests/test_memory_encoder_health.py` | encode_worker / gate / depth_by_priority health (no secrets) |
| `tests/test_memory_index.py` | Hybrid merge; filters; optimize guards; channel resolve; repair |
| `tests/test_memory_parcel.py` | Split before truncate; parent on chain; default off parity |
| `tests/test_memory_meal_semantic.py` | Budget v2; dedup; timeout; `no_hits` / `deduped`; meta |
| `tests/test_memory_semantic_integration.py` | Flags off = Phase 1 parity; semantic on + mock + fake index |
| `tests/test_memory_vectors_api.py` | Health honesty; neighbors auto; rebuild notes; fail closed |
| `tests/test_settings.py` | Invalid channel/backend/ivf min rejection |
| Phase 1 suite | Still green with semantic defaults off |

Hermetic CI: **no** torch, **no** GPU, **no** network. Lance tests skip-if-unavailable. Optional Nemotron path behind markers / missing-deps mock fallback.

---

## 10. Related docs

| Document | Role |
|----------|------|
| [design-phase-2-rectification.md](../../design/memory/design-phase-2-rectification.md) | **Normative fix plan** KD-R* + PR-R1–R6 (product-intent recovery) |
| [design-phase-2-implementation.md](../../design/memory/design-phase-2-implementation.md) | Historical implementation design, KDs, PR plan (PR1–PR9) |
| [design-phase-2-semantic.md](../../design/memory/design-phase-2-semantic.md) | Short phase outline (points here + implementation + rectification) |
| [design-embed-async-encode-worker.md](../../design/embed/design-embed-async-encode-worker.md) | **Normative continuous encode** — EncodeWorker, EmbedderGate, single-owner, KD-E1–E18 |
| [design-nemotron-runtime.md](../../design/memory/design-nemotron-runtime.md) | Portable encode contract; Gate B checklist |
| [spikes/lance-emb-migration.md](../../design/memory/spikes/lance-emb-migration.md) | Lance emb migration spike (Gate A) |
| [spikes/nemotron-runtime.md](../../design/memory/spikes/nemotron-runtime.md) | Nemotron runtime spike notes |
| [design-database-choices.md](../../design/memory/design-database-choices.md) | Lance ANN, interface rule |
| [design-context-meal-composition.md](../../design/memory/design-context-meal-composition.md) | Supporting channel + cut order |
| [architecture/phase-1-temporal.md](phase-1-temporal.md) | Phase 1 shipped manual |
| [design-phase-2a-directed-traversal.md](../../design/memory/design-phase-2a-directed-traversal.md) | Phase 2a boundary (Graph) — needs **rectified seeds** |
| [inspiration-activity-model-and-storage.md](../../design/memory/inspiration-activity-model-and-storage.md) | §3 activity baseline |
| [philosophical-soft-guidance.md](../philosophical-soft-guidance.md) | Judgment influences only |
| [known-bugs.md](../../known-bugs.md) | **BUG-mem-p2-01**, **BUG-mem-gpu-01** |

When behaviour changes, update **this** architecture note (and activity map) as part of done — historical design docs stay historical unless a decision is revised (rectification owns the product-path fix plan).

---

## 11. Follow-on packaging

| Work | Role |
|------|------|
| **Operator smoke dogfood** | Enable `backend=lance` + embed/semantic flags; verify neighbors/meal/repair on live corpus (code rectification landed; verification pending) |
| **Continuous encode dogfood** | Busy create→ready; `drain_ok_total` during multi-minute work; meal/API under text bulk; worker death resume; embed off→on — see **BUG-mem-gpu-01** product-path checklist (does **not** close packaging) |
| **Gate B / default-on** | Dogfood mock → Nemotron → optional default-on; flip only after operator sign-off ([design-nemotron-runtime.md](../../design/memory/design-nemotron-runtime.md)) |
| **BUG-mem-gpu-01** | Packaging / Tensile / multi-device matrix still Open; continuous encode **code path** shipped (worker + gate) |
| **Optional 2D projection** | Non-gate polish for Vectors tab (KD18) |
| **Phase 2a** | Directed traversal → **Graph** tab — **after** rectified semantic seeds |
| **Phase 3** | Procedural / success-path (evaluation-first); vector ANN ≠ procedure |

Phase 2 product surface: meal semantic + vector search + **Vectors glass** + continuous background encode + architecture note, with rectification closing the joint-empty dogfood hole and embed-async closing idle-only corpus starvation. Graph/hypergraph UI is Phase 2a. Flags stay off until dogfood proves latency and quality under `semantic_select_max_ms`.
