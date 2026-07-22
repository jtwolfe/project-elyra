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
- Full body on activate  

---

## Tool package

**TOOL.md** — name, description, kind (`read` | `mutate` | `speak` | `control` | `integrate`).  
**schema.json** — JSON Schema for arguments.  
**runner.json** — how to execute (allowlisted kinds only).

Stretch 1 runners (finite):

| runner | Meaning |
|--------|---------|
| `builtin` | Host code (humans) |
| `sandbox_shell` | Argv in sandbox |
| `sandbox_python` | `impl/` in sandbox |

No host `eval` of model code. New runner kinds = rare code change.

### Lifecycle

```text
create-tool → tools/drafts/ (not callable)
           → verify_tool (sandbox tests)
           → promote_tool → tools/local/ (callable)
```

| State | Callable? |
|-------|-----------|
| `drafts/` | No |
| `local/` / `bundled/` promoted | Yes (if in toolset) |

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
`run` = **sandbox only**, not host shell.

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
