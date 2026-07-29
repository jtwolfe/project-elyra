# Evidence matrix

Observation × hypothesis × bucket. Use **relative** relations; absolute N is run-specific.

**Status of cells:** fill “Evidence run” during PR5 dogfood seal. This file is the **procedure checklist** for PR4 adjacency.

Legend: **S** = supports · **R** = refutes · **D** = disconfirm target · **C** = cascade/secondary

---

## Core (H1 / H1a / H1b / H2) — critical path

| Observation | Supports | Refutes | Bucket | Procedure | Evidence run |
|-------------|----------|---------|--------|-----------|--------------|
| `count_rows` ≫ bare `to_arrow` (~10); full APIs large | H1 | “disk lost data” | A | P01 | |
| `to_arrow` ids/order == `head(10)` prefix | H1a | H4 “random/latest fragment only” as sole | A | P01 | |
| H1b: `head(n_full)` and/or `to_lance` full while bare thin | H1b | “impossible full materialize” | A | P01 | |
| `hasattr(table, "query") is False` on 0.20.0 sync Table | (impl note) | treating missing `query` as H1b fail | A | P01 | |
| `_load` uses bare `to_arrow` only | H2 | | B | R0 / CODE-PATH-MAP | |
| process `atom_count` ≈ `n_arrow` ≪ `n_full` after open | H2 | | B | P02 | |
| Corrupt skip count ≪ gap | | H5 | B | P02 | |

---

## Write / version residual

| Observation | Supports | Refutes | Bucket | Procedure | Evidence run |
|-------------|----------|---------|--------|-----------|--------------|
| Disk retains large corpus (full APIs) | H3 | “promote never wrote” | C healthy | P01, P04 | |
| Live/quarantine `put_atom` grows `n_full` | H3 | | C | P04 | |
| Version history row counts non-decreasing to large N | H3; H10 not active now | H10 as **current** thin process | C | P08 | |
| Version history sudden collapse then partial | H10 historical | | A+C | P08 | |

---

## Adjacency D — embed / index (H6, H11)

| Observation | Supports | Refutes | Bucket | Procedure | Evidence run |
|-------------|----------|---------|--------|-----------|--------------|
| `vectors_ready` tiny, `below_ivf_min` with small N | H2→D **C** | H6 as **primary** | D secondary | P05 | |
| `list_ready_embeddings_for_seed` ≤ process ready | cascade | “seed reads disk full” | D | P05 | |
| Missing emb only for ids **in** `_by_id` with disk ready | H6 **S** | | D | P05 | |
| No such holes (gap = unloaded ids) | | H6 **D** | D | P05 | |
| expand_ms_spent ≫ budget (e.g. 80ms → tens of s) | H11 **S** | “truncation causes 94s” as row-loss | D adj | P05/P06 | |

---

## Adjacency E — graph / traverse / meal (H8, H12)

| Observation | Supports | Refutes | Bucket | Procedure | Evidence run |
|-------------|----------|---------|--------|-----------|--------------|
| Traverse few temporal seeds; keep set haiku tools | H2→E **C** | H8 as **primary** | E secondary | P06 | |
| Full ephemeral store surfaces non-haiku neighbors; thin does not | | H8 **D** | E | P06 + consumer_compare | |
| Meal episodic haiku prior-moment / semantic `no_index` | H2→E→D **C** | independent meal hide | E | P06 | |
| wait_timeout → haiku after restart (BUG-wake-02) | H12 **S** as consumer | as **root** of Lance gap | E/F adj | P06 + known-bugs | |

---

## Adjacency F — glass (H7)

| Observation | Supports | Refutes | Bucket | Procedure | Evidence run |
|-------------|----------|---------|--------|-----------|--------------|
| Post-restart glass `atom_count` ~ process thin set | H2 | H7 alone as root | B, F | P03, P07 | |
| Glass list cap 200, count ~10–13 | | H7 **D** (cap not root) | F | P07 | |
| Glass newest-first haiku among thin `_by_id` | consumer order | “to_arrow selects haiku” | E/F | P07 | |
| Raw `to_arrow` kinds summary+tool prefix (not haiku-only) | H1a | haiku-required for P01 | A | P01 | |

---

## Adjacency G — promote weave (H9)

| Observation | Supports | Refutes | Bucket | Procedure | Evidence run |
|-------------|----------|---------|--------|-----------|--------------|
| Disk edges with endpoint missing from thin set | H9 **C** | | G | P09 | |
| Live promote mid-session works | H3 | G as write failure | C | P04 | |
| After restart new promotes attach to thin tail only | H9 **C** | | G | P09 | |

---

## Interaction summary

```text
A (default-limit to_arrow)
  └─► B (_load thin _by_id / health)
        ├─► D (vectors_ready / ANN starve)     H6 default disconfirm
        ├─► E (traverse / meal / graph myopia) H8 default disconfirm
        ├─► F (glass reports process truth)    H7 default disconfirm
        └─► G (post-restart weave fracture)    H9 cascade doc
C write healthy (H3)
H11 / H12 — adjacent known-bugs; not Lance row-loss root
```

| If primary is… | Then secondary usually… | Do not elevate as root until… |
|----------------|-------------------------|-------------------------------|
| A+B | D, E, F, G | disconfirmed as independent (this matrix) |
| H11 / H12 alone | — | R1 H1a/H1b on disk |

---

## Provisional root-cause template (after H1a+H1b+H2)

When core rows are high-confidence, draft (dossier PR5; adjacency must not block):

```text
Primary: Bucket A — bare to_arrow default-limit (~10) misused as full-table load
Direct product impact: Bucket B — LanceMemoryStore._load rebuilds _by_id from thin set
Mechanism: H1 + H1a + H1b + H2
Demoted: H4 fragment-only; H10 as explanation of current thin process
Cascade (secondary): D, E, F, G as documented in adjacency/
Adjacent: H11 BUG-mem-gpu-01; H12 BUG-wake-02
```

Do **not** implement the fix in this package.
