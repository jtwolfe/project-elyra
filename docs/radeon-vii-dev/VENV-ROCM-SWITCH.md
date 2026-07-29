# Runbook: Project venv CUDA → ROCm 7.2 torch switch

**Audience:** Operator on LuxPrimata  
**Design:** [design-rocm-venv-gpu-embed-smoke.md](design-rocm-venv-gpu-embed-smoke.md) §2–§3  
**Inventory:** [STACK-INVENTORY.md](STACK-INVENTORY.md)  
**Policy:** Python ML libs stay in **project `.venv`** (Python 3.12.8). Host ROCm is system packages only. Never use system Python 3.14 / `python-pytorch-rocm`.

---

## Summary

1. Stop Elyra/presence; pin product embed to CPU (**local, uncommitted**) **before** uninstall.
2. Freeze + purge CUDA torch and residual `nvidia-*` / `cuda-*` packages.
3. Install `torch==2.13.0` + `torchvision==0.28.0` from `https://download.pytorch.org/whl/rocm7.2`.
4. Pass A1–A5; **A5 is a hard stop** before model load.
5. Post-swap hermetic tests only: `pytest tests/test_memory_embed_*.py -q -m "not gpu"`.
6. Rollback is **exclusive:** reinstall cu130 from the PyTorch CUDA index — freeze alone is insufficient.

**This phase’s acceptance is scripts-only; product path arming is a side effect that must be controlled, not ignored.**

---

## 1. Host prerequisites (H1–H12)

| # | Check | Command / action | Required? |
|---|--------|------------------|-----------|
| H1 | `rocminfo` lists gfx906 / Radeon VII | `rocminfo \| grep -A2 'Marketing Name'` | **Yes** |
| H2 | `rocm-smi` works | `rocm-smi` | **Yes** |
| H3 | `/opt/rocm` present; `ROCM_PATH` set | `echo $ROCM_PATH`; `ls /opt/rocm` | **Yes** |
| H4 | `/dev/kfd` accessible | `ls -l /dev/kfd` | **Yes** |
| H5 | User in `render` + `video` | `groups`; `sudo usermod -aG render,video $USER` + re-login | Recommended |
| H6 | No dual OpenCL stack | opencl-amd AUR not installed | Preferred |
| H7 | Tier A packages present | see inventory | **Yes** |
| H8 | Tier B (rocBLAS / MIOpen) | **Only** on `librocblas` / MIOpen dlopen errors — **never** for ISA misses | Conditional |
| H9 | Project venv, Python **3.12.8** | `source .venv/bin/activate && python -V` | **Yes** |
| H10 | Disk for wheels | ~2–4 GiB free (+ model already cached) | **Yes** |
| H11 | **No concurrent presence / pytest / encode** | stop Elyra; no other venv consumers | **Yes** |
| H12 | Product path pin ready | edit `elyra.toml` per §2 **before uninstall** (required). Post-HIP pin is emergency only if pin was forgotten **and** presence remains stopped | **Yes** |

```bash
export ROCM_PATH=/opt/rocm
# HSA_OVERRIDE_GFX_VERSION — last resort only; Radeon VII is native gfx906 — do not set by default
```

---

## 2. Product-path arming control (mandatory — H12 / KD16 / KD22)

On LuxPrimata, successful ROCm install changes the effective product embed device because `elyra.toml` already has `embed_backend=nemotron` and `embed_device=auto`. After HIP + `is_available`, `select_device("auto")` → `"rocm"` and the presence worker will load Nemotron on GPU on the next `_ensure_embedder` call.

### Before any uninstall (order is normative)

1. **Stop all Elyra / presence processes** using this `.venv` (no mid-swap encode queue).
2. **Do not run** `pytest`, notebooks, or other tools that import torch during the swap window.
3. **Pin product embed away from GPU** **before** uninstall/install. Preferred temporary change in `elyra.toml` (pick one):

   ```toml
   # Temporary during ROCm bring-up — LOCAL ONLY; do not commit
   embed_device = "cpu"
   # OR stronger:
   # embed_enabled = false
   ```

   **Commit hygiene (normative):** `elyra.toml` is **tracked**. The temporary pin is **local / uncommitted** unless the operator deliberately records a bring-up policy change in a dedicated commit with explicit intent. Do **not** land freezes/docs PRs that accidentally include `embed_device=cpu` or `embed_enabled=false` from swap safety. NOTES may state that the pin was applied without embedding the pin in the committed toml. Before merge of unrelated branch work, restore the intended dogfood values (`auto` / `rocm` / prior state) if the pin was only for swap safety.

