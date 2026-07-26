---
name: draft_identity
description: Write a self or user identity draft (body and/or meta_patch). Never updates current.
kind: mutate
---

# draft_identity

Write **draft only** — current.md is unchanged until promote under host gates.

- Required: `actor`, `reason`
- When `actor=user`: `user_id` required
- `body`: full markdown (required unless meta-only / name-nudge-only)
- `meta_patch`: goes_by, display_name, full_name (needs `force_full_name: true` to set/change),
  real_name_known, provisional, record_name_nudge (operational — live name_nudge, not draft_meta)
- Self and user may draft freely; **promote** is gated (self needs operator grant)

Use with skills `review-identity` / `update-identity` when available.
