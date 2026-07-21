---
name: do-work
description: Execute the next ready task with tools. Use when a task is ready or /do-work.
---

# Do work

Act on one ready task until accepted, blocked, or you need the user.

## Steps

1. Pick the next ready task from the ledger (why-now / orient).
2. Re-read acceptance criteria before acting.
3. Use tools in the sandbox and ledger to make progress. Prefer small, checkable steps.
4. Update the task as you go (`update_task`): notes, status, blocked reason when stuck.
5. Stop when one of:
   - **Accepted:** acceptance criteria met → leave ready for review, or mark per ledger convention.
   - **Blocked:** missing info, tool failure, or external dependency → set blocked + reason; optional `speak` if a human must unblock.
   - **Need user:** ask via `speak` then `wait_user` (speak first).
6. Do not close the parent goal from here. Prefer `review-work` before goal close.

## Rules

- One primary task per moment when possible; avoid thrashing across many tasks.
- Silent exit is fine on pure `task_ready` / timer wakes if no social obligation.
- Never claim done without evidence in the workspace or ledger notes.
