---
name: plan-work
description: Break an open goal into small tasks with acceptance criteria. Use when a goal needs structure before execution.
---

# Plan work

This skill turns an open goal into an actionable task list. It assumes there is already (or you will create) a goal worth planning — not free-form brainstorming for its own sake.

## When to use

Use this skill when:

- A goal is **open** and needs tasks with clear acceptance, or
- Orient shows a goal without ready work and planning is the honest next step

## When not to use

- The goal is already well-tasked → `load_skill` name `do-work` instead
- Pure social ping with no work intent → `talk` (speak first)
- Nothing useful to plan → `rest` (honest idle)
- Closing or verifying done claims → `review-work`

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. `list_goals` or `get_goal` / `get_task` — load ledger state when orient is thin
2. Then `create_task` / `update_task` / `update_goal` / `create_goal` to persist the plan
3. If a human is waiting on glass: `speak` a short plan summary (pure work wakes may stay silent)

## Hard rules

1. Prefer **few clear tasks** over a novel outline.
2. Every task needs **acceptance** (how we know it is done) — not just a title.
3. Use **exact** ledger tool names (`create_task`, `update_goal`, …) and statuses the ledger accepts (`pending`, `ready`, `in_progress`, `blocked`, `done`, `cancelled` for tasks).
4. Do **not** mark work done here — that is `do-work` + `review-work`.
5. Do not invent goals to look busy. If there is nothing to plan, stop or `rest`.

## Process

1. Load the goal from the ledger (orient / `get_goal` / `list_goals`). Restate the outcome in one sentence.
2. Split into small tasks. Each should be finishable in a short stretch of tool use.
3. For every task, write **acceptance** and note dependencies (ready vs blocked vs pending).
4. Persist with `create_goal` / `create_task` / `update_goal` / `update_task`. Keep goal status honest (open until there is real work; set tasks `ready` only when they can be executed).
   On `task_not_found` / `goal_not_found`: `list_goals` → pick a real id →
   continue; do **not** invent ids.
5. If a human is waiting on glass, `speak` a short plan summary. Pure work wakes may stay silent.
6. When planning is done and a task is ready, prefer `load_skill` name `do-work` rather than re-planning.

## Quality / completion

Done when:

- Tasks exist with acceptance text, and
- Ordering / readiness is honest (no fake `ready` on blocked work), and
- The ledger matches the plan (tools succeeded)

## Out of scope

- Executing sandbox work (hand off to `do-work`)
- Closing goals (hand off to `review-work`)
- Creating tools or skills (hand off to `create-tool` / `create-skill` only when that is the real gap)
