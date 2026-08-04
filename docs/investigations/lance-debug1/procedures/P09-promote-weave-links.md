# P09 — Promote weave / links

| Field | Value |
|-------|--------|
| **Status** | Ready (PR4) |
| **Safety class** | **R1** (full materialization offline; no live promote required) |
| **Prove / disprove** | **H9** (cascade), bucket **G** |
| **Evidence** | weave edge counts thin-set vs full materialization |
| **Adjacency** | [../adjacency/promote-weave.md](../adjacency/promote-weave.md) |

## Purpose

On **full** disk materialization, count prev/next edges whose endpoints are **missing from the thin `to_arrow` set** — document weave fracture as a **cascade of thin load** (G secondary of B), not as an independent promote bug during a healthy mid-session process.

## Code touchpoints (inspection only)

| Item | Location |
|------|----------|
| Promote link | `elyra/memory/promote.py` `_link_and_put` ~L318 |
| Tails | `moment_tail` / `global_tail` over **`_by_id` only** |
| Links | `prev_atom_id` / `next_atom_id` on atoms; `update_links` |
| Walks | `walk_prev` / `walk_next` process maps |

Mid-session: promote links among **live full** `_by_id` (H3 healthy). After restart: load thins `_by_id` → new promotes attach to **thin tail only** → H9 cascade.

## Prerequisites

- [ ] P01 quarantine URI with full materialization path (H1b)
- [ ] Prefer same snapshot as load parity (thin id set known)
- [ ] No compact / delete on versions

## Procedure (executable)

### 1. Materialize full rows + thin id set

```bash
export LANCE_DEBUG_URI=...   # quarantine
export PYTHONPATH=.

# Prefer ids from prior api-matrix:
#   thin = h1a.arrow_ids / bare to_arrow ids
#   full = head(n_full) or to_lance table rows
```

Optional automated helper (if using consumer_compare / ad-hoc):

```bash
python docs/investigations/lance-debug1/scripts/consumer_compare.py \
  --uri "$LANCE_DEBUG_URI" \
  --weave-report \
  --out "$RUN/consumer-compare.json"
```

(`--weave-report` adds prev/next endpoint analysis; see script help.)

### 2. Build prev/next graph on full rows

For each full-materialized atom with `prev_atom_id` / `next_atom_id`:

| Edge type | Count |
|-----------|-------|
| Both endpoints in thin set | in-process weave intact after thin load |
| One endpoint **outside** thin set | **broken in-process** after restart (G signal) |
| Both outside thin | invisible to process entirely |

### 3. Island / tip analysis

| Pattern | Informs |
|---------|---------|
| Thin set is **recent contiguous tip** by version / t_start | residual H4 “tip only” (still secondary if H1a holds — H1a is table prefix, not necessarily newest) |
| Thin set is **table-order prefix** matching `head(10)` | H1a — default limit; not random sample |
| Random sample of ids | different client bug class |

**Expected if H1a:** thin ids == first 10 of `head` order, **not** “newest 10 by t_start” necessarily — so weave break edges are common when newest tools link to older non-prefix atoms.

### 4. H9 cascade statement

| Phase | Behavior |
|-------|----------|
| Live session (full `_by_id`) | `_link_and_put` uses full tails; disk weave grows (H3) |
| After restart (thin `_by_id`) | tails only among survivors; new promotes amplify thin-set skew |
| Haiku amplification | consumer order + residual wake (H12) among survivors — secondary |

**H9** is a **cascade documentation** hypothesis, not a product fix in this package.

## Pass / fail (hypotheses)

| Hypothesis | Pass |
|------------|------|
| **H9 cascade** | Non-zero (typically many) edges with endpoint outside thin set; post-restart promote can only extend thin tail |
| **G as independent root** | **refuted** if mid-session promote was healthy (H3) and disk full APIs large |

## Expected if load is default-limit prefix

- Thin set = table-order prefix (~10), not random.
- Many disk sequential edges cross the thin boundary → process walks incomplete after restart.
- Does **not** require “promote never wrote.”

## Forbidden

- Live promote stress as the only proof path (R1 offline sufficient)
- Compacting versions to “fix” weave
- Product changes to `_link_and_put` in this package

See design §P09 and [../adjacency/promote-weave.md](../adjacency/promote-weave.md).
