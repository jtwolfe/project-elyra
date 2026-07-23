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

Only after the Grok path and usage meter are verified:

1. **Ledger-aware soft bias** — still one line, still not a hard gate. Examples of intent:
   - Ready task → prefer `do-work`
   - Open goal with no ready tasks → prefer `plan-work`
   - Nothing honest remains → prefer `rest`
   - Social wake → prefer `talk` (unchanged)
2. **Short Decide cadence in orient** — a few lines that say: given this orient, pick an appropriate stage skill, then let the do-loop run it; prefer tools over speculation; honest idle is allowed. Do **not** over-specify hop-by-hop behaviour.
3. **No new runtime object required** at this stage.

This is a shallow refactor / prompt-and-bias shape pass, not a ceremony introduction.

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
| **After Phase 0 stable** | Stage B shallow shape (ledger-aware bias, short Decide cadence). |
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
Start using **metacognition / MC** as the name of the glue that keeps goals, tasks, skills, tools (and later memory) coherent under continuous work. After Elyra is stable on Grok, add only a little more form via soft bias and orient shape. Promote to a real package only if transparency or the Memory dual truly needs it.
