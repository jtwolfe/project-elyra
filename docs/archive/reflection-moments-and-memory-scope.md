> **Archived.** Not build freeze. See [../state/stretch-1.md](../state/stretch-1.md) and [../README.md](../README.md).

# Reflection: memory scope — moments, linear atoms, sleep

**Status:** Consideration — **not** an implementation freeze.  
**Date:** 2026-07  
**Pairs with:** [reflection-memory-and-lineage.md](reflection-memory-and-lineage.md), [tools-and-skills.md](../state/tools-and-skills.md), [time-and-identity.md](../state/time-and-identity.md), atomic-memory essay (esp. *what is an atom* / *why atomize*).

---

## 1. The old default (and why it hurts)

Prior Elyra generations often treated **every primitive event** as a peer memory:

```text
message  → atom
tool_call → atom
tool_result → atom
monologue line → atom
…
```

That matches a naive reading of “atomize everything,” but it collides with practice:

| Problem | Effect |
|---------|--------|
| **Flat peer atoms** | A greets and a three-hour debug arc look the same height |
| **No episode boundary** | Retrieval can’t ask “what *run* was that?” only “what line mentioned X?” |
| **Junk density** | Every hop pollutes the graph; thrash and self-similar linear tapes |
| **Ontology too early** | Linking every micro-event invents false structure |
| **Human mismatch** | People remember *episodes* (“that afternoon fixing deploy”), then details *inside* them |

The essay still stands: the unit of memory is a **situated instance**, not a warehouse fact. It also says atoms are indivisible **at the level the mind cares about** — zoom level is not fixed by “one tool call = one atom forever.”

---

## 2. The proposal (in plain terms)

Force structure by **leveraging the work loop we already believe in**.

```text
MOMENT  ≈  one coherent run of the mind
           (like one Grok "session of work until the task stops"
            or one social bout until wait/sleep)

  inside: LINEAR ATOMS  (ordered micro-events of that run)
          tool calls, observations, speaks, decisions, …

  between moments: CHAIN  (what led to what in the day / life)

  after enough moments / day strain: SLEEP
          opaque Stretch-2 consolidation
          (ontologise, deepen links — not a chatty mid-loop skill)
```

### Language note

Do **not** reintroduce elyra2-style *organs* (or similar). Specialization is **skills + toolsets**. Sleep is a **host / Stretch-2 process**, not a persona the loop monologues about.

Rough human analogy:

| Human | Elyra sketch |
|-------|----------------|
| A stretch of continuous engagement | **Moment** (one harness run / arc) |
| Individual beats inside it | **Linear atoms** (ordered tape) |
| Sequence of episodes in a day | **Moment chain** (soft **strain** budget) |
| Sleep (rest after enough waking) | **Opaque Stretch-2 consolidation** (sparse ontology / edges) |
| Facts/skills after many nights | **Patterns / concepts** (not peer atoms; not maximal link spam) |

---

## 3. What is a “moment” / “run”?

Not a formal schema yet — working intuition:

A **moment** is the memory-facing envelope of **one outer harness engagement**:

```text
wake (why-now)
  → skill(s) + tool loop until stop
  → persist
  → sleep-or-schedule
```

Examples that *feel* like one moment:

- User asks to fix a bug → multi-hop tools → speak summary → stop  
- Timer “follow up Developer” → talk skill → speak → maybe schedule again  
- Quiet work: do-work on task T until blocked / hop budget / acceptance  

Examples that are probably **not** one micro-atom-as-whole-life:

- A single `list_dir` in the middle of that bugfix (that’s a linear beat *inside* the moment)  
- An idle process tick with no engagement (maybe no moment at all)

### Open (deliberately unset)

| Question | Tension |
|----------|---------|
| Does one user message always open one moment? | Or can one moment span multi-cycle continue until the *task* ends? |
| Can a moment nest (sub-run inside work)? | Grok subagents vs single mind — careful |
| Soft vs hard close | Model text-only stop vs Critic/acceptance vs budget |
| Social vs work moments | Same envelope type, different skill/toolset tags? |

Likely useful **tags on a moment** (when we specify later): wake reason, skill(s), primary user?, goal/task ids, start/end UTC, stop reason, privacy scope.

---

## 4. Linear atoms *inside* a moment

Keep fine grain **inside** the episode — that’s still atomization, but **scoped**.

```text
Moment M42  "help Developer with deploy"
  [0] wake: user message …
  [1] skill: do-work
  [2] tool: read_file …
  [3] obs: …
  [4] tool: run tests …
  [5] obs: fail
  [6] tool: search_replace …
  [7] speak: "fixed X, still seeing Y"
  [8] stop: hop budget / acceptance / blocked
```

