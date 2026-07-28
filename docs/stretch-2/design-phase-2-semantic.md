# Phase 2 — Semantic Memory (Nemotron + Multi-Embeddings)

**Status:** Design draft  
**Depends on:** Phase 1 temporal substrate stable

## Goal

Add semantic / vector support as *supporting context*:

- Load **NVIDIA Omni-Embed-Nemotron-3B** (or compatible) portably (CPU / CUDA / ROCm).
- For every atom (and summary atom) that has media, produce:
  - per-modality embeddings (text, image, audio, video as present)
  - one joint embedding
- These form **bonded sub-atoms** / channels inside the parent atom.
- Maintain vector indexes (or multi-channel indexes) so similarity search can seed the mnemonic subspace.
- Large messages that exceed model context are segmented at natural boundaries into **linked parcels** that remain retrieval-coherent.

## Non-goals

- No success-path weighting yet.
- Directed traversal is Phase 2a.
- No assumption that the vector index is the primary organiser; it supports the temporal structure.

## Key design points

### Multi-channel embeddings per atom

```text
Atom
  ...
  embeddings:
    text:    vector | null
    image:   vector | null
    audio:   vector | null
    video:   vector | null
    joint:   vector | null
```

Intra-atom bonds are implicit (all channels belong to the same atom id) and can later be materialised as edges if useful.

### Segmentation of oversized content

If a single message / generated text exceeds Nemotron context:

1. Split at natural points (paragraphs, sections, tool-result boundaries).
2. Create a sequence of parcel atoms (or sub-records) that are explicitly linked (sequential + “same-parent-message” structural link).
3. Embed each parcel; the parent can hold a joint or summary embedding if desired.

Retrieval of any parcel can surface the linked siblings.

### Indexes

- Primary: joint embedding index for whole-atom similarity.
- Secondary (optional in first cut): per-modality indexes for cross-modal queries.
- Short-timescale buffer (recent atoms) can be a flat or small HNSW for speed; longer-term indexes updated incrementally.

### Portable runtime

See companion doc `design-nemotron-runtime.md`. Must detect available device (CUDA, ROCm, CPU) and fall back cleanly. No hard-coded paths or assumptions about Radeon VII.

## Success criteria

- [ ] Nemotron loads and produces multi-channel embeddings on at least CPU + one GPU backend.
- [ ] Atoms receive embeddings; similarity search returns coherent neighbours.
- [ ] Oversized content is segmented and linked correctly.
- [ ] Semantic results can be mixed into the temporal context package as supporting context.
- [ ] Tests cover embedding shape, segmentation, and basic retrieval.
- [ ] No Phase 3 machinery.
