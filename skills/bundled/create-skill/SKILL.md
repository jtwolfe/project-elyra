---
name: create-skill
description: Author high-quality local playbooks. Use when durable, non-obvious procedural knowledge is missing and has already been judged worth encoding.
---

# Create skill

This skill is the execution path for adding a new local skill. It assumes the decision that a new skill is warranted has already been made.

Most ideas should **not** become skills.

## Decision framework (use this first)

Before writing anything, answer these questions:

1. Is this knowledge **non-obvious**? (If a competent agent already knows it, stop.)
2. Is it **procedural / how-to** rather than factual or one-off?
3. Will this be reused across multiple moments or goals?
4. Is there already a skill that covers most of this? (Prefer improving it.)
5. Would this work be better handled by an existing tool, or later by Grok Build, rather than encoded as instructions?

If the answer to 1–3 is not clearly yes, or if 4–5 suggest another path, **do not create a skill**.

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. Confirm no existing catalog skill covers this (orient / prior `load_skill` attempts)
2. Draft or install:
   - Preferred review path: `install_skill_draft` → optional `get_skill` (`which=draft`) → `promote_skill`
   - One-shot path: `install_skill` (still archives prior local on overwrite)
3. Immediately `load_skill` on the new name and verify body + catalog entry

If the gap is executable rather than instructional, switch to `load_skill` name `create-tool` instead.

## Quality bar

A good skill is:

- Short and holdable (prefer under ~80–100 lines of body)
- Opinionated about *how* work should be done in this agent
- Focused on judgment, sequencing, and failure modes
- Free of generic advice the model already knows
- Written so that loading it actually changes behaviour for the better

Reject skills that are:
- Thin wrappers around existing tools
- Generic project-management advice
- One-off notes that belong in a task or goal
- Near-duplicates of bundled or local skills

## Process

1. Search the current catalog (via orient or `load_skill` attempts) for anything close. Prefer extending an existing skill.
2. Choose a precise name (`[a-z0-9][a-z0-9_-]*`).
3. Write a tight one-line description (this is the primary trigger surface).
4. Write the body using this shape when possible:

   - **When to use / When not to use**
   - **Hard rules** (the real constraints)
   - **Process** (clear sequence)
   - **Quality / completion criteria**
   - **Out of scope** (important)

5. Install path (choose one):

   - **Draft → promote (preferred when reviewing):**
     1. `install_skill_draft` with name / description / body
     2. Optional: `get_skill` with `which=draft` to inspect
     3. `promote_skill` to make it catalog-loadable
   - **One-shot:** `install_skill` (writes draft then promotes; archives any prior local)

6. Immediately `load_skill` on the new name and verify that the body and catalog entry are correct and useful.

### Updating an existing local skill

Re-promote (or re-`install_skill`) archives the previous local package under
`skills/local/<name>/versions/<version_id>/`. Recover with `get_skill`
(`list_versions=true`) and `revert_skill` (reason required, min 8 chars).
Bundled skill names cannot be overwritten.

## Format

```markdown
---
name: example-skill
description: One precise line that says what it does and when it should trigger.
---

# Example skill

...
```

## Hard rules

- Skills are pure instructions. They never grant host power.
- Write only under `skills/drafts/` and `skills/local/`. Never touch `skills/bundled/`.
- Drafts are not catalog-visible until promoted.
- Prefer improving an existing skill over creating a new one.
- If the missing capability is executable rather than instructional, use `create-tool` (or later Grok Build) instead.
- Body size hard cap: 64 KiB UTF-8.
- Do not create a skill just to feel productive. Silence is better than a low-value skill.

## Ledger and review

Skill creation is a meaningful capability change. Keep it visible:

- Work should normally sit under an explicit goal or task.
- After creation, update the relevant ledger entries.
- Prefer `review-work` before treating the new skill as fully done.
