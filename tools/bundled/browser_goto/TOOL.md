---
name: browser_goto
description: Navigate a browser session to an http(s) URL and wait for load. Clears snapshot refs — re-snapshot after.
kind: mutate
---

# browser_goto

Navigate to `url` (must be `http://` or `https://`) and wait for load.
**Always** call `browser_snapshot` after navigation before click/type by ref —
refs from earlier snapshots are invalid.

