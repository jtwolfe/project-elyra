---
name: promote_tool
description: Move a verified draft tool to tools/local/ so it becomes callable. Re-promote archives the previous local package. Requires green verify hash match. No force.
kind: mutate
---

# promote_tool

Promotes only when `.verify.json` has `passed: true` and `content_hash`
matches the current draft tree. Refuses `builtin` runner kind and refuses
overwrite of **bundled** packages. When `tools/local/<name>/` already exists,
the previous payload is archived under `versions/<version_id>/` (package VCS)
then replaced via whole-tree rename. Reloads the tool registry so the tool is
callable on the next hop. There is no force flag.

On re-promote, the result may include `archived_version_id`. Recover a prior
version with `get_tool` / `revert_tool`.

Use via the `load_skill("create-tool")` checklist order:
`install_tool_draft` → `verify_tool` → `promote_tool`. Promote only after
green verify.
