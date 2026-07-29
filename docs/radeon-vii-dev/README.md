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
| Venv ROCm torch | **Installed** `2.13.0+rocm7.2` — HIP OK; A1–A4 PASS |
| A5 matmul / gfx906 kernels | **FAIL** — rocBLAS no gfx906 Tensile |
| A6–A7 / encode smoke | **NOT RUN** (A5 hard stop) |
| Embed path effective device | **CPU** (local `embed_device=cpu` pin; uncommitted) |
| BUG-mem-gpu-01 | **Open** — dogfood template filled; see [NOTES-DOGFOOD.md](NOTES-DOGFOOD.md) |

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
| [NOTES-DOGFOOD.md](NOTES-DOGFOOD.md) | PR3 switch + PR4 A6–A7 block + dogfood template (encode re-opens after A5 green) |

---

## Critical operator warning

Live `elyra.toml` already has:

```toml
embed_enabled = true
embed_backend = "nemotron"
embed_device = "auto"
```

As soon as HIP torch reports `rocm=True`, the presence worker will open **real Nemotron on ROCm** on the next embedder ensure. This phase’s *acceptance* is **scripts-only**, but product path arming is a side effect that **must be controlled**:

1. **Stop** Elyra / presence before any venv torch change.
2. **Pin** `embed_device = "cpu"` (or `embed_enabled = false`) in `elyra.toml` **before uninstall** — **local / uncommitted**.
3. Keep the pin until A1–A7 are green and you deliberately choose worker dogfood.

Full procedure: [VENV-ROCM-SWITCH.md](VENV-ROCM-SWITCH.md).

---

## Goals (this phase)

1. Replace venv CUDA torch with **ROCm 7.2** wheels matching host ROCm 7.2.4.
2. Prove HIP + gfx906 matmul (**A5 hard gate**) before any model load.
3. Prove standalone Nemotron encode on GPU with **non-gameable** asserts (G1–G9).
4. Keep **BUG-mem-gpu-01** Open; do not claim “GPU embed fixed.”

## Non-goals

- Full meal / presence worker GPU encode as acceptance
- System `python-pytorch-rocm` (wrong Python 3.14)
- Product-tree shims or ROCm attn reorder
- Multimodal smoke, ANN/Lance, pyproject ROCm pins
- CI GPU job (none exists)

---

## Quick start (after scripts land)

```bash
cd /path/to/project-elyra
source .venv/bin/activate   # Python 3.12.8 only
export PYTHONPATH=.
export ROCM_PATH=/opt/rocm

# Only after VENV-ROCM-SWITCH.md completed and A5 green:
python docs/radeon-vii-dev/scripts/01_device_probe.py
python docs/radeon-vii-dev/scripts/02_matmul_smoke.py   # HARD GATE
python docs/radeon-vii-dev/scripts/03_nemotron_encode.py
```

Do **not** run the switch or smokes while presence/pytest are using this venv.

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
