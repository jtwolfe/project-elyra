# NOTES — ROCm venv switch dogfood (LuxPrimata)

**Date:** 2026-07-29  
**Host:** LuxPrimata  
**Operator action:** PR3 — project `.venv` CUDA → ROCm 7.2 torch switch  
**Related:** [VENV-ROCM-SWITCH.md](VENV-ROCM-SWITCH.md), [STACK-INVENTORY.md](STACK-INVENTORY.md), freezes/  
**Bug:** BUG-mem-gpu-01 remains **Open** (not closed by this note)

---

## Outcome (summary)

| Gate | Result |
|------|--------|
| Arming (presence stopped; local `embed_device=cpu`) | OK — no `elyra start` process; pin applied **local/uncommitted** on main-project `elyra.toml` only |
| Pre freezes | Captured under `freezes/pre-rocm-*` |
| Uninstall cu130 + nvidia residual purge | OK |
| Install `torch==2.13.0` / `torchvision==0.28.0` from `https://download.pytorch.org/whl/rocm7.2` | OK |
| A1–A4 (`01_device_probe.py` / HIP + probe) | **PASS** (exit 0) |
| **A5** (`02_matmul_smoke.py` / fp16 matmul) | **FAIL — ISA / rocBLAS gfx906 missing** (hard stop) |
| A6–A7 / `03_nemotron_encode.py` | **NOT RUN** (A5 hard stop) |
| Hermetic post-swap | `pytest tests/test_memory_embed_*.py -q -m "not gpu"` → **84 passed, 1 skipped, 1 deselected** |
| Product pin committed? | **No** — `elyra.toml` pin remains local only |
| Venv left as | **ROCm** (`2.13.0+rocm7.2`) — install complete; matmul unsupported for gfx906 |

---

## Versions after switch

```
torch              2.13.0+rocm7.2
torchvision        0.28.0+rocm7.2
triton-rocm        3.7.1
torch.version.hip  7.2.53211
torch.version.cuda None
cuda.is_available  True
get_device_name(0) AMD Radeon Graphics   # torch marketing string; rocminfo = AMD Radeon VII / gfx906
```

Host ROCm remains **7.2.4** Tier A (unchanged). No Tier B installed (ISA miss — Tier B forbidden as cargo-cult).

---

## A1–A4 evidence

`01_device_probe.py` (scripts from PR2 worktree; `PYTHONPATH` = main project root):

```
version='2.13.0+rocm7.2'
hip='7.2.53211'
cuda_is_available=True
device_name_0='AMD Radeon Graphics'
probe_devices={'torch_available': True, 'cuda': False, 'rocm': True, 'cpu': True, 'error': None}
select_device("rocm")='rocm'
select_device("auto")='rocm'
PASS: HIP present; probe_devices rocm=True; select_device(rocm)=rocm
exit=0
```

Residual `nvidia-*` / `cuda-*` pip packages: **none** after purge + ROCm install.

---

## A5 hard stop — exact error

`02_matmul_smoke.py` and equivalent inline `x @ x` on `cuda:0` **aborted** (exit 134 / SIGABRT) with:

```
rocBLAS error: Cannot read .../torch/lib/rocblas/library/TensileLibrary.dat:
  Illegal seek / No such file or directory for GPU arch : gfx906
 List of available TensileLibrary Files :
  TensileLibrary_lazy_gfx1030.dat
  TensileLibrary_lazy_gfx1100.dat
  TensileLibrary_lazy_gfx1101.dat
  TensileLibrary_lazy_gfx1102.dat
  TensileLibrary_lazy_gfx1150.dat
  TensileLibrary_lazy_gfx1151.dat
  TensileLibrary_lazy_gfx1200.dat
  TensileLibrary_lazy_gfx1201.dat
  TensileLibrary_lazy_gfx908.dat
  TensileLibrary_lazy_gfx90a.dat
  TensileLibrary_lazy_gfx942.dat
  TensileLibrary_lazy_gfx950.dat
```

**Interpretation:** Official `torch 2.13.0+rocm7.2` wheel ships rocBLAS Tensile kernels for **gfx908+ and RDNA/CDNA modern arches**, but **not gfx906** (Vega 20 / Radeon VII). HIP device enumeration works; **compute matmul does not**.

### What was NOT done (normative hard stop)

