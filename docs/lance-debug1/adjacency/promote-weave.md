# Adjacency — Promote sequential weave (bucket G)

| Field | Value |
|-------|--------|
| **Bucket** | G |
| **Procedures** | [P09](../procedures/P09-promote-weave-links.md) |
| **Hypotheses** | **H9** (post-restart cascade) |
| **Related** | H3 healthy mid-session write; H1a thin set shape |

## Cascade

```text
Live session: _by_id full → moment_tail / global_tail full → _link_and_put healthy (H3)
Restart: bare to_arrow → _by_id thin (~10)
  → tails only among survivors
  → new promotes link only to thin tail
  → haiku/tool skew can amplify for subsequent sessions (with E/F residual)
```

`promote.py` `_link_and_put` (~L318) resolves `prev` via `store.moment_tail` / `global_tail` — both process-map only.

## H9 — cascade documentation (not product fix)

| Phase | Observation |
|-------|-------------|
| Disk full materialization | many `prev`/`next` edges cross outside thin id set |
| Process after restart | walks incomplete; tails short |
| New promotes | attach among survivors only |

**H9 supported as cascade** when those cross-boundary edges are abundant **and** mid-session promote was healthy (H3). Elevating G as **root** of missing disk rows is wrong if full APIs still large.

## Tip vs prefix (informs residual H4)

| Thin set shape | Meaning |
|----------------|---------|
| == `head(10)` id order | H1a default-limit **prefix** (primary) |
| Newest-by-`t_start` tip only | would support different “tip fragment” story — check against H1a |
| Random sample | client bug class beyond default limit |

Snapshot B: prefix kinds summary+tool, while glass newest-first shows haiku tools — **weave + list order**, not promote-only.

## Offline check (R1)

1. Full rows: build prev/next multigraph.
2. Thin ids = bare `to_arrow` atom_ids.
3. Count edges with an endpoint ∉ thin set → G fracture signal.
4. Optional: `consumer_compare.py --weave-report`.

## Non-goals

- Changing promote link policy
- Rebuilding historical weave on disk
- Compaction to “repair” versions
