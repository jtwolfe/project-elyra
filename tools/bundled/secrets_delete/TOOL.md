---
name: secrets_delete
description: Delete a named secret. Never returns the value.
kind: mutate
---

# secrets_delete

Remove a named secret from the file store.

- Required: `name`
- Errors: `secret_not_found`, `reserved_secret_name`, `invalid_secret_name`
