---
name: browser_wait
description: Short stability wait on a browser session (seconds, capped at 10).
kind: read
---

# browser_wait

Brief stability wait (`seconds`, default 0.5, max 10). Use sparingly after
actions that animate; prefer snapshot/load waits from navigation.

