# Architecture — Phase 1 Temporal / Episodic Memory

**Status:** **Done** (2026-07-28) — Phase 1 operator-complete on `grok-improvement-memory` (PR1–PR9). Defaults: `memory.enabled` / `write_atoms` **on**. Deferred glass/prompt polish: [known-bugs.md](../../known-bugs.md).
**Package:** `elyra/memory/`
**Philosophy:** [memory-atoms.pdf](../../../memory-atoms.pdf)
**Design (planning):** [design-phase-1-temporal.md](../../../design/memory/design-phase-1-temporal.md), [design-phase-1-implementation.md](../../../design/memory/design-phase-1-implementation.md)
**Residual stack (closed):** [design-phase-1-remaining-pr8-pr9.md](../../../design/memory/design-phase-1-remaining-pr8-pr9.md)
**Meal sketch:** [design-context-meal-composition.md](../../../design/memory/design-context-meal-composition.md)
**Baseline activities:** [inspiration-activity-model-and-storage.md](../../../design/memory/inspiration-activity-model-and-storage.md) §3
**Program status:** [stretch-2 README](../README.md) Phase 1 close-out

This is the **post-implement concept-mapping manual** for Phase 1. It describes what shipped, how code maps to essay concepts, which activities are live, and how the system fails. It is not a re-statement of the design PR stack plan.

---

## What shipped

Phase 1 adds a durable episodic substrate that can run **without** embeddings, ANN, directed traversal, or success-path weights:

| Concern | Module / surface |
|---------|------------------|
| Atom record + period grids | `elyra/memory/types.py` |
| Swappable store Protocol + factory | `elyra/memory/store.py` |
| Default hermetic backend | `elyra/memory/jsonl_store.py` (`JsonlMemoryStore`) — **CI / default** |
| Optional Lance backend (PR8) | `elyra/memory/lance_store.py`; `memory.backend = "lance"` + `elyra[memory-lance]` |
| Beat → atom promotion | `elyra/memory/promote.py` |
| Range / walk helpers | `elyra/memory/temporal.py` |
| Period summary ladder (template-first) | `elyra/memory/ladder.py` |
| Labeled meal + slide-off + media expand | `elyra/memory/meal.py` |
| Glass inspect helpers | `elyra/memory/inspect.py` |
| Token split (`len//4`) | `elyra/memory/tokens.py` |
| Paths + `MemorySettings` | `elyra/memory/config.py` |
| Settings / flags | `Settings.memory` in `elyra/settings.py` (`[memory]` in elyra.toml) |
| Write path | `elyra/loop/doloop.py` (`promote_beat` on memorable beats) |
| Store open, wake promote, idle ladder, meal drop-in | `elyra/presence/worker.py` |
| Glass Memory page (PR9) | Context meal inspector, atoms list; Vectors/Graph **stubs** |

**Not shipped in Phase 1 (by design):** Nemotron embeddings, ANN ranking, directed keep-set, trajectory / success edges, automatic archival of fine atoms under summaries, historical backfill of pre-flag life, rich Vectors/Graph UIs.

**Deferred polish (not Phase 1 reopen):** Moments/Memory UI beautify, inspector flash, Status bugs, system-prompt soften — see [known-bugs.md](../../known-bugs.md).

---

## 1. Structure map

Essay / planning terms ↔ concrete implementation.

| Essay / planning term | Implementation |
|----------------------|----------------|
| Memory atom | `elyra.memory.types.Atom` (schema_version 1); persist via `MemoryStore.put_atom` |
| Moment as lived interval | Existing `MomentStore` open/close + beat tapes; atoms group by `Atom.moment_id` |
| Context (time) | `t_start` / `t_end`; period `window_start` / `window_end` + `scale`; moment membership |
| Consolidation | `ladder.py` summary atoms (`kind="summary"`) on UTC grids 15m→1h→6h→1d→1w→1m |
| Weave (temporal only) | `prev_atom_id` / `next_atom_id`; walks via `walk_next` / `walk_prev` |
| Warehouse anti-pattern | No fact-row merge; instances retained; summaries reference child ids in meta, they do not replace children |
| Working context vs durable memory | `meal.py` compose + slide-off (meal only) vs `MemoryStore` durability |
| Parcel (stub) | Kind `parcel` reserved; Phase 1 does not auto-split oversized content into parcels |
| Embedding status | Field `embedding_status` (`none`/`pending`/`ready`/`failed`); always `"none"` in Phase 1 writes |
| Temporary traversal buffer (Phase 2a) | **Not present**; must never appear in ladder sources or meal as durable (forward invariant) |

