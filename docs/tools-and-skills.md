# Tools and skills

**Freeze for runtime shape:** [stretch-1.md](stretch-1.md).  
This page: **formats**, **base catalog**, **dogfood**, **create-tool safety**.

---

## Split

| | Skills | Tools |
|--|--------|--------|
| What | How to work (playbook) | What can run |
| Form | `SKILL.md` package | `TOOL.md` + `schema.json` + `runner.json` |
| Model | Load body when needed | Call with args |
| Security | Instructions only | Runtime executes under policy |

Goals, identity, and users are **state**, not skills/tools.

---

## On-disk layout

```text
$ELYRA_HOME/
  skills/{bundled,local}/<name>/SKILL.md
  tools/{bundled,local,drafts}/<name>/
    TOOL.md
    schema.json
    runner.json
    tests/          # required before promote
    impl/           # optional
```

Bundled and local use the **same** layout. Priority: project (later) → local → bundled.

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

### Lifecycle

```text
create-tool → tools/drafts/ (not callable; host-only; not visible via sandbox FS)
           → verify_tool (stage to sandboxes/sandbox0/tools/.verify/<name>/;
                          guest pytest when isolation on + pyenv_ready;
                          host pytest when ELYRA_SANDBOX=0)
           → promote_tool → tools/local/ (callable; staged into sandbox tools/ at run time)
```

| State | Callable? |
|-------|-----------|
| `drafts/` | No |
| `local/` / `bundled/` promoted | Yes (if in toolset) |

**Sandbox tree honesty:** product FS root is `{ELYRA_HOME}/sandboxes/sandbox0/` (guest `/workspace` when isolation is on). Sandbox `tools/` holds **staged runtime copies** (plus `.stage` / `.verify`) — not host `tools/drafts/`. Growth tools own drafts/promote on the host.

---

## create-tool safety (Stretch 1 required)

Runtime + skill must be **fail-closed**:

1. Names must not clash with existing tools (case-normalized)  
2. Write **only** under `drafts/`  
3. Package complete before verify  
4. Verify fails → stay draft  
5. Promote only after green verify  
6. Never overwrite bundled or existing promoted packages  
7. Never call draft tools  

The `create-tool` skill body should be a strict checklist matching the above so Gemma cannot half-ship. Runtime enforces gates even if the skill is ignored.

`create-skill` writes only skill packages (no new runners).

---

## Base tools

| Group | Tools |
|-------|--------|
| Sandbox | `read_file`, `list_dir`, `grep`, `search_replace`, `run` |
| Ledger | `create_goal`, `create_task`, `list_goals`, `get_goal`, `get_task`, `update_task`, `update_goal` |
| Social | `speak`, wait/questions, `schedule_wake` |
| Skills | `load_skill` (or host equivalent) |
| Growth | `install_tool_draft`, `verify_tool`, `promote_tool`, `install_skill` |
| Optional later | `search_tools`, `use_tool` |

`speak` failure → tool result with reason.  
`run` = guest exec when isolation on (fail closed `sandbox_unavailable:*` if unusable); host `Sandbox.run` only when `ELYRA_SANDBOX=0`. Not a host login shell.

### Operator sandbox install (isolation on)

Product default is isolation **on** when `ELYRA_SANDBOX` is unset. Without the optional extra, guest tools fail closed while chat continues — install early so create-tool does not look “broken”:

```bash
pip install -e '.[sandbox]'          # microsandbox SDK
./scripts/setup-microsandbox.sh      # doctor / optional --install-extra / --smoke
# hermetic tests / host-stub: export ELYRA_SANDBOX=0
```

Glass `/api/status` sandbox block: `mount_ready`, `pyenv_ready`, `ready` / `warming` / `client_unusable`.

---

## Base skills

| Skill | Job |
|-------|-----|
| `talk` | Social; may open goals; speak / wait |
| `plan-work` | Goal → tasks + acceptance |
| `do-work` | Act on a task |
| `review-work` | Check done claims (prefer before close) |
| `rest` | Idle honestly |
| `create-skill` | New playbook on disk |
| `create-tool` | Draft → verify → promote path |

---

## Dogfood rules

1. One skill format; one tool package format  
2. No capability without a package (tiny loader excepted)  
3. Draft ≠ promoted  
4. Verify before promote  
5. Catalog short; bodies/schemas on demand  
6. Skills never grant host power by prose  
7. Finite runners  

---

## From Grok (ideas only)

Outer tool loop, skill files, progressive tool discovery, create-skill, disk as truth.  
Not: TUI product, subagent factories, PR stacks as the mind.
