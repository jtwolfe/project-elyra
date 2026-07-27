---
name: secrets_list
description: List named secrets metadata (names, grants, timestamps). Never returns secret values.
kind: read
---

# secrets_list

List operator-managed secrets stored under `data/secrets/` (metadata only).

- Returns `{ secrets: [{name, managed_by, grants, created_at, updated_at, last_used_at}], count }`
- **Never** includes secret values
