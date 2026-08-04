# Metacognition (MC)

| Field | Value |
|-------|--------|
| **Class** | DESIGN |
| **Status** | Shipped (concept + hybrid ontology; Stage B plan ready — see [stage-b-mc.md](stage-b-mc.md)) |
| **Audience** | Implementers |
| **Normative?** | Soft geometry — prefer code on `working` when conflict |
| **Durable path** | `docs/design/grok-improvement-plan/metacognition.md` |
| **Branch (historical)** | `grok-improvement` (tip now **`working`**) |
| **Role in plan** | Named functionality unit; **shallow shape (Stage B / MC-beta)** after Grok path stable; **optional package (Stage C)** only if justified |
| **Engineering rules** | [engineering-principles.md](../../dev/engineering-principles.md) |

---

## 1. Intention

Metacognition (MC) is the **light glue** that keeps Project Elyra’s primary materials interacting coherently under continuous problem-solving.

**Primary materials (the real substance):**

- **Goals / tasks** — durable commitments (why / what)
- **Skills / tools** — procedures and callables (how / with what)
- **Memory** (later) — structured residue of experience

**MC is not:**

- A second mind or parallel worker
- A new runtime subsystem (Stage B explicitly avoids this)
- A free-text planner that bypasses the ledger or stage skills
- An ontology owner
- Something that calls tools or speaks itself
- The owner of hard host policies (speak→glass, skill-commit, thrash, continuous gates, usage)

**MC is:**

- The path / glue that guides how those materials interact
- Just enough structure so continuous work stays coherent
- **Soft shape on the existing CoT** (Decide lean), not a constraint layer over it

The do-loop remains the only executor. MC decides and hands off (or, in the lightest form, makes the existing Decide path more legible); it does not perform the work.

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

## 3. Hybrid ontology: soft Decide vs hard policies

This section records a **locked design decision** (operator + plan discussion, 2026-07). Implementers must not “simplify” by consolidating both layers into one MC module.

### 3.1 Two kinds of structure

| Kind | Nature | Examples in codebase today |
|------|--------|----------------------------|
| **Soft Decide** | Interpretive lean in the CoT — provisional, language the model uses while choosing a stage skill | `format_skill_bias()`, `prompts/orient.md` hygiene, stage skill playbooks (`talk`, `do-work`, …) |
| **Hard policies** | Host law — integrity, channels, recovery even when the model is confused | SpeakTransport + `counts_as_speak` (free-text never glass), `NO_SPEAK_NUDGE`, skill-commit HOST, thrash HOST, continuous progress gates, usage hard-stop |

Soft Decide is the **growth surface** (judgment under incomplete information; pattern-shaped; editable by experience later).  
Hard policies are the **survival surface** (skeleton / immune system: glass, disk, budget, fail-closed promote).

### 3.2 Hybrid decision

```text
[ Soft Decide — name this MC / Stage B ]
  orient cadence + ledger-aware bias + handover vocabulary
           │
           ▼
[ Do-loop only executor ]
           │
           ├── skill-commit HOST      (stay)
           ├── no-speak / speak law   (stay; soft answer-speak first)
           ├── thrash HOST            (stay)
           ├── continuous gates       (stay; default OFF)
           └── usage hard stop        (stay)
```

- **Consolidate vocabulary and soft bias** under MC naming.  
- **Do not consolidate enforcement** under MC.  
- Prefer **extend `format_skill_bias` / orient / skills** over a parallel “MC decide engine.”  
- New HOST gates only when soft text fails repeatedly in dogfood.  
- Stage C package only if self-mod or Memory dual forces an inspectable process body.

**Why not full consolidation:** folding hard policies into MC treats law as opinion (glass can be “satisfied” by status speak while the operator sees silence). Folding soft Decide into hard gates treats judgment as a workflow engine and kills continuous problem-solving. Prefer final **product outcome** (coherent choice + stable walls) over a tidy org chart of the code.

### 3.3 Procedural value (CoT, personhood, self-mod)

| | Soft Decide | Hard policies |
|--|-------------|---------------|
| **In the CoT** | Stage preference, completion reminders, honest idle | HOST lines and stop conditions the model cannot talk past |
| **Person / glass** | Better judgment about what to do next | Words actually reach glass; promote is really callable; budget is real |
| **Self-improvement** | Bias/orient/skill text should become editable patterns | Protected seams so self-edit cannot erase channel honesty |
| **Failure mode if overused** | Ignored under load; contradictory soft advice | Brittle ceremony; second planner |

### 3.4 Alignment with atomized memory (Phase 3 gravity)

