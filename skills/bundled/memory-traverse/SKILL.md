---
name: memory-traverse
description: Model-guided multi-hop memory walk when one-shot meal semantic is not enough. Use for finding related past atoms under budget; not for ordinary chat.
---

# Memory traverse

Disciplined multi-hop walk over thin `memory_traverse_*` tools. Prefer this skill
when associative recall needs **steering** beyond the meal's one-shot semantic
channel. Temporary until `finish` — never invent atom bodies or keep blind ids.

## When to use

- Multi-hop / ambiguous recall ("what led to X?", related past work across moments)
- Meal semantic empty, thin, timed out, or clearly the wrong neighbourhood
- Operator asks to dig / walk / explore memory under budget
- You need a **keep-set + walk summary** for later outer context (directed_keep)

## When not to use

- Ordinary chat or open-moment spine already in the meal → rely on temporal / episodic
- One-shot similarity is enough → meal `semantic` (no walk)
- Flag off (`traverse_disabled`) → stop; do not thrash start
- You only need bodies of known ids this hop → `memory_traverse_inspect` after start,
  or note ids for next outer meal after finish — do not rewrite the full meal mid-walk

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry.
Do not answer with free-text only.

1. **`memory_traverse_start`** with a short `goal` (+ optional `seed_query` /
   `seed_atom_ids` / `seed_media_ids` / `seed_mode`)
2. On `traverse_disabled` / `traverse_unavailable` → honest stop (ledger / speak); no invent
3. Else continue the loop below

### Seed mode (product default **`auto`**)

| Mode | When |
|------|------|
| **`auto`** (default) | Open-ended digs; dual temporal anchors attach when semantic hits so the walk is not only ANN |
| **`semantic_only`** | **Prefer when you already know what you are looking for** (focused traversal / named topic) — pure semantic start; empty frontier is OK if cold/no hits |
| `temporal_only` | Recent strip only (no ANN) |
| `explicit_only` | Only the atom ids you already have |

Do **not** wait for meal semantic timeout before choosing `semantic_only` — if the
goal is already focused, start pure semantic immediately.

Dual-start honesty: under `auto`, up to `dual_n` (default 2) temporal anchors are
**reserved** before semantic fill so recent context is not starved by a full ANN top-k.
Cold encoder → `encoder_cold` (no torch load on start); structural/explicit still work.

Media seed: pass `seed_media_ids` (+ usually `semantic_only`) when the entry is an
attachment; start semantic wait uses the unified long-path ceiling.

## Graph physics handles

Host expand follows these edge kinds. You do not name edges in tool args — choose
**maneuvers** (below) so expand lands on the right fabric. Prefer reading
`local_map` (edges + compass) before blind expand.

| Handle | Walkable? | Prefer when |
|--------|-----------|-------------|
| `sequential` | Yes | Narrative before/after (time spine) |
| `in_moment` / expand-moment rewrite | Yes | Co-members of a moment (moment bloom) |
| `same_moment` | Yes (soft, capped) | Fallback when durable `in_moment` thin |
| `created_with` | Yes | What was in context at birth (context fan) |
| `recalls` | Yes (durable when present) | Speak-time “reminds me of” without new ANN |
| `parent_of` / `child_of` | Yes | Parcel / family hierarchy |
| `summary_child` / `summary_source` / `supersedes` | Yes | Ladder up/down summaries |
| `semantic_hop` | Yes (ephemeral ANN) | Associative jump — **at most one per step** (host) |
| `has_channel` | **No** (usual) | Modality bond only; not a walk destination |

## Named walk maneuvers (KD-P1)

Pick a maneuver for the **goal**, not every step. One worked tool-args example each.
Ids below are placeholders — use real frontier / seed ids from the thin surface.

### Moment bloom — co-members of a moment

Intent: open everyone in the same moment as a known seed (hub rewrite / soft peers).

```json
// start from a known atom, then expand it so in_moment peers enter the frontier
memory_traverse_start({
  "goal": "who else was in this moment?",
  "seed_mode": "explicit_only",
  "seed_atom_ids": ["atom_focus"]
})
memory_traverse_step({
  "expand_ids": ["atom_focus"],
  "scratchpad": "moment bloom: expand focus for in_moment / same_moment peers"
})
```

### Context fan — born-with context

Intent: what rode in the meal when this atom was created (`created_with` out;
reverse in for “who was born with me”). Steer from `local_map` edges / ring.

