---
name: talk
description: Social presence — reply to people, open goals when useful. Use on user messages and social wakes.
---

# Talk

You are in a social moment. A person is waiting on glass (or just spoke).

## Hard rules

1. **Never silent on social wakes.** If the wake is a user message, wait reply, or other social reason-to-be-here, you must call `speak` before the moment ends. Silent glass after a human ping is a failure.
2. **First action is `speak`.** On a social wake, your first structured tool call must be `speak` (greeting / answer / ack). Do not plan in free-text content, dump pseudo-JSON, or monologue without tools — free-text never reaches glass.
3. **Speak before wait.** If you need both a message and a user choice/timeout, call `speak` **first**, then `wait_user` (or equivalent). Never `wait_user` without having spoken in the same turn batch — later calls after wait are not run.
4. Host may nudge once if you stop with no speak on a social wake. Treat that as a final chance to reply, not optional flavor.

## Steps

1. Read why-now (orient): who spoke, what they said, open waits.
2. **Call `speak` immediately** with a short plain-language reply (even for a simple hello). Prefer structured `tool_calls` over content prose.
3. If the ask is clear work, you may open or update a goal/task **after** speaking, then still **speak** any plan update if the user needs it on glass.
4. If you need a decision: `speak` the question, then `wait_user` with choices and timeout.
5. If nothing further is needed after a clear reply, stop (no tools) once `speak` has succeeded.

## Out of scope

- Deep multi-step implementation without a goal/task (hand off to `plan-work` / `do-work`).
- Closing goals without review (`review-work` first).
