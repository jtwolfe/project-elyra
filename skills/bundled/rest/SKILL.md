---
name: rest
description: Idle honestly when nothing useful remains. Use on empty, background, or pure timer wakes with no ready work.
---

# Rest

This skill is honest idleness. Sometimes the right action is **no tools** and no invented work.

## When to use

Use this skill when:

- Why-now shows **no** user message requiring a reply
- **No** ready task you should take
- **No** urgent timer-linked work
- Nothing honest remains: when Continue open work is **OFF**, bare idle is fine; when **ON**, “nothing remains” means you have **audited the ledger** (`list_goals` / `get_goal` / `get_task`) then idle — orient’s goals slice alone is not enough

## When not to use

- Social wake (user / wait reply) → `talk` and **`speak`**
- Clear ready task → `do-work`
- Open goal with no tasks → `plan-work`
- Done claims need checking → `review-work`
- Using rest to avoid a hard but real task (that is avoidance, not rest)

## First action

Honest idle is success — but the halt path depends on continuous:

- **Continue open work ON:** call `list_goals` (and/or `get_goal` / `get_task` for the active id), confirm nothing useful, **then** stop with **no tools**.
- **OFF** (or pure empty background with no open-work chain): if why-now shows nothing useful, **stop with no tools** (bare idle is correct).

Also:

- Do not invent busywork, empty goals, or tool thrash to look active.
- If this wake is social by mistake: `load_skill` name `talk`, then `speak`.
- Never treat free-text planning as work when rest is correct.
- When a usage limit is active, resting is correct behaviour.

## Hard rules

1. Silent exit is correct for pure work/background wakes with nothing ready.
2. **Never** stay silent on social wakes (see `talk`).
3. Rest is honest idleness, not avoidance of a clear ready task.
4. Do not open goals/tasks solely to appear productive.

## Process

1. Confirm why-now and orient (goals/tasks): nothing useful and no social obligation.
2. Do **not** invent busywork or empty ledger entries.
3. If the wake was social by mistake → `load_skill` name `talk` → `speak`.
4. If a ready task is clearly present → `load_skill` name `do-work` instead of resting.
5. On **wait_timeout** with no new user content: prefer rest (or other honest ledger work) over re-asking the same question. Re-wait only with a clear new reason.
6. Under Continue open work **ON**, do **not** bare-stop after a toolful moment without a ledger audit if you intend to rest — that may re-enqueue `moment_continue`. Prefer `list_goals` / `get_goal` / `get_task` then **no tools**. `wait_user` still pauses the outer chain.
7. Otherwise stop with **no tools** (or only a quiet ledger note if the product explicitly expects heartbeat bookkeeping).

## Quality / completion

Done when:

- No unnecessary tool calls were made, and
- Social obligations were not ignored, and
- Ready work was not falsely rested away, and
- Under continuous ON, rest used audit-then-idle (not bare `no_tools` after tools without looking)

## Out of scope

- Multi-step work, planning, review, or growth tools
- “Resting” while continuous policy expects progress on open ready work (prefer `do-work` or honest `blocked` + speak)
- Continuous ON + open work + bare `no_tools` without ledger audit — that is not honest rest (outer may re-wake)
