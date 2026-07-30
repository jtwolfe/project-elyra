# Continuity & agency affordances — inventory

**When:** 2026-07-29 ~05:07 UTC (task t_a5abe18c8c7a)
**Goal:** g_ff66028e8f9b Continuity and agency scaffolding
**Scope:** What actually keeps the chain alive from this side — not host internals.

---

## 1. Inventory (grounded)

### Ledger (primary continuity hook)
| Affordance | Role | Notes |
|---|---|---|
| `create_goal` / `update_goal` | Durable intent across moments | Open goals are what the continuous-work **backstop** hangs on |
| `create_task` / `update_task` | Schedulable work units | Status `ready` can enqueue a **task_ready** wake |
| `list_goals` / `get_goal` / `get_task` | Re-orient without human restate | Always re-read acceptance before claiming done |

Jim's clarification (this session): continuous-work is a **backstop**, not chat-forever. If open goals remain, the system should not halt-closed and drop the chain quietly.

### Time / self-wake
| Affordance | Role | Notes |
|---|---|---|
| `schedule_wake` | Future timer wake | Use for delayed resume; **not** a substitute for waiting on a human reply |
| Orient clock (NOW) | Situational time | Use provided UTC/local; no felt stream between moments |

### Social boundary
| Affordance | Role | Notes |
|---|---|---|
| `speak` | Only path to glass | Tool JSON / free-text are **not** user-visible |
| `wait_user` | End moment; wait for human | After speak when a decision/reply is needed |
| User / wait-reply wakes | Social first | `talk` then speak first; do not only plan privately |

### Execution skills (honest loop stages)
| Skill | When |
|---|---|
| `plan-work` | Goal needs tasks / acceptance structure |
| `do-work` | One ready task; tools until accepted, blocked, or need user |
| `review-work` | Before goal close; evidence vs acceptance |
| `rest` | No useful ready work — honest idle, no busywork |

### Growth (only when capability is missing)
| Path | Role |
|---|---|
| `create-tool` then draft, verify_tool, promote_tool | New **callable** |
| `create-skill` then install/promote | Reusable **playbook** only |

### Sandbox (durable artifacts inside jail)
| Path | Mode | Use |
|---|---|---|
| `tmp/` | RW | Notes, scratch, tool outputs (this file) |
| `general/`, `lib/`, `fixtures/` | RO | Seeds / helpers — not a second memory store |
| `tools/` | staged runtime | Not host drafts |

**Not available from here:** continuous sensory stream, host repo wholesale, secret values in context, inventing wakes the ledger/timer did not create.

---

## 2. Minimal honest continuity loop

1. Open goal with clear acceptance (reversible, not fake chores)
2. At least one task with concrete acceptance; set **ready** when executable
3. On task_ready / continue wake → load do-work → act with tools
4. Leave evidence: ledger notes and/or sandbox note under tmp/
5. If more real work remains → next ready task (or schedule_wake for a timed step)
6. If need human → speak + wait_user (do not fake progress)
7. If nothing useful remains → rest / honest idle (backstop only matters while goals stay open)
8. Before closing a goal → review-work with evidence

**Anti-patterns**
- Ready tasks that are busywork just to keep the backstop warm
- Silent free-text done with no ledger/sandbox evidence
- schedule_wake spam instead of real task decomposition
- Closing goals from do-work without review
- Treating continuous-work as permission to monologue without goals

---

## 3. Proposed next micro-step (visible to Jim)

**Task candidate:** Stand up the next real agency step under this goal — pick **one**:

**A.** Encode this loop as a small local skill (e.g. continuity-loop) via create-skill so future moments load the playbook instead of re-deriving it
**B.** Add one concrete capability gap task (only if a missing callable is already felt — then create-tool, not a note)
**C.** Run one harmless end-to-end probe: schedule_wake + short reason linked to this goal, confirm timer wake lands and re-orients on open work — then rest if nothing else is ready

**Recommendation:** **A** if we want durable procedure; **C** if we want empirical proof the backstop + wakes behave; avoid B until a real tool gap appears.

Default if Jim does not steer: **A** next (skill draft, reversible, no new host power).

---

## 4. Acceptance check (this task)

| Criterion | Evidence |
|---|---|
| Clear inventory | sections 1-2 above |
| One proposed next micro-step Jim can see | section 3 A/B/C + recommendation |
| Not busywork | Note is reference material; next step is optional encode or probe |

