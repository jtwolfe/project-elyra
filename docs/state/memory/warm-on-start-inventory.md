# Warm-on-start — STATE inventory (P0)

| Field | Value |
|-------|--------|
| **Class** | STATE |
| **Audience** | Implementers + operators |
| **Status** | Active (pre-behavior inventory for warm-on-start) |
| **Normative?** | No — points at design; code truth is tip |
| **Date** | 2026-08-06 |
| **Design (normative)** | [design-warm-on-start.md](../../design/design-warm-on-start.md) (**Draft R2**) |
| **Related dogfood** | [edges-traversal-dogfood.md](edges-traversal-dogfood.md) |
| **P0.5 spike results** | [warm-on-start-spike-edges-open.md](warm-on-start-spike-edges-open.md) — **C4_ok** on py3.12 copy (2904/2904 parity ~1.4 s) + **C3** fragments; **C_segfault** on py3.14 only |
| **Program** | [README.md](README.md) |
| **Implementation branch** | `fix/warm-on-start` (from `fix/general-touchup1`; restack onto `working` if touchup1 merges first) |

> **One line:** Large durable `edges.lance` on disk while Graph shows zero after restart is an **open/load/honesty** problem first — not “writes never landed.” This note inventories today’s lazy fabric so P0.5–P5 know what to fix.

**No behavior change in this slice (P0).** Design already in tree; this is the living STATE companion.

---

## Lazy inventory (current code)

Pinned from design § “Code-backed lazy inventory” and implementation anchors. Confirm against tip before coding.

| Component | Today | Why it matters for warm-on-start |
|-----------|--------|----------------------------------|
| **Atoms Lance** | Eager on worker start (`_ensure_memory_store` → `lance load complete atoms=N`) | Working pattern; warm core should mirror this for edges |
| **EmbeddingIndex** | Opened inside store ensure | Already on start path with atoms |
| **Embedder** | **Lazy.** Product default **`embed_preload=false`**. Cold load only via `role=loader` (encode worker / preload path). Consumers never cold-load | ~70 s Nemotron warm often **after** store open; first semantic hop late |
| **EdgeStore** | **Lazy** on first Graph / backfill / promote / encode drain | Restart → first Graph peek may open under timeout or soft-fail |
| **Sticky soft-fail** | `open_edge_store(fail_soft=True)` → **`UnavailableEdgeStore`** retained in `self._edge_store` (`is not None` ⇒ no retry) | Process-life dead fabric; random `edge_store_unavailable` |
| **Health / Graph counts** | `edge_count` = **RAM** `len(_by_id)`; optional `disk_edge_count` / `edge_count_parity` exist but Graph does not surface them well | Disk-full + RAM-zero looks like “empty” |
| **Graph empty label** | `edge_store_empty = edge_count == 0` **regardless of `edge_ok`** | Unavailable mislabeled “EdgeStore empty” → steers to force backfill |
| **Lance put shape** | Per-edge `merge_insert` | Dogfood fragment explosion (~thousands of data files / versions) — open hang risk (P3 batch/compact) |
| **Dual SoT** | `backend=lance` **ignores** `edges.jsonl`; live SoT is `data/memory/lance/edges.lance` only | Operator confusion; do not auto-delete leftover jsonl |

**Non-required warm (out of scope for `memory_ready`):** Playwright, full MSB image pull, Grok Build seed — stay async / non-blocking (KD23 pattern).

---

## Dogfood symptoms (operator host)

Observed product failure mode that motivates the design (not a signed checklist):

| Symptom | Notes |
|---------|--------|
| **`edges.lance` large on disk** | ~348 MB; ~2900 data files / ~2905 versions — **writes largely landed** |
| **Graph after restart** | `durable_edges_enabled=on`, `backend=lance`, **EdgeStore empty**, **edge_count=0**, edges by kind none |
| **Force backfill** | Can repopulate RAM for a session; **next restart zeros again** or sticky unavailable |
| **Dual edges files** | `data/memory/edges.jsonl` (dogfood: ~10 `in_moment` lines) **vs** large `edges.lance`; Lance process does not read jsonl |
| **Embedder late** | Nemotron often warms ~70 s after store open on first encode-worker tick (`embed_preload=false`) |
| **Open fragility** | Live table has hung/segfaulted on open in local repros; Graph health peek can timeout |

Full edges/polish1 dogfood boxes remain under [edges-traversal-dogfood.md](edges-traversal-dogfood.md). Warm-on-start does **not** claim Gate B from this inventory alone.

**P0.5 open-class pin (2026-08-06):** copy-on-read of operator `edges.lance` under **Python 3.12.8** → **`C4_ok`** (`open_edge_store` ~1.4 s, RAM=disk=**2904**, parity true, kinds: created_with 1920 / has_channel 468 / in_moment 331 / recalls 185) with co-class **`C3_fragment_explosion`** (2904 data files / 2905 versions). Default **py3.14** lance is universal **`C_segfault`** (not table-specific). Restart→Graph-zero therefore leans **C1 sticky/lazy + C7 honesty**, not empty disk — details: [warm-on-start-spike-edges-open.md](warm-on-start-spike-edges-open.md).

---

## Root-cause lean (pinned)

**Primary lean for “restart → Graph zero”:** open / load / index / honesty — **not** non-durable writes.

