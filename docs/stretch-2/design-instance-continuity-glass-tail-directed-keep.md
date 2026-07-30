# Design draft: Instance continuity — glass-tail + sticky directed keep

| Field | Value |
|-------|--------|
| **Document** | Draft product / implementation design (not yet execute-plan ready) |
| **Product** | project-elyra |
| **Date** | 2026-07-30 |
| **Status** | **Draft** — to be refined after a meal-formation / continuation review report |
| **Issue** | [#93](https://github.com/jtwolfe/project-elyra/issues/93) — `BUG-meal-03` (scope expanded) |
| **Bug id** | `BUG-meal-03` |
| **Branch** | `design/BUG-meal-03-93-instance-continuity` |
| **Depends on** | Memory meal active; meal budget fraction shipped (#91); Phase 2a directed_keep channel exists |
| **Related design** | [design-context-meal-composition.md](design-context-meal-composition.md), [design-phase-2a-implementation.md](design-phase-2a-implementation.md), [promotion-discussion/README.md](../promotion-discussion/README.md) §4–5 |
| **Adjacent issues** | [#68](https://github.com/jtwolfe/project-elyra/issues/68) wake-02 (post-restart wrong work thread); [#92](https://github.com/jtwolfe/project-elyra/issues/92) LLM summaries (episodic quality, not tip); provider timeout board draft |

---

## 1. Purpose

Define how Project Elyra keeps a **well-formed “memory of instance”** for every next model call so that:

- **Immediate chat** survives moment boundaries and process restarts (**glass-tail**).
- **Intentionally pinned** material survives hours with slow decay (**sticky directed keep**).
- **Path variants** (wait reply, interject, timeout, restart, continue) cannot shatter the tip of continuity while episodic bulk still looks healthy.

This document is the **draft design home for #93** after dogfood expanded the issue from “add a chat band” to **instance continuity** (tip + intentional working set). A follow-on **meal formation / continuation review report** will refine this draft before a PR plan / execute-plan.

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

### 2.2 Prince Rupert’s drop

- **Bulb** = episodic (+ semantic when seeded) + ledger. Looks rich in Memory UI.
- **Tip** = last glass turns + wake truth + wait setup, with **true roles and order**.
- **Smash the tip** → confident wrong speak even when the bulb is full.

Raising meal budget (#91) thickened the bulb. It did not harden the tip.

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
- **Semantic** is momentary (seed + ANN); encode lag can leave new observations unindexed for minutes after speak.
- `why_now` for wait_reply is often `wait reply (wait_id=…)` **without** user text.

Legacy path (memory off) still centers sliding glass — do not regress that path.

---

## 3. Goals and non-goals

### 3.1 Goals

1. **Glass-tail band** in the outer meal: recent durable glass user/assistant rows, honest roles, chronological order, restart-safe from `messages.jsonl` (or equivalent).
2. **Path parity:** idle `user_message`, `wait_reply`, wait timeout, interject, moment continue / task_ready, and process restart all produce a **well-formed next hop** under the same continuity invariant.
3. **Sticky directed keep tray:** confirmed pins survive moments and restarts under **token LRU + wall-clock TTL** (hours → ≤1 day), not minute thrash and not moment-end wipe.
4. **Layered recall** so “what do you remember about **THIS**?” can use tip → keep → semantic without inventing from episodic vibe alone.
5. **Host-deterministic** age/size policy for keep; skills may curate but must not be the sole TTL enforcer.

### 3.2 Non-goals

- Dump entire glass history unbounded into every hop.
- Replace ladder / episodic with raw chat.
- Make directed keep long-term memory (no multi-day silent retention).
- Fix SuperGrok pacing, TTS, or sources links in this workstream.
- Full graph-traversal rewrite (future pass may reinforce tray UX only).
- LLM period summaries (#92) — adjacent bulb quality only.

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

**Proposed order** (draft — review may adjust):

```text
system
→ episodic
→ semantic
→ directed_keep
→ glass_tail          # NEW — dialogue tip before spine/orient
→ temporal            # open moment atoms
→ orient              # why_now, goals, bias
```

Rationale: supports first (background), then **conversation tip**, then open-moment spine, then decision frame. Dedup: if a glass row is already represented as open-moment wake obs with same `message_id`, prefer one copy (keep glass role shape or temporal label — pick in implement; default **prefer glass_tail role fidelity** for social rows).

### 5.3 Wake / path rules

| Path | Glass-tail must include | Orient / wake extras |
|------|-------------------------|----------------------|
| `user_message` | Tail ending with that user row | why_now may stay short; content is in tail |
| `wait_reply` | Prior assistant wait setup **and** user answer (at least last speak + wait prompt context if present on glass) + reply | why_now should not be the only carrier of user text |
| Interject | N/A for outer (chain gets text); promote obs still consistent | — |
| Wait timeout | Tail of recent social; do not let ancient epi alone define “work” | Adjacent #68 sanitation |
| Restart | Rebuild tail from disk before first social hop | No empty tip because snapshot was RAM-only |

**Hybrid wake inject** remains for media / missing id; with glass-tail it must not **duplicate** the same user row thrice (glass + temporal + hybrid).

### 5.4 Budget interaction

- Glass-tail takes from residual **after** system+orient fixed cost, **before** or **with** supports under pressure.
- **Cut order under pressure (draft):** semantic → (optional soft) directed_keep age-soft → episodic → **never cut glass-tail below floor** for social wakes; never cut temporal protect tail below existing floor.
- Meal fraction (#91) enlarges residual; glass-tail **must exist even at small budgets** (absolute min turns, not only %).

### 5.5 Acceptance (glass-tail)

1. Memory-on: after wait → user off-topic question, first hop reasoning/speak addresses the **question**, not only wait ceremony.
2. Restart mid-wait or after wait armed: first social hop still sees last N glass turns from disk.
3. Legacy memory-off path unchanged (sliding glass).
4. Unbounded glass dump does not occur (cap + floor tested).

---

## 6. Part B — Sticky directed keep

### 6.1 Today vs target

| | **Today (Phase 2a)** | **Target (this draft)** |
|--|----------------------|-------------------------|
| Source | Last graph finish confirm | Merge/reinforce tray from confirms (+ optional explicit pin tools later) |
| Lifetime | Next compose; **clear at moment end** | Hours; survive moments + restarts |
| Sliding | Last finish **replaces** | Token **LRU** + merge |
| Age | None | Soft priority ~3h; **hard max ≤24h** (tunable) |
| Storage | Worker RAM thin snapshot | **Persisted instance tray** (`data/runtime/` or memory meta) |

### 6.2 Tray model

```text
directed_keep_tray (instance-local, persisted)
  entries: [
    {
      atom_id,
      confirmed_at,
      last_reinforced_at,
      source_session_id?,
      note?            # optional short walk blurb fragment
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
  on_restart:
    load tray; apply age drop; pack if any remain
```

### 6.3 Deterministic host vs skill

| Concern | Owner |
|---------|--------|
| Hard TTL, token cap, LRU order | **Host** (testable) |
| Soft “still relevant to tip” | **Defer v1**; optional later host heuristic |
| Re-confirm / drop / “refresh for topic” | **Skill / tools** (curation) |
| Sole enforcer of 3h drop | **Not** skill nudge alone |

Skill may say: prefer re-confirm or drop entries older than soft age when starting a new topic. That is hygiene, not correctness.

### 6.4 Interaction with semantic

- Semantic remains **momentary** (seed from tip/temporal + ANN).
- Keep is **intentional working set** — not a substitute for ANN.
- “What do you remember about X?” order of preference:
  1. Glass-tail / temporal about X  
  2. Directed keep entries about X  
  3. Semantic neighbors from tip seed  
  4. Episodic only as last resort (and label it as era narrative)

### 6.5 Graph traversal (future pass — note only)

- Finish should **merge/reinforce** tray by default; optional replace mode.
- Later: pin-from-semantic, list/drop tray tools, Glass Graph shows tray vs last walk (keep KD-A19 last_session separate from meal-thin tray).
- Not blocking glass-tail ship if tray v1 is “persist last_confirmed + TTL/LRU without full UX.”

### 6.6 Acceptance (directed keep)

1. Confirm keep → end moment → new moment: channel still non-empty (until TTL/LRU).
2. Restart process: tray reloads; expired ids gone; under-cap pack works.
3. Over-cap adds: oldest (or soft-aged) drop first.
4. Hard age: nothing older than max_age_hard appears in meal.
5. Flags off / empty tray: Phase 1/2 budget parity preserved (existing golden tests).

---

## 7. Path matrix (must pass)

Each row must produce a well-formed next hop under the invariant in §4.

| # | Path | Tip requirement | Keep | Notes |
|---|------|-----------------|------|-------|
| P1 | Idle → `user_message` | Tail ends with user text | Tray as packed | Baseline social |
| P2 | Waiting → glass continue (`wait_reply`) | Tail has prior speak + user answer | Tray | **Rockets class** |
| P3 | Waiting → timeout | Tail recent; no ancient-work default | Tray | Adjacent #68 |
| P4 | `in_moment` → interject | Chain gets text; promote consistent | Tray unchanged | Chain-native |
| P5 | ends_moment + wait → later reply | Same as P2 | Tray | Continuity bridge |
| P6 | `moment_continue` / `task_ready` | Work path doesn’t erase social tip if social pending | Tray | Policy order |
| P7 | Restart mid-wait | Tail + tray from disk | Tray load | No RAM-only |
| P8 | Restart idle | Tail still present for next social | Tray load | Instance memory |
| P9 | Long tool moment + chain pressure | Outer tip intact; chain may compress | Tray | In-turn ≠ outer |

---

## 8. Soft recall nudge (orient / skill)

Cheap behavior glue for the topic-sized hole (not a substitute for bands):

- Orient or talk/memory skill soft line:  
  *If the user asks what you remember about a topic, use glass-tail and directed_keep first; if thin, use semantic / memory-traverse — do not invent from episodic summaries alone.*

Ship as copy tweak after bands exist so we don’t prompt-paper over missing channels.

---

## 9. Implementation sketch (non-normative PR slices)

Order preferred for risk:

| Slice | Content | Risk |
|-------|---------|------|
| **S0** | Review report: meal formation + continuation edge matrix (feeds refine of this draft) | Process |
| **S1** | Glass-tail select + pack + budget floor; compose order; dedupe with wake | Product tip |
| **S2** | Path parity tests P1–P2–P5–P7; why_now / dual-copy cleanup | Correctness |
| **S3** | Persist directed_keep tray; stop moment-end wipe; TTL+LRU; restart load | Working set |
| **S4** | Confirm merge vs replace; meal channel wire to tray | 2a evolve |
| **S5** | Soft recall nudge; optional tray glass/API inspect | Polish |
| **S6** | Graph UX / reinforce tools (defer if needed) | Later |

Do **not** ship S3 without S1 if dogfood is still failing wait-reply social — tip first.

---

## 10. Open questions (for review report + operator lock)

| ID | Question | Default if unset |
|----|----------|------------------|
| OQ1 | Glass-tail % vs absolute min turns? | Floor turns + soft % |
| OQ2 | Newest-toward-orient vs oldest-first band? | Newest toward orient |
| OQ3 | Hard max keep age 6h vs 24h? | **24h hard**, 3h soft |
| OQ4 | Moment-end: clear keep (old) vs keep tray (new)? | **Keep tray** |
| OQ5 | Replace vs merge default on finish? | **Merge** |
| OQ6 | Dedup glass-tail vs temporal wake: which wins? | Glass-tail roles |
| OQ7 | Should wait_reply why_now include user snippet? | Optional; tail is source of truth |
| OQ8 | Semantic seed always from glass-tail last user? | Prefer yes when social |

---

## 11. Relationship to other work

| Item | Relationship |
|------|----------------|
| **#91 meal budget** | Done — residual size; does not create chat channel |
| **#92 LLM summaries** | Better bulb; must not outrank tip |
| **#68 wake-02** | Restart sanitation of *work* thread; complementary to tip |
| **Phase 2a design** | Superseded **for keep lifetime** by sticky tray; walk/session DTO rules mostly stand |
| **Promotion §4** | This doc is the expanded design response to chat-chain gap |

---

## 12. Next process step (explicit)

1. ~~Draft this design~~ (this document).  
2. Update GitHub #93 + project board to point here and expand scope.  
3. **Plan and run a meal-formation / continuation review** → written **report** isolating edge conditions.  
4. Refine this draft from the report → PR plan → implement on a fix branch.

Until step 3–4 complete, treat ratios, cut order, and OQ defaults as **provisional**.

---

## 13. Document history

| Date | Change |
|------|--------|
| 2026-07-30 | Initial draft from dogfood (wait_reply rockets), interject asymmetry, sticky keep discussion; scope of #93 expanded |
