---
name: promote_tool
description: Move a verified draft tool to tools/local/ so it becomes callable. Requires green verify hash match. No force.
kind: mutate
---

# promote_tool

Promotes only when `.verify.json` has `passed: true` and `content_hash`
matches the current draft tree. Refuses `builtin` runner kind, refuses
overwrite of bundled or existing local packages. Reloads the tool registry
so the tool is callable on the next hop. There is no force flag.
