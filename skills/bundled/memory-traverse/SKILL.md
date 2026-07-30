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

1. **`memory_traverse_start`** with a short `goal` (+ optional `seed_query` / `seed_atom_ids`)
2. On `traverse_disabled` / `traverse_unavailable` → honest stop (ledger / speak); no invent
3. Else continue the loop below

## Loop

```text
memory_traverse_start(goal, seed_query?)
  → read frontier (label ≤80, preview ≤400 on seeds / new expands)
  → memory_traverse_inspect(atom_ids) when label/preview insufficient for keep
  → memory_traverse_step(expand_ids, keep_ids?, scratchpad?)
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

## Tool map

| Tool | Role |
|------|------|
| `memory_traverse_start` | Create session; seed explicit / semantic / temporal |
| `memory_traverse_step` | Expand frontier nodes; provisional keep; scratchpad |
| `memory_traverse_inspect` | Capped body slices before keep |
| `memory_traverse_finish` | Confirm keep-set + walk summary; sticky snapshots |
| `memory_traverse_abandon` | Drop active only; last finished + meal keep retained |

## Failure modes

| Signal | Action |
|--------|--------|
| `traverse_disabled` | Stop; feature flag off — do not invent a walk |
| `traverse_unavailable` | Stop; host wiring missing — note for operator |
| `no_active_session` | Call `start` first (or finish already happened) |
| `atom_not_found` | Drop that id; re-inspect only known considered ids |
| `expand_truncated` / cold encoder | Structural path may still work; do not thrash semantic |
| empty seeds + thin frontier | Abandon or finish with honest empty keep |

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
