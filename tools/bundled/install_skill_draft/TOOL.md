---
name: install_skill_draft
description: Write a skill draft under skills/drafts/<name>/SKILL.md only. Promote separately.
kind: mutate
---

# install_skill_draft

Write or update a skill **draft** only. Does not touch `skills/local/` or the
catalog. Use `promote_skill` after review to make it loadable.

- Required: `name`, `body`
- Optional: `description` (one-line catalog blurb; defaults to name)
- Size cap: 64 KiB (UTF-8) for body / assembled SKILL.md
- Frontmatter requires non-empty `name` and `description` after assemble

Prefer the draft → promote path for skills you want to review first. For a
one-shot local install (archives prior local if present), use `install_skill`.

Errors: `missing_name`, `invalid_name`, `missing_body`, `invalid_body`,
`invalid_description`, `body_too_large`, `invalid_frontmatter`, `path_jail`.
