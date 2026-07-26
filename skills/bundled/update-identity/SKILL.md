---
name: update-identity
description: Draft self or session-user identity changes; self stops for Glass grant; user may promote under medium gate; soft name-nudge.
---

# Update identity

This skill is the **mutation** path for identity. Process lives here; tools stay thin: `get_identity` → `draft_identity` → (gated) `promote_identity`. Drafts never inject live — only a successful promote updates current.

## When to use

Use this skill when:

- Self or a user’s digest / address-as / notes should change, and
- You have clear intent (human asked, or soft onboarding name-nudge), and
- You will draft first — never thrash live current via imaginary patch tools

## When not to use

- Read-only inspect / compare → `load_skill` name `review-identity`
- No identity change needed → `talk` / `do-work` / `rest` as appropriate
- Creating a new local user profile → Glass / operator (`POST /api/users`); no model create-user tool in v1

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. Confirm **actor** + **session** `user_id` (K16: only update the **active session user** unless the operator promotes another profile via Glass admin). Then **`get_identity`** current (and draft if `has_draft`); honor `should_name_nudge` when present.
2. Compose body / meta; **`draft_identity`** with a non-empty `reason`. Use `meta_patch.force_full_name: true` when **setting or changing** `full_name` (including first known name).
3. Branch:
   - **actor=self** → `speak` that the draft awaits **operator adopt in the Identity panel** (Glass primary); **stop** — do not promote without a real `grant_token`.
   - **actor=user** and social context + clear user intent → `promote_identity` with `user_id` = **session user** and a clear `reason`.
4. `speak` confirmation of what changed (e.g. goes_by / notes) or that self draft is waiting for grant.

## Hard rules

1. **Draft never live.** Only `promote_identity` (or Glass promote) makes current. Do not claim a draft is adopted.
2. **Self: hard stop for grant.** After `draft_identity` for self, prefer Glass Identity panel adopt. Model `promote_identity` needs operator `grant_token` — do not invent tokens; do not loop hoping the gate opens.
3. **User: medium gate.** Promote only when social context (or host-allowed path) + reason + **target user_id == session user**. Wrong user → switch session in Glass first (or operator admin promote).
4. **Session-user only (K16).** No list-users tool. Do not draft/promote Sam while session is Jim.
5. **Name-nudge (soft):** if `get_identity` returns `should_name_nudge: true`, ask once (what to call them / real name if they want), then `draft_identity` with `meta_patch.record_name_nudge: true` (body optional when only recording the nag). Do **not** hard-block speak or other tools.
6. **`force_full_name`:** required true when setting or changing `full_name` (including null → first value). Flag is operational — host strips it; it never lands in stored draft_meta.
7. Use **exact** tool names (`get_identity`, `draft_identity`, `promote_identity`, `speak`, `wait_user`) and skill names (`review-identity`, hyphenated).
8. On social wakes, **`speak`** outcomes on glass. Free-text never reaches the human.

## Process

1. Resolve actor (`self` | `user`) and, for user, **session** `user_id` only.
2. `get_identity` current; if `has_draft`, also fetch `which: "draft"` so you extend or replace intentionally.
3. **Name-nudge branch:** if `should_name_nudge`, `speak` a short ask (address-as / name), optionally `wait_user` with a long timeout for free text; after you ask (or they answer), record via `draft_identity` + `meta_patch.record_name_nudge: true`. If they gave a name, include body and/or `goes_by` / `real_name_known` in the same or a follow-up draft.
4. Compose full draft **body** (markdown) when content changes; set `meta_patch` for structured fields (`goes_by`, `display_name`, `full_name`+`force_full_name`, `real_name_known`, `provisional`). Always pass `reason` (audit).
5. Call `draft_identity`. On `full_name_force_required`, retry with `force_full_name: true` only if the human clearly intended a full-name set/change.
6. **Self:** speak “draft ready — awaiting operator adopt in Identity panel”; stop. Optional: if operator pastes a grant token in chat, only then `promote_identity` with that token and reason ≥ 8 chars — Glass remains primary.
7. **User:** if medium gate conditions hold (social wake + clear intent + session match), `promote_identity` with matching `user_id` and reason ≥ 4 chars. Else leave draft, speak what is pending, and do not thrash promote.
8. After promote or draft-stop, `speak` what is live vs still draft. For inspect-only follow-up, `load_skill` name `review-identity`.

## Quality / completion

Done when:

- Draft exists when a change was intended (`draft_identity` succeeded), and
- Self path stopped for grant **or** user path promoted under medium gate (or honestly left draft + spoken why), and
- Name-nudge, if triggered, was asked at most once this moment and recorded, and
- Human-facing moments got a real glass `speak`

## Out of scope

- Silent self promote without grant
- Promoting a non-session user from the model path
- Read-only deep compare (use `review-identity`)
- Creating users or minting grants (Glass / host)
- Inventing `grant_token` values or host-only promote flags
