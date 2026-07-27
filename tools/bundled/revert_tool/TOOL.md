---
name: revert_tool
description: Restore a prior archived tool package version. Archives current first. Reason required.
kind: mutate
---

# revert_tool

Recover a previous local tool package from `tools/local/<name>/versions/<version_id>/`.

- Required: `name`, `version_id`, `reason` (min 8 characters)
- Archives the live package first (`pre_revert:<reason>`), then whole-tree swap
- Does not delete the restored version from history
- Refuses bundled names; no force flag
- Reloads the tool registry on success

Use `get_tool` with `list_versions=true` to discover version ids.

Errors: `missing_name`, `invalid_name`, `version_not_found`, `reason_required`,
`package_not_found`, `refuses_overwrite_bundled`, `package_locked`, `force_not_allowed`.