4. After A1–A7 green, operator **chooses explicitly**:
   - **Keep pin** (`embed_device=cpu`) until a planned worker-path dogfood session — still **uncommitted** unless intentional; or
   - **Intentional product dogfood:** set `embed_device = "rocm"` (or `auto`) and accept GPU load in presence — still **does not** close BUG-mem-gpu-01 alone; record as separate NOTES entry with “product worker path: exercised.” Only commit if that is the deliberate branch dogfood policy.

**H12 / KD22 arming window:** pin **before** uninstall/install. “Immediately after HIP works” is **emergency recovery only** if the pin was forgotten **and** presence remains stopped — never rely on post-install pin as the primary plan.

---

## 3. Preflight (read-only)

```bash
cd /home/jim/Workspace/project-elyra   # or your clone root
source .venv/bin/activate
python -V   # expect 3.12.8
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.version.hip, torch.cuda.is_available())"
# expect today: 2.13.0+cu130  13.0  None  False
pip list | rg -i 'nvidia|cuda|rocm|torch|triton' | tee docs/radeon-vii-dev/freezes/pre-rocm-gpu-stack.txt
```

Confirm presence stopped and `elyra.toml` pin applied **before uninstall** (§2 / H12). Confirm pin is **not** staged for commit unless intentional.

---

## 4. Capture freeze (partial rollback artifact)

```bash
mkdir -p docs/radeon-vii-dev/freezes
pip freeze > docs/radeon-vii-dev/freezes/pre-rocm-pip-freeze.txt
```

**Limitation (normative):** `pip freeze` shows `torch==2.13.0` **without** the `+cu130` local label. Restoring with `pip install -r pre-rocm-pip-freeze.txt` alone will **not** reliably restore a CUDA (or HIP) wheel and may pull a generic/CPU-oriented build. Freezes are for **forensics and non-torch package versions**, not for torch backend restore. See [freezes/README.md](freezes/README.md).

---

## 5. Uninstall CUDA torch family **and residual NVIDIA stack**

**Default, not “only if fails.”** Leaving the full `nvidia-*` tree after ROCm install confuses diagnostics, bloats freezes, and can cause mixed-library failures that look like “gfx906 broken.”

```bash
# Core torch family
pip uninstall -y torch torchvision torchaudio

# CUDA-oriented triton (ROCm torch will re-pull a compatible build if needed)
pip uninstall -y triton

# Residual NVIDIA / CUDA pip packages (live venv as of 2026-07-29 — best-effort full purge)
pip uninstall -y \
  cuda-toolkit cuda-bindings cuda-pathfinder \
  nvidia-cublas nvidia-cuda-cupti nvidia-cuda-nvrtc nvidia-cuda-runtime \
  nvidia-cudnn-cu13 nvidia-cufft nvidia-cufile nvidia-curand \
  nvidia-cusolver nvidia-cusparse nvidia-cusparselt-cu13 \
  nvidia-nccl-cu13 nvidia-nvjitlink nvidia-nvshmem-cu13 nvidia-nvtx \
  2>/dev/null || true

# Catch stragglers by name (best-effort; re-run until empty)
pip list | rg -i '^(nvidia-|cuda-)' || true
# If any remain: pip uninstall -y <name>
```

Capture post-cleanup listing:

```bash
pip list | rg -i 'nvidia|cuda|rocm|torch|triton' | tee docs/radeon-vii-dev/freezes/mid-swap-gpu-stack.txt
# expect: essentially empty (no torch yet)
```

---

## 6. Install ROCm 7.2 pair (pinned)

```bash
pip install --upgrade pip
pip install torch==2.13.0 torchvision==0.28.0 \
  --index-url https://download.pytorch.org/whl/rocm7.2
```

Pins file (for documentation / PR3 artifacts):

```text
# docs/radeon-vii-dev/freezes/torchn-rocm7.2-pins.txt
# Install (only valid after uninstall of cu130 + nvidia residual):
#   pip install torch==2.13.0 torchvision==0.28.0 --index-url https://download.pytorch.org/whl/rocm7.2
torch==2.13.0+rocm7.2
torchvision==0.28.0+rocm7.2
```

**Do not** soft-upgrade with `--extra-index-url` alone — pip may keep cu130 under `==2.13.0`.

### Re-assert HF stack

