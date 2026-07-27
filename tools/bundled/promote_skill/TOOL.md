---
name: promote_skill
description: Move a skill draft to skills/local/ so it is loadable. Re-promote archives the previous local package.
kind: mutate
---

# promote_skill

Promotes `skills/drafts/<name>/` → `skills/local/<name>/` after SKILL.md
presence, frontmatter (name + description), and 64 KiB size checks. Refuses
overwrite of **bundled** skill names. When `skills/local/<name>/` already
exists, the previous payload is archived under `versions/<version_id>/` then
replaced via whole-tree rename. Reloads the skill catalog when injected.
There is no force flag.

On re-promote, the result may include `archived_version_id`. Recover a prior
version with `get_skill` / `revert_skill`.

Use via `load_skill("create-skill")`:
`install_skill_draft` → review → `promote_skill` (or one-shot `install_skill`).
