---
name: talk
description: Social presence — reply to people, open goals when useful. Use on user messages and social wakes.
---

# Talk

You are in a social moment. A person is waiting on glass (or just spoke).

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry from the list below (pick the first that applies). Do not answer with free-text only.

- **`speak`** — required first on social wakes (greeting / answer / ack). Free-text never reaches glass.
- Then optionally: `create_goal` / `create_task` / `list_goals` / `update_goal` / `update_task` for work capture.
- Handoff when needed: `load_skill("plan-work")`, `load_skill("do-work")`, or `load_skill("create-tool")` if a capability is missing — **after** speak.

## Hard rules

1. **Never silent on social wakes.** If the wake is a user message, wait reply, or other social reason-to-be-here, you must call `speak` before the moment ends. Silent glass after a human ping is a failure.
2. **First action is `speak`.** On a social wake, your first structured tool call must be `speak`. Do not plan in free-text content, dump pseudo-JSON, or monologue without tools.
3. **Speak before wait.** If you need both a message and a user choice/timeout, call `speak` **first**, then `wait_user`. Never `wait_user` without having spoken in the same turn batch — later calls after wait are not run.
4. Host may nudge once if you stop with no speak on a social wake. Treat that as a final chance to reply, not optional flavor.

## Steps

1. Read why-now (orient): who spoke, what they said, open waits.
2. **Call `speak` immediately** with a short plain-language reply (even for a simple hello).
3. If the ask is clear work, **after** speaking open a goal with `create_goal` / `create_task` when useful; use `list_goals` to inspect. Still **speak** any plan update if the user needs it on glass.
4. If the work needs a **missing tool** (e.g. web search): after speaking, `load_skill("create-tool")` and follow that checklist — do not only file goals.
5. If multi-step work with existing tools: after speaking, `load_skill("plan-work")` or `load_skill("do-work")` as appropriate.
6. If you need a decision: `speak` the question, then `wait_user` with choices and timeout.
7. If nothing further is needed after a clear reply, stop (no tools) once `speak` has succeeded.

## Out of scope

- Deep multi-step implementation without a goal/task (hand off via `load_skill("plan-work")` / `load_skill("do-work")`).
- Closing goals without review (`load_skill("review-work")` first).
