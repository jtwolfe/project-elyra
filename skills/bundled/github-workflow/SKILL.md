---
name: github-workflow
description: Branch, worktree, Projects, and package-VCS discipline for repo work and self-mod. Use for multi-step git/gh changes; stop for grants on destructive actions.
---

# GitHub workflow

Self-improvement bridge: branch and worktree culture, GitHub Projects tracking, package VCS recovery, grant stops, and **`grok_build` mode/async rails** when the instrument is present.

Person vs instrument: **Elyra owns goals, identity, and moments.** git / `gh` / Build are instruments under path jail and secrets policy.

For **L/M/H routing** and the full H-spine (design → human approve → execute_plan), prefer skill **`self-improve`**. This skill owns branch/tip law, worktrees, Projects, package recovery, and how to call/poll `grok_build` without thrashing.

## When to use

- Multi-step repo changes, self-mod, or execute-plan style work
- Need isolation (worktree), branch discipline, or Projects tracking
- Promoting/reverting local tools or skills after growth work
- Multi-file implement/design/execute_plan via `grok_build` (when in the tool list)

## When not to use

- Pure sandbox task with no host repo → `do-work`
- Social-only wake → `talk` first
- One-shot status check that does not need a playbook → direct `git_*` / `gh_*` is fine
- Tools missing from the schema list → do not invent calls; note gap / ledger / ask operator
- Complexity routing only (which mode / H-spine?) → load `self-improve`

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. Ledger: `get_task` / `get_goal` / `list_goals` when orient is thin — restate acceptance
2. Repo posture: `git_status` and/or `git_worktree_list` (path-jailed `repo` / cwd)
3. Then branch or worktree setup before edits (`git_branch` / `git_checkout` / `git_worktree_add`)
4. If an async instrument job is already in flight → poll `grok_build` with `job_id` (do not re-spawn blindly)

## Before any change (issue + branch workflow)

Always recommend this sequence for multi-step repo work (including when the operator asks via Grok Build). Development structure is also law in `docs/engineering-principles.md` §9 and `docs/branch-law.md`.

1. **Inspect issues** — `gh_issue_list` / search / Project items: find an existing home; read body + packaging label.
2. **Update or create** — update the issue (scope, residual framing, acceptance); **create a new issue if none fits**. Packaging priority: exactly one of `v0.1-gate` | `backlog` | `research` (remove triad siblings before add). Parent packaging gates under the v0.1 epic when applicable.
3. **Branch type** — short-lived branch from current **`working`**: `feature/*`, `fix/*`, `self/*`, `execute-plan/*`. Prefer worktree isolation.
4. **Work** — implement; tests; docs with the change. Do not pile unrelated edits on tip branches.
5. **Update issue / board** — Status honesty; dates if gated; close only when acceptance is met or a **named successor** owns residual. Comment evidence without secrets.

Skip only trivial one-line ops the human explicitly scopes as untracked. **Never** invent a parallel tip or silent board state.

## Hard rules

1. **Never commit to `main`** without an explicit human request. Prefer feature / `execute-plan/<id>` / topic branches on top of **`working`** (the house integration tip). Former GI branch `grok-improvement` is superseded by `working` (see `docs/branch-law.md`).
2. **Never force-push `main`** (or force-like rewrite of protected defaults). No force-push helpers in v1 — treat force as human-only forever.
3. Prefer **worktree tools** for isolation: `git_worktree_add` → work → `git_worktree_remove` (dirty remove needs `confirm: true`) / `git_worktree_prune`.
4. Track multi-step work on **Projects** + ledger: `gh_project_list` / `gh_project_item_list` / `gh_project_item_add` / `gh_project_item_edit` / `gh_project_field_list` when available; soft-fail `auth_unavailable` without inventing success. Prefer the **before any change** issue workflow above before edits.
5. **Package VCS** for self-grown packages: re-promote archives prior local; recover with `get_tool` / `get_skill` (`list_versions`) and `revert_tool` / `revert_skill` (**reason required**). Never overwrite **bundled**.
6. Prefer **`grok_build` when present** for multi-file **implement / design / execute_plan** (and related modes). Else `search_replace` + `run` (or create-tool path for new callables). Do not thrash host path fishing. Prefer tool name **`grok_build`** with modes — never invent a second instrument or second auth. Grok Build should still recommend issue+branch structure (inspect → update/create → branch → work → board).
7. **Grant stops** on high-impact / destructive actions: dirty worktree remove, package revert of high-impact tools, anything merge/force-like, identity-critical changes, execute_plan without human-approved design. Stop → `speak` / `wait_user` (or operator grant) — do not auto-confirm. **No auto-merge. No operating-pin move** (pin convention: `docs/operating-pins.md`).
8. Path jail: stay inside allowed repo roots. Outside jail → honest refuse, not workarounds via `run`.
9. **Host vs guest paths:** instruments (`grok_build`, host `git_*` with jailed `repo`/`cwd`) need **host-absolute** paths under allowed roots. Sandbox FS tools / guest `run` use **guest** paths. Do not mix blindly — guest-relative `tmp/foo` is not a valid `grok_build` `cwd` (use `…/sandboxes/sandbox0/tmp/foo` or another host-absolute clone).
10. Secrets: `gh_*` use injected token only; raw secrets never in speak, ledger notes, or free-text. Instrument auth is PE `xai_oauth` access-only — never embed tokens in prompts.

## grok_build modes (when tool is present)

Single host tool **`grok_build`**. Call with `mode` (+ mode-conditional args). Schemas win over this table.

