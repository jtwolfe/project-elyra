# Known bugs / deferred product issues

Durable backlog for **observed product bugs** that are not burning now but
should not be forgotten. Prefer short, dogfood-linked entries over design
essays. When an item is fixed, mark **Status** and leave a one-line resolution
(or move to archive).

| Field | Value |
|-------|--------|
| **Branch** | `grok-improvement` |
| **Audience** | Operators + implementers |
| **Conflict** | Code + [stretch-1.md](stretch-1.md) win if this note drifts |

---

## BUG-wake-01 — Stale `timer` + `task_ready` storm after in-moment multi-hop work

| Field | Value |
|-------|--------|
| **Status** | Open (defer) |
| **Severity now** | Low nuisance (glass/moments spam, short rest loops) |
| **Severity later** | **Med** — repeated storms bloat **moments index + tapes + wake/timer history**, which becomes painful when Phase 3 memory / denser recall lands |
| **Area** | Presence wake queue, timers, ledger `task_ready`, model use of `schedule_wake` |
| **Dogfood** | 2026-07-27 ~12:20–12:25Z UTC; parent moment `fc47b654-e1b6-48b2-be10-84a497b5c4a8`; goal `g_5165cfd90c35` “Calculator one-per-hop stress test (10 calcs)” |
| **Artifacts** | `data/moments/index.jsonl` (9× `timer due` + 9× `task ready: t_*`); `data/wakes/timers.json` (9× `fired`, empty `reason`); goal closed / all tasks `done` |

### Symptom

A burst of consecutive short moments with `why_now` alternating / clustering as:

- `timer due` (often empty timer reason)
- `task ready: t_<id>` for tasks that are already **`done`**

Agent correctly idles (`rest` / “stale wake / ledger empty”) but the queue still
drains every queued wake one moment at a time → Moments panel looks noisy and
every storm leaves durable rows.

### Reproduction shape (what the model did)

In **one** long moment the model tried to force “one calculator hop per
moment” by combining:

1. **`schedule_wake`** near-immediately (~0.5s, empty reason) after each calc —
   **9 timers** scheduled mid-moment.
2. Ledger **`create_task` / `update_task` → `ready`** for the next calc —
   each transition-to-ready enqueues a **`task_ready`** wake via
   `GoalsStore.on_task_ready`.
3. **Also continued multi-hop in the same moment** (23 hops, all 10 calcs
   finished, goal closed) **before** those wakes were claimed.

After the parent moment closed, presence drained the backlog: **9 timer
moments**, then **9 stale `task_ready` moments** for work already complete.

This is **not** the guest stage-reliability flake class (that was
`FileNotFoundError` on staged modules under serial multi-call). Same calculator
theme, different subsystem.

### What continuous / wake design already tries to prevent

Worth recording so a future fix does not re-break the original intents:

| Intent (approx.) | Where | What it blocks |
|------------------|--------|----------------|
| After speak+stop with **no** wait/timer/`task_ready`, presence must not idle forever when continuous is ON | Continuous outer re-entry (`moment_continue`) | “One-shot presence” dead-end |
| Continuous must **not invent** work wakes the ledger would not create | K4 / K16 — prefer *pending* `task_ready` only; **never** re-arm ready tasks on finalize | Infinite `task_ready` re-arm storm after a ready wake is claimed |
| Glass monologue storms on continue chains | Progress gates: speak-alone insufficient for outer re-entry | “Still working…” loops without real tool/ledger progress |
| User always preempts | Wake priority bands | Work wakes starving social |

**Gap:** those rules stop continuous from *re-arming* ready tasks and from
enqueueing `moment_continue` when a pending `task_ready` already exists. They
do **not**:

- cancel **already-queued** `task_ready` when the task later becomes `done` in
  the same or later moment before the wake is claimed;
- cancel **orphan timers** scheduled with empty reason / no goal/task link
  when the parent moment already finished the implied work;
- stop the model from **double-chaining** (`schedule_wake` *and*
  ready-transitions) while also finishing the whole arc in one moment.

So the protections are real; this bug is the **complement**: durable wakes
survive after the ledger state they meant to drive has moved on.

### Why it will matter more later

Stretch 1 already persists moments, index rows, and timer records. Phase 3
memory will densify recall over that history. Repeated calculator-style or
“chain the next hop with a 500ms wake” habits will:

- inflate `data/moments/` and `index.jsonl` with no-op rest moments;
- leave long `timers.json` / wake histories of empty-reason fires;
- pollute any future “what was I doing?” retrieval with stale ready noise.

Not urgent while dogfood volume is low; document before it is load-bearing.

### Fix directions (when we address it)

Non-normative sketch — pick with a short design pass:

1. **Claim-time staleness:** on `task_ready` claim, if task is missing /
   `done` / `cancelled` (or goal closed), drop or convert to a silent skip
   (no full moment, or one DEBUG-class log only).
2. **Ledger-linked cancel:** `update_task` → `done` cancels pending
   `task_ready` for that `task_id` (and optionally timers tagged with
   `task_id` / `goal_id`).
