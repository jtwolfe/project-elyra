---
name: browser_snapshot
description: Capture accessibility tree with ref=eN markers (size-capped). Use refs for click/type/fill. Re-snapshot after every navigation or DOM change.
kind: read
---

# browser_snapshot

Return a compact **accessibility tree** with `[ref=eN]` markers (char-capped).
Use those refs with `browser_click` / `browser_type` / `browser_fill` /
`browser_get_text`. Refs are **valid only until the next navigation or
interaction that changes the DOM** — re-snapshot after every change.

