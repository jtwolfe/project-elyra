# Durable edges + traversal extension — operator dogfood checklist (#98 / #120 / #103 / #105)

| Field | Value |
|-------|--------|
| **Class** | STATE |
| **Audience** | Operators dogfooding EdgeStore fabric + pure semantic start + raised traverse budgets |
| **Design** | [design-memory-edges-and-traversal.md](../../design/memory/design-memory-edges-and-traversal.md) (**Shipped (code)**) |
| **Architecture priors** | [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md), [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) |
| **Program** | [README.md](README.md) — edges/traversal close-out |
| **Related issues** | [#98](https://github.com/jtwolfe/project-elyra/issues/98) source/context edges; [#120](https://github.com/jtwolfe/project-elyra/issues/120) C14 edges dogfood; [#103](https://github.com/jtwolfe/project-elyra/issues/103) semantic seed timeout; [#105](https://github.com/jtwolfe/project-elyra/issues/105) frontier cache + dual start |
| **Claim today (2026-08-05)** | **Code complete + hermetic tests green** on edges stack (PR0–PR7: EdgeStore, GraphView, `created_with`/`in_moment`/retarget, `recalls`/`has_channel`, pure semantic start + dual slots, raised budgets + frontier cache, this checklist). **Live operator dogfood not signed.** **Not** Gate B / product default-on. |

---

## Truth notes

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
| Live promote → edges → multi-hop walk on operator machine | **Open** — boxes below |
| Gate B / `durable_edges_enabled` factory default-on | **Not** this checklist’s done bar |

**Factory defaults stay off:** `durable_edges_enabled` / `directed_traversal_enabled` / `directed_keep_enabled` / `semantic_enabled` / `embed_enabled` **false**. Dogfood must opt in via operator `elyra.toml`. Raised traverse *product defaults* (depth 5, nodes 80, …) apply when traversal is on; they do **not** enable writes or tools by themselves.

---

## Prep

- [ ] Tip of edges stack (or `feature/edge-enrichment` after merge) with hermetic suite green
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
  ```
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

- [ ] Speak promote (Elyra speak or user wake observation) with warm embedder + semantic on → up to ~5 durable `recalls` (top ~15 by sim, then newest by `t_start` — OQ-E3)
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
- [ ] Start budget: `traverse_start_expand_max_ms` product **250**; payload reports `start_ms_budget` / `start_ms_spent`

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

## No Gate B / no default-on

- [ ] Confirm `durable_edges_enabled` factory default remains **false** on clean settings
- [ ] Confirm `directed_traversal_enabled` / `semantic_enabled` / `embed_enabled` factory defaults remain **false**
- [ ] This checklist does **not** authorize product default-on of edges, traversal, or Nemotron (Gate B is separate)
- [ ] Sign-off here is **edges fabric + traverse start/budgets dogfood** only

---

## Hermetic evidence (not a substitute for boxes above)

| Suite | Role |
|-------|------|
| `tests/test_memory_edges.py` | EdgeStore put/list/budget/FIFO both backends |
| `tests/test_memory_promote_edges.py` | created_with / in_moment / retarget / OQ-E1–E2 |
| `tests/test_memory_recalls_has_channel.py` | recalls rank/write soft-fail; has_channel |
| `tests/test_memory_graph.py` | durable expand, expand_moment, kind priority |
| `tests/test_memory_graph_api.py` | overview counts + legend + neighbors honesty |
| `tests/test_memory_traverse.py` | seed modes, dual slots, budget clamp, moment cache |
| `tests/test_memory_traverse_tools.py` | tool schema budgets / seed_mode / media |
| `tests/test_settings.py` | product defaults + hard max validation |

Optional live: warm Nemotron path for recalls + semantic start quality (mock proves plumbing).

---

## Sign-off block

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

## Related

- [design-memory-edges-and-traversal.md](../../design/memory/design-memory-edges-and-traversal.md) — KD-E* + PR0–PR7
- [architecture/phase-2a-directed-traversal.md](architecture/phase-2a-directed-traversal.md) — base 2a walk / Graph glass
- [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) — semantic seeds / MM loop
- [mm-embed-dogfood.md](mm-embed-dogfood.md) — prefer MM encode smoke before rich multi-hop quality claims
- [docs/goal/v0.1.md](../../goal/v0.1.md) — C13 meal/traversal; C14 edges
