---
name: review-identity
description: Read and compare self or user identity (current, draft, versions); speak findings. Never promote.
---

# Review identity

This skill is the **read-only** path for identity digests. Use it to inspect current vs draft vs a version, then tell the human what you found. Drafts never inject into orient; only promoted current does — and this skill **never** promotes.

## When to use

Use this skill when:

- The human asks who you are, who they are, or what a draft / version says
- You need to compare `current` vs `draft` (or a version) before deciding whether to change anything
- Orient or a goal asks for an identity summary without mutation

## When not to use

- You need to **change** identity (body or meta) → `load_skill` name `update-identity`
- Pure social hello with no identity ask → `talk` and `speak`
- Creating tools/skills or doing ledger implementation → other stage skills

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. **`get_identity`** — required first. Set `actor` (`self` | `user`); when `actor=user`, set `user_id` (session / known id). Use `which` (`current` default | `draft` | `version`), `version_id` when `which=version`, and `list_versions: true` when comparing history.
2. Optionally a second `get_identity` for the other side (e.g. draft after current, or a specific version).
3. **`speak`** a short plain-language summary of findings — do **not** call `promote_identity` or `draft_identity` from this skill.

## Hard rules

1. **Never promote.** Do not call `promote_identity` from this skill — not “helpfully,” not after a green comparison.
2. **Never draft from here.** Mutation belongs in `update-identity`. Stay read-only.
3. **Never silent on social wakes.** If a person is waiting on glass, `speak` the summary (or say you found nothing useful). Free-text alone never reaches glass.
4. Use **exact** tool names (`get_identity`, `speak`) and skill names (`update-identity`, hyphenated) via `load_skill`.
5. For `actor=user`, prefer the **active session user** when known; do not invent foreign `user_id`s. There is no list-users tool in v1.
6. Drafts and versions are inspection only — treat `has_draft` / version list as facts, not as live SELF/USER inject.

## Process

1. Decide actor: **self** (Elyra) or **user** (a person). For user, use the session / wake user_id you know.
2. Call `get_identity` with `list_versions: true` when the ask is about history or “what changed.”
3. If `has_draft` and the human cares about pending change, call `get_identity` again with `which: "draft"`.
4. If they name a version, call with `which: "version"` and that `version_id`.
5. Compare honestly: what is live (current), what is pending (draft), what an older version said. Note meta fields that matter (`goes_by`, `display_name`, `provisional`, `real_name_known`) without dumping secrets or huge bodies unless asked.
6. **`speak`** a concise summary. Offer next step only if useful: e.g. `load_skill` name `update-identity` to draft a change — still no promote from this skill.
7. If tools error (`user_not_found`, `draft_missing`, `version_not_found`), say so on glass and stop or hand off; do not invent digests.

## Quality / completion

Done when:

- At least one successful `get_identity` (or a clear spoken error), and
- On social / user-facing moments, a real glass `speak` carried the findings, and
- No `draft_identity` or `promote_identity` was called

## Out of scope

- Writing drafts or promoting (use `update-identity`)
- Minting users or switching Glass session (operator / Glass UI)
- Operator self-adopt / grant mint (Glass Identity panel)
- Thrashing `get_identity` without a speak when a human is waiting