- Did **not** run `03_nemotron_encode.py` / model load  
- Did **not** install Tier B (`rocblas` / MIOpen system packages) as an ISA workaround  
- Did **not** set `HSA_OVERRIDE_GFX_VERSION`  
- Did **not** close BUG-mem-gpu-01  
- Did **not** claim “GPU embed fixed”

### Optional next experiments (deferred; document only)

1. Try older wheel indexes (`rocm7.1` / `rocm7.0` / `rocm6.x`) **only after** this 7.2 failure is recorded — may still lack gfx906.  
2. Source-build torch/rocBLAS with gfx906 Tensile (high cost; deferred).  
3. Keep product pin `embed_device=cpu` for dogfood safety while ROCm torch remains installed for further ISA experiments.

---

## Product arming note

- Before uninstall: main-project `elyra.toml` set `embed_device = "cpu"` (was `"auto"`).  
- Pin is **local / uncommitted** on `/home/jim/Workspace/project-elyra/elyra.toml` — **not** part of this docs commit.  
- Operator must restore or deliberately dogfood `auto`/`rocm` later; with ROCm torch present, `select_device("auto")` → `rocm` at library level, so an unpinned presence worker would attempt GPU Nemotron load and hit the same ISA cliff.

---

## Freezes committed

| Artifact | Role |
|----------|------|
| `freezes/pre-rocm-pip-freeze.txt` | cu130 baseline |
| `freezes/pre-rocm-gpu-stack.txt` | nvidia/cuda/torch pre listing |
| `freezes/pre-rocm-host-stack.txt` | pacman ROCm + rocminfo + pre torch |
| `freezes/mid-swap-gpu-stack.txt` | empty after purge |
| `freezes/post-rocm-pip-freeze.txt` | ROCm venv freeze |
| `freezes/post-rocm-gpu-stack.txt` | torch/torchvision/triton-rocm only |
| `freezes/post-rocm-host-stack.txt` | post probe + Tensile arch list + A5 fail |
| `freezes/torchn-rocm7.2-pins.txt` | install pins used |

**Reminder:** freezes do **not** restore `+cu130` / `+rocm7.2` local labels; exclusive index reinstall for rollback (see freezes/README.md).

---

## BUG-mem-gpu-01 evidence fields (partial)

| Field | Value |
|-------|--------|
| Host | LuxPrimata |
| GPU | AMD Radeon VII (gfx906) |
| Host ROCm | 7.2.4 Tier A |
| torch | 2.13.0+rocm7.2 |
| hip | 7.2.53211 |
| is_available | True |
| A5 | **FAIL** — rocBLAS no gfx906 TensileLibrary |
| Model encode | not attempted |
| Bug status | remains **Open** |

---

*Operator dogfood record for PR3. A5 hard stop honored; no 03.*

---

## PR4 — A6–A7 blocked; dogfood template completeness (2026-07-29)

**Scope of this addendum:** document A6–A7 hard stop, fill the required BUG-mem-gpu-01 dogfood template fields, and record Gate B / product decisions. **No re-run of the venv switch. No run of `03_nemotron_encode.py`. No product worker dogfood.**

### A6–A7 — NOT RUN (A5 hard stop)

| Gate | Script | Result |
|------|--------|--------|
| **A6** | model load (`03_nemotron_encode.py` phase) | **NOT RUN** |
| **A7** | encode + G1–G9 asserts | **NOT RUN** |

**Reason:** A5 (`02_matmul_smoke.py` / fp16 matmul) **FAIL** — rocBLAS TensileLibrary has **no gfx906**. Design KD17 / VENV-ROCM-SWITCH §A5 hard process gate: **do not load Nemotron / do not run 03** until matmul is green.

- No model weights loaded
- No `load_ms` / `encode_ms` / `vram_peak_bytes` measurements
- No attn_impl observation
- Encode dogfood **re-opens only after A5 is green** (alternate wheel indexes and/or source Tensile with gfx906 — see “Optional next experiments” above)

### Required dogfood template (mirrored → `docs/known-bugs.md`)

#### Dogfood — venv ROCm smoke (2026-07-29)

