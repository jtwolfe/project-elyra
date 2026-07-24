# Metacognition (MC)

**Status:** Concept documented; no implementation required for Phase 0  
**Branch:** `grok-improvement`  
**Role in plan:** Named functionality unit now; light form only after the Grok path is stable

---

## 1. Intention

Metacognition (MC) is the **light glue** that keeps Project Elyra’s primary materials interacting coherently under continuous problem-solving.

**Primary materials (the real substance):**

- **Goals / tasks** — durable commitments (why / what)
- **Skills / tools** — procedures and callables (how / with what)
- **Memory** (later) — structured residue of experience

**MC is not:**

- A second mind or parallel worker
- A new runtime subsystem
- A free-text planner that bypasses the ledger or stage skills
- An ontology owner
- Something that calls tools itself

**MC is:**

- The path / glue that guides how those materials interact
- Just enough structure so continuous work stays coherent
- Soft shape on the existing CoT, not a constraint layer over it

The do-loop remains the only executor. MC decides and hands off (or, in the lightest form, simply makes the existing Decide path more legible); it does not perform the work.

---

## 2. Geometry

### 2.1 Hierarchy between the two action axes (keep sharp)

```text
goal → task     (why / what we are committed to)
      ↓
skill → tool    (how / with what we act)
```

Commitments sit above actions. Skills do not invent goals. Tools discharge tasks.

### 2.2 MC and Memory as equal peers (not a third hierarchy)

```text
        ┌──────────────────┐          ┌──────────────────┐
        │  Metacognition   │   ≈≈≈    │     Memory       │
        │  (process)       │  peers   │  (substrate)     │
        └────────┬─────────┘          └────────┬─────────┘
                 │                             │
                 │     both modulate           │
                 ▼                             ▼
        ┌───────────────────────────────────────────────┐
        │  goal → task          skill → tool             │
        │  (commitment axis)    (action axis)            │
        └───────────────────────────────────────────────┘
```

| Element | Nature | Contribution to throughput |
|---------|--------|----------------------------|
| **MC** | Process | *When / whether* to commit, revise, hand off, rest; interprets current state against commitments |
| **Memory** | Substrate | *What* prior experience is relevant; supplies structured history both axes can draw on |

Memory without MC is a pile of events with no disciplined use.  
MC without Memory is a pure process that re-interprets every moment from a thin orient slice and never accumulates structured experience.  
They co-evolve; neither sits under the other.

---

## 3. What already exists (codebase reality)

Stretch 1 already supplies most of the outer loop:

| Piece | Present today |
|-------|----------------|
| Commitment axis | Goals/tasks ledger + orient goals slice |
| Action axis | Stage skills (`talk`, `plan-work`, `do-work`, `review-work`, `rest`, `create-tool`, `create-skill`) + tools |
| Soft Decide | `format_skill_bias()` by wake kind |
| Follow-through | Skill-commit HOST after `load_skill` |
| Executor | Single do-loop only |
| Continuous work | Opt-in continue policy |

What is thin is **shape**, not machinery: the Decide step is still largely free CoT plus one soft bias line and orient hygiene text. That is enough for many moments; continuous problem-solving benefits from slightly clearer guidance without turning it into a gate.

---

## 4. Employment plan

### Stage A — Naming (now, including during Phase 0)

- Use **metacognition / MC** in design docs as the name of this functionality unit.
- Optionally, light language in orient or system that marks the Decide role (stage choice, ledger coherence) without adding packages or hard rules.
- **No code change required.** Phase 0 stays focused on provider + usage meter + prompt fitness for Grok.

### Stage B — Shallow shape (after Phase 0 is stable on Grok)

Only after the Grok path and usage meter are verified (and preferably after H-series live dogfood):

1. **Ledger-aware soft bias** — still one line, still not a hard gate. Examples of intent:
   - Ready task → prefer `do-work`
   - Open goal with no ready tasks → prefer `plan-work`
   - Nothing honest remains → prefer `rest`
   - Social wake → prefer `talk` (unchanged)
2. **Short Decide cadence in orient** — a few lines that say: given this orient, pick an appropriate stage skill, then let the do-loop run it; prefer tools over speculation; honest idle is allowed. Do **not** over-specify hop-by-hop behaviour.
3. **Handover gate concepts** — revise Decide language so stage/skill/tool *handoffs* and *completion* are coherent (see §4a). Discussion shape only until Stage B lands; do not invent a new gate subsystem in Phase 0/H polish.
4. **No new runtime object required** at this stage.

This is a shallow refactor / prompt-and-bias shape pass, not a ceremony introduction. Stage B work should **pre-emptively scan** for handover failures of the same family as live dogfood (status speak without answer speak, task_ready after already-done work, promote-green / call-hollow, etc.) before they reappear under continuous Grok work.

### Stage C — Optional form (later, only if needed)

Promote MC to a thin privileged on-disk package **only if**:

- self-modification needs an inspectable, fail-closed-updatable process body, or
- the Memory dual needs a durable peer interface that is awkward to keep purely in orient/bias.

