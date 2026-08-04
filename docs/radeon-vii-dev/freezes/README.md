# Freeze artifacts (Radeon VII / ROCm bring-up)

**Purpose:** Capture pip package state before and after the project `.venv` CUDA → ROCm torch swap on **LuxPrimata**, for forensics, non-torch version restore, and review evidence.

**Design:** [design-rocm-venv-gpu-embed-smoke.md](../../design/memory/design-rocm-venv-gpu-embed-smoke.md) §3.2, §3.9  
**Runbook:** [VENV-ROCM-SWITCH.md](../../state/radeon-vii/VENV-ROCM-SWITCH.md)

---

## What lives here

| Artifact | When | Role |
|----------|------|------|
| `pre-rocm-pip-freeze.txt` | **Before** uninstall | Full `pip freeze` of cu130 + nvidia residual baseline |
| `pre-rocm-gpu-stack.txt` | Before uninstall | Filtered listing: nvidia / cuda / rocm / torch / triton |
| `mid-swap-gpu-stack.txt` | After purge, before ROCm install | Expect essentially empty (no torch) |
| `post-rocm-pip-freeze.txt` | After successful ROCm install | Full freeze of ROCm venv |
| `post-rocm-gpu-stack.txt` | After ROCm install | Filtered GPU stack listing |
| `torchn-rocm7.2-pins.txt` | Document install pins | Exact torch/torchvision lines + index URL |

These files are **operator-generated on LuxPrimata** (typically PR3 after A1–A5). They may be absent until the swap is performed. Do not invent freezes on machines without the hardware/stack.

---

## Machine-specific scope

| Rule | Why |
|------|-----|
| Freezes are **LuxPrimata / host-specific** | Package trees, residual names, and HIP build tags differ by machine |
| **`post-rocm-pip-freeze.txt` is ROCm-only** | Applying it on NVIDIA CI or a CUDA workstation as a full env restore is wrong and harmful |
| Do **not** treat freezes as portable “works everywhere” env files | Label and review as host dogfood artifacts |

---

## Freeze-restore limitations (normative)

**`pip freeze` is not sufficient to restore a torch backend.**

1. Freeze lines look like `torch==2.13.0` and **omit** the local version label (`+cu130` / `+rocm7.2`).
2. `pip install -r pre-rocm-pip-freeze.txt` alone may pull a **generic or CPU-oriented** build, not the CUDA (or HIP) wheel you had.
3. Freezes are for:
   - **Forensics** (what was installed when something broke)
   - **Non-torch** package versions (transformers, accelerate, safetensors, etc.)
4. Freezes are **not** for:
   - Restoring torch/torchvision backend alone
   - Full env clone onto a different GPU vendor host

### Exclusive rollback for torch (primary path)

Prefer the explicit index recipe from the runbook — freeze alone is **forbidden** as the sole method:

```bash
pip uninstall -y torch torchvision torchaudio
pip install torch==2.13.0 torchvision==0.28.0 \
  --index-url https://download.pytorch.org/whl/cu130
```

CUDA torch will re-pull `nvidia-*` runtime deps as needed. Optionally re-align non-torch pins from `pre-rocm-pip-freeze.txt` **excluding** torch/torchvision lines.

See [VENV-ROCM-SWITCH.md](../../state/radeon-vii/VENV-ROCM-SWITCH.md) §11.

---

## Commit hygiene

| Do | Do not |
|----|--------|
| Commit freezes **after** a successful (or documented failed) operator swap | Commit freezes that embed temporary `elyra.toml` pins |
| Label post-ROCm freezes as LuxPrimata/ROCm-only in PR notes | Apply `post-rocm-pip-freeze.txt` on NVIDIA CI |
| Keep freezes under `docs/radeon-vii-dev/freezes/` only | Scatter freezes into product package paths |

Temporary `embed_device=cpu` (or embed off) during bring-up is **local / uncommitted** unless deliberate dogfood policy — freezes/docs PRs must not land that pin accidentally.

---

## Expected generation order (operator)

```text
1. pre-rocm-gpu-stack.txt + pre-rocm-pip-freeze.txt   # before uninstall
2. mid-swap-gpu-stack.txt                               # after purge
3. post-rocm-gpu-stack.txt + post-rocm-pip-freeze.txt  # after ROCm install + A1–A4
4. torchn-rocm7.2-pins.txt                              # document pins used
```

Update [STACK-INVENTORY.md](../../state/radeon-vii/STACK-INVENTORY.md) when freezes land.

---

*Freezes document state; they do not replace the exclusive cu130 / rocm7.2 install recipes.*
