---
name: browser_session_open
description: Open a headless Chromium browser session bound to the current moment. Returns session_id. Max 2 concurrent sessions. Requires elyra[browser] + playwright install chromium.
kind: mutate
---

# browser_session_open

Open a **headless Chromium** session (host-side Playwright). Returns `session_id`
for later `browser_*` calls. Sessions are bound to the current `moment_id` and
closed on moment end / supervisor stop.

- No arguments required.
- Max **2** concurrent sessions process-wide.
- Fail-closed error taxonomy:
  - `browser_unavailable` — playwright package not importable
    (`pip install -e '.[browser]'` then `playwright install chromium`)
  - `chromium_unavailable` — package present, Chromium binary missing
    (`playwright install chromium`)
  - `browser_launch_failed` — import succeeded but host Sync backend failed
    (e.g. Sync-in-asyncio / env); **not** an install-hint path — see `detail`

Prefer skill `browse` for multi-step page work. Always `browser_session_close`
when done early; otherwise host cleanup runs on moment end.