If that happens:

- Package is skill-shaped (loadable, inspectable).
- Job remains Decide / handoff only — no tool calls, no free-text plan that bypasses stage skills.
- Promotion uses the same fail-closed path as tools/skills (draft → regression check on decision routing + social path → explicit promote).
- Bundled version stays read-only / operator-gated until then.

Until Stage C is justified, keep MC conceptual + Stage B shape.

---

## 4a. Handover gate concepts (Stage B discussion)

**Status:** Discussion note for Stage B / extended MC concept revision. **Not** an implementation plan. **Not** a new runtime package.  
**Name:** **handover gate concepts** — the small set of *transition conditions* where work must change hands cleanly (user ↔ person ↔ skill ↔ tool ↔ ledger ↔ glass), or the moment “succeeds” on tape while the operator sees a failure.

MC’s Decide role is already “when / whether to commit, revise, hand off, rest.” Handover gates are that idea made slightly more concrete for review: **what must be true when control or meaning moves across a boundary.** They stay soft (bias / cadence / skill language) unless live evidence forces a thin host check.

### 4a.1 Why this exists (live dogfood seed)

**Case — social calc Q&A (`13be7a22` → `a7e62ecc`, 2026-07-24):**

1. User asked for a numeric answer (tool-using social wake).
2. Early **`speak`**: “Calculating…” (social / talk bias — speak first).
3. **`calculator` ran successfully** (twice); result on the tool beat.
4. Model produced the **full answer in free-text** on a no-tools hop.
5. Moment stopped with `spoke=true`, `tools_ran=true`, `stop_reason=no_tools`.
6. Glass never showed the number — only the status line. User had to re-ask; a later moment **`speak`**’d the answer.

**Root family (not “broken calculator”):**

| Layer | Fact |
|-------|------|
| Glass contract | Only successful **`speak`** reaches glass; free-text is tape/orphan content by design |
| Early speak | Status / ack speak satisfies `spoke` / no-speak policy |
| Completion | Nothing required a **final answer speak** after non-speak tools on a user question |
| Continuous | OFF — no moment-continue to force a second pass |

Same *shape* as other thrash: **inner ceremony green, outer handoff incomplete.** Compare: promote `callable:true` then `module_not_found`; task_ready after task already done; tools catalog ghost after host `rm`. MC Stage B should look for these **before** continuous Grok work multiplies them.

### 4a.2 What a “handover gate” is (concept only)

A **handover gate** is a *named transition*, not a new executor:

```text
from channel / artifact A  →  to channel / artifact B
with obligation: what must be true so the operator (or next stage) is not misled
```

Examples of *channels*: glass (`speak`), moment tape, ledger (goals/tasks), tool result map, registry catalog, draft/local package, wait bar, continuous continue wake.

MC does **not** call tools or speak. Decide / skill bias / orient cadence may **remind** which handoff is still open. Host policies already implement some hard walls (`counts_as_speak`, skill-commit HOST, thrash HOST); handover gates name the *semantic* gaps between those walls.

### 4a.3 Candidate gates for pre-emptive Stage B review

Use this list in an **extended MC concept pass** — find analogous holes, not implement all as code.

| Gate (working name) | Boundary | Obligation (intent) | Dogfood / known relative |
|---------------------|----------|---------------------|---------------------------|
| **Answer speak** | tools → glass | If the user asked a question and tools produced a user-visible result, **final `speak` carries the answer**; early “working…” does not discharge that | Calc: status speak only |
| **Status vs result** | social ack → completion | Distinguish *progress speak* from *result speak*; `spoke=true` alone is not “user got the answer” | Same moment |
| **Skill load → first tool** | Decide → stage skill | After `load_skill`, first real step is the skill’s mandatory tool path (already partly skill-commit) | Skill thrash history |
| **Draft → verify → promote → call** | growth path | Green verify/promote implies module resolves and a safe smoke path exists; name ≠ reserved/collision | Nested `impl.*` hollow promote |
| **Catalog ↔ disk** | registry ↔ `tools/local` | Glass/tool list and execute path match host tree after promote/delete | Ghost `ddg_search` |
| **Task ready ↔ truth** | ledger → wake | `task_ready` only when work remains; done/closed should not re-wake busywork | Stale ready after promote |
| **Blocked ↔ operator** | do-work → wait/user | When host-only action is required, clear `wait_user` / block notes; don’t thrash guest `rm` | Dedupe host cleanup |
| **Review → close** | evidence → goal closed | Close only after acceptance evidence (review-work); don’t close a twin empty goal | Dual web_search goals |
| **Continuous handoff** | moment end → continue | If continuous ON, continue only when progress + honest next step; if OFF, stop without orphan free-text as glass | Continuous policy (existing) |
| **Wait arm** | ask → glass choices | Wait must be armed and visible; free-text stop is not a substitute for an open question | Wait bar UX |

**Pattern to hunt in revision sessions:**  
“What did the **tape / ledger / registry** believe was done that the **operator channel** (glass, wait, task list) never received?”

