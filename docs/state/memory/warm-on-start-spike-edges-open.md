# Warm-on-start P0.5 — dogfood `edges.lance` open class

| Field | Value |
|-------|--------|
| **Class** | STATE (spike results) |
| **Slice** | P0.5 `spike(memory): dogfood edges.lance open class` |
| **Date** | 2026-08-06 |
| **Inventory** | [warm-on-start-inventory.md](warm-on-start-inventory.md) |
| **Design** | [design-warm-on-start.md](../../design/design-warm-on-start.md) |
| **Helper** | [`tools/spikes/spike_edges_lance_open.py`](../../../tools/spikes/spike_edges_lance_open.py) |
| **Raw results** | `/tmp/grok-1000/spike-edges-open-results-py312.json` (operator-class) · `/tmp/grok-1000/spike-edges-open-results.json` (py3.14 segfault matrix) |

> **One line:** On **operator Python 3.12.8 + lance 0.23.2 / lancedb 0.20.0**, a **copy** of dogfood `edges.lance` **opens cleanly** (`C4_ok`) with **RAM=disk=2904, parity=true** in ~1.4 s via `open_edge_store`; fragment scale remains pathological (**C3**, ~2904 data files / 2905 versions). Restart→Graph-zero is therefore **not** explained by “table unreadable / empty disk” — pin **C1 sticky/lazy + C7 honesty** for P2; do **not** claim durable-on usefulness without reopen/parity gates.

---

## Method (safety)

| Rule | What we did |
|------|-------------|
| **Do not destroy operator data** | Never wrote into `/home/jim/Workspace/project-elyra/data/memory/` |
| **Copy-on-read** | `cp -a` → `/tmp/grok-1000/edges-spike/edges.lance` (~2.3 s); synthetic home + symlink for Elyra layout |
| **Timeout / process isolation** | Each phase in a **subprocess**; parent timeout 120–180 s; SIGSEGV recorded as `C_segfault` without killing the agent |
| **Phases** | **A** `lance.dataset` · **B** `lancedb.connect` + `open_table("edges")` · **C** `open_edge_store(..., backend=lance, fail_soft=False)` + `health()` |
| **Sanity control** | Clean temp `LanceEdgeStore` put/reopen on same interpreter |

Operator source table (read-only inventory):  
`/home/jim/Workspace/project-elyra/data/memory/lance/edges.lance`

---

## Failure / success class (pinned)

### Primary (operator runtime)

| Class | Verdict |
|-------|---------|
| **`C4_ok`** | **Primary.** Full open + materialize + health succeeds on copy under **Python 3.12.8** (operator `.venv`). |

| Metric | Value |
|--------|--------|
| `open_edge_store` wall | **~1.41 s** open / **~1.55 s** phase wall |
| `lance.dataset` open | **~0.033 s**; `count_rows=2904` |
| `lancedb.open_table` | **~0.013 s**; `count_rows=2904` |
| RAM `edge_count` | **2904** |
| `disk_edge_count` | **2904** |
| `edge_count_parity` | **true** |
| `health.ok` | **true** |
| Dataset version | **2905** |
| Fragment count | **2904** (1 fragment ≈ 1 data file ≈ 1 version step) |

**Edges by kind (RAM / health):**

| kind | count |
|------|------:|
| `created_with` | 1920 |
| `has_channel` | 468 |
| `in_moment` | 331 |
| `recalls` | 185 |
| **total** | **2904** |

### Co-classified (scale / env)

| Class | Verdict |
|-------|---------|
| **`C3_fragment_explosion`** | **Confirmed scale.** ~**328 MB** (copy du) / inventory **~348 MB**; **2904** data files under `data/`; **2905** `_versions` manifests. Per-edge `merge_insert` signature. Open still fast *today* at N≈3k — **P3 batch/compact still required** before claiming long-term open health. |
| **`C_segfault`** | **Interpreter-specific, not dogfood-table-specific.** Default `python3` → **3.14.5** + lance 0.23.2 / lancedb 0.20.0: **SIGSEGV (~3–4 s)** on phases A/B/C **and** on clean tiny lance write/open. Operator product path is **3.12.8** (`.venv`) where clean + dogfood open work. Agents/CI must pin 3.12 for Lance work. |
| **`C1_open_hang` / `C6`** | **Not observed** on py3.12 copy (no timeout at 180 s). Historical “open hang” reports may mix hang vs sticky unavailable vs wrong interpreter. |
| **`C2_materialize_fail`** | **Not observed** (materialize completed; parity true). |
| **`C3_empty_RAM_disk_mismatch`** | **Not observed** (RAM=disk=2904). |
| **`C8_parity_mismatch`** | **Not observed**. |

### Implication for “restart → Graph edge_count=0”

Disk proves **writes largely landed** and **load path can repopulate RAM with full parity** when `open_edge_store` actually runs on a healthy interpreter.

So zero-after-restart is **not** “empty Lance table” and **not** (on 3.12) “open always segfaults.” Remaining high-likelihood product classes from inventory/design:

