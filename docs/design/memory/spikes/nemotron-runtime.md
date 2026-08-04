# Spike: Omni-Embed-Nemotron portable runtime (PR8)

| Field | Value |
|-------|--------|
| **Class** | DESIGN |
| **Status** | Implementation landed (portable runtime); **Gate B measurements pending** operator dogfood |
| **Related** | [`design-nemotron-runtime.md`](../design-nemotron-runtime.md), [`design-phase-2-implementation.md`](../design-phase-2-implementation.md) PR8 |
| **Code** | `elyra/memory/embed/runtime.py`, `encode.py`; optional extra `elyra[memory-embed]` |
| **Date** | 2026-07-28 |

## Goal

Wire a **portable** load/encode path for `nvidia/omni-embed-nemotron-3b` without:

- hard-failing `import elyra.memory` when torch is absent
- flipping `semantic_enabled` / `embed_enabled` product defaults
- requiring model download in hermetic CI

## Pin approach

| Item | Choice | Notes |
|------|--------|--------|
| **Model id** | `nvidia/omni-embed-nemotron-3b` | Matches `MemorySettings.embed_model_id` default |
| **Revision** | Hub default at first dogfood; **lock commit hash after successful CUDA smoke** | Do not freeze a revision in code until Gate B green on operator hardware; record hash here |
| **Output dim** | **2048** (`EMBED_DIM`) | Matches public model card |
| **Precision** | **fp16 first** | `NEMOTRON_DTYPE_PREFERENCE = ("float16", "bfloat16", "float32")`. CPU load prefers float32. Card examples use bf16; we still try fp16 first per OQ1 / KD default |
| **Attention** | flash_attention_2 → sdpa → eager | Best-effort; missing flash-attn falls through |
| **Trust remote code** | `true` | Required by model card for Thinker-only Qwen2.5-Omni embed path |
| **License** | NVIDIA OneWay Noncommercial + Qwen RESEARCH | Research/dev dogfood only; re-check before any product default-on |

**Revision pin template (fill after Gate B):**

```text
model_id: nvidia/omni-embed-nemotron-3b
revision: <git sha after successful encode smoke>
measured: <date> device=<cuda|rocm|cpu> dtype=<float16|…> VRAM/RAM=<…>
```

CI **does not** download weights. Operators may set `memory.embed_model_path` to a local snapshot under `ELYRA_HOME` / HF cache.

## Optional dependencies

```toml
# pyproject.toml — elyra[memory-embed]
memory-embed = [
  "torch>=2.2",
  "transformers>=4.51",
  "accelerate>=0.33",
  "Pillow>=10.0",
]
```

Full multimodal (image/audio/video) may additionally need:

- A transformers build with Qwen2.5-Omni processor support (card historically cited `v4.51.3-Qwen2.5-Omni-preview`)
- `qwen-omni-utils` (`process_mm_info`) for media packing

Text-only encode works with stock AutoModel/AutoProcessor when the checkpoint loads. Media channels soft-skip when utilities or files are missing (text still encodes).

## Device policy (as shipped)

Preference order for `embed_device=auto`:

1. **CUDA** (NVIDIA) when `torch.cuda.is_available()` and not HIP
2. **ROCm** when HIP build + CUDA-namespace device available (best-effort)
3. **CPU** when torch present
4. **unavailable** when torch missing → `open_encoder(backend=nemotron)` **mock fallback**

Env escape hatch: `ELYRA_EMBED_DEVICE` (used only when preference is `auto`).

## Graceful failure matrix

| Condition | Behaviour |
|-----------|-----------|
| torch / transformers missing | Mock fallback embedder; health notes `requested_backend=nemotron` |
| Device unavailable | Mock fallback |
| Model load fail (missing path / hub / OOM) | `NemotronEmbedder.health()["ok"]=False`; encode returns `failed`; queue marks atom failed/skipped |
| Media oversize / unknown MIME | Skip that channel; still encode text |
| `qwen_omni_utils` missing | Soft-skip image/audio/video channels; **never** store text-only pool under `emb_image`/`emb_joint`; media-only atom → `skipped`; text still encodes |
| Dim ≠ 2048 | Fail closed (`EncodeResult(status=failed)`); no pad/truncate |
| Core import | **Never** imports torch at `elyra.memory` or `elyra.memory.embed` import time |
| `open_encoder(backend=mock)` | Does **not** probe/import torch; reports `device=cpu` |

## Media matrix (encode.py)

| Modality | Accept | Cap |
|----------|--------|-----|
| image | png/jpeg/webp (+ best-effort other `image/*`) | `embed_media_max_bytes` (default 8_000_000) |
| audio | wav/mp3 | same bytes; `embed_media_max_seconds` reserved |
| video | mp4 | same |
| other | skip channel | — |

Missing media id → text-only, not hard-fail of the atom when text present.

## Tests

| Test | Marker | CI |
|------|--------|-----|
| Mock fallback without deps | (none) | Always |
| Import does not pull torch | (none) | Always |
| Media matrix / oversize | (none) | Always |
| Defaults still off | (none) | Always |
| Open real backend when deps present | `memory_embed` | Skip if no torch |
| GPU text encode smoke | `gpu` + `memory_embed` | Skip without CUDA/ROCm + cached weights |

```bash
pytest tests/test_memory_embed_mock.py tests/test_memory_embed_types.py -q
pytest tests/test_memory_embed_nemotron.py -q   # expects skips ok
```

## Gate B checklist (not closed by PR8 alone)

- [ ] Load pinned revision on CUDA; encode text → 2048-d L2-norm
- [ ] Image / short audio / short video smoke as available
- [ ] Joint multimodal encode smoke
- [ ] CPU fallback correctness (may be slow)
- [ ] ROCm attempt on operator AMD; document outcome
- [ ] Memory footprint notes (fp16 / quant experiments)
- [x] Mock encoder for `pytest -m 'not gpu'`
- [x] Optional extra + graceful import

## Measured results

_None in this environment (no torch/GPU/model download during PR8 CI)._

Operator: paste VRAM peak, load seconds, encode p50/p95, and final revision pin above after dogfood.

## Non-goals (reaffirmed)

- Fine-tuning Nemotron
- Default-on `semantic_enabled` / `embed_enabled`
- Guaranteed ROCm parity day one
- Shipping NIM as hard dependency
