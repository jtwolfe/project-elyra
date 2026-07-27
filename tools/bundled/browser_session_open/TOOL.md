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
- Fail-closed: `browser_unavailable` (pip install -e '.[browser]') or
  `chromium_unavailable` (`playwright install chromium`).

Prefer skill `browse` for multi-step page work. Always `browser_session_close`
when done early; otherwise host cleanup runs on moment end.