| Mode | When | Primary args | Default async |
|------|------|--------------|---------------|
| `prompt` | Short free-form / debug | `prompt` | **false** (sync) |
| `design` | Architecture / multi-PR design | `prompt` | **true** → `job_id` |
| `implement` | Multi-file feature work | `prompt`; `effort` 1–5 | **true** → `job_id` |
| `execute_plan` | Approved design stack | `design_doc_path` (jailed file) | **true** → `job_id` |
| `review` | Pre-PR / branch / PR review | `target` optional | **true** → `job_id` |
| `deep_research` | Optional research (may be experimental) | `prompt` = query | **true** → `job_id` |

**Product defaults for `execute_plan`:**

- Plain-git: `use_graphite=false` default (`--no-graphite` unless operator overrides)
- Base branch **`working`** (PE preflight: `working` or `origin/working` must resolve)
- Stale stacks (~10 days behind `working`): restack or extend with written reason
- Design doc path must exist inside path jail
- Human/PE approve design before spawn when on the H-spine (`self-improve`)

## Async jobs / background wake

1. Long modes (timeout > 15m) **default async** and return **`job_id`** with `status=running`.
2. **Do not** expect a multi-hour sync result in one moment.
3. After spawn: ledger note with `job_id` → optional `schedule_wake` (timer poll) and/or wait for a **`background` wake** with `payload.source=grok_build` → poll `grok_build` with **`job_id`**.
4. **Never invent** wake kind `instrument_job`. Completion uses closed kind **`background`** only.
5. On `status=needs_human` (`ok=true`): `speak` open questions → `wait_user`.
6. On terminal failure: read `error_reason`; soft-fail honestly; do not invent success.

## Common `error_reason` soft-fail manners

| `error_reason` / status | Manner |
|-------------------------|--------|
| `auth_unavailable` | Operator PE `xai_oauth` login; do not fake PR/instrument success |
| `auth_expired` | Re-auth; block instrument until fixed |
| `base_branch_missing` | Create/push `working`; do not silently base on `main` |
| `missing_prompt` | Supply `prompt` for the mode |
| `missing_design_doc_path` / `design_doc_missing` | Fix path; do not free-text a fake design doc |
| `mode_experimental` | deep_research (or similar) not enabled — skip or ledger |
| `mode_not_ready` | Jobs/reaper/runtime not ready — block mode |
| `usage_hard_stop` | Respect meter; rest / wait; no bypass |
| `timeout` | Note failure; re-spawn only with clear reason |
| `needs_human` (status) | `speak` / `wait_user` — not a hard invent-failure |
| `artifact_missing` | Do not claim design/review complete |
| `grok_not_found` | Host `grok` CLI missing — operator install |
| `grok_skills_unavailable` | Seeded GROK_HOME skills missing — operator/runtime |
| `job_not_found` | Bad/stale `job_id` — re-check ledger / runtime |
| `path_jail` / `not_a_repo` / `missing_repo` | Fix to **host-absolute** cwd/repo under allowed roots; guest-relative paths fail; no jail escape |
| `auth_unavailable` on `gh_*` | Note missing `gh_token` / grants; do not invent issue/PR/project success |

## Process

1. **Orient** the job: goal/task acceptance, base branch, whether a worktree is warranted, L/M/H if self-mod. Run **Before any change**: inspect issues → update/create → choose branch type.
2. **Isolate:** create or select a worktree/branch from **`working`**; never pile unrelated edits on `main`.
3. **Track:** open/update ledger task; Projects item with clear status fields; keep packaging label honest (exactly one of v0.1-gate|backlog|research).
4. **Implement:** prefer `grok_build` when present for multi-file implement/design/execute_plan; else focused file edits + tests via allowed tools. Async → ledger `job_id` → poll / background wake.
5. **Verify:** tests / dogfood acceptance; `git_status` / `git_diff` / `git_log` before commit; optional `grok_build` mode=`review`.
6. **Ship path:** `git_add` → `git_commit` on the feature branch; PR base **`working`**; `gh_pr_create` / `gh_pr_list` / `gh_pr_view` when remote review is needed; `gh_issue_*` / `gh_api` as escape hatch only when structured tools lack the op. **No auto-merge.**
7. **Package growth:** follow `create-tool` / `create-skill` (draft → verify tools only → promote). On broken local: list versions → `revert_*` with reason (stop for grant if high-impact).
8. **Cleanup:** remove disposable worktrees when done; **update issue/board** (status, residual, named successors); update ledger / Projects; `review-work` before goal close.

## Failure modes

| Signal | Action |
|--------|--------|
| `path_jail` / `not_a_repo` / refuse outside roots | Stop escape attempts; use host-absolute path under roots or ask operator |
| `auth_unavailable` on `gh_*` or instrument | Note missing token / OAuth; do not fake success |
| Dirty worktree remove without `confirm` | Re-check; only confirm when intentional; grant stop if unsure |
| Broken local tool/skill after promote | `get_*` + `list_versions` → `revert_*` with reason; do not rewrite bundled |
| git/`gh` / `grok_build` tools absent from schema | Ledger the gap; do not invent tool names or shell around the jail |
| Async job running | Poll `job_id` or wait for `background` wake (`source=grok_build`); do not sync-block |

## Quality / completion

Done when:

- Work is on a non-`main` branch/worktree with honest status, or
- PR/issue/project state matches reality (or soft-fail noted), and
- Package changes are promoted/reverted with recovery path clear, and
- Instrument jobs are polled to a terminal status or handed off with `job_id` on the ledger, and
- Destructive steps either completed with explicit confirm/grant or stopped for the human

## Out of scope

- Implementing the `grok_build` tool itself (rails only — call it when present)
- Force-push / automated merge to `main`
- Moving the operating pin without explicit human request
- Bypassing path jail or secret injection
- Reimplementing Grok skill loops in free-text
- Duplicating full git/`gh` / `grok_build` schema contracts in prose (schemas win)