### Atom kinds (Phase 1 vocabulary)

| Kind | Role |
|------|------|
| `observation` | Social wake / user-facing ingress (and similar) |
| `speak` | Delivered speak (tool-shaped beats promoted as kind speak) |
| `tool` | Memorable tool results (preview + density cap) |
| `model` | Selected model content above min-char threshold |
| `ledger` | Goal/task create/update one-liners |
| `summary` | Ladder period summary (stable id `as_…`) |
| `parcel` | Reserved |
| `moment_meta` | Reserved; **not** promoted for wakes (density / BUG-wake-01) |

### Public store API (`MemoryStore`)

| Method | Purpose |
|--------|---------|
| `put_atom` / `get_atom` | Insert-or-replace by `atom_id` |
| `update_links` | Patch sequential prev/next only |
| `list_by_moment` | Open-moment temporal spine |
| `list_range` | Half-open `[t_start, t_end)` by atom `t_start` |
| `list_summaries` | Ladder index by scale / overlapping window |
| `moment_tail` / `global_tail` | Link promotion heads (excludes summary/parcel/moment_meta from chain tails) |
| `walk_next` / `walk_prev` | Sequential weave walk |
| `delete_atom` | Admin/tests only — **meal never calls this** |
| `health` | `{ok, backend, atom_count, …}` for glass status |
| `close` | Mark store unusable |

Factory: `open_memory_store(paths, settings)` → jsonl; `backend=lance` logs and falls back to jsonl until a Lance implementation exists.

### On-disk layout (`{ELYRA_HOME}/data/memory/`)

```text
meta.json           # schema_version, backend, created_at
atoms.jsonl         # append-only rows; latest atom_id wins; tombstones `_deleted`
atoms/{id[:2]}/…    # blob spill when body > inline_max_chars (default 4000)
ladder/state.json   # round-robin scale index + last_refresh per scale
```

### Feature flags (`MemorySettings`)

| Flag | Default | Effect |
|------|---------|--------|
| `write_atoms` | `true` | Promote beats / wake observations into the store |
| `enabled` | `true` | Outer meal uses labeled memory package (no full sliding glass) |
| `ladder_enabled` | `true` | Idle/finalize ladder when write_atoms **or** enabled and store open |
| `backend` | `jsonl` | `jsonl` \| `lance` (lance → jsonl fall-back in Phase 1) |

Defaults: write path + memory meal both on. Rollback: set `enabled=false` (glass meal) and/or `write_atoms=false`; atoms remain on disk inert.

### Integration hooks

| Hook | Where | When |
|------|-------|------|
| `promote_beat` | `doloop._record_beat` | After memorable beat append (best-effort, never changes hop outcome) |
| `promote_wake_observation` | `presence.worker` after `open_moment` | Social wakes only |
| `refresh_due` | presence idle tick | Budgeted (`ladder_max_ms_per_tick`, default 50ms); one scale/tick round-robin |
| `refresh_window(…, "15m")` | moment finalize | Fine-scale catch-up on close |
| `compose_outer_messages` + `expand_memory_meal_for_provider` | `rebuild_outer` when `enabled` and store healthy | Drop-in outer meal + media expand parity |

---

## 2. Activity map (§3 inspiration)

Which [§3 activities](../../../design/memory/inspiration-activity-model-and-storage.md) are live after Phase 1.

### 3.1 Write / ingest

| Activity | Live? | Module / notes |
|----------|-------|----------------|
| Create atom from beat / speak / tool | **Yes** (`write_atoms`) | `promote.promote_beat` + doloop |
| Create atom from social wake | **Yes** (`write_atoms`) | `promote.promote_wake_observation` |
| Attach media refs | **Yes** | `Atom.media_ids` from beat / wake |
| Sequential prev/next link | **Yes** | `_link_and_put` (moment tail; optional global tail) |
| Link to contextual influencers | **No** | Later weave kinds |
| Write multi-embeddings | **No** | Phase 2 (`embedding_status` stub only) |
| Split oversized content into parcels | **Partial** | Truncate / blob spill; no parcel chain |
| Goal/outcome trajectory markers | **No** | Phase 3; ledger atoms are one-liners only |
| Online edge-weight update | **No** | Phase 3 |

