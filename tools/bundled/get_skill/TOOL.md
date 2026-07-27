---
name: get_skill
description: Read a skill package (current, draft, or archived version) and optional versions list.
kind: read
---

# get_skill

Inspect a skill package without loading it into the active playbook.

- Required: `name`
- Optional: `which` (`current` default | `draft` | `version`), `version_id`, `list_versions`
- `list_versions` returns meta only (version_id, content_hash, archived_at, bytes, reason) — not full bodies
- Previews are truncated

Errors: `missing_name`, `invalid_name`, `invalid_which`, `package_not_found`,
`draft_missing`, `version_not_found`.
