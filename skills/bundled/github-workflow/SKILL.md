---
name: github-workflow
description: Branch, worktree, Projects, and package-VCS discipline for repo work and self-mod. Use for multi-step git/gh changes; stop for grants on destructive actions.
---

# GitHub workflow

Self-improvement bridge: branch and worktree culture, GitHub Projects tracking, package VCS recovery, and grant stops — rails for Phase 1 `grok_build` when it exists.

Person vs instrument: **Elyra owns goals, identity, and moments.** git / `gh` / Build are instruments under path jail and secrets policy.

## When to use

- Multi-step repo changes, self-mod, or execute-plan style work
- Need isolation (worktree), branch discipline, or Projects tracking
- Promoting/reverting local tools or skills after growth work

## When not to use

- Pure sandbox task with no host repo → `do-work`
- Social-only wake → `talk` first
- One-shot status check that does not need a playbook → direct `git_*` / `gh_*` is fine
- Tools missing from the schema list → do not invent calls; note gap / ledger / ask operator

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. Ledger: `get_task` / `get_goal` / `list_goals` when orient is thin — restate acceptance
2. Repo posture: `git_status` and/or `git_worktree_list` (path-jailed `repo` / cwd)
3. Then branch or worktree setup before edits (`git_branch` / `git_checkout` / `git_worktree_add`)

## Hard rules

1. **Never commit to `main`** without an explicit human request. Prefer feature / `execute-plan/<id>` / topic branches on top of `grok-improvement` tip when that is the house base.
2. **Never force-push `main`** (or force-like rewrite of protected defaults). No force-push helpers in v1 — treat force as human-only forever.
3. Prefer **worktree tools** for isolation: `git_worktree_add` → work → `git_worktree_remove` (dirty remove needs `confirm: true`) / `git_worktree_prune`.
4. Track multi-step work on **Projects** + ledger: `gh_project_list` / `gh_project_item_list` / `gh_project_item_add` / `gh_project_item_edit` / `gh_project_field_list` when available; soft-fail `auth_unavailable` without inventing success.
5. **Package VCS** for self-grown packages: re-promote archives prior local; recover with `get_tool` / `get_skill` (`list_versions`) and `revert_tool` / `revert_skill` (**reason required**). Never overwrite **bundled**.
6. Prefer **`grok_build` when present** for multi-file implementation; else `search_replace` + `run` (or create-tool path for new callables). Do not thrash host path fishing.
7. **Grant stops** on high-impact / destructive actions: dirty worktree remove, package revert of high-impact tools, anything merge/force-like, identity-critical changes. Stop → `speak` / `wait_user` (or operator grant) — do not auto-confirm.
8. Path jail: stay inside allowed repo roots. Outside jail → honest refuse, not workarounds via `run`.
9. Secrets: `gh_*` use injected token only; raw secrets never in speak, ledger notes, or free-text.

## Process

1. **Orient** the job: goal/task acceptance, base branch, whether a worktree is warranted.
2. **Isolate:** create or select a worktree/branch; never pile unrelated edits on `main`.
3. **Track:** open/update ledger task; optionally Projects item with clear status fields.
4. **Implement:** `grok_build` if in tool list; else focused file edits + tests via allowed tools.
5. **Verify:** tests / dogfood acceptance; `git_status` / `git_diff` / `git_log` before commit.
6. **Ship path:** `git_add` → `git_commit` on the feature branch; `gh_pr_create` / `gh_pr_list` / `gh_pr_view` when remote review is needed; `gh_issue_*` / `gh_api` as escape hatch only when structured tools lack the op.
7. **Package growth:** follow `create-tool` / `create-skill` (draft → verify tools only → promote). On broken local: list versions → `revert_*` with reason (stop for grant if high-impact).
8. **Cleanup:** remove disposable worktrees when done; update ledger / Projects; `review-work` before goal close.

## Failure modes

| Signal | Action |
|--------|--------|
| `path_jail` / refuse outside roots | Stop escape attempts; fix path or ask operator |
| `auth_unavailable` on `gh_*` | Note missing `gh_token` / grants; do not fake PR/issue/project success |
| Dirty worktree remove without `confirm` | Re-check; only confirm when intentional; grant stop if unsure |
| Broken local tool/skill after promote | `get_*` + `list_versions` → `revert_*` with reason; do not rewrite bundled |
| git/`gh` tools absent from schema | Ledger the gap; do not invent tool names or shell around the jail |

## Quality / completion

Done when:

- Work is on a non-`main` branch/worktree with honest status, or
- PR/issue/project state matches reality (or soft-fail noted), and
- Package changes are promoted/reverted with recovery path clear, and
- Destructive steps either completed with explicit confirm/grant or stopped for the human

## Out of scope

- Implementing the `grok_build` tool itself (rails only)
- Force-push / automated merge to `main`
- Bypassing path jail or secret injection
- Duplicating full git/`gh` schema contracts in prose (schemas win)
