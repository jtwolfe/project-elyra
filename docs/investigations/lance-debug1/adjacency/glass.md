# Adjacency — Glass serialization + curl notes (bucket F)

| Field | Value |
|-------|--------|
| **Bucket** | F |
| **Procedures** | [P03](../procedures/P03-inprocess-vs-oop.md), [P07](../procedures/P07-glass-serialization.md) |
| **Hypotheses** | **H7** (disconfirm further truncation beyond store) |
| **Safety** | **R2** (read-only HTTP) |

## Cascade

Glass `_get_memory_*` calls `worker._ensure_memory_store()` then inspect helpers — **process truth only**. It does not re-scan Lance with full-table APIs.

```text
_load thin _by_id
  → health.atom_count = len(_by_id)
  → GET /api/memory reports that count
  → list_atoms_for_glass newest-first among process maps (cap 200)
```

## H7 disconfirm

| Check | Expected |
|-------|----------|
| glass `atom_count` | == process `health.atom_count` |
| hard cap 200 | cannot explain counts ~10–13 |
| glass ≪ `n_full` | because process is thin (B), not glass drop |

## Split: to_arrow prefix vs glass newest-first

| | bare `to_arrow` (H1a) | glass Atoms list |
|--|----------------------|------------------|
| **Order** | table physical prefix (`head(10)`) | newest-first weave / `t_start` among `_by_id` |
| **Kinds (snapshot B)** | summary×6 + tool×4 | often haiku **tools** if those are newest survivors |
| **Must match?** | **No** — different orderings over related thin sets |

Misreading glass haiku list as “`to_arrow` selects haiku” confuses F/E consumer order with A limit semantics.

## Curl notes (R2)

Default base: `http://127.0.0.1:8787` (override with `ELYRA_GLASS_BASE`).

```bash
BASE="${ELYRA_GLASS_BASE:-http://127.0.0.1:8787}"
RUN=docs/investigations/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01/glass
mkdir -p "$RUN"

# Overview — flags + store health atom_count (process only).
# Note: worker memory status block does NOT include vectors_ready.
curl -sS "$BASE/api/memory" | tee "$RUN/overview.json"

# Context meal inspector
curl -sS "$BASE/api/memory/context" | tee "$RUN/context.json"

# Atoms timeline (newest-first among process; hard cap 200)
curl -sS "$BASE/api/memory/atoms?limit=200" | tee "$RUN/atoms.json"
curl -sS "$BASE/api/memory/atoms?limit=50&kind=tool" | tee "$RUN/atoms-tool.json"

# Single atom
# curl -sS "$BASE/api/memory/atoms/ATOM_ID" | tee "$RUN/atom-detail.json"

# Vectors health — process vectors_ready is here (index.vectors_ready), not overview
curl -sS "$BASE/api/memory/vectors" | tee "$RUN/vectors.json"
curl -sS "$BASE/api/memory/vectors/atoms?limit=200" | tee "$RUN/vectors-atoms.json"
# curl -sS "$BASE/api/memory/vectors/neighbors?atom_id=ATOM_ID&k=12" | tee "$RUN/vectors-neighbors.json"

# Graph tab
curl -sS "$BASE/api/memory/graph" | tee "$RUN/graph.json"
curl -sS "$BASE/api/memory/graph/session" | tee "$RUN/graph-session.json"
# curl -sS "$BASE/api/memory/graph/neighbors?atom_id=ATOM_ID" | tee "$RUN/graph-neighbors.json"
```

### Timing

Prefer capture **just after restart**, before heavy promote — aligns glass thin counts with bare `to_arrow` order of magnitude. After many puts in-process, glass grows while disk was already large.

### Auth / port

If glass is bound elsewhere or behind auth, set `ELYRA_GLASS_BASE` and any cookies/headers your deployment requires. This package does not ship credentials.

### Avoid for inspection default

| Endpoint | Why |
|----------|-----|
| `POST /api/memory/vectors/rebuild` | mutates index; not needed for H7 |
| `POST /api/memory/graph/traverse` | optional operator debug; can spend encode budget |

## Code pins (inspection)

| Symbol | File |
|--------|------|
| routes | `elyra/runtime/api.py` L297+ / `_get_memory_*` |
| list + cap | `elyra/memory/inspect.py` `_ATOM_LIST_HARD_CAP=200`, `list_atoms_for_glass` |
| health | `elyra/memory/lance_store.py` `health()` `atom_count=len(_by_id)` |

## Non-goals

- Glass UI redesign
- Dual-count health in product (fix follow-on only)
- Dual live `lancedb.connect` (use P03 preferred path)
