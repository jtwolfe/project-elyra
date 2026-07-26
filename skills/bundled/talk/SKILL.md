---
name: talk
description: Social presence — speak first on user and wait-reply wakes; capture work only after the human has a real glass reply.
---

# Talk

This skill is the social stage. A person is waiting on glass (or just spoke). Glass only updates after a successful **`speak`** tool call — free-text never reaches them.

## When to use

Use this skill when:

- The wake is a **user message**, **wait reply**, or other social reason-to-be-here
- You need a human-facing reply before (or without) deeper work

## When not to use

- Pure `task_ready` / timer / background wakes with no social obligation — prefer `do-work` or `rest`
- Deep multi-step implementation without a glass reply first (speak, then hand off)
- Closing goals (use `review-work` first)

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. **`speak`** — always first on social wakes (greeting / answer / ack).
2. Then optionally ledger tools: `create_goal`, `create_task`, `list_goals`, `get_goal`, `update_goal`, `update_task`.
3. Then hand off only **after** speak: `load_skill` with exact name `plan-work`, `do-work`, or `create-tool` when warranted.

## Hard rules

1. **Never silent on social wakes.** User message, wait reply, or other social why-now → you must **`speak`** before the moment ends.
2. **First structured action is `speak`.** Do not plan only in free-text content, dump pseudo-JSON, or monologue without tools.
3. **Speak before wait.** If you need a choice or timeout: `speak` first, then `wait_user`. Later calls after wait are not run.
4. Use **exact** tool names (`speak`, `wait_user`, snake_case) and **exact** skill names (`plan-work`, hyphenated) via `load_skill`.
5. Host may nudge once if you stop with no speak on a social wake — treat that as a final chance to reply.

## Process

1. Read orient why-now: who spoke, what they said, open waits.
2. **Call `speak` immediately** with a short plain-language reply (even for a simple hello).
3. If the ask is clear work, **after** speaking open or update goals/tasks when useful (`create_goal` / `create_task` / `list_goals`). Speak any plan update the human needs on glass.
4. If a **callable capability is missing**, after speaking `load_skill` name `create-tool` and follow that playbook — do not only file goals and stop.
5. If multi-step work with **existing** tools: after speaking, `load_skill` name `plan-work` or `do-work` as appropriate.
6. If you need a decision: `speak` the question, then `wait_user` with choices and timeout.
7. If nothing further is needed after a clear reply, stop with no tools once `speak` has succeeded.

## Quality / completion

Done when:

- The human has a real glass reply (successful `speak`), and
- Either work is handed off cleanly (ledger + skill), or the moment ends honestly with nothing left to say

## Out of scope

- Deep implementation without a goal/task (hand off via `plan-work` / `do-work`)
- Closing goals without `review-work`
- Inventing busywork to avoid a short honest reply
