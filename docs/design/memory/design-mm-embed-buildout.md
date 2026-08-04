# Design: Multimodal semantic memory buildout (ingest → encode → search → glass)

| Field | Value |
|-------|--------|
| **Class** | DESIGN |
| **Document** | Complete multimodal product loop for Phase 2 semantic / embedding memory |
| **Product** | project-elyra |
| **Author** | Grok Build (design agent) |
| **Date** | 2026-08-04 |
| **Status** | **Ready for operator review** — not yet executed |
| **Topic branch** | `feature/mm-embed-buildout` (from `working`) |
| **PR base** | `working` (integration tip; house branch law) |
| **Depends on** | Phase 2 PR1–PR9 + rectification PR-R1–R5 + continuous encode (embed-async PR1–PR4) **shipped in code** |
| **Related issues** | [#80](https://github.com/jtwolfe/project-elyra/issues/80) (semantic dogfood residual), [#114](https://github.com/jtwolfe/project-elyra/issues/114) (busy encode dogfood), [#115](https://github.com/jtwolfe/project-elyra/issues/115) (GPU packaging — peer, not blocking image path on CPU/mock) |
| **Architecture (as-shipped)** | [architecture/phase-2-semantic.md](../../state/memory/architecture/phase-2-semantic.md) |
| **Normative priors** | [design-phase-2-implementation.md](design-phase-2-implementation.md) (historical), [design-phase-2-rectification.md](design-phase-2-rectification.md), [design-nemotron-runtime.md](design-nemotron-runtime.md), [design-embed-async-encode-worker.md](../embed/design-embed-async-encode-worker.md), [design-glass-multimodal-attachments.md](../glass/design-glass-multimodal-attachments.md) |
| **Engineering** | [engineering-principles.md](../../dev/engineering-principles.md) |
| **Out of scope** | Hyperedge formation product (#98-class); Phase 2a traversal polish; Phase 3 procedural; product default-on Gate B for all installs |

> **Terminology (locked):** This design completes the **multimodal product loop** for Phase 2 **vector ANN / bonded multi-channel embeddings**. It does **not** own hypergraph edge formation or directed traversal product polish. Those follow **after** this buildout (operator sequence: MM → edges → traversal).

---

## Overview

Phase 2 already ships the **plumbing** for multimodal semantic memory:

- Bonded channels `text` / `image` / `audio` / `video` / `joint` (~2048-d).
- Promote marks embeddable atoms `pending`; EncodeWorker + EmbedderGate drain continuously when flags are on.
- `encode_atom` resolves `media_ids` via `MediaStore` under a MIME matrix; Nemotron/mock implement per-mod + joint APIs.
- Lance stores `emb_*`; search uses `auto` / joint-primary after rectification; meal semantic + Vectors glass exist.

What is **not** product-complete is the **closed loop operators can trust**:

1. Glass / chat media reliably becomes atom `media_ids`.
2. Encode actually produces **real media channels** when deps allow (not silent text-only with a green pill).
3. Search / meal can surface media-backed atoms under honest channel selection.
4. Glass **Atoms / Vectors / Context** show the same truth as the backend (status, skip reasons, per-channel counts, media previews).

This design plans the **vertical slice** to finish that loop, with **image-first** as the hard gate and audio/video as the same pipeline with tighter caps. Work lands on **`feature/mm-embed-buildout`**, stacked to `working` only after hermetic suite green. **No auto-promote to `main`.**

---

## Background & Motivation

### Why now

- Operator sequence agreed: polish multimodal semantic memory **before** hyperedge formation and traversal depth work.
- Architecture and designs already describe the target; residual is **integrity + dogfood + glass honesty**, not a new embedding theory.
- Dogfood flags can be on (`elyra.toml`: lance + semantic + nemotron) while factory defaults stay safe (off).

### Current state (verified in code, 2026-08-04)

#### Ingest / promote

| Step | Where | Behaviour today |
|------|-------|-----------------|
| Glass attach | `runtime/web` + media APIs | Durable `MediaStore`; chat attachments rendered |
| Promote media | `memory/promote.py` `_media_ids_from_beat` | Pulls `media_ids` / `attachment_ids` from beat meta or speak JSON |
| Media-only atoms | promote | Allowed when text empty and media present |
| Fingerprint | `embed/encode.py` `content_fingerprint` | Text + sorted media ids |

#### Encode

| Step | Where | Behaviour today |
|------|-------|-----------------|
| Media resolve | `embed/encode.py` `resolve_media_inputs` | First image/audio/video only; oversize / unknown / missing soft-skip into `EncodeResult.meta["embed_media_skipped"]` |
| **MediaStore path gap (verified)** | `encode.py` vs `media/store.py` | Resolve looks for `att.path` / `att.local_path` or `resolve_path` / `path_for`. Live **`Attachment` has neither**; truth is **`sha256` + `MediaStore.blob_path(sha256)`** / `read_bytes`. **Product media encode can soft-fail as `no_path` even when blobs exist** — **PR1 must fix this contract** (prefer `blob_path` or path helper on store; do not invent fields on Attachment without need). |
| Drain wire | `presence/worker.py` + `embed/queue.py` | Passes `MediaStore(self.paths)` into drain |
| Meta on atom | `queue.py` | Persists `embed_error`, `embed_channels`, `embed_content_fp`, attempts — **does not yet always persist `embed_media_skipped`** for glass |
| Mock | `embed/mock.py` | Full channel contract for CI |
| Nemotron | `embed/runtime.py` | Media needs `qwen_omni_utils.process_mm_info`; without it media soft-skipped; text continues; media-only → `skipped` |
| Deps | `pyproject.toml` `memory-embed` | torch / transformers / torchvision / Pillow — **no explicit `qwen-omni-utils` pin** |

#### Search / meal

| Step | Where | Behaviour today |
|------|-------|-----------------|
| Channel resolve | rectification | `auto` → joint-primary when healthy |
| Joint-for-single | KD-R1 | Text-only still gets `emb_joint` copy |
| Meal semantic | `meal.select_semantic` | Text seed encode + ANN under hard ms budget |
| Cross-modal | product intent | Text query can hit image atoms **only if** joint is true multimodal or image vectors indexed and search channel includes them |

#### Glass

| Surface | Today | Gap for MM |
|---------|-------|------------|
| Chat | Attachments footer / markdown | Mostly in place (glass multimodal attachments design) |
| Atoms list/detail | `embedding_status` chip; little media UI | Need media chips/thumbs, encode error, channels present |
| Vectors health | encoder + index + worker | Need explicit `media_encode` boolean and skip reasons |
| Vectors atoms | status filter | Need media-backed filter / channel badges |
| Context meal | semantic omit notes | Need channel + media-backed honesty when hits are media atoms |

### Pain points

1. **Silent demotion** — media soft-skip (mm utils missing, MIME, oversize) can leave text-only ready atoms that look “fine” while `emb_image` stays empty.
2. **Glass lag** — operators cannot see `media_encode`, skip reasons, or which channels are populated without reading Lance internals.
3. **No fixed smoke fixtures** — hard to claim MM green without a reproducible image→neighbor path.
4. **Audio/video second class only in practice** — matrix exists but dogfood energy is text-biased.
5. **Open residuals** (#80, #114) can still mask MM truth if text path is flaky under busy PE.

### What must not regress

- Continuous encode invariants (KD-E*): hop never blocks on bulk encode; gate lookup priority for meal/API.
- Rectification channel law: no false `ann_index_built`; joint-for-single; honest meal omit reasons.
- Never store a text-only pool under `emb_image` / `emb_audio` / `emb_video` (Nemotron fail-closed / soft-skip policy).
- Hermetic CI: mock always; no mandatory torch / GPU / real media deps.
- Engineering: modular packages, tests-as-feature, narrow APIs, docs in same change, branch law (`feature/*` → `working`, no casual `main`).

---

## Goals & Non-Goals

### Goals

1. **Closed multimodal product loop** for semantic memory: media on atom → encode channels → searchable → glass-visible.
2. **Image-first hard gate** with contract tests + operator smoke checklist.
3. **Honest encode outcomes**: durable, inspectable reasons for skip/fail/partial media (including `mm_utils_unavailable`).
4. **Search honesty**: neighbors/meal work for media-backed atoms under `auto`/joint; optional per-channel inspect remains.
5. **Glass parity**: Atoms, Vectors, Context (and chat attach if gaps) match backend capabilities and failure modes.
6. **Deps clarity**: document and optionally pin MM packing dependency in `memory-embed` (or explicit optional extra) without hard-failing text-only installs.
7. **Architecture honesty**: update Phase 2 STATE note when loop is dogfood-green; leave default-on to a later Gate B.

### Non-goals

| Non-goal | Deferred to |
|----------|-------------|
| Hyperedge / source-context edge formation product | Follow-on after this branch |
| Directed traversal polish / Graph hypergraph UI | Phase 2a residual issues (#103/#105) |
| Phase 3 success weights | Phase 3 |
| Product default-on `semantic_enabled` for all installs | Gate B + operator sign-off after dogfood |
| PDF / arbitrary document embedding as image | Later |
| Long-form video / streaming audio encode | Out of v1 matrix |
| Full ROCm/Tensile packaging matrix | #115 peer |
| Replacing Grok chat with Nemotron | Never |

---

## Product target (locked behaviour)

### Loop

```text
Glass/chat/tool media
  → MediaStore (durable att_id)
  → promote_beat / wake observe (atom.media_ids + optional text)
  → embedding_status=pending (semantic on)
  → EncodeWorker drain + MediaStore resolve
  → emb_text? emb_image? emb_audio? emb_video? emb_joint?
  → EmbeddingIndex upsert (Lance)
  → meal select_semantic / Vectors neighbors / tools
  → Glass Atoms + Vectors + Context show status + media truth
```

### Modality matrix (v1 product)

| Channel | Accept | Encode rule |
|---------|--------|-------------|
| **text** | `content_text` non-empty | Always when present |
| **image** | png / jpeg / webp (+ best-effort other `image/*`) | First resolvable id under size cap |
| **audio** | wav / mp3 | First under size cap; duration probe optional later |
| **video** | mp4 | First under size cap; short clips only (bytes cap) |
| **joint** | ≥1 modality | Multi-mod: true joint encode when model allows; single-mod: joint = copy (KD-R1) |

**First resolvable wins per channel** (already coded). Multiple images on one atom: document as “first image encodes; others listed in skip `channel_full`” — optional later PR for multi-image bags is non-goal.

### Readiness semantics

| Outcome | Meaning |
|---------|---------|
| `ready` | Index holds vectors satisfying KD20 (joint or sole non-joint) |
| `pending` | Queued / retryable (including `media_unresolved`) |
| `skipped` | Permanent for this content (kind, empty, mm utils + media-only, …) |
| `failed` | Encode/upsert failed after attempts |

**Partial media** (text ready, image skipped for mm utils) is allowed: atom may be `ready` with `embed_channels=["text","joint"]` and durable meta listing media skip reasons. Glass must show partial, not “fully multimodal.”

### Search product defaults

| Surface | Default |
|---------|---------|
| Product search channel | `auto` (joint-primary when joint healthy) |
| Meal seed | Text seed from meal composition (unchanged); hits may be media-backed atoms via joint |
| Free-text neighbors API | Existing `q=` path |
| Image-as-query | **Optional stretch** (PR-S1); not required for MM complete if text→image-atom works via joint |

### Operator dogfood bar (“MM green”)

With `backend=lance`, `semantic_enabled`, `embed_enabled`, and (for real media) Nemotron + MM utils:

1. Attach a fixture image in Glass → atom shows `media_ids` + pending→ready.
2. Vectors health: `media_encode=true` (or honest false with reason).
3. `vectors_by_channel.image ≥ 1` (or joint populated from true multi-mod when text+image).
4. Text query related to image content returns the image atom in neighbors under `auto` (or documented channel).
5. Meal Context either packs semantic hit or shows honest omit reason (not silent empty).
6. Atoms detail shows media chip + channels + last encode error/skip if any.

Mock-only CI proves contract shape; live Nemotron proves real media.

---

## Key Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| **KD-M1** | **Image-first hard gate**; audio/video same pipeline, secondary acceptance | Highest product value; already in MIME matrix and glass vision path |
| **KD-M2** | **Do not invent a new encoder**; finish Nemotron/mock contract + MediaStore wire | Plumbing exists; risk is integrity and honesty |
| **KD-M3** | **Persist encode diagnostics** on atom meta (`embed_media_skipped`, stable `embed_error` codes) and expose via inspect/API | Glass cannot lie less without data |
| **KD-M4** | **Surface `media_encode` in encoder health** (from `NemotronEmbedder.media_encode_available` / mock true) | Operators diagnose soft-skip without reading logs |
| **KD-M5** | **Optional dep explicit**: add `qwen-omni-utils` (or documented package name from spike) to `memory-embed` **or** a nested optional; never required for hermetic install | Today soft-skip is easy to miss |
| **KD-M6** | **One fixed fixture pack** under `tests/fixtures/mm_embed/` (tiny png + optional wav) + hermetic tests; live smoke script optional `@pytest.mark.memory_embed` / `gpu` | Tests-as-feature; deterministic CI |
| **KD-M7** | **Glass parity in same stack**, not a follow-up epic | “Code works but UI dark” fails definition of done |
| **KD-M8** | **Factory defaults stay off**; operator `elyra.toml` may enable | Gate B not this design |
| **KD-M9** | **#80 / #114 are prerequisites or parallel dogfood**, not re-implemented here | Link and re-verify; do not reopen rectification |
| **KD-M10** | **Branch law**: `feature/mm-embed-buildout` → PR(s) into `working`; hermetic `pytest -m 'not llm and not live_grok'` green before merge; no auto `main` | House process |
| **KD-M11** | **No silent allow of text vectors under media channel names** (keep current fail-closed) | Trust invariant |
| **KD-M12** | **Multi-image bag encode deferred**; first-wins + skip reason is v1 | Avoid scope explosion |
| **KD-M13** | Hyperedges / traversal **explicitly sequenced after** this buildout | Prevents walk polish on weak MM substrate |

---

## Architecture

### Modules (touch set — keep narrow)

| Module | Role in buildout |
|--------|------------------|
| `elyra/memory/promote.py` | Ensure glass attachment ids reliably become `media_ids` (audit + fix gaps only) |
| `elyra/memory/embed/encode.py` | Resolve matrix; fingerprint; encode_atom meta completeness |
| `elyra/memory/embed/runtime.py` / `mock.py` | media_encode health; MM soft-skip consistency |
| `elyra/memory/embed/queue.py` | Persist `embed_media_skipped` / channels on all outcomes |
| `elyra/memory/inspect.py` | atom detail + encoder health fields for glass |
| `elyra/runtime/api.py` | Pass-through only; no god-module growth |
| `elyra/runtime/web/*` | Atoms / Vectors / Context honesty |
| `elyra/media/store.py` | Resolve path contract used by encode (no redesign) |
| `tests/test_memory_embed_*` + fixtures | Contract + integration |
| `docs/state/memory/architecture/phase-2-semantic.md` | Honesty banner update at closeout |
| `pyproject.toml` | Optional MM dep pin |

**Non-touch (unless bug found):** `loop/`, tool thrash, graph traversal session logic, Phase 3 weights.

### Data contract (atom meta — additive)

| Key | When | Glass |
|-----|------|-------|
| `embed_error` | fail/skip/unresolved (existing) | Atoms detail, Vectors list |
| `embed_channels` | success (existing) | Channel badges |
| `embed_media_skipped` | list of reason strings (persist from EncodeResult.meta) | Atoms detail + Vectors health summary |
| `embed_content_fp` | success (existing) | debug |
| `embed_attempts` | existing | debug |

API detail may also include **resolved media inventory** (id, mime, modality, url `/api/media/{id}`) via MediaStore lookup in inspect — read-only, no secrets.

### Encode outcome codes (normalize, do not explode)

Stable `embed_error` / skip tokens (document in architecture):

- `kind_skipped`, `no modalities`, `media_unresolved`
- `media_mm_utils_unavailable`
- `mm_utils_unavailable` (partial skips listed in `embed_media_skipped` as `image:mm_utils_unavailable`)
- `oversize_bytes`, `unknown_type`, `missing`, `unreadable`, `channel_full:{mod}`
- `encode_failed`, `index_upsert_failed`, `queue_overflow`

Prefer reusing existing strings; only add when glass/tests need a stable code.

### Search behaviour (no redesign)

- Keep `resolve_search_channel` / joint-for-single / Lance-native path from rectification.
- Add **tests** that: image-only mock vector is findable under `auto` after joint copy; text+image joint mock finds under joint; optional live Nemotron cross-modal note in dogfood checklist.
- Meal: no new channel type; semantic items may reference media-backed atoms — Context UI should not assume text-only snippets (truncate + media badge).

---

## Glass requirements (normative)

### Atoms

- List row: `embed=` status + small **media count / type chips** when `media_ids` non-empty.
- Detail: media inventory (thumbnail for image via `/api/media/id`, type label for audio/video); `embed_channels`; `embed_error`; `embed_media_skipped` if present.
- Do not full-rebuild flash selection (respect known glass poll hygiene).

### Vectors

- Health card: **`media_encode: yes/no`**, backend, device, queue, worker liveness (existing), `vectors_by_channel` including zeros for image/audio/video when useful.
- Atoms-by-status table: show media chip + channels column if cheap.
- Neighbors: keep channel select; show resolved channel; empty state reasons already partly present — ensure media-related empty (`media_encode` false) is named.

### Context

- Semantic section: existing omit notes; if hits include media-backed atoms, show a media marker on the card when atom payload includes `media_ids`.
- No new meal channel name.

### Chat

- Re-verify attach → message → promote path against glass multimodal attachments design; **fix only proven gaps** (no redesign of STT/TTS in this branch).

---

## Testing strategy (principles §3)

| Layer | Content |
|-------|---------|
| **Unit** | `resolve_media_inputs` matrix; fingerprint; skip reasons; joint policy; health `media_encode` |
| **Contract** | `encode_atom` with fake MediaStore; queue persists `embed_media_skipped`; inspect/API JSON keys |
| **Integration** | promote beat with attachment ids → pending atom with `media_ids`; mock drain → ready + channels |
| **Hermetic suite** | All of the above under `not llm and not live_grok` |
| **Optional live** | `@pytest.mark.memory_embed` / `gpu`: Nemotron image encode when deps+weights present; skip cleanly otherwise |
| **Regression** | Never place text vector under image channel (existing Nemotron tests reinforced) |

Fixtures: tiny valid PNG (and optional tiny WAV) checked into `tests/fixtures/mm_embed/` — no large binaries.

---

## PR plan (execute-plan DAG)

Base: `working`. Topic branch: **`feature/mm-embed-buildout`**. Prefer linear stack or small Graphite stack; each PR hermetic-green.

```text
PR0  design + catalog (this doc)
  │
  ▼
PR1  ingest integrity (promote media_ids + MediaStore resolve contract tests)
  │
  ▼
PR2  encode diagnostics + media_encode health + optional dep pin
  │
  ▼
PR3  image-first encode fixtures + mock (and optional live) contract
  │
  ├────────────┐
  ▼            ▼
PR4 search   PR5 glass (Atoms/Vectors/Context parity)
  │            │
  └─────┬──────┘
        ▼
PR6  audio/video matrix dogfood (same pipeline; secondary)
        │
        ▼
PR7  docs architecture closeout + dogfood checklist + issue comments (#80/#114 link)
```

### PR0 — Design land

| | |
|--|--|
| **Scope** | This design + `docs/design/README.md` catalogue entry |
| **Out** | Code behaviour changes |
| **Tests** | n/a |
| **Done** | Operator accepts KDs / PR DAG (or notes OQ answers) |

### PR1 — Ingest integrity

| | |
|--|--|
| **Scope** | Audit/fix promote paths so glass `attachment_ids` / beat media become atom `media_ids`; hermetic tests with fake beats + MediaStore |
| **Out** | Glass chrome; Nemotron weights |
| **Modules** | `promote.py`, tests; maybe thin message→beat glue if gap proven |
| **Done** | Media-only and text+image promote tests green |

### PR2 — Encode diagnostics + deps + health

| | |
|--|--|
| **Scope** | Persist `embed_media_skipped` on atom meta for all drain outcomes; `encoder_health_block.media_encode`; `atom_to_detail` exposes meta + optional media inventory; pin/document `qwen-omni-utils` in `memory-embed` (or nested extra) |
| **Out** | UI rendering (API only) |
| **Done** | Unit/contract tests for meta + health keys |

### PR3 — Image-first encode green

| | |
|--|--|
| **Scope** | Fixture PNG; mock encode image (+ text+image joint); queue ready path; optional live Nemotron test marked skippable |
| **Out** | Full glass |
| **Done** | Hermetic image channel ready; live test skips clean without deps |

### PR4 — Search / meal honesty for media-backed atoms

| | |
|--|--|
| **Scope** | Tests: neighbors/auto find media-backed mock vectors; meal select does not crash on media atoms; semantic meta remains honest |
| **Out** | New ANN algorithm |
| **Done** | Contract tests green; document live cross-modal dogfood step |

### PR5 — Glass parity

| | |
|--|--|
| **Scope** | Atoms media chips + detail inventory; Vectors `media_encode` + channel counts + skip/error display; Context media marker on semantic items when available |
| **Out** | Graph tab redesign; STT/TTS feature work |
| **Done** | Manual checklist on dogfood + any pure UI tests if present |

### PR6 — Audio / video matrix

| | |
|--|--|
| **Scope** | Fixture/smoke for wav (and mp4 if cheap); same diagnostics; glass type labels |
| **Out** | Long media, streaming |
| **Done** | Hermetic mock paths; live optional |

### PR7 — Closeout

| | |
|--|--|
| **Scope** | Update architecture Phase 2 honesty banner + memory README status; dogfood checklist under STATE or design appendix; comment #80/#114 with MM evidence; mark design **Shipped** when merged to `working` |
| **Out** | Gate B default-on flip (separate decision) |

### Dependency summary

| PR | Depends on | Parallel? |
|----|------------|-----------|
| PR0 | — | |
| PR1 | PR0 (or together if design already on branch) | |
| PR2 | PR1 | |
| PR3 | PR2 | |
| PR4 | PR3 | parallel with PR5 after PR3 |
| PR5 | PR2 (API fields); ideally PR3 | parallel with PR4 |
| PR6 | PR3 | after PR4/PR5 preferred |
| PR7 | PR4+PR5 (+PR6 if claimed) | |

---

## Dogfood checklist (operator)

Copy into issue or STATE when executing PR7.

### Text residual (link #80)

- [ ] lance + semantic + embed: neighbors under `auto` non-empty on text corpus
- [ ] `joint_repair_remaining` drains toward 0
- [ ] meal semantic packs or honest omit

### Busy encode (link #114)

- [ ] under continuous wakes, `pending` → `ready` progresses

### Multimodal (this design)

- [ ] `media_encode` true after MM utils + Nemotron load (or false with clear reason)
- [ ] attach fixture image → atom `media_ids` → ready with `image` and/or multi joint
- [ ] Vectors `vectors_by_channel` reflects media
- [ ] text query finds image atom (joint/`auto`)
- [ ] Glass Atoms/Vectors/Context show media + encode truth
- [ ] (optional) short wav/mp4 same path

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| `qwen_omni_utils` / transformers multimodal brittle | Soft-skip + health flag; hermetic mock path; optional live only |
| GPU flaky on operator box | CPU/mock first-class; #115 separate |
| Scope creep into edges/traversal | KD-M13; explicit non-goals |
| Large media binaries in git | Tiny fixtures only; size caps at encode |
| Glass poll flash | Reuse existing soft-refresh patterns; no full DOM thrash |
| Partial encode misread as full MM | Glass shows channels + skip list (KD-M3/M7) |

---

## Open questions (operator)

Defaults recommended; lock before PR1 if disagreeing.

| ID | Question | Recommended default |
|----|----------|---------------------|
| **OQ-M1** | Pin `qwen-omni-utils` inside `memory-embed` vs nested `memory-embed-mm` extra? | **Inside `memory-embed`** with soft import (matches torch optional pattern); document in README |
| **OQ-M2** | Image-as-query in Vectors free-text box? | **Defer** (PR-S1 later); text query sufficient for MM green |
| **OQ-M3** | Should partial media (text ready, image skipped) keep `ready`? | **Yes** (current KD20); glass must show partial |
| **OQ-M4** | Open umbrella GitHub issue for this buildout? | **Yes** — single epic “MM embed buildout” linking #80/#114; children optional per PR |

---

## Definition of done (branch)

1. PR0–PR7 merged to `working` (or PR6 explicitly deferred with note).
2. Hermetic suite green on tip.
3. Dogfood checklist MM section completed or filed with evidence.
4. Architecture / design status updated (not aspirational).
5. Issues updated (#80/#114 comments; umbrella closed or residual-only).
6. **Not** required: promote to `main`, Gate B default-on, edges, traversal.

---

## Sequencing after this branch (context only)

```text
feature/mm-embed-buildout  →  hyperedge formation  →  traversal polish
     (this design)              (#98-class)            (#103/#105, Graph)
```

Do not start edge/traversal product work on this branch except to fix a hard blocker discovered in MM dogfood.

---

## References

- [engineering-principles.md](../../dev/engineering-principles.md)
- [docs/state/memory/README.md](../../state/memory/README.md)
- [architecture/phase-2-semantic.md](../../state/memory/architecture/phase-2-semantic.md)
- [design-phase-2-rectification.md](design-phase-2-rectification.md)
- [design-nemotron-runtime.md](design-nemotron-runtime.md)
- [spikes/nemotron-runtime.md](spikes/nemotron-runtime.md)
- [design-embed-async-encode-worker.md](../embed/design-embed-async-encode-worker.md)
- [design-glass-multimodal-attachments.md](../glass/design-glass-multimodal-attachments.md)
- [known-bugs.md](../../state/known-bugs.md) BUG-mem-p2-01, BUG-mem-gpu-01
