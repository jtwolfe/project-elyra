# Radeon VII / ROCm stack inventory (dev)

**Branch:** `execute-plan/859daddb-pr-3-operator-rocm-venv-switch-freezes`  
**Recorded:** 2026-07-29T11:24:25Z (baseline) · **updated:** 2026-07-29T12:09:00Z (post ROCm switch)  
**Host:** LuxPrimata  
**Purpose:** Hardware + package + venv baseline for an isolated Radeon VII embed shim.  
**Policy:** Python ML libs stay in the **project venv** (not system `python-pytorch-rocm`). Host ROCm is system packages only.

> **PR3 status (2026-07-29):** Project `.venv` switched to `torch==2.13.0+rocm7.2`. **A1–A4 green; A5 HARD FAIL** — official rocm7.2 wheel rocBLAS has **no gfx906** TensileLibrary (available: gfx908/90a/942/950 + RDNA3/4). See [NOTES-DOGFOOD.md](NOTES-DOGFOOD.md). `03` not run. BUG-mem-gpu-01 remains **Open**.

---

## 1. Hardware

| Item | Value |
|------|--------|
| GPU | AMD Radeon VII (Vega 20) |
| PCI ID | `1002:66af` |
| ISA | **gfx906** (`amdgcn-amd-amdhsa--gfx906:sramecc+:xnack-`) |
| VRAM | 15.98 GiB (17163091968 bytes) |
| PCIe | 16× @ 8.0 GT/s |
| CPU | AMD Ryzen 5 1600X (12 threads) |
| Kernel | 7.1.4-arch1-1 |
| Driver | amdgpu (in-tree) |
| Devices | `/dev/kfd`, `/dev/dri/renderD128`, `/dev/dri/card1` |

### rocminfo (GPU agent excerpt)

```
Agent 2                  
*******                  
  Name:                    gfx906                             
  Uuid:                    GPU-2284206172df8893               
  Marketing Name:          AMD Radeon VII                     
  Vendor Name:             AMD                                
  Feature:                 KERNEL_DISPATCH                    
  Profile:                 BASE_PROFILE                       
  Float Round Mode:        NEAR                               
  Max Queue Number:        128(0x80)                          
  Queue Min Size:          64(0x40)                           
  Queue Max Size:          131072(0x20000)                    
  Queue Type:              MULTI                              
  Node:                    1                                  
  Device Type:             GPU                                
  Cache Info:              
    L1:                      16(0x10) KB                        
    L2:                      8192(0x2000) KB                    
  Chip ID:                 26287(0x66af)                      
  ASIC Revision:           1(0x1)                             
  Cacheline Size:          64(0x40)                           
  Max Clock Freq. (MHz):   1801                               
  BDFID:                   3584                               
  Internal Node ID:        1                                  
  Compute Unit:            60                                 
  SIMDs per CU:            4                                  
  Shader Engines:          4                                  
  Shader Arrs. per Eng.:   1                                  
  WatchPts on Addr. Ranges:4                                  
  Coherent Host Access:    FALSE                              
  Memory Properties:       
  Features:                KERNEL_DISPATCH 
  Fast F16 Operation:      TRUE                               
  Wavefront Size:          64(0x40)                           
  Workgroup Max Size:      1024(0x400)                        
  Workgroup Max Size per Dimension:
    x                        1024(0x400)                        
    y                        1024(0x400)                        
    z                        1024(0x400)                        
```

### Access / groups

| Check | Status |
|-------|--------|
| User in `render` / `video` | **No** (still only jim, ollama, docker, input, wheel) |
| `/dev/kfd` mode | world RW (works without group today) |
| `/dev/dri/renderD128` | render group RW |
| Recommended | `sudo usermod -aG render,video $USER` then re-login |

---

## 2. Host ROCm (package manager)

| Field | Value |
|-------|--------|
| Install root | `/opt/rocm` (`ROCM_PATH` set) |
| Reported version | **7.2.4** |
| HIP (hipconfig) | 7.2.53211-9999 |
| HSA runtime | 1.18 (rocminfo) |
| rocm-smi-lib | package 7.2.0-2 / tool reports ROCM-SMI-LIB 7.8.0 |
| opencl-amd AUR | **not** installed (good — no dual stack) |

### Installed packages (snapshot)

```
comgr 2:7.2.4-1
hip-runtime-amd 7.2.4-1
hsa-rocr 7.2.4-1
rocm-cmake 7.2.4-1
rocm-core 7.2.4-1
rocm-device-libs 2:7.2.4-1
rocm-hip-runtime 7.2.4-1
rocm-language-runtime 7.2.4-1
rocm-llvm 2:7.2.4-1
rocm-smi-lib 7.2.0-2
rocminfo 7.2.4-1
rocprofiler-register 7.2.4-1
```

