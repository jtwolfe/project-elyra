---
name: self-improve
description: Route self-mod and large engineering work by complexity (L/M/H) through grok_build modes and github-workflow. Use when improving Elyra/product code, multi-file features, or execute-plan stacks.
---

# Self-improve

Person owns goals, identity, and moments. **Grok Build is an instrument** via the single tool name **`grok_build`** (modes on one tool). Never invent a second auth ceremony, second instrument tool, or free-text fake of design/implement/execute-plan.

Use **`github-workflow`** for branch/worktree/Projects/package-VCS discipline. Use this skill for **L/M/H routing** and the **H-spine**.

## When to use

- Self-mod, multi-file product work, or program-scale engineering on Elyra/repo code
- Choosing among `grok_build` modes (`prompt`, `design`, `implement`, `execute_plan`, `review`, `deep_research`)
- Execute-plan stacks, design docs, or multi-PR architecture work
- Orient shows improvement / feature work that is larger than a single local edit

## When not to use

- Pure social wake → `talk` first
- Single ready sandbox task with no self-mod / multi-file scope → `do-work`
- Goal has no tasks yet → `plan-work`
- Closing or verifying done claims only → `review-work`
- `grok_build` missing from the tool schema list → do not invent calls; ledger the gap; fall back to PE tools + `github-workflow` rails
- Implementing the `grok_build` tool itself (out of scope for this playbook)

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. Ledger orient: `get_task` / `get_goal` / `list_goals` — restate acceptance and scope when orient is thin
2. Classify **L / M / H** (decision tree below) and note the tier on the task if useful
3. Then the first instrument or git posture call:
   - L → PE tools (`search_replace`, `run`, `git_*`) and/or `grok_build` mode `prompt` / light `implement`
   - M/H → `git_status` / `git_worktree_list` (or `load_skill` name `github-workflow`) before multi-file instrument work
   - If polling an in-flight job → `grok_build` with `job_id` only

## Async instrument rule

Long modes **return `job_id`** (async by default when wall timeout > 15m):

| Mode | Default async | Notes |
|------|---------------|--------|
| `prompt` | **false** (sync) | Short free-form; do not use for multi-hour work |
| `design` | **true** | Returns `job_id`; harvest `artifacts/design.md` |
| `implement` | **true** | `effort` 1–5 inside skill flags only |
| `execute_plan` | **true** | Needs `design_doc_path`; base **`working`** |
| `review` | **true** | Local / branch / PR target |
| `deep_research` | **true** | May be **experimental** |

Rules:

1. **Do not** expect a multi-hour sync tool result in one moment. Do not block the moment waiting 6h.
2. After spawn: **ledger note with `job_id`** → optional `schedule_wake` (timer poll) and/or wait for a **`background` wake** with `payload.source=grok_build` → poll `grok_build` with **`job_id`**.
3. **Never invent** wake kind `instrument_job`. Completion wakes use closed kind **`background`** only.
4. On `status=needs_human` (`ok=true`, not a hard tool error): `speak` open questions → `wait_user` (H-spine approve).
5. On `mode_experimental` / `mode_not_ready`: block honestly; do not free-text fake design or research.
6. Prefer tool name **`grok_build`** with a `mode` argument. Never invent a second instrument or second OAuth path.

## cwd law (host-absolute before spawn)

`grok_build` is **host-jailed**. Before every spawn (not poll), pass **`cwd` as a
host-absolute path** to a git repo under `allowed_repo_roots`:

1. Prefer a path already proven by `git_status` / `git_worktree_list` / worktree
   add (host absolute under the jail).
2. For sandbox clones: map guest paths (`tmp/foo`) to the **host** sandbox root
   (e.g. `…/sandboxes/sandbox0/tmp/foo`) — do **not** pass guest-relative
   `tmp/foo` alone (fails `not_a_repo` / `path_jail` against project root).
3. Guest `run` FS space ≠ host instrument path space. Never thrash re-spawns
   with guest-relative cwd hoping the jail will rewrite it (it will not).
4. On `path_jail` / `not_a_repo` / `missing_repo`: fix to a host-absolute jailed
   git repo; do not invent success or escape the jail.

## L / M / H decision tree

### L — Low (well-specified, local, under ~2 files or pure ledger/sandbox)

- Prefer PE tools: `search_replace`, `run`, `git_*`, create-tool path when a callable is missing
- Optional: `grok_build` mode=`prompt` (sync) or mode=`implement` effort=1
- **Skip** `design` / `execute_plan`
- Still never commit to `main` without explicit human request; prefer a short feature branch on **`working`**

### M — Medium (needs investigation, multi-file, unclear edges)

