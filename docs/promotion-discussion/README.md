# Promotion discussion — v0.1 close & long-term gym

| Field | Value |
|-------|--------|
| **Created** | 2026-07-30 |
| **Goal** | Orient **promotion to a durable v0.1 product cut** + instantiate a **long-term self-improvement gym** (process + GitHub Project), without pretending unfinished dogfood is done |
| **Branch context** | Product work has lived on `grok-improvement` → `grok-improvement-memory` → `grok-improv-radeonvii`; tips and docs drift — sanitation is part of v0.1 |
| **Status** | Working discussion (not a freeze contract). When it conflicts with Stretch 1 runtime rules, [stretch-1.md](../stretch-1.md) wins |

This folder is the home for **promotion / v0.1** thinking: what we have, what remains, meal/context limits, and how to track everything that landed in ~1–2 weeks of dense build.

**Index**

| Doc | Role |
|-----|------|
| **This README** | Charter, roadmap reflection, GitHub Project, meal reconstruction & budgets |
| *(later)* | Exit criteria checklist, milestone seed issues, merge plan |

Related: [project-status-pass.md](../project-status-pass.md) (stale snapshot), [stretch-2/README.md](../stretch-2/README.md), [grok-improvement-plan/README.md](../grok-improvement-plan/README.md), [known-bugs.md](../known-bugs.md), [radeon-vii-dev/README.md](../radeon-vii-dev/README.md).

---

## 1. One-line product truth (2026-07-30)

We have a **real always-on teammate runtime on Grok**, with **Stage B soft metacognition**, **sandbox fitness**, **identity prep**, and a **full Stretch 2 Phase 1 + 2 + 2a code stack** (semantic + directed walk; flags default **off**). We have a **working Radeon VII GPU embed path** (standalone green; product load on ROCm; **in-moment encode unconfirmed**). We do **not** yet have: **`grok_build` tool**, **GI Phase 2 self-mod continuity**, **Stretch 2 Phase 3 procedural** (experimental — may defer), or a **clean institutional gym** (GitHub Project + one product tip + honest status).

**v0.1 is achievable** if defined as *instrument + dogfooded memory path + process gym*, not *all research phases closed*.

---

## 2. Reflection — what shipped vs what remains

### 2.1 Layers (honest)

| Layer | State |
|--------|--------|
| Stretch 1 harness | **Shipped** — presence, moments, tools/skills, goals, glass, sandbox |
| GI Phase 0 Grok path + meter | **Shipped** |
| H1–H6 harness / sandbox fitness | **Shipped** |
| Stage B MC-beta | **Shipped** (some status docs still lag) |
| Identity + multi-user prep | **Shipped** |
| Stretch 2 Phase 1 (temporal / meal spine) | **Code done** |
| Stretch 2 Phase 2 + rectification | **Code done**; **operator smoke / Gate B pending**; flags default **off** |
| Stretch 2 Phase 2a (directed traversal) | **Code done**; **operator smoke pending**; flags default **off** |
| Nemotron / ROCm (Radeon VII) | Standalone A1–A7 **PASS** after Tensile inject; product `embed_device=rocm` **load** seen; **moment encode open** (BUG-mem-gpu-01) |
| GI Phase 1 `grok_build` | **Not started** |
| GI Phase 2 self-mod continuity | **Not started** |
| Stretch 2 Phase 3 procedural | **Planned / experimental** — **not required for immediate v0.1** |
| Stage C MC package | **Optional**, not next |
| Promote integration → `main` | Operator sign-off |

### 2.2 Execute-plan complete ≠ product target

Stretch 2 README already states this. For v0.1 we must not confuse:

- **Code landed** (PR stacks green hermetically), with  
- **Product dogfood** (semantic meal works on this host with Nemotron; encode during moments; Graph walk feels real).

### 2.3 v0.1 program pillars (revised)

