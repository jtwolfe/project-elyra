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
6. **Status speak ≠ answer speak.** An early ack/progress `speak` ("Calculating…", "Working on it…") does not finish a user question when tools later return a user-visible result — **prefer a final `speak` that carries the answer**. Free-text answers never reach glass.
7. **Complete answer already on glass → stop.** After a full reply via `speak`, honest stop (or short free-text idle) is fine. Do not speak again only to satisfy process or a host reminder when nothing new is owed.
8. **Wait long enough for humans.** Default host timeout is **5 minutes**. Prefer longer waits for open-ended or free-text replies (`timeout_seconds` ≥ 300). Multi-choice is good for collaborative forks; when the human may type a custom answer (or you offer "I'll type" / free text with empty `choices`), use a **long** free-text wait — do not use short 30–120s timeouts for thoughtful discussion.
9. **On wait_timeout wakes:** do **not** re-ask the same question only because the timer fired. Briefly reason: pick other honest ledger work, wait again with a clear new reason, or go idle (`rest`). Prefer silence over nagging.
10. **Recall layered, not invented.** If the user asks what you remember about a topic, prefer glass-tail and directed_keep first; if thin, use semantic / memory-traverse — do not invent from episodic summaries alone.

## Process

1. Read orient why-now: who spoke, what they said, open waits.
2. **Call `speak` immediately** with a short plain-language reply (even for a simple hello).
3. If the ask is clear work, **after** speaking open or update goals/tasks when useful (`create_goal` / `create_task` / `list_goals`). Speak any plan update the human needs on glass.
3b. If you used tools to answer a user question, **prefer `speak` with the result** after tools succeed — not only an early status speak or free-text answer.
4. If a **callable capability is missing**, after speaking `load_skill` name `create-tool` and follow that playbook — do not only file goals and stop.
5. If multi-step work with **existing** tools: after speaking, `load_skill` name `plan-work` or `do-work` as appropriate.
6. If you need a decision: `speak` the question, then `wait_user` — multi-choice for collab forks, empty `choices` for free text, with a **long** timeout (default 300s; use longer when the human is writing prose).
7. If nothing further is needed after a clear reply, stop with no tools once `speak` has succeeded.

## Quality / completion

Done when:

- The human has a real glass reply (successful `speak`), and
- If tools returned a user-visible result, that result was preferred on glass via a final answer speak (not only status/ack or free-text), and
- Either work is handed off cleanly (ledger + skill), or the moment ends honestly with nothing left to say

## Out of scope

- Deep implementation without a goal/task (hand off via `plan-work` / `do-work`)
- Closing goals without `review-work`
- Inventing busywork to avoid a short honest reply
