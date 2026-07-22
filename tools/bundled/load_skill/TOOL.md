---
name: load_skill
description: Load full skill playbook body. Call before multi-step work (talk, plan-work, do-work, create-tool, create-skill). Catalog alone is not enough.
kind: read
---

# load_skill

Return the full SKILL.md body for a skill from the catalog (bundled or local).

Orient lists **name + description only**. You must call this tool to activate a
playbook before following its steps. Typical names: `talk`, `plan-work`,
`do-work`, `review-work`, `rest`, `create-tool`, `create-skill`.

On success the wire message is framed as **PLAYBOOK ACTIVE** (not a raw JSON
blob). Follow **First tool call (mandatory)** or **First action** in the body —
do not re-plan in free-text only.
