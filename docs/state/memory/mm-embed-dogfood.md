# Multimodal semantic memory — operator dogfood checklist (#124)

| Field | Value |
|-------|--------|
| **Class** | STATE |
| **Audience** | Operators dogfooding Phase 2 semantic + multimodal loop |
| **Design** | [design-mm-embed-buildout.md](../../design/memory/design-mm-embed-buildout.md) (**Shipped (code)**) |
| **Architecture** | [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) |
| **Umbrella** | [#124](https://github.com/jtwolfe/project-elyra/issues/124) |
| **Related** | [#80](https://github.com/jtwolfe/project-elyra/issues/80) text residual; [#114](https://github.com/jtwolfe/project-elyra/issues/114) busy encode; [#115](https://github.com/jtwolfe/project-elyra/issues/115) GPU packaging (peer) |
| **Claim today (2026-08-05)** | **Code complete + hermetic tests green** on `feature/mm-embed-buildout` (PR0–PR7). **Live operator dogfood not signed.** **Not** Gate B / product default-on. |

---

## Truth notes

| Claim | Status |
|-------|--------|
| MediaStore resolve (`blob_path` / `read_bytes`) | **Code** — no product `no_path` when blob exists (MM PR1) |
| Durable `embed_media_skipped` + `media_encode` health | **Code** (MM PR2) |
| Image-first drain → ready + joint | **Code + hermetic** (MM PR3) |
| Media-as-query neighbors API | **Code** (MM PR4) |
| Glass Atoms / Vectors / Context MM honesty | **Code** (MM PR5) |
| Audio/video encode + query matrix | **Code + hermetic** (MM PR6) |
| Architecture + design status honesty | **Docs** (MM PR7) |
| Live attach → neighbor path on operator machine | **Open** — boxes below |
| Gate B default-on | **Not** this checklist’s done bar |

**Factory defaults stay off:** `semantic_enabled` / `embed_enabled` / `parcels_enabled` **false**. Dogfood must opt in via operator `elyra.toml` (`backend=lance` + `elyra[memory-lance]` + embed/semantic flags).

---

## Prep

- [ ] `backend=lance` + `elyra[memory-lance]` installed
- [ ] `semantic_enabled=true`, `embed_enabled=true` (and encode worker on unless testing idle rollback)
- [ ] Encoder backend known: **mock** (CI/dev) or **Nemotron** with optional `qwen-omni-utils` for real media packing
- [ ] Glass Memory page available (Atoms / Vectors / Context)

---

## Text residual (link #80)

- [ ] lance + semantic + embed: neighbors under `auto` non-empty on text corpus
- [ ] `joint_repair_remaining` drains toward 0
- [ ] meal semantic packs or honest omit (`no_hits` / `deduped` / meta)

---

## Busy encode (link #114)

- [ ] under continuous wakes / work, `pending` → `ready` progresses (`drain_ok_total` moves)
- [ ] meal/API lookup not permanently starved under text bulk (gate wait metrics visible)

---

## Multimodal (this buildout)

### Encode / MediaStore

- [ ] confirm no `no_path` / no extensionless-blob `unknown_type` on product MediaStore for real uploads
- [ ] promote/wake: chat attach → atom `media_ids` non-empty (A1–A5 audit green)
- [ ] attach fixture image → atom ready with `image` and/or multi-mod joint; partial skips durable when expected
- [ ] `media_encode` true after MM utils + Nemotron load (or **false** with clear reason / glass tooltip)
- [ ] mock/fallback backend: glass tooltip does **not** claim Nemotron omni when `backend=mock`

### Search / Vectors

- [ ] Vectors `vectors_by_channel` reflects media once encoded
- [ ] text query finds image-backed atom (joint/`auto`) when true multi-mod joint exists
- [ ] **image-as-query** under `auto` resolves to modality (or documented fallback) and returns related neighbors; glass shows `resolved_channel` / `channel_reason`
- [ ] smoke **audio-as-query** and **video-as-query** (att_id POST neighbors)
- [ ] omit paths honest: `media_missing` / `media_encode_unavailable` / oversize — no silent text-only substitute

### Glass honesty

- [ ] Atoms list/detail: media chips, inventory, `embed_channels`, `embed_error`, `embed_media_skipped`
- [ ] Vectors health: `media_encode` line + channel counts (zeros visible)
- [ ] Context: media marker when meal hits are media-backed
- [ ] soft-refresh keeps open atom selection / inspect folds

### Audio / video corpus (optional depth)

- [ ] short wav/mp4 corpus encode path ready with `audio` / `video` channels
- [ ] query path for same fixtures returns neighbors or honest omit

---

## Hermetic evidence (not a substitute for boxes above)

| Suite / fixture | Role |
|-----------------|------|
| `tests/fixtures/mm_embed/tiny.png` / `tiny.wav` / `tiny.mp4` | Resolve + encode matrix inputs |
| `tests/test_memory_embed_resolve_media.py` | Real MediaStore resolve (MM PR1) |
| `tests/test_memory_embed_diagnostics.py` | Skip durability + `media_encode` (MM PR2) |
| `tests/test_memory_embed_mm_drain.py` | Image drain / joint (MM PR3) |
| `tests/test_memory_embed_mm_av.py` | A/V matrix (MM PR6) |
| Vectors neighbors API tests | Media-as-query contracts (MM PR4) |

Optional live: `@pytest.mark.memory_embed` / `gpu` — skip clean without deps/weights.

---

## Sign-off block

| Field | Value |
|-------|--------|
| Operator | |
| Date | |
| Tip SHA | |
| Encoder backend | mock / nemotron |
| Result | pass / fail / partial |
| Notes | |

**Done for “MM green” product claim:** all Multimodal boxes checked with notes, or residual filed on #124 with explicit defer. **Still not** Gate B default-on (separate decision after mock → Nemotron quality/latency).

---

## Related

- [architecture/phase-2-semantic.md](architecture/phase-2-semantic.md) — as-shipped map
- [design-mm-embed-buildout.md](../../design/memory/design-mm-embed-buildout.md) — KD-M* + PR plan
- [design-nemotron-runtime.md](../../design/memory/design-nemotron-runtime.md) — Gate B
- [known-bugs.md](../known-bugs.md) — **BUG-mem-p2-01**, **BUG-mem-gpu-01**
