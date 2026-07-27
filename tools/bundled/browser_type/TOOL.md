---
name: browser_type
description: Type (append) text into an element by snapshot ref. Use browser_fill to replace existing value.
kind: mutate
---

# browser_type

Append `text` into the element identified by `ref`. Prefer `browser_fill` when
you need to replace the current value. Re-snapshot after significant DOM changes.

