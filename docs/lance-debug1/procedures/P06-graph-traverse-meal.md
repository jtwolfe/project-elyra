# P06 — Graph / traverse / meal

| Field | Value |
|-------|--------|
| **Status** | Ready (PR4) |
| **Safety class** | **R2** + optional offline harness (**R1**) |
| **Prove / disprove** | **H8** (disconfirm independent filter), **H11**, **H12** |
| **Bucket** | E (usually downstream of B) |
| **Script (optional)** | [`../scripts/consumer_compare.py`](../scripts/consumer_compare.py) |
| **Adjacency** | [../adjacency/graph.md](../adjacency/graph.md), [../adjacency/meal.md](../adjacency/meal.md) |

## Purpose

Document consumer behavior under **thin vs full** corpus. Prove graph / traverse / meal myopia is explained by process maps (`_by_id` / index seed), not an independent “hide non-haiku” filter (**H8**). **No patched product `_load`.**

## Split expectation (critical)

| Layer | Order / selection | Typical dogfood look |
|-------|-------------------|----------------------|
| **A — bare `to_arrow`** | Table **physical prefix** (`head(10)` order) | summary×6 + tool×4 (snapshot B) — **not** haiku-only |
| **B — process `_by_id`** | Load inherits A (+ mid-session puts) | thin set after restart |
| **C — glass / meal / traverse** | **Newest-first** weave, residual meal, wake context among B | haiku tools may dominate UI |

Do **not** require glass haiku list kinds to match raw `to_arrow` kinds. See [../adjacency/glass.md](../adjacency/glass.md).

## Prerequisites

- [ ] P01 H1a/H1b preferred; P02/P03 for thin process confirmation
- [ ] Optional traverse session / moment tape for moment `4fb55533…` (or current dogfood id)
- [ ] `PYTHONPATH=.` if running `consumer_compare.py`
- [ ] No product `_load` patch

## Procedure (executable)

### 1. R2 — Thin store consumers (live after restart)

```bash
BASE="${ELYRA_GLASS_BASE:-http://127.0.0.1:8787}"
RUN=docs/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01
mkdir -p "$RUN/glass"

curl -sS "$BASE/api/memory/context" | tee "$RUN/glass/context.json"
curl -sS "$BASE/api/memory/graph" | tee "$RUN/glass/graph.json"
curl -sS "$BASE/api/memory/graph/session" | tee "$RUN/glass/graph-session.json"
# Optional: neighbors for a seed atom_id from atoms list
# curl -sS "$BASE/api/memory/graph/neighbors?atom_id=..." | tee "$RUN/glass/graph-neighbors.json"
```

Record:

| Signal | Expected under thin load |
|--------|---------------------------|
| Temporal / graph seeds | ⊆ loaded atoms only |
| Structural neighbors | only among loaded (`get_atom` miss → edge dropped) |
| Meal episodic | ladder + prior-moment among process maps |
| Semantic omit | often `no_index` / thin seed when D starved |
| Glass atoms list | newest-first among `_by_id` (may be haiku tools) |

### 2. Dogfood traverse session (if logged)

Replay or inspect moment tape / traverse JSON (e.g. moment `4fb55533…` from OBSERVED-FACTS):

| Observation | Bucket |
|-------------|--------|
| Few temporal seeds (current + haiku tools) | E secondary of B |
| `expand_ms_budget=80` but `expand_ms_spent` ~ tens of seconds, `expand_truncated` | **H11** / BUG-mem-gpu-01 — distinct from truncation of disk rows |
| Keep set = haiku tools | rational selection on truncated universe — not independent haiku filter (H8) |

### 3. Optional offline harness — `consumer_compare.py`

**No patched product `_load`.** Script materializes two row sets offline and builds ephemeral dict-backed stores:

```bash
export PYTHONPATH=.
python docs/lance-debug1/scripts/consumer_compare.py \
  --uri "$LANCE_DEBUG_URI" \
  --out "$RUN/consumer-compare.json"
```

What it does:

1. Row set **A** = bare `table.to_arrow()` (thin).
2. Row set **B** = `head(n_full)` or `to_lance().to_table()` (full; same H1b public paths).
3. Build two minimal in-memory stores implementing `get_atom` / `list_by_moment` / walks needed by `GraphView.neighbors`.
4. Run structural `GraphView.neighbors` (semantic off) over seeds from A and from B.
5. Record seed counts, kind histograms, and whether **non-haiku** atoms appear as reachable only on B.

| Result | Interpretation |
|--------|----------------|
| Full store shows many more seeds / non-haiku neighbors | H8 **disconfirmed** as primary (consumers see what store holds) |
| Thin and full identical after equalizing to same id set | consumer has no extra filter |
| Only thin hides ids that exist on disk | E secondary of B |

### 4. H12 — BUG-wake-02 adjacency

| Claim | Check |
|-------|-------|
| Wake-02 is consumer of residual glass + thin meal | wait_timeout steers into haiku from sandbox/glass residue after restart |
| Wake-02 is **not** cause of missing Lance rows | full APIs still large (H1b); disk has non-haiku corpus |

Link `docs/known-bugs.md` **BUG-wake-02**. File notes under adjacency; **do not** treat as Lance root without R1 (H1a/H1b).

## Pass / fail (hypotheses)

| Hypothesis | Pass |
|------------|------|
| **H8 disconfirm** | Full corpus (B) surfaces non-haiku; thin (A) only shows loaded subset — no independent hide filter |
| **H11** | expand_ms overruns attributed to encode latency / gpu-01, not “truncation causes 94s” as row-loss mechanism |
| **H12** | wake-02 linked as residual consumer; disk gap still H1/H2 |

## Forbidden

- Patching `_load` to “simulate full” for this procedure
- Dual live connect as default
- Elevating E as root before B comparison

See design §P06 and adjacency notes.