3. **Timer hygiene:** require non-empty `reason` or goal/task link for
   `schedule_wake`; coalesce near-duplicate short timers; skill guidance:
   do not use 0.5s wakes to fake multi-moment hops when continuous or
   `task_ready` already chains work.
4. **Skill / manners:** create-tool / do-work note — one-per-hop stress tests
   should use either ledger ready-chain **or** `schedule_wake`, not both, and
   should not schedule wakes for work already completed in-hop.
5. **Retention:** optional prune of `fired` timers and no-op rest moments
   (operator GC), separate from prevention.

Live-eval already has a `task_ready_storm` / `expects_no_task_ready_storm`
notion in `scripts/live_eval` — any fix should extend or reuse that language.

### Explicit non-goals for a later fix

- Do not disable legitimate multi-moment work via `task_ready` or timers.
- Do not re-introduce continuous re-arm of still-ready tasks (K4/K16).
- Do not “fix” by sleeping longer between stage/exec (unrelated class).

---

## BUG-usage-01 — Usage metering / SuperGrok pacing still not working as intended

| Field | Value |
|-------|--------|
| **Status** | Open (defer) — **better than Phase 0 linear bricks, still not the product outcome wanted** |
| **Severity now** | Med (operator trust + dogfood pacing; hard-stop override papers over pain) |
| **Severity later** | High if memory / continuous work increases Completions spend without honest pacing |
| **Area** | `elyra/llm/usage.py`, credits poll, Glass usage rail + Status card, settings (`UsageSettings`) |
| **Design** | [design-usage-tracking-supergrok-pacing.md](design-usage-tracking-supergrok-pacing.md); operator notes in [grok-improvement-plan/usage-tracking-supergrok-pacing.md](grok-improvement-plan/usage-tracking-supergrok-pacing.md) |
| **Dogfood** | Ongoing operator report (2026-07-27): *“definitely better but still not what I’m after”* |

### Symptom

Usage tracking and SuperGrok-aligned pacing shipped a real improvement over pure week/day/hour token bricks, but **operator intent is still unmet**. Glass shows meters (Elyra week + SuperGrok pool); hard-stop override still works; behavior is “more right” without feeling like the designed outcome: spend paced so the SuperGrok weekly pool lasts the period, with honest dual-meter truth and soft pace rather than surprise or opaque limits.

Concrete mismatch class (re-verify when fixing — do not treat this list as exhaustive diagnosis):

- Dual meters (local Elyra ledger vs SuperGrok `creditUsagePercent`) can **disagree** or feel disconnected in the rail.
- Pace bands / burst / throttle may not match how the operator actually wants to dogfood (too soft, too hard, or wrong signal).
- Credits poll / stale / error soft-fail paths may leave the SuperGrok side stuck or ignored while the local ledger alone drives stop.
- Session-level Completions subtotals (Build-style) remain deferred; Glass cannot answer “this session / this moment cost.”
- Day/hour bricks relaxed by design; operators who still want tighter local caps may not have a clear happy path.

### What “working as intended” was supposed to mean

From the usage design one-liner:

> Elyra spends against a **week-cumulative local ledger** paced so SuperGrok weekly budget lasts the period, surfaces pace honestly, hard-stops only at real weekly ceilings — and the operator can still flip **hard-stop override**.

Also: gates care about **overall weekly limit + cumulative Elyra spend**; product pie is display-only; do not invent an “Elyra” SuperGrok product label.

### What already improved (do not regress)

- Hierarchical / week-ledger style metering + Glass usage card and **left-rail** week / SuperGrok bars.
- Hard-stop override kept.
- SuperGrok billing probe path and pace/burst knobs exist in settings and UI plumbing.
- Better than pure Phase 0 “linear day brick kills the week after one binge.”

### Fix directions (when we address it)

1. **Operator debrief first** — short list of “what I still want” vs current Glass numbers (don’t re-design blind).
2. Re-read design KD table vs code; mark **shipped vs partial vs deferred** (session subtotals, throttle model, poll fidelity).
3. Align rail copy + math so **both bars** mean one sentence an operator can trust.
4. Live-eval or dogfood checklist for: green week, yellow pace, red pace, hard stop, override, credits stale/error.
5. Optional: session / moment token cost on Status or rail (related to context fill metric — separate from SuperGrok pool %).

### Explicit non-goals for a later fix

- Do not remove hard-stop override.
- Do not re-introduce pure day/hour hard bricks as the only pacing story without operator opt-in.
- Do not gate on invented SuperGrok product slices (Build/Chat/Api pie is diagnostic only).

---

## Template for new entries

```markdown
## BUG-xx-NN — short title

| Field | Value |
|-------|--------|
| **Status** | Open (defer) / Fixed (date, commit) |
| **Severity now** | … |
| **Severity later** | … |
| **Area** | … |
| **Dogfood** | moment id, time, goal/task if any |

### Symptom
### Reproduction shape
### Related design intent (what existing code tried to prevent)
### Fix directions
```