| Class | Likelihood for zero-after-restart | Notes |
|-------|-----------------------------------|--------|
| **C1** sticky Unavailable / never eager open | **High** | Soft-fail handle retained; Graph peeks zero |
| **C6** open hang / timeout | **High** on fragment-heavy table | Matches open fragility |
| **C7** Graph empty mislabel | **High** (UX path) | `ok=false` labeled “empty” |
| **C8** partial load / soft parity | **Medium** | Silent parse skips; soft ok + parity false |
| **C3** fragment explosion | **High for hang**; secondary for zero if open eventually “succeeds” empty | P3 batch/compact; may need compact-before-open in spike |
| **C2** writes never land | **Low** as primary | Disk size / version count contradict pure non-durable writes |
| **C4** embedder late | Confirmed for encode path | Separate from edge zero counts |
| **C5** dual SoT | Operator confusion | Not why Lance Graph is zero |

**Implication:** P2 (reopen/parity/Graph honesty/open state machine/single-flight) is **not** cosmetics. Do not claim durable-on usefulness until P2 acceptance on non-trivial N + restart checklist. **P0.5** classifies the dogfood open failure mode before pinning eager open on the live pathological table.

Normative decisions: design **KD-ROOT**, **KD-PERSIST**, **KD-ES-***, **KD-SOT**, **KD-EP**, **KD-WARM-UX**. Full tables in design.

---

## Design link

| Doc | Role |
|-----|------|
| [design-warm-on-start.md](../../design/design-warm-on-start.md) | **Normative design** — eager fabric, component readiness, single SoT, PR plan, key decisions |
| This file | **STATE inventory** — lazy today, dogfood symptoms, root-cause lean, slice order |

Charter (from design): *On process start, the memory fabric that makes Elyra useful — Lance atoms, durable EdgeStore, embedding index, and Nemotron embedder — is open and honest before chat tools pretend the graph is alive; edges written once survive every restart without daily force-backfill.*

---

## PR / slice order

**Reordered so P2 behavior lands before P1 eager open of the live table.**

```text
P0    docs(memory): warm-on-start design + inventory     ← this slice (no behavior)
P0.5  spike(memory): dogfood edges.lance open class      ← pin C1/C6/… class
P2    fix(memory): edge reopen/parity/honesty + open SM + single-flight
P1    fix(memory): eager EdgeStore + embed_preload warm path
P3    fix(memory): edge batch upsert + compact fallbacks
P4    feat(memory): memory_ready aggregate + CLI/Glass badge polish
P5    chore(memory): tray optional warm + recalls notes
```

| Slice | One-line scope | Claim bar |
|-------|----------------|-----------|
| **P0** | Design + this inventory | Docs only |
| **P0.5** | Open copy of operator table; time/counts/parity/failure class | **Done** — [spike note](warm-on-start-spike-edges-open.md): **C4_ok**+**C3** on py3.12; P2 still required |
| **P2** | Reopen tests; hard-fail RAM=0/disk>0; Graph empty vs unavailable vs parity; open SM clears Unavailable; single-flight; optional timeout | **First behavior**; no durable-on usefulness claim until green + restart checklist |
| **P1** | `_warm_memory_core`; side-thread sole embedder loader; claim after core; `embed_preload=true` | Hermetic warm/claim tests; dogfood pin only after P2 (and P3 if needed) |
| **P3** | Batch upsert (merge-blocking); compact best-effort + quarantine fallbacks | Batch tests; compact smoke or documented unsupported |
| **P4** | Aggregate `memory_ready` + CLI/Glass badge polish | Status contract; `chat_ready` independent |
| **P5** | Optional tray warm; recalls notes | Soft fail tray |

### Out-of-order rules (do not violate)

- **Do not** land P1 eager open on live pathological `edges.lance` without P2 green and either compact/quarantine or proven open time.
- **Do not** claim Gate B / durable-on usefulness after eager-only.
- Develop P1 against clean temp dirs in hermetic tests regardless.
- `durable_edges_enabled` product default-on is already on touchup1 tip — warm-on-start makes **restart survival** real; do not re-litigate Gate B flag default in this stack.

---

## Quick code anchors

| Area | Path |
|------|------|
| Worker ensure / sticky | `elyra/presence/worker.py` — `_ensure_edge_store`, `_ensure_memory_store`, `_ensure_embedder`, `run` |
| Edge store | `elyra/memory/edges.py` — `LanceEdgeStore`, materialize/parity, `health`, `open_edge_store(fail_soft=True)`, `UnavailableEdgeStore` |
| Graph mislabel | `elyra/runtime/api.py` `_get_memory_graph` |
| Config | `elyra/memory/config.py` `embed_preload=False` today |
| Supervisor race | `elyra/runtime/supervisor.py` (API concurrent with presence) |
| Dogfood paths | `data/memory/lance/edges.lance`; leftover `data/memory/edges.jsonl` |

---

## Honesty

- This inventory is **not** architecture manual “shipped” status.
- Execute-plan P0 complete ≠ fabric warm ≠ Gate B.
- Prefer [design-warm-on-start.md](../../design/design-warm-on-start.md) for normative KDs and acceptance; update this note when P0.5 pins a class or when behavior slices land.
