# Design: Episodic Summary Ladder — LLM Narratives, Hourly Cascade & Version Archaeology

| Field | Value |
|-------|--------|
| **Class** | DESIGN |
| **Status** | Design (normative for `#92` / BUG-meal-02) |
| **Issue** | [#92](https://github.com/jtwolfe/project-elyra/issues/92) — `BUG-meal-02` |
| **Integration branch** | `feature/92` (base `main` @ ~`66d7782`) |
| **Product branch rule** | **KD-BR:** every product implementation branch is cut **FROM `feature/92`** (or previous stack tip after merge into `feature/92`). Never from bare `main`. Stack: child `fix/BUG-meal-02-92-sN-…` → merge into `feature/92` → dogfood → `feature/92` → `main`. |
| **Repo landing path** | `docs/stretch-2/design-episodic-summary-ladder-llm.md` (on `feature/92`) |
| **Area** | `elyra/memory/ladder.py`, `temporal.py`, `types.py`, `config.py`, `meal.py`, `weights.py`, `graph.py`, presence idle hook |
| **Audience** | Senior implementers |
| **Related (do not conflate)** | #93 glass-tail (done), #98 source edges, #103/#105 traverse depth, #104 directed keep |
| **Philosophy** | Prefer LLM/prompt nudges over hard constraints; soft continuous-work merge is **prompt-only** |

---

## Overview

Phase 1 shipped a **template-first** period ladder (`render_template_summary` / `select_highlights` / `refresh_due` with 50 ms idle nibble). Dogfood shows those atoms are unreadable highlight lists (~160-char lines), not “what happened.” This design upgrades the ladder into a **budgeted LLM narrative system** with:

1. **Scale set** `1h → 1d → 1w → 1m → 1y` (drop `15m` / `6h` for *new* writes).
2. **Hourly schedule** that closes 1h windows from moments/atoms, then **cascades** coarser heads.
3. **Two-pass LLM** (draft → scale budget) with **template fallback**.
4. **Rich archaeology:** never delete closed 1h; coarser scales get **versioned** rewrites with supersedes links.
5. **Edge fabric at write time** (summary↔children, 1h→sources, supersedes); **lite** runtime expand by default.
6. **Meal tip policy:** current tip per coarser scale + recent 1h only — not the full version archive.

The stored body quality is the product fix; Glass Context already displays summary atoms.

---

## Background & Motivation

### What shipped (truth on `feature/92`)

| Piece | Location | Behaviour today |
|-------|----------|-----------------|
| Scales | `elyra/memory/types.py` `PERIOD_SCALES` / `PERIOD_SCALE_ORDER` | `15m → 1h → 6h → 1d → 1w → 1m` |
| Child map | `elyra/memory/temporal.py` `_CHILD_SCALE` | `1h→15m`, `1d→6h`, … |
| Template body | `ladder.render_template_summary` | Fixed header + highlight bullets + open threads |
| Stable id | `types.stable_summary_id(scale, window_start)` | `as_` + sha256; **replace-in-place** |
| Build/store | `build_summary_atom` / `refresh_window` | Template only; `meta.source = "template"`; skip put if body unchanged |
| Idle tick | `ladder.refresh_due` + `PresenceWorker._idle_memory_ladder` | Round-robin **one scale**, `ladder_max_ms_per_tick` default **50** |
| Finalize hook | `_finalize_memory_ladder_15m` | Refreshes **15m** after moment close |
| Meal pack | `meal.select_episodic` `_SUMMARY_PACK_ORDER` | Coarse→fine: `1m…15m`; **current + previous** window each scale |
| Ladder index | `JsonlMemoryStore._ladder[(scale, window_start)] → atom_id` | **One tip per window**; overwrites on put |
| Edges | `graph.py` / `weights.py` | Projected sequential / parent_of / child_of / same_moment / semantic_hop — **no summary edges** |
| LLM | Phase 1 design explicitly **out of path** | `summary_mode` sketched, never shipped |

### Product pain

- Episodic meal blocks read as inventory, not memory (`BUG-mem-ui-01` residual → `BUG-meal-02`).
- 50 ms round-robin across six scales means coarser heads lag and never get real narrative work.
- Replace-in-place erases how the “day story” evolved when a new hour lands.
- `meta.child_atom_ids` (cap 64) is write-side only; GraphView cannot walk summary→child.

### Locked operator decisions (normative)

See **Key Decisions**. Treat as product law for this design; soft constraints philosophy still applies *inside* generation.

---

## Goals & Non-Goals

### Goals

1. **Readable period narratives** in stored `content_text` for meal + Glass Atoms/Context.
2. **Budgeted LLM path** with two-pass when useful; **template fallback** always available.
3. **Hourly well-defined schedule** + cascade; catch-up after downtime; respect usage hard-stop / pacing.
4. **Retention:** closed **1h** kept long-term; coarser **version archaeology** (new version per cascade).
5. **Meal:** only **current tips** + recent 1h — not archive dump.
6. **Edge fabric** recorded comprehensively at write; **lite** runtime walk by default; deep mode later (Phase 3 / #103).
7. **Idle / gap honesty** in narrative (LLM-formatted absence, not fake work, not silent omit).
8. Soft **self/other** naming via existing identity digests; soft pointers to `a_…` / `g_…` / `t_…`.

### Non-Goals

- Re-open #72 chrome / #93 glass-tail work.
- Full Phase 3 success-path edge weighting (hang hooks only).
- Hard programmed continuous-work span merge engine (prompt-only if at all).
- Hard-delete / GC of closed 1h in this issue.
- Hard migration deleting legacy `15m`/`6h` atoms (optional legacy **read**).
- Multi-user 2-minute grid (future density hint only; base grain = ~one narrative point per moment).
- Calling LLM on hop / `rebuild_outer` path.
- Solving #98/#103/#104/#105 as primary scope (coordinate naming only).

---

## Current → Target Architecture

### Mental model

```text
Moments / atoms  ──►  1h narrative (closed hours accumulate forever)
                         │
                         ▼ cascade after each new closed 1h
                      1d tip(k)  ──version──► 1d tip(k+1) …
                         │
                         ▼
                      1w tip …
                         │
                         ▼
                      1m tip …
                         │
                         ▼  (when instance age allows)
                      1y tip …
```

After each hour lands: coarser **heads shift** (recompute tips) while the fine **1h series accumulates**. Meal sees only tips + a short recent 1h band.

### Sequence (hourly + cascade)

Logical “edge factory” = **meta lists written on the ladder put path** (`meta.source_atom_ids` / `child_atom_ids` / `supersedes_atom_id`); GraphView **projects** those fields at expand time — no separate `edge_factory` module.

```mermaid
sequenceDiagram
  participant P as PresenceWorker idle
  participant L as ladder.tick_hourly
  participant S as MemoryStore
  participant C as ChatClient + UsageMeter

  P->>P: claimed is None; after _fire_due
  P->>L: process_due (outside lock)
  L->>S: find closed 1h windows not tip-current / missing
  L->>S: collect sources (moments/atoms in window)
  alt summary_mode=llm and meter.can_call
    L->>C: pass A draft (sources)
    L->>C: pass B reduce to 1h budget
  else fallback
    L->>L: render_template_summary
  end
  L->>S: put_atom 1h + meta source_atom_ids (capped)
  Note over L,S: write-time fabric = atom meta only
  loop cascade parents via parent_scale_write 1d→1w→1m→1y
    L->>S: collect child tip blurbs in parent window
    L->>C: two-pass or template
    L->>S: put NEW version atom; tip index → new id
    Note over L,S: meta child_atom_ids + supersedes_atom_id
  end
  L->>S: ladder/state.json progress
```

### Module map

| Concern | Module | Change class |
|---------|--------|--------------|
| Scales + window grids + ids | `types.py` | Add `1y`; deprecate write of `15m`/`6h`; version-id helper |
| Child/parent | `temporal.py` | Write child/parent maps: `1d→1h`, …; `parent_scale` uses **write order** (no 6h) |
| Template + LLM + schedule | `ladder.py` (split optional: `ladder_llm.py`) | Core rewrite |
| Settings | `config.py` / `settings.py` | `summary_mode`, LLM budgets, schedule knobs, edge depth |
| Meal tip select | `meal.py` | Pack order + tip-only + recent 1h |
| Store tip index | `jsonl_store.py` / `lance_store.py` | Tip vs versions; list tips |
| Edge kinds + weights | `weights.py`, `graph.py` | New kinds; project from meta / edge table |
| Presence hook | `worker.py` | Hourly schedule + cascade; drop 15m finalize or replace with 1h nudge |
| Identity soft names | `identity.store` (read-only) | Inject display names into LLM system prompt |

---

## Proposed Design

### 1. Scale set

#### Normative scale allowlists (write vs all)

```python
# types.py — normative constants
PERIOD_SCALES_WRITE: frozenset[str] = frozenset({"1h", "1d", "1w", "1m", "1y"})
PERIOD_SCALE_ORDER_WRITE: tuple[str, ...] = ("1h", "1d", "1w", "1m", "1y")

PERIOD_SCALES_LEGACY: frozenset[str] = frozenset({"15m", "6h"})
PERIOD_SCALES_ALL: frozenset[str] = PERIOD_SCALES_WRITE | PERIOD_SCALES_LEGACY
# PERIOD_SCALES remains == PERIOD_SCALES_ALL for window_bounds / read validation
# PERIOD_SCALE_ORDER (fine→coarse, read/legacy-aware):
PERIOD_SCALE_ORDER: tuple[str, ...] = (
    "15m", "1h", "6h", "1d", "1w", "1m", "1y",
)
```

| Scale | Grid | Child sources | Grow-in rule (instance age) |
|-------|------|---------------|-----------------------------|
| `1h` | UTC hour floor | Raw experience atoms (non-summary) in window; moment-grain narrative points | Always (once write_atoms active) |
| `1d` | UTC midnight | Up to 24× **tip** 1h in day | Always once ≥1 closed 1h exists |
| `1w` | Monday 00:00 UTC | Up to 7× tip 1d | After ~7d instance age **or** enough 1d tips (provisional: ≥3) |
| `1m` | 1st of month UTC | Tip 1w (or 1d fallback if weeks sparse) | After ~28d or enough 1w (provisional: ≥2) |
| `1y` | Jan 1 UTC → next | Tip 1m | After ~365d or enough 1m (provisional: ≥2) |

**Drop for new writes:** `15m`, `6h`.

**Legacy read:** `list_summaries` / meal may still load existing `15m`/`6h` if present; do not require hard migration or delete. `_SUMMARY_PACK_ORDER` eventually omits them for *selection preference* once tips exist at 1h/1d.

#### Child / parent maps (write-era; no 6h parents)

Today `parent_scale` walks `PERIOD_SCALE_ORDER`, so parent of `1h` is **`6h`**. That must not survive on the write path.

```python
# temporal.py — write ladder structure
_CHILD_SCALE_WRITE = {
    "1h": None,      # raw atoms
    "1d": "1h",
    "1w": "1d",
    "1m": "1w",
    "1y": "1m",
}
_PARENT_SCALE_WRITE = {
    "1h": "1d",
    "1d": "1w",
    "1w": "1m",
    "1m": "1y",
    "1y": None,
}
# Legacy-only (read / optional repair when ladder_write_legacy_scales):
_CHILD_SCALE_LEGACY = {"15m": None, "6h": "1h"}
_PARENT_SCALE_LEGACY = {"15m": "1h", "6h": "1d"}  # informational; not used when flag false
```

**Normative API:**

| Function | Behaviour |
|----------|-----------|
| `child_scale(scale)` | Write map for scales in `PERIOD_SCALES_WRITE`; legacy map for `15m`/`6h`; raises on unknown |
| `parent_scale(scale)` | **Write parent map** for write scales (`1h→1d`, never `1h→6h`); legacy map only for legacy scales |
| `parent_scale_write(scale)` | Alias used by cascade — iterates `PERIOD_SCALE_ORDER_WRITE` ancestors only |
| Cascade / `tick` / `refresh_due` nibble | **Write order only** when `ladder_write_legacy_scales=false` (default) |

**Invariant:** *No write-path parent edge through `15m`/`6h` when `ladder_write_legacy_scales=false`.* Cascade is:

```text
s = "1h"
while (p := parent_scale(s)) is not None:  # 1d, 1w, 1m, 1y
    recompute tip for window containing H at scale p
    s = p
```

Validation:

- **Write path** rejects new `15m`/`6h` when `ladder_write_legacy_scales=false` (default).
- **Read path** accepts all of `PERIOD_SCALES_ALL`.

#### Window bounds for `1y`

Extend `window_bounds` in `types.py` (accepts any scale in `PERIOD_SCALES_ALL`):

```python
elif scale == "1y":
    start = dt.replace(month=1, day=1, hour=0, minute=0)
    end = start.replace(year=start.year + 1)
```

### 2. Generation quality

#### Modes

| `memory.summary_mode` | Behaviour |
|----------------------|-----------|
| `template` | Current `render_template_summary` only (tests, offline, CI default hermetic) |
| `llm` | Try LLM; on fail / hard-stop / timeout → template; stamp `meta.source` |

Default for dogfood product: **`llm`** when a real ChatClient + meter are available; **`template`** in unit tests / stub clients.

#### Two-pass pipeline

```text
Pass A — draft
  Input: structured source pack (see §2.3)
  Output: rich draft narrative (may exceed final budget)
  Max tokens: scale-specific draft ceiling

Pass B — reduce (when draft exceeds scale budget or always for 1d+)
  Input: draft + scale budget + pointer schema reminder
  Output: final content_text for storage
  Skip B if draft already under budget (1h often)
```

**When two-pass is “useful” (normative heuristic):**

- Always for `1d`, `1w`, `1m`, `1y`.
- For `1h`: pass A only if source pack tokens ≤ draft ceiling; if sources huge, still A then B.
- Template path: single pass, no LLM.

#### Soft narrative targets (not hard char counters)

| Scale | Target shape | Approx tokens (soft) |
|-------|--------------|----------------------|
| `1h` | 2–6 short paragraphs; cover moments + gaps | 250–600 |
| `1d` | ~24× reduced 1h blurbs → 2–3 sentences each *compressed*; day arc | 800–1800 |
| `1w` | ~7× 1d at 3–5 sentences each, week arc | 1200–2500 |
| `1m` | month themes from weeks | 1500–3000 |
| `1y` | year-level themes from months | 2000–4000 |

Hard store cap remains `MemorySettings.atom_max_chars` (8000) with blob spill above `inline_max_chars` (4000). LLM max_tokens set below these.

#### Required content behaviours (prompt-first)

1. **Idle / no-work periods:** note gap spans with no moments (LLM-formatted). Do not invent work; do not omit large empty ranges silently.
2. **Pointers in prose + structured meta:**
   - Prose: soft mentions of important atoms / goals / tasks.
   - Meta: `meta.pointer_atom_ids`, `meta.pointer_goal_ids`, `meta.pointer_task_ids` (capped lists; extracted from sources + optional JSON trailer parse).
3. **User-linked vs independent work:**
   - User-linked: “Jim asked…” (display name from `UsersStore` / orient user digest).
   - Independent: “I decided…” (self `display_name` from `IdentityStore`, default Elyra).
4. **Soft continuous-work span merge:** prompt-only (“you may merge continuous tool chains into one beat”). **No** hard skip-parent-recompute engine.
5. **No warehouse collapse:** do not invent facts not grounded in sources; prefer under-claim.

#### Source pack for LLM (structured, not raw dump)

**Named helpers (PR-A; required so gaps are not skipped):**

| Symbol | Responsibility |
|--------|----------------|
| `ladder.moment_blocks_for_window(store, w_start, w_end, …)` | Group raw atoms by `moment_id`; emit ordered moment blocks with ranked lines (`select_highlights` / `_highlight_rank`) |
| `ladder.gap_spans(window_start, window_end, moment_intervals)` | Half-open empty ranges with no moments; min gap threshold e.g. ≥5 minutes to emit |
| `ladder.build_source_pack(scale, window_start, window_end, sources, *, identity_names, from_children)` | Render structured pack text (or structured dict + `format_source_pack`) for LLM / template diagnostics |

Tests: empty mid-window range appears in pack text (KD6). Do not dump flat atom lines without gap spans.

For **1h** (from raw):

```text
[window 1h | ws → we]
[identity] self=Elyra; user=Jim (soft names only)
[moments]
- m_… t0–t1 why_now=… n_atoms=…
  - speak: …
  - observation: …
  - ledger: goal g_… / task t_…
  - tool fail: …
[gaps]
- no moments from T1 to T2 (~Nm)
[highlights ranked] …  (reuse select_highlights for prioritization)
```

Caps: e.g. max **40** moment blocks; per-moment **8** atom lines; line truncate 200 chars; total pack ~6–12k tokens soft. Prefer speak / observation / ledger / failed tool (same rank as `_highlight_rank`).

For **coarser** (from child **tips** only):

```text
[window 1d | …]
[child tips scale=1h]
- 2026-07-31T10:00Z as_… : <content_text full or first N chars>
- …
[missing child windows] list closed hours without tips
[gaps] empty hours noted explicitly
```

#### Template fallback body

Keep `render_template_summary` as hermetic baseline; extend header slightly for new scales and note `source=template`. Still useful for empty-ish windows and tests.

#### Meta honesty (every summary atom)

```python
meta = {
  "source": "llm" | "template" | "llm_fallback_template",
  "summary_mode_requested": "llm" | "template",
  "from_children": bool,
  "child_scale": "1h" | None,
  "child_atom_ids": [...],          # ≤ MAX_CHILD_IDS (raise from 64 → 96 for 1d)
  "source_atom_ids": [...],         # 1h only; capped for edge fabric
  "n_atoms": int,
  "n_moments": int,
  "n_speak": int,
  "n_tool": int,
  "pointer_atom_ids": [...],
  "pointer_goal_ids": [...],
  "pointer_task_ids": [...],
  "version": int,                   # 1-based per (scale, window_start); 1h always 1 under tip-replace
  "supersedes_atom_id": str | None, # new tip → previous tip id (coarser versions)
  "previous_version_id": str | None,  # synonym / chain helper; same as supersedes when set
  "llm_model": str | None,
  "llm_passes": 0 | 1 | 2,
  "llm_error": str | None,          # short reason if fallback
  "draft_chars": int | None,
  "generated_at": iso_z,
  # NOTE: do NOT rely on meta.is_tip — tip-ness is ladder index only (KD-TIP).
}
```

### 3. Identity / stable ids / version archaeology

#### 1h atoms — closed hours accumulate

**Policy (rich archaeology):**

- **Open (current) hour:** may refresh in place while the hour is still open (stable id OK), using template or cheap LLM only if operator enables mid-hour refresh; default **defer LLM until hour closes**.
- **Closed hours:** once a 1h window end ≤ now and body is “finalized”, **do not delete**. Prefer:
  - **Option A (recommended):** keep `stable_summary_id` for the **tip** of each 1h window; mid-hour rewrites replace tip; after finalize flag `meta.finalized=true` and skip-unchanged unless sources hash changes (catch-up). No version chain needed for 1h unless re-finalization after late-arriving atoms.
  - **Late atoms:** if promote writes land after finalize (clock skew / downtime catch-up), recompute 1h and cascade parents; if content changes, either replace tip (same id) **or** version 1h as well. Prefer **replace tip + cascade** for 1h to avoid 24× version fan-out; coarser scales still version.

#### Coarser scales — new version per cascade

Replace silent forever-overwrite for `1d` / `1w` / `1m` / `1y`:

1. Build new atom with **new id** (not `stable_summary_id` alone):

```python
def versioned_summary_id(scale, window_start, version: int) -> str:
    key = f"{scale}|{to_iso_z(window_start)}|v{version}"
    return "as_" + sha256(key.encode()).hexdigest()[:20]
```

2. Ladder index maps `(scale, window_start) → tip_atom_id` without requiring tip id to equal `stable_summary_id`. (`stable_summary_id` remains for **1h** tip identity / tests.)

3. On cascade (**normative PR-B policy = immutable old + tip pointer** — option A):
   - Read previous tip from ladder index (if any).
   - `version = prev.meta.version + 1` (or 1).
   - `put_atom(new_version)` only — **never** rewrite previous version rows to flip flags.
   - Move tip: `_ladder[(scale, window_start)] = new_id`.
   - New atom meta: `supersedes_atom_id=prev.atom_id`, `version=n`.
   - Previous atom body/meta left **immutable** as written.

#### Tip-ness truth (KD-TIP) — option A locked

| Source of truth | Role |
|-----------------|------|
| **Ladder index** `_ladder[(scale, window_start)]` | **Sole** tip identity for meal, cascade, GraphView “current” |
| `list_summaries(scale, …, tips_only=True)` default | Resolves via ladder index only |
| Atom meta | **Must not** be required for tip filtering. Optional diagnostic: stamp `meta.was_tip_at_write=True` on every version when written (historical “I was the tip at put time”) — **not** a live is_tip flag |

**Do not** implement option B (patch previous tip `is_tip=False`) in #92.

**Tests assert tip identity via index** (and `list_summaries` default), not by scanning `meta.is_tip`.

#### Version listing contract (admin / Glass later)

| API | Behaviour in #92 |
|-----|------------------|
| `list_summaries(scale, overlapping=…, tips_only=True)` **default** | Ladder index only — O(tips). **Index never holds non-tips.** |
| `list_summaries(..., tips_only=False)` / `include_versions=True` | **No secondary version index in #92.** v1 algorithm: `list_atoms(kinds=["summary"], limit=…)` (or store scan), filter `scale` + `window_start` match, sort by `meta.version` ascending. Accept O(n) for admin/Glass archaeology. |
| Efficient version index / GC | **Non-goal** for #92 |

#### Interaction with skip-unchanged

Today `refresh_window` skips put when `content_text` equal. For versioned coarser:

- Compare against **tip** body (index lookup); if equal, skip new version (no cascade noise).
- Child set hash: `meta.child_content_hash = sha256(join(child_ids + child body digests))` — if hash equal and tip exists, skip LLM and skip version.

### 4. Schedule

#### Problem with 50 ms nibble alone

`refresh_due` round-robins scales under 50 ms: good for template CPU, bad for LLM (one completion ≫ 50 ms) and bad for “hour just closed → cascade now.”

#### Normative schedule (dual path)

| Path | When | Work | Budget |
|------|------|------|--------|
| **Hourly process** | Idle tick when `now` crossed an hour boundary since `state.last_hourly_process`, **or** catch-up queue non-empty | Close due 1h → cascade parents for affected windows | `ladder_hourly_max_ms` (default **8000–15000**); max N LLM calls / tick |
| **Nibble / repair** | Other idle ticks | Template fill gaps, tip repair, legacy windows, state hygiene | `ladder_max_ms_per_tick` (keep **50–200** for template) |
| **Moment finalize** | After `_finalize_moment` | **Optional** light touch: mark current 1h dirty; do **not** run full LLM on hop boundary | Cheap |

**Drop or repurpose** `_finalize_memory_ladder_15m` → `_finalize_memory_ladder_mark_dirty` (set `state.dirty_1h_windows` for current hour).

#### Due 1h selection

```text
closed_hours = windows_in_horizon("1h", now, n_windows=catchup_n)
  where window_end <= now
for each hour oldest-first (catch-up):
  if tip missing OR sources_hash != tip.meta.sources_hash OR not finalized:
    generate 1h
    cascade parents containing that hour
stop when ms budget or max_llm_calls_per_tick exhausted
```

Catch-up after downtime: process **oldest missing closed hours first** up to `ladder_catchup_max_hours` (default **24**) **closed hours per hourly tick**. Remaining lag continues across later ticks via `state.catchup_cursor` (no separate process-life burst limit). Prefer completing cascade for each hour before starting the next hour when budget allows (keeps day tip coherent).

#### LLM call limits (respect usage)

- Always go through the same gated `ChatClient` as the do-loop when available (`UsageHardStopError` → template fallback, never kill presence).
- Separate soft pacing: `ladder_llm_max_calls_per_tick` (default 3), `ladder_llm_max_calls_per_hour` (default 40).
- Record usage into the meter (same token accounting) so ladder competes fairly with work.
- `ladder_enabled=false` → no work; `summary_mode=template` → no LLM.

#### State file (`data/memory/ladder/state.json`)

Extend:

```json
{
  "round_robin_idx": 0,
  "last_refresh": {"1h": "…", "1d": "…"},
  "last_hourly_process": "…Z",
  "last_closed_1h_processed": "…Z",
  "dirty_1h_windows": ["…Z"],
  "catchup_cursor": "…Z",
  "llm_calls_hour": {"hour": "…", "count": 0},
  "schema_version": 2
}
```

### 5. Cascade recompute

When 1h tip for hour H is written (new or content-changed):

```text
s = "1h"
while (scale := parent_scale(s)) is not None:  # write map: 1d→1w→1m→1y
  if not scale_allowed_for_instance_age(scale): break  # coarser also gated
  w_start, w_end = window_bounds(scale, H.start)
  children = tip child summaries fully inside [w_start, w_end)
  if empty and no raw fallback needed: s = scale; continue
  if child_content_hash == tip.meta.child_content_hash: s = scale; continue
  generate scale narrative from children (+ note missing slots)
  put new version; move tip; write meta fabric
  s = scale
```

**No hard skip-parent-recompute** based on “continuous work” heuristics — only hash equality skip. **Never** walk through `6h`/`15m` on this path.

### 6. Hyperedges / edge fabric (write meta + GraphView project)

There is **no** `edge_factory` module. Write-time fabric = meta fields on `put_atom`; runtime expand = GraphView projection (same pattern as sequential / parcel `parent_of`).

#### Frozen kind strings (PR-C; coordinate #98)

| Constant | String token | Direction | When | Cap | ≠ parcel |
|----------|--------------|-----------|------|-----|----------|
| `EDGE_SUMMARY_CHILD` | `"summary_child"` | parent summary → child summary atom | Coarser from children | All children in window (≤24 day-of-hours; ≤~31 month-of-days) | **Not** `child_of` / `parent_of` (those use `parent_atom_id` for parcels) |
| `EDGE_SUMMARY_SOURCE` | `"summary_source"` | 1h summary → source experience atom | 1h from raw | Top-K highlight rank, default **K=24** | **#98 must use this same token** for “source edges at create” on 1h→raw; do not invent `"source"` / `"derived_from"` parallel |
| `EDGE_SUPERSEDES` | `"supersedes"` | new tip → previous version | Coarser versioning | 1 | N/A |

Meta fields: `child_atom_ids`, `source_atom_ids`, `supersedes_atom_id` / `previous_version_id`.

#### GraphView projection entry points (PR-C)

| Method / site | Behaviour |
|---------------|-----------|
| `weights.EDGE_*` constants | Add three kinds to `EDGE_KINDS` + base weights |
| `GraphView._project_summary_child` | From `kind=summary` + `meta.child_atom_ids` |
| `GraphView._project_summary_source` | From 1h summary + `meta.source_atom_ids` (cap lite K) |
| `GraphView._project_supersedes` | From `meta.supersedes_atom_id` when present |
| `GraphView.neighbors` | Include summary projections when seed is a summary atom |

Base weights (v1 static): child 0.88, source 0.75, supersedes 0.95. Temporal decay applies.

#### Runtime depth flags

| Mode | Setting | Behaviour |
|------|---------|-----------|
| **Lite (default)** | `traverse_summary_expand = "lite"` | Expand `summary_child` one hop; `summary_source` only from 1h tip with K≤8; **do not walk `supersedes`** (test-asserted) |
| **Deep** | `"deep"` or Phase 3 / #103 flags | Multi-hop child ladder, larger K, optional supersedes chain for archaeology UI |

Recording fabric is always on at write (cheap). Walking depth is the control. **Same storage for lite/deep.** Optional durable edge table later reuses **these exact tokens** — not a second vocabulary.

### 7. Meal / episodic select policy

Update `select_episodic` (`meal.py`):

#### Summary pack order (write-era)

```python
_SUMMARY_PACK_ORDER = ("1y", "1m", "1w", "1d", "1h")  # coarse first
# omit 15m/6h unless no 1h tip exists and legacy atoms present (soft fallback)
```

#### What to load (tip-only)

| Scale | Windows into meal |
|-------|-------------------|
| `1y` / `1m` / `1w` / `1d` | **Current tip only** (open window containing `now`) — not previous window by default |
| `1h` | **Recent band:** last `episodic_recent_1h_count` closed hours (default **6**) + current open hour tip if any |

Rationale: coarser tips already absorb history; packing previous day/week duplicates narrative mass and worsens B11-class “era narrative drowns tip” pressure (#93 lessons). Recent 1h preserves near-term texture under the coarser day story.

#### Shrink / drop order (rewrite of `_SUMMARY_DROP_ORDER` behaviour)

Today code drops by fine→coarse scale (`PERIOD_SCALE_ORDER`: 15m first). New pack shape needs **recency within the 1h band**, not “drop entire 1h scale before 1d in one step.”

**Normative under-pressure steps for episodic summaries (PR-D):**

1. **Drop oldest closed 1h** in the recent band (by `window_start` ascending) one at a time until under cap.
2. Continue until only **one** closed 1h remains (or zero if still over).
3. Drop **current open-hour** 1h tip if present and still over.
4. Drop coarser tips **fine→coarse among remaining packed scales** (`1d` then `1w` then `1m` then `1y`), but **protect the single most recent `1d` tip until last resort** (same spirit as today’s last-resort 1h/1d protection — now 1d only as last among coarses if needed).
5. Never pull version archives or legacy 15m/6h under pressure once write-era tips exist.

Replace / retire `_SUMMARY_DROP_ORDER = PERIOD_SCALE_ORDER` for summary items; keep raw-prior-moment shrink steps (3a/3b) as today.

#### Token share

Keep `EPISODIC_SUMMARY_SHARE` (~0.7 of episodic cap). LLM bodies are larger than templates → expect fewer summary items under same token cap; **recent 1h count may need default 4–6** after dogfood measure.

### 8. Presence / worker integration

| Hook | Today | Target |
|------|-------|--------|
| `_idle_memory_ladder` | `refresh_due(max_ms=50)` | `ladder.tick(store, settings, llm=…)` choosing hourly vs nibble |
| `_finalize_memory_ladder_15m` | `refresh_window(15m)` | Mark dirty 1h / optional template-only open hour refresh |
| Client injection | None | Lazy adapter when `summary_mode=llm` |
| Placement | Outside lock, idle only | **Unchanged invariant** — never hop path |

#### `SummaryLlm` protocol + ChatClient adapter (normative PR-A)

```python
class SummaryLlm(Protocol):
    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
    ) -> str:
        """Return assistant content text; raise SummaryLlmError on hard failure."""
        ...
```

**Worker-owned adapter** (`presence` or thin `elyra/memory/ladder_llm.py` helper — **not** imported by presence→ladder cycle from ladder into presence):

| Responsibility | Normative behaviour |
|----------------|---------------------|
| Map to API | Call `ChatClient.chat_completion(messages, max_tokens=…, reasoning=False, temperature=…)` |
| Reasoning | **`reasoning=False`** by default for ladder (cost/latency); do not use do-loop reasoning budget |
| Extract text | Use `ChatCompletionResult.content`; empty/whitespace → treat as failure → template fallback |
| Usage hard-stop | Catch `UsageHardStopError` → template fallback; **never** kill presence |
| Meter record | Use the **same gated client / meter path** as the do-loop so tokens count toward weekly/day ceilings |
| Other errors | Timeout / HTTP / parse → log WARNING + template fallback (`meta.source=llm_fallback_template`) |
| Import rule | `ladder` depends only on `SummaryLlm` protocol; **must not** import `presence` or `elyra.llm.client` if that creates cycles — adapter lives at worker boundary |

Tests inject a stub `SummaryLlm`. Ladder unit tests never need a live ChatClient.

### 9. Instance-age scale growth

```python
# Provisional dogfood knobs (not locked product law) — PR-E tests pin these.
LADDER_ENOUGH_1D_TIPS = 3   # unlock 1w early if age < 7d
LADDER_ENOUGH_1W_TIPS = 2   # unlock 1m early
LADDER_ENOUGH_1M_TIPS = 2   # unlock 1y early

def allowed_scales(instance_created_at, now, *, tip_counts: dict[str, int]) -> list[str]:
    age = now - instance_created_at
    out = ["1h", "1d"]
    if age >= timedelta(days=7) or tip_counts.get("1d", 0) >= LADDER_ENOUGH_1D_TIPS:
        out.append("1w")
    if age >= timedelta(days=28) or tip_counts.get("1w", 0) >= LADDER_ENOUGH_1W_TIPS:
        out.append("1m")
    if age >= timedelta(days=365) or tip_counts.get("1m", 0) >= LADDER_ENOUGH_1M_TIPS:
        out.append("1y")
    return out
```

`instance_created_at` from `memory/meta.json` `created_at` (already written by jsonl/lance stores). Age thresholds 7d/28d/365d are soft product defaults; “enough tips” constants are **provisional** and may move after dogfood without a design re-open.

### 10. Observability

| Signal | Where |
|--------|--------|
| `ladder.last_hourly_process` | state + `/api/status` memory block |
| `ladder.llm_calls_*`, fallback counts | counters in state / health |
| WARNING on LLM fail + fallback | `_LOG.warning` |
| Glass Atoms | already shows `kind=summary`; show `meta.source`, `meta.version` when inspect lands (tip via index, not meta.is_tip) |
| Meal labels | `episodic/summary 1h` unchanged; optional `v{n}` in meta only |

### 11. Complexity & risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| LLM cost / quota starvation of do-loop | High | Shared meter; hard-stop → template; per-hour ladder call caps; default catch-up bound |
| Latency on idle (blocks wake claim) | High | Hard wall-clock `ladder_hourly_max_ms`; stop between hours; never hold `_lock` |
| Hallucinated “work” in empty periods | Med | Prompt + gap list in source pack; template fallback for empty sources (`skip_empty`) |
| Version archive bloat (JSONL) | Med | Coarser only; skip-unchanged hash; 1h not versioned by default; compaction keeps latest-by-id but **versions are distinct ids** so they accumulate — accept archaeology cost; optional GC later (non-goal) |
| Meal token blow-up from long narratives | Med | Soft token targets; pass B; tip-only pack; reduce recent 1h count |
| Ladder index tip vs versions bugs | Med | Single tip map; tests for supersedes chain |
| Child preference hole early life | Low | Raw fallback for 1h; coarser wait until children exist |
| Test non-determinism | Med | Default template in unit tests; stub LLM returns fixed text |
| Coupling to ChatClient | Med | Narrow `SummaryLlm` protocol; optional null |
| Dual scale vocabulary confusion | Med | Explicit WRITE vs LEGACY allowlists; parent_scale write map; tests |
| Mega PR-A | Med | Optional A0/A1 split; merge gate = template + stub LLM green without live API |

**Honest complexity:** this is a **multi-PR subsystem** (~medium-large). LLM path alone is medium; version archaeology + meal policy + edges push total effort to **~1–2 weeks** focused implementer time including dogfood, not a single afternoon PR.

---

## API / Interface Changes

### Settings (`MemorySettings`)

| Knob | Default | Notes |
|------|---------|-------|
| `summary_mode` | `"template"` in code/CI; dogfood toml `"llm"` | `template \| llm` |
| `ladder_max_ms_per_tick` | 50 → **200** (nibble) | Template / repair |
| `ladder_hourly_max_ms` | **12000** | Hourly + cascade wall |
| `ladder_catchup_max_hours` | **24** | Max **closed hours processed per hourly tick**; remainder via `catchup_cursor` |
| `ladder_llm_max_calls_per_tick` | **3** | |
| `ladder_llm_max_calls_per_hour` | **40** | |
| `ladder_recent_1h_meal` | **6** | Meal band |
| `ladder_source_edge_k` | **24** | Write cap |
| `ladder_write_legacy_scales` | **false** | |
| `traverse_summary_expand` | `"lite"` | `lite \| deep` |

### Public ladder API

```python
def tick(store, now=None, *, settings, llm=None, identity_names=None, max_ms=None) -> dict
def process_closed_hours(store, now, *, ...) -> dict
def cascade_from_hour(store, hour_start, *, ...) -> dict
def build_summary_atom(...)  # extended: mode, version, llm
def build_source_pack(...)   # structured pack for LLM (gaps + moments)
def moment_blocks_for_window(...)
def gap_spans(...)
def render_template_summary(...)  # keep
def collect_window_sources(...)  # child map change — write children
def refresh_due(...)  # nibble over PERIOD_SCALE_ORDER_WRITE only (default)
```

### Store

- `list_summaries(..., tips_only: bool = True)` — default tips via ladder index.
- `tips_only=False` / versions: O(n) `list_atoms` filter by scale+window (no secondary index in #92).
- Ladder index **never** holds non-tips; no API delete of versions.

### Meal

- `select_episodic` uses new pack order + tip/recent-1h policy + recency-aware 1h drop order.

### Graph

- Project `summary_child` / `summary_source` / `supersedes` from summary meta; lite skips supersedes walk.

---

## Data Model Changes

### Atom

No new top-level fields required for v1 — **meta extension** as above. Optional later: `parent_atom_id` for supersedes if desired for parent_of symmetry; prefer meta + explicit `supersedes` edge kind to avoid overloading parcel parent.

### Scale vocabulary

- Add `"1y"` to `PERIOD_SCALES_ALL` / read order.
- `PERIOD_SCALES_WRITE` / `PERIOD_SCALE_ORDER_WRITE` drive write + cascade + nibble.
- `parent_scale` for write scales uses write parent map (`1h→1d`), not legacy order.

### Ids

- Tip index key: `(scale, window_start)`.
- Versioned atom ids via `versioned_summary_id`.
- `stable_summary_id` remains valid for 1h tip identity / tests.

### On-disk

```text
data/memory/
  atoms.jsonl          # grows with version atoms
  ladder/state.json    # schema_version 2 fields
  meta.json            # created_at for instance age
```

No mandatory migration job. Old `15m`/`6h` rows linger harmlessly.

---

## Alternatives Considered

| Alt | Pros | Cons | Decision |
|-----|------|------|----------|
| A1. LLM on compose only | Fresh meal | Blocks rebuild_outer; cost on every social wake | **Reject** |
| A2. Keep 15m/6h + LLM | Less type churn | Noise; product dropped; meal dilution | **Reject for writes** |
| A3. Overwrite coarser only (no versions) | Simple | Loses day-story evolution | **Reject** (rich archaeology locked) |
| A4. Version 1h every refresh | Max archaeology | Explodes store | **Reject**; 1h tip replace |
| A5. Separate edge DB now | Clean query | Second system; #98/#103 overlap | **Defer**; meta + GraphView project first |
| A6. Hard char counters for scale budgets | Predictable tokens | Overconstrains prose | **Reject**; soft targets + pass B |
| A7. Delete fine under coarse | Space | Contradicts forever-1h | **Reject** |
| A8. Only 50 ms idle forever | Simple | LLM impossible | **Reject** as sole schedule |

---

## Security & Privacy

- Source pack may include user speech and tool previews — same trust boundary as meal; do not log full packs at INFO.
- LLM provider sees summary source packs (local or xAI) — already true for do-loop; no new egress class.
- Redaction: reuse promote redaction; do not expand secrets into ladder packs.
- Version archive increases retention surface (intentional archaeology).

---

## Observability

(See §10.) Minimum dogfood dashboard: status shows last hourly process, last 1h closed, llm vs template counts, catch-up cursor lag hours.

---

## Rollout Plan

### Branch stack (KD-BR — mandatory)

```text
main
  └── feature/92                          # integration tip (design + merged product)
        ├── fix/BUG-meal-02-92-s1-ladder-llm-schedule     # PR-A
        ├── fix/BUG-meal-02-92-s2-version-tips            # PR-B
        ├── fix/BUG-meal-02-92-s3-summary-edges           # PR-C
        ├── fix/BUG-meal-02-92-s4-meal-tip-pack           # PR-D
        └── fix/BUG-meal-02-92-s5-tests-dogfood           # PR-E
```

Rules (mirror #93 product stack style):

1. **Every product branch is created FROM `feature/92`** (or from the previous product tip after that tip is merged into `feature/92` when stacking unmerged).
2. **Never** cut product work from bare `main`.
3. **`/execute-plan` must use `feature/92` as stack base**, not `main` (see PR Plan → Execute-plan overrides).
4. Merge child → `feature/92`; dogfood on integration tip; merge `feature/92` → `main` only after dogfood OK.
5. Land design file + BUG-meal-02 Design home pointer on `feature/92` first (design commit or PR-A).

### Steps

1. Land design on `feature/92` at `docs/stretch-2/design-episodic-summary-ladder-llm.md`; patch `docs/state/known-bugs.md` BUG-meal-02 **Design home** → that path (status remains In Progress until DoD).
2. Cut `fix/BUG-meal-02-92-s1-…` **from `feature/92`**; implement PR-A → merge into `feature/92`.
3. Repeat for s2–s5 per PR Plan **Branch** fields.
4. Operator: set `summary_mode=llm` in `elyra.toml` after PR-A on the dogfood host; measure cost 24h.
5. No forced rewrite of historical template atoms; optional backfill script later (non-blocking).
6. Close #92 / mark BUG-meal-02 Fixed when PR-E + dogfood sign-off complete on `feature/92` (then main).

---

## Open Questions

| ID | Question | Lean |
|----|----------|------|
| OQ1 | Mid-hour LLM refresh vs close-only? | **Close-only** for LLM; template dirty optional |
| OQ2 | Exact instance-age thresholds for 1w/1m/1y | Soft: 7d / 28d / 365d; enough-tips provisional (3/2/2) — see §9 |
| OQ3 | Should Glass show version history UI in #92? | **No** — store only; O(n) list later; UI later |
| OQ4 | Durable edge table vs meta-only? | Meta+project for #92; table if #98 needs it (same tokens) |
| OQ5 | Pass B always vs conditional? | Conditional under budget for 1h; always 1d+ |
| OQ6 | Raise `atom_max_chars` for yearly? | Keep 8000; pass B enforces |

---

## Definition of Done for #92

1. New writes use scales **1h→1d→1w→1m→1y** (no new 15m/6h); `parent_scale("1h")=="1d"`.
2. With `summary_mode=llm` and client available, closed 1h bodies are **LLM narratives** (or honest `llm_fallback_template` with meta).
3. Template mode + unit tests remain hermetic and green (merge gate does not require live LLM).
4. Hourly schedule + cascade updates coarser **tips** without hop-path LLM.
5. Coarser cascade creates **new version atoms** + tip pointer (index truth); old versions retained immutable.
6. Meal packs **tips + recent 1h only**; under pressure drops **oldest 1h first**; dogfood Context readable.
7. Edges recorded (meta + lite GraphView project) for child/source/supersedes; lite does not walk supersedes.
8. Usage hard-stop does not crash presence; ladder falls back.
9. Catch-up after multi-hour downtime eventually fills missing 1h + parents (≤24 hours/tick, cursor continues).
10. Docs: this design in-repo; BUG-meal-02 Design home points here; status → Fixed when merged.
11. Tests: scale/parent write map, version tip via index, cascade hash skip, meal drop order, source-pack gaps, LLM stub two-pass + adapter hard-stop, usage fallback.
12. All product branches were cut **from `feature/92`** (KD-BR).

---

## Mapping to Existing Symbols (implementer cheat sheet)

| Symbol | Role in #92 |
|--------|-------------|
| `ladder.render_template_summary` | Fallback + tests |
| `ladder.collect_window_sources` | Child map retarget; tip children only |
| `ladder.build_source_pack` | Structured LLM pack (moments + gaps) |
| `ladder.moment_blocks_for_window` | Group raw atoms by moment for pack |
| `ladder.gap_spans` | Empty ranges for KD6 honesty |
| `ladder.build_summary_atom` | Mode, version meta, LLM body |
| `ladder.refresh_window` | Specialize: 1h close path |
| `ladder.refresh_due` | Nibble over **write** scales only |
| `ladder.load/save_ladder_state` | Hourly cursor + dirty set + catchup_cursor |
| `types.stable_summary_id` | 1h tip / legacy |
| `types.versioned_summary_id` | Coarser version atoms |
| `types.window_bounds` | Add 1y; keep legacy grids |
| `types.PERIOD_SCALE_ORDER_WRITE` / `PERIOD_SCALES_WRITE` | Write + cascade + nibble |
| `types.PERIOD_SCALES_ALL` | Read validation / window_bounds |
| `temporal.child_scale` / `parent_scale` | Write maps; **1h→1d parent** |
| `meal.select_episodic` | Tip + recent 1h; recency drop within 1h band |
| `meal._load_window_summary` | Load tip via list_summaries (index) |
| `meal._SUMMARY_DROP_ORDER` | Replace with recency-aware steps (§7) |
| `PresenceWorker._idle_memory_ladder` | Call `tick` + inject SummaryLlm adapter |
| `PresenceWorker._finalize_memory_ladder_15m` | Dirty mark / retire 15m |
| `MemorySettings.ladder_*` | New knobs |
| `weights.EDGE_SUMMARY_*` / `graph.GraphView._project_summary_*` | Summary edge project |
| `ChatClient.chat_completion` | Via worker adapter only |
| `llm.usage.UsageHardStopError` | Adapter → template fallback |
| `IdentityStore.display_name` / user digest | Soft names in pack |

---

## References

- Issue [#92](https://github.com/jtwolfe/project-elyra/issues/92) / `docs/state/known-bugs.md` BUG-meal-02
- `docs/stretch-2/design-phase-1-implementation.md` § Period summary ladder
- `docs/stretch-2/design-phase-1-temporal.md`
- `docs/state/memory/architecture/phase-1-temporal.md`
- `docs/stretch-2/design-context-meal-composition.md`
- `docs/state/memory/architecture/phase-2a-directed-traversal.md` (edge projection pattern)
- Code: `elyra/memory/ladder.py`, `temporal.py`, `types.py`, `config.py`, `meal.py`, `weights.py`, `graph.py`, `elyra/presence/worker.py`

---

## Key Decisions

| ID | Decision | Rationale | Locked? |
|----|----------|-----------|---------|
| **KD-BR** | All product implementation branches cut **FROM `feature/92`** (or previous tip after merge into `feature/92`); never bare `main`; child → `feature/92` → dogfood → main | Same process as #93 stack; integration tip holds design + merged product | **Yes** |
| **KD1** | Write scales: **1h → 1d → 1w → 1m → 1y**; drop **15m/6h** for new writes | Product; simpler ladder; moment grain under 1h | **Yes** |
| **KD2** | Legacy 15m/6h: **read optional**, no hard migration | Avoid rewrite risk | **Yes** |
| **KD3** | Base grain under 1h: **~one narrative point per moment** (not 2-min grid) | Multi-user 2min is future hint only | **Yes** |
| **KD4** | Generation: **budgeted LLM**, **two-pass** when useful; **template fallback** | Quality + safety | **Yes** |
| **KD5** | `summary_mode = template \| llm` | Operator / test control | **Yes** |
| **KD6** | Idle/gaps: **LLM-formatted note**, not omit, not fake work | Honesty | **Yes** |
| **KD7** | Soft narrative size targets per scale (not hard char counters) | Soft-constraints philosophy | **Yes** |
| **KD8** | Pointers in prose + structured meta (`a_`/`g_`/`t_`) | Meal + future graph | **Yes** |
| **KD9** | Soft self/other naming via identity digests | Existing stores | **Yes** |
| **KD10** | Continuous-work merge: **prompt-only**; no hard parent-skip engine | Soft constraints | **Yes** |
| **KD11** | **Hourly schedule** + cascade; not 50ms nibble alone | LLM needs wall time; clear semantics | **Yes** |
| **KD12** | LLM cost accepted; **respect usage hard-stop / pacing** | Shared meter | **Yes** |
| **KD13** | Catch-up after downtime: max **24 closed hours per hourly tick** via `ladder_catchup_max_hours`; continue with `catchup_cursor` | Continuity without one-tick thrash | **Yes** |
| **KD14** | **Never delete** closed 1h for archaeology | RICH retention | **Yes** |
| **KD15** | Cascade recompute coarser **heads** when new 1h lands | Mental model: heads shift | **Yes** |
| **KD16** | Coarser: **new version** per cascade + supersedes/previous; tip pointer | Version archaeology | **Yes** |
| **KD-TIP** | Tip-ness is **ladder index only** (immutable version rows; no live `meta.is_tip`); tests assert index | Avoid lying meta under immutability | **Yes** |
| **KD17** | Meal: **current tip** per coarser + **recent 1h band** only; drop **oldest 1h first** under pressure | Budget + #93 lesson | **Yes** |
| **KD18** | Edges: **write comprehensively** (meta); runtime **lite** default; deep later same storage | Cost asymmetry | **Yes** |
| **KD19** | Frozen edge tokens: `summary_child`, `summary_source`, `supersedes`; #98 uses **`summary_source`** | One vocabulary; ≠ parcel parent_of | **Yes** |
| **KD20** | LLM **never** on hop / rebuild_outer | Presence invariant | **Yes** |
| **KD21** | 1h: tip replace (stable); coarser: versioned ids | Balance archaeology vs bloat | **Yes** |
| **KD22** | Child of 1d is **1h** (not 6h); of 1h is **raw**; **`parent_scale("1h")=="1d"`** via write parent map | Scale set; no accidental 6h cascade | **Yes** |
| **KD23** | Prefer meta+GraphView project before durable edge table | Ship #92 without second store | **Yes** |
| **KD24** | Default code `summary_mode=template`; dogfood toml `llm` | CI hermetic | **Yes** |
| **KD25** | Write-order cascade / nibble / `parent_scale` for write scales; `PERIOD_SCALES_ALL` for read/window only | Issue 2 | **Yes** |
| **KD26** | Version listing: tips via index; full versions = O(n) `list_atoms` filter — no secondary index in #92 | Ship tips first | **Yes** |
| **KD27** | Ladder ChatClient adapter: `reasoning=False`; hard-stop → template; meter via gated client | Issue 7 | **Yes** |

---

## PR Plan

**Integration tip / stack base:** `feature/92` (not `main`).  
**Process:** each PR is a **child product branch** cut **FROM `feature/92`** (KD-BR). Merge child → `feature/92`. Do **not** merge product branches to `main` until `feature/92` dogfood is OK.

### Execute-plan overrides (KD-BR — mandatory)

The default `/execute-plan` skill bases level-0 branches on `origin/main` and stacks PRs toward `main`. **For this plan, override that default:**

| Step | Default skill behavior | **This plan** |
|------|------------------------|---------------|
| Level-0 branch create | `git branch <pr> origin/main` | `git branch <pr> origin/feature/92` (or local `feature/92` tip after `git fetch`) |
| Dependent branch create | from dep `commit_sha` | unchanged (still from dep tip) |
| Stack assembly bottom parent | `main` | **`feature/92`** — bottom of linearized stack is parented on `feature/92`; each PR above parents on the previous stack branch |
| `gh pr create --base` | first PR → `main` | first PR → **`feature/92`**; rest → previous stack branch |
| Merge target after dogfood | n/a | children → `feature/92` first; only then `feature/92` → `main` |

Never cut any product branch for PR-A…E from bare `main`. Design commit already lives on `feature/92`.

Order is dependency-respecting. Optional intra-PR-A split (not mandatory): **A0** types/temporal/write allowlist + template-only hourly schedule; **A1** LLM path + worker adapter — or keep one PR-A with **merge gate = template path green without live LLM**.

### PR-A — Bodies, scale set, schedule (core #92)

| Field | Value |
|-------|--------|
| **Title** | `feat(memory): LLM period summaries + 1h→1y ladder schedule` |
| **Branch** | `fix/BUG-meal-02-92-s1-ladder-llm-schedule` **from** `feature/92` |
| **Depends on** | none (first); design file may already be on `feature/92` |
| **Files (expected)** | `elyra/memory/types.py`, `temporal.py`, `ladder.py` (+ optional `ladder_llm.py` adapter helper), `config.py`, `elyra/settings.py`, `elyra/presence/worker.py`, `tests/test_memory_ladder.py`, `tests/test_memory_types.py`, `tests/test_memory_temporal.py`, settings validation tests, `docs/stretch-2/design-episodic-summary-ladder-llm.md`, `docs/state/known-bugs.md` (Design home pointer) |
| **Description** | Add `1y` grid; `PERIOD_SCALES_WRITE` / write parent map (`1h→1d`); drop 15m/6h writes; `build_source_pack` / `gap_spans` / `moment_blocks_for_window`; `summary_mode`, two-pass LLM via `SummaryLlm` + ChatClient adapter (`reasoning=False`, hard-stop fallback); hourly `tick` / catch-up (24h/tick); retire finalize-15m to dirty-mark; wire worker idle path; meta honesty (no live is_tip). Keep `refresh_due` as nibble over write scales. Optional A0/A1 split; mergeable when template path + stub LLM tests green. |
| **Tests** | Window bounds 1y; `parent_scale("1h")=="1d"`; gap in source pack; template path; stub LLM one/two-pass; adapter hard-stop; hourly due; budget stops mid-cascade; no hop-path call. |
| **Dogfood** | `summary_mode=llm` after hour boundary → readable 1h + 1d tip bodies. |

### PR-B — Version archaeology (coarser heads)

| Field | Value |
|-------|--------|
| **Title** | `feat(memory): versioned coarser summary tips + supersedes meta` |
| **Branch** | `fix/BUG-meal-02-92-s2-version-tips` **from** `feature/92` (or s1 tip if stacking unmerged) |
| **Depends on** | PR-A merged into `feature/92` (preferred) |
| **Files** | `ladder.py`, `types.py` (`versioned_summary_id`), `jsonl_store.py`, `lance_store.py` (`list_summaries` tips_only), `tests/test_memory_ladder.py`, store tests |
| **Description** | Immutable old + tip pointer (KD-TIP); new version atom per cascade; supersedes meta; `child_content_hash` skip; 1h tip-replace; document O(n) version listing non-goal for efficiency. |
| **Tests** | Two cascades → two atom ids, one tip **via index**; skip when hash equal; list_summaries default tips; tips_only=False scan filter. |
| **Dogfood** | Force two 1h lands in same day; two 1d version atoms; meal shows latest tip only. |

### PR-C — Summary edge fabric lite + GraphView project

| Field | Value |
|-------|--------|
| **Title** | `feat(memory): summary edge fabric (child/source/supersedes) lite expand` |
| **Branch** | `fix/BUG-meal-02-92-s3-summary-edges` **from** `feature/92` (or s2 tip if supersedes required unmerged) |
| **Depends on** | PR-A (PR-B preferred for supersedes edges) |
| **Files** | `weights.py`, `graph.py` (`_project_summary_*`), `ladder.py` (write meta lists), `config.py` (`ladder_source_edge_k`, `traverse_summary_expand`), traverse/graph tests |
| **Description** | Freeze tokens `summary_child` / `summary_source` / `supersedes`; write capped meta; GraphView project; lite default (no supersedes walk); deep flag stub for #103. **#98 uses `summary_source`.** No second edge DB. |
| **Tests** | Neighbors from 1h tip include sources; 1d tip includes child 1h; supersedes projectable; lite does **not** walk supersedes by default. |
| **Note** | Logical edge factory = ladder meta write, not a module. |

### PR-D — Meal pack policy

| Field | Value |
|-------|--------|
| **Title** | `feat(memory): episodic meal tip-only + recent 1h band` |
| **Branch** | `fix/BUG-meal-02-92-s4-meal-tip-pack` **from** `feature/92` (or s2 tip for tip semantics) |
| **Depends on** | PR-A (PR-B for tip semantics preferred) |
| **Files** | `meal.py`, `config.py` (`ladder_recent_1h_meal`), `tests/test_memory_meal.py` (or existing meal tests) |
| **Description** | Update `_SUMMARY_PACK_ORDER`; tips only for ≥1d; recent N×1h; recency-aware drop (oldest 1h first, then coarser with 1d last-resort); legacy 15m/6h soft fallback only when no 1h tips. |
| **Tests** | Pack order; no version archive leak; pressure drops oldest 1h before 1d tip; then coarser order. |
| **Dogfood** | Context shows day narrative + last few hours, not template spam. |

### PR-E — Tests hardening, status, dogfood polish

| Field | Value |
|-------|--------|
| **Title** | `test(memory): ladder LLM/cascade suite + status observability` |
| **Branch** | `fix/BUG-meal-02-92-s5-tests-dogfood` **from** `feature/92` (after s1–s4 merged preferred) |
| **Depends on** | PR-A–D |
| **Files** | tests, `worker.py` status block, `docs/state/known-bugs.md` (mark Fixed when closing), glass inspect optional light meta |
| **Description** | Integration: catch-up 3 missing hours; meter exhaustion; instance-age + provisional enough-tips; status knobs. Close #92 when dogfood sign-off. |
| **Definition** | Matches Definition of Done checklist. |

### Suggested merge order

```text
feature/92
  s1 (PR-A) ──► merge into feature/92
       │
       ├─► s2 (PR-B) ──► feature/92
       │         │
       │         ├─► s4 (PR-D) ──► feature/92
       │         └─► s3 (PR-C, parallel s4) ──► feature/92
       └─► s5 (PR-E) after A–D ──► feature/92 ──dogfood──► main
```

### Out of stack (coordinate only)

| Issue | Touchpoint |
|-------|------------|
| #98 source edges | **Must** reuse frozen token `summary_source` |
| #103/#105 depth/cache | `traverse_summary_expand=deep` |
| #104 directed keep | No change |
| #93 glass-tail | Already on main; meal pressure lessons inform PR-D |

---

## Appendix A — Prompt sketch (non-normative)

```text
System: You write factual episodic memory for an agent instance.
Use only the source pack. Name self as {self_name}, users as given.
Note idle gaps explicitly. Soft-merge continuous tool work. Prefer under-claim.
Include light pointers to important atom/goal/task ids when present.

User: Summarize this {scale} window for durable memory.
Target shape: {target_shape}
SOURCE PACK:
...
```

Pass B: “Reduce to ≤{n} tokens; keep gaps, pointers, and causal spine.”

## Appendix B — Complexity budget (rough)

| Workstream | Eng-days (1 senior) |
|------------|---------------------|
| PR-A (or A0+A1) | 3–5 (A0 ~1–2 template/schedule; A1 ~2–3 LLM) |
| PR-B | 1–2 |
| PR-C | 1–2 |
| PR-D | 1 |
| PR-E + dogfood | 1–2 |
| **Total** | **~7–12** |

---

*End of design. Revision: review issues 1–13 addressed (KD-BR, write parent map, KD-TIP, version listing, catch-up 24/tick, meal drop order, SummaryLlm adapter, PR-A split note, frozen edge tokens, source-pack helpers, provisional enough-tips, sequence diagram, known-bugs Design home).*
