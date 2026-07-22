---
name: load_skill
description: Load full skill playbook body by exact catalog name. Call before multi-step work. Catalog alone is not enough.
kind: read
---

# load_skill

Return the full SKILL.md body for a skill from the catalog (bundled or local).

Orient **Skills available** lists **name + description only**. You must call this
tool with the **exact** catalog name (hyphenated) before following a playbook.

**Exact bundled names:** `talk`, `plan-work`, `do-work`, `review-work`, `rest`,
`create-tool`, `create-skill`. Wrong: `create_tool`, `plan_work` (underscores).

On success the wire message is framed as **PLAYBOOK ACTIVE** (not a raw JSON
blob). Follow **First tool call (mandatory)** or **First action** in the body —
do not re-plan in free-text only.

On `unknown_skill`, read `available` / `did_you_mean` in the tool result and
retry with the catalog name — do not monologue.
