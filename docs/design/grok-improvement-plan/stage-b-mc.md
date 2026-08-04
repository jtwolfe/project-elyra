# Stage B Metacognition (MC-beta) — Implementation Plan

| Field | Value |
|-------|--------|
| **Class** | DESIGN |
| **Status** | Shipped (implementation plan; soft Decide bias/orient landed in code after this plan — prefer `working`) |
| **Audience** | Implementers |
| **Normative?** | No — prefer code on `working` when conflict |
| **Durable path** | `docs/design/grok-improvement-plan/stage-b-mc.md` |
| **Branch (historical)** | `grok-improvement` (tip now **`working`**) |
| **Workstream nickname** | **MC-beta** = this Stage B shallow-shape work only |
| **Canonical concept** | [metacognition.md](metacognition.md) |
| **Engineering rules** | [engineering-principles.md](../../dev/engineering-principles.md) |

This document is the **handoff plan** for implementing Stage B. It is written so Grok Build (or any implementer) can execute **without** prior chat context. Read [metacognition.md](metacognition.md) first for geometry and hybrid ontology; this file is the ordered, file-level plan.

---

## 1. Goal and success criteria

### Goal

Make Elyra’s **Decide** path legible and **ledger-aware**, and close the highest-frequency **glass handoff** failure (status speak without answer speak) — using **soft** bias, orient cadence, and skill language only. Do **not** introduce an MC runtime package, second executor, or consolidation of hard host policies under MC.

### Success criteria (all required)

1. **Ledger-aware soft bias** — `format_skill_bias` (or a pure helper it calls) prefers stage skills from ledger shape when stronger than pure wake-kind (see §5). Still **one line**, still **not a hard gate**. Unit tests cover the preference table.
2. **Short Decide cadence** in `prompts/orient.md` (≤4 lines of guidance): pick one stage skill → `load_skill` → first tool step; tools over speculation; **answer speak** after tools on user questions; honest idle OK.
3. **Answer-speak vocabulary** in orient + `skills/bundled/talk/SKILL.md` (and a light line in do-work if present): early status/ack `speak` does **not** complete a user question when tools produced a user-visible result; a **final `speak` must carry the answer**.
4. **No regression** of hard policies: skill-commit, no-speak, thrash, continuous gates, usage hard-stop, SpeakTransport (free-text never reaches glass).
5. **Continuous remains default OFF**; no continuous policy formula changes under the MC banner.
6. **Live dogfood (operator, continuous OFF):**
   - Pure hello → one successful `speak` on glass.
   - Factual/numeric user question + tool (e.g. calculator) → **answer visible in glass messages**, not only in tape/free-text.
   - `task_ready` wake with a ready task → bias leans `do-work`.
   - Empty honest work + social → talk/stop; no busywork.
   - create-tool path still honest (H6 regression).

### Non-goals / out of scope (Stage B)

| Non-goal | Why |
|----------|-----|
| MC Stage C package | Optional later; not “first real MC” |
| MC calling tools or speaking | MC is Decide/handoff only |
| Moving skill-commit / thrash / continuous / usage into an MC module | Hard policies stay host law |
| Auto-promoting free-text to glass | Breaks speak contract and identity wall |
| Expanding continuous-work policy or default ON | Separate product decision |
| Phase 1 `grok_build` tool | After Stage B is calm |
| Memory substrate / atom graph implementation | Phase 3 |
| New workflow engine or hop-by-hop HOST checklist | Over-constrains CoT |
| Hard “must answer-speak” HOST | Soft path first; evidence-gated follow-up only |

---

## 2. Context Grok Build must internalize

### 2.1 Hybrid ontology (do not collapse)

Two different kinds of structure already exist in the codebase:

| Kind | Role | Examples (today) |
|------|------|------------------|
| **Soft Decide** (MC Stage B surface) | Interpretive lean in CoT: which stage skill, whether handoff complete | `format_skill_bias`, orient “How to use”, talk playbook |
| **Hard policies** (host law) | Integrity / channel / recovery even when the model is confused | SpeakTransport + `counts_as_speak`, `NO_SPEAK_NUDGE`, skill-commit HOST, thrash HOST, continuous progress gates, usage hard-stop |

**Hybrid decision (locked):** consolidate only the **soft Decide story** under MC naming. **Leave hard policies where they are.** Do not vacuum enforcement into an MC hub. Tolerating overlap (“should we still work?” hinted in bias and gated in continuous) is intentional: soft path for the model, hard path for the host.

### 2.2 Why answer-speak matters (ops + glass)

Live dogfood pattern (social tool Q&A):

1. Early `speak` (“Calculating…”) → `spoke=true`.
2. Non-speak tools succeed → `tools_ran=true`.
3. Answer appears in **free-text** → tape only; **not glass**.
4. No-speak nudge does not fire (`spoke` already true).
5. Moment ends green on tape; **operator never saw the answer**.