### Completeness

| Tier | Packages | Status |
|------|----------|--------|
| **A — HIP runtime (minimum)** | rocm-core, hip-runtime-amd, hsa-rocr, rocm-device-libs, rocminfo, rocm-hip-runtime, rocm-language-runtime, rocm-llvm, comgr, rocm-smi-lib | **Present** |
| **B — BLAS / MIOpen (often needed by full torch builds)** | rocblas, hipblas, hipblaslt, miopen-hip, rocm-hip-sdk | **Missing** |
| **C — Distro PyTorch** | python-pytorch-rocm | **Not installed** (by design if libs stay in venv) |

**Host verdict:** Tier A is enough for HIP runtime + `rocminfo`/`hipcc`/`rocm-smi`. Tier B is **not** required until a venv torch build expects system rocBLAS/MIOpen; **PyTorch official ROCm wheels usually bundle or ship their own lib set** and primarily need a working HIP runtime + device libs (Tier A). Install Tier B only if wheel load fails with missing `librocblas` / MIOpen.

Optional if wheels complain:

```bash
omarchy pkg add rocblas hipblas hipblaslt miopen-hip
# or full SDK meta:
omarchy pkg add rocm-hip-sdk
```

---

## 3. Project venv (Python libs — keep here)

| Field | Value |
|-------|--------|
| Path | `.venv` (`/home/jim/Workspace/project-elyra/.venv`) |
| Python | **3.12.8** |
| torch | **2.13.0+rocm7.2** (was `2.13.0+cu130` pre-PR3) |
| torchvision | **0.28.0+rocm7.2** |
| torch.version.hip | **`7.2.53211`** |
| cuda.is_available | **`True`** |
| get_device_name(0) | `AMD Radeon Graphics` (rocminfo: AMD Radeon VII / **gfx906**) |
| transformers | **5.14.1** |
| accelerate | **1.14.0** |
| safetensors | **0.8.0** |
| numpy | **2.5.1** |
| triton | **triton-rocm 3.7.1** (ROCm wheel dep; CUDA `triton` removed) |
| flash_attn | not installed |
| residual nvidia-\* / cuda-\* pip | **none** (purged) |

### pip freeze subset (post-ROCm)

```
accelerate==1.14.0
numpy==2.5.1
safetensors==0.8.0
tokenizers==0.22.2
torch==2.13.0
torchvision==0.28.0
transformers==5.14.1
# local labels: torch 2.13.0+rocm7.2, torchvision 0.28.0+rocm7.2, triton-rocm==3.7.1
```

Full artifacts: [freezes/](freezes/) (`pre-rocm-*`, `mid-swap-*`, `post-rocm-*`).

### torch detail

```
2.13.0+rocm7.2
cuda meta None
hip 7.2.53211
avail True
name AMD Radeon Graphics
```

### A5 compute gate (2026-07-29)

| Check | Result |
|-------|--------|
| HIP / is_available / probe | **PASS** (A1–A4) |
| fp16 matmul on `cuda:0` | **FAIL** — rocBLAS: no TensileLibrary for **gfx906** |
| Wheel Tensile lazy arches | gfx1030, 1100–1102, 1150–1151, 1200–1201, **908, 90a, 942, 950** — **no 906** |
| Action | **Hard stop** — no model load / no Tier B for ISA |

### Elyra probe (post-switch)

- `probe_devices`: torch available; **cuda=False, rocm=True, cpu=True**
- `select_device(auto)` → **rocm** (library level; ignores toml)
- `elyra.toml` on main project during bring-up: `embed_device=cpu` (**local uncommitted pin** — do not commit as swap safety)
- Product worker must keep pin until a deliberate dogfood plan exists; GPU matmul is currently unusable on this wheel

### pyproject `memory-embed` constraints

| Requirement | Constraint | Current venv | Notes |
|-------------|------------|--------------|--------|
| torch | `>=2.2` | 2.13.0+rocm7.2 | Version OK; **backend ROCm** but **gfx906 kernels missing** in wheel rocBLAS |
| torchvision | `>=0.17` | 0.28.0+rocm7.2 | Matches torch family |
| transformers | `>=4.51` | 5.14.1 | OK for Nemotron path |
| accelerate | `>=0.33` | 1.14.0 | OK |
| Pillow | `>=10.0` | (check if needed) | optional path |

