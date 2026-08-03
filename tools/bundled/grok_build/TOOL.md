---
name: grok_build
description: >-
  Host instrument that runs Grok Build headless (design, implement, execute_plan,
  deep_research, review, or free-form prompt). Not a second presence. Requires
  PE xai_oauth. Prefer self-improve / github-workflow skills for routing.
kind: integrate
---

# grok_build

Host-only Grok Build **instrument**. Elyra remains the person; this tool brokers
the host `grok` CLI (`grok -p …`) under isolated `GROK_HOME` with a live PE
auth provider. Guest runners **never** receive OAuth or this tool's secrets.

## Arguments

- **`mode`** (required): `prompt` | `design` | `implement` | `execute_plan` |
  `deep_research` | `review`
- **`prompt`**: free-form / design / implement / research query (mode-dependent)
- **`design_doc_path`**: required for `execute_plan` (path-jailed file)
- **`effort`**: implement 1–5 (skill flag **inside** `-p` only — never CLI `--effort`)
- **`async`**: background job; **defaults true** when mode timeout > 15m
  (`design`, `implement`, `execute_plan`, `review`, `deep_research`); **false**
  for `prompt`. Pass `async=false` only for operator/debug on long modes.
- **`job_id`**: poll a prior async job (poll-only when set)
- **`base_branch`**: execute_plan preflight branch (default **`working`**)
- **`cwd`**: **host-absolute** path to a git repo under `allowed_repo_roots`
  (typically PE `project_root` or a clone under
  `…/sandboxes/sandbox0/tmp/…`). Guest-relative paths like `tmp/foo` are
  **not** host paths — they resolve against project root and fail
  `not_a_repo` / `path_jail`. Guest `run` FS space ≠ host instrument path space.
- **`target`**: review target (`local` | branch | PR number/URL)
- **`max_turns`**: optional grok `--max-turns`

## Auth & secret plane

- Auth: PE `xai_oauth` access-only via `resolve_access_token_for_tool` preflight
  + live `elyra.instrument.auth_provider` mid-run refresh.
- **Fail-closed** when OAuth is missing / reauth required → `auth_unavailable`.
- **Never** assign OAuth into `ctx.extras["secret_env"]` (not on the grant-based
  secret plane). Guest never merges `secret_env` either — host-builtin only.
- Results/logs redact known auth values; refresh_token never leaves `data/secrets`.

## Async / jobs

- Long modes default **async**: spawn returns `job_id` + `status=running`.
- Poll with `job_id` (mode optional on poll). Reaper finalizes and may enqueue a
  **`background`** wake with `payload.source=grok_build` (never invent
  `instrument_job`).
- Sync only for `prompt` (or explicit `async=false`).

## execute_plan preflight

- Base branch must resolve (`working` or `origin/working`) → else
  `base_branch_missing`.
- `design_doc_path` must be a jailed existing file → else `design_doc_missing` /
  `missing_design_doc_path`.

## deep_research

Experimental until headless spike signs a contract. May return
`mode_experimental` while `DEEP_RESEARCH_EXPERIMENTAL` is true.

## Result

Success / running payload (normalized):

```json
{
  "ok": true,
  "mode": "design",
  "run_id": "…",
  "status": "completed|running|needs_human|failed|interrupted",
  "summary": "…redacted…",
  "open_questions": [],
  "artifacts": [{"kind": "design_doc", "path": "…"}],
  "usage": {"total_tokens": 0, "recorded": false},
  "exit_code": 0,
  "job_id": "…",
  "log_path": "…/result.json"
}
```

`status=needs_human` is **ok=true** (not a hard tool error) — route to speak /
wait_user.

## Fail-closed `error_reason` catalog

| `error_reason` | Meaning |
|----------------|---------|
| `invalid_args` | Bad/missing mode or args |
| `missing_prompt` | prompt required for mode |
| `missing_design_doc_path` | execute_plan without path |
| `design_doc_missing` | design doc path not a file |
| `base_branch_missing` | `working` (or base_branch) not in repo |
| `missing_repo` | no resolvable jailed cwd/repo — pass host-absolute cwd |
| `path_jail` / `not_a_repo` / `invalid_path` | VCS path jail; guest-relative cwd is a common footgun |
| `auth_unavailable` | OAuth missing / reauth |
| `auth_expired` | mid-run / finalize auth death |
| `grok_not_found` | host `grok` binary missing |
| `grok_skills_unavailable` | seeded skills missing under GROK_HOME |
| `mode_experimental` | deep_research not enabled |
| `mode_not_ready` | long mode without jobs/reaper readiness |
| `usage_hard_stop` | usage meter refuses call |
| `timeout` | wall timeout / process group kill |
| `nonzero_exit` / `skill_failed` | grok failed |
| `artifact_missing` | design/review artifact not harvested |
| `target_ambiguous` | bad review target |
| `job_not_found` | unknown job_id on poll |

## cwd path-jail law (host-absolute)

`grok_build` is **host-jailed**. `cwd` must be a **host-absolute** git repo under
`allowed_repo_roots`. Do **not** pass guest-relative paths from sandbox tools
(`tmp/repo`, `./oneuptime`) alone — those resolve against PE project root and
typically return `not_a_repo` or `path_jail`.

Examples that work (shape, not literal):

- `/home/…/project-elyra` (PE project root when it is a git repo under roots)
- `/home/…/sandboxes/sandbox0/tmp/oneuptime` (sandbox clone as seen on the **host**)

Guest `run` / sandbox FS paths and host instrument paths are different spaces.
Resolve the host path from prior `git_*` evidence, worktree listing, or known
sandbox host mapping **before** spawn. There is no silent guest→host rewrite.

Never invent success. Never put tokens in speak/ledger. Prefer skills
`self-improve` / `github-workflow` for routing judgment.
