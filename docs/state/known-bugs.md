# Known bugs / deferred product issues

Durable backlog for **observed product bugs** that are not burning now but
should not be forgotten. Prefer short, dogfood-linked entries over design
essays. When an item is fixed, mark **Status** and leave a one-line resolution
(or move to archive).

| Field | Value |
|-------|--------|
| **Branch** | `main` (product tip after known-bugs Glass batch) |
| **Project** | [Autopoiesis Commons #2](https://github.com/users/jtwolfe/projects/2) |
| **Epic** | [#59](https://github.com/jtwolfe/project-elyra/issues/59) — break-out into issues |
| **Audience** | Operators + implementers |
| **Conflict** | Code + [stretch-1.md](stretch-1.md) win if this note drifts |

---

## Issue index

Each BUG-* entry has a GitHub issue (sub-issue of [#59](https://github.com/jtwolfe/project-elyra/issues/59)) on the project board.

| Bug ID | Issue | Doc status (summary) |
|--------|-------|----------------------|
| `BUG-wake-01` | [#67](https://github.com/jtwolfe/project-elyra/issues/67) (open) | Open (defer) |
| `BUG-wake-02` | [#68](https://github.com/jtwolfe/project-elyra/issues/68) (open) | Open (defer) |
| `BUG-usage-01` | [#69](https://github.com/jtwolfe/project-elyra/issues/69) (open) | Open (defer) — better than Phase 0 linear bricks, still not the pro… |
| `BUG-glass-01` | [#70](https://github.com/jtwolfe/project-elyra/issues/70) (closed) | **Fixed** on main (Moments pretty-print) |
| `BUG-glass-02` | [#71](https://github.com/jtwolfe/project-elyra/issues/71) (closed) | **Fixed** on main — Moments tab under Memory |
| `BUG-mem-ui-01` | [#72](https://github.com/jtwolfe/project-elyra/issues/72) (closed) | **Fixed** on main (Context chrome + inspect); residual: ladder narrative |
| `BUG-mem-ui-02` | [#73](https://github.com/jtwolfe/project-elyra/issues/73) (open) | Open (defer) |
| `BUG-mem-ui-03` | [#74](https://github.com/jtwolfe/project-elyra/issues/74) (closed) | **Fixed** on main (Atoms soft-skip under glass-03) |
| `BUG-glass-03` | [#86](https://github.com/jtwolfe/project-elyra/issues/86) (open) | **Partial** on main — knip soft-skip shipped; residual poll architecture later |
| `BUG-chat-01` | [#75](https://github.com/jtwolfe/project-elyra/issues/75) (closed) | **Done** — KaTeX on fix/known-bugs (dogfood OK) |
| `BUG-chat-02` | [#84](https://github.com/jtwolfe/project-elyra/issues/84) (closed) | **Done** — soft newlines on fix/known-bugs (dogfood OK) |
| `BUG-chat-03` | [#88](https://github.com/jtwolfe/project-elyra/issues/88) (open) | Open — Sources / reference links must open correctly |
| `BUG-wait-01` | [#89](https://github.com/jtwolfe/project-elyra/issues/89) (open) | Open — multi-choice wait after speak; strong instruction nudge |
| `BUG-tts-01` | [#85](https://github.com/jtwolfe/project-elyra/issues/85) (open) | Open — TTS needs text sanitation before service call |
| `BUG-status-01` | [#76](https://github.com/jtwolfe/project-elyra/issues/76) (closed) | **Fixed** on main |
| `BUG-status-02` | [#77](https://github.com/jtwolfe/project-elyra/issues/77) (closed) | **Fixed** on main |
| `BUG-status-03` | [#78](https://github.com/jtwolfe/project-elyra/issues/78) (closed) | **Fixed** on main |
| `BUG-prompt-01` | [#79](https://github.com/jtwolfe/project-elyra/issues/79) (open) | Open (defer) — review after memory is up (Phase 1 meal/store stable… |
| `BUG-mem-p2-01` | [#80](https://github.com/jtwolfe/project-elyra/issues/80) (open) | Fixed in code (PR-R1–R5, 2026-07-29) — residual: operator smoke dog… |
| `BUG-mem-lance-01` | [#81](https://github.com/jtwolfe/project-elyra/issues/81) (closed) | Fixed (2026-07-29, `fcb5130`) — restart required so process maps re… |
| `BUG-mem-gpu-01` | [#82](https://github.com/jtwolfe/project-elyra/issues/82) (**closed** umbrella) → residuals [#114](https://github.com/jtwolfe/project-elyra/issues/114) (C6a busy dogfood) + [#115](https://github.com/jtwolfe/project-elyra/issues/115) (C6 packaging) | **Closed umbrella** 2026-08-04; continuous-encode **code** shipped; live busy dogfood → #114; GPU/env packaging → #115 |
| `BUG-meal-01` | [#91](https://github.com/jtwolfe/project-elyra/issues/91) (closed) | **Fixed** on main — runtime fraction default 0.5 → ~250k; slider max 0.75 + `--max-meal-override` |
| `BUG-meal-02` | [#92](https://github.com/jtwolfe/project-elyra/issues/92) (closed) | **Fixed** on main — LLM period ladder + meal tip policy; dogfood OK on feature/92 |
| `BUG-meal-03` | [#93](https://github.com/jtwolfe/project-elyra/issues/93) (closed) | **In progress** — instance continuity: glass-tail + sticky directed keep (implement plan ready; S1–S6 product PRs) |

---

## BUG-wake-01 — Stale `timer` + `task_ready` storm after in-moment multi-hop work

| Field | Value |
|-------|--------|
| **Status** | Open (defer) |
| **Issue** | [#67](https://github.com/jtwolfe/project-elyra/issues/67) |
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

## BUG-wake-02 — No post-restart sanitation / “I just restarted” awareness

| Field | Value |
|-------|--------|
| **Status** | Open (defer) |
| **Issue** | [#68](https://github.com/jtwolfe/project-elyra/issues/68) |
| **Severity now** | Med nuisance — after restart (or after long idle), timed-out waits and stale context can resume **old** threads (e.g. haiku collection) instead of recent work |
| **Severity later** | Med–High when denser memory + continuous make “zombie” threads more common |
| **Area** | Presence startup (`_startup_recover`, `_fire_due_unlocked`), wait/timer rehydrate, orient / meal seed, rest vs work skills on `wait_timeout` |
| **Dogfood** | 2026-07-29 — process restart ~05:15Z; wait `183dea3c…` (armed after continuity chat, prompt about continuity) expired **05:16:16Z** → moment `6dcd352d…` **`wait_timeout`**; model reasoning chose *“Completing the promised funny-haiku collection”* from older glass/sandbox residue (`tmp/funny_haiku_collection.md`, prior joke/haiku moments), not the wait prompt and not open goal `g_ff66028e8f9b` continuity. Continuous was **off**. |

### Symptom

Operator restarts Elyra (or process comes back near a due wait). Shortly after, a moment runs that:

- is triggered by a **normal** durable mechanism (`wait_timeout`, due timer, recovered claimed wake, stale `task_ready`, …), **not** a special “restart” wake; and
- the model treats residual **old** context (glass, sandbox files, episodic jokes) as live unfinished work, ignoring **recency** of the latest social/goal thread.

Feels like “restart restarted the haiku problem” even when the enqueue path is just “wait expired while process was alive again.”

### Reproduction shape

1. Multi-thread dogfood: older haiku/joke arc + newer continuity/agency chat.
2. Social moment ends with `wait_user` (e.g. 300s) whose **prompt** is about the new thread.
3. Restart (or leave process down) such that due-fire runs when the wait expires.
4. `wait_timeout` moment opens; first hop free-chooses old sandbox/haiku promise from context residue.

Same class can apply without restart if a long wait expires while glass still shows ancient threads — restart makes the discontinuity more obvious to the operator.

### What already works (do not regress)

| Intent | Where |
|--------|--------|
| Crash: re-enqueue durable claimed work; cancel fragile social claims | `queue.recover_claimed` |
| Close interrupted open moments | `moments.recover_open_moments` |
| Rehydrate waits/timers; fire due → `wait_timeout` / timer wakes | `timers` + `_fire_due_unlocked` / startup recover |
| Continuous OFF does not invent `moment_continue` | continuous policy |

**Gap:** recovery is **mechanical** (don’t lose durable wakes). There is **no** POST-like hygiene that says: *process boundary or long gap → re-check recency of latest social/goal context before treating residual glass/sandbox as active work.*

### Fix directions (when we address it)

1. **Startup / first-claim policy (“POST” test):** after `_startup_recover` (and optionally first idle claim after boot), inject a short **host observation** or orient flag, e.g. `runtime_event=process_start` + boot time + “prefer most recent social/goal; do not resume abandoned sandbox threads without reconfirming.” Not necessarily a full moment — could be a one-shot orient line for the next social/work wake only.
2. **Wait-timeout skill bias:** on `wait_timeout`, prefer **rest** or **re-ask** unless there is a **ready ledger task** or wait prompt that clearly continues work; deprioritize free-form “finish old promise from glass” unless linked to open goal/task.
3. **Recency gate for free work:** if last user message / last social moment is older than X minutes relative to this wake, require explicit ready task or new user message before multi-hop sandbox work (timers that only re-prompt are fine).
4. **Optional:** on restart, **do not fire** waits that expired while the process was down without a single **startup summary** moment (or demote them to a compact “missed waits” list) — more aggressive; product call.
5. **Sanitation beyond timers:** same recency test for recovered `task_ready` / empty-reason timers (ties **BUG-wake-01**).

### Explicit non-goals

- Do not drop legitimate due work forever on every restart.
- Do not disable `wait_timeout` as a wake kind.
- Do not require GPU/semantic wait fixes for this class (**BUG-mem-gpu-01** / semantic wait are separate).

### Related

- **BUG-wake-01** — stale timer/`task_ready` storms (complement: cancel when work already done).
- **BUG-mem-lance-01** — thin post-restart process maps from bare `to_arrow` (**Fixed**); wake-02 is still open adjacency after load is full.
- Continuous / rest skills: honest idle vs invented busywork.
- Memory meal seed: `empty_seed` on timeout wakes does **not** prevent glass history from steering the model.

---

## BUG-usage-01 — Usage metering / SuperGrok pacing still not working as intended

| Field | Value |
|-------|--------|
| **Status** | Open (defer) — **better than Phase 0 linear bricks, still not the product outcome wanted** |
| **Issue** | [#69](https://github.com/jtwolfe/project-elyra/issues/69) |
| **Severity now** | Med (operator trust + dogfood pacing; hard-stop override papers over pain) |
| **Severity later** | High if memory / continuous work increases Completions spend without honest pacing |
| **Area** | `elyra/llm/usage.py`, credits poll, Glass usage rail + Status card, settings (`UsageSettings`) |
| **Design** | [design-usage-tracking-supergrok-pacing.md](../design/usage/design-usage-tracking-supergrok-pacing.md); operator notes in [usage-and-pacing.md](usage-and-pacing.md) |
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

## BUG-glass-01 — Moments panel looks like raw JSON (needs beautify pass)

| Field | Value |
|-------|--------|
| **Status** | **Fixed** (operator dogfood 2026-07-30 on `fix/known-bugs` — tool JSON pretty-print; pending main)|
| **Issue** | [#70](https://github.com/jtwolfe/project-elyra/issues/70) |
| **Severity now** | Low–Med (operator readability; dogfood friction) |
| **Severity later** | Med if Moments remain a primary debug surface |
| **Area** | Glass Moments panel (`elyra/runtime/web/`, moments API payload render) |
| **Dogfood** | 2026-07-28 operator note on `grok-improvement-memory` — moments read as dumped JSON rather than a scannable tape |

### Symptom

Moments UI presents beats / payload in a raw or near-JSON shape that is hard to scan while dogfooding (speak/tool/obs/stop not visually structured for humans).

### Fix directions

1. Beautify pass: role/kind chips, readable timestamps, collapsible raw JSON, prose-first speak bodies.
2. Keep full raw available for debug (expand / copy), do not lose fidelity for operators who need it.
3. Coordinate with **BUG-glass-02** if Moments move under Memory.

---

## BUG-glass-02 — Move Moments page into Memory as a subsection

| Field | Value |
|-------|--------|
| **Status** | **Fixed** on main |
| **Issue** | [#71](https://github.com/jtwolfe/project-elyra/issues/71) |
| **Severity now** | — |
| **Severity later** | — |
| **Area** | Glass nav + Memory tabs |
| **Dogfood** | 2026-07-28 request; 2026-07-30 IA move |

### Symptom

Moments was a top-level nav peer of Memory; operators wanted one place for meal / tape / store.

### Fix (landed)

1. **Moments** tab under Memory: order **Context · Moments · Atoms · Vectors · Graph**.
2. Top-level Moments nav + `#panel-moments` removed.
3. `refreshMemory` / tick poll Moments when that Memory tab is active; soft-refresh unchanged.
4. List/detail open class on `#panel-memory` (same pattern as Atoms).
5. Element ids (`#moments-list`, `#moment-detail`) and `/api/moments` unchanged.

### Explicit non-goals

- Do not delete the moment store or change moment = do-loop semantics.
- No hash deep-link router in this pass.

---

## BUG-mem-ui-01 — Memory Context needs beautify + review of summary generation

| Field | Value |
|-------|--------|
| **Status** | **Fixed** on main (Glass Context chrome + inspect soft-refresh). Residual: template ladder / narrative summary quality — open separately if needed. |
| **Issue** | [#72](https://github.com/jtwolfe/project-elyra/issues/72) |
| **Severity now** | Med (hard to understand what the model is “eating”) |
| **Severity later** | High if Context is the primary trust surface for meal/ladder correctness |
| **Area** | Glass Memory → Context (`inspect.meal_*`, `app.js` `renderMemoryContext`); ladder (`elyra/memory/ladder.py`) |
| **Dogfood** | 2026-07-28 — episodic summaries look truncated / opaque; unclear UI vs generation |

### Symptom

Context cards are hard to read: snipped text, channel labels without hierarchy, little visual structure for temporal vs episodic vs orient. Operators cannot easily tell whether a short summary is **inspect snippet truncation** (240 chars), **template highlight truncation** (160 chars/line), or missing ladder work.

### Related generation notes (current truth)

- Summaries are **template-first, no LLM** (`render_template_summary` / `select_highlights`).
- Highlight lines cap by scale (12/16/20); each body truncated ~160 chars in the **stored** summary.
- Glass inspect further truncates meal item `snippet` to **240** chars; full body is not expandable in Context today.

### Fix directions

1. **Beautify:** channel sections, scale/window badges, token budget bars, expandable full content (use `content_chars` / detail endpoint). **Done on branch:** channel groups, truncation honesty (`snippet k/N chars`), prose body, open-atom link; inspect snippet 480 chars.
2. **Generation review:** when ladder runs (idle `refresh_due` vs on-compose), skip-unchanged behavior, child-preference vs raw, highlight ranking, whether template-only is enough for dogfood. **Residual:** still template-first / no LLM summaries — intentional until dogfood asks for richer narrative.
3. Document operator-visible “this is a snippet of N chars” so truncation is not mistaken for empty memory. **Done on branch.**
4. Optional later: richer narrative summaries (LLM) only after template path is trustworthy and budgeted.

---

## BUG-mem-ui-02 — Memory Atoms list needs beautify pass

| Field | Value |
|-------|--------|
| **Status** | Open (defer) |
| **Issue** | [#73](https://github.com/jtwolfe/project-elyra/issues/73) |
| **Severity now** | Low–Med |
| **Severity later** | Med as atom volume grows |
| **Area** | Glass Memory → Atoms (`atom_to_list_row` / detail, `app.js` atoms timeline) |
| **Dogfood** | 2026-07-28 operator note |

### Symptom

Atoms timeline/detail reads as dump-ish (truncated text rows, weak kind/time visual hierarchy). Hard to browse speak vs tool vs summary vs observation at a glance; empty `content_text` with blob-backed speak bodies is especially confusing.

### Fix directions

1. Beautify list rows: kind color/chip, relative time, moment badge, clearer empty-vs-blob affordance.
2. Detail pane: structured meta, full text when under cap, blob hydrate indicator.
3. Avoid fighting **BUG-mem-ui-03** (do not full re-render while selecting text).

---

## BUG-mem-ui-03 — Memory Atoms inspector flashes; system update breaks text selection

| Field | Value |
|-------|--------|
| **Status** | **Fixed** on main (list + soft detail under BUG-glass-03) |
| **Issue** | [#74](https://github.com/jtwolfe/project-elyra/issues/74) |
| **Severity now** | — (status-rail thrash residual under #86 if still felt) |
| **Severity later** | Med if soft-skip regresses |
| **Area** | Glass poll loop (`app.js` `tick` ~1.5s → `refreshMemory` / `refreshMemoryAtoms` when Memory active) |
| **Dogfood** | 2026-07-28 — inspector flash; 2026-07-30 — list + detail soft-skip on branch |
| **Related** | Same root class as Context inspect flash. Implement under **BUG-glass-03** (#86). |

### Symptom

While on Memory → Atoms (inspector/detail), the UI **flashes** on a regular cadence. Selecting text for copy is interrupted or reset — full DOM rebuild from the global poll.

### Fix (landed on `fix/known-bugs`)

1. `refreshMemoryAtoms` fingerprints list payload; skip list wipe when unchanged.
2. Open detail uses soft `loadAtomDetail` with body fingerprint — no loading flash / re-paint when body stable.
3. Manual Apply / kind filter / nav still `force: true`.
4. Residual: status rail thrash and any larger poll architecture review deferred to #86 residual / later.

---

## BUG-glass-03 — Glass poll hard-rebuilds active panels (inspectors flash; text selection dies)

| Field | Value |
|-------|--------|
| **Status** | **Partial** on main — catalog soft-skip knip shipped; residual poll architecture / status strip later |
| **Issue** | [#86](https://github.com/jtwolfe/project-elyra/issues/86) |
| **Severity now** | Low–Med residual (status strip; larger poll architecture) |
| **Severity later** | High if soft-skip regresses or new panels omit the pattern |
| **Area** | `elyra/runtime/web/app.js`: `tick` → soft `refreshActivePanel`; nav/mutations use `force: true` |
| **Dogfood** | 2026-07-30 — patch pass; operator verify selection survives idle poll |

### Symptom

While a Glass catalog panel is open, UI **rebuilded on a regular ~1.5s period** (inspectors flash, text selection dies). Root is **one poll architecture** that always hard-rebuilt DOM.

### Root cause (confirmed)

```text
tick (1.5s)
  → refreshStatus() + refreshMessages()   // always
  → if active panel is catalog:
        refreshGoals | refreshMoments | refreshMemory* |
        refreshTools | refreshIdentity | refreshSecrets
```

Most paths used to **always** do destructive DOM updates even when data was unchanged.

### Soft-skip surfaces (do not regress)

| Surface | Mechanism |
|---------|-----------|
| **Chat messages** | `messagesFingerprint` |
| **Memory → Context** | `fingerprintMemoryMeal` + preserve open inspect folds |
| **Moments detail** | Soft `loadMomentDetail` / snapshot |
| **Goals** | `stableFingerprint` list skip |
| **Moments list** | list meta fingerprint (detail remains soft) |
| **Memory → Atoms** | list FP + soft detail (**#74**) |
| **Memory → Vectors / Graph** | payload fingerprint |
| **Tools / Skills** | catalog list FP + soft inspector |
| **Identity** | payload FP + `setTextIfChanged` |
| **Secrets** | list FP (open grant editors preserved when unchanged) |

### Force paths (must rebuild after write / nav)

Nav click, Memory tab/Apply, catalog Rescan, secret save/delete/grants, identity chip switch / promote / session user switch, `refreshAllPanels` (reset). Tick stays soft (`refreshActivePanel()` without force).

### Residual / later (not this patch)

- Full poll architecture review (whether tick should refresh every catalog panel this way)
- Status strip thrash / selection during `refreshStatus`
- Diff-patch DOM instead of skip-or-full-rebuild
- Selection-aware pause while pointer is selecting

### Success criteria

- [x] Shared `stableFingerprint` / force-vs-soft convention on catalog panels
- [ ] Operator dogfood: Goals / Tools inspector / Identity / Secrets / Atoms detail — text selection survives ≥10s idle poll with unchanged data
- [ ] New server data still appears without manual nav re-click
- [ ] Context + chat soft-skip still work
- [ ] #74 closable after dogfood
- [x] Doc note in known-bugs

### Related

- **#74** BUG-mem-ui-03 — Atoms instance (soft-skip in this patch)
- **#72** BUG-mem-ui-01 — Context inspect meal soft-refresh
- Moments soft detail / chat `messagesFingerprint` — patterns copied

### Explicit non-goals

- Do not disable live updates entirely
- Do not require per-tool output contracts
- Do not rewrite Glass as a framework — knip soft-skip only

---

## BUG-chat-01 — Chat needs equation / math rendering (LaTeX or equivalent)

| Field | Value |
|-------|--------|
| **Status** | **Done** (dogfood 2026-07-30; code on `fix/known-bugs`) |
| **Issue** | [#75](https://github.com/jtwolfe/project-elyra/issues/75) |
| **Severity now** | Low residual (edge TeX / currency `$` false positives) |
| **Severity later** | Med if models change delimiter style |
| **Area** | Glass `renderMarkdown` + vendored KaTeX (`elyra/runtime/web/vendor/katex/`) |
| **Dogfood** | 2026-07-28 request; 2026-07-30 Grok speak uses multi-line `\[ … \]` (Schrödinger moment) |

### Symptom

Math in chat showed as raw `\[…\]` / `$…$` instead of rendered equations.

### Fix (landed)

1. **Grok primary format:** multi-line display `\[ … \]` (also `$$`, `\(...\)`, conservative `$…$`).
2. Extract math to placeholders **before** markdown escape/italic; restore via `katex.renderToString` (`throwOnError: false`).
3. Self-hosted KaTeX 0.16.21 under `/vendor/katex/` (no CDN).
4. Covers chat bubbles + Moments speak prose that already uses `renderMarkdown`.

### Explicit non-goals

- Do not treat this as memory/atom formatting (separate from **BUG-mem-ui-***).
- Do not pull MathJax or a full MDX stack.

---

## BUG-chat-02 — Soft newlines from chat input missing in Glass history (present in atoms)

| Field | Value |
|-------|--------|
| **Status** | **Done** (dogfood 2026-07-30; code on `fix/known-bugs`) |
| **Issue** | [#84](https://github.com/jtwolfe/project-elyra/issues/84) |
| **Severity now** | — |
| **Severity later** | — |
| **Area** | Glass `renderMarkdown` paragraph flush |
| **Dogfood** | 2026-07-30 — multi-line composer flattened in history; atoms OK |

### Symptom

Multi-line chat input showed as a single run-on paragraph in Glass history; atoms kept real newlines (display-only defect).

### Fix (landed)

`flushPara` joins soft-broken lines with `<br>` instead of spaces; blockquotes match. Store/atom paths unchanged.

### Related

- **BUG-chat-01** — same `renderMarkdown` pipeline (math placeholders).
- **BUG-chat-03** — source links in the same bubble pipeline.

---

## BUG-chat-03 — Source / reference links in Glass chat must open correctly

| Field | Value |
|-------|--------|
| **Status** | Open |
| **Issue** | [#88](https://github.com/jtwolfe/project-elyra/issues/88) |
| **Severity now** | Med (research speaks dump Sources operators cannot reliably open) |
| **Severity later** | Med as citation-heavy dogfood grows |
| **Area** | Glass `renderMarkdown` link path; speak/chat **Sources** sections |
| **Dogfood** | 2026-07-30 — moment `cb41e603-1a7a-497c-932c-91983f4e1893` Schrödinger speak; Sources with Wikipedia/Grokipedia markdown links (non-ASCII path chars e.g. `Schrödinger`) |

### Symptom

Long research **`speak`** payloads end with a **Sources** list. Operators need every link to be clickable and land on the intended page (new tab). Risk classes: Unicode wiki paths, bare URLs (no markdown), trailing punctuation, broken `href` encoding.

### Likely cause

- Only `[label](url)` becomes anchors; bare `https://` may not autolink.
- Non-ASCII path segments may need percent-encoding in `href` for some browsers.
- Edge cases: parentheses, mixed encoding, `target`/`rel` hygiene.

### Fix directions

1. Safely encode `http(s)` hrefs (IRI → valid URL) without double-encoding.
2. Optionally autolink bare `https://` lines in Sources-like blocks.
3. Regression: Schrödinger Sources (Wikipedia + Grokipedia) all open; reject `javascript:`.
4. Display-only — do not change atom/message store text.

### Related

- **BUG-chat-01** / **BUG-chat-02** — same `renderMarkdown` surface.
- Parent epic [#59](https://github.com/jtwolfe/project-elyra/issues/59).

---

## BUG-wait-01 — Multi-choice wait after substantive speak (strong instruction nudge)

| Field | Value |
|-------|--------|
| **Status** | Open |
| **Issue** | [#89](https://github.com/jtwolfe/project-elyra/issues/89) |
| **Severity now** | Med (fork questions die without wait bar; human free-types into a new moment) |
| **Severity later** | High for collaborative multi-step dogfood |
| **Area** | `wait_user` + `talk` / social skills + loop `ends_moment` ordering; Glass `#wait-choices` |
| **Dogfood** | 2026-07-30 — same Schrödinger moment: after long answer **speak**, model listed forks (1)–(4) and asked “Which fork?” **without** arming `wait_user` multi-choice — no Glass choice buttons |

### Symptom

After a substantive **speak** that offers multiple follow-up forks, the model often:

1. Writes options only in prose (“(1)… (2)… Which fork?”), and  
2. **Does not** call **`wait_user`** with `choices=[…]` (and adequate timeout),

so Glass never shows the wait bar / multi-choice buttons. Operator must re-open the conversation free-text instead of tapping a choice.

Product intent already exists (`talk` skill: speak then wait; multi-choice for collab forks) but the **nudge is too soft** — model treats the fork list as a rhetorical close.

### Likely cause

- Soft skill language (“if you need a decision”) rather than a hard close-path rule.
- `ends_moment` batch abort after `wait_user` — model must order **speak → wait_user** in one turn; may skip wait to “finish cleanly”.
- Glass wait UI is correct when armed (`renderWaitBar`); defect is **failure to arm**.

### Fix directions

1. **Strong skill + TOOL.md nudge:** when speak offers numbered / lettered forks, **must** follow with `wait_user` using those strings as `choices` (or long free-text wait if offering “I’ll type”).
2. Concrete dogfood example in `talk` + `wait_user` (research close + 4 forks).
3. Optional host one-shot reminder if speak looks multi-choice and batch has no wait (avoid thrash).
4. Verify: armed multi-choice → buttons; free-text → composer hint; reply routes `wait_reply`.

### Explicit non-goals

- Do not invent choices the model did not offer.
- Do not retune default timeouts without separate dogfood.
- Separate from **BUG-wake-*** storm class.

### Related

- `tools/bundled/wait_user/TOOL.md`, `skills/bundled/talk/SKILL.md`, `elyra/tools/builtin/social.py`
- Parent epic [#59](https://github.com/jtwolfe/project-elyra/issues/59).

---

## BUG-tts-01 — TTS needs sanitation before text is sent to the service

| Field | Value |
|-------|--------|
| **Status** | Open |
| **Issue** | [#85](https://github.com/jtwolfe/project-elyra/issues/85) |
| **Severity now** | Med (broken / chaotic audio for common Glass speak payloads) |
| **Severity later** | High if TTS is a default listen path for long agent turns with tables, links, UI chrome |
| **Area** | `elyra/media/tts.py` (`validate_text_for_tts` / `synthesize` / `get_or_synthesize`); Glass play-on-message path that feeds **saved** message text to xAI TTS |
| **Dogfood** | 2026-07-30 operator: non-language / non-speech characters create **generation chaos**. Example class: Grok UI / glass elements like *“you can see the table in our conversation history”* (and similar chrome, tables, markup) when included in text sent to TTS |

### Symptom

Text is handed to the TTS service with little or no **speech-oriented sanitation**. Current guard in product is mainly **empty / length** (`validate_text_for_tts`), not content hygiene. Result:

- Tables, markdown, URLs, code fences, emoji-heavy chrome, and **UI meta-phrases** (e.g. affordance copy about “conversation history” / “see the table”) get spoken or scramble prosody.
- Audio sounds broken, robotic-garble, or reads control/UI prose that was never meant for ears.
- Operator loses trust in play-on-message for real multi-part agent replies.

Storage of the original message can stay intact; this is a **TTS ingress** problem (what we send on the wire), not necessarily a glass-display bug.

### Likely cause (unverified)

`validate_text_for_tts` returns full stored content after empty/length checks; `synthesize` posts that string as `text` to `POST /v1/tts`. No strip of:

- markdown / table syntax
- bare URLs and attachment tokens
- code fences
- glass/system chrome strings and non-linguistic punctuation runs
- optional: role labels, citation chips, “see table in history” style meta

### Fix directions

1. **Sanitize pipeline** before `synthesize` / cache key material that should reflect *spoken* form: strip or rewrite non-speech structures to short spoken equivalents (or drop).
2. Prefer **fail soft**: if after sanitation text is empty, refuse with a clear reason rather than calling the service.
3. Inventory dogfood offenders: tables, lists, code, URLs, emoji, UI meta lines (including Grok-style *“you can see the table in our conversation history”* elements).
4. Keep original message on disk; cache key may need to include a **sanitized hash** or version suffix so old chaotic caches do not stick after the fix.
5. Optional operator toggle later (“read raw”); default must be sanitized.

### Explicit non-goals

- Do not change STT.
- Do not require perfect SSML for v1 — plain cleaned prose is enough.
- Do not delete original chat/atom text.

### Related

- Glass play-on-message / media TTS cache (`tts_cache` kind).
- **BUG-chat-01** / **BUG-chat-02** — chat *display* issues; TTS is the *listen* path (related multi-modal surface, separate fix).

---

## BUG-status-01 — Status page does not scroll

| Field | Value |
|-------|--------|
| **Status** | **Fixed** (operator dogfood 2026-07-30 on `fix/known-bugs`; pending main) |
| **Issue** | [#76](https://github.com/jtwolfe/project-elyra/issues/76) |
| **Severity now** | Med (lower half of Status unreachable on typical viewports) |
| **Severity later** | Med |
| **Area** | Glass Status panel layout/CSS (`#panel-status`, `.status-cards`, main content overflow) |
| **Dogfood** | 2026-07-28 — unable to scroll Status page |

### Symptom

Status panel content that exceeds the viewport cannot be scrolled; cards below the fold (usage, continuous, dev speed, reset, etc.) are hard or impossible to reach depending on window height.

### Fix directions

1. Fix overflow on the main panel / status container (`overflow-y: auto` on the scroll parent, not a non-scrolling flex child).
2. Verify with reduced height and with all cards expanded (usage product details, etc.).
3. Regression: keyboard / trackpad scroll and focus order.

---

## BUG-status-02 — Dev speed mode shows checkbox + switch; switch is inoperative

| Field | Value |
|-------|--------|
| **Status** | **Fixed** (operator dogfood 2026-07-30 on `fix/known-bugs`; pending main) |
| **Issue** | [#77](https://github.com/jtwolfe/project-elyra/issues/77) |
| **Severity now** | Low–Med (confusing control; one affordance appears dead) |
| **Severity later** | Low |
| **Area** | Glass Status → Dev speed card (`#dev-speed-toggle` in `index.html` / `app.js` `renderDevSpeed`) |
| **Dogfood** | 2026-07-28 — checkbox and switch sit next to each other; switch does nothing |

### Symptom

Dev speed control presents **both** a native checkbox and a styled switch track. Operator expectation: one switch. The visual switch appears inoperative; functionality may only be on the checkbox (or CSS class mismatch vs continuous/override toggles).

### Likely cause (unverified)

Hard-stop override / continuous use `input.continuous-toggle` + `.toggle-track` styling. Dev speed checkbox may be **missing** `class="continuous-toggle"`, so the native box stays visible beside the track and click targets feel split.

### Fix directions (operator preference)

1. **Remove the visible checkbox** (hide native input via existing toggle pattern).
2. **Single switch** drives enable/disable; wire the same `PATCH /api/dev-speed` path.
3. Keep delay number input; badge/meta should track enabled state only from server status.

---

## BUG-status-03 — Hard-stop override does not reliably remember OFF across restarts

| Field | Value |
|-------|--------|
| **Status** | **Fixed** (operator dogfood 2026-07-30 on `fix/known-bugs`; pending main) |
| **Issue** | [#78](https://github.com/jtwolfe/project-elyra/issues/78) |
| **Severity now** | Med (budget safety + operator trust; override ON is dangerous if sticky) |
| **Severity later** | High if ON sticks unintentionally across restarts |
| **Area** | `UsageMeter` / `usage.json` `hard_stop_override`; `PATCH /api/usage`; Status override toggle |
| **Dogfood** | 2026-07-28 — override was ON at one point; after turning OFF it does **not** seem to stay off between restarts (or UI/state disagree) |
| **Related** | BUG-usage-01; design claims override **persists** in `usage.json` and default is OFF |

### Symptom

Hard-stop override can be switched ON successfully. Turning it OFF appears not to survive process/app restarts (comes back ON), or the Status toggle does not reflect the persisted OFF state. Exact failure mode not fully bisected (write path vs load path vs UI re-check from stale status).

### Design intent (do not regress)

- Override default **OFF**.
- When set, **persist** across restarts in `usage.json`.
- Fail-soft on corrupt usage file: **never invent override ON**.
- Override never skips usage `record`.

### Fix directions

1. Repro matrix: ON → restart (must stay ON); OFF → restart (must stay OFF); corrupt/missing file → OFF.
2. Confirm PATCH false writes disk; confirm load maps `hard_stop_override` / `override_active` into Status toggle without a race that re-applies last ON.
3. Glass: do not treat missing field as ON; seed toggle only from server.
4. Add/extend tests for OFF persistence if coverage is only ON-path happy.

---

## BUG-prompt-01 — System prompt is too hard; soften identity walls (post-memory review)

| Field | Value |
|-------|--------|
| **Status** | Open (defer) — **review after memory is up** (Phase 1 meal/store stable in dogfood) |
| **Issue** | [#79](https://github.com/jtwolfe/project-elyra/issues/79) |
| **Severity now** | Med (over-constrains presence / tone; feels like hard identity enforcement) |
| **Severity later** | Med–High if hard walls fight natural self from memory + identity digests |
| **Area** | `prompts/system.md` (loaded via `elyra/prompts/loader.py` → `loop/context.py`); possibly orient copy; tests that pin system-prompt wording (`tests/test_prompts_loader.py`, skill/tool name tests) |
| **Dogfood** | 2026-07-28 — Memory Context shows system channel with quite hard instructions; operator: *“this is too hard”* |

### Symptom

The fixed **system** block (esp. the leading `# Elyra system` framing and **Hard walls** posture) reads as rigid identity enforcement rather than light ethical guardrails. Walls that should be gentle (self ≠ user, honest tools, speak for glass) are packaged as hard product law that may over-prescribe how Elyra *is*, not only what must not be done.

### Intent for later review (not implement now)

1. Consider **removing or de-emphasizing** the `# Elyra system` element / title framing.
2. **General pass** over system (and related fixed prompts): keep necessary **ethical gentle walls**, drop or soften over-hard identity / process commandments.
3. Prefer identity + memory (SELF digest, atoms, orient) to carry *who she is*; system should not re-litigate a full persona constitution every hop.
4. Revisit after memory dogfood so meal composition and digests are real — do not soften blindly while Context still hard to inspect (**BUG-mem-ui-01**).

### What should probably stay (sketch — confirm in review)

- Self ≠ user (data-path safety; no writing prefs into the wrong digest).
- Do not pretend tools ran; speak for user-visible glass.
- No inventing private memories not in context.
- Secrets / grant stops as real safety.

### What to question in review

- Dense tool-family catalogs and growth pipelines duplicated in system when schemas + skills already teach them.
- Command tone (“Hard walls”, mandatory hop choreography) vs collaborative teammate tone.
- Anything that freezes a fixed Elyra persona against emerging memory/identity.

### Explicit non-goals until review

- Do not rewrite `prompts/system.md` in this note’s landing commit.
- Do not remove self≠user or tool honesty casually.
- Do not expand system into a longer constitution while “softening.”

### Related

- Memory Context surfaces this as fixed `system` snippet (**BUG-mem-ui-01** beautify may make the hardness more obvious).
- Identity digests: `prompts/seeds/identity/`, `data/identity/`.

---

## BUG-mem-p2-01 — Phase 2 semantic surface dead on text-only corpus (joint default)

| Field | Value |
|-------|--------|
| **Status** | **Fixed in code (PR-R1–R5, 2026-07-29)** — residual: **operator smoke dogfood verification pending** before full product sign-off |
| **Issue** | [#80](https://github.com/jtwolfe/project-elyra/issues/80) |
| **Severity now** | Low residual (code path restored; unconfirmed on live operator corpus) |
| **Severity later** | High if regressed — 2a seeds and meal semantic go empty again |
| **Area** | `elyra/memory/embed/*`, `index.py`, `lance_store.py`, `meal.py`, Vectors APIs / glass |
| **Dogfood** | 2026-07-28 on `grok-improvement-memory`: `vectors_ready≈32`, neighbors `channel=joint` → 0 hits; `channel=text` → real cosine hits; meal channels episodic+temporal only; `ann_index_built=false`, `search_mode=full`, `last_optimize=null` |
| **Fix ownership** | [design-phase-2-rectification.md](../design/memory/design-phase-2-rectification.md) PR-R1–R5; docs closeout PR-R6 |

### Symptom

Default product search used `channel=joint` while text-only encode wrote only `emb_text` (no `emb_joint`). Neighbors and meal semantic looked “off” or empty; optimize/rebuild targeted empty joint; glass had no channel control / weak empty-state honesty.

### Root chain (pre-fix)

Text-only encode → ready + `emb_text` only → search `joint` → 0 main hits; recent buffer stored text → invisible under joint search; meal hardcoded joint with no `no_hits` reason.

### Resolution (code)

| PR | Fix |
|----|-----|
| **PR-R1** | `resolve_search_channel` (`auto`); joint-for-single **copy**; eager joint-copy repair |
| **PR-R2** | Meal omit `no_hits` / `deduped` + `semantic_select_meta` |
| **PR-R3** | Optimize skip n=0 / below IVF min; no false `ann_index_built` |
| **PR-R4** | Lance-native main search; small-N `full_lance` |
| **PR-R5** | Vectors channel auto/toggle + honest empty/rebuild UX |

Architecture: [architecture/phase-2-semantic.md](memory/architecture/phase-2-semantic.md). Program: [stretch-2 README](memory/README.md).

### Residual

- Confirm on operator dogfood corpus with flags on (`backend=lance`, embed+semantic): neighbors under `auto`, meal semantic non-empty when data exists, `joint_repair_remaining→0`.
- Product default-on still requires Gate B — not this bug’s reopen.

---

## BUG-mem-lance-01 — LanceMemoryStore full load truncated by bare `to_arrow` (default limit ~10)

| Field | Value |
|-------|--------|
| **Status** | **Fixed (2026-07-29, `fcb5130`)** — **restart required** so process maps rebuild from full disk |
| **Issue** | [#81](https://github.com/jtwolfe/project-elyra/issues/81) |
| **Severity now** | Low residual (code fixed; live process still thin until restart) |
| **Severity later** | High if regressed — glass / meal / graph / traverse operate on ~10-atom prefix after every restart |
| **Area** | `elyra/memory/lance_store.py` (`_load`, migrate, promote, empty-check, health dual-count) |
| **Dogfood** | 2026-07-29 sealed run `docs/lance-debug1/evidence/2026-07-29-run-01/`: `count_rows`/`head`/`to_lance` = **386**; bare `to_arrow` = **10**; process `atom_count` ≈ 10 after open |
| **Fix ownership** | [design-fix-load-truncation.md](../lance-debug1/design-fix-load-truncation.md); inspection dossier [BUG-DOSSIER.md](../lance-debug1/BUG-DOSSIER.md); product fix commit `fcb5130` |

### Symptom

After process restart, Glass Memory / vectors / context meal / graph / directed traversal saw only a **thin** in-memory corpus (~**10** atoms) while on-disk `atoms.lance` held the full table (hundreds of rows). Mid-session `put_atom` looked fine (merge_insert); the drop appeared only on reopen.

### Root chain (pre-fix)

Bare `lancedb.Table.to_arrow()` on **0.20.x** is a **default-limit query of ~10 rows**, not a full-table scan. Product `_load` (and residual migrate/promote full-intent sites) treated it as full materialization → thin `_by_id` / emb maps. Disk and promote writes were intact.

### Resolution (code)

| Piece | Fix |
|-------|-----|
| **Helper** | `_materialize_table_arrow` / `_materialize_table_rows`: `head(count_rows)` primary; `to_lance().to_table()` fallback; **never** bare `to_arrow` for full-table intent; parity assert; `MemoryUnavailable` on failure |
| **`_load`** | Full materialize + load logging + `_disk_atom_count_at_load` |
| **Migrate / promote / empty-check** | Same helper; migrate fail-closed (no `rows=[]` wipe) |
| **Health** | Open-store dual-count: `disk_atom_count`, `atom_count_parity` |
| **Tests** | Reopen N=25; Phase-1 migrate N=15; FakeTable `head`; materialize unit paths |

Design: [lance-debug1/design-fix-load-truncation.md](../lance-debug1/design-fix-load-truncation.md). Package status: [lance-debug1/README.md](../lance-debug1/README.md).

### Residual / operator note

- **Restart** presence/glass after deploy so process maps rebuild from full disk. Phase-2 corpora need no data migration.
- Confirm on live dogfood: `health.atom_count` ≈ `disk_atom_count`, `atom_count_parity=true`; sample `get_atom` beyond the old 10-row prefix.

### Still-open adjacency (not fixed by this)

| ID | Relation |
|----|----------|
| **BUG-wake-02** | Post-restart “resume old thread” sanitation / recency — consumer of residual glass after restart; **not** Lance row-loss root. Still **Open**. |
| **BUG-mem-gpu-01** | Nemotron/embed on ROCm vs CPU — encode latency / device; **not** missing-row root. Umbrella **#82 closed**; residuals **#114** (busy dogfood) + **#115** (packaging). |

---

## BUG-mem-gpu-01 — Nemotron / embed path not on Radeon VII ROCm GPU (CPU fallback)

| Field | Value |
|-------|--------|
| **Status** | **Closed umbrella** ([#82](https://github.com/jtwolfe/project-elyra/issues/82), 2026-08-04) — continuous-encode **code** shipped; residuals split under v0.1-ready epic [#111](https://github.com/jtwolfe/project-elyra/issues/111) |
| **Issue** | [#82](https://github.com/jtwolfe/project-elyra/issues/82) (closed umbrella). Successors: **[#114](https://github.com/jtwolfe/project-elyra/issues/114)** C6a EncodeWorker busy-drain live dogfood; **[#115](https://github.com/jtwolfe/project-elyra/issues/115)** C6 GPU/env matrix packaging |
| **Severity now** | Med — **standalone** GPU encode works after Tensile inject; product **continuous encode code path shipped** (EncodeWorker + gate); **live operator dogfood of busy drain still pending** (#114); packaging/Tensile residual open (#115); wheel reinstall loses inject |
| **Severity later** | High when product default-on wants durable multi-device embed (CUDA / modern ROCm / CPU) + encode during live moments under meal budgets |
| **Area** | `elyra/memory/embed/runtime.py`, `embed/queue.py`, `embed/gate.py`, `embed/worker.py`, presence encode ownership, moment/meal/API gated encode, device select (`embed_device`), ROCm/CUDA wheels, operator setup (project-wide + optional Radeon VII dev path) |
| **Dogfood** | 2026-07-28 — CPU. **2026-07-29 AM** — A5 FAIL (no gfx906 Tensile). **2026-07-29 PM** — Tensile inject → A1–A7 **PASS** (`03` / `cuda:0`). Later same day: local `embed_device=rocm`; model **loads** on GPU at presence start; prior observation was idle-only drain (starvation under busy). **2026-08-03** — continuous encode stack (PR1–PR4) lands product drain path; architecture + this checklist updated (PR5). **2026-08-04** — #82 closed with #114+#115 split (board packaging hygiene). Ops: [radeon-vii-dev/NOTES-DOGFOOD.md](radeon-vii/NOTES-DOGFOOD.md) |
| **Ownership** | Gate B + [design-nemotron-runtime.md](../design/memory/design-nemotron-runtime.md) + [design-embed-async-encode-worker.md](../design/embed/design-embed-async-encode-worker.md); **not** Phase 2 rectification core (KD-R10). Packaging residual → **#115**; busy dogfood → **#114**; continuous-encode product path is KD-E17 evidence only |

### Symptom

Operator requests GPU/ROCm for Omni-Embed-Nemotron; without operator steps, official `+rocm7.2` wheel **enumerates** the device but **aborts matmul** (no gfx906 Tensile in the wheel). Presence stays on CPU when `embed_device=cpu` or fails compute if forced onto ROCm without the inject.

**2026-07-29 PM addendum:** Injecting `TensileLibrary_lazy_gfx906.dat` (+ kernels) from Arch `rocblas` 7.2.4-2 into the venv torch `rocblas/library` unlocks matmul and full Nemotron load/encode on Radeon VII. Helper: `docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py`. This is **operator local**, not a product packaging fix.

**2026-07-29 product-path observation (historical):** After setting local `embed_device = "rocm"`, presence appeared to **load** Nemotron onto GPU, but corpus drain was **idle-only** (`PresenceWorker._idle_memory_encode` only when no wake claimed). Under multi-hour directed work, pending backlog and empty semantic seeds were expected. That **in-moment / continuous drain gap** is the product-path slice of #82 (known-bugs gap #7) — addressed in code by continuous EncodeWorker (see below). Packaging/Tensile remains separate and **Open**.

### Related design intent

Portable encode contract: **CUDA / modern ROCm / CPU** as first-class product paths; fallback without hard-failing presence. Core imports must not require torch/GPU. Standalone script success ≠ product moment encode.

**Productization target (feature, tracked here so it is not lost):** keep a **generic modern** device story for operators; treat **Radeon VII / gfx906** as a **non-standard dev** profile, not the template for “all AMD.”

**Continuous encode (product path — code shipped, dogfood pending):** corpus drain is the PE process’s background encode job while up (when `semantic_enabled` + `embed_enabled`), with **lookup priority** for meal/graph/API free-text via `EmbedderGate`. Design: [design-embed-async-encode-worker.md](../design/embed/design-embed-async-encode-worker.md). Architecture invariant: [stretch-2/architecture/phase-2-semantic.md](memory/architecture/phase-2-semantic.md) §3 invariant 1. **Honesty (KD-E17 / KD5 packaging split):** continuous-encode **code** evidence alone did **not** close packaging or live dogfood — umbrella #82 closed **only** after successors **#114** (busy dogfood) + **#115** (packaging matrix) were opened under #111.

### Device matrix (desired product shape)

| Path | Role | What “works” means |
|------|------|---------------------|
| **CPU** | Default-safe / CI / no GPU | Encode correct; slow OK; continuous drain valid success for product-path law |
| **CUDA** | NVIDIA hosts with matching torch | `select_device` / load / encode on GPU; matmul green; continuous drain during busy |
| **ROCm (modern)** | AMD GPUs **in official PyTorch ROCm Tensile set** (typical current RDNA / CDNA listed by the wheel) | Host ROCm + matching `+rocm*` wheel; **A5 matmul green without inject**; then product continuous encode |
| **dev / Radeon VII (gfx906)** | **Non-standard** operator/dev only | Official wheel may enumerate HIP but omit Tensile → needs **explicit** inject (`00_inject_gfx906_tensile`) or equivalent; **must not** become the only documented AMD path |

**Portability rule (ops):** on any AMD card, install host ROCm + matching venv torch → run hard gate **matmul smoke** → only then load Nemotron. Green A5 ⇒ same product path as modern AMD. Red A5 with missing Tensile for `gfx####` ⇒ arch not in the wheel; VII-style inject is **arch-specific**, not “install any packages.” Avoid cargo-cult `HSA_OVERRIDE` / Tier B for ISA miss.

### Product continuous-encode path (code evidence — umbrella #82 closed with split)

Shipped on embed-async stack (2026-08-03), design [design-embed-async-encode-worker.md](../design/embed/design-embed-async-encode-worker.md):

| Slice | Status | Notes |
|-------|--------|--------|
| Thread-safe priority `EncodeQueue` (P1 create / P2 catchup) | **Code** | PR1; concurrent hook + worker safe |
| Continuous `EncodeWorker` + `encode_owner` single-owner | **Code** | PR2; drain while PE up; death → restart/gap drain, **not** permanent idle |
| `EmbedderGate` + `GatedEmbedder` (meal/graph/API lookup > bulk) | **Code** | PR2–PR3; between-atom only |
| Worker/gate health on Vectors / inspect | **Code** | PR4; `drain_ok_total`, owner, alive, gate waits — no secrets |
| Architecture + this checklist | **Docs** | PR5; invariant idle-only → continuous single-owner |
| Live busy dogfood (`drain_ok_total` / pending→ready under continuous wakes) | **Pending → [#114](https://github.com/jtwolfe/project-elyra/issues/114)** | C6a under #111; operator run still required |
| Packaging / Tensile / modern device matrix | **Open → [#115](https://github.com/jtwolfe/project-elyra/issues/115)** | C6 under #111; **not** closed by continuous encode |

**Acceptance criterion for product-path evidence (when dogfood runs on #114):** `drain_ok_total` (or ready count) rises during multi-minute busy work with pending atoms — CPU **or** GPU. Packaging green alone does **not** pass this criterion.

### Product continuous-encode dogfood checklist

Use with `backend=lance`, `semantic_enabled` + `embed_enabled` on, and (default) `encode_worker_enabled=true`. Mock or Nemotron; GPU optional for product-path law.

- [ ] **Create → pending → ready without idle claim** — promote during continuous wakes; vectors land while moments stay open
- [ ] **Busy progress** — multi-minute directed/social work; Vectors health `encode_worker.drain_ok_total` or ready count ↑ (CPU **or** GPU)
- [ ] **Lookup not starved (warm, text bulk)** — meal semantic / graph hop / free-text neighbors within budget; gate wait metrics may rise
- [ ] **API free-text gated** — concurrent bulk drain + Vectors free-text; no crash; serialized encode
- [ ] **Soft fail** — bad device / encode failure marks atom; PE process stays up
- [ ] **Worker death during busy** — force worker exception under continuous wakes → restart and/or gap drain; `drain_ok_total` still ↑ (no idle-claim wait)
- [ ] **embed off→on** — create pending with embed off; enable embed → ready without PE restart
- [ ] **Cold load / ensure** — consumer ensure returns None while loading; hop latency not +load_ms; meal may omit `encoder`
- [ ] **Rollback** — `encode_worker_enabled=false` → idle-only drain resumes
- [ ] **Honesty** — record continuous-encode evidence on **#114**; packaging matrix tracked on **#115** (umbrella #82 already closed with both successors named)

### Fix directions (later — packaging residual)

1. **Generic modern stack (product):** document and dogfood **CPU + CUDA + modern ROCm** as the supported matrix; `embed_device=auto` prefers real GPU when probe/matmul-healthy, else CPU. Do not require VII inject on modern cards.
2. **Non-standard Radeon VII / gfx906 (dev):** keep under `docs/radeon-vii-dev/`; optional setup flag or profile (e.g. “dev-radeon-vii”) that runs Tensile inject after torch install; never imply this is required for all AMD.
3. Durable gfx906: inject script (done); re-run after torch reinstall; optional future vendor/document only for that profile.
4. Vectors health: requested vs effective device honesty (optional polish) — worker metrics already expose process-local drain/gate state.
5. Do **not** block meal/channel product path on GPU presence.
6. Gate B: standalone smoke green; **operator continuous-encode dogfood** (checklist above) still required before semantic default-on.
7. **Operator continuous-encode dogfood (next):** run checklist during live work; capture moment ids + Vectors `encode_worker` health + log snippets. Criterion: `drain_ok_total` during busy — not packaging alone.
8. **Project-wide setup script (ongoing feature, not VII-only):** evolve `scripts/setup_venv.sh` / project setup into a single operator entry that can install extras, optional torch backend (cpu / cuda / rocm), probe devices, run smoke gates, and **optionally** apply non-standard profiles (e.g. Radeon VII Tensile inject). Track as iterative work — not a one-shot PR; keep secrets out of repo; keep machine-specific freezes out of the generic path.

### Dogfood — venv ROCm smoke after Tensile inject (2026-07-29)

| Field | Value |
|-------|--------|
| Status of BUG-mem-gpu-01 | **Umbrella #82 closed** 2026-08-04 (script path green; continuous-encode **code** shipped 2026-08-03; **live busy dogfood → #114**; packaging residual → **#115**) |
| torch_version | 2.13.0+rocm7.2 |
| hip_version | 7.2.53211 |
| device_name | AMD Radeon VII (gfx906); torch `"AMD Radeon Graphics"` |
| A1–A7 pass/fail | **A1–A7 PASS** (after `00_inject_gfx906_tensile`) |
| load_ms | ~18500 |
| encode_ms | ~2200 first / ~100 subsequent (**standalone `03` only**) |
| vram_peak_bytes | ~9580803584 |
| attn_impl if known | product tries flash_attention_2 then falls back |
| product worker path | local `embed_device=rocm` after inject; **GPU load seen** (2026-07-29); continuous EncodeWorker **code path** (2026-08-03); **operator busy drain dogfood not yet recorded** |
| Notes | Real GPU load+encode via `03` + `NemotronEmbedder(device="rocm")`. No “product GPU embed fully fixed.” Re-inject after torch reinstall. Continuous encode does not close packaging. |

Earlier same-day failure (pre-inject A5 red): [radeon-vii-dev/NOTES-DOGFOOD.md](radeon-vii/NOTES-DOGFOOD.md) PR3/PR4 sections. Product-path note: same file PR5 / product follow-on.

### Related

- **BUG-mem-lance-01** — full load truncation (**Fixed**); not the GPU/embed root. Expand_ms / encode latency remain this bug’s class.
- [design-embed-async-encode-worker.md](../design/embed/design-embed-async-encode-worker.md) — continuous EncodeWorker + gate (product continuous-encode path).
- [stretch-2/architecture/phase-2-semantic.md](memory/architecture/phase-2-semantic.md) — corpus encode single-owner invariant (replaces idle-only product law).
- [radeon-vii-dev/NOTES-DOGFOOD.md](radeon-vii/NOTES-DOGFOOD.md) — switch, inject, A5/A7 green, product-path open questions, portability notes; residuals **#114** / **#115**.
- [design-v0.1-ready-board-recategorization.md](../design/board/design-v0.1-ready-board-recategorization.md) — KD5/KD11 close policy for #82 → C6a+C6.
- [radeon-vii-dev/STACK-INVENTORY.md](radeon-vii/STACK-INVENTORY.md) — post-switch inventory / A5 status.
- [radeon-vii-dev/scripts/00_inject_gfx906_tensile.py](../radeon-vii-dev/scripts/00_inject_gfx906_tensile.py) — **dev-only** gfx906 path, not generic AMD.
- Project setup today: [docs/README.md](../README.md) / `scripts/setup_venv.sh` — to grow into multi-backend setup (ongoing).
- Operator start (LuxPrimata / new terminal): [radeon-vii-dev/README.md](radeon-vii/README.md) § *New terminal session — start Elyra*.
- v0.1 promotion / gym / meal size & chat-chain notes: [promotion-discussion/README.md](../promotion-discussion/README.md).

---

## BUG-meal-01 — Raise outer meal budget toward ~250k (~50% of model window)

| Field | Value |
|-------|--------|
| **Status** | **Fixed** on `main` (`0cc9b42`, 2026-07-30) |
| **Issue** | [#91](https://github.com/jtwolfe/project-elyra/issues/91) (closed) |
| **Severity now** | — (resolved) |
| **Severity later** | Monitor cost/latency at 250k; optional ceiling via override |
| **Area** | `meal_budget_fraction`, `sliding_input_tokens`, meal compose budget, `in_turn_max_tokens`, glass context rail |
| **Design home** | [promotion-discussion/README.md](../promotion-discussion/README.md) §5.6 |; **product implement:** [`design-instance-continuity-product-implement.md`](../design/memory/design-instance-continuity-product-implement.md)

### Goal

Step outer meal from **50k** (~10% of 500k window) toward **~250k** (~50%), measuring cost/latency; raise/review in-turn budget together. Does not fix missing glass chat alone (**BUG-meal-03**).

### Fix (product) — shipped

- Primary knob: `meal_budget_fraction` of `model_context_window_tokens` (default **0.5** → **250k** @ 500k; product slider max **0.75**, hard max **1.0**).
- Persisted `data/runtime/meal_budget.json` + `PATCH /api/meal-budget`; does not mutate frozen Settings.
- Raise slider ceiling: **`elyra start --max-meal-override PCT`** (percent 1–100; e.g. `100` → full model window).
- **Policy A:** effective tokens apply to **both** sliding and in-turn caps on product paths (worker meal compose, do-loop, inspect, status context).
- Glass Status Context card: range + readout; bars/gold mark read-only monitoring.
- Also on this merge tip: provider HTTP timeout catch (`error_class=provider_timeout`) as operational report (board draft for future rate-limit work).

---

## BUG-meal-02 — Period summary atoms: real LLM narratives (not template-only)

| Field | Value |
|-------|--------|
| **Status** | **Fixed** on `main` (2026-07-31) — LLM period ladder + meal tip policy; dogfood OK |
| **Issue** | [#92](https://github.com/jtwolfe/project-elyra/issues/92) |
| **Severity now** | — (closed) |
| **Severity later** | — |
| **Area** | `elyra/memory/ladder.py`, summary atom bodies; residual of closed [#72](https://github.com/jtwolfe/project-elyra/issues/72) |
| **Design home** | [design/memory/design-episodic-summary-ladder-llm.md](../design/memory/design-episodic-summary-ladder-llm.md) |

### Goal

Replace (or supplement) template-first period summaries with **budgeted LLM narratives** of each ladder window’s content; keep template fallback.

### Resolution

Shipped on `feature/92` then merged to `main`: write scales `1h→1y`, hourly cascade, versioned coarser tips, summary edges lite, meal tip-only + recent 1h band, LLM `summary_mode`, Context ladder status + rebuild button, meal packs non-template tips only, age gates off by default. Follow-ons: #106 taxonomy, #107 atom truncation eval.

---

## BUG-meal-03 — Instance continuity: glass-tail + sticky directed keep

| Field | Value |
|-------|--------|
| **Status** | **Fixed** on `main` (`2ea3580`, 2026-07-31) — glass-tail, framing, sticky keep, semantic seed; S4/S6 deferred|
| **Issue** | [#93](https://github.com/jtwolfe/project-elyra/issues/93) |
| **Severity now** | High for dogfood (chat-amnesic / wrong wait-reply framing when memory meal on) |
| **Severity later** | High for multi-moment + multi-hour instance memory |
| **Area** | Memory meal rebuild; glass-tail band; wait/interject/restart paths; directed_keep tray TTL/LRU |
| **Design home** | [design/memory/design-instance-continuity-glass-tail-directed-keep.md](../design/memory/design-instance-continuity-glass-tail-directed-keep.md) (refined from review DRAFT-EXTENSIONS) |
| **Implement plan** | [design/memory/design-instance-continuity-implement-plan.md](../design/memory/design-instance-continuity-implement-plan.md) — ordered product PRs S1 glass-tail → S2 framing dual-write → S3 sticky keep B5+B5b → S4 merge/confirm → S5 recall nudge → S6 graph UX defer |
| **Review report** | [stretch-2/meal-continuity-review/REPORT.md](../stretch-2/meal-continuity-review/REPORT.md) — fault isolation (B1/B12 co-primary; B5+B5b sticky keep dual kill); evidence `meal-continuity-review/evidence/sa9b-e6d460f2/` |
| **Also** | [design/memory/design-meal-formation-continuity-review-plan.md](../design/memory/design-meal-formation-continuity-review-plan.md) (review method; done); [design/memory/design-instance-continuity-implement-plan.md](../design/memory/design-instance-continuity-implement-plan.md) (execute-plan); [promotion-discussion/README.md](../promotion-discussion/README.md) §4; Phase 2a keep channel |

### Goal

When memory meal is active, preserve a **well-formed instance continuity package** for every next hop:

1. **Glass-tail** — durable immediate chat (roles + order) across moments/restarts.  
2. **Sticky directed keep** — intentional pins with slow decay (hours ≤ day), token LRU, restart-safe; stop moment-end wipe (**B5**) **and** meal-wire without `moment_id` equality filter (**B5b**).  
3. **Path parity** — wait_reply / interject / timeout / restart cannot shatter the tip while episodic bulk still looks healthy.

Implement order: **S1 glass-tail → S2 framing dual-write + path tests → S3 sticky keep (B5+B5b)**. Coordinate with meal budget (**BUG-meal-01**, fixed). Adjacent: **#68** wake-02 (wrong work thread sanitation), not the same fix. Dogfood anchor: wait_reply rockets moment `e6d460f2-4087-42cd-870f-d34a89b6feaf` (2026-07-30).

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