Properties of linear atoms here:

| Property | Note |
|----------|------|
| **Order is free** | Position in the run *is* temporal structure |
| **Context is inherited** | Moment supplies who/why/task; atom needn’t repeat full dossier |
| **World-first preferred** | Tool obs + speak + user lines > monologue chrome (CoT-eval lesson) |
| **Not all equal for dream** | Later consolidation can weight speak + failures higher than list_dir |

This is **not** “abandon atoms.” It is **two levels**:

1. **Beat** — linear atom (local, ordered, cheap)  
2. **Episode** — moment (the instance retrieval often wants first)

Essay-compatible reading: the *instance you care about recalling* is often the moment; beats are how you re-enter and re-traverse it.

---

## 5. Moments chained together

```text
M40 — morning check-in with Developer
  ↓ follows / caused_by / same_day
M41 — implement schedule_wake tool
  ↓
M42 — follow-up speak after timer
  ↓
M43 — …
```

Chain edges are mostly **cheap and structural** (can be host-written without LLM):

- temporal next/prev  
- same user / same goal / same task  
- “opened because timer from M41”  
- day bucket / local date  

Deeper edges (“this moment *contradicts* that belief”) wait for **dream**, not for every tool hop.

Day-scale picture:

```text
        day boundary (local clock)
  ┌─────────────────────────────────────┐
  │  M1 → M2 → M3 → … → Mk              │  waking chain
  │         (moments / runs)            │
  └─────────────────────────────────────┘
                    │
                    v
              SLEEP / DREAM
           (budgeted, offline-ish)
                    │
                    v
         graph: concepts, stronger links,
         decay, summaries of the day
```

---

## 6. Sleep as the place for ontologising (Stretch 2, opaque)

Human constraint used as **architecture**, not poetry:

> Only so many moments in a day; then rest; sleep weaves.

| Waking (Stretch 1; light memory only) | Sleep (Stretch 2, **opaque**) |
|--------------------------------------|----------------------------------|
| Run loops, produce moments + linear tapes | Cluster moments, propose concepts |
| Cheap chain links | Causal / associative / contradiction candidates |
| Structural time, speak, ledger | Edge reweight, decay, sparse summaries |
| Must stay fast and Gemma-safe | Batch / offline-ish; not mid-conversation CoT |

### Opaque by design

Sleep/ontologise should **just happen** as a Stretch-2 host process when the day (or strain budget) says rest — not a skill the model performs in chat, not a glass theatre of “I am dreaming now,” not a new loop stage named like a body part.

Operator glass may show *that* consolidation ran and *what* it wrote (for debugging). The waking loop does not roleplay dream.

### Strain (soft day budget for moments)

Treat waking capacity as **strain**, not only wall-clock.

Intuition (numbers illustrative, not frozen):

```text
soft day budget ≈ "about 16 hours of moment-capacity"
                or a max count / total duration of moments
                after which prefer REST → SLEEP
```

| Goal | Why |
|------|-----|
| **Soft, not hard** | Urgent user messages may still open a moment past the soft cap |
| **Leave room for sleep** | If the day is packed with too many moments, consolidation has too much to review and either skimps or over-links |
| **Protect the graph** | Overfull days + aggressive linking → hypergraph spam; sparse, high-quality edges beat dense junk |
| **Presence, not thrash** | Soft pressure toward rest / fewer new arcs when strain is high |

**Strain rises with** (examples): number of moments, total moment duration, density of beats, open tasks unfinished at “evening.”  
**Strain falls with**: rest, successful sleep that processes the day’s moment set, deferred low-priority wakes.

We do **not** want end-of-day review of hundreds of peer micro-events; we want a **bounded set of moments** so sleep can finish structure without inventing a million weak edges.

### Link budget (with sleep)

| Prefer | Avoid |
|--------|--------|
| Cheap structural chain (follows, same task/user/day) while awake | Deep associative mesh on every tool hop |
| Sparse, reuse-strengthened edges after sleep | Auto-link everything that co-occurred once |
| Propose / promote for risky ontology | Silent dense graph rewrite every night |

Essay warning still applies: small samples invent superstition; **over-linking is the graph version of that.**

Why this helps auto-ontology:

1. **Sample size** — sleep sees a day’s *moments*, not every tool line as a peer  
2. **Cadence** — night (or rest windows), not continuous rewiring  
3. **Cost** — graph work off the hot path  
4. **Identity safety** — sleep must not silently rewrite `identity/core` from user prefs (self ≠ user)  
5. **Capacity** — soft strain keeps the overnight job tractable  