### 3.2 Temporal / episodic

| Activity | Live? | Module / notes |
|----------|-------|----------------|
| Time-range query | **Yes** | `store.list_range` / `temporal.list_range` |
| Sequential walk | **Yes** | `walk_next` / `walk_prev` |
| Refresh period summary ladder | **Yes** (idle + finalize) | `ladder.refresh_due` / `refresh_window` |
| Fetch active ladder summaries for now | **Yes** | `meal.select_episodic` summary pass |
| Query prior moments for episodic fill | **Yes** | `select_episodic` raw fill |

### 3.3 Meal composition

| Activity | Live? | Module / notes |
|----------|-------|----------------|
| Compose labeled meal | **Yes** (`enabled`) | `meal.compose_meal` / `compose_outer_messages` |
| Dedup atoms across channels | **Yes** | Open moment wins over episodic prior |
| Slide-off open-moment under budget | **Yes** | `slide_off_temporal` (meal only) |
| Re-gather on moment boundary | **Yes** | `rebuild_outer` on open/close path |
| Optional re-gather every N hops | **Wired off** | `regather_every_n_hops=0` default |

### 3.4–3.6 Semantic / directed / procedural

| Regime | Live? |
|--------|-------|
| ANN / embeddings / index optimize | **No** — Phase 2 |
| Directed multi-hop / temporary keep-set | **No** — Phase 2a |
| Trajectories / success weights | **No** — Phase 3 |

### 3.7 Operational

| Concern | Live? | Notes |
|---------|-------|-------|
| Restart-safe under `ELYRA_HOME` | **Yes** | JSONL reload latest-wins |
| Hermetic tests without external servers | **Yes** | jsonl default; no lancedb in CI |
| Single-writer friendly | **Yes** | Presence worker; `RLock` on store |
| Backup ≈ copy data dir | **Yes** | Copy `data/memory/` with `data/moments/` |

---

## 3. Invariants

Normative rules operators and later phases must preserve.

1. **Slide-off never deletes durable atoms.**  
   `slide_off_temporal` only omits atoms from the meal package and may inject a meal-only `temporal/compact` glue block. Store `atom_count` is unchanged. `delete_atom` is admin/tests only.

2. **Temporary / directed context never enters the ladder or durable meal channels.**  
   Phase 2a temporary buffers must not be written as atoms used by `collect_window_sources` or `select_episodic`. Phase 1 has no temporary channel; do not smuggle one into summary sources.

3. **Control-plane observations never promote.**  
   `CONTROL_OBS_KINDS` and `thrash*` kinds are skipped (continue, thrash, skill_commit, tool_skip_identical, …).

4. **Summaries are instances with stable ids, not warehouse facts.**  
   `stable_summary_id(scale, window_start)` → replace-in-place for the same window. Child experience remains as separate atoms.

5. **Ladder excludes non-experience kinds from raw sources.**  
   When collecting raw experience for a window, kinds `summary`, `parcel`, `moment_meta` are excluded (child-scale summaries are preferred for coarser scales).

6. **Promote is best-effort and never changes hop outcome.**  
   Exceptions are logged; `DoLoopResult` / stop reasons are independent of memory I/O.

7. **Single-writer assumption.**  
   One presence worker owns put/link/ladder/compact. Concurrent multi-process writers are unsupported on jsonl.

8. **Feature flags cleanly degrade.**  
   - Both flags false → no store open; legacy glass meal.  
   - `write_atoms` only → atoms + ladder; glass meal.  
   - `enabled` + healthy store → labeled memory meal; full sliding glass excluded from outer.  
   - Store open failure → log once; promote/ladder/meal no-op / legacy fall-back for the worker lifetime.

9. **Media continuity under `enabled`.**  
   Atoms carry `media_ids`; expand path uses meal media (and hybrid single glass wake row if wake atom missing). Must not reintroduce full `limit=80` sliding glass.