| Field | Value |
|-------|--------|
| Status of BUG-mem-gpu-01 | **Still Open** (script path only / product worker: not exercised) |
| torch_version | 2.13.0+rocm7.2 |
| hip_version | 7.2.53211 |
| device_name | AMD Radeon VII (gfx906); torch name may say "AMD Radeon Graphics" |
| A1–A7 pass/fail | A1–A4 PASS; A5 FAIL (rocBLAS no gfx906 Tensile); A6–A7 NOT RUN (A5 hard stop) |
| load_ms | n/a (model not loaded) |
| encode_ms | n/a |
| vram_peak_bytes | n/a |
| attn_impl if known | n/a |
| product worker path | **not exercised** |
| Notes | Official rocm7.2 wheel enumerates HIP device but matmul aborts; no "GPU embed fixed"; standalone smoke only under docs/radeon-vii-dev |

### Spike Gate B checkbox

| Checkbox | State |
|----------|--------|
| “ROCm attempt succeeded” / smoke encode green | **Unchecked** — smoke did **not** pass encode (A5 fail → A6–A7 not run) |
| Gate B ready for product semantic default-on | **No** — remains blocked on real GPU encode path |

Do **not** check Gate B from this session. HIP probe green alone is insufficient; encode smoke did not complete.

### Decision — product pin vs intentional GPU dogfood

| Decision | Value |
|----------|--------|
| Product pin | Keep local uncommitted `embed_device=cpu` while ROCm torch remains in `.venv` for ISA experiments |
| Intentional product worker GPU dogfood this session | **No** |
| Product worker path | **not exercised** |
| `elyra.toml` committed? | **No** |
| Language | No “GPU embed fixed”; BUG-mem-gpu-01 stays **Open** |

### Re-open condition for encode dogfood

1. A5 green on LuxPrimata (gfx906 matmul works under some torch/rocBLAS build).
2. Then run `03_nemotron_encode.py` only (A6–A7 / G1–G9).
3. Fill `load_ms` / `encode_ms` / `vram_peak_bytes` / `attn_impl` from real 03 output.
4. Still keep BUG-mem-gpu-01 **Open** until a deliberate product-path decision — scripts-only success does not close the bug alone.

### Cross-links

