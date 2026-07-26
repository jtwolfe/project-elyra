---
name: get_identity
description: Read self or user identity (current, draft, or version) plus meta and optional version list.
kind: read
---

# get_identity

Read versioned identity digests. Drafts never inject into orient — only current.

- Required: `actor` (`self` | `user`)
- When `actor=user`: `user_id` required
- Optional: `which` (`current` default | `draft` | `version`), `version_id`, `list_versions`
- For users: payload includes computed `should_name_nudge` (soft onboarding hint)

Errors: `invalid_actor`, `missing_user_id`, `invalid_user_id`, `user_not_found`,
`version_not_found`, `draft_missing`.
