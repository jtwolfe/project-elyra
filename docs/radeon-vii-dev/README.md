# Radeon VII / ROCm venv bring-up (dev)

**Branch / train:** `grok-improv-radeonvii`  
**Host target:** LuxPrimata (AMD Radeon VII, gfx906, host ROCm 7.2.4)  
**Scope:** Project `.venv` ROCm torch swap + standalone Nemotron encode smoke.  
**Isolation:** All Radeon-VII notes, freezes, and helpers live under **`docs/radeon-vii-dev/`** only for this phase. No product-tree `dev_shims`.

---

## Status

| Layer | Status |
|-------|--------|
| Hardware Radeon VII / gfx906 | **OK** — seen by `rocminfo` |
| Host ROCm 7.2.4 Tier A | **OK** |
| Host Tier B (rocBLAS / MIOpen) | Not installed (ISA miss — not used as workaround) |
| Venv ROCm torch | **Installed** `2.13.0+rocm7.2` — HIP OK |
| gfx906 Tensile inject | **Required** — `scripts/00_inject_gfx906_tensile.py` (Arch rocblas → venv torch) |
| A5 matmul / gfx906 kernels | **PASS** (after inject) |
| A6–A7 / encode smoke | **PASS** — G1–G9; ~9 GiB VRAM; param `cuda:0` |
| Product worker embed device | local `embed_device=rocm` — GPU **load** seen; **in-moment encode unverified** |
| BUG-mem-gpu-01 | **Open** — standalone green; moment encode path to dig later; see [NOTES-DOGFOOD.md](NOTES-DOGFOOD.md) |

See [STACK-INVENTORY.md](STACK-INVENTORY.md) for the full hardware/package baseline.

---

## Package index

| Path | Purpose |
|------|---------|
| [STACK-INVENTORY.md](STACK-INVENTORY.md) | Hardware, host ROCm, venv package baseline |
| [design-rocm-venv-gpu-embed-smoke.md](design-rocm-venv-gpu-embed-smoke.md) | Full design (acceptance, G1–G9, KD*, PR plan) |
| [VENV-ROCM-SWITCH.md](VENV-ROCM-SWITCH.md) | **Operator runbook** — pin, purge, install, gates, rollback |
| [freezes/README.md](freezes/README.md) | Freeze artifacts purpose, machine scope, restore limits |
| [scripts/README.md](scripts/README.md) | Smoke scripts overview (G1–G9, exit codes, prereqs) |
| `freezes/*.txt` | Operator freezes (post-swap; LuxPrimata/ROCm-only) |
| `scripts/01_*.py` … | Standalone probes (land in scripts PR) |
| [NOTES-DOGFOOD.md](NOTES-DOGFOOD.md) | Switch / inject / A5–A7 dogfood + product-path notes |
| **This README § New terminal** | **How to start Elyra on LuxPrimata from a fresh shell** |

---

## New terminal session — start Elyra (LuxPrimata)

**Everyday** (venv + ROCm torch already set up on this host):

```bash
cd /home/jim/Workspace/project-elyra   # or your clone path
source .venv/bin/activate             # project 3.12 venv — not system Python 3.14
export ROCM_PATH=/opt/rocm            # good habit for ROCm / torch

# Only after a torch reinstall (or if A5 matmul fails again):
# python docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py

elyra start
# UI: http://127.0.0.1:8787/
```

Use the **activated project `.venv`** so `elyra` is the editable install (a global `elyra` on PATH can point at the wrong Python).

**First-time / rebuilt venv only:**

```bash
cd /home/jim/Workspace/project-elyra
./scripts/setup_venv.sh && source .venv/bin/activate
pip install -e '.[sandbox]'              # guest isolation (usual dogfood)
./scripts/setup-microsandbox.sh --doctor-only
# Optional: pip install -e '.[search]' / '.[browser]' / memory-embed extras as needed

# ROCm torch must match host ROCm — see VENV-ROCM-SWITCH.md
# Then inject gfx906 Tensile (VII only):
python docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py

elyra start
```

**Hermetic / no real LLM:**

```bash
elyra start --stub-llm
# ELYRA_SANDBOX=0   # host-stub without sandbox if required
```

