---
name: get_tool
description: Read a tool package (current, draft, or archived version) and optional versions list.
kind: read
---

# get_tool

Inspect a tool package without calling it.

- Required: `name`
- Optional: `which` (`current` default | `draft` | `version`), `version_id`, `list_versions`
- `list_versions` returns meta only (version_id, content_hash, archived_at, bytes, reason) — not full package bodies
- Previews are truncated

Errors: `missing_name`, `invalid_name`, `invalid_which`, `package_not_found`,
`draft_missing`, `version_not_found`.
