# Tools and skills

**Freeze for runtime shape:** [stretch-1.md](stretch-1.md).  
This page: **formats**, **base catalog**, **package VCS**, **capability-growth families** (search, browser, secrets, git/gh), **dogfood**, **create-tool safety**, **identity tools/skills**.

Identity store design (layout, gates, work-origin USER): [design-identity-self-other-multi-user.md](design-identity-self-other-multi-user.md) · life-shell rules: [time-and-identity.md](time-and-identity.md).

**Capability growth (shipped on this branch):** product design [design-capability-growth-search-browse-vcs-secrets.md](design-capability-growth-search-browse-vcs-secrets.md) · execute-plan contract [design-capability-growth-implementation-plan.md](design-capability-growth-implementation-plan.md).

---

## Split

| | Skills | Tools |
|--|--------|--------|
| What | How to work (playbook) | What can run |
| Form | `SKILL.md` package | `TOOL.md` + `schema.json` + `runner.json` |
| Model | Load body when needed | Call with args |
| Security | Instructions only | Runtime executes under policy |

Goals and identity digests are **state**. Identity mutation uses a **thin tool trio** + skills for process — not a fused “update who” tool, and not skills granting host power by prose alone.

Judgment skills (`web-research`, `browse`, `github-workflow`, growth/identity playbooks) encode multi-step procedure and stop conditions. Tools stay thin. **Agency-preserving:** do not force every action through a skill.

---

## On-disk layout

```text
$ELYRA_HOME/
  skills/
    bundled/<name>/SKILL.md          # immutable
    local/<name>/
      SKILL.md
      versions/<version_id>/…        # archived payload (no nested versions/)
      .versions_meta.json
    drafts/<name>/SKILL.md           # not loadable by catalog
  tools/
    bundled/<name>/…                 # immutable
    local/<name>/
      TOOL.md, schema.json, runner.json, tests/, impl/, …
      versions/<version_id>/…        # full package payload snapshot
      .versions_meta.json
    drafts/<name>/…                  # not callable; host-only
```

Bundled and local use the **same** package shape. Priority: project (later) → local → bundled.  
Drafts are **never** in the skill catalog or tool registry as callable packages.

Identity *state* (not tool packages) lives under `data/identity/` and `data/users/<id>/` — see [time-and-identity.md](time-and-identity.md).

