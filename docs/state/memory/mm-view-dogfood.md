# Multimodal view + meal rebalance — operator dogfood checklist

| Field | Value |
|-------|--------|
| **Class** | STATE |
| **Audience** | Operators |
| **Status** | Active (living checklist) |
| **Normative?** | No — prefer code on `working` |
| **Last verified** | 2026-08-05 (code on `working`/`main`; dogfood open) |
| **Design** | [design-view-media-meal-rebalance.md](../../design/memory/design-view-media-meal-rebalance.md) (**Shipped (code; dogfood pending)**) |
| **Related** | [mm-embed-dogfood.md](mm-embed-dogfood.md) (encode/search — separate path) |
| **Claim today** | **Code on `working`/`main` + hermetic tests** for viewing set, expand (image+AV), URL fetch, rebalance, observability. **Live operator dogfood not signed** (partial path/att_id dogfood in edges sessions). **Not** Gate B / product default-on for semantic encode. |

---

## Truth notes

| Claim | Status |
|-------|--------|
| `view_media` path / att_id → moment viewing set + dirty | **Code** (PR3) |
| Force re-outer when viewing dirty (KD-V13) | **Code + hermetic** (PR2) |
| Image Completions expand on wake ∪ viewing | **Code + hermetic** (PR2) |
| Audio/video Completions expand (caps + fail-closed) | **Code + hermetic** (PR4) |
| SSRF-aware URL fetch → MediaStore → view | **Code + hermetic** (PR5) |
| Outer meal rebalance (glass_tail + episodic tip) | **Code + hermetic** (PR1) |
| Status / meal snapshot viewing fields | **Code + hermetic** (PR6) |
| Operator logs: view / fetch / expand / force re-outer | **Code** (PR6) |
| Live pixel-grounded describe / AV reaction | **Open** — boxes below |
| Gate B semantic default-on | **Not** this checklist’s done bar |

**Chat perception ≠ memory encode.** Completions expand uses `ELYRA_MEDIA` + `ELYRA_VISION` (images) + `ELYRA_AV_EXPAND` (audio/video, default on). Encode soft-fail (`media_encode` false when utils missing) must **not** break expand.

---

## Prep

- [ ] xAI Completions path live (provider `xai`); local/mock providers fail-closed to inventory
- [ ] `ELYRA_MEDIA` on (default); `ELYRA_VISION` on for image; `ELYRA_AV_EXPAND` on for AV (default)
- [ ] Optional: `media_encode` / Nemotron only if also checking encode breadcrumbs — **not** required for perception dogfood
- [ ] Glass Status / Memory Context available (or `GET /api/status` + `GET /api/memory/context`)
- [ ] Sandbox writable; sample image + short wav + ≤10s mp4 ready (or public HTTPS media URLs)

---

## Image path → force re-outer → describe

- [ ] Download or place an image under the sandbox (e.g. animal photo)
- [ ] Model calls `view_media` with `path=` (or operator injects the tool)
- [ ] Tool result: `ok:true`, `perception:true`, `presentation:image_url`, `expand_next_hop:true`, `viewing_dirty:true`
- [ ] Next hop: model describes **pixels** (color/subject), not filename theater
- [ ] Logs: `view_media op=view …`, `viewing dirty: force rebuild_outer`, `media expand … images≥1`
- [ ] Status: `viewing.viewing_count ≥ 1`, `viewing.viewing_dirty` false after successful re-outer, `viewing.viewing_att_ids` lists `att_*` only (no paths/URLs)

---

## view att_id, URL fetch (public), soft large-media warnings

- [ ] Re-view via `view_media(att_id=…)` — same set membership; re-view still dirties
- [ ] `view_media(url=https://…)` public image: stored + viewing; `source_url` redacted of query secrets in logs
- [ ] Soft warnings present for large/AV media (`soft_warnings` in tool JSON)
- [ ] Private / loopback URL blocked (`url_ssrf_blocked`); log shows redacted URL only
- [ ] Moment end / finalize: viewing set + dirty cleared (KD-V12)

---

## Audio / video expand (media_encode + AV expand on)

- [ ] Short wav (≤30s product hard; soft warn >15s): next hop grounded reaction (not filename)
- [ ] ≤10s mp4: next hop grounded reaction; expand uses `video_url` data URL path when gates allow
- [ ] >10s video: explicit skip notice / `duration_over_cap`; **no** fake `perception:true` on wire when expand skips
- [ ] `ELYRA_AV_EXPAND=0`: inventory-only + host notice; tool reports `perception:false` / `av_expand_disabled`
- [ ] Encode soft-fail does **not** break Completions expand (chat path independent)

---

## Meal rebalance (more prior-moments / glass_tail)

- [ ] Subjective continuity: recent glass reactions + prior-moment media crumbs stay in outer meal tip longer than pre-rebalance
- [ ] Meal fraction remains **0.5 × window**; temporal floor **0.55** unchanged
- [ ] Context tab / last meal snapshot shows thicker glass_tail / episodic prior when corpus supports it
- [ ] Hermetic goldens already cover cut order — this box is live feel only

---

## Observability (PR6)

- [ ] `GET /api/status` → `viewing.viewing_count`, `viewing.viewing_dirty`, `viewing.viewing_att_ids`
- [ ] `GET /api/memory/context` last meal includes same viewing fields when a meal was composed mid-moment with a non-empty set
- [ ] Logs never print URL query strings, base64, or sandbox absolute paths for fetch/view/expand

---

## No Gate B flip

- [ ] Confirm semantic / embed factory defaults stay **off** unless operator already opted in for MM encode dogfood
- [ ] This checklist does **not** authorize Gate B or product default-on of Nemotron
- [ ] Sign-off here is **view_media + Completions perception + rebalance** only

---

## Sign-off

| Field | Value |
|-------|--------|
| Operator | |
| Date | |
| Branch / commit | |
| Image path describe | ☐ pass / ☐ fail |
| att_id re-view | ☐ pass / ☐ fail |
| URL public + SSRF block | ☐ pass / ☐ fail |
| Short audio | ☐ pass / ☐ fail / ☐ skipped |
| ≤10s video | ☐ pass / ☐ fail / ☐ skipped |
| Meal tip feel | ☐ pass / ☐ fail / ☐ n/a |
| Gate B untouched | ☐ confirmed |
| Notes | |