- Full A5 error text: [§ A5 hard stop](#a5-hard-stop--exact-error) above; `freezes/post-rocm-host-stack.txt`
- Bug entry: [docs/known-bugs.md](../known-bugs.md) **BUG-mem-gpu-01**
- Inventory: [STACK-INVENTORY.md](STACK-INVENTORY.md)

---

*PR4 ops record: A6–A7 blocked on A5 red; dogfood template filled; Gate B unchecked; bug stays Open; product worker not exercised.*

---

## PR5 follow-on — gfx906 Tensile inject → A5/A7 green (2026-07-29, same host)

**Problem:** Official `torch 2.13.0+rocm7.2` wheel HIP-enumerates Radeon VII but **ships no** `TensileLibrary_lazy_gfx906.dat` in bundled rocBLAS (only gfx908/90a/942/950 + RDNA).

**Fix (operator, not a product code change):** inject gfx906 Tensile assets from **Arch `rocblas` 7.2.4-2** into the venv torch library path:

```text
.venv/lib/python3.12/site-packages/torch/lib/rocblas/library/*gfx906*
```

Reproducible helper (re-run after any torch reinstall):

```bash
python docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py
```

**Script fix:** `reset_peak_memory` must call `torch.cuda.init()` first on ROCm (otherwise `reset_peak_memory_stats(0)` → `Invalid device argument` before any alloc).

### Outcome after inject

| Gate | Result |
|------|--------|
| A1–A4 `01` | **PASS** |
| A5 `02` matmul | **PASS** (~75 ms fp16 256×256 on `cuda:0`) |
| A6–A7 `03` encode | **PASS** — G1–G9 |
| Product `NemotronEmbedder(device="rocm")` | **PASS** — params on `cuda:0` |

### 03_nemotron_encode measured (LuxPrimata)

```text
model_id=nvidia/omni-embed-nemotron-3b
param_device=cuda:0
dtype=float16
load_s≈18.5
encode_a_s≈2.2 (first) / encode_b_s≈0.10
dim=2048
l2_norm≈1.0
cosine_a_b≈0.80
vram_peak_bytes≈9580803584 (~8.9–9.0 GiB)
hip=7.2.53211
exit=0
```

### Dogfood — venv ROCm smoke after Tensile inject (2026-07-29)

| Field | Value |
|-------|--------|
| Status of BUG-mem-gpu-01 | **Still Open** (standalone script path green; **product worker / meal path not yet dogfooded**) |
| torch_version | 2.13.0+rocm7.2 |
| hip_version | 7.2.53211 |
| device_name | AMD Radeon VII (gfx906); torch `"AMD Radeon Graphics"` |
| A1–A7 pass/fail | **A1–A7 all PASS** (after `00` Tensile inject) |
| load_ms | ~18500 |
| encode_ms | ~2200 first text; ~100 subsequent |
| vram_peak_bytes | ~9580803584 |
| attn_impl if known | product tries flash_attention_2 → sdpa/eager fallback (not logged as string this run) |
| product worker path | later same day: local `embed_device=rocm` (was `cpu`); GPU **load** seen; **in-moment encode not confirmed** |
| Notes | Real GPU load+encode demonstrated via `03` + direct `NemotronEmbedder(device="rocm")`. Do **not** claim product GPU embed default-on fixed. Re-inject Tensile after torch reinstall. |

### Gate B

| Checkbox | State |
|----------|--------|
| Standalone ROCm encode smoke (G1–G9) | **Checked** (scripts path) |
| Product worker GPU load | **Partial** — `embed_device=rocm` local; model loads on GPU |
| Product in-moment encode | **Unchecked / suspect** — operator not sure encode runs during a moment |
| Product semantic default-on | **No** — still Gate B / product decision |

### How to re-run

```bash
cd /home/jim/Workspace/project-elyra
source .venv/bin/activate
export PYTHONPATH=. ROCM_PATH=/opt/rocm
python docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py   # no-op if already present
python docs/radeon-vii-dev/scripts/01_device_probe.py
python docs/radeon-vii-dev/scripts/02_matmul_smoke.py
python docs/radeon-vii-dev/scripts/03_nemotron_encode.py
```

Product path (local only): `embed_device = "rocm"` in `elyra.toml` (uncommitted) → `elyra start` → confirm health device. Keep BUG open until moment-path dogfood is done.

---

## Product follow-on — moment encode path (open, dig later)

**Date:** 2026-07-29 (after PR5 inject + pin flip)  
**Status:** **Open observation** — not a sealed root-cause; filed under **BUG-mem-gpu-01** adjacency / Gate B product dogfood.

### Observation

1. Standalone GPU encode is **proven** (`03`, params on `cuda:0`).
2. Local product pin set to `embed_device = "rocm"` (was `cpu`); presence appears to **load** Nemotron on GPU.
3. Operator is **unsure whether embeddings actually run during a moment** (encode queue / idle worker / meal or atom write path vs load-at-start only).

Possible explanations (unranked, not yet tested):

| Hypothesis | Notes |
|------------|--------|
| H-m1 | Load-only: ensure_loaded on start; encode queue never drains during moment |
| H-m2 | Encode deferred to idle / refresh_due; moment ends before work runs |
| H-m3 | Atoms written without enqueue (flags / backend / semantic off for that write) |
| H-m4 | Encode runs but is silent in UI; vectors_ready / logs not checked |
| H-m5 | Device pin works for load; path still falls back or skips under meal budgets |

### Dogfood procedure (when ready)

1. Keep Tensile inject present; `embed_device=rocm`; restart presence.
2. Run a short social/work moment that should write memory atoms (or force a known write path).
3. Capture: presence logs (embed ensure, queue enqueue/complete, device), moment id, atom ids.
4. Check Memory health / vectors: new embeddings present? `vectors_ready`? dim 2048?
5. Optional: `rocm-smi` / VRAM during moment to see encode spikes vs load-only.
6. Record pass/fail + evidence in this NOTES section and update BUG-mem-gpu-01 dogfood fields.

### Explicit non-claims

- Do **not** claim “GPU embed fixed” for product meals until in-moment encode is confirmed.
- Do **not** open a separate bug id until root is distinguished from timing/UX gap.
- BUG-mem-gpu-01 remains **Open**.

---

*PR5 ops: Tensile inject unlocked A5/A7; real Nemotron on cuda:0 demonstrated. Product pin later set to rocm; in-moment encode still open. Bug remains Open.*