```json
memory_traverse_start({
  "goal": "what context was present when this was born?",
  "seed_mode": "explicit_only",
  "seed_atom_ids": ["atom_born"]
})
// After start: read local_map.edges for created_with, then expand those dst ids
memory_traverse_step({
  "expand_ids": ["atom_context_a", "atom_context_b"],
  "scratchpad": "context fan along created_with"
})
```

### Time spine — narrative before/after

Intent: walk prev/next sequential chain (and temporal seeds when open-ended).

```json
memory_traverse_start({
  "goal": "what happened just before/after this speak?",
  "seed_mode": "explicit_only",
  "seed_atom_ids": ["atom_speak"]
})
// Prefer sequential prev/next from local_map.compass.sequential
memory_traverse_step({
  "expand_ids": ["atom_prev_or_next"],
  "scratchpad": "time spine sequential"
})
```

Open-ended recent strip (no ANN):

```json
memory_traverse_start({
  "goal": "recent narrative around X",
  "seed_mode": "temporal_only",
  "seed_query": "X"
})
```

### Associative enter — jump by meaning

Intent: land in a semantic neighborhood (`semantic_only` start / durable `recalls` /
one `semantic_hop` on a step). Prefer focused `semantic_only` when you know the topic.

```json
memory_traverse_start({
  "goal": "find past work on edge enrichment dogfood",
  "seed_mode": "semantic_only",
  "seed_query": "edge enrichment dogfood"
})
// Later: one expand may take a semantic_hop; further expands same step stay structural
memory_traverse_step({
  "expand_ids": ["atom_semantic_seed"],
  "scratchpad": "associative enter; expect ≤1 semantic_hop this step"
})
```

Media-as-query entry:

```json
memory_traverse_start({
  "goal": "atoms near this image",
  "seed_mode": "semantic_only",
  "seed_media_ids": ["att_…"]
})
```

### Anchor+dig — dual temporal anchors, then dig inward

Intent: open-ended dig that keeps recent context (`auto` + dual_n) then **structural**
expand (moment / sequential / created_with). Avoid thrashing ANN every step.

```json
memory_traverse_start({
  "goal": "what have we been working on related to memory graph?",
  "seed_mode": "auto",
  "seed_query": "memory graph walk"
})
// Dig with multi-id structural expand; do not re-seed semantic every step
memory_traverse_step({
  "expand_ids": ["seed_semantic_or_temporal_a", "seed_temporal_b"],
  "scratchpad": "anchor+dig: structural multi-expand; one semantic_hop max if needed"
})
```

## local_map compass (steering)

On start and when expand moves focus, the host may return **`local_map`** (~d2.5
filtered map for primary seed / first expanded id) and optional **`local_maps`**
(≤3 on multi-expand). May be null when map disabled or no focus.

**Read the map before blind expand.** Use:

| Field | Steer with |
|-------|------------|
| `edges` | Weighted neighbors (kinds after noise filter) |
| `ring` | Primary nodes — prefer **speak / observation / summary** |
| `compass.sequential` | prev/next for time spine |
| `compass.moment_peers` | moment bloom candidates |
| `compass.ladder` | parent summary / child tips |
| `compass.associative` | durable recalls / already-computed hop (no extra encode for map) |
| `filters` | whether noisy kinds were omitted |

Do not invent graph structure the map does not show. If map is null, fall back to
frontier labels/previews and inspect.

## Noise policy

- **Prefer keep** speak, observation, summary (and other primary kinds).
- **Tool / ledger / raw model** are noisy: omitted from `local_map` ring by default;
  only sequential bridges with short labels (`tool:name`, `ok`) appear.
- Pass **`include_noisy_kinds: true`** on start/step only when the goal explicitly
  needs tool/ledger/model atoms (e.g. “which tool failed?”).
- Do not keep noisy kinds unless the goal demands them.

## Semantic hop budget (host)

- **At most one `semantic_hop` ANN call per `memory_traverse_step`** (first expand_id
  that still has ANN budget). Further `expand_ids` in the same step are structural-only.
- Prefer multi-id **structural** expand when blooming a moment or fanning context.
- Do not thrash step just to force more ANN.

## Timeout / truncated ≠ empty memory

Under a **warm but slow** embedder:

- `semantic_reason=timeout`, `expand_truncated`, or empty semantic seeds **do not**
  mean the store is empty.
