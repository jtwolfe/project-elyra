---
name: secrets_set
description: Store a named secret. Result omits the value; chain args are redacted.
kind: mutate
---

# secrets_set

Write a user-managed secret. Prefer Glass for operator writes.

- Required: `name`, `value`
- Optional: `grants` (tool names that may receive inject)
- Result never includes `value`
- Reserved names (e.g. `xai_api_key`) are rejected