### 4a.4 Standing constraints (do not invent ceremony)

When Stage B discusses handover gates:

1. **Prefer soft Decide / skill language** over new hard HOST gates — unless dogfood proves soft text fails repeatedly (calc-style re-ask rate).
2. **Do not auto-promote free-text to glass** — that breaks the speak contract and identity wall.
3. **Do not make MC speak or run tools** — Decide hands off to `talk` / `do-work` / `review-work` / `rest`.
4. **Do not over-specify hop-by-hop** — one clear completion bias (“answer speak after tools on Q&A”) beats a checklist of twenty gates in orient.
5. **Reuse existing machinery** where it already is a gate: `counts_as_speak`, no-speak nudge, skill-commit, thrash HOST, review-work, continuous progress gates, promote/verify fail-closed.
6. **Quantize vocabulary** for gates (status speak / answer speak / blocked host / catalog sync) so Stage B bias lines and later Memory events share names.

### 4a.5 Suggested Stage B discussion agenda (lightweight)

For the **extended MC concept revision** (still docs-first):

1. Accept or rename the **handover gate** vocabulary.
2. Walk §4a.3 and mark each: *soft bias only* / *skill playbook line* / *already host-enforced* / *needs dogfood* / *out of scope*.
3. Draft 2–4 **Decide / talk / do-work** sentences max that cover the highest-frequency holes (start with **answer speak** + **status vs result**).
4. List which gates need Memory hooks later (e.g. “last result not spoken”) vs pure process.
5. Explicit non-goals: full workflow engine, automatic glass injection, MC as reviewer of every hop.

### 4a.6 Relation to other docs

| Doc | Link |
|-----|------|
| This file §4 Stage B | Handover gates are part of shallow shape, not Stage C package |
| [harness-sandbox-fitness.md](harness-sandbox-fitness.md) | Growth path fail-closed, thrash HOST, create-tool honesty — action-axis gates |
| Stretch 1 speak / no-speak / skill-commit | Existing process walls; handover gates name *semantic* completion across them |
| Phase 0 / H live dogfood | Source of seed cases; keep adding one-line rows to §4a.3 when new thrash families appear |

---

## 5. Peer hooks for Memory (leave open)

Even while Memory is Phase 3, design language and any Stage B changes should leave three clean hooks:

1. **State input** — the same slot that feeds orient / Decide (ledger + why-now today) later also receives “relevant memories for this goal/task.”
2. **Event write** — beats and ledger changes already happen; Memory will index those events. MC does not own the write path; it participates in producing the events.
3. **No hard-coded ontology** — do not bake concept structures into MC (or into orient Decide text). Memory owns ontology when it arrives.

---

## 6. Design rules (standing)

1. Keep the two hierarchies sharp (goal/task above skill/tool).
2. Treat Memory and MC as dual from the start of the design conversation.
3. Let the do-loop remain the only executor.
4. Anchor interpretation in the ledger (+ later memory), not in free-form reflection.
5. Prefer “privileged process language / soft shape” over “new subsystem.”
6. Quantize Decide just enough (stage vocabulary, soft bias) without freezing creativity.
7. Design for eventual transparency carefully; do not require it in the first instantiation.
8. **Do not over-constrain the CoT.** MC facilitates continuous problem-solving; it does not prescribe every hop.

---

## 7. Fit in the Grok Improvement Plan

| Phase | MC-related work |
|-------|------------------|
| **Phase 0** | Name the unit in docs. No MC implementation. Optional light wording only if it helps Grok prompt fitness. |
| **After Phase 0 stable** (+ H live dogfood preferred) | Stage B shallow shape (ledger-aware bias, short Decide cadence, **handover gate concepts** §4a). |
| **Phase 1** | May open with Stage B if not already done; then `grok_build` tool + self-improvement scaffolding. MC package is **not** a Phase 1 requirement. |
| **Phase 2** | If a package form exists, it becomes eligible for the same self-mod continuity path as skills/tools. |
| **Phase 3** | Memory arrives as equal peer; the three hooks light up. |

Phase 0 success criteria are unchanged. Do not block the Grok path on MC form.

---

## 8. Non-goals for MC work

- New parallel worker or second do-loop
- MC calling tools or speaking directly
- Hard gates that replace free CoT stage choice
- Ontology or concept graphs inside MC
- Requiring a package before Memory exists
- Expanding continuous-work policy under the banner of MC

---

**Bottom line:**  
Start using **metacognition / MC** as the name of the glue that keeps goals, tasks, skills, tools (and later memory) coherent under continuous work. After Elyra is stable on Grok, add only a little more form via soft bias, orient shape, and **handover gate concepts** (when control or meaning crosses a boundary — especially glass). Promote to a real package only if transparency or the Memory dual truly needs it.

**Stage B discussion seed:** early “Calculating…” `speak` + successful tools + free-text answer never on glass is a **status vs answer speak** handover failure; use §4a to pre-empt similar incomplete handoffs before continuous Grok work multiplies them.
