# Adjacency — Meal / context (bucket E)

| Field | Value |
|-------|--------|
| **Bucket** | E (meal packaging) |
| **Procedures** | [P06](../procedures/P06-graph-traverse-meal.md), glass [P07](../procedures/P07-glass-serialization.md) |
| **Hypotheses** | **H8** (disconfirm independent filter), **H12** (BUG-wake-02 consumer) |
| **Glass** | `GET /api/memory/context` |

## Cascade

`compose_meal` / ladder / episodic packaging read **store process maps** (and residual last-meal snapshot). Under thin load:

| Channel | Typical thin-world symptom |
|---------|----------------------------|
| Episodic / ladder | only summaries / moments present in `_by_id` |
| Prior-moment tools | haiku tools if those are the survivors / recent puts |
| Semantic | `semantic_omitted_reason` often `no_index` when D starved |
| Directed keep | omitted reasons when budgets + thin candidates |

## Split expectation (meal vs to_arrow)

| Source | Ordering |
|--------|----------|
| bare `to_arrow` | physical table prefix — **not** meal ranking |
| Meal items | channel budgets + ladder/time among **loaded** atoms |
| Glass Context tab | serializes last/current meal package (may be snapshot vs recompose) |

Haiku dominance in Context is a **consumer of thin process + residual**, not proof that `to_arrow` selected haiku (H1a: prefix is summary+tool on snapshot B).

## H12 — BUG-wake-02

| Claim | Status framing |
|-------|----------------|
| Wake-02 steers into haiku after restart via residual glass/sandbox/meal | **consumer** of thin memory + other residue |
| Wake-02 causes missing Lance rows | **false** if H1b full APIs large |

Cross-link: `docs/known-bugs.md` **BUG-wake-02**. Capture wait_timeout moment notes under evidence; do not elevate as Lance root without R1.

## R2 capture

```bash
BASE="${ELYRA_GLASS_BASE:-http://127.0.0.1:8787}"
curl -sS "$BASE/api/memory/context" | tee context.json
# Note: semantic_omitted_reason, channels, item kinds, open_moment_id
```

## Non-goals

- Meal budget redesign
- Wake sanitation product fix (separate design)
- Semantic architecture rewrites (stretch-2 docs stay out of scope)