| Pillar | Program name | Nature | v0.1 priority |
|--------|--------------|--------|----------------|
| **Grok Build tool** | GI **Phase 1** | New capability — person/instrument split | **Core** |
| **Memory Phase 2 product close** | Smoke + Gate B + moment-encode dig | Dogfood / bugs, not a big new design | **Core** |
| **Stretch 2 Phase 3 procedural** | Success-path weights | **Experimental**; evaluation-first; **may defer** past v0.1 after more consideration | **Defer-friendly** |
| **Sanitation + GitHub Project** | Long-term self-improvement **gym** | Institutional; almost **immediately** justified | **Core / immediate** |

### 2.4 Proposed v0.1 definition

**In:**

1. One clear **product tip** (merge/FF plan across gi / memory / radeon lines) + refreshed status docs.  
2. **`grok_build` tool** under self-improve scaffolding, fail-closed, dogfood once.  
3. Memory **Phase 2 path dogfooded enough** to trust (smoke + answer moment-encode); flags may stay default-off.  
4. **GitHub Project + v0.1 milestone** seeded from known-bugs + remaining work.  
5. Context/meal issues **recorded and triaged** (see §4–5); size/chat-chain fixes as follow-on cards if not fixed in v0.1 window.

**Out of v0.1 (default):**

- Full GI Phase 2 self-mod (worktree / promote / restart continuity)  
- Stretch 2 Phase 3 **default-on** or large procedural build without eval plan  
- Stage C MC package  
- Per-user glass isolation  
- Claiming “GPU embed product fixed” without moment-encode confirmation  

### 2.5 Suggested order

```text
0. GitHub Project + sanitation (almost immediately)
   - Milestone v0.1, workstream views, seed issues
   - Status pass rewrite; branch tip honesty

1. Memory Phase 2 product close (dogfood-heavy)
   - Moment encode dig (BUG-mem-gpu-01)
   - Semantic (+ optional 2a) smoke on this box
   - Context / chat-chain / meal size triage (§4–5)

2. Grok Build tool (GI Phase 1) — main implementation
   - Design confirm if needed → implement → dogfood

3. Optional: Stretch 2 Phase 3 research track
   - Only after explicit go; evaluation-first; not a v0.1 gate

4. Promote / gym loop
   - First self-improve goal via tool; backlog groom for post-v0.1
```

### 2.6 Stretch 2 Phase 3 — defer note (operator direction 2026-07-30)

Phase 3 (procedural / success-path) remains **interesting** for a long-term gym narrative but is **experimental** and may need more design consideration. **v0.1 can proceed without Phase 3.** Prefer productizing Phase 2/2a dogfood + Grok Build + Project board over opening another memory regime under time pressure.

---

## 3. GitHub Project — long-term self-improvement gym

### 3.1 Why now

In ~1–2 weeks the repo became **large and featureful**: harness, Grok path, Stage B, identity, full memory phases 1–2a, GPU embed train, glass panels, known-bugs sprawl. Markdown alone is no longer a reliable **tracking surface**. Instantiating a **GitHub Project** is justified **almost immediately** — even before `grok_build` lands — as the outer loop for humans (and later Elyra-assisted issue updates).

### 3.2 What the gym is

Not a second mind. Same philosophy as goals/tasks:

| Inner (Elyra) | Outer (GitHub Project) |
|---------------|-------------------------|
| Goals / tasks / moments | Issues / milestones / status |
| Glass | Project board views |
| Person + instrument later | Humans own promote/quota; Elyra may file/update cards later |

### 3.3 Suggested shape (first instantiation)

**Project name:** e.g. `Elyra — product & self-improve gym`

**Views (columns or status field):**

| Status | Meaning |
|--------|---------|
| Backlog | Known, not scheduled |
| Ready | Spec clear enough to start |
| In progress | Active work |
| Dogfood | Needs operator smoke / live proof |
| Blocked | Waiting on hardware, design, or dep |
| Done | Merged + noted |

**Workstreams (labels or custom field):**

- `harness` — presence, loop, sandbox, glass chrome  
- `memory` — Stretch 2, meal, vectors, graph  
- `embed-gpu` — Nemotron, ROCm, BUG-mem-gpu-01  
- `grok-build` — GI Phase 1 tool  
- `self-mod` — GI Phase 2 (post-v0.1 default)  
- `docs-hygiene` — status pass, branch map  
- `context-meal` — budget, chat chain, reconstruction  

