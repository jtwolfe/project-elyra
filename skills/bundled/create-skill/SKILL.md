---
name: create-skill
description: Author a new playbook on disk via install_skill into skills/local/. Use when a reusable workflow should be captured.
---

# Create skill

Grow the skill catalog in the same format as bundled playbooks (dogfood).

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry from the list below (pick the first that applies). Do not answer with free-text only.

- **`install_skill`** — after name, description, and body are ready (writes `skills/local/<name>/SKILL.md`).
- Then **`load_skill`** on that name to confirm the body loads and the catalog entry is correct.

## Steps

1. Choose a **name** matching `[a-z0-9_][a-z0-9_-]*` that does not clash with an existing skill (case-normalized).
2. Write a short **description** (one line; this is what the catalog shows).
3. Write the **body** as a clear checklist: hard rules first, then steps, then out-of-scope. Keep it holdable — not a novel. Include a First tool call / First action section when the playbook should drive tools.
4. Call `install_skill` (writes only `skills/local/<name>/SKILL.md`). There is no draft/verify gate for skills.
5. Confirm with `load_skill` that the body loads and the catalog entry is correct.
6. Prefer improving an existing skill over spawning near-duplicates.

## Format (must match hand-written packages)

```markdown
---
name: my-skill
description: One-line catalog blurb.
---

# My skill
...
```

## Rules

- Skills are instructions only — they never grant host power by prose.
- Do not write under `skills/bundled/` (shipped; read-only for the model).
- Tools that execute need `create-tool`, not this skill.