Stage B priority handover vocabulary:

- **status speak** — progress/ack on glass  
- **answer speak** — final user-visible result on glass  

`spoke=true` alone is not “user got the answer.”

### 2.3 MC-beta nickname

**MC-beta** = informal name for **Stage B shallow shape** only.  
It does **not** mean “prep until Stage C is the real MC.” Stage B **is** the first behavioral MC implementation. Stage C remains **optional package form** (see metacognition.md § Stage C).

### 2.4 Super-future arc (do not implement now)

Long-term direction (Phase 3+ design gravity, not Stage B scope):

- Skills, tools, goals/tasks, and eventually MC **process language** live as structure in an **atomized memory** substrate (instances + weighted typed edges; knowledge as patterns — see §2.5).
- CoT becomes **ephemeral activation** over that graph for one moment.
- A **thin outer interpreter** runs wake → moment → channel enforcement → persist atoms.

**Even in that future**, channel laws (speak→glass, fail-closed promote, usage ceilings) stay **outer invariants**, not ordinary soft patterns the graph can edit away. Stage B only lays soft vocabulary and bias patterns Memory can later absorb.

### 2.5 Memory thesis (reference; optional in-repo essay)

Operator design essay *What is wrong with my memory?* (Developer, 2026-04-28) argues: memory is not a warehouse of facts; it is **organized experience** — **atoms** (instances with content, context, felt signal, connections), **hypergraph** edges with strength, knowledge as **shadows/patterns** across atoms. Forgetting and consolidation keep the graph usable.

**Implication for Stage B:** soft Decide text is pattern-shaped and should stay editable; hard policies are closer to substrate rules that keep handoffs honest. Essay in-repo: [`docs/memory-atoms.pdf`](../../memory-atoms.pdf) — Phase 3 reference, not Stage B dependency.

### 2.6 Engineering principles to respect

From [engineering-principles.md](../../dev/engineering-principles.md):

- One job per module; **no god module** named MC that owns speak + continuous + thrash + bias.
- Small units; pure bias helper testable without presence I/O.
- Tests are part of the feature.
- Skills/prompts on disk; load, don’t embed multi-page strings in Python.
- Prefer soft shape over new forever-on ceremony.
- Stretch discipline: **no Phase 3 hypergraph smuggled** into Stage B.

---

## 3. Codebase map (current — implementer starting point)

| Concern | Primary files |
|---------|----------------|
| Soft bias | `elyra/loop/orient_slice.py` — `format_skill_bias`, `format_goals_slice`, `format_skill_catalog` |
| Orient prompt | `prompts/orient.md` |
| Outer meal assembly | `elyra/loop/context.py` — `assemble_outer_meal` / `fill_orient` |
| Presence wiring | `elyra/presence/worker.py` — `rebuild_outer` passes goals, catalog, `format_skill_bias(wake.kind, payload)` |
| Talk playbook | `skills/bundled/talk/SKILL.md` |
| Do-work playbook | `skills/bundled/do-work/SKILL.md` (if present; light answer-speak line only) |
| Skill-commit (leave alone) | `elyra/loop/skill_commit_policy.py` |
| Continuous (leave alone) | `elyra/loop/continuous_policy.py` |
| Do-loop inject order (leave alone) | `elyra/loop/doloop.py` — skill_commit → no_speak → work_continue → stop |
| Speak contract (leave alone) | Speak tool + `counts_as_speak`; free-text never glass |
| Bias tests | `tests/test_orient_slice.py` |

**Do not** refactor do-loop free-text order or continuous formulas for Stage B.

---

## 4. Ordered implementation steps

Prefer **docs-touching commits first**, then thin code, then skill text. Small PRs against `grok-improvement`.

### Step 0 — Plan docs (this pass)

Already the concept + this plan. No code.

### Step 1 — Ledger-aware soft bias

**Files:** `elyra/loop/orient_slice.py`, `tests/test_orient_slice.py`, optionally `elyra/presence/worker.py` if signature needs goals list.

**Intent (preference order; still one returned string):**

| Condition | Prefer skill bias toward |
|-----------|---------------------------|
| Social wake (`user_message`, `wait_reply`) | `talk` (unchanged; social wins) |
| Else any task `ready` or `in_progress` | `do-work` |
| Else open/review goal with no ready/in_progress tasks | `plan-work` |
| Else nothing honest open | `rest` |
| Else | existing wake-kind table (`task_ready`, `timer`, `moment_continue`, `background`, …) |

**Design constraints:**

- Pure function; no I/O.
- Accept optional goals sequence (same shape as `format_goals_slice` input) or a tiny summary struct — avoid forcing presence to format strings twice.
- Social wake-kind **must not** be overridden by ledger (glass presence first).
- Keep existing `BIAS_*` constants where possible; add new constants only if needed.
- YAGNI: no multi-line bias essays.