**Milestone:** `v0.1` with exit criteria from §2.4.

**Seed from (non-exhaustive):**

- BUG-mem-gpu-01 (moment encode + device matrix + setup script ongoing)  
- BUG-mem-p2-01 (rectified; dogfood verify)  
- BUG-wake-02, glass polish bugs (as capacity allows)  
- GI Phase 1 design/implement `grok_build`  
- Branch merge / FF plan (memory + radeon → product tip)  
- Context: meal size ~50% model window (proposal)  
- Context: immediate chat chain under memory meal (bug/feature)  
- Stretch 2 Phase 3 — **backlog / experimental**, not v0.1 gate  

### 3.4 Guardrails

- Do not auto-merge from the board.  
- Secrets never in issues.  
- Machine-specific freezes stay under `docs/radeon-vii-dev/`.  
- Prefer linking known-bugs IDs and design paths in issue bodies.  
- Later: Elyra tools for issue CRUD are optional; **human ownership of promote-to-main** remains.

### 3.5 Immediate next ops step

Create the Project + milestone + seed issues from this doc. No code change required. Can happen **before** any further feature PR.

---

## 4. Context pressure — chat chain vs reconstructed meal

### 4.1 Operator signal (2026-07-30)

Recent conversation with Elyra suggests we may be **context-limited**, and that **reconstructed context may not adequately include the immediate chat chain**. Treat as **open product observation** (not yet sealed with moment ids / token dumps).

### 4.2 Two different “histories”

| Surface | What it is | Where it lives |
|---------|------------|----------------|
| **Glass chat** | User/assistant rows in the UI | `data/messages.jsonl` |
| **Memory atoms** | Durable instances (moments, speak/obs, ladder, …) | Lance / JSONL store |
| **In-turn chain** | Tool hops inside one open moment | Do-loop `chain_messages` (ephemeral to the moment) |

These are **not** the same stream. Confusion here drives “she forgot what we just said.”

### 4.3 What the code does today (memory path active)

When memory meal is **active** (`_memory_meal_active()`), presence rebuilds the **outer prefix** as a **labeled memory meal**, explicitly **not** a full sliding glass history:

```text
# presence/worker.py (concept)
# Memory path: labeled meal (no full sliding glass) + media expand via glass index
```

Order of outer messages (`compose_outer_messages`):

```text
system
→ episodic (broader prior)
→ semantic (if enabled)
→ directed_keep (if active)
→ temporal (open-moment atoms)
→ orient
```

Then the do-loop appends the **in-turn chain** (tool hops):

```text
model_call_messages = outer_prefix + chain_messages
```

Documented in `compose_outer_messages`:

> Chain (tool hops) is owned by doloop and is **not** included [in the meal package].

**Implication:** Immediate multi-turn **glass chat** is **not** wholesale-injected into the memory meal. Glass is used for **media expand** (`index_glass`) and wake correlation. Chat content appears in the model call only if it has been:

- promoted / written as **open-moment atoms** (temporal),  
- present as **wake content**,  
- carried in the **in-turn chain** for the current moment, or  
- retrieved via **episodic / semantic** from prior material.

If atoms lag glass (encode/write timing) or a new moment starts without re-hydrating recent chat into temporal/episodic, the model can look **context-amnesic on the chat chain** even when Memory glass shows a rich meal.

### 4.4 Legacy path (memory meal inactive)

`loop.context.build_outer_prefix`: **system → sliding glass history → orient**, budgeted by `sliding_input_tokens`, drop oldest history first, protect wake trigger. That path **does** center the glass chain — and is what memory meal **replaced** when memory is on.

### 4.5 In-turn chain truncation

Even with a good outer meal, `enforce_in_turn_budget` can drop/compress oldest chain batches when `outer + chain` exceeds `min(sliding_input_tokens, in_turn_max_tokens)`. Long tool-heavy moments can lose early hop text while outer memory stays large.

### 4.6 Follow-up cards (for GitHub Project)