Named secrets (not tool packages) live under `data/secrets/` — see [Secrets](#secrets-store--inject--glass) below. Provider API key (`xai_api_key`) coexists there and is reserved.

---

## Skill package

```markdown
---
name: do-work
description: Execute the next ready task. Use when working a task or /do-work.
---

# Do work
1. Load task from ledger
2. Act with tools
3. Stop when accepted, blocked, or need user
```

- One skill per directory  
- Catalog shows **name + short description** only  
- Full body on activate (`load_skill`)  
- Wire success is framed as **PLAYBOOK ACTIVE** (not a raw JSON blob); playbooks open with **First tool call (mandatory)** (work + talk) or **First action** (rest: honest stop with no tools is OK). Follow that section with tools — do not free-text re-plan.  

---

## Tool package

**TOOL.md** — name, description, kind (`read` | `mutate` | `speak` | `control` | `integrate`).  
**schema.json** — JSON Schema for arguments.  
**runner.json** — how to execute (allowlisted kinds only).

Stretch 1 runners (finite; **implemented**):

| runner | Meaning | Isolation **on** (product default) | Isolation **off** (`ELYRA_SANDBOX=0`) |
|--------|---------|------------------------------------|--------------------------------------|
| `builtin` | Host code (humans) | Host | Host |
| `sandbox_shell` | Argv in sandbox; model args via env **`ELYRA_TOOL_ARGS`** → guest `tmp/elyra_tool_args_*.json` | Guest exec only; fail closed if sandbox unusable | Host stub under `sandboxes/sandbox0/` |
| `sandbox_python` | `impl/` module `function(args)` in sandbox | Guest exec only; fail closed if sandbox unusable | Host stub under `sandboxes/sandbox0/` |

No host `eval` of model code. New runner kinds = rare code change.  
Invalid `runner.json` shapes surface as `invalid_runner:*` on verify/promote.  
Full isolation design: [grok-improvement-plan/harness-sandbox-fitness.md](grok-improvement-plan/harness-sandbox-fitness.md).

Host capability-growth tools (search, browser, secrets, git/gh, package VCS) are **`builtin`** runners — they need host network, Chromium, secrets inject, or host repo paths. Guest packages never receive secret env or host git worktrees.

### Lifecycle (tools)

```text
create-tool → tools/drafts/ (not callable; host-only; not visible via sandbox FS)
           → verify_tool (stage to sandboxes/sandbox0/tools/.verify/<name>/;
                          guest pytest when isolation on + pyenv_ready;
                          host pytest when ELYRA_SANDBOX=0)
           → promote_tool → tools/local/ (callable; staged into sandbox tools/ at run time)
                │
                └─ if local/<name>/ already exists:
                     archive payload → local/<name>/versions/<version_id>/
                     then atomic whole-tree replace (never hollow live name)
```

| State | Callable? |
|-------|-----------|
| `drafts/` | No |
| `local/` / `bundled/` promoted | Yes (if in toolset) |

**Sandbox tree honesty:** product FS root is `{ELYRA_HOME}/sandboxes/sandbox0/` (guest `/workspace` when isolation is on). Sandbox `tools/` holds **staged runtime copies** (plus `.stage` / `.verify`) — not host `tools/drafts/`. Growth tools own drafts/promote on the host.

### Lifecycle (skills)

```text
install_skill_draft → skills/drafts/<name>/SKILL.md (not in catalog)
                   → promote_skill → skills/local/<name>/
                        │
                        └─ if local exists: archive → versions/<version_id>/, then replace

install_skill (compat) → write draft content then promote (archive-on-overwrite when local exists)
```

Skill promote gates: `SKILL.md` present, frontmatter `name` + `description`, size cap, content hash — **no** sandbox pytest.

---

## Package VCS (archive-on-promote)

Identity-aligned recovery for **local** tool and skill packages. Design detail: [design-capability-growth-implementation-plan.md](design-capability-growth-implementation-plan.md) §1–2.

### Semantics

| Rule | Behavior |
|------|----------|
| First promote | Draft → `local/<name>/` (atomic rename when possible) |
| Re-promote when local exists | **Archive** current payload → `versions/<version_id>/`, then replace local with draft |
| Bundled name | **Always refuse** (`refuses_overwrite_bundled`) — no recover-via-overwrite for bundled |
| `force` arg | Still rejected (`force_not_allowed`) |
| Nested archives | Never: archive payload excludes `versions/`, `.versions_meta.json`, `__pycache__` |
| GC | Cap **50** versions per package (oldest dropped) |
| Hollow package | **Forbidden** — whole-tree rename swap; live name is complete, fully old, fully new, or temporarily absent |

**Migration note:** older dogfood homes that relied on “promote once only / refuses local overwrite” now **gain recovery** instead of refusal. Re-promoting a local tool is expected and archives the previous payload.

### Tools

| Tool | Role |
|------|------|
| `get_tool` | Inspect `current` / `draft` / `version`; optional `list_versions` (meta only, truncated previews) |
| `revert_tool` | Restore a `version_id`; **reason required** (min length enforced); archives current as `pre_revert:…` first; registry reload |
| `promote_tool` | Existing path; may return `archived_version_id` on re-promote |
| `get_skill` | Same pattern for skills |
| `install_skill_draft` | Write draft only |
| `promote_skill` / `revert_skill` | Archive-on-promote / restore for skills; catalog reload |

`install_tool_draft` / `verify_tool` unchanged (verify still required before tool promote).

---

## create-tool safety (Stretch 1 required)

Runtime + skill must be **fail-closed**:

1. Names must not clash with existing tools (case-normalized)  
2. Write **only** under `drafts/`  
3. Package complete before verify  
4. Verify fails → stay draft  
5. Promote only after green verify  
6. Never overwrite **bundled** packages; re-promote of local packages archives the previous payload under `versions/` (recover with `get_tool` / `revert_tool`)  
7. Never call draft tools  

The `create-tool` skill body is a strict checklist matching the above (plus **Package VCS recovery**). Runtime enforces gates even if the skill is ignored.

`create-skill` writes only skill packages (no new runners). Local skill re-promote uses the same archive/revert culture via `get_skill` / `revert_skill`.

---

## Base tools

| Group | Tools |
|-------|--------|
| Sandbox | `read_file`, `list_dir`, `grep`, `search_replace`, `run` |
| Ledger | `create_goal`, `create_task`, `list_goals`, `get_goal`, `get_task`, `update_task`, `update_goal` |
| Social | `speak`, wait/questions, `schedule_wake` |
| Skills | `load_skill` |
| Growth | `install_tool_draft`, `verify_tool`, `promote_tool`, `install_skill` |
| Package VCS | `get_tool`, `revert_tool`, `get_skill`, `install_skill_draft`, `promote_skill`, `revert_skill` |
| Identity | `get_identity`, `draft_identity`, `promote_identity` |
| Search | `web_search` (optional extra — see below) |
| Browser | `browser_session_open`, `browser_session_close`, `browser_goto`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_fill`, `browser_get_text`, `browser_wait` |
| Secrets | `secrets_list`, `secrets_set`, `secrets_delete` |
| Git | `git_status`, `git_diff`, `git_log`, `git_add`, `git_commit`, `git_branch`, `git_checkout`, `git_worktree_add`, `git_worktree_list`, `git_worktree_remove`, `git_worktree_prune` |
| GitHub | `gh_auth_status`, `gh_pr_create`, `gh_pr_list`, `gh_pr_view`, `gh_issue_create`, `gh_issue_list`, `gh_api`, `gh_project_list`, `gh_project_item_list`, `gh_project_item_add`, `gh_project_item_edit`, `gh_project_field_list` |
| Optional later | `search_tools`, `use_tool`, `web_fetch`, browser screenshot → media |

`speak` failure → tool result with reason.  
`run` = guest exec when isolation on (fail closed `sandbox_unavailable:*` if unusable); host `Sandbox.run` only when `ELYRA_SANDBOX=0`. Not a host login shell.

### Search (`web_search`)

- Host builtin; backend via optional `elyra[search]` (`ddgs`).
- Structured results: `{title, url, snippet, …}` — no HTML dumps.
- Fail-closed without extra: `search_unavailable` + install hint (`pip install -e '.[search]'`).
- Empty / rate-limit / timeout surface honest `error_reason` or `warning` — **never invent** results.
- Judgment skill: `web-research` (multi-query, cite, stop, optional ledger goal).

### Browser (Playwright primitives)

- Host builtins + process-local session manager (max concurrent sessions; bound to moment).
- Primary interaction: accessibility **snapshot + refs** (`browser_snapshot` → `browser_click` / type / fill by `ref`).
- Lifecycle: `browser_session_close`; host also closes sessions on moment end (success **and** fail) and on supervisor shutdown.
- Fail-closed:
  - package missing → `browser_unavailable` (`pip install -e '.[browser]'` then `playwright install chromium`)
  - package present, Chromium missing → `chromium_unavailable` (`playwright install chromium`)
- Screenshots → media store: **deferred** (text/snapshot first).
- Judgment skill: `browse`.

### Secrets (store, inject, Glass)

**Layout:**

```text
$ELYRA_HOME/data/secrets/
  xai_api_key              # reserved — llm.auth (unchanged)
  meta.json                # names, grants, timestamps — never values
  values/<name>            # mode 0600 raw values
```

| Surface | Role |
|---------|------|
| `secrets_list` | Metadata + grants only — **never values** |
| `secrets_set` | Write value; optional `grants` (tool names); result omits value; chain args redacted for secret write tools |
| `secrets_delete` | Delete named secret |
| Glass `/api/secrets*` | Operator CRUD + grants; never echoes values |
| Inject (not model-facing) | Registry attaches call-local `secret_env` for tools in the host grant map; guest/host-stub **ignore** it |

**Rules (non-negotiable):**

- Secrets **never** in model context, moment tape, or status JSON as raw values.
- `gh_*` tools soft-fail `auth_unavailable` when token missing/not granted — registry does not invent that error.
- Prefer Glass for operator writes; model `secrets_set` is scrubbed in the chain message builder.
- Node-local file store in v1 (no multi-machine sync). Optional OS keyring backend is design-only / not required for dogfood.

### Git + GitHub (structured host builtins)

- **Path jail:** `tools.allowed_repo_roots` in `elyra.toml` (tuple of strings). Empty sentinel → use-site defaults `[project_root(), home]`. Paths outside jail, `..`, and symlink escape are refused.
- Local git tools are argv wrappers (not a free-form shell). Worktree lifecycle is first-class: add / list / remove (dirty remove needs `confirm: true`) / prune.
- GitHub tools use injected `GH_TOKEN` from secrets when granted; soft-fail without inventing success.
- Destructive / high-impact actions: stop for human grant when the skill says so (`github-workflow`); never force-push `main` in v1.
- Deferred v1: `git_stash`, `gh_repo_*`, merge/force automation.

### Identity tools (thin trio)

Parallel to create-tool’s draft→promote culture, **without** a verify package step (identity is prose + meta; gates are social/host):

| Tool | Kind | Role |
|------|------|------|
| `get_identity` | read | Current / draft / version body + meta; optional `list_versions`; users get host-computed `should_name_nudge` |
| `draft_identity` | mutate | Write **draft only** (body and/or `meta_patch`); current unchanged |
| `promote_identity` | mutate | Draft → current after host gates; archives previous current into `versions/` |

**Rules:**

- **Draft never injects** into orient — only promoted `current.md`.  
- **Self promote:** hard gate (operator grant token; Glass Identity panel is primary).  
- **User promote:** medium gate (social wake + reason + target `user_id` == session user; Glass admin may promote other profiles).  
- **`full_name`:** set/change requires `force_full_name: true` in draft meta (operational; never stored in `draft_meta`). Prefer living **`goes_by`**.  
- **No** `patch_identity` / `patch_user`. No list-users tool (user discovery is Glass/session).  
- Identity writes are **host builtins**, not sandbox FS.

Process playbooks: skills `review-identity` and `update-identity` below. Orient USER policy (work-origin, not blind operator): [time-and-identity.md](time-and-identity.md).

### Operator install (optional extras)

Product default is isolation **on** when `ELYRA_SANDBOX` is unset. Capability-growth extras are **optional**; missing deps fail closed (chat continues).

```bash
# Sandbox isolation (guest run / verify_tool)
pip install -e '.[sandbox]'
./scripts/setup-microsandbox.sh      # doctor / optional --install-extra / --smoke
# hermetic tests / host-stub: export ELYRA_SANDBOX=0

# Native web search
pip install -e '.[search]'

# Playwright browser primitives
pip install -e '.[browser]'
playwright install chromium          # required after browser extra

# Combined research surface (search + browser extras, when present as a meta-extra)
# Otherwise install both:
pip install -e '.[search,browser]'
playwright install chromium
```

Glass `/api/status` sandbox block: `mount_ready`, `pyenv_ready`, `ready` / `warming` / `client_unusable`.

Optional VCS roots:

```toml
# elyra.toml
[tools]
verify_timeout_seconds = 120
# empty / omitted → project root + home at use site
allowed_repo_roots = ["/path/to/project-elyra"]
```

---

## Base skills

| Skill | Job |
|-------|-----|
| `talk` | Social; may open goals; speak / wait |
| `plan-work` | Goal → tasks + acceptance |
| `do-work` | Act on a task |
| `review-work` | Check done claims (prefer before close); package VCS recovery when growth claims break |
| `rest` | Idle honestly |
| `create-skill` | New playbook on disk; local skill VCS recovery notes |
| `create-tool` | Draft → verify → promote path; package VCS recovery |
| `review-identity` | Read/compare self or user identity (current/draft/versions); speak; **never** promote |
| `update-identity` | Draft identity changes; self stops for Glass grant; user may promote under medium gate |
| `web-research` | Multi-query search, cite sources, honest stop; may open ledger work |
| `browse` | Snapshot + ref click loop; session hygiene |
| `github-workflow` | Branch / worktree / Projects discipline; package VCS; grant stops on destructive actions |

### Identity skills (process)

| Skill | First tool path (summary) |
|-------|---------------------------|
| `review-identity` | `get_identity` → optional second get → `speak` findings. No `draft_identity` / `promote_identity`. |
| `update-identity` | `get_identity` → `draft_identity` → **self:** speak + stop for grant; **user:** `promote_identity` when medium gate + session match. Soft name-nudge via `should_name_nudge` / `record_name_nudge`. |

Exact checklists live in `skills/bundled/review-identity/SKILL.md` and `skills/bundled/update-identity/SKILL.md`.

### Judgment skills (capability growth)

| Skill | Depends on | Notes |
|-------|------------|-------|
| `web-research` | `web_search`, ledger | No invent on empty/rate-limit; cite; stop conditions |
| `browse` | `browser_*` | Re-snapshot after navigation; close sessions; unavailable → honest stop |
| `github-workflow` | `git_*`, `gh_*`, package VCS | Prefer feature/execute-plan branches; never force-push main; worktrees; Projects; grant stops |

Phase 1 `grok_build` tool is **not** in this surface — skills teach rails only.

---

## Dogfood rules

1. One skill format; one tool package format  
2. No capability without a package (tiny loader excepted)  
3. Draft ≠ promoted (tools, skills, **and** identity digests)  
4. Verify before promote (**callable tools**); skill promote uses SKILL.md gates; identity promote uses host gates  
5. Catalog short; bodies/schemas on demand  
6. Skills never grant host power by prose  
7. Finite runners  
8. Linked work wake → USER from goal/task `created_in_context` when present; autonomous → empty USER (not operator)  
9. Re-promote of local packages **archives** (expected); recover with get + revert + reason  
10. Optional extras fail closed — never crash the supervisor for missing search/browser/token  
11. Secrets never appear as raw values in chain args, tool results, moments, or Glass list APIs  

---

## Capability-growth dogfood checklist (operator)

Cumulative procedure for the full program (product design + implementation plan PR9). Check off on a live home after install.

### Package VCS

- [ ] Promote a tool when `tools/local/<name>/` already exists → previous payload archived and listable via `get_tool` (`list_versions`); archives are not nested forever (GC cap 50)
- [ ] `revert_tool` restores a chosen version; registry updates; **reason required**
- [ ] Promote still **refuses** bundled overwrite
- [ ] Skill path: draft → `promote_skill` archives local if present; `get_skill` / `revert_skill` recover

### Search + research

- [ ] `web_search` returns structured results; empty/rate-limit are honest; model does not invent hits
- [ ] Without `elyra[search]`: clear `search_unavailable` (no crash loop)
- [ ] `web-research`: multi-query + cites + stop; can open a ledger goal

### Browser

- [ ] Snapshot + click-by-ref headless; session cleans up on moment end
- [ ] Missing Playwright package → `browser_unavailable`; missing Chromium → `chromium_unavailable`
- [ ] `browse` skill: re-snapshot after navigation; honest stop when unavailable

### Secrets + GitHub

- [ ] Set a secret in Glass (or `secrets_set`); grant `gh_*` tools as needed
- [ ] `gh_auth_status` / a read-only `gh_*` succeeds with token; soft-fails `auth_unavailable` without
- [ ] Raw secret **never** in moment tape, status, or tool results
- [ ] Project item list/add/edit with token; soft-fail without inventing success

### Git / worktrees / workflow

- [ ] `git_worktree_add` / list / remove (dirty needs `confirm`); path outside jail refused
- [ ] `github-workflow`: sensible stops for grants; no force-push main; package VCS recovery path known

### Regression

- [ ] Existing create-tool / identity / usage / effort paths still green
- [ ] `pytest -m 'not llm'` green on the integration tip

---

## Design pointers

| Doc | Role |
|-----|------|
| [design-capability-growth-search-browse-vcs-secrets.md](design-capability-growth-search-browse-vcs-secrets.md) | Product design (v2): search, browser, package VCS, secrets, workflow skills |
| [design-capability-growth-implementation-plan.md](design-capability-growth-implementation-plan.md) | Execute-plan PR DAG, normative promote algorithm, security gates |
| [design-identity-self-other-multi-user.md](design-identity-self-other-multi-user.md) | Identity draft/promote + multi-user prep (parallel version culture) |
| [grok-improvement-plan/harness-sandbox-fitness.md](grok-improvement-plan/harness-sandbox-fitness.md) | Sandbox runners, isolation, honesty |
| [stretch-1.md](stretch-1.md) | Runtime freeze for Stretch 1 shape |

---

## From Grok (ideas only)

Outer tool loop, skill files, progressive tool discovery, create-skill, disk as truth.  
Not: TUI product, subagent factories, PR stacks as the mind.
