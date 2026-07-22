---
name: plan-work
description: Break a goal into tasks with acceptance criteria. Use when a goal is open and needs a plan.
---

# Plan work

Turn an open goal into an actionable task list.

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry from the list below (pick the first that applies). Do not answer with free-text only.

- `list_goals` or `get_goal` / `get_task` — load ledger state first when orient is thin.
- Then `create_task` / `update_task` / `update_goal` / `create_goal` to persist the plan.
- If a human is waiting on glass: `speak` a short plan summary (work wakes may stay silent).

## Steps

1. Load the goal from the ledger (orient / tools). Confirm the outcome in one sentence.
2. Split into small tasks. Each task should be finishable in a short stretch of tool use.
3. For every task, write **acceptance** (how we know it is done) — not just a title.
4. Order tasks so dependencies are clear (ready vs blocked).
5. Persist with `create_goal` / `create_task` / `update_goal` / `update_task`. Keep the goal status honest (planning → active when tasks are ready).
6. If a human is waiting on glass, `speak` a short summary of the plan. Pure work wakes may stay silent.

## Rules

- Prefer few clear tasks over a novel outline.
- Do not mark work done here — that is `do-work` + `review-work`.
- If the goal is already well-tasked, skip re-planning; load `do-work` instead.
