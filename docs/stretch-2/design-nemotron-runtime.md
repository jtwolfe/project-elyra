# Design: Omni-Embed-Nemotron runtime (portable encoding)

**Status:** Design draft (Phase 2 dependency; not required for Phase 1)
**Branch:** `grok-improvement-memory`
**Related:** [design-phase-2-semantic.md](design-phase-2-semantic.md), [design-database-choices.md](design-database-choices.md), [design-context-meal-composition.md](design-context-meal-composition.md)

## Goal

Define a **portable contract** for loading and running **NVIDIA Omni-Embed-Nemotron** (primary target: `nvidia/omni-embed-nemotron-3b`) so Phase 2 semantic memory can encode multimodal atoms without hard-coding a single GPU vendor path into core imports.

Phase 1 must not depend on this module.

---

## Model snapshot (research baseline)

Public model card / paper characteristics (verify against pinned revision at implement time):

| Property | Typical value |
|----------|----------------|
| Identity | Omni-Embed-Nemotron-3B (NV-QwenOmni-Embed style); Thinker-only from Qwen2.5-Omni-3B lineage |
| Parameters | ~4.7B |
| Modalities | Text, image, audio, video — alone or combined |
| Output | Dense embedding, **2048** dimensions (L2-normalized for similarity) |
| Context | Large text context class (card cites up to ~32k-class token budget; respect practical limits per modality) |
| Retrieval modes | Cross-modal and joint-modal in one space |
| License / use | Check Hugging Face card at pin time (research vs commercial terms) |

This matches the logical data prototype’s multi-vector columns (`emb_text`, `emb_image`, `emb_audio`, `emb_video`, `emb_joint`) at ~2048-d.

---

## Elyra encoding contract

```text
elyra/memory/embed/
  runtime.py     # device select, load, unload
  encode.py      # modality + joint encode APIs
  types.py       # EmbeddingSet, status enums
```

### Public behaviour

1. **Encode channels present on an atom** — text / image / audio / video as available.
2. **Optional joint encode** when more than one modality is present (eager vs lazy is a Phase 2 open question).
3. **Return** float vectors (2048-d) + status (`pending` | `ready` | `failed` | `skipped`).
4. **Never block the do-loop** on cold load or large media — queue / async encode; meal uses whatever is ready + temporal spine.
5. **CI** uses a fake/mock encoder; real weights optional behind markers.

### Device policy (portability)

| Preference order | Notes |
|------------------|--------|
| CUDA | Primary documented path for NVIDIA GPUs |
| ROCm | Attempt for AMD (including operator Radeon-class hardware); treat as best-effort until spiked |
| CPU | Fallback for correctness and hermetic tests; slow; may limit batch size / media length |
| Unavailable | Embeddings stay `pending`/`skipped`; semantic channel omitted from meal |

Rules:

- No import-time hard failure if torch/CUDA/ROCm missing when memory semantic feature is off.
- Config under `ELYRA_HOME` (model path, device override, enable flag) — few env vars.
- Do not assume A100/H100-only; those are vendor test points, not Elyra minimum hardware.

### Operator hardware note

Full-precision multimodal 3B-class models are memory-heavy. Implementation should document:

- Expected VRAM/RAM bands for fp16 / quantized attempts (spike-measured, not guessed in code).
- That **semantic memory is optional** at runtime if the box cannot load the model.
- Quantization / attention backend experiments belong behind the runtime module, not scattered in `loop/`.

---

## Interaction with storage and meal

- Vectors written to Lance atom columns; ANN indexes per [design-database-choices.md](design-database-choices.md).
- Semantic neighbours enter the meal only as a **supporting** channel ([design-context-meal-composition.md](design-context-meal-composition.md)).
- Oversized text → parcels before encode; media via existing attachment refs.
- Index freshness (recent buffer + optimize) remains a Phase 2 policy, not a runtime concern beyond “encode complete” signals.

---

## Non-goals

- Fine-tuning Nemotron
- Replacing Grok (chat) with Nemotron (embed-only)
- Shipping vendor NIM as a hard dependency (local weights preferred for operator machine)
- Guaranteeing ROCm parity on day one without a spike report

---

## Spike checklist (before Phase 2 default-on)

- [ ] Load pinned revision on CUDA; encode text → 2048-d
- [ ] Encode image / short audio / short video smoke (as modalities available)
- [ ] Joint multimodal encode smoke
- [ ] CPU fallback path (may be slow; prove correctness)
- [ ] ROCm attempt on operator AMD hardware; document outcome
- [ ] Memory footprint notes at chosen precision
- [ ] Mock encoder for `pytest -m 'not gpu'`

---

## Success criteria (when Phase 2 lands this module)

- [ ] Single module owns device selection and encode
- [ ] Feature-flag / graceful omit if model absent
- [ ] Contract tests with mock; optional GPU test marked
- [ ] Architecture note links model pin + measured device results

---

*Pin model revision and re-verify card details at implementation time; this doc is the portability contract, not a frozen vendor brochure.*