---

## 4. Required Python versions for venv ROCm (target)

**Goal:** replace CUDA torch **inside the venv** with a ROCm build, leave host ROCm as system packages.

| Package | Recommended for this host | Source | Pairs with |
|---------|---------------------------|--------|------------|
| **torch** | **2.13.x** (or latest on index) **+rocm7.2** | `pip install … --index-url https://download.pytorch.org/whl/rocm7.2` | Host ROCm **7.2.4** |
| **torchvision** | Matching tag on same index (expect **0.28.x+rocm7.2**) | same index | torch |
| **torchaudio** | optional; same index if used | same | torch |
| **transformers** | keep **≥4.51**; current **5.14.1** fine | PyPI | — |
| **accelerate** | keep **≥0.33**; current **1.14.0** fine | PyPI | — |
| **safetensors / tokenizers / numpy** | keep current unless wheel conflicts | PyPI | — |
| **triton** | let ROCm torch wheel pull compatible build | wheel deps | do not force CUDA triton |
| **flash-attn** | **do not require** | — | product falls back to sdpa/eager; AUR CK is gfx9-experimental |

`
Official pip indexes (HTTP 200 confirmed): rocm6.0–6.4, rocm7.0–7.2 among others.
Host ROCm is **7.2.4** → prefer **https://download.pytorch.org/whl/rocm7.2** for venv torch.
`

### Install sketch (venv-only libs — do not run until you approve)

```bash
cd /home/jim/Workspace/project-elyra
source .venv/bin/activate
# remove CUDA builds first
pip uninstall -y torch torchvision torchaudio
pip install --upgrade pip
# Confirmed on index: torch-2.13.0+rocm7.2 and torchvision-0.28.0+rocm7.2 for cp312
pip install torch==2.13.0 torchvision==0.28.0 --index-url https://download.pytorch.org/whl/rocm7.2
# re-assert HF stack if needed
pip install 'transformers>=4.51' 'accelerate>=0.33' safetensors
python -c "import torch; print(torch.__version__, torch.version.hip, torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)"
```

If `rocm7.2` wheel install fails or `is_available` stays false:

1. Confirm groups + `rocminfo` still list gfx906  
2. Try `HSA_OVERRIDE_GFX_VERSION` only as last resort (document in shim; Radeon VII is already gfx906)  
3. Optionally add Tier B system packages (`rocblas`, `miopen-hip`)  
4. Fall back to `rocm7.1` / `rocm7.0` wheel index only if 7.2 wheels are incompatible — **prefer matching major.minor to host 7.2**

**Do not** install Arch `python-pytorch-rocm` into the venv path; it is for **system Python 3.14** and conflicts with keeping libs in `.venv`.

---

## 5. Gaps / next actions

| # | Item | Priority |
|---|------|----------|
| 1 | Add user to `render` + `video`, re-login | Medium (kfd is world-RW today; still best practice) |
| 2 | ~~Swap venv torch/torchvision to **rocm7.2** wheels~~ | **Done (PR3)** — install OK; A5 ISA fail |
| 3 | ~~Re-run Elyra `probe_devices` → expect `rocm=True`~~ | **Done** — `rocm=True` |
| 4 | **gfx906 kernel path** — older wheel index experiment, or source/Tensile with gfx906; **not** Tier B cargo-cult | **Critical** for any GPU encode |
| 5 | Install Tier B only if dynamic linker / missing `librocblas` errors (not for ISA) | Low — not applicable to current A5 |
| 6 | Keep product `embed_device=cpu` pin until compute works | **High** safety |
| 7 | Isolated Radeon VII shim only after A5 green | Blocked on A5 |
| 8 | `03_nemotron_encode` + BUG-mem-gpu-01 update after A5 green | Blocked on A5 |

---

## 6. Quick status summary

| Layer | Status |
|-------|--------|
| Hardware Radeon VII / gfx906 | **OK** — seen by rocminfo |
| Host ROCm 7.2.4 Tier A | **OK** — installed and tools work |
| Host Tier B (BLAS/MIOpen) | **Not installed** — not used for ISA miss |
| Venv ROCm torch | **Installed** `2.13.0+rocm7.2` — HIP OK |
| A5 matmul / gfx906 kernels | **FAIL** — no rocBLAS Tensile for gfx906 in wheel |
| Embed path effective device | **CPU** (local pin); library `auto` would choose **rocm** |

---

*Updated after LuxPrimata operator switch (PR3). See NOTES-DOGFOOD.md for A5 evidence.*
