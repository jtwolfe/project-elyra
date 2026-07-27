---
name: install_skill
description: One-shot install of a skill playbook to skills/local/. Archives prior local on overwrite. Prefer draft→promote when reviewing.
kind: mutate
---

# install_skill

Install or update a local skill package in the same format as bundled
playbooks. Compat one-shot path: assembles SKILL.md, writes
`skills/drafts/<name>/`, then promotes to `skills/local/` (archive-on-overwrite
when a local package already exists). Reloads the skill catalog when injected.
Refuses overwriting bundled skill names.

For review-before-live, prefer:
`install_skill_draft` → (optional `get_skill` which=draft) → `promote_skill`.

Recover prior versions with `get_skill` / `revert_skill`.

Use `load_skill` afterward to confirm the body loads. Prefer the
`load_skill("create-skill")` checklist.