1. **Confirm** with a dogfood moment: dump channel token counts + whether last N glass user/assistant rows appear as temporal atoms.  
2. **Design option A:** hybrid outer = memory supports + **recent glass tail** (fixed token band for immediate chat).  
3. **Design option B:** stronger wake/promote path so every glass turn is an atom before next hop.  
4. **Design option C:** on moment continue / social wake, seed temporal from last K glass messages.  
5. Do not fix by only raising budget if the **selection set** excludes chat (larger budget won’t pull missing channels).

---

## 5. Meal reconstruction & fractional breakdown

### 5.1 Budget vs model window (current constants)

| Knob | Default (code) | Role |
|------|----------------|------|
| `MODEL_CONTEXT_WINDOW_TOKENS` | **500_000** | Full model window (Grok 4.5 class); glass rail denominator |
| `LoopSettings.sliding_input_tokens` | **50_000** | Outer meal / sliding budget |
| `LoopSettings.in_turn_max_tokens` | **50_000** | Outer + chain combined in-turn cap (min with sliding) |
| `DEFAULT_MEAL_BUDGET_TOKENS` | **50_000** | Same number; memory `compose_meal` default |
| Token estimate | `len(text) // 4` | Heuristic only |

**Today’s outer meal ≈ 50k / 500k = ~10% of the model window**, not ~50%. Generation (`generation_max_tokens` default 8192) and the in-turn chain still need headroom inside the remaining window, but **50k outer is conservative** relative to a 500k model.

### 5.2 Construction flow (memory meal)

```text
1. Fixed cost
   system_text + orient_text  →  fixed_tokens = estimate(system)+estimate(orient)

2. Residual R = max(0, budget_tokens - fixed)

3. Split R via split_memory_budget_v3 (flags matter)
   → semantic_cap, directed_keep_cap, episodic_cap, temporal_cap
   with temporal floor

4. Select into caps
   - temporal: open-moment atoms (list_by_moment), slide-off if over
   - episodic: summaries (up to 70% of epi cap) then prior moments
   - semantic: ANN neighbours under wall-clock + token cap (if enabled)
   - directed_keep: last confirmed keep-set (if active)

5. Dedup
   open-moment wins over episodic; temporal+episodic win over semantic;
   directed_keep after semantic for same-id priority (KD-A8)

6. Render order
   system → epi → sem → directed_keep → temporal → orient
   then doloop chain_messages
```

### 5.3 Fractional split of residual **R** (not of full model window)

Defaults from `MemorySettings` / `split_memory_budget_v3`:

| Channel | Config | Default share of **R** | Notes |
|---------|--------|------------------------|--------|
| **Semantic** | `semantic_fraction` | **0.12** if semantic on, else 0 | Cut first when enforcing temporal floor |
| **Directed-keep** | `directed_keep_fraction` | **0.08** if keep active, else 0 | Cut second under floor pressure |
| **Episodic** | `episodic_fraction` / `episodic_fraction_with_semantic` | **0.20** (no semantic) / **0.18** (with semantic) | Cut third under floor pressure |
| **Temporal (open moment)** | remainder after above | **≥ `temporal_min_fraction` (0.55)** of R | Floor protected; “what is happening now” |
| **System + orient** | outside R | paid first from full meal budget | Always present when provided |

**Worked example** (semantic on, directed_keep off, ignore fixed for residual fractions):

| Channel | Fraction of R | Tokens if R = 45_000 |
|---------|---------------|----------------------|
| Semantic | 12% | 5_400 |
| Episodic | 18% | 8_100 |
| Temporal | 70% (rest; above 55% floor) | 31_500 |

**With directed_keep active** (semantic on): sem 12% + keep 8% + epi 18% = 38% supports → temporal starts at 62%, still above 55% floor.

**Illustrative design doc** ([design-context-meal-composition.md](../stretch-2/design-context-meal-composition.md)) used ~40–50% temporal / ~10%+ episodic / ~10–15% semantic / ~10% keep — **non-normative**. Code is the law for product: **≥55% temporal floor on residual**, supports smaller.

### 5.4 Episodic internal split