**Tests:** table-driven cases for social override, ready task, open goal no tasks, empty ledger, unknown wake kind.

### Step 2 — Decide cadence + answer-speak in orient

**File:** `prompts/orient.md`

Under “How to use this frame”, add a short **Decide** block (2–4 lines), including:

- Given why-now + goals + soft skill bias: pick **one** stage skill, `load_skill` exact name, then follow **First tool call / First action**.
- Prefer tools over speculation; do not free-text the work of a loaded skill.
- On user questions: after tools produce a user-visible result, **answer speak** must carry the result — early status speak is not enough.
- Honest idle → free-text stop or `rest`; no busywork.

Do not name “Stage C” or “MC package.” Light use of “Decide” is fine.

### Step 3 — Talk (and light do-work) skill completion rules

**Files:** `skills/bundled/talk/SKILL.md`; optionally `skills/bundled/do-work/SKILL.md`

Talk — Hard rules / Quality / completion:

- Distinguish **status speak** (ack/progress) vs **answer speak** (final result for the user).
- If the user asked a question and tools returned a user-visible result, a **final `speak` must include the answer** before the moment ends.
- Early “working…” / “calculating…” does not discharge that obligation.

Do-work — one short line only: results the user asked for must be spoken on glass, not left only in tool JSON or free-text.

### Step 4 — Wire goals into bias if needed

If Step 1 needs goals at bias call site: update `PresenceWorker._run_moment` / `rebuild_outer` to pass `list_goals()` (or a minimal summary) into `format_skill_bias`. Re-read goals on rebuild (already true for goals slice).

### Step 5 — Verification

1. Unit tests green (`test_orient_slice` + any worker orient tests).
2. Full existing loop/policy tests still green (no policy moves).
3. Operator live checklist (§1 success criteria item 6) with continuous **OFF**.
4. Confirm glass **messages** list (not only activity pill) for answer-speak cases.

### Step 6 — Soft answer-speak + narrow post-tool reminder (landed, then softened)

Soft Decide (orient / talk) owns status vs answer judgment and monologue cases. A thin HOST may fire **only** for a post-tool glass gap:

- Pure predicate ``should_answer_speak_nudge`` in `skill_commit_policy.py`; inject once in free-text order: skill_commit → no_speak → **answer_speak** → work_continue → stop.
- Fires when social + already spoke + tools ran + no speak *since* those tools + non-empty free-text.
- HOST copy is choice-preserving (if user still needs a tool result on glass, speak; otherwise stop) — not “must answer-speak.”
- **Does not** fire on free-text length after a complete social speak (false-positive dogfood: second meta speak).
- Never auto-inject free-text onto glass.

---

## 5. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Bias fights social presence | Social wake-kind always wins over ledger |
| Over-long orient / bias text | Cap Decide cadence; one-line bias |
| Implementer consolidates policies under MC | Explicit non-goals; code review against §2.1 |
| Hard answer-speak HOST too early | Soft path mandatory; HOST only after dogfood evidence |
| Continuous creep | Default OFF; no formula changes in Stage B PRs |
| Phase 3 memory work sneaks in | Stretch discipline; atoms essay is reference only |

---

## 6. What to leave completely alone

- `elyra/loop/skill_commit_policy.py` logic and HOST strings (unless a typo fix)  
- `elyra/loop/continuous_policy.py` gate formulas and defaults  
- `elyra/loop/tool_thrash_policy.py`  
- SpeakTransport / `counts_as_speak` contract  
- Free-text inject **order** in do-loop  
- Usage meter and pre-claim `model_available`  
- Provider / Phase 0 defaults  
- Creating `elyra/loop/metacognition.py` or an MC package directory  

---

## 7. UX and glass notes for implementers

- **Glass truth** is the messages list after successful `speak`, not free-text model content and not the activity pill alone.
- Activity trail may still show “speaking…” then “ran calculator” then “thinking…” — that is OK if a later `speak` carries the answer.
- Status API continuous block: no Stage B requirement to change fields.
- Optional later (out of scope): debug fields for “spoke” vs incomplete handoff — only if operator needs them after soft path.

---

## 8. Ready for Grok Build — next actions

1. Open a work branch on top of `grok-improvement` (e.g. `stage-b-mc-bias`).  
2. Implement **Step 1** (ledger-aware bias + unit tests).  
3. Implement **Steps 2–3** (orient + talk/do-work text).  
4. Wire presence if signature requires (**Step 4**).  
5. Run unit suite; operator runs live glass checklist (**Step 5**).  
6. Merge down to `grok-improvement`. Do **not** open Stage C or Phase 1 from this workstream.

**Definition of done:** §1 success criteria 1–6; non-goals respected; engineering principles checklist (tests, disk prompts/skills, no god module).
