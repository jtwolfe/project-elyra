# continuity-loop smoke-check

**When:** 2026-07-29 ~10:34 UTC
**Goal:** g_ff66028e8f9b
**Task:** t_1a844234f30a

## Load

- `load_skill` name `continuity-loop` → PLAYBOOK ACTIVE, source **local** (this hop and prior promote)
- `get_skill` which=current → complete package at skills/local/continuity-loop (SKILL.md present)

## Playbook vs inventory (tmp/continuity_agency_inventory.md)

| Inventory loop step | Skill coverage |
|---|---|
| Open goal + acceptance | Process §1; Hard rule 1 (ledger primary) |
| Task with acceptance; ready only when executable | Process §2; No fake heat |
| task_ready → do-work | Process §3; Stage map |
| Evidence in ledger/tmp | Process §4; Hard rule 3 |
| More work / schedule_wake | Process §5 |
| Need human → speak + wait_user | Process §6; Hard rule 4 |
| Nothing useful → rest | Process §7; Stage map |
| Before close → review-work | Process §8; Hard rule 6 |

**Anti-patterns** (inventory §2): all five appear under skill Anti-patterns (busywork ready, silent free-text done, wake spam, close without review, monologue without goals) plus re-deriving the loop.

**No host-power claims:** skill states skills grant no host power; growth only via create-skill/create-tool for real gaps.

## Result

PASS — load works; playbook matches inventory loop + anti-patterns; reversible local skill only.
