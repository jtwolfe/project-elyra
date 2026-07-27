---
name: browser_click
description: Click a page element by snapshot ref (e.g. e3). Re-snapshot after. Stale refs return stale_ref.
kind: mutate
---

# browser_click

Click by **ref** from the most recent `browser_snapshot`. On `stale_ref`,
snapshot again and retry. After click, re-snapshot before further ref actions.

