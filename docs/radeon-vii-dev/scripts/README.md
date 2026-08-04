# Standalone Radeon VII / ROCm embed smoke scripts

**Purpose:** Prove HIP torch + gfx906 matmul + Nemotron encode **outside** the presence worker, encode queue, and meal path.  
**Design:** [design-rocm-venv-gpu-embed-smoke.md](../../design/memory/design-rocm-venv-gpu-embed-smoke.md) §4  
**Runbook:** [VENV-ROCM-SWITCH.md](../VENV-ROCM-SWITCH.md)

**Status:** Scripts present (`00` / `01` / `02` / `03` / `_common.py`). On LuxPrimata after ROCm venv + **gfx906 Tensile inject**, A1–A7 green (2026-07-29). On cu130 (pre-swap) they **fail closed** (exit 2).

---

## Script list

| Script | Role | Gate |
|--------|------|------|
| `00_inject_gfx906_tensile.py` | Copy gfx906 Tensile from Arch `rocblas` pkg into venv torch | **Prerequisite** for A5 on official `+rocm7.2` wheel |
| `01_device_probe.py` | HIP torch + product `probe_devices` / `select_device` agree | A1–A4 |
| `02_matmul_smoke.py` | Tiny fp16 matmul on `cuda:0` | **A5 HARD GATE** — stop before model load on ISA fail |
| `03_nemotron_encode.py` | Real Nemotron text encode on GPU with G1–G9 asserts | A6–A7 |
| `_common.py` | Shared helpers: param device, VRAM floor, GPU-proof asserts | used by 03 |

**Order is normative:** `00` (if missing Tensile) → `01` → `02` → only if exit 0 → `03`.  
**Do not** run `03` if `02` fails with ISA / arch unsupported.

---

## Prerequisites

```text
- Activated project .venv only (Python 3.12.8) — never system Python 3.14
- cwd = repo root
- PYTHONPATH=.
- ROCM_PATH=/opt/rocm (and host checklist H1–H11)
- Presence stopped; elyra.toml embed_device=cpu (or embed off) during bring-up
  (local/uncommitted pin — see VENV-ROCM-SWITCH.md §2)
- ROCm torch already installed and A1–A4 green before relying on 01/02
```

```bash
cd /path/to/project-elyra
source .venv/bin/activate
export PYTHONPATH=.
export ROCM_PATH=/opt/rocm
python docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py
```

**Why `00`:** Official PyTorch ROCm wheels list modern CDNA/RDNA Tensile arches but **not gfx906**. Arch `rocblas` still ships the gfx906 library files; we copy them into the wheel’s `torch/lib/rocblas/library/`. Re-run after any torch reinstall.

---

## Exit codes (all scripts)

| Code | Meaning |
|------|---------|
| 0 | Pass |
| 2 | ROCm / device missing |
| 3 | Load or kernel failure (or ISA — treat as hard fail) |
| 4 | Encode / dim / assert fail |
| 5 | OOM |
| 6 | Anti-game / GPU-proof assert fail (A7 hard checks) |

On current cu130 (pre-swap) hosts, scripts should **fail closed** (exit 2) — greppable asserts still present for review without hardware.

---

## Example invocation

```bash
cd /path/to/project-elyra
source .venv/bin/activate   # 3.12.8 only
export PYTHONPATH=.
export ROCM_PATH=/opt/rocm

python docs/radeon-vii-dev/scripts/01_device_probe.py
echo exit=$?

python docs/radeon-vii-dev/scripts/02_matmul_smoke.py   # HARD GATE
echo exit=$?

# only if 02 exit 0:
python docs/radeon-vii-dev/scripts/03_nemotron_encode.py \
  --text-a "passage: a red cube on a table" \
  --text-b "passage: a blue sphere in space"
```

Helpers live in `_common.py`: `require_hip`, `require_select_rocm`, `parameter_device` / `assert_params_on_cuda0` (`# noqa: SLF001` on `_model`), `VRAM_FLOOR_BYTES` (default `1_000_000_000`), `assert_gpu_nemotron` (G1–G9).

---

## Script contracts (summary)

### `01_device_probe.py`

| Item | Spec |
|------|------|
| Purpose | HIP torch + product probe agree |
| Steps | print version/hip/is_available/name; `probe_devices()`; `select_device("rocm")` and `"auto"`; exit 0 iff rocm visible |
| Runtime | < 10 s |

### `02_matmul_smoke.py` — **HARD GATE**

| Item | Spec |
|------|------|
| Purpose | Prove gfx906 compute kernels |
| Steps | fp16 matmul on `cuda:0`; synchronize; time ms |
| Fail | ISA errors → exit 3; **do not proceed to 03** |
| Runtime | < 30 s |

