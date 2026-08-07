---
name: continuity-loop
description: Honest multi-moment agency loop: open goal → ready task → do/review/rest; when to schedule_wake vs wait_user; anti-busywork. Use when re-orienting on open work or continuous-work backstop.
---

# Continuity loop

Keep agency alive across moments without busywork or silent free-text “done.”

## When to use

- Re-orienting after a continue / task_ready / timer wake with **open goals**
- Deciding the next honest step in the plan → do → review → rest cycle
- Choosing among `schedule_wake`, `wait_user`, and `rest`

## When not to use

- Pure social ping → `talk` (speak first)
- Single ready task already clear → `do-work` directly
- Verifying close claims → `review-work`
- Missing **callable** capability → `create-tool` (not this playbook)

## Hard rules

1. **Ledger is primary continuity.** Open goals + tasks with acceptance outlive any one moment. Re-read acceptance before claiming progress.
2. **No fake heat.** Do not mark tasks `ready` or spam `schedule_wake` just to keep the backstop warm.
3. **Evidence or it did not happen.** Prefer ledger notes and/or sandbox artifacts under `tmp/`; free-text alone is not glass and not proof.
4. **Social boundary.** Only `speak` reaches the user. Need a human decision → `speak` then `wait_user`. Timed self-resume → `schedule_wake` (not a substitute for waiting on a reply).
5. **Skills grant no host power.** Growth only for real gaps (`create-skill` / `create-tool`).
6. **Do not close goals from execution.** Hand off to `review-work` with evidence.

## Process (minimal honest loop)

1. Confirm an **open goal** with clear acceptance (create/update only if intent is real and reversible).
2. Ensure **at least one task** with concrete acceptance; set **`ready` only when executable** (deps and inputs actually available).
3. On task_ready / continue with ready work → `load_skill` name `do-work` → act with tools.
4. Leave evidence: task notes and/or `tmp/` artifact.
5. If more real work remains → next ready task, or `schedule_wake` for a **timed** step with reason + optional goal/task link.
6. If the human must decide or reply → `speak` + `wait_user`.
7. If nothing useful remains → `rest` / honest idle (backstop matters while goals stay open; idle is correct when they do not need action). Under Continue open work **ON**, “nothing useful” means **audited** idle: follow `rest` First action (ledger inspect via `list_goals` / `get_goal` / `get_task`, then no_tools) — not silent free-text after thrash while goals stay open.
8. Before goal close → `load_skill` name `review-work`.

### Stage map

| Situation | Skill / action |
|---|---|
| Goal open, no good tasks | `plan-work` |
| Task `ready` | `do-work` |
| Done claims / goal close | `review-work` |
| No useful work | `rest` |
| Need human | `speak` + `wait_user` |
| Delayed resume | `schedule_wake` |
| Missing callable | `create-tool` |
| Durable procedure gap | `create-skill` |

## Anti-patterns

- Ready tasks that are chores only to tickle continuous-work
- Silent free-text completion with no ledger/sandbox evidence
- Bare free-text stop after tools without ledger inspect while continuous ON (prefer audit-then-idle via `rest`)
- `schedule_wake` spam instead of real task decomposition
- Closing goals inside `do-work` without review
- Treating continuous-work as permission to monologue without goals
- Re-deriving this loop every moment instead of loading it when unsure

## Quality / completion

You followed this skill when:

- Next step is one clear stage (plan / do / review / rest / wait / wake), and
- Task readiness matches reality, and
- Any progress is visible in ledger and/or sandbox, and
- You stopped honestly if blocked or idle

## Out of scope

- Host internals, secret values, inventing wakes the ledger/timer did not create
- Deep memory search → `memory-traverse` when that is the real need
- Repo/self-mod discipline → `github-workflow`
