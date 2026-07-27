---
name: revert_skill
description: Restore a prior archived skill package version. Archives current first. Reason required.
kind: mutate
---

# revert_skill

Recover a previous local skill package from
`skills/local/<name>/versions/<version_id>/`.

- Required: `name`, `version_id`, `reason` (min 8 characters)
- Archives the live package first (`pre_revert:<reason>`), then whole-tree swap
- Does not delete the restored version from history
- Refuses bundled names; no force flag
- Reloads the skill catalog when injected

Use `get_skill` with `list_versions=true` to discover version ids.

Errors: `missing_name`, `invalid_name`, `version_not_found`, `reason_required`,
`package_not_found`, `refuses_overwrite_bundled`, `package_locked`, `force_not_allowed`.