**Current dogfood pin** (this branch / host; may be committed on `grok-improv-radeonvii`):

```toml
embed_enabled = true
embed_backend = "nemotron"
embed_model_id = "nvidia/omni-embed-nemotron-3b"
embed_device = "rocm"    # GPU Nemotron after Tensile inject; flip to "cpu" to disarm
```

**If GPU path breaks after torch reinstall:**

```bash
source .venv/bin/activate
export ROCM_PATH=/opt/rocm
python docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py
python docs/radeon-vii-dev/scripts/02_matmul_smoke.py   # must PASS before trusting embed
```

Groups: ideally `render` + `video` + re-login; on LuxPrimata kfd/render often worked without group membership.  
Generic project run notes: [docs/README.md](../README.md) §Run. Full venv switch: [VENV-ROCM-SWITCH.md](VENV-ROCM-SWITCH.md).

---

## Critical operator warning (bring-up / torch swap)

With HIP torch and `embed_device=rocm` (or `auto` when ROCm probes true), the presence worker will open **real Nemotron on ROCm** on embedder ensure. During **venv torch uninstall/install**:

1. **Stop** Elyra / presence before any torch change.
2. Temporarily pin `embed_device = "cpu"` (or `embed_enabled = false`) **before uninstall**.
3. Re-inject gfx906 Tensile after ROCm torch reinstall; restore `rocm` only when A5 is green.

Full procedure: [VENV-ROCM-SWITCH.md](VENV-ROCM-SWITCH.md).

---

## Goals (this phase)

1. Replace venv CUDA torch with **ROCm 7.2** wheels matching host ROCm 7.2.4.
2. Prove HIP + gfx906 matmul (**A5 hard gate**) before any model load.
3. Prove standalone Nemotron encode on GPU with **non-gameable** asserts (G1–G9).
4. Keep **BUG-mem-gpu-01** Open; do not claim “GPU embed fixed.”

## Scope vs product (important)

This directory is the **non-standard Radeon VII / gfx906 dev** path (Tensile inject, host freezes, VII NOTES). Product should keep a **generic** story:

- **CPU / CUDA / modern ROCm** — first-class (official wheels, matmul green without inject)
- **Radeon VII** — optional dev profile only (see BUG-mem-gpu-01 device matrix)

Other AMD GPUs: install matching host ROCm + venv `+rocm*` torch → run `02` matmul; inject only if that arch is missing from the wheel. Project-wide setup script (ongoing) will eventually cover multi-backend install + optional `--dev-radeon-vii`-style profile — not VII-only.

## Non-goals

- Full meal / presence worker GPU encode as acceptance
- System `python-pytorch-rocm` (wrong Python 3.14)
- Product-tree shims or ROCm attn reorder
- Multimodal smoke, ANN/Lance, pyproject ROCm pins
- CI GPU job (none exists)
- Treating VII inject as the universal AMD install recipe

---

## Quick start (LuxPrimata / after ROCm venv install)

```bash
cd /path/to/project-elyra
source .venv/bin/activate   # Python 3.12.8 only
export PYTHONPATH=.
export ROCM_PATH=/opt/rocm

# Official +rocm7.2 wheel lacks gfx906 Tensile — inject once (or after torch reinstall):
python docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py

python docs/radeon-vii-dev/scripts/01_device_probe.py
python docs/radeon-vii-dev/scripts/02_matmul_smoke.py   # HARD GATE — must exit 0
python docs/radeon-vii-dev/scripts/03_nemotron_encode.py
```

Do **not** run the switch or smokes while presence/pytest are using this venv for GPU work.

---

## Related

| Doc | Why |
|-----|-----|
| `docs/known-bugs.md` **BUG-mem-gpu-01** | Bug stays Open; dogfood evidence only |
| `elyra/memory/embed/runtime.py` | Product probe / Nemotron / device map |
| `docs/stretch-2/design-nemotron-runtime.md` | Nemotron runtime design |
| Design §PR plan | PR1 docs → PR2 scripts → PR3 freezes → PR4 NOTES |

---

*Dev isolation package for Radeon VII ROCm bring-up. Update status after venv swap.*
