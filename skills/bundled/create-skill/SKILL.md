---
name: create-skill
description: Author high-quality local playbooks. Use only when durable, non-obvious procedural knowledge is missing and worth encoding.
---

# Create skill

Create a new local skill only when it will meaningfully improve future performance. Most ideas should not become skills.

## Decision framework (use this first)

Before writing anything, answer these questions:

1. Is this knowledge **non-obvious**? (If a competent agent already knows it, stop.)
2. Is it **procedural / how-to** rather than factual or one-off?
3. Will this be reused across multiple moments or goals?
4. Is there already a skill that covers most of this? (Prefer improving it.)
5. Would this work be better handled by an existing tool, or later by Grok Build, rather than encoded as instructions?

If the answer to 1–3 is not clearly yes, or if 4–5 suggest another path, **do not create a skill**.

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

5. Call `install_skill`.
6. Immediately `load_skill` on the new name and verify that the body and catalog entry are correct and useful.

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
- Write only under `skills/local/`. Never touch `skills/bundled/`.
- Prefer improving an existing skill over creating a new one.
- If the missing capability is executable rather than instructional, use `create-tool` (or later Grok Build) instead.
- Do not create a skill just to feel productive. Silence is better than a low-value skill.

## After creation

If the new skill is part of a larger improvement effort, update the relevant goal/task and consider whether `review-work` is needed before treating the work as done.