Design thesis (operator essay *What is wrong with my memory?* — recommend in-repo as [`docs/memory-atoms.pdf`](../../memory-atoms.pdf) (PDF in-repo; optional `.md` twin later)): memory is **organized experience**, not a warehouse of facts. Units are **instances (atoms)** with content, context, felt signal, and connections; knowledge (facts, opinions, causal rules) is a **pattern / shadow** across many atoms; edges have strength; forgetting and consolidation keep the graph usable.

| System piece | Essay analogue |
|--------------|----------------|
| Moments, tool results, speaks, ledger events | Atoms (instances with context) |
| Soft Decide bias / cadence / handover language | Emerging patterns — should stay soft and updateable |
| Hard policies (speak→glass, fail-closed growth, usage) | Substrate / channel rules that make a usable graph possible — not ordinary opinions |

Stage B lays soft patterns. It does **not** implement the mnemonic substrate.

### 3.5 Super-future (thin interpreter; do not build in Stage B)

Long arc:

1. Skills, tools, goals/tasks, and eventually MC **process language** addressable in memory.  
2. CoT as **ephemeral context** (activation over the graph for this moment).  
3. Thin outer interpreter: claim wake, run moment, enforce channel laws, persist atoms/edges.

**Standing constraint:** even then, speak→glass, fail-closed promote, and usage ceilings remain **outer invariants**. If they become only soft atoms, a self-modifying person can edit away the difference between “thought the answer” and “spoke the answer.”

---

## 4. What already exists (codebase reality)

Stretch 1 already supplies most of the outer loop:

| Piece | Present today |
|-------|----------------|
| Commitment axis | Goals/tasks ledger + orient goals slice |
| Action axis | Stage skills (`talk`, `plan-work`, `do-work`, `review-work`, `rest`, `create-tool`, `create-skill`) + tools |
| Soft Decide | `format_skill_bias()` by **wake kind** (not yet ledger-aware) + short orient hygiene |
| Follow-through | Skill-commit HOST after `load_skill` |
| Social presence | Hop-0 speak pin; talk skill; no-speak nudge if never spoke |
| Executor | Single do-loop only |
| Continuous work | Opt-in; **default OFF**; progress = non-speak tools or ledger |
| Glass contract | Only successful `speak` (`counts_as_speak`) reaches glass |

What is thin is **shape**, not machinery: Decide is free CoT plus wake-kind bias; **answer speak vs status speak** is not yet named in orient/skills. That gap produces “inner green, outer incomplete” moments (see §6).

---

## 5. Employment plan

### Stage A — Naming (done during Phase 0)

- Use **metacognition / MC** in design docs.  
- No MC implementation required for Phase 0.  

### Stage B — Shallow shape (next; after Phase 0 stable / H dogfood preferred)

**Nickname:** **MC-beta** means this Stage B workstream only — not “fake MC until Stage C.”

**Detailed implementation plan:** [stage-b-mc.md](stage-b-mc.md) (file targets, tests, glass checklist, non-goals).

Summary:

1. **Ledger-aware soft bias** — still one line, still not a hard gate.  
2. **Short Decide cadence in orient.**  
3. **Handover vocabulary** — especially **status speak** vs **answer speak** in orient + talk (soft first).  
4. **No new runtime object.**

### Stage C — Optional form (later, only if needed)

Promote MC to a thin privileged on-disk package **only if**:

- self-modification needs an inspectable, fail-closed-updatable process body, or  
- the Memory dual needs a durable peer interface awkward to keep purely in orient/bias.

If that happens:

- Package is skill-shaped (loadable, inspectable).  
- Job remains Decide / handoff only — no tool calls, no free-text plan that bypasses stage skills.  
- Same fail-closed path as tools/skills.  

**Stage C is not “the first real MC.”** Stage B is the first behavioral MC. Stage C is optional structure.

Until Stage C is justified, keep MC as conceptual + Stage B shape.

---

## 6. Handover gate concepts

**Status:** Stage B uses these as **vocabulary and soft guidance**, not as a new gate subsystem.  
**Name:** handover gate concepts — transition conditions where work must change hands cleanly, or the moment “succeeds” on tape while the operator sees a failure.

### 6.1 Live dogfood seed (status vs answer speak)

**Case — social calc Q&A (`13be7a22` → `a7e62ecc`, 2026-07-24):**

1. User asked for a numeric answer (tool-using social wake).  
2. Early **`speak`**: “Calculating…” → `spoke=true`.  
3. **`calculator` ran successfully**; result on the tool beat.  
4. Model put the **full answer in free-text**.  
5. Stop: `spoke=true`, `tools_ran=true`, `stop_reason=no_tools`.  
6. Glass never showed the number.

| Layer | Fact |
|-------|------|
| Glass contract | Only successful **`speak`** reaches glass; free-text is tape/orphan by design |
| Early speak | Status speak satisfied no-speak policy |
| Completion | Nothing required **answer speak** after tools on a user question |
| Continuous | OFF — no second moment to recover |