- **`github-workflow`**: worktree/branch off **`working`** (not `main`; former GI tip `grok-improvement` is superseded — see `docs/branch-law.md`)
- `grok_build` mode=`implement` effort=2–3 (**async**) → poll `job_id`
- `review-work` + `grok_build` mode=`review` before PR when the change warrants it
- **No `execute_plan`** unless a design doc already exists and was approved

### H — High (wide impact, architecture, multi-PR, security/auth, branch law)

**H-spine** — do not skip without human grant:

1. Optional: `deep_research` if enabled (else skip / note `mode_experimental`)
2. `grok_build` mode=`design` (async) → poll → artifacts/design.md
3. If `needs_human` or design ready: `speak` / `wait_user` / grant **approve**
4. `execute_plan` plain-git base=**`working`** (async) with jailed `design_doc_path`
5. `review` on stack/PRs; `github-workflow` Projects + ledger
6. **Never** merge `main` / move operating pin without **explicit human** action

```
Work item
  → Orient ledger + repo posture
  → Complexity?
       L → PE tools and/or prompt / implement e=1
       M → worktree + implement async + poll + review
       H → [deep_research?] → design async + poll
            → needs_human? speak/wait_user
            → human approve design?
            → execute_plan async base=working → poll job_id
            → review + github-workflow
  → ledger note / review-work
```

## Hard rules

1. **Never invent `grok_build` success.** Read `error_reason` / `status`; soft-fail honestly.
2. **Never put tokens** (OAuth, API keys) in `speak`, ledger notes, or free-text.
3. Base branch **`working`**; never commit to `main` without explicit human request. No auto-merge. No operating-pin move.
4. **Grant stops:** `execute_plan`, high-impact package revert, force-like ops, identity-critical changes → stop → `speak` / `wait_user` (or operator grant). Do not auto-confirm.
5. `auth_unavailable` / `auth_expired` → `speak` operator for PE `xai_oauth` login; **block** the instrument task. Do not invent a second auth.
6. Prefer exact tool name `grok_build` and skill names `self-improve` / `github-workflow` (hyphenated).
7. Path jail and secrets policy still apply — instruments do not bypass them.
   **`cwd` for `grok_build` must be host-absolute** under allowed roots (see cwd law).
8. On `usage_hard_stop` / `timeout`: ledger + optional speak; do not thrash re-spawns.

## Common soft-fail manners (`error_reason` / status)

| Signal | Action |
|--------|--------|
| `auth_unavailable` / `auth_expired` | Stop instrument; operator OAuth; do not fake success |
| `base_branch_missing` | Note create/push `working`; do not base on `main` silently |
| `missing_prompt` / `missing_design_doc_path` / `design_doc_missing` | Fix args or block; no free-text design substitute |
| `mode_experimental` / `mode_not_ready` | Block mode; ledger gap; do not invent |
| `usage_hard_stop` | Rest / wait budget; do not bypass meter |
| `timeout` / `job_not_found` | Note failure; re-spawn only with clear reason |
| `artifact_missing` | Do not claim design/review done |
| `grok_not_found` / `grok_skills_unavailable` | Operator host install / seed issue |
| `path_jail` / `not_a_repo` / `missing_repo` | Use **host-absolute** cwd under allowed roots; guest-relative `tmp/…` is wrong; no jail escape |
| `status=needs_human` | `speak` open questions → `wait_user` (ok path, not hard error) |
| Tool absent from schema | Ledger gap; PE tools + github-workflow only |

## Process

1. **Orient** goal/task acceptance and complexity tier (L/M/H).
2. **Isolate** (M/H): worktree/branch via `github-workflow` off **`working`**.
3. **Route** to PE tools and/or `grok_build` mode per tree above.
4. **Async:** spawn → ledger `job_id` → background wake and/or timer → poll until terminal status.
5. **H human gate:** design approve before `execute_plan`.
6. **Verify:** tests/dogfood; `review` / `review-work` before close claims.
7. **Ship path:** feature branch commits + PR via github-workflow; no auto-merge to `main`/`working` tip hijack; no pin move.

## Quality / completion

Done when:

- L: local change verified with evidence, or honestly blocked, or
- M: implement/review complete on a non-`main` branch/worktree with honest status, or
- H: spine steps completed or stopped at a grant/human gate with clear ledger notes, and
- No invented instrument success; async jobs recorded and polled or handed off

## Out of scope

- Implementing the `grok_build` tool, reaper, or auth provider itself
- Becoming Grok; reimplementing design/implement/execute-plan loops in free-text
- Auto-merge; silent operating-pin move; guest OAuth
- Inventing wake kinds, second instruments, or second auth ceremonies
- Force-push / rewrite of protected tips
