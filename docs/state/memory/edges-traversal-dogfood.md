# Durable edges + traversal extension — operator dogfood checklist (#98 / #120 / #103 / #105)

| Field | Value |
|-------|--------|
| **Class** | STATE |
| **Audience** | Operators |
| **Status** | Active (living checklist) |
| **Normative?** | No — prefer code on `working`; boxes are ops evidence, not product default-on |
| **Last verified** | 2026-08-05 (code on `working`/`main` @ `161a820`; live dogfood **partial**) |
| **Design (edges stack)** | [design-memory-edges-and-traversal.md](../../design/memory/design-memory-edges-and-traversal.md) (**Shipped (code; dogfood partial)**) |
| **Design (polish1)** | [design-memory-edges-polish1.md](../../design/memory/design-memory-edges-polish1.md) (**Shipped (code; dogfood partial)** — PR0–PR6) |
| **Architecture priors** | [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md), [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) |
| **Program** | [README.md](README.md) — edges/traversal + polish1 close-out |
| **Related issues** | [#98](https://github.com/jtwolfe/project-elyra/issues/98), [#120](https://github.com/jtwolfe/project-elyra/issues/120), [#103](https://github.com/jtwolfe/project-elyra/issues/103), [#105](https://github.com/jtwolfe/project-elyra/issues/105); polish2 residuals **[#125](https://github.com/jtwolfe/project-elyra/issues/125)** |
| **Claim today (2026-08-05)** | **Edges + polish1 code on `working`/`main` @ `161a820`** (hermetic green). **Live dogfood partial** — fabric/wait/sticky/backfill/multi-hop `created_with` proven; full boxes not signed. **Polish2** residuals on #125 (cold `semantic_only`, start `local_map` budget, recalls on expand). **Not** Gate B / product default-on of `durable_edges_enabled`. |

---

## Truth notes

### Edges stack (PR0–PR7)

| Claim | Status |
|-------|--------|
| EdgeStore both backends (jsonl + lance) + kinds + budgets | **Code** (PR1) |
| GraphView durable ∪ projected expand; `expand_moment`; full legend | **Code** (PR2) |
| `created_with` + `in_moment` write + retarget-to-1h tip | **Code** (PR3) |
| `recalls` (speak-time) + `has_channel` (encode-ready) | **Code** (PR4) |
| Pure semantic start + dual temporal reserve (#103 / #105 seed) | **Code** (PR5) |
| Raised product budgets + HARD_MAX clamp + frontier/moment cache (#105) | **Code** (PR6) |
| Graph API `edge_count` / `edges_by_kind` / `durable_edges_enabled` | **Code** (PR2+) |
| Glass overview edge counts polish | **Code** (PR7) |
| Visual free-browse graph (#61) | **Code** (PR8 on edges tip) |
| Live promote → edges → multi-hop walk on operator machine | **Partial live** 2026-08-05 — multi-hop + durable kinds proven; boxes below still open for full sign-off |
| Gate B / `durable_edges_enabled` factory default-on | **Not** this checklist’s done bar |

### Polish1 (PR0–PR6)

| Claim | Status |
|-------|--------|
| Unified semantic wait ceiling for long-path ANN (meal / traverse start / step hop / deferred recalls; band 1s–120s) | **Code** (PR1a) + **partial live** (`semantic_wait.applies_to`, 120s dogfood) |
| Dual deadlines: structural `expand_ms` vs semantic wait; free-browse / HTTP snappy or structural default | **Code** (PR1a) |
| Speak `recalls` **deferred** product default (promote never waits; idle-tick drain) | **Code** (PR1b) + **partial live** (store `recalls` count grew under dogfood) |
| `edge_recalls_max_ms` deprecated no-op for live ANN ceiling | **Code** (PR1b) |
| Host ~d2.5 `local_map` + kind filters (noisy kinds default-off) | **Code** (PR2) + **partial live** (map present; start often budget-starved — #125) |
| Skill walk maneuvers + tool surface `local_map` / `include_noisy_kinds` | **Code** (PR3) + **partial live** (model used named maneuvers) |
| Dev force edge backfill (`in_moment` structural-first) + Graph button | **Code** (PR4) + **partial live** (force backfill wrote `in_moment`) |
| Glass last finished walk sticky across moment close (process-life) | **Code** (PR5) + **partial live** (`has_last_session` after moment end) |
| Expand / walk budget honesty (structural vs semantic spent) | **Code** (PR5 + PR1a); accounting noise residual #125 |
| Live polish1 dogfood (120s wait, deferred recalls, map, backfill, sticky) | **Partial** — not full sign-off; polish2 [#125](https://github.com/jtwolfe/project-elyra/issues/125) |
| Gate B / factory default-on | **Still not** — polish1 does not flip flags |

**Factory defaults stay off:** `durable_edges_enabled` / `directed_traversal_enabled` / `directed_keep_enabled` / `semantic_enabled` / `embed_enabled` **false**. Dogfood must opt in via operator `elyra.toml`. Raised traverse *product defaults* (depth 5, nodes 80, …) apply when traversal is on; they do **not** enable writes or tools by themselves.

**Polish1 product defaults that *do* change behaviour when flags are on (not Gate B):**

| Knob | Product default | Dogfood note |
|------|-----------------|--------------|
| `semantic_wait_max_ms` | **15_000** (band max **120_000**) | Operator dogfood: set runtime / glass wait to **120s** for ROCm Nemotron long-path ANN |
| `edge_recalls_inline` | **false** (deferred) | Hermetic tests may force inline |
| `edge_backfill_dev_enabled` | **true** (dev / dogfood era) | Toggleable; marked dev; not a product Gate B flag |
| Free-browse `allow_semantic` | **off / snappy** unless explicit | Full wait only with `semantic_wait=1` (+ allow semantic) |

---

## Prep

- [ ] Tip of **`working` / `main`** (or post-merge pin) with edges + polish1; hermetic suite green
- [ ] `backend=lance` + `elyra[memory-lance]` (preferred dogfood path; JSONL structural-only is ok for edge fabric without ANN)
- [ ] Operator `elyra.toml` (or equivalent) includes:
  ```toml
  [memory]
  enabled = true
  write_atoms = true
  backend = "lance"
  durable_edges_enabled = true
  directed_traversal_enabled = true
  # keep follows traversal (OQ-A1) when directed_keep_enabled omitted/false
  semantic_enabled = true
  embed_enabled = true
  # optional: product default is already false (deferred)
  # edge_recalls_inline = false
  # optional: product default true for dogfood-era Graph backfill button
  # edge_backfill_dev_enabled = true
  ```
- [ ] **Semantic wait for long-path ANN:** glass / runtime `semantic_wait` enabled with `max_ms` **120000** (dogfood OK; product settings default remains 15s until raised)
- [ ] Encoder known: **mock** (dev/CI) or **Nemotron** warm for live recalls + semantic start
- [ ] Glass **Memory → Graph** tab available (`GET /api/memory/graph`)
- [ ] Confirm factory defaults elsewhere remain **off** when this install is not dogfooding (no accidental Gate B)

---

## Edge fabric — `created_with` / `in_moment` / retarget (#98 / #120)

- [ ] With edges flag **on**, promote a **speak** (or observation/model) while a prior meal exists → EdgeStore gains:
  - `in_moment` atom → `moment:{id}` hub
  - `created_with` atom → meal context atoms (non-empty meal only; **empty meal → zero** created_with — OQ-E1)
- [ ] Tool / ledger atoms: still get `in_moment`; **not** `created_with` sources or destinations (OQ-E2) — walkable via sequential / expand_moment
- [ ] Glass Graph overview: `edge_count` increases; `edges_by_kind` shows `created_with` / `in_moment` (and later `recalls` / `has_channel`)
- [ ] Graph neighbors on a new speak: durable `created_with` edges to **real atom ids** only (no virtual hubs as destinations)
- [ ] Age-out / FIFO (optional depth): when `created_with` hits kind window, retarget drops to **existing** youngest 1h tip with `meta.retarget_from` — **never invents** summary atoms (OQ-E7)
- [ ] Flag **off**: promote still succeeds; no new durable edge writes; tools still fail closed if traversal off

---

## Edge fabric — `recalls` / `has_channel` (#98 / #120)

- [ ] Speak promote (Elyra speak or user wake observation) with warm embedder + semantic on → durable `recalls` appear (**deferred** under polish1 — see polish1 section; not inline under 40ms island)
- [ ] `meta.cosine` present on recalls edges; expand recomputes weight (stored weight not authority)
- [ ] Soft-skip under cold encoder / encode-queue pressure / flag off — **never blocks** speak/promote
- [ ] View-observation / tool promote: **no** recalls writes
- [ ] After encode ready on multi-channel atom: `has_channel` edges appear; default expand **omits** has_channel destinations (virtual ids never walkable)
- [ ] Restart process: durable edges still listable (EdgeStore on disk — not session-only)

---

## Graph expand / `expand_moment` (#98 / #105)

- [ ] `GET /api/memory/graph/neighbors?atom_id=…` (or tool step expand) unions projected sequential/parent/same_moment/summary_* with durable kinds
- [ ] Co-members of a moment reachable via neighbors / step **without** a separate expand_moment call when `in_moment` fabric exists (hub rewrite → peer atoms)
- [ ] Explicit `expand_moment` (tool or GraphView) materializes all moment members including tool/ledger
- [ ] Dual `same_moment` + `in_moment` to same peer collapses preferring **`in_moment`**
- [ ] Legend on overview lists **all** EDGE_KINDS including summary_* and durable kinds (no stub legend)

---

## Pure semantic start + dual slots (#103 / #105 seed)

- [ ] `memory_traverse_start` with goal text (and optional `seed_media_ids`) under warm mock/Nemotron:
  - `seed_mode=auto` (default): semantic seeds fill after dual temporal reserve; `seed_sources` honest
  - dual_n default **2** → at least 1–2 temporal anchors when semantic hits present
- [ ] Cold encoder / no index: `semantic_reason` ∈ {`encoder_cold`, `no_index`, …}; **no torch cold-load** on start
- [ ] `seed_mode=semantic_only` + cold → **empty frontier OK** (does not fall through to temporal strip — pure semantic honesty for #103)
- [ ] `seed_mode=temporal_only` → strip fill; no semantic ANN
- [ ] Skill nudge: focused goals may use `semantic_only` (playbook); default remains `auto` (OQ-E6)
- [ ] Start structural budget: `traverse_start_expand_max_ms` product **250** for non-ANN start work / reporting; **ANN seed uses semantic wait ceiling** (polish1 — not 250 as ANN cap)
- [ ] Start payload reports `start_ms_budget` / `start_ms_spent` (structural) and honest semantic budget/spent when wait on

---

## Raised budgets + frontier cache (#105)

- [ ] Product defaults visible on overview / session when traversal on (approx): depth **5**, nodes **80**, steps **12**, frontier **24**, expand/step **5**, keep **20**, neighbor_k **16**, same_moment_k **8**, semantic_k **10**
- [ ] Tool budgets may **raise** above product up to HARD_MAX (e.g. `max_nodes=100` with hard 160 → session **100**)
- [ ] Multi-hop walk: expand_moment / step populates session `moment_member_cache`; glass session shows `moment_cache_size` ≥ 1 after moment expand
- [ ] Finish → glass considered/kept → next meal **directed_keep** when keep follows traversal (existing 2a path; verify still green)

---

## Glass honesty (overview)

- [ ] `GET /api/memory/graph` includes `edge_count`, `edges_by_kind`, `edge_store.durable_edges_enabled`
- [ ] Graph tab overview UI shows **durable edges** flag, total **edge count**, and compact **by-kind** counts (zeros ok when empty)
- [ ] Honesty note: traversal flag off is muted (expected default), not a red error
- [ ] With edges flag off and empty store: counts 0 / kinds empty; structural neighbor probe still works when store open

---

## Polish1 — unified semantic wait (long-path ANN)

Design: [design-memory-edges-polish1.md](../../design/memory/design-memory-edges-polish1.md) §1 / KD-P0.

- [ ] Status `semantic_wait.applies_to` lists long-path sites: meal select, traverse start, traverse step semantic, speak recalls deferred, http neighbors opt-in
- [ ] Operator sets wait max **120000** (dogfood OK) via glass / runtime; traverse start / step hop / deferred recalls share that ceiling identity (not secret 40 / 120 / 250 ms islands)
- [ ] Product settings default remains **15_000** until raised — *identity* of ceiling is unified; 120s is operator dogfood, not a new factory max
- [ ] `memory_traverse_start` `semantic_only` under warm encoder + index hits: seeds non-empty / `semantic_reason` not starved-timeout from structural 250ms
- [ ] Step with multi `expand_ids`: structural multi-id under `expand_ms`; **at most one** `semantic_hop` ANN per step under wait budget
- [ ] Free-browse / `GET …/neighbors` default: **no** multi-minute hang; semantic unchecked / snappy; full wait only with explicit `semantic_wait=1` (+ allow semantic)
- [ ] Cold encoder: `encoder_cold` (or honest cold reason); **no torch cold-load** on traverse start
- [ ] Glass `set_semantic_wait` changes long-path ceiling without process restart (worker overlay)
- [ ] Expand / walk surfaces report structural vs semantic spent/budget when relevant

---

## Polish1 — deferred speak recalls (product default)

Design: polish1 §1.3 / KD-P0-defer / PR1b.

- [ ] Speak → promote returns quickly (no inline ANN wait on promote path)
- [ ] After idle tick / deferred drain under warm encoder + wait on: EdgeStore gains `recalls` for that speak (`recalls` count > 0; not permanent 40ms soft-skip)
- [ ] Soft-skip still applies under cold / encode pressure / edges or semantic off — **never blocks** speak
- [ ] Metrics / status: deferred queue activity visible when present (`recalls_deferred_*` or equivalent)
- [ ] `edge_recalls_inline=true` only for debug / hermetic — **not** dogfood product path
- [ ] `edge_recalls_max_ms` is **not** presented as the live ANN ceiling in status

---

## Polish1 — host d2.5 `local_map` + kind filters

Design: polish1 §2 / KD-P2 / PR2.

- [ ] `memory_traverse_start` / `step` thin surface includes `local_map` (null only when no focus / empty seeds)
- [ ] Caps respected (approx): edges ≤ 16, ring ≤ 12, moment_peers ≤ 8, associative ≤ 5, d2 fanout limited
- [ ] Default map **omits** tool / ledger / raw model from primary ring; sequential bridges may appear with short labels / `bridge_noisy`
- [ ] `include_noisy_kinds=true` surfaces noisy kinds when goal needs them
- [ ] Multi-expand step: `local_map` = first expanded; `local_maps` ≤ 3 when multiple expand
- [ ] Map build does **not** re-run seed ANN; truncation → honest `map_truncated` / partial map, not tool failure
- [ ] Depth-1 star alone is **not** the model surface — host map present when focus exists

---

## Polish1 — skill walk maneuvers

Design: polish1 §3 / KD-P1 / PR3; skill: `skills/bundled/memory-traverse/SKILL.md`.

- [ ] Skill documents named maneuvers with worked tool-args examples: **Moment bloom**, **Context fan**, **Time spine**, **Associative enter**, **Anchor+dig**
- [ ] Skill states: `timeout` / `expand_truncated` under warm slow embedder ≠ empty memory; prefer structural maneuvers or wait
- [ ] Skill / tools document `local_map` read-before-blind-expand and `include_noisy_kinds`
- [ ] Skill does **not** promise disk-sticky last walk (process-life glass only)
- [ ] Multi-expand: at most one semantic hop per step; prefer structural multi-id

---

## Polish1 — dev force edge backfill

Design: polish1 §4 / KD-P-backfill / PR4.

- [ ] Graph glass **Force edge backfill** (or equivalent) visible when `edge_backfill_dev_enabled` and edges path live
- [ ] Force backfill raises `in_moment` for historical atoms with `moment_id` missing hub edge
- [ ] Re-run is cheap / idempotent: `written ≈ 0` when fabric already complete
- [ ] Progress / last result visible on glass (sync POST; no pollable job v1)
- [ ] `durable_edges_enabled` off or dev flag off: honest failure / button hidden; no silent writes
- [ ] v1 does **not** invent `created_with` / `recalls` history

---

## Polish1 — glass last-session stickiness

Design: polish1 §5 / KD-P-glass / PR5.

- [ ] Finish walk → glass `has_last_session` true; walk summary / considered / kept visible
- [ ] After **moment close / boundary**: **still** `has_last_session` (process-life sticky; not cleared on moment_close)
- [ ] Abandon retains sticky last session (existing behaviour)
- [ ] Tray / meal: directed_keep still packs on **next** `compose_meal` only (KD-A16 unchanged)
- [ ] Process restart: last walk **not** claimed durable (honesty)
- [ ] Expand / session glass shows structural vs semantic budget honesty when walk used ANN

---

## No Gate B / no default-on

- [ ] Confirm `durable_edges_enabled` factory default remains **false** on clean settings
- [ ] Confirm `directed_traversal_enabled` / `semantic_enabled` / `embed_enabled` factory defaults remain **false**
- [ ] This checklist does **not** authorize product default-on of edges, traversal, or Nemotron (Gate B is separate)
- [ ] Polish1 timeout / wait / defer / dev-backfill defaults are **not** Gate B
- [ ] Sign-off here is **edges fabric + traverse start/budgets + polish1 product polish dogfood** only

---

## Hermetic evidence (not a substitute for boxes above)

### Edges stack

| Suite | Role |
|-------|------|
| `tests/test_memory_edges.py` | EdgeStore put/list/budget/FIFO both backends; **backfill** idempotent / flags |
| `tests/test_memory_promote_edges.py` | created_with / in_moment / retarget / OQ-E1–E2 |
| `tests/test_memory_recalls_has_channel.py` | recalls rank/write soft-fail; has_channel; **deferred promote path + wait ceiling** |
| `tests/test_memory_graph.py` | durable expand, expand_moment, kind priority; **dual deadline** structural vs semantic |
| `tests/test_memory_graph_api.py` | overview counts + legend + neighbors honesty; **neighbors snappy defaults / wait opt-in; backfill API; session sticky** |
| `tests/test_memory_traverse.py` | seed modes, dual slots, budget clamp, moment cache; **local_map filters/caps; stickiness on moment_close; one ANN per step** |
| `tests/test_memory_traverse_tools.py` | tool schema budgets / seed_mode / media; **local_map surface; include_noisy_kinds** |
| `tests/test_settings.py` | product defaults + hard max validation; polish1 flags / deprecations |

### Polish1 additions

| Suite | Role |
|-------|------|
| `tests/test_semantic_wait.py` | Helper clamp/overlay; status `applies_to`; glass assets; 1s–120s band |
| `tests/test_presence_worker.py` | `_memory_settings_with_wait`; deferred recalls enqueue/drain; soft-skip cold |
| `tests/test_memory_recalls_has_channel.py` | PR1b deferred path; promote not blocked; wait ceiling; inline flag |
| `tests/test_memory_traverse.py` | PR1a dual deadlines / per-step ANN; PR2 local_map; PR5 stickiness |
| `tests/test_memory_graph.py` | PR1a dual deadline structural complete + semantic timeout |
| `tests/test_memory_graph_api.py` | PR1a neighbors defaults; PR4 backfill; PR5 sticky after moment_close |
| `tests/test_memory_edges.py` | PR4 backfill_in_moment writes + idempotent + flag gates |
| `tests/test_settings.py` | `edge_recalls_inline` default false; `edge_backfill_dev_enabled` default true; wait band |
| Skill / tools (docs) | `skills/bundled/memory-traverse/SKILL.md` + `tools/bundled/memory_traverse_*` maneuvers / local_map |

Optional live: warm Nemotron path for recalls + semantic start quality (mock proves plumbing). Prefer MM encode smoke ([mm-embed-dogfood.md](mm-embed-dogfood.md)) before rich multi-hop quality claims.

---

## Sign-off block — edges stack

| Field | Value |
|-------|--------|
| Operator | |
| Date | |
| Tip SHA | |
| Encoder backend | mock / nemotron |
| `durable_edges_enabled` | true (dogfood) |
| Result | pass / fail / partial |
| Notes | |

**Done for “edges + traverse green” product claim:** fabric + start + budgets boxes checked with notes, or residual filed on #98 / #120 / #103 / #105 with explicit defer. **Still not** Gate B or factory default-on of `durable_edges_enabled`.

---

## Sign-off block — polish1

| Field | Value |
|-------|--------|
| Operator | |
| Date | |
| Tip SHA | (`working`/`main` @ land, e.g. `161a820`, or operating pin) |
| Encoder backend | mock / nemotron |
| `semantic_wait.max_ms` (dogfood) | e.g. 120000 |
| Deferred recalls | observed / not observed |
| local_map | pass / fail / partial |
| Dev backfill | pass / fail / n/a |
| Last-session sticky | pass / fail |
| Skill maneuvers | read / exercised |
| `durable_edges_enabled` factory | still **false** |
| Result | pass / fail / partial |
| Notes | |

**Done for “polish1 green” product claim:** unified wait (120s dogfood OK) + deferred recalls + local_map + backfill + glass sticky + skill boxes checked with notes, **or** residual filed with explicit defer. **Polish2 residuals filed:** [#125](https://github.com/jtwolfe/project-elyra/issues/125). **Still not** Gate B or factory default-on of `durable_edges_enabled`.

---

## Related

- [design-memory-edges-and-traversal.md](../../design/memory/design-memory-edges-and-traversal.md) — KD-E* + PR0–PR8 edges stack
- [design-memory-edges-polish1.md](../../design/memory/design-memory-edges-polish1.md) — KD-P* + PR0–PR6 polish1
- [#125](https://github.com/jtwolfe/project-elyra/issues/125) — edges **polish2** residuals (cold semantic_only, start local_map budget, recalls on expand)
- [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md) — base 2a walk / Graph glass
- [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) — semantic seeds / MM loop
- [mm-embed-dogfood.md](mm-embed-dogfood.md) — prefer MM encode smoke before rich multi-hop quality claims
- [docs/goal/v0.1.md](../../goal/v0.1.md) — C13 meal/traversal; C14 edges
