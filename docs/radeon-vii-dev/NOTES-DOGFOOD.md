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

### Dogfood — venv ROCm smoke (2026-07-29)

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

- Full A5 error text: [§ A5 hard stop](#a5-hard-stop--exact-error) above; freezes/`post-rocm-host-stack.txt`
- Bug entry: [docs/known-bugs.md](../known-bugs.md) **BUG-mem-gpu-01**
- Inventory: [STACK-INVENTORY.md](STACK-INVENTORY.md)

---

*PR4 ops record: A6–A7 blocked on A5 red; dogfood template filled; Gate B unchecked; bug stays Open; product worker not exercised.*
