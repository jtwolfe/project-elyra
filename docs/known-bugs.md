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

## BUG-glass-01 — Moments panel looks like raw JSON (needs beautify pass)

| Field | Value |
|-------|--------|
| **Status** | Open (defer) |
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

## BUG-status-01 — Status page does not scroll

| Field | Value |
|-------|--------|
| **Status** | Open (defer) |
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

## BUG-mem-gpu-01 — Nemotron / embed path not on Radeon VII ROCm GPU (CPU fallback)

| Field | Value |
|-------|--------|
| **Status** | **Open** (defer to Gate B / runtime) |
| **Severity now** | Med — dogfood encode slow on CPU; mock path fine for CI |
| **Severity later** | High when product default-on wants real Nemotron latency under meal budgets |
| **Area** | `elyra/memory/embed/runtime.py`, device select (`embed_device`), optional `memory-embed` extra / ROCm wheels |
| **Dogfood** | 2026-07-28 — Radeon VII / ROCm environment; Nemotron encode effective device CPU |
| **Ownership** | Gate B + [design-nemotron-runtime.md](stretch-2/design-nemotron-runtime.md); **not** Phase 2 rectification core (KD-R10). Rectification only optional device honesty in health |

### Symptom

Operator requests GPU/ROCm for Omni-Embed-Nemotron; runtime lands on CPU (or mock). Idle encode and optional warm paths are slower than hardware suggests; does not by itself empty joint search once PR-R1 repair/encode is in place.

### Related design intent

Portable encode contract: CUDA / ROCm / CPU fallback without hard-failing presence. Core imports must not require torch/GPU.

### Fix directions (later)

1. ROCm wheel / quant matrix validation on operator hardware.
2. Vectors health: requested vs effective device honesty (optional polish).
3. Do **not** block meal/channel product path on GPU presence.
4. Gate B checklist before product default-on of semantic flags.

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