1. **`C1`** — EdgeStore still **lazy**; first Graph peek may soft-fail → sticky **`UnavailableEdgeStore`** (`is not None` blocks retry) for process life.  
2. **`C7`** — Graph labels `edge_count==0` as **empty** even when `ok=false` / unavailable.  
3. **Supervisor/API race** — concurrent ensure without single-flight (design KD-ES-*).  
4. **`C3`** — residual risk as fragment count grows; compact/batch still P3.

**P2 remains mandatory** before any durable-on usefulness claim: reopen/parity honesty, clear Unavailable on retry, open state machine + single-flight, Graph empty vs unavailable vs parity. **Do not** treat this spike as Gate B or “eager open alone is enough.”

---

## Fragment / version scale observed

| FS metric | Copy measurement | Matches inventory? |
|-----------|------------------|--------------------|
| Table size | ~328 MB (`size_bytes` walk) / `du` 348 MB source | Yes (~348 MB) |
| `data/` files | **2904** | Yes (~2900) |
| `_versions` manifests | **2905** | Yes (~2905) |
| Lance dataset `version` | **2905** | — |
| `get_fragments()` | **2904** | — |
| Row count | **2904** | ≈ one row per fragment (pathological put shape) |

Threshold from design (warn if `data` file count **> 500**): **exceeded ~5.8×**.

---

## Dual `edges.jsonl` note

| Artifact | Path | Spike observation |
|----------|------|-------------------|
| **Live SoT (backend=lance)** | `data/memory/lance/edges.lance` | 2904 edges; process uses this only |
| **Leftover jsonl** | `data/memory/edges.jsonl` | **10** lines, all `in_moment` (~4.1 KB), dated 2026-08-06 promote_membership |

`open_edge_store` with `backend=lance` **ignores** jsonl (confirmed by code + spike: health never surfaces those 10 rows as Lance counts). **Do not auto-delete** jsonl (KD-SOT); operator may archive to reduce confusion. Dual SoT is **operator confusion (C5)**, not the cause of Lance Graph zero when Lance open never runs or soft-fails sticky.

---

## Implications for P2 (and later slices)

| Must / must not | Detail |
|-----------------|--------|
| **Must not** claim durable-on usefulness from P0.5 alone | Open works on copy ≠ restart survival in product with lazy ensure + sticky soft-fail + Graph mislabel |
| **Must not** skip P2 reopen/parity/honesty | Spike proves *when open runs*, counts can match; product must **force honest state** when open fails or is skipped |
| **Must not** land P1 eager open as the *only* fix | Eager open on 3.12 is plausible (~1.4 s for N=3k) **if** single-flight + no sticky Unavailable; still need P2 gates |
| **Must** keep P3 batch/compact on roadmap | 2904 fragments already over design warn threshold; will worsen |
| **Must** pin Lance experiments to **Python 3.12** on this host | py3.14 = universal `C_segfault` for lance today |
| **May** treat quarantine/compact-before-open as P3 fallback | Not required to *read* current dogfood copy, but still the repair path if open degrades |

### Suggested P2 lock-in from this class pin

1. **Open SM + single-flight** so Graph never races a second open; state `opening` ≠ empty.  
2. **Clear `UnavailableEdgeStore`** before retry; do not retain immortal soft-fail handle.  
3. **Hard honesty:** if disk count > 0 and RAM 0 after load → not `ok` empty; surface `disk_edge_count` / parity on Graph.  
4. **Reopen acceptance:** put → close → open → count/parity (hermetic); dogfood restart checklist uses this spike’s N≈2904 as lower bound.  
5. **Optional open timeout** still useful under concurrent load; not the primary failure mode measured here.

---

## Environment matrix (repro)

| Interpreter | lance / lancedb | Clean tiny table | Dogfood copy open |
|-------------|-----------------|------------------|-------------------|
| **3.12.8** (operator `.venv`) | 0.23.2 / 0.20.0 | **OK** (empty open ~0.36 s; reopen +1 edge parity) | **`C4_ok`** ~1.4 s, 2904/2904 parity |
| **3.14.5** (mise `python3`) | 0.23.2 / 0.20.0 | **SIGSEGV** | **`C_segfault`** ~3–4 s all phases |

Spike command (copy already under `/tmp`):

```bash
/home/jim/.local/share/mise/installs/python/3.12.8/bin/python3 \
  tools/spikes/spike_edges_lance_open.py \
  --table /tmp/grok-1000/edges-spike/edges.lance \
  --timeout-s 180 \
  --json-out /tmp/grok-1000/spike-edges-open-results-py312.json
```

---

## Honesty

- Spike used a **filesystem copy**, not a live open of the operator process’s handle; no write to operator memory.  
- Did **not** exercise concurrent Graph + presence open, sticky Unavailable retention, or full restart checklist (those are **P2**).  
- Did **not** compact or mutate the dogfood table.  
- Prior local “segfault/hang on open” reports remain credible for **wrong interpreter** or **future fragment growth**; they do **not** overturn **C4_ok** on the operator 3.12 path for this snapshot.
