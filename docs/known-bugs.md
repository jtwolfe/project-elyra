# Known bugs / deferred product issues

Durable backlog for **observed product bugs** that are not burning now but
should not be forgotten. Prefer short, dogfood-linked entries over design
essays. When an item is fixed, mark **Status** and leave a one-line resolution
(or move to archive).

| Field | Value |
|-------|--------|
| **Branch** | `main` (product tip after #66) |
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
| `BUG-glass-01` | [#70](https://github.com/jtwolfe/project-elyra/issues/70) (open) | Open (defer) |
| `BUG-glass-02` | [#71](https://github.com/jtwolfe/project-elyra/issues/71) (open) | Open (defer) — product IA, not a functional defect |
| `BUG-mem-ui-01` | [#72](https://github.com/jtwolfe/project-elyra/issues/72) (open) | Open (defer) |
| `BUG-mem-ui-02` | [#73](https://github.com/jtwolfe/project-elyra/issues/73) (open) | Open (defer) |
| `BUG-mem-ui-03` | [#74](https://github.com/jtwolfe/project-elyra/issues/74) (open) | Open (defer) |
| `BUG-chat-01` | [#75](https://github.com/jtwolfe/project-elyra/issues/75) (open) | Open (defer) — feature gap |
| `BUG-chat-02` | [#84](https://github.com/jtwolfe/project-elyra/issues/84) (open) | Open — soft newlines lost in Glass chat, kept in atoms |
| `BUG-tts-01` | [#85](https://github.com/jtwolfe/project-elyra/issues/85) (open) | Open — TTS needs text sanitation before service call |
| `BUG-status-01` | [#76](https://github.com/jtwolfe/project-elyra/issues/76) (open) | Fixed on fix branch (pending main) |
| `BUG-status-02` | [#77](https://github.com/jtwolfe/project-elyra/issues/77) (open) | Open (defer) |
| `BUG-status-03` | [#78](https://github.com/jtwolfe/project-elyra/issues/78) (open) | Open (defer) |
| `BUG-prompt-01` | [#79](https://github.com/jtwolfe/project-elyra/issues/79) (open) | Open (defer) — review after memory is up (Phase 1 meal/store stable… |
| `BUG-mem-p2-01` | [#80](https://github.com/jtwolfe/project-elyra/issues/80) (open) | Fixed in code (PR-R1–R5, 2026-07-29) — residual: operator smoke dog… |
| `BUG-mem-lance-01` | [#81](https://github.com/jtwolfe/project-elyra/issues/81) (closed) | Fixed (2026-07-29, `fcb5130`) — restart required so process maps re… |
| `BUG-mem-gpu-01` | [#82](https://github.com/jtwolfe/project-elyra/issues/82) (open) | Open (defer to Gate B / runtime) |

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

## BUG-glass-01 — Moments panel looks like raw JSON (needs beautify pass)

| Field | Value |
|-------|--------|
| **Status** | Open (defer) |
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
| **Status** | Open (defer) — product IA, not a functional defect |
| **Issue** | [#71](https://github.com/jtwolfe/project-elyra/issues/71) |
| **Severity now** | Low (nav clutter / split brain: Moments vs Memory) |
| **Severity later** | Med once Memory (context/atoms/vectors/graph) is the default operator mental model |
| **Area** | Glass nav + panels; Memory tabs (context / atoms / vectors / graph) |
| **Dogfood** | 2026-07-28 operator request: Moments should sit next to Context, Atoms, Vectors, Graph |

### Symptom

Moments is a top-level nav peer of Memory. After Stretch 2 Phase 1, operators think “history / tape / meal” as one place; two panels force context-switching and duplicate mental models (moment tape vs atom store).

### Fix directions

1. Add a **Moments** (or **Tape**) tab under Memory next to Context / Atoms / Vectors / Graph.
2. Retire or demote top-level Moments nav once the subsection is at parity.
3. Preserve deep-link / refresh behavior; do not break moment-id filters used by Atoms.
4. Pair with **BUG-glass-01** beautify so the moved panel is worth opening.

### Explicit non-goals

- Do not delete the moment store or change moment = do-loop semantics.
- Do not block on Vectors/Graph product before the IA move.

---

## BUG-mem-ui-01 — Memory Context needs beautify + review of summary generation

| Field | Value |
|-------|--------|
| **Status** | Open (defer) |
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

1. **Beautify:** channel sections, scale/window badges, token budget bars, expandable full content (use `content_chars` / detail endpoint).
2. **Generation review:** when ladder runs (idle `refresh_due` vs on-compose), skip-unchanged behavior, child-preference vs raw, highlight ranking, whether template-only is enough for dogfood.
3. Document operator-visible “this is a snippet of N chars” so truncation is not mistaken for empty memory.
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
| **Status** | Open (defer) |
| **Issue** | [#74](https://github.com/jtwolfe/project-elyra/issues/74) |
| **Severity now** | Med (actively interferes with inspection) |
| **Severity later** | Med |
| **Area** | Glass poll loop (`app.js` `tick` ~1.5s → `refreshMemory` / `refreshMemoryAtoms` when Memory active); possibly status/system strip re-render |
| **Dogfood** | 2026-07-28 — inspector keeps flashing; system update also impacts ability to select text |

### Symptom

While on Memory → Atoms (inspector/detail), the UI **flashes** on a regular cadence. Selecting text for copy is interrupted or reset — likely full DOM rebuild from the global poll. Operator also reports **system update** (status rail / status refresh) interfering with text selection.

### Likely cause (unverified)

`setInterval(tick, 1500)` always runs `refreshStatus()` + `refreshMessages()`, and when `activePanel === "memory"` also `refreshMemory()`, which rebuilds Context/Atoms DOM even when data is unchanged.

### Fix directions

1. Diff-then-patch or skip re-render when payload hash/etag unchanged.
2. Do not replace nodes that contain an active text selection / focus.
3. Pause aggressive panel refresh while pointer is selecting or detail is focused.
4. Audit status/system strip updates for the same full-replace pattern.

---

## BUG-chat-01 — Chat needs equation / math rendering (LaTeX or equivalent)

| Field | Value |
|-------|--------|
| **Status** | Open (defer) — feature gap |
| **Issue** | [#75](https://github.com/jtwolfe/project-elyra/issues/75) |
| **Severity now** | Low–Med (depends on dogfood topics; calculator / STEM chats unreadable) |
| **Severity later** | Med for teaching / technical work |
| **Area** | Glass chat message render (`app.js` message HTML); speak / model content pipeline |
| **Dogfood** | 2026-07-28 operator request — add equation rendering (LaTeX? whatever format models emit) |

### Symptom

Math in chat shows as raw `$...$` / `$$...$$` / `\(...\)` (or similar) instead of rendered equations.

### Fix directions

1. Inventory what Grok / tools actually emit (KaTeX-friendly `$`, `$$`, code fences, Unicode).
2. Client-side render with a maintained math library (e.g. KaTeX) on assistant/user bubbles; CSP-safe, no remote fonts required if vendored.
3. Fallback: keep raw source on render failure; never execute untrusted HTML.
4. Decide speak-tool vs model-beat parity (only surface promoted speak, or also debug model content).

### Explicit non-goals

- Do not treat this as memory/atom formatting (separate from **BUG-mem-ui-***).
- Do not pull a heavy full Markdown+math stack without need.

---

## BUG-chat-02 — Soft newlines from chat input missing in Glass history (present in atoms)

| Field | Value |
|-------|--------|
| **Status** | Open |
| **Issue** | [#84](https://github.com/jtwolfe/project-elyra/issues/84) |
| **Severity now** | Low–Med (multi-line chat composition looks flattened in history) |
| **Severity later** | Med for any structured prompts / poetry / lists typed with Enter in the composer |
| **Area** | Glass chat history render (`elyra/runtime/web/app.js` `renderMarkdown` / message bubbles); user social text path is fine into store |
| **Dogfood** | 2026-07-30 operator: newlines typed in chat input **do not appear** in Glass chat history, but **do appear** in Memory atoms |

### Symptom

Operator composes a multi-line message in the Glass chat input (soft line breaks via Enter). After send:

- **Glass Chat** history shows the text as a **single run-on paragraph** (line breaks collapsed / invisible).
- **Memory atoms** (and likely raw store / meal) still contain the **real newlines** — so fidelity is not lost on the backend, only on Glass chat presentation.

Confirms storage path is OK; defect is **display** of user (and possibly assistant) bubbles after markdown-ish rendering.

### Likely cause (unverified — dig on fix)

`renderMarkdown` flushes paragraph lines with `para.join(" ")` — single newlines become spaces, Markdown-style. Chat soft-breaks are not converted to `<br>` and message CSS may not use `white-space: pre-wrap` for user bubbles. Atoms UI shows raw-ish text, so newlines survive there.

### Fix directions

1. For **chat history bubbles**, preserve soft line breaks: either treat single `\n` as `<br>` (GFM-style / chat-composer style) or render user messages with `white-space: pre-wrap` and skip aggressive join-space collapse.
2. Prefer fixing **user** role first (composer source of truth); then decide if assistant speak should match.
3. Regression: multi-line user message still looks multi-line after refresh / re-open Glass; atom body unchanged.
4. Do not strip newlines from the wire format or atom promote path.

### Explicit non-goals

- Do not change atom schema or meal composition for this bug.
- Do not require full CommonMark hard-break rules unless we adopt them product-wide.
- Separate from **BUG-chat-01** (math); fix can land without KaTeX.

### Related

- **BUG-chat-01** — math rendering (same chat bubble pipeline).
- **BUG-mem-ui-*** — atoms already show newlines; good contrast for dogfood.

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
| **Status** | Fixed on `fix/BUG-status-01-76` (pending merge to main) |
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
| **Status** | Open (defer) |
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
| **Status** | Open (defer) |
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
| **Fix ownership** | [design-phase-2-rectification.md](stretch-2/design-phase-2-rectification.md) PR-R1–R5; docs closeout PR-R6 |

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

Architecture: [architecture/phase-2-semantic.md](stretch-2/architecture/phase-2-semantic.md). Program: [stretch-2 README](stretch-2/README.md).

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
| **Fix ownership** | [design-fix-load-truncation.md](lance-debug1/design-fix-load-truncation.md); inspection dossier [BUG-DOSSIER.md](lance-debug1/BUG-DOSSIER.md); product fix commit `fcb5130` |

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

Design: [lance-debug1/design-fix-load-truncation.md](lance-debug1/design-fix-load-truncation.md). Package status: [lance-debug1/README.md](lance-debug1/README.md).

### Residual / operator note

- **Restart** presence/glass after deploy so process maps rebuild from full disk. Phase-2 corpora need no data migration.
- Confirm on live dogfood: `health.atom_count` ≈ `disk_atom_count`, `atom_count_parity=true`; sample `get_atom` beyond the old 10-row prefix.

### Still-open adjacency (not fixed by this)

| ID | Relation |
|----|----------|
| **BUG-wake-02** | Post-restart “resume old thread” sanitation / recency — consumer of residual glass after restart; **not** Lance row-loss root. Still **Open**. |
| **BUG-mem-gpu-01** | Nemotron/embed on ROCm vs CPU — encode latency / device; **not** missing-row root. Still **Open**. |

---

## BUG-mem-gpu-01 — Nemotron / embed path not on Radeon VII ROCm GPU (CPU fallback)

| Field | Value |
|-------|--------|
| **Status** | **Open** (defer to Gate B / runtime) |
| **Issue** | [#82](https://github.com/jtwolfe/project-elyra/issues/82) |
| **Severity now** | Med — **standalone** GPU encode works after Tensile inject; product **moment/meal encode path not verified**; wheel reinstall loses inject |
| **Severity later** | High when product default-on wants durable multi-device embed (CUDA / modern ROCm / CPU) + encode during live moments under meal budgets |
| **Area** | `elyra/memory/embed/runtime.py`, encode queue / idle worker, moment/meal integration, device select (`embed_device`), ROCm/CUDA wheels, operator setup (project-wide + optional Radeon VII dev path) |
| **Dogfood** | 2026-07-28 — CPU. **2026-07-29 AM** — A5 FAIL (no gfx906 Tensile). **2026-07-29 PM** — Tensile inject → A1–A7 **PASS** (`03` / `cuda:0`). Later same day: local `embed_device=rocm`; model **loads** on GPU at presence start; operator **suspects embeddings may not run during a moment** (not yet proven). Ops: [radeon-vii-dev/NOTES-DOGFOOD.md](radeon-vii-dev/NOTES-DOGFOOD.md) |
| **Ownership** | Gate B + [design-nemotron-runtime.md](stretch-2/design-nemotron-runtime.md); **not** Phase 2 rectification core (KD-R10). Rectification only optional device honesty in health |

### Symptom

Operator requests GPU/ROCm for Omni-Embed-Nemotron; without operator steps, official `+rocm7.2` wheel **enumerates** the device but **aborts matmul** (no gfx906 Tensile in the wheel). Presence stays on CPU when `embed_device=cpu` or fails compute if forced onto ROCm without the inject.

**2026-07-29 PM addendum:** Injecting `TensileLibrary_lazy_gfx906.dat` (+ kernels) from Arch `rocblas` 7.2.4-2 into the venv torch `rocblas/library` unlocks matmul and full Nemotron load/encode on Radeon VII. Helper: `docs/radeon-vii-dev/scripts/00_inject_gfx906_tensile.py`. This is **operator local**, not a product packaging fix.

**2026-07-29 product-path observation (unconfirmed):** After setting local `embed_device = "rocm"`, presence appears to **load** Nemotron onto GPU, but the operator is **not sure embeddings actually run during a moment** (encode queue / idle / meal-path vs load-only). Treat as open dogfood — may be timing, queue, flags, or observation gap; **not** sealed as a separate root cause yet.

### Related design intent

Portable encode contract: **CUDA / modern ROCm / CPU** as first-class product paths; fallback without hard-failing presence. Core imports must not require torch/GPU. Standalone script success ≠ product moment encode.

**Productization target (feature, tracked here so it is not lost):** keep a **generic modern** device story for operators; treat **Radeon VII / gfx906** as a **non-standard dev** profile, not the template for “all AMD.”

### Device matrix (desired product shape)

| Path | Role | What “works” means |
|------|------|---------------------|
| **CPU** | Default-safe / CI / no GPU | Encode correct; slow OK |
| **CUDA** | NVIDIA hosts with matching torch | `select_device` / load / encode on GPU; matmul green |
| **ROCm (modern)** | AMD GPUs **in official PyTorch ROCm Tensile set** (typical current RDNA / CDNA listed by the wheel) | Host ROCm + matching `+rocm*` wheel; **A5 matmul green without inject**; then product encode |
| **dev / Radeon VII (gfx906)** | **Non-standard** operator/dev only | Official wheel may enumerate HIP but omit Tensile → needs **explicit** inject (`00_inject_gfx906_tensile`) or equivalent; **must not** become the only documented AMD path |

**Portability rule (ops):** on any AMD card, install host ROCm + matching venv torch → run hard gate **matmul smoke** → only then load Nemotron. Green A5 ⇒ same product path as modern AMD. Red A5 with missing Tensile for `gfx####` ⇒ arch not in the wheel; VII-style inject is **arch-specific**, not “install any packages.” Avoid cargo-cult `HSA_OVERRIDE` / Tier B for ISA miss.

### Fix directions (later)

1. **Generic modern stack (product):** document and dogfood **CPU + CUDA + modern ROCm** as the supported matrix; `embed_device=auto` prefers real GPU when probe/matmul-healthy, else CPU. Do not require VII inject on modern cards.
2. **Non-standard Radeon VII / gfx906 (dev):** keep under `docs/radeon-vii-dev/`; optional setup flag or profile (e.g. “dev-radeon-vii”) that runs Tensile inject after torch install; never imply this is required for all AMD.
3. Durable gfx906: inject script (done); re-run after torch reinstall; optional future vendor/document only for that profile.
4. Vectors health: requested vs effective device honesty (optional polish).
5. Do **not** block meal/channel product path on GPU presence.
6. Gate B: standalone smoke green; **product worker + in-moment encode dogfood still required** before semantic default-on.
7. **Moment encode dogfood (next):** during a live moment, confirm encode queue activity, atom vectors land, health/device stay ROCm, and semantic expand is not load-only. Capture moment id + log snippets when repro’d.
8. **Project-wide setup script (ongoing feature, not VII-only):** evolve `scripts/setup_venv.sh` / project setup into a single operator entry that can install extras, optional torch backend (cpu / cuda / rocm), probe devices, run smoke gates, and **optionally** apply non-standard profiles (e.g. Radeon VII Tensile inject). Track as iterative work — not a one-shot PR; keep secrets out of repo; keep machine-specific freezes out of the generic path.

### Dogfood — venv ROCm smoke after Tensile inject (2026-07-29)

| Field | Value |
|-------|--------|
| Status of BUG-mem-gpu-01 | **Still Open** (script path green; product moment encode **unverified**) |
| torch_version | 2.13.0+rocm7.2 |
| hip_version | 7.2.53211 |
| device_name | AMD Radeon VII (gfx906); torch `"AMD Radeon Graphics"` |
| A1–A7 pass/fail | **A1–A7 PASS** (after `00_inject_gfx906_tensile`) |
| load_ms | ~18500 |
| encode_ms | ~2200 first / ~100 subsequent (**standalone `03` only**) |
| vram_peak_bytes | ~9580803584 |
| attn_impl if known | product tries flash_attention_2 then falls back |
| product worker path | local `embed_device=rocm` after inject; **GPU load seen**; **in-moment encode not confirmed** |
| Notes | Real GPU load+encode via `03` + `NemotronEmbedder(device="rocm")`. No “product GPU embed fixed.” Re-inject after torch reinstall. Dig later: does encode run during a moment? |

Earlier same-day failure (pre-inject A5 red): [radeon-vii-dev/NOTES-DOGFOOD.md](radeon-vii-dev/NOTES-DOGFOOD.md) PR3/PR4 sections. Product-path note: same file PR5 / product follow-on.

### Related

- **BUG-mem-lance-01** — full load truncation (**Fixed**); not the GPU/embed root. Expand_ms / encode latency remain this bug’s class.
- [radeon-vii-dev/NOTES-DOGFOOD.md](radeon-vii-dev/NOTES-DOGFOOD.md) — switch, inject, A5/A7 green, product-path open questions, portability notes; bug stays **Open**.
- [radeon-vii-dev/STACK-INVENTORY.md](radeon-vii-dev/STACK-INVENTORY.md) — post-switch inventory / A5 status.
- [radeon-vii-dev/scripts/00_inject_gfx906_tensile.py](radeon-vii-dev/scripts/00_inject_gfx906_tensile.py) — **dev-only** gfx906 path, not generic AMD.
- Project setup today: [docs/README.md](README.md) / `scripts/setup_venv.sh` — to grow into multi-backend setup (ongoing).
- Operator start (LuxPrimata / new terminal): [radeon-vii-dev/README.md](radeon-vii-dev/README.md) § *New terminal session — start Elyra*.
- v0.1 promotion / gym / meal size & chat-chain notes: [promotion-discussion/README.md](promotion-discussion/README.md).

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