```bash
pip install 'transformers>=4.51' 'accelerate>=0.33' safetensors tokenizers
# known-good: transformers==5.14.1, accelerate==1.14.0
```

---

## 7. Post-install verification (A1–A4 + inline matmul)

```bash
python - <<'PY'
import torch
print("version", torch.__version__)
print("hip", torch.version.hip)
print("cuda_meta", torch.version.cuda)
print("is_available", torch.cuda.is_available())
assert torch.version.hip, "hip not set — not a ROCm build"
assert torch.cuda.is_available(), "cuda.is_available False"
print("name", torch.cuda.get_device_name(0))
x = torch.randn(1024, 1024, device="cuda", dtype=torch.float16)
y = x @ x
torch.cuda.synchronize()
print("matmul_ok", float(y[0, 0]))
PY

python -c "from elyra.memory.embed.runtime import probe_devices, select_device; print(probe_devices()); print(select_device('auto')); print(select_device('rocm'))"
# expect: rocm=True; select_device('auto')→rocm at library level
# Note: select_device('auto') ignores elyra.toml — only MemorySettings/open_encoder reads toml.
# Product worker uses open_encoder → MemorySettings.embed_device from elyra.toml pin.

pip list | rg -i 'nvidia|cuda|rocm|torch|triton' | tee docs/radeon-vii-dev/freezes/post-rocm-gpu-stack.txt
pip freeze > docs/radeon-vii-dev/freezes/post-rocm-pip-freeze.txt
```

| Check | Expected |
|-------|----------|
| `torch.__version__` | contains `rocm7.2` / hip-capable from that index |
| `torch.version.hip` | non-`None` string |
| `torch.cuda.is_available()` | `True` |
| `get_device_name(0)` | Radeon VII marketing name |
| residual `nvidia-*` | **absent** (or only packages ROCm wheel re-pulled intentionally — document if any) |
| product pin | `elyra.toml` still `embed_device=cpu` (or embed off) until A1–A7 |

Update [STACK-INVENTORY.md](STACK-INVENTORY.md) §3/§6 after success.

---

## 8. Gates A1–A5 (A5 hard stop before model load)

| # | Criterion | How verified |
|---|-----------|--------------|
| A1 | `torch.version.hip` set | one-liner / `01_device_probe.py` |
| A2 | `torch.cuda.is_available()` True | same |
| A3 | `get_device_name(0)` identifies Radeon | same |
| A4 | `probe_devices()["rocm"]` and `select_device("rocm")=="rocm"` | same |
| A5 | Tiny GPU matmul succeeds | `02_matmul_smoke.py` (or inline matmul above) |

### A5 hard process gate (KD17)

Run `02_matmul_smoke.py` (or equivalent). **A5 is a hard process gate:**

- If matmul fails with **ISA / “no binary for gfx906” / arch not supported**:
  1. **STOP.** Do not run `03_nemotron_encode.py`.
  2. Do **not** install Tier B as a cargo-cult fix for ISA misses.
  3. Write `NOTES-DOGFOOD.md` + BUG-mem-gpu-01 evidence (torch+hip versions, exact error).
  4. Optional **only after documenting 7.2 failure:** try `rocm7.1` / `rocm7.0` indexes as experiment.
  5. Source build deferred; leave BUG open.

A6–A7 (encode + G1–G9) run only after A5 is green — see [scripts/README.md](scripts/README.md).

---

## 9. Post-swap pytest gate (must exclude `gpu` — KD21)

There is **no** `addopts = -m "not gpu"` in `pyproject.toml`. After ROCm works, `test_nemotron_encode_text_gpu` (`@pytest.mark.gpu`) becomes **runnable**: real Nemotron load/encode on ROCm, multi‑GiB wall time, bypasses G1–G9, and **ignores** the temporary `elyra.toml` pin.

**Normative post-swap command** (import/ABI / mock-path regression only):

```bash
# From repo root, activated .venv — excludes real GPU encode
pytest tests/test_memory_embed_*.py -q -m "not gpu"
```

| Do | Do not |
|----|--------|
| Use `-m "not gpu"` for the post-swap / checklist gate | `pytest tests/test_memory_embed_*.py -q` alone (may **run** full model load) |
| Prefer excluding **only** `gpu` | `-m "not gpu and not memory_embed"` — too aggressive |
| Treat `@pytest.mark.gpu` encode as **optional later**, **not** A7 acceptance | Claim pytest GPU green = A7 acceptance |

