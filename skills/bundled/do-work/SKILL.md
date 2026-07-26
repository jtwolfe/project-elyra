---
name: do-work
description: Execute one ready task with tools until accepted, blocked, or the user is needed. Use on task_ready wakes and when orient shows ready work.
---

# Do work

This skill is the execution stage for **one** ready task. Prefer tools over silent free-text exits when there is real work; prefer honest **blocked** over fake progress.

## When to use

Use this skill when:

- Why-now or orient shows a **ready** (or clearly executable) task, or
- You have already planned and the next step is to act with existing tools

## When not to use

- Social wakes that need a human reply first → `talk` then speak, then return here if needed
- Goal has no tasks yet → `plan-work`
- Claims of done / goal close → `review-work`
- Nothing useful ready → `rest` (do not invent busywork)
- Missing **callable** capability and growth path is the real job → after updating the ledger, `load_skill` name `create-tool` (do not thrash an empty sandbox pretending to implement host runtime)

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. `list_goals` or `get_task` / `get_goal` — pick the ready task and re-read acceptance when orient is thin
2. Then sandbox tools (`read_file`, `list_dir`, `grep`, `search_replace`, `run`) and/or `update_task` to make progress
3. If a **capability is missing** (no tool for the job): `load_skill` with name **`create-tool`** — do not fake progress in free-text or thrash empty sandbox lists / host path fishing

## Hard rules

1. **One primary task per moment** when possible; avoid thrashing across many tasks.
2. Never claim done without **evidence** in the sandbox or honest ledger notes.
3. Sandbox tools are jailed under the sandbox root — they do **not** see host `tools/bundled` or repo paths. Prefer ledger + allowed tools; drafts only via `install_tool_draft` (through create-tool).
4. On blocker: set task **`blocked`** (or clear notes) with a specific reason; optional `speak` if a human must unblock. Do not spin forever. Sandbox isolation failures (`sandbox_unavailable:*`, `guest_pytest_unavailable`) are blockers — block / speak / rest; do not thrash guest tools.
5. Do **not** close the parent goal from here. Prefer `load_skill` name `review-work` before goal close.
6. Use **exact** tool names (snake_case) and skill names (hyphenated) only.

## Process

1. Pick the next ready task (why-now / orient, or `list_goals` / `get_task`).
2. Re-read acceptance (`get_goal` / `get_task` when orient is thin).
3. If the task needs a capability you do not have as a **callable tool**, `load_skill` name `create-tool` and follow that path — after a ledger note. Do not rewrite the host product via `run`.
4. Use sandbox and ledger tools for small, checkable steps.
5. Update the task as you go (`update_task`: notes, status, blocked reason).
6. Stop when one of:
   - **Accepted:** acceptance met → leave ready for review (or mark per ledger convention); do not silent-close the goal
   - **Blocked:** missing info, tool/runtime failure, external dependency → status + reason; `speak` if the operator must act
   - **Need user:** `speak` then `wait_user` (speak first)
7. When execution is done and review is next: `load_skill` name `review-work`.

## Quality / completion

Done when:

- Progress is visible in workspace and/or ledger notes, or
- The task is honestly blocked with a reason a human can act on, or
- The user has been asked clearly on glass

## Out of scope

- Free-form exploration of the host filesystem to “find examples”
- Closing goals without `review-work`
- Creating low-value tools instead of composing existing tools (see create-tool quality bar)
