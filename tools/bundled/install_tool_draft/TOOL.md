---
name: install_tool_draft
description: Write or update files under tools/drafts/<name>/ only. Invalidates prior verify. Drafts are not callable until promote.
kind: mutate
---

# install_tool_draft

Create or update a draft tool package. Paths in `files` must be relative
(no `..`, no absolute). Reserved control sidecars (`.verify.json`,
`.verify.*`, `.promote.*`) are rejected. After writes, `.verify.json` is
always deleted so a previous green verify cannot be reused.

**`files` shape:** a JSON **object** map `relative_path → string content`.
Not a list of objects, not a stringified JSON blob. On shape errors the
result includes `received_type`, `args_keys`, and a `hint`.

Use via the `load_skill("create-tool")` checklist order:
`install_tool_draft` → `verify_tool` → `promote_tool`.
