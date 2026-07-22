---
name: install_skill
description: Write a skill playbook to skills/local/<name>/SKILL.md. No draft/verify gate for skills.
kind: mutate
---

# install_skill

Install or update a local skill package in the same format as bundled
playbooks. Writes only under `skills/local/`. Refuses overwriting bundled
skill names. Use `load_skill` afterward to confirm the body loads.

Prefer the `load_skill("create-skill")` checklist order (name → body →
`install_skill` → confirm with `load_skill`).