Same family as other thrash: **inner ceremony green, outer handoff incomplete.**

### 6.2 Candidate gates (review list)

| Gate (working name) | Boundary | Obligation (intent) | Stage B treatment |
|---------------------|----------|---------------------|-------------------|
| **Answer speak** | tools → glass | Final `speak` carries the answer after tools on a user Q | **Soft first** (orient + talk) |
| **Status vs result** | social ack → completion | `spoke=true` ≠ user got the answer | **Soft vocabulary** |
| **Skill load → first tool** | Decide → stage skill | First real step is skill’s tool path | **Already host** (skill-commit) |
| **Draft → verify → promote → call** | growth path | Green promote implies callable | **Already host** (H-series) |
| **Catalog ↔ disk** | registry ↔ tools/local | List matches tree | **Already host** |
| **Task ready ↔ truth** | ledger → wake | Ready only when work remains | Soft bias + existing ledger |
| **Blocked ↔ operator** | do-work → wait | Clear wait_user when host-only | Skill language |
| **Review → close** | evidence → goal closed | Close after acceptance | review-work skill |
| **Continuous handoff** | moment end → continue | Progress + honest next step | **Already host**; default OFF |
| **Wait arm** | ask → glass choices | Wait armed and visible | Existing wait path |

**Pattern to hunt:** what did tape/ledger/registry believe was done that glass/wait/task list never received?

### 6.3 Standing constraints

1. Prefer soft Decide / skill language over new hard HOST gates — unless dogfood proves soft text fails repeatedly.  
2. Do **not** auto-promote free-text to glass.  
3. Do **not** make MC speak or run tools.  
4. Do **not** over-specify hop-by-hop.  
5. Reuse existing machinery (`counts_as_speak`, no-speak, skill-commit, thrash, continuous, promote/verify).  
6. Quantize vocabulary (status speak / answer speak / …) for Stage B and later Memory events.

---

## 7. Peer hooks for Memory (leave open)

Even while Memory is Phase 3, Stage B language should leave three clean hooks:

1. **State input** — orient / Decide slot later also receives relevant memories for this goal/task.  
2. **Event write** — beats and ledger changes already happen; Memory will index them. MC does not own the write path.  
3. **No hard-coded ontology** — do not bake concept graphs into MC or orient Decide text.

---

## 8. Design rules (standing)

1. Keep the two hierarchies sharp (goal/task above skill/tool).  
2. Treat Memory and MC as dual from the start of the design conversation.  
3. Let the do-loop remain the only executor.  
4. Anchor interpretation in the ledger (+ later memory), not in free-form reflection.  
5. Prefer “privileged process language / soft shape” over “new subsystem.”  
6. Quantize Decide just enough without freezing creativity.  
7. Design for eventual transparency carefully; do not require a package in the first instantiation.  
8. **Do not over-constrain the CoT.**  
9. **Hybrid:** soft Decide under MC naming; hard policies stay host law.  
10. Respect [engineering-principles.md](../../dev/engineering-principles.md): no god modules, tests with features, prompts/skills on disk, no Stretch 3 graph smuggled early.

---

## 9. Fit in the Grok Improvement Plan

| Phase | MC-related work |
|-------|------------------|
| **Phase 0** | Name the unit. No MC implementation. (Complete on branch; operator smoke.) |
| **After Phase 0 stable** (+ H live preferred) | **Stage B / MC-beta** — [stage-b-mc.md](stage-b-mc.md) |
| **Phase 1** | `grok_build` tool + self-improve scaffolding; Stage B may land just before; **MC package not required** |
| **Phase 2** | If a package form exists, eligible for self-mod continuity like skills/tools |
| **Phase 3** | Memory substrate as equal peer; hooks light up; atomized experience model |

Do not block the Grok path on MC form. Prefer Stage B before continuous ON experiments and before Phase 1 multiplies incomplete glass handoffs under a stronger instrument.

---

## 10. Non-goals for MC work

- New parallel worker or second do-loop  
- MC calling tools or speaking directly  
- Hard gates that replace free CoT stage choice  
- Ontology or concept graphs inside MC  
- Requiring a package before Memory exists  
- Treating Stage C as mandatory “first real MC”  
- Expanding continuous-work policy under the banner of MC  
- Consolidating skill-commit / thrash / continuous / usage into an MC module  

---

**Bottom line:**  
Metacognition is light Decide/handoff glue. **Stage B (MC-beta)** is the real first behavioral implementation: ledger-aware bias, short Decide cadence, status vs answer speak — soft, on disk, tested. Hard policies stay where they are. Stage C is optional package form. Memory arrives later as peer substrate; soft patterns Stage B introduces are what that substrate should eventually absorb — without dissolving channel law into ordinary opinion.

**Implement next:** [stage-b-mc.md](stage-b-mc.md).