10. **Secrets hygiene.**  
    Promote reuses redacted beat content already safe for tapes; never re-parses raw secret tool arguments into atoms.

11. **Dedup.**  
    Same atom appears at most once in a package; open-moment membership wins over episodic prior blocks.

12. **Tool density (KD16).**  
    Ok-tool bodies are preview-capped (`tool_ok_preview_chars`, default 240); non-speak non-ledger tools soft-capped per moment (`max_tool_atoms_per_moment`, default 48); failures exempt after cap.

---

## 4. Failure modes

| Failure | Severity | Behaviour |
|---------|----------|-----------|
| Store open fails | Med | Log once; `memory=None` for worker life; promote no-op; meal falls back to glass |
| `put_atom` / link I/O error | Low | Log; drop that atom; loop continues |
| Ladder exception / timeout | Low | Partial refresh; next idle continues; never blocks hop |
| Corrupt JSONL line | Low | Skip line; `health.corrupt_lines` increments |
| `backend=lance` without implementation | Low | Warning; open jsonl |
| Meal over budget with only protected rows | Low | Keep protected (media, wake, tail, latest speak/failed tool); package may slightly exceed cap |
| Dual systems (glass vs atoms) | Med | When `enabled`, outer uses atoms not full glass; tapes remain forensic source of truth |
| Wake storm density (BUG-wake-01) | Med | No moment_meta atoms; non-social wakes skip wake obs; control kinds never promote |
| Media expand missing under `enabled` | High | Mitigated: `media_ids` + hybrid wake inject; integration tests gate parity |
| Tool spam | Med | Preview + per-moment cap |
| Promote idempotency hit | Low | Silent skip (same key already stored) |
| JSONL compact failure | Low | Temp file cleaned; old file retained; log |

---

## 5. Glossary

| Term | Meaning in Phase 1 |
|------|--------------------|
| **Beat** | Append-only moment-tape event (`MomentStore`); not necessarily memorable |
| **Atom** | Durable memory instance with time, kind, content, optional moment and links |
| **Moment** | Do-loop / presence lived interval; atoms reference `moment_id` |
| **Summary atom** | Ladder consolidation for a UTC grid window; stable id; template body |
| **Ladder** | Ordered scales 15m → 1h → 6h → 1d → 1w → 1m with child-summary preference |
| **Meal / meal package** | Working-set messages for one model call: labeled temporal + episodic (+ system/orient) |
| **Slide-off** | Drop oldest unprotected open-moment atoms from the **meal only**; optional compact glue text |
| **Compact (meal)** | In-meal-only text summarizing slid-off span — **not** a ladder atom |
| **Compact (JSONL)** | Rewrite `atoms.jsonl` to one latest line per id (idle / admin) |
| **Promote** | Normative filter turning selected beats/wakes into atoms + sequential links |
| **Episodic channel** | Prior-moment raw atoms + ladder summaries outside the open moment |
| **Temporal channel** | Open-moment atoms (plus optional slide-off compact) |
| **Glass** | Stretch 1 sliding message history; legacy outer meal when memory meal off |
| **Chain** | In-turn assistant/tool hops owned by doloop; not part of memory meal package |

---

## 6. Restart, load, and JSONL compaction

### Restart behaviour

1. On first need (`write_atoms` or `enabled`), worker calls `open_memory_store`.
2. `JsonlMemoryStore` ensures dirs + `meta.json`, then **replays** `atoms.jsonl`:
   - Empty / missing file → empty indexes.
   - Each non-empty line: JSON parse; `_deleted` tombstone removes id; else `atom_from_dict` + blob hydrate; **latest line for an `atom_id` wins**.
   - Corrupt lines are skipped (`corrupt_lines` in health).
3. Secondary indexes rebuilt: by moment (time-ordered ids), ladder `(scale, window_start) → atom_id`.
4. Ladder `state.json` is independent soft state (round-robin index); missing/corrupt → defaults.

Atoms created only while flags allowed writes; pre-flag history remains in glass/tapes until an optional backfill (not a Phase 1 gate).

### Blob spill

Bodies longer than `inline_max_chars` (default 4000) write to `atoms/{prefix}/{atom_id}.txt` with `content_ref="blob:…"`. Shrink/replace re-derives locator from current body length; orphan blobs cleaned on replace/delete when possible.