Sleep outputs might be (options, not freeze): concept candidates, a few strengthened edges, day summary, demotion of junk beats — early on **sparse + reviewable**, not maximal connectivity.

---

## 7. How this maps to the Grok-like loop

```text
GROK-LIKE RUN  ─────────────────────────────►  MOMENT
  wake → skill → tool* → stop
       │
       └─ each tool/speak/obs  ─────────────►  LINEAR ATOMS (in moment)

many moments in "day"  ─────────────────────►  CHAIN (+ strain)

soft strain high / night  ───────────────────►  opaque SLEEP → sparse graph weave
```

**Stretch 1 implication (even before full hypergraph):**

- Persist **moments** as first-class envelopes (id, bounds, why-now, stop reason).  
- Persist **ordered beats** under them (trace already almost is this).  
- Host-link **follows** between moments.  
- Optional: refuse to open infinite micro-moments on no-op wakes; later track **strain**.

**Stretch 2 implication:**

- Hypergraph attaches primarily to **moments** (and selective beats), not only to raw tool lines.  
- Traversal: open a concept → member moments → expand beats if needed.  
- Auto-ontologise runs **opaque** on **closed moments / days**, with **link budget** — not mid-hop, not maximal connectivity.

---

## 8. Contrasts with “every event is a peer memory”

| | Flat peer atoms | Moments + linear atoms + opaque sleep |
|--|-----------------|--------------------------------------|
| Primary recall unit | Line / message | Episode, then details |
| Tool hop | Same rank as whole task | Beat inside a run |
| Linking when | Often every write | Cheap chain online; sparse deep links at sleep |
| Risk | Junk graph, thrash | Over-large moments / high strain / over-link |
| Loop fit | Memory fights loop | **Loop defines moment** |
| Human day | Infinite packing | Soft strain → rest → sleep |

---

## 9. Risks and tensions (hold these open)

1. **Moment too big** — whole day as one moment loses internal structure; linear tape must stay.  
2. **Moment too small** — every tool hop re-opens a moment → back to flat peers.  
3. **Continue arcs** — Grok-style multi-cycle “same task” may be **one moment with pauses** or **moment chain with same task_id**; needs a rule later.  
4. **Social half-turns** — “reply only” vs “work then reply” — still one moment?  
5. **What never becomes a moment** — pure host maintenance, failed empty wakes.  
6. **Felt signal** — essay wants valence; moment-level mood vs per-beat?  
7. **Multi-user** — one moment, one primary user (self≠user); multi-party scenes later as hyperedges across moments.  
8. **Sleep authority** — auto-apply vs propose-only vs operator promote; keep **sparse**  
9. **Strain metering** — exact units (hours vs count vs weighted beats); soft vs hard overflow policy  
10. **Language** — never reintroduce *organs* / cast; skills, tools, host sleep only  

---

## 10. Relation to prior Elyra ideas (without re-importing bloat)

| Prior idea | Resonance |
|------------|-----------|
| Summary ladder (15m → day → …) | Moments aggregate into day summaries at sleep |
| Progressive linear tape | Linear atoms *inside* moment |
| Work arcs / hop budgets | Moment bounds ≈ arc bounds; **strain** is day-scale cousin of hop budget |
| OntoFlux / dreaming | Opaque Stretch-2 sleep — **later**, not phase-0, not mid-loop roleplay |
| “Organs” cast | **Drop as product language** — skills + toolsets only |
| CoT-eval “world-first linear” | Beats prefer world delta; monologue optional |
| Greenfield `traces/` | Natural home for linear atoms under moment id |

---

## 11. Working slogan (non-normative)

> **The loop produces moments.  
> Moments hold linear memory.  
> Moments chain into days (under soft strain).  
> Opaque sleep weaves days into meaning — sparsely.**

Stretch 1 can already aim to **emit moments + chains + tapes** (and later track strain).  
Stretch 2 can aim to **sleep those into a navigable concept graph without over-linking**.  
Neither requires every tool call to be a peer hypergraph node on the hot path, nor a waking “dream skill.”

---

## 12. What we are still not deciding

- Exact schema for moment / beat / edge types  
- Exact strain formula and soft-cap numbers (16h is intuition only)  
- Sleep implementation internals (only that it is Stretch 2, opaque, sparse)  
- Close predicates for moments  
- Whether concepts are real nodes or read-models over moments  

Those wait. The point of this note is **scope and zoom**: memory is structured by the **same loop that does work**; ontology is **circadian and capacity-bounded**, not continuous or maximally dense.