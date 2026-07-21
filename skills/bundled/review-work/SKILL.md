---
name: review-work
description: Check done claims against acceptance; set review then closed. Prefer before any goal close.
---

# Review work

Verify that claimed progress is real before closing.

## Hard rule

**Do not close a goal without review.**  
Closing skips this skill only with an explicit, rare force path on the ledger tool — never as the default. Prefer: review status → then closed.

## Steps

1. Load the goal and its tasks. List what is claimed done.
2. For each claim, check evidence:
   - Sandbox artifacts (`read_file`, `list_dir`, `grep`)
   - Task notes / acceptance text
3. Mark review outcome on the ledger (reviewed / needs_fix). Be specific about gaps.
4. Only after a green review, close the goal (or leave open with remaining tasks).
5. If a human is in the loop, `speak` a short review summary.

## Rules

- Prefer failing review over silent close.
- Do not invent evidence. If you cannot check, leave open or blocked for the operator.
- `force` on close is a soft-bypass last resort documented on the ledger tool — do not treat it as normal.
