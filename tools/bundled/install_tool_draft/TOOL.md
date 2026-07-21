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
