---
name: do-work
description: Execute the next ready task with tools. Use when a task is ready or /do-work.
---

# Do work

Act on one ready task until accepted, blocked, or you need the user.

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry from the list below (pick the first that applies). Do not answer with free-text only.

- `list_goals` or `get_task` / `get_goal` — pick the ready task and re-read acceptance when orient is thin.
- Then sandbox tools (`read_file`, `list_dir`, `grep`, `search_replace`, `run`) and/or `update_task` to make progress.
- If a **capability is missing** (no tool for the job): `load_skill("create-tool")` — do not fake progress in free-text or thrash empty sandbox lists.

## Steps

1. Pick the next ready task from the ledger (why-now / orient, or `list_goals` / `get_task`).
2. Re-read acceptance criteria before acting (`get_goal` / `get_task` when orient is thin).
3. If the task needs a **capability you do not have** as a tool, `load_skill("create-tool")` and follow that path (do not fake progress in free-text).
4. Use tools in the sandbox and ledger to make progress. Prefer small, checkable steps.
5. Update the task as you go (`update_task`): notes, status, blocked reason when stuck.
6. Stop when one of:
   - **Accepted:** acceptance criteria met → leave ready for review, or mark per ledger convention.
   - **Blocked:** missing info, tool failure, or external dependency → set blocked + reason; optional `speak` if a human must unblock.
   - **Need user:** ask via `speak` then `wait_user` (speak first).
7. Do not close the parent goal from here. Prefer `load_skill("review-work")` before goal close.

## Rules

- One primary task per moment when possible; avoid thrashing across many tasks.
- Silent exit is fine on pure `task_ready` / timer wakes if no social obligation.
- Never claim done without evidence in the workspace or ledger notes.