- Prefer **structural maneuvers** (time spine, moment bloom, context fan, explicit
  seeds) or wait for encode; then retry associative enter if still needed.
- **Never invent** atoms to fill a thin frontier. Abandon/finish honestly if no signal
  after structural paths.

Cold encoder remains fail-soft (`encoder_cold`); start does not cold-load torch.

## Loop

```text
memory_traverse_start(goal, seed_query?, seed_mode?, seed_media_ids?, include_noisy_kinds?)
  → read frontier + local_map (label ≤80, preview ≤400 on seeds / new expands)
  → memory_traverse_inspect(atom_ids) when label/preview insufficient for keep
  → memory_traverse_step(expand_ids, keep_ids?, scratchpad?, include_noisy_kinds?)
  → … until stop
  → memory_traverse_finish(keep_ids?, summary_hint?)
     or memory_traverse_abandon if no signal
```

### Hard rules

1. **Inspect before keep** when the 80-char label (or even the 400 preview) is not
   enough to decide. Do **not** keep blind ids (KD-A17).
2. Use **exact** tool names (`memory_traverse_start`, …). Skill name is `memory-traverse`.
3. **One active walk** at a time. New start abandons the previous active only.
4. **No full meal rewrite mid-walk.** Tools return a thin surface only.
5. Prefer **few good expands** over thrashing steps. Respect budget remaining.
6. **Fail closed:** never invent atom content on `atom_not_found`, empty seeds, or errors.
7. Moment co-members appear via normal **step expand** (in_moment hub rewrite) —
   no separate expand_moment tool required.
8. **Steer from `local_map`** when present; do not ignore compass for blind multi-expand.

## Stop conditions

Stop (finish or abandon) when any of:

- Enough keeps for the goal
- Budgets exhausted (`steps` / `nodes` / `depth` remaining 0) — finish with partial keep OK
- Goal answered from inspected previews
- No signal (empty frontier / empty seeds after start) → **abandon**
- `traverse_disabled` / `traverse_unavailable` / hard error → stop honestly

## After finish (meal timing — KD-A16)

| Surface | Timing |
|---------|--------|
| Glass Graph | **Immediate** — last finished walk (considered vs kept + budgets) |
| Outer meal `directed_keep` | **Next `compose_meal` only** — next re-outer, moment boundary, or hop if regather N>0 — **not** guaranteed same hop after the tool result |

Use `memory_traverse_inspect` for same-turn body access. Do not assume the keep-set
is already packed into this hop's outer package.

### Glass stickiness honesty

- Last finished walk is **process-life** sticky (survives moment close; abandon of
  **active** only).
- It is **not** disk-durable across process restart — do not promise a surviving
  last walk after restart.
- `clear_confirmed_keep(clear_glass=True)` remains the operator escape.

## Tool map

| Tool | Role |
|------|------|
| `memory_traverse_start` | Create session; seed explicit / semantic / dual-temporal; optional `local_map` |
| `memory_traverse_step` | Expand frontier nodes; provisional keep; scratchpad; `local_map` / `local_maps` |
| `memory_traverse_inspect` | Capped body slices before keep |
| `memory_traverse_finish` | Confirm keep-set + walk summary; process-life glass sticky |
| `memory_traverse_abandon` | Drop active only; last finished + meal keep retained |

## Failure modes

| Signal | Action |
|--------|--------|
| `traverse_disabled` | Stop; feature flag off — do not invent a walk |
| `traverse_unavailable` | Stop; host wiring missing — note for operator |
| `no_active_session` | Call `start` first (or finish already happened) |
| `atom_not_found` | Drop that id; re-inspect only known considered ids |
| `expand_truncated` / `encoder_cold` / semantic `timeout` | **≠ empty memory** — try structural maneuvers; do not thrash ANN |
| empty seeds + thin frontier | Abandon or finish with honest empty keep |
| `semantic_only` + empty | Expected when cold/no hits — abandon honestly |

## Quality / completion

Done when:

- Keep-set confirmed with inspect/preview-backed choices, or
- Abandoned honestly with no useful signal, or
- Budgets exhausted with partial keep finished

## Out of scope

- Automatic walk every hop (model/skill opt-in only)
- Writing temporary atoms into the store
- Phase 3 success-weight learning
- Rewriting meal composition mid-walk
- Blind multi-start thrash without finish/abandon
- Claiming last walk survives process restart