### Compaction (JSONL)

| API | Role |
|-----|------|
| `needs_compact()` | Dirty lines ≥ `jsonl_compact_dirty` (256) **or** file size ≥ `jsonl_compact_bytes` (8 MiB) |
| `maybe_compact()` | Compact if needed; return whether rewrite ran |
| `compact()` | Force rewrite: one latest row per atom_id, sorted by time |

**Rules:**

- Compaction is **idle / admin only** — never mid-hop on the promote path.
- Rewrite is atomic via temp file + replace.
- After compact, `line_count` equals live atom count; tombstones and stale versions are dropped from the file (in-memory already latest-wins).
- Restart without compact remains correct; compact only bounds growth and load time.

### Concurrency

One `threading.RLock` per store instance. Presence is the intended single writer. Readers in the same process take the lock briefly for consistent snapshots.

---

## 7. Meal labels (operator-facing)

Messages render with a `[context:…]` header:

| Label pattern | Channel | Content |
|---------------|---------|---------|
| `episodic/summary {scale}` | episodic | Ladder summary body |
| `episodic/prior-moment {short_id}` | episodic | Formatted prior-moment atom lines |
| `temporal/compact` | temporal | Meal-only slid-off glue (ephemeral) |
| `temporal/moment {short_id}` | temporal | Open-moment atom lines |

Order of outer messages: **system → episodic → temporal → orient** (chain appended by doloop).

Budget: residual after system+orient split by `episodic_fraction` (default 0.20) vs temporal; default meal budget **50_000** tokens (`len//4` heuristic, same as glass).

---

## 8. Tests (shipped coverage)

| File | Focus |
|------|-------|
| `tests/test_memory_types.py` | Validation, summary id stability, window bounds |
| `tests/test_memory_store.py` | Protocol over jsonl; compaction; lock smoke |
| `tests/test_memory_temporal.py` | Range / windows / child scales |
| `tests/test_memory_promote.py` | R1–R10, control kinds, tool cap, wake dedupe |
| `tests/test_memory_ladder.py` | Rollup, replace-stable id, `max_ms` budget |
| `tests/test_memory_meal.py` | Labels, episodic golden, slide-off non-delete, media protect |
| `tests/test_memory_context_integration.py` | Flag on/off, budget, orient last, image/media-only wake expand |
| `tests/test_memory_flag_fallback.py` | Store None / open fail; promote never raises |

---

## 9. Related docs

| Document | Role |
|----------|------|
| [design-phase-1-implementation.md](../../../design/memory/design-phase-1-implementation.md) | Implementation design, key decisions, promote rules R1–R10 |
| [design-phase-1-temporal.md](../../../design/memory/design-phase-1-temporal.md) | Short phase outline (superseded for implement detail) |
| [design-context-meal-composition.md](../../../design/memory/design-context-meal-composition.md) | Meal channels, slide-off, re-gather sketch |
| [design-database-choices.md](../../../design/memory/design-database-choices.md) | Future Lance path; Protocol boundary |
| [inspiration-activity-model-and-storage.md](../../../design/memory/inspiration-activity-model-and-storage.md) | §3 activity baseline |
| [philosophical-soft-guidance.md](../../../stretch-2/philosophical-soft-guidance.md) | Judgment influences only |

When behaviour changes, update **this** architecture note (and activity map) as part of done — design docs stay historical unless a decision is revised.

---

## 10. Follow-on packaging (PR8 / PR9 → Phase 2)

Normative product sequence after Phase 1 core (detail in [design-phase-1-implementation.md](../../../design/memory/design-phase-1-implementation.md) PR Plan):

| Work | Role |
|------|------|
| **PR8** | Optional **Lance** backend — Protocol parity only; no glass, no ANN columns yet. |
| **PR9** | Glass **Memory** page: **context meal inspector** (primary), light atom browser, **Vectors** and **Graph** tabs as stubs. |
| **Phase 2** | Semantic embeddings + ANN → implement **Vectors** tab for real. |
| **Phase 2a** | Directed traversal / typed edges → implement **Graph** tab for real. |

Do not require rich vector or hypergraph visualization for Phase 1 correctness. Moments panel remains the tape debugger; Memory page is meal + store inspection.