Of the episodic **cap**, select_episodic targets up to **70%** for ladder **summaries** (coarse→fine), then fills with **prior-moment raw** atoms. Open moment is excluded from broader episodic.

### 5.5 Slide-off

If open-moment temporal exceeds its cap, slide-off drops oldest unprotected open-moment material from the **meal only** (store keeps durable atoms). Supports are cut before the spine under budget pressure (design principle; floor enforcement implements temporal protection).

### 5.6 Proposal — extend meal size toward ~50% of model context

| Idea | Detail |
|------|--------|
| **Target** | Outer meal budget ≈ **~50% of `model_context_window_tokens`** → ~**250k** if window is 500k |
| **Today** | 50k outer (~10% of 500k) |
| **Motivation** | Reduce artificial starvation; long social/work threads; richer temporal + supports |
| **Cautions** | (1) In-turn chain + generation still need room — raise `sliding_input_tokens` and review `in_turn_max_tokens` together. (2) Cost/latency/quota rise. (3) **Does not fix missing chat channel** if glass is still excluded from memory meal. (4) Token estimate is `len//4` — real tokenizer may differ. (5) Semantic select wall-clock may dominate before token cap on CPU/ROCm. |

**Suggested experiment (not implemented here):**

1. Config: `sliding_input_tokens` / meal budget → e.g. **150k–250k** (step up, measure).  
2. Keep temporal floor fractions unless dogfood shows imbalance.  
3. Add **glass-tail band** (fixed or fraction of R) if chat-chain gap confirmed.  
4. Meter: glass context fill UI already uses model window as denominator — validate operator-visible %.  
5. Track as Project cards: `context-meal` workstream.

---

## 6. Risks if we rush promotion

- Grok Build without memory dogfood → strong instrument, weak continuity (acceptable if explicit).  
- Phase 3 without Gate B → more surface, still empty semantic meals under load.  
- GitHub Project without branch hygiene → issues point at unreachable tips.  
- Raising meal size only → does not restore immediate chat if selection excludes glass.  
- Calling Radeon Tensile inject “all AMD” → keep generic modern ROCm vs VII-dev (BUG-mem-gpu-01).

---

## 7. Immediate checklist (promotion program)

- [ ] Create GitHub Project + **v0.1** milestone; seed issues from §3.3  
- [ ] Refresh [project-status-pass.md](../project-status-pass.md) or replace with link to this folder  
- [ ] Branch FF/merge plan: product tip includes memory + radeon dogfood  
- [ ] Dogfood: Phase 2 semantic smoke; moment encode; note chat-chain inclusion  
- [ ] Triage meal budget experiment (~50% window) vs chat-tail hybrid design  
- [ ] Design/implement **`grok_build`** (GI Phase 1)  
- [ ] Explicitly **defer** Stretch 2 Phase 3 unless reopened  
- [ ] Keep BUG-mem-gpu-01 Open until product-path encode + durable setup story  

---

## 8. Source map (for future agents)

| Topic | Code / doc |
|-------|------------|
| Meal compose | `elyra/memory/meal.py` — `compose_meal`, `compose_outer_messages` |
| Budget split | `elyra/memory/tokens.py` — `split_memory_budget_v3` |
| Memory settings fractions | `elyra/memory/config.py` — `semantic_fraction`, `episodic_*`, `temporal_min_fraction` |
| Presence chooses meal vs glass | `elyra/presence/worker.py` — `_memory_meal_active`, rebuild_outer |
| Legacy glass sliding | `elyra/loop/context.py` — `build_outer_prefix` |
| In-turn chain budget | `elyra/loop/doloop.py` — `enforce_in_turn_budget` |
| Loop knobs | `elyra/settings.py` — `LoopSettings` |
| Model window | `elyra/llm/constants.py` — `MODEL_CONTEXT_WINDOW_TOKENS` |
| Design fractions (illustrative) | `docs/stretch-2/design-context-meal-composition.md` |
| GPU / start Elyra | `docs/radeon-vii-dev/README.md` |
| Known open bugs | `docs/known-bugs.md` |

---

*Promotion discussion seed 2026-07-30. Capture decisions here as they firm; link the GitHub Project URL when created.*
