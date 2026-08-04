# Design draft: Instance continuity — glass-tail + sticky directed keep

| Field | Value |
|-------|--------|
| **Class** | DESIGN |
| **Document** | Product / implementation design (refined after meal continuity review) |
| **Product** | project-elyra |
| **Date** | 2026-07-30 |
| **Status** | **Ready for implement plan** — continuity review report applied; OQ4/6/7 locked with evidence; OQ1/2/3/5 provisional; OQ8 prefer-lock after S1 |
| **Issue** | [#93](https://github.com/jtwolfe/project-elyra/issues/93) — `BUG-meal-03` (scope expanded) |
| **Bug id** | `BUG-meal-03` |
| **Branch** | Content origin: `design/BUG-meal-03-93-instance-continuity`; refined after `docs/investigations/meal-continuity-review/` |
| **Depends on** | Memory meal active; meal budget fraction shipped (#91); Phase 2a directed_keep channel exists |
| **Review report** | [meal-continuity-review/REPORT.md](../../investigations/meal-continuity-review/REPORT.md) (DRAFT-EXTENSIONS §6 applied here) |
| **Related design** | [design-context-meal-composition.md](design-context-meal-composition.md), [design-phase-2a-implementation.md](design-phase-2a-implementation.md), [promotion-discussion/README.md](../../promotion-discussion/README.md) §4–5 |
| **Adjacent issues** | [#68](https://github.com/jtwolfe/project-elyra/issues/68) wake-02 (post-restart wrong work thread); [#92](https://github.com/jtwolfe/project-elyra/issues/92) LLM summaries (episodic quality, not tip); provider timeout board draft |

---

## 1. Purpose

Define how Project Elyra keeps a **well-formed “memory of instance”** for every next model call so that:

- **Immediate chat** survives moment boundaries and process restarts (**glass-tail**).
- **Intentionally pinned** material survives hours with slow decay (**sticky directed keep**).
- **Path variants** (wait reply, interject, timeout, restart, continue) cannot shatter the tip of continuity while episodic bulk still looks healthy.

This document is the **design home for #93** after dogfood expanded the issue from “add a chat band” to **instance continuity** (tip + intentional working set). The **meal formation / continuation review report** ([REPORT.md](../../investigations/meal-continuity-review/REPORT.md)) refined this draft; next step is an implement plan / execute-plan on a fix branch.

---

## 2. Problem statement

### 2.1 Dogfood anchor (2026-07-30)

| Surface | Content |
|---------|---------|
| **Glass** | User: *“what is the coolest thing you remember about rockets?”* |
| **Speak** | Status about closed philosophy/fabric threads — not an answer to rockets |
| **Path** | `wait_reply` after time + `wait_user` (“Anything else?”) |
| **Moment** | `e6d460f2-4087-42cd-870f-d34a89b6feaf` |
| **Model reasoning** | Framed as wait-social after time; no rockets content in reasoning |

Glass showed a normal Q→A pair. The model outer meal did not present a dialogue-shaped **tip**, so episodic “closed work” framing won.

**Rockets failure class (normative):** ignored user question **+** missing prior assistant glass in outer — not missing wait-prompt text alone. Wait prompt lives in `waits.json` / prior tape, often **not** as a glass row.

### 2.2 Prince Rupert’s drop

- **Bulb** = episodic (+ semantic when seeded) + ledger. Looks rich in Memory UI.
- **Tip** = last glass turns + wake truth + wait setup, with **true roles and order**.
- **Smash the tip** → confident wrong speak even when the bulb is full.

Raising meal budget (#91) thickened the bulb. It did not harden the tip. **Larger residual R ≠ tip channel.**

### 2.3 Why interjection “never” fails this way

| | **Interjection** (`in_moment`) | **Wait reply / new social moment** |
|--|--|--|
| Landing | In-turn **chain** as user obs | New moment → **outer meal rebuild** |
| Continuity | Outer already fixed for this moment | Must **reconstitute** world from meal |
| Failure mode | Delayed until safe drain | Wrong story of the world |

Interject rides continuity. Wait-reply / post-wait continuation must rebuild it. That asymmetry is design-critical, not anecdotal.

### 2.4 What is broken today (normative facts)

When memory meal is active (`_memory_meal_active()`):

```text
outer = system → episodic → semantic → directed_keep → temporal → orient
(+ hybrid single wake glass row for media / wake id when needed)
chain = do-loop tool hops only (ephemeral to open moment)
```

- **No full sliding glass** and **no dedicated glass-tail band**.
- Glass is used for media expand + wake id correlation, not chat continuity.
- Meal items are largely **`role: user` host blocks** with labels — not alternating user/assistant turns.
- **Directed keep** is Phase 2a **last finish wins**, feeds next compose, **cleared at moment end**, effectively process-local — not a multi-hour sticky tray.
- **Meal wire (B5b):** `get_last_confirmed_keep` / compose requires `snap.moment_id == open_moment_id` (or None) — keep confirmed in moment A never packs into moment B even mid-lifetime.
- **Semantic** is momentary (seed + ANN); encode lag can leave new observations unindexed for minutes after speak.
- `why_now` for wait_reply is often `wait reply (wait_id=…)` **without** user text.

Legacy path (memory off) still centers sliding glass — do not regress that path.

### 2.5 Rockets fix-evaluation summary (review)

Keep **empirical** vs **normative** labels distinct (from [REPORT.md](../../investigations/meal-continuity-review/REPORT.md) SA-9b + EDGE-MATRIX P2):

> **Empirical SA-9b recompose ranking** (hop-0 carriers in outer + tape): **B12 → B11 → B3 → B7 → B1**.  
> **Normative fix / structural priority** (implement order): **B12 + B1 co-primary → B3 → B7 → B11** (epi mass under tip floor once a glass-tail channel exists).  
> Elevating B1 for S1 is fix-priority (missing channel), not a re-rank of the empirical list. Implementation must evaluate **tail-only vs tail+orient snippet**, not B1 alone.

| Bucket | Mechanism (short) |
|--------|-------------------|
| **B12** | `_why_now("wait_reply")` = wait_id only + skill bias → wait-ceremony framing |
| **B1** | Memory meal has **no** glass-tail band |
| **B3** | Meal items default `role: user` host blocks (role collapse) |
| **B7** | Hybrid injects one glass row for media/id only |
| **B11** | Episodic mass / order can outrank thin tip |
| **B5 + B5b** | Moment-end wipe + meal-wire `moment_id` equality (sticky keep dual kill) |

**Primary package:** glass-tail (S1) + path/framing dual-write (S2) + sticky keep B5+B5b (S3).

---

## 3. Goals and non-goals

### 3.1 Goals

1. **Glass-tail band** in the outer meal: recent durable glass user/assistant rows, honest roles, chronological order, restart-safe from `messages.jsonl` (or equivalent).
2. **Path parity:** idle `user_message`, `wait_reply`, wait timeout, interject, moment continue / task_ready, and process restart all produce a **well-formed next hop** under the same continuity invariant.
3. **Sticky directed keep tray:** confirmed pins survive moments and restarts under **token LRU + wall-clock TTL** (hours → ≤1 day), not minute thrash and not moment-end wipe; meal compose reads **instance tray without `moment_id` equality filter**.
4. **Layered recall** so “what do you remember about **THIS**?” can use tip → keep → semantic without inventing from episodic vibe alone.
5. **Host-deterministic** age/size policy for keep; skills may curate but must not be the sole TTL enforcer.

### 3.2 Non-goals

- Dump entire glass history unbounded into every hop.
- Replace ladder / episodic with raw chat.
- Make directed keep long-term memory (no multi-day silent retention).
- Fix SuperGrok pacing, TTS, or sources links in this workstream.
- Full graph-traversal rewrite (future pass may reinforce tray UX only).
- LLM period summaries (#92) — adjacent bulb quality only.
- Soft recall nudge alone as the rockets fix (A5 rejected as primary; ship only after bands).

---

## 4. Continuity invariant (product law)

> After **any** operator or system action (message, wait reply, interject, timeout, restart, continue, task_ready), the **next model call** must see a **well-formed continuity package**: who spoke last, what was asked, what is open, what was deliberately kept, and enough support that “remember / continue” is answerable.

### 4.1 Layered package

| Layer | Job | Cadence | Durability |
|-------|-----|---------|------------|
| **Glass-tail** | What we just said (dialogue tip) | seconds–minutes | Disk glass log |
| **Temporal** | Open-moment atoms / working spine | this moment | Store + promote |
| **Directed keep** | What we **pinned** for this thread of thought | hours, slow decay | Runtime tray + atom ids |
| **Semantic** | Related atoms for *this* seed | per hop | Vectors on disk; seed from tip |
| **Episodic** | Era narrative / summaries | hours–days | Ladder store |
| **In-turn chain** | Tool hops | open moment only | **Not** across stop/restart |
| **Orient / path frame** | Why awake, soft skill bias, goals | per rebuild | Derived |

**Precedence under conflict:** glass-tail + temporal wake truth **outrank** episodic thematic bulk when the tip is a clear user question.

### 4.2 Prince Rupert protection rule

Never allow a full-looking meal (high token use, rich epi) if the **tip is missing** for a social wake. Prefer a smaller meal with an intact tip over a large meal without one.

---

## 5. Part A — Glass-tail band

### 5.1 Definition

**Glass-tail** = last *K* durable glass messages (user + assistant), selected by recency, packed into a labeled meal channel (or fixed band adjacent to temporal), with:

- Original **roles** (`user` / `assistant`).
- Chronological order (oldest → newest within the band, or newest-at-end toward orient — pick one; **newest nearest orient** recommended).
- Token budget: fixed or fraction of residual (illustrative: **5–12%** residual, floor for ≥ last 4–8 turns when available).
- Source of truth: **`data/messages.jsonl`** (or `list_messages`), not RAM-only session.

### 5.2 Placement in outer order

**Confirmed order** (review locked):

```text
system
→ episodic
→ semantic
→ directed_keep
→ glass_tail          # NEW — dialogue tip before spine/orient
→ temporal            # open-moment atoms
→ orient              # why_now, goals, bias
```

Rationale: supports first (background), then **conversation tip**, then open-moment spine, then decision frame.

**Offline recompose note (SA-9b):** temporal already holds weak wake obs; **glass_tail must still exist** for prior assistant roles and true user role. Prefer glass_tail for social `message_id` when deduping against temporal (**OQ6 locked**).

**Hybrid:** remains media/id only. With glass-tail, **skip hybrid** when the same `message_id` is already present on **tail or temporal**. Hybrid must not duplicate the same user row thrice (glass + temporal + hybrid).

### 5.3 Wake / path rules — normative min tip set

| Path | Must in outer | Nice | Orient / wake extras |
|------|---------------|------|----------------------|
| `wait_reply` | Last glass **user answer** + recent **assistant** glass social turns (e.g. prior speak) | Wait prompt if only in `waits.json` / prior tape | why_now should not be the only carrier of user text; dual-write snippet recommended (OQ7) |
| `user_message` | Same must set with triggering user row | — | why_now may stay short; content is in tail |
| Interject | N/A for outer (chain gets text); promote obs still consistent | — | — |
| Wait timeout | Tail of recent social; do not let ancient epi alone define “work” | — | Adjacent #68 sanitation |
| Restart | Rebuild tail from disk before first social hop | — | No empty tip because snapshot was RAM-only |

**Rockets failure** = ignored user question + missing prior assistant glass in outer — **not** missing wait-prompt text alone.

### 5.4 Budget interaction and cut order

Evidence (SA-9b hermetic pressure): tip temporal ~27 tokens vs epi hundreds–thousands. Meal fraction #91 enlarges residual **R** but **does not create a tip** — document explicitly: **larger R ≠ tip.**

- Glass-tail takes from residual **after** system+orient fixed cost, **before** or **with** supports under pressure.
- **Law:** **never cut glass-tail below floor** for social wakes (absolute min turns, e.g. **≥4 messages** or **≥ last 2 full turns**).
- **Cut order under pressure:** semantic → age-soft directed_keep → episodic; **temporal protect tail** unchanged; **never** cut glass-tail below floor for social wakes.
- Keep must not substitute for tip under pressure (age-soft keep before tip floor).
- Glass-tail **must exist even at small budgets** (absolute min turns, not only %).

### 5.5 Acceptance (glass-tail) → named tests

1. Memory-on: after wait → user off-topic question, first hop reasoning/speak addresses the **question**, not only wait ceremony.
2. Restart mid-wait or after wait armed: first social hop still sees last N glass turns from disk.
3. Legacy memory-off path unchanged (sliding glass).
4. Unbounded glass dump does not occur (cap + floor tested).

**Named acceptance / future tests** (implement plan):

| Test name | Intent |
|-----------|--------|
| `test_meal_glass_tail_wait_reply_includes_user_and_prior_assistant` | P2 rockets-class fixture |
| `test_meal_glass_tail_roles_preserved` | True `user` / `assistant` roles in band |
| `test_meal_tip_floor_under_epi_pressure` | Floor holds when epi mass high |
| Offline golden from `evidence/sa9b-e6d460f2/` shape | Recompose carrier regression |

---

## 6. Part B — Sticky directed keep

### 6.1 Today vs target

| | **Today (Phase 2a)** | **Target (this design)** |
|--|----------------------|-------------------------|
| Source | Last graph finish confirm | Merge/reinforce tray from confirms (+ optional explicit pin tools later) |
| Lifetime | Next compose; **clear at moment end** (B5 wipe) | Hours; survive moments + restarts |
| Meal wire | `get_last_confirmed_keep(open_moment_id)` — **equality filter** (B5b) | **Instance tray** — no `snap.moment_id == open_moment_id` requirement for meal path |
| Sliding | Last finish **replaces** | Token **LRU** + merge |
| Age | None | Soft priority ~3h; **hard max ≤24h** (tunable) |
| Storage | Worker RAM thin snapshot | **Persisted instance tray** (`data/runtime/` or memory meta) |

**B5 + B5b dual kill (review confirmed):**

1. **B5** — `TraversalRegistry.on_moment_close` wipes meal-relevant `last_confirmed_keep` (`traverse.py:1106–1117`). Kill switch #1.
2. **B5b** — `get_last_confirmed_keep` returns None when `snap.moment_id` is not in `(None, open_moment_id)`. Kill switch #2: **removing wipe alone is insufficient**; compose must read an **instance tray** not scoped to the open moment id.

### 6.2 Tray model (normative target)

```text
directed_keep_tray (instance-local, persisted)
  entries: [
    {
      atom_id,
      confirmed_at,
      last_reinforced_at,
      source_session_id?,
      source_moment_id?,   # audit only — not a meal compose filter
      note?                # optional short walk blurb fragment
    },
    ...
  ]
  policy:
    max_age_hard: 24h          # host drop, no model vote
    soft_evict_after: 3h       # under pressure, drop these first
    cap_tokens: directed_keep_fraction * residual   # ~8% default, measure
  on_confirm(mode=merge|replace):
    merge or replace ids
    set confirmed_at / last_reinforced_at
    drop age > max_age_hard
    LRU trim until pack ≤ cap_tokens
  on_compose:
    select_directed_keep(tray) → channel [context:directed-keep]
    # MUST NOT require snap.moment_id == open_moment_id
  on_moment_close:
    # DO NOT wipe meal-relevant tray / last_confirmed_keep for meal path
    # optionally still clear glass last_session per KD-A19
  on_restart:
    load tray; apply age drop; pack if any remain
```

### 6.3 Sticky keep implement checklist (B5 + B5b)

1. **Stop** `on_moment_close` wipe of meal-relevant `last_confirmed_keep` (optionally still clear glass `last_session` per KD-A19).
2. **Persist** tray under `data/runtime/` (or memory meta) with `confirmed_at` / `last_reinforced_at`.
3. **Host TTL/LRU:** soft ~3h, hard ≤24h (OQ3 default, provisional pending measurement).
4. **Meal wire (B5b):** `select_directed_keep` / compose reads **instance tray** and must **not** require `snap.moment_id == open_moment_id`. Remove or bypass equality filter in `get_last_confirmed_keep` for meal path (glass session view may keep scope separately).
5. **Merge vs replace** on finish (OQ5 → merge default, provisional).
6. **Inspect:** tray age, token use, entry moment_ids on `/api/memory/context` + graph session.
7. Keep must not substitute for tip under pressure (age-soft keep before tip floor).

### 6.4 Deterministic host vs skill

| Concern | Owner |
|---------|--------|
| Hard TTL, token cap, LRU order | **Host** (testable) |
| Soft “still relevant to tip” | **Defer v1**; optional later host heuristic |
| Re-confirm / drop / “refresh for topic” | **Skill / tools** (curation) |
| Sole enforcer of 3h drop | **Not** skill nudge alone |

Skill may say: prefer re-confirm or drop entries older than soft age when starting a new topic. That is hygiene, not correctness.

### 6.5 Interaction with semantic

- Semantic remains **momentary** (seed from tip/temporal + ANN).
- Keep is **intentional working set** — not a substitute for ANN.
- “What do you remember about X?” order of preference:
  1. Glass-tail / temporal about X  
  2. Directed keep entries about X  
  3. Semantic neighbors from tip seed  
  4. Episodic only as last resort (and label it as era narrative)

### 6.6 Graph traversal (future pass — note only)

- Finish should **merge/reinforce** tray by default; optional replace mode.
- Later: pin-from-semantic, list/drop tray tools, Glass Graph shows tray vs last walk (keep KD-A19 last_session separate from meal-thin tray).
- Not blocking glass-tail ship if tray v1 is “persist last_confirmed + TTL/LRU without full UX.”

### 6.7 Acceptance (directed keep)

1. Confirm keep → end moment → new moment: channel still non-empty (until TTL/LRU).
2. Restart process: tray reloads; expired ids gone; under-cap pack works.
3. Over-cap adds: oldest (or soft-aged) drop first.
4. Hard age: nothing older than max_age_hard appears in meal.
5. Flags off / empty tray: Phase 1/2 budget parity preserved (existing golden tests).
6. Confirm in moment A, compose in moment B (open ≠ confirm): tray still packs (B5b regression).

---

## 7. Path matrix (must pass)

Each row must produce a well-formed next hop under the invariant in §4.

| # | Path | Tip requirement | Keep | Notes |
|---|------|-----------------|------|-------|
| P1 | Idle → `user_message` | Tail ends with user text | Tray as packed | Baseline social |
| P2 | Waiting → glass continue (`wait_reply`) | Tail has prior speak + user answer | Tray | **Rockets class** |
| P3 | Waiting → timeout | Tail recent; no ancient-work default | Tray | Adjacent #68 |
| P4 | `in_moment` → interject | Chain gets text; promote consistent | Tray unchanged | Chain-native |
| P5 | ends_moment + wait → later reply | Same as P2 | Tray | Continuity bridge; B5+B5b |
| P6 | `moment_continue` / `task_ready` | Work path doesn’t erase social tip if social pending | Tray | Policy order |
| P7 | Restart mid-wait | Tail + tray from disk | Tray load | No RAM-only |
| P8 | Restart idle | Tail still present for next social | Tray load | Instance memory |
| P9 | Long tool moment + chain pressure | Outer tip intact; chain may compress | Tray | In-turn ≠ outer |

---

## 8. Soft recall nudge (orient / skill)

Cheap behavior glue for the topic-sized hole (not a substitute for bands):

- Orient or talk/memory skill soft line:  
  *If the user asks what you remember about a topic, use glass-tail and directed_keep first; if thin, use semantic / memory-traverse — do not invent from episodic summaries alone.*

Ship as copy tweak **after bands exist** so we don’t prompt-paper over missing channels. Review **A5 reject as primary** reaffirmed — soft recall is not S1.

---

## 9. Implementation sketch (non-normative PR slices)

Order preferred for risk (S1 before S3; S0 complete via review report):

| Slice | Content | Risk |
|-------|---------|------|
| **S0** | ~~Review report: meal formation + continuation edge matrix~~ → [REPORT.md](../../investigations/meal-continuity-review/REPORT.md) | Process — **done** |
| **S1** | Glass-tail select + pack + budget floor; compose order; dedupe with wake; evaluate tail-only vs tail+orient for rockets | Product tip |
| **S2** | Framing dual-write (**why_now user snippet** recommended) + path parity tests P1–P2–P5–P7 + hybrid dedupe vs tail (B12 / B10) | Correctness |
| **S3** | Persist directed_keep tray; **stop moment-end wipe (B5)**; **meal wire without moment_id filter (B5b)**; TTL+LRU; restart load | Working set |
| **S4** | Confirm merge vs replace; meal channel wire to tray | 2a evolve |
| **S5** | Soft recall nudge; optional tray glass/API inspect | Polish |
| **S6** | Graph UX / reinforce tools (defer if needed) | Later |

Do **not** ship S3 without S1 if dogfood is still failing wait-reply social — tip first.  
Do **not** ship prompt-only soft recall (A5) as S1.

---

## 10. Open questions (evidence + locks)

| ID | Question | Draft default | Review evidence | Status |
|----|----------|---------------|-----------------|--------|
| **OQ1** | Glass-tail % vs absolute min turns? | Floor turns + soft % | No live token calibration | **provisional** |
| **OQ2** | Newest-toward-orient vs oldest-first band? | Newest toward orient | Matches hybrid insert-before-orient pattern | **provisional lock OK** |
| **OQ3** | Hard max keep age 6h vs 24h? | **24h hard**, 3h soft | No dogfood TTL data | **provisional** |
| **OQ4** | Moment-end: clear keep (old) vs keep tray (new)? | **Keep tray** | B5 wipe confirmed `traverse.py:1106–1117`; wipe is kill switch #1 | **Locked: keep tray** |
| **OQ5** | Replace vs merge default on finish? | **Merge** | No multi-confirm dogfood in review | **provisional** |
| **OQ6** | Dedup glass-tail vs temporal wake: which wins? | Glass-tail roles | SA-9b: temporal role collapse; assistant glass only recoverable via true roles | **Locked: glass-tail roles** |
| **OQ7** | Should wait_reply why_now include user snippet? | Optional; tail is source of truth | B12 tape tracks wait ceremony; tail-only may not fully kill framing | **Locked: optional but recommended dual-write for wait_reply** |
| **OQ8** | Semantic seed always from glass-tail last user? | Prefer yes when social | Structural: seed open-moment only today; no live ANN | **Prefer lock after S1** |

---

## 11. Relationship to other work

| Item | Relationship |
|------|----------------|
| **#91 meal budget** | Done — residual size; does not create chat channel; larger R ≠ tip |
| **#92 LLM summaries** | Better bulb; must not outrank tip |
| **#68 wake-02** | Restart sanitation of *work* thread; complementary to tip (B9 adjacency) |
| **Phase 2a design** | Superseded **for keep lifetime** by sticky tray (B5+B5b); walk/session DTO rules mostly stand |
| **Promotion §4** | This doc is the expanded design response to chat-chain gap |
| **meal-continuity-review/** | Fault isolation + DRAFT-EXTENSIONS source for this refinement |
| **lance-debug1 / thin load** | Can starve bulb after restart; do not attribute tip smash solely to thin Lance |

---

## 12. Next process step (explicit)

1. ~~Draft this design~~.  
2. ~~Update GitHub #93 + project board~~.  
3. ~~Review plan (normative method)~~ — [design-meal-formation-continuity-review-plan.md](design-meal-formation-continuity-review-plan.md) → report under `docs/investigations/meal-continuity-review/`.  
4. ~~Refine **this** draft from the report~~ (this revision).  
5. **Implement plan** on a fix branch (S1 → S2 → S3); measure provisional OQs; lock OQ8 after tip exists.

Locked OQs (4/6/7) and confirmed order / cut law / B5+B5b scope are **normative for implement**. Ratios (OQ1), exact TTL (OQ3), and merge default (OQ5) remain measurable defaults.

---

## 13. Document history

| Date | Change |
|------|--------|
| 2026-07-30 | Initial draft from dogfood (wait_reply rockets), interject asymmetry, sticky keep discussion; scope of #93 expanded |
| 2026-07-30 | Refined from [meal-continuity-review/REPORT.md](../../investigations/meal-continuity-review/REPORT.md) DRAFT-EXTENSIONS: confirmed outer order; normative min tip set; cut order + tip floor from SA-9b; named acceptance tests; sticky keep B5 wipe + B5b meal-wire (instance tray without moment_id filter); OQ4/6/7 locked with evidence; S2 framing dual-write explicit; rockets empirical vs normative ranking; status → Ready for implement plan |