**On ISA / “no binary for gfx906”:** STOP. No model load. No Tier B cargo-cult. Document in NOTES + BUG-mem-gpu-01.

### `03_nemotron_encode.py` — acceptance encode

| Item | Spec |
|------|------|
| Purpose | Real embedding on GPU without worker |
| Model | `nvidia/omni-embed-nemotron-3b` or `--model-path` |
| Construct | **`NemotronEmbedder(device="rocm")` only** — no `open_encoder` |
| Dtype | **fp16 only for acceptance**. On OOM → exit 5 with VRAM stats. Optional `--dtype` is experimental / non-acceptance. Do **not** retry bf16/f32 as VRAM recovery. |
| Encode | `encode_text` A; optional B + cosine |
| Not used | EncodeQueue, presence worker, meal, Lance |

---

## G1–G9 anti-game asserts (A7)

Implement as a helper in `_common.py` (e.g. `assert_gpu_nemotron(embedder) -> dict`), called after `ensure_loaded()` and after encode. Reviewers must be able to **grep** for these checks.

| # | Assert | Why hard |
|---|--------|----------|
| **G1** | `torch.version.hip` is truthy **inside script 03** | Not only in 01 |
| **G2** | `select_device("rocm") == "rocm"` **before** construct | Backend visible |
| **G3** | Construct path is `NemotronEmbedder(device="rocm")` only | No mock via `open_encoder` |
| **G4** | After load: `health()["loaded"] is True`, `health()["error"] is None`, `health()["backend"]=="nemotron"` | Load succeeded (health alone insufficient for device) |
| **G5** | **Hard GPU proof:** first parameter device is CUDA index 0 | health `device` is constructor-only |
| **G6** | **Hard VRAM floor:** `torch.cuda.max_memory_allocated(0) >= VRAM_FLOOR_BYTES` | mock/CPU ≈ 0; real 3B fp16 multi‑GiB |
| **G7** | `len(vec)==EMBED_DIM` (2048); L2 norm ≈ 1.0 (±1e-3) | Contract |
| **G8** | Reject if requested device was ever `"cpu"` or any silent CPU placement of params | Anti-fallback |
| **G9** | Reset peak memory before load (`torch.cuda.reset_peak_memory_stats(0)`) so floor measures this run | Avoid stale peaks |

**VRAM floor:** default `VRAM_FLOOR_BYTES = 1_000_000_000` (1 GiB). Must be far above matmul-only noise; **tune upward after first real load** (expect multi‑GiB for 3B fp16) and record actual peak in NOTES. Floor is a **gate**, not a target.

### Weak signals (insufficient alone — do not use as sole pass)

- `health()["backend"]=="nemotron"` (always true for class)
- `health()["device"]=="rocm"` (constructor self-report)
- dim + L2 alone (mock and CPU satisfy)
- vector device (always `.cpu()`’d in product `_to_unit_list`)

### Parameter device access (no product public API)

`NemotronEmbedder` does not expose a public `parameter_device`. Scripts use **documented private access** after `ensure_loaded()` via `_common.py` (`# noqa: SLF001` on `_model`). Do **not** weaken A7 waiting for a product API.

---

## What these scripts are not

| Not this | Why |
|----------|-----|
| Presence / meal / EncodeQueue dogfood | Design invariant; acceptance is scripts-only |
| Substitute for `@pytest.mark.gpu` as A7 | pytest GPU test ignores G1–G9 and the toml pin |
| Post-swap hermetic gate alone | Use `pytest tests/test_memory_embed_*.py -q -m "not gpu"` for ABI/import (see runbook §9) |
| Product package code | Isolation: helpers stay under `docs/radeon-vii-dev/scripts/` |

---

## Product imports (one-way)

Scripts may import product runtime:

```python
from elyra.memory.embed.runtime import (
    DEFAULT_NEMOTRON_MODEL_ID,
    NemotronEmbedder,
    probe_devices,
    select_device,
)
from elyra.memory.embed.types import EMBED_DIM  # 2048
```

`open_encoder` / presence **must not** import docs scripts. **Forbid `open_encoder` on the acceptance path** (mock fallback + toml `auto` can false-pass).

---

## Related

| Doc | Why |
|-----|-----|
| [VENV-ROCM-SWITCH.md](../VENV-ROCM-SWITCH.md) | Install/purge/rollback before scripts matter |
| [freezes/README.md](../freezes/README.md) | Host freezes after A1–A5 |
| design §4.3 / G1–G9 | Normative assert table |
| BUG-mem-gpu-01 | Partial evidence only; bug stays Open |

---

*Contract for PR2 scripts. Fail closed without ROCm; hard-assert GPU proof when run for acceptance.*
