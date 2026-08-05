---
name: view_media
description: Look at media mid-moment from a sandbox path, prior att_id, or HTTPS URL so the next model hop can perceive it (images now; audio/video Completions wire follows). Use when you need to see, hear, or inspect media that is not already on the wake.
kind: media
---

# view_media

Host capability: resolve **path** and/or **att_id** and/or **url** into the
durable media store, add the attachment to the **moment viewing set**, and leave
a thin memory breadcrumb. Multimodal bytes never appear in the tool JSON — they
travel only on the Completions wire after the host **force-rebuilds outer**
before the next hop (`expand_next_hop: true`).

## When to use

- Sandbox file you wrote or downloaded (`tmp/animal.png`, a short clip, …) and
  need to **actually look at** (not invent from the filename).
- Re-view a prior `att_*` already in MediaStore.
- HTTPS URL to fetch into MediaStore then view (prefer short clips).
- `op=list` / `drop` / `clear` to manage the current moment viewing set.

## Args

| Arg | Role |
|-----|------|
| `path` | Sandbox-relative path → ingest (origin `view`) |
| `att_id` | Existing `att_*` |
| `url` | HTTPS URL → SSRF-safe host fetch into MediaStore (origin `view`) |
| `op` | `view` (default) \| `list` \| `drop` \| `clear` |
| `note` | Optional short caption on **first** promote only |

Provide at least one of `path` / `att_id` / `url` for `op=view`. Combinations are
allowed when they refer to the **same** durable media (matching sha); different
media → `ambiguous_source`. When multiple sources are named and only one
resolves (e.g. good path + missing att_id), the host uses the successful source.
URL security failures (`url_ssrf_blocked`, `url_invalid`, …) are always hard.

**Path/URL re-view is content-idempotent:** if the blob sha already has a
MediaStore meta, that `att_*` is reused (no extra durable id / no second
breadcrumb for the same media). Prefer `att_id` when you already have one.

## URL fetch (SSRF-aware)

- **HTTPS only** (http / file / other schemes → `url_invalid` or blocked).
- Host DNS is resolved; loopback, link-local, private RFC1918, and metadata IPs
  (e.g. `169.254.169.254`) are blocked (`url_ssrf_blocked`).
- Redirects revalidated (max 3 hops); timeouts and size caps apply.
- Prefer sandbox paths when the bytes are already local.

## Modalities (honesty)

| Kind | Viewing set | Completions perception (this build) |
|------|-------------|--------------------------------------|
| **image** | yes | **yes** — `image_url` data URL on next hop (subject to provider / `ELYRA_MEDIA` / `ELYRA_VISION` gates and caps) |
| **audio** | yes | **inventory-only for now** — true `input_audio` wire parts land in a follow-on PR; tool reports `perception: false` |
| **video** | yes | **inventory-only for now** — true video wire parts land in a follow-on PR; tool reports `perception: false` |
| **file** | yes | inventory / text extract when applicable — not vision |

Never treat `ok: true` + membership as guaranteed pixels/audio when
`perception: false` or `presentation: "inventory"`.

## Soft guidance (large / long media)

- Prefer **short** media. **Video perception is reliable around ≤10 seconds**;
  longer clips may be truncated or skipped for Completions expand.
- Large downloads cost time and tokens; prefer sandbox paths when already local.
- Soft warnings may appear in the tool JSON (`soft_warnings`).

## Ops

```text
# View a sandbox image (next hop sees pixels)
view_media(path="tmp/animal.png")

# Fetch + view an HTTPS image
view_media(url="https://example.com/cat.png")

# Re-view a prior attachment
view_media(att_id="att_…")

# List / drop / clear moment set
view_media(op="list")
view_media(op="drop", att_id="att_…")
view_media(op="clear")
```

## Result shape (success view)

- `att_id`, `kind`, `mime`, `byte_size`, `source`
- `viewing` / `viewing_count` — FIFO set after the op
- `expand_next_hop: true`, `viewing_dirty: true`
- `presentation` / `perception` — honesty for wire expand
- `promoted` — whether a first-wins observation atom was written
- `source_url` — redacted (no query) when the view used `url`

Tool payload has **no media bytes**. Do not claim you saw the image until after
the next hop when `perception: true`.