---

## 10. Failure ladder

| Symptom | Severity | Action |
|---------|----------|--------|
| Wheel not found for cp312 | High | Confirm index; exact wheel URL; last resort older index |
| dlopen missing `librocblas` / MIOpen | Med | Tier B packages only |
| `is_available` False after HIP build | High | groups, kfd, `ROCM_PATH`; no `HSA_OVERRIDE` first |
| **ISA / gfx906 unsupported on matmul** | **High — hard stop** | NOTES + BUG evidence; no model load; no Tier B for ISA |
| Matmul OK, model OOM at fp16 | Med | Exit 5 with VRAM; no CPU fallback; no bf16/f32 “recovery” for acceptance |
| Want CUDA torch again | — | **Exclusive rollback §11** |

### Mid-swap / dual-use

| Concern | Rule |
|---------|------|
| Mid-swap (no torch) | **H11:** no presence, pytest, or encode |
| Post-swap ABI / import gate | hip one-liner + `probe_devices` + **non-GPU** pytest only (§9) |
| `post-rocm-pip-freeze.txt` | **LuxPrimata / ROCm-specific** — **must not** be applied on NVIDIA CI or other machines as a full env restore |
| Long dual experimentation | Escape hatch: second venv (e.g. `.venv-rocm`) — **not** default |

---

## 11. Rollback (ordered and exclusive — KD4)

**Never document `pip install -r pre-rocm-pip-freeze.txt` as sufficient alone.**

| Priority | Action |
|----------|--------|
| **1 — Preferred** | Explicit cu130 recipe (restores torch backend): |
| | ```bash |
| | pip uninstall -y torch torchvision torchaudio |
| | pip install torch==2.13.0 torchvision==0.28.0 --index-url https://download.pytorch.org/whl/cu130 |
| | ``` |
| | CUDA torch will re-pull the `nvidia-*` residual tree as dependencies. |
| **2 — Secondary** | Restore **non-torch** packages from freeze if versions drifted (transformers, etc.) — **exclude** torch/torchvision lines or override with step 1. |
| **3 — Forbidden as sole method** | `pip install -r pre-rocm-pip-freeze.txt` alone for torch backend restore |

If nvidia residual was purged and you rollback to cu130 on an **NVIDIA** machine later, step 1’s index install must re-pull runtime libs; verify with `pip list | rg nvidia`.

Restore product pin intentionally when leaving bring-up (document in NOTES).

---

## 12. Operator checklist

```text
[ ] H1–H12 (incl. stop presence, no concurrent pytest)
[ ] elyra.toml: embed_device=cpu OR embed_enabled=false  **BEFORE uninstall** (local, do not commit)
[ ] pre-rocm freeze + pre-rocm-gpu-stack listing
[ ] pip uninstall torch/vision/audio + triton + nvidia-*/cuda-* residual
[ ] pip install torch/vision from rocm7.2 index
[ ] A1–A4 green; residual nvidia gone
[ ] A5 matmul HARD GATE — stop on ISA
[ ] post-rocm freeze + stack listing
[ ] post-swap: pytest tests/test_memory_embed_*.py -q -m "not gpu"   # NEVER omit -m "not gpu"
[ ] 03 only if A5 green → A6–A7 (G1–G9)   # not pytest @gpu
[ ] NOTES + BUG template (bug still Open)
[ ] Decide: keep product pin OR intentional worker dogfood (separate notes); pin still uncommitted unless deliberate
[ ] Optional: usermod render,video
[ ] Tier B only on dlopen — never for ISA
```

---

## 13. Acceptance criteria (this phase, reference)

| # | Criterion | How verified |
|---|-----------|--------------|
| A1–A4 | HIP / available / name / probe | §7, scripts 01 |
| A5 | Matmul on GPU (**hard gate**) | scripts 02 |
| A6 | Text encode → 2048-d, L2 ≈ 1.0 | scripts 03 G7 |
| A7 | G1–G9 GPU proof | scripts 03 |
| A9 | Freezes + this runbook under `docs/radeon-vii-dev/` | PR review |
| A10 | BUG-mem-gpu-01 dogfood note; bug remains **Open** | PR4/PR5 |

**Explicitly not required:** image/audio/video encode, EncodeQueue/worker dogfood as acceptance, ANN rebuild, flash_attention_2 success, Tier B if wheels work, CI GPU job.

---

*Normative operator procedure for the ROCm venv switch. Keep in sync with design §3.*
