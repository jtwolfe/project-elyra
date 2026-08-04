# P05 — Embed / index adjacency

| Field | Value |
|-------|--------|
| **Status** | Ready (PR4) |
| **Safety class** | **R1** / **R2** (W1 only if reusing load_parity quarantine open) |
| **Prove / disprove** | **H6** (disconfirm independent vector loss), **H11** secondary (expand_ms / GPU adj.) |
| **Bucket** | D (cascade of B unless H6 holds) |
| **Evidence** | disk ready counts + glass/process vectors health + adjacency writeup |
| **Adjacency** | [../adjacency/embed.md](../adjacency/embed.md) |

## Purpose

Show that thin `vectors_ready` / `below_ivf_min` / `no_index` are **numerically explained** by load truncation (bucket B → D), not independent embed-queue loss for atoms already in `_by_id` (**H6**). Separate **BUG-mem-gpu-01** latency (**H11**) from missing-atom root cause.

## Cascade (read this first)

```text
A bare to_arrow (~10)
  └─► B _load thin _by_id / health.atom_count
        └─► D _emb_by_id only for loaded rows
              ├─ list_ready_embeddings_for_seed ≤ process ready
              ├─ vectors_ready tiny → ANN notes below_ivf_min
              └─ upsert_vectors cannot target atoms absent from _by_id
```

Default knobs (`elyra/memory/config.py`): `ann_ivf_min_vectors=256`, `ann_full_search_below=2000`, `ann_recent_buffer_max=256`.

## Prerequisites

- [ ] P01 (disk ready / full materialization path known)
- [ ] P02 or glass R2 for process `vectors_ready`
- [ ] Read [../FAULT-BUCKETS.md](../FAULT-BUCKETS.md) bucket D
- [ ] No GPU required

## Procedure (executable)

### 1. Disk ready count (R1 full materialization)

From quarantine URI (prefer same snapshot as P01):

```bash
# Prefer counts already in api-matrix / load-parity; or ad-hoc head(n_full)
# Count rows where embedding_status == ready (or emb columns present).
python docs/investigations/lance-debug1/scripts/api_matrix.py \
  --uri "$LANCE_DEBUG_URI" \
  --out "$RUN/api-matrix.json"
```

If matrix does not include ready hist, materialize full via H1b path (`head(n_full)` / `to_lance().to_table()`) and tally:

| Field | How |
|-------|-----|
| `disk_ready` | rows with `embedding_status == "ready"` (or non-null emb joint vector) |
| `disk_atoms` | `n_full` |

Absolute N is run-specific (snapshot B ~327 ready / ~386 rows — **do not** hardcode).

### 2. Process vectors health (R2 or W1)

**R2 glass** (preferred after restart):

```bash
BASE="${ELYRA_GLASS_BASE:-http://127.0.0.1:8787}"
curl -sS "$BASE/api/memory/vectors" | tee "$RUN/glass/vectors.json"
curl -sS "$BASE/api/memory" | tee "$RUN/glass/overview.json"
```

**Or W1** from `load_parity.py` / store open on quarantine: `health()["vectors_ready"]`, index notes if exposed.

Record:

| Field | Source |
|-------|--------|
| `process.atom_count` | R2: `GET /api/memory` overview; W1: `store.health()["atom_count"]` |
| `process.vectors_ready` | R2: **`GET /api/memory/vectors`** → `index.vectors_ready` (not on overview); W1: `store.health()["vectors_ready"]` |
| `vectors_by_channel` | if present on vectors/index health |
| Index notes | `below_ivf_min`, `ann_index_built`, `no_index` on vectors payload |

### 3. Seed list vs process ready

Code truth (inspection only): `list_ready_embeddings_for_seed` scans **`_emb_by_id` ∩ ready** — process maps only ([CODE-PATH-MAP.md](../CODE-PATH-MAP.md) ~L1608).

| Check | Expected if B→D cascade |
|-------|-------------------------|
| `len(list_ready_embeddings_for_seed) ≤ process.vectors_ready` | always |
| `process.vectors_ready ≪ disk_ready` | after thin load |
| `below_ivf_min:emb_joint:N` with small N | correct given thin corpus; not independent root |

### 4. H6 disconfirm path (independent loss?)

**H6 claim:** embed queue / `upsert_vectors` independently lose vectors for atoms **present in `_by_id`** while disk emb is ready.

| Step | Action |
|------|--------|
| 1 | Take process id set S = keys of `_by_id` (from load_parity / glass atom list + known puts) |
| 2 | For each id in S with disk row `embedding_status=ready`, check process has ready emb map entry |
| 3 | **H6 supported only if** ready vectors **missing** for ids **inside** S while disk ready |
| 4 | **H6 disconfirmed** if every ready-on-disk id in S is also ready in process (gap is only ids **outside** S) |

Default expectation: **H6 disconfirmed** — missing ready is explained by atoms never loaded into `_by_id`.

### 5. H11 adjacency (expand_ms / GPU)

| Observation | Interpretation |
|-------------|----------------|
| `expand_ms_spent` ≫ budget (e.g. 80ms → tens of seconds) | **H11** / BUG-mem-gpu-01 / CPU Nemotron — **not** proof of missing Lance rows |
| `expand_truncated=true` with slow encode | performance adjacency under D |
| Thin temporal seeds | bucket E / H2 cascade — separate from ms overruns |

Do not require GPU to complete P05. Link `docs/state/known-bugs.md` **BUG-mem-gpu-01**.

## Expected

| Symptom | Primary | Do not elevate as root until… |
|---------|---------|-------------------------------|
| `vectors_ready` tiny | B→D | H6 shows missing emb **inside** `_by_id` |
| `below_ivf_min` | B→D (N &lt; 256) | independent ANN bug with large process ready |
| Encode backlog after restart for “missing” ids | atoms not in `_by_id` | queue drops for loaded ids |
| expand_ms overrun | H11 / gpu-01 | claimed as cause of disk gap |

## Pass / fail (hypotheses)

| Hypothesis | Pass |
|------------|------|
| **H6 disconfirm** | No (or negligible) ready-on-disk holes for ids in `_by_id`; gap = unloaded ids |
| **H11** | expand_ms overruns documented as encode latency adjacency, not load truncation mechanism |

## Forbidden

- Requiring GPU / rebuild ANN on live as part of inspection
- `POST /api/memory/vectors/rebuild` on live unless operator-accepted out-of-band (not needed for H6)
- Treating D symptoms as root without B comparison

See design §P05 and [../adjacency/embed.md](../adjacency/embed.md).
