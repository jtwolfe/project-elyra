---
name: review-work
description: Check done claims against acceptance with evidence; set review then close. Prefer before any goal close.
---

# Review work

This skill verifies that claimed progress is real before closing. Prefer a failed review over a silent close.

## When to use

Use this skill when:

- Tasks or a goal are claimed done / ready for close, or
- Orient or the operator asks to close work, or
- You finished `do-work` and need a gate before `update_goal` → closed

## When not to use

- Work is still in progress with no done claim → `do-work`
- Goal has no tasks yet → `plan-work`
- Pure social reply → `talk`
- Nothing to review → `rest`

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. `get_goal` / `list_goals` — load what is claimed done
2. Then `read_file` / `list_dir` / `grep` (and related) for **sandbox evidence** against acceptance
3. Then `update_goal` / `update_task` for review outcome; close only after a green review

## Hard rules

1. **Do not close a goal without review** as the default path.
2. Prefer status **review** then **closed** (or leave open with remaining tasks). Soft-close / `force` on the ledger tool is a rare last resort — not normal procedure.
3. **Do not invent evidence.** If you cannot check, leave open or blocked for the operator.
4. Be specific about gaps (which acceptance line failed, what is missing).
5. Use exact ledger tool names and honest statuses.

## Process

1. Load the goal and its tasks. List what is claimed done.
2. For each claim, check evidence:
   - Sandbox artifacts (`read_file`, `list_dir`, `grep`, …)
   - Task notes / acceptance text
   - Promoted tools only if the claim is “tool exists and works” (call it if safe)
3. Mark review outcome on the ledger (`update_goal` / `update_task`: reviewed / needs_fix / notes). Be specific.
4. Only after a green review, close the goal — or leave open with remaining tasks.
5. If a human is in the loop, `speak` a short review summary.

## Quality / completion

Done when:

- Every closed claim has evidence or an explicit “cannot verify” hold, and
- Ledger statuses match reality, and
- The human (if present) can see the outcome on glass when they need it

## Out of scope

- Doing the remaining implementation (hand back to `do-work`)
- Creating tools/skills mid-review unless a verified gap blocks review
- Treating `force` close as success without stating the risk
