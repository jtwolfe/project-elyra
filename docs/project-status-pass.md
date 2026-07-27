# Project status pass — where we are, small cleanup, what comes next

| Field | Value |
|-------|--------|
| **Date** | 2026-07-26 |
| **Branch tip** | `grok-improvement` (product integration branch; `main` is behind) |
| **Purpose** | Honest snapshot of the project as a whole; small prep before Grok Build + memory integration; open questions for development workflow and larger self-improvement |

This is a **status and orientation** note, not a freeze contract. When it conflicts with [stretch-1.md](stretch-1.md) on Stretch 1 runtime rules, stretch-1 wins.

---

## 1. One-line summary

**Stretch 1 is a real always-on agent harness.** On `grok-improvement`, Elyra already runs on **Grok**, with usage metering, warm sandbox fitness, **Stage B soft metacognition**, **versioned self/other identity**, multi-user *prep*, and **Aurimago gold glass**. **Phase 3 memory is not built.** Several status docs are **stale** relative to code. Session user does **not** yet mean per-user chat glass.

---

## 2. What the product is

```text
elyra start
  ├── LLM (xAI Grok default on gi; local Gemma path still exists)
  ├── HTTP API + glass UI  →  http://127.0.0.1:8787/
  └── PresenceWorker
        wake queue + timers
             │
             ▼
        open MOMENT  (= one do-loop)
          model ↔ tools until stop / wait
          skills, speak / wait, sandbox, ledger
             │
             ▼
        close moment · persist beats · next wake
```

| Unit | Role |
|------|------|
| **Presence** | Always-on host; one moment at a time |
| **Wake queue** | What starts the next do-loop |
| **Moment** | One full do-loop until stop / wait |
| **Tools / skills** | Callable actions + markdown playbooks |
| **Goals / tasks** | Durable *what* (shared ledger + provenance tags) |
| **Self / users** | Durable *who* (separate stores; draft → promote) |
| **Sandbox** | Host tree + guest exec (default ON) |
| **Glass** | Operator console (chat, wait, goals, moments, tools, identity, status) |

**Stance (unchanged):** one mind; speak-only glass; self ≠ user; no language debt; dogfood-created tools/skills match builtins.

---

## 3. Roadmap layers — done vs not

```text
Stretch 1 foundation          ── SHIPPED
Phase 0 Grok path + meter     ── SHIPPED on grok-improvement
H1–H6 harness / sandbox       ── SHIPPED on grok-improvement
Stage B MC-beta (soft Decide) ── SHIPPED on grok-improvement
Identity + multi-user prep    ── SHIPPED on grok-improvement
Glass Aurimago gold polish    ── SHIPPED on grok-improvement
        │
Small cleanup pass (below)    ── NEXT (before big integrations)
        │
Phase 1 grok_build tool       ── NOT STARTED
Phase 2 self-mod continuity   ── NOT STARTED
Phase 3 memory atoms          ── ESSAY ONLY (docs/memory-atoms.pdf)
Stage C MC package            ── OPTIONAL; not next
Per-user glass / group chat   ── PRECONDITIONS ONLY (see §5)
Promote gi → main             ── OPERATOR SIGN-OFF
```

### Shipped in more detail

| Area | Notes |
|------|--------|
| Stretch 1 core | Presence, moments, do-loop, tools/skills, speak/wait, goals, glass panels |
| Inference | Grok default on gi; usage meter + hard-stops; continuous **default OFF** |
| Sandbox | Warm MSB sandbox0; create-tool path largely green in dogfood |
| Stage B MC | Ledger-aware skill bias; orient Decide; status vs answer-speak (soft path only) |
| Identity | Versioned self/users; get/draft/promote tools; review/update skills; gates/grants |
| Multi-user prep | Digests, session switcher, actor labels, `created_in_context`, work-origin USER inject |
| Glass UX | Gold theme, density polish, brand from self display name |

### Incomplete or mixed (expected gaps)

| Gap | Reality |
|-----|---------|
| Session user ≠ chat lens | Switching user does **not** change transcript; one global `messages.jsonl` |
| Per-user glass | Tags exist; filter/isolation does **not** |
| Memory | Moments + linear tape; thrash lessons moment-scoped; **no atom/hypergraph** |
| Hard answer-speak HOST | Soft path only |
| Stage C / MC package | Intentionally not built |
| `grok_build` self-improve | Phase 1 — not started |
| Privacy / external auth / email users | Deferred |
| `main` vs `gi` | `main` lags integration branch |

---

## 4. Docs health — leftovers and drift

### Still truthful

- [stretch-1.md](stretch-1.md), [engineering-principles.md](engineering-principles.md)
- [tools-and-skills.md](tools-and-skills.md), [time-and-identity.md](time-and-identity.md) (identity draft/promote + capability-growth catalog/dogfood)
- Design docs for recent work: [design-identity-self-other-multi-user.md](design-identity-self-other-multi-user.md), [design-glass-aurimago-gold-polish.md](design-glass-aurimago-gold-polish.md)
- Capability growth: [design-capability-growth-search-browse-vcs-secrets.md](design-capability-growth-search-browse-vcs-secrets.md), [design-capability-growth-implementation-plan.md](design-capability-growth-implementation-plan.md) (package VCS, search, browser, secrets, git/gh, judgment skills — see tools-and-skills dogfood checklist)
- Phase 0 / H-series designs (good history; implemented)

### Stale or misleading (cleanup candidates)

| Doc | Problem |
|-----|---------|
| [grok-improvement-plan/README.md](grok-improvement-plan/README.md) | Still says Stage B “code not started”; understates identity / glass / post-H6 work |
| [docs/README.md](README.md) | Reading order still Gemma-first; weak pointers to Grok plan + new designs |
| Root [README.md](../README.md) | Dual Gemma/Grok story without “gi is current product tip” |
| Older freeze designs | Gemma sampling, thrash, continuous, post-skill commit — historical “why,” not “what to do next” |

### Archive / essay

- [archive/](archive/) — Stretch 2 / memory research (not freeze)
- [memory-atoms.pdf](memory-atoms.pdf) — Phase 3 thesis, not an implementation plan

**Conflict rule for this pass:** Prefer **code on `grok-improvement`** over stale status prose in plan READMEs.

---

## 5. Multi-user glass vs memory (current precondition story)

Session user today:

- **Does:** tag who is typing; default Identity selection; actor labels; waits/speak user_id  
- **Does not:** switch or filter the chat transcript  

| Capability | Ready? |
|------------|--------|
| Multiple user digests | Yes |
| `user_id` on messages / moments / goals provenance | Yes / mostly |
| Session “who is typing” | Yes |
| Chat filter by user | **No** |
| Per-user orient history | **No** |
| Memory per user | **No** |

**Implication:** A first “glass per user” can be a **channel lens** (filter/thread on tags) without full Phase 3 memory. Real **relationship continuity** (“what I know with Jim vs Sam”) wants memory design so isolation does not fight continuous work and a shared ledger. See also the multi-user section of the identity design.

---

## 6. Small cleanup pass — before Grok Build + memory integration

Intent: **cheap clarity**, not a new phase. Do these (or a subset) so integration work starts from honest docs and calm glass.

| # | Item | Why |
|---|------|-----|
| 1 | **Refresh status docs** — `docs/README.md`, `grok-improvement-plan/README.md`, light root README banner | Kill “Stage B not started” / Gemma-only reading order |
| 2 | **Link this status pass** from docs README | One entry point for “where are we?” |
| 3 | **Dogfood checklist skim** — answer-speak on glass, identity promote/grant, gold UI, sandbox create-tool still honest | Confidence before heavier instruments |
| 4 | **Note open product choices** (below) without implementing | Avoid accidental scope in memory / grok_build designs |
| 5 | **Optional:** `gi` → `main` promote when operator is ready | Separate from Build/memory |

Out of this small pass:

- Per-user glass implementation  
- Hard answer-speak HOST  
- Phase 1 `grok_build`  
- Phase 3 memory schema  

---

## 7. Chat seed — development workflow as base capability vs larger self-improvement

This section is **not a design freeze**. It is the agenda for a deliberate conversation before we wire Grok Build and memory.

### 7.1 What “base development workflow” might mean

Today Elyra can already:

- Load **skills** (how) and call **tools** (do)  
- Use **goals/tasks** (what)  
- Grow via **create-tool / create-skill** (fail-closed, verify/promote)  
- **Draft/promote identity** (who)  

A **base development workflow** would be the *default way she does software work on herself and the repo* without a human having to reinvent the loop every time:

1. **Notice** a gap (failure, missing capability, user ask, ledger item)  
2. **Orient** (why-now, goals, skills, self/users)  
3. **Plan** (goal/tasks with acceptance; maybe provenance “for Jim”)  
4. **Change** (tools: edit, run, tests — later: stronger coding instrument)  
5. **Verify** (tests, dogfood gates, review-work)  
6. **Promote or rollback** (tool promote; later: branch/worktree/restart)  
7. **Leave a note** future-self can use (moment tape now; memory later)  

That workflow should feel like a **skill + ledger pattern**, not a second mind. Soft Decide (Stage B) already points at “one stage skill, tools over speculation, honest rest.”

### 7.2 Beyond tools and skills — what “larger self-improvement” mixes in

| Layer | Base today | Larger self-improvement later |
|-------|------------|-------------------------------|
| **Tools / skills** | create / load / verify / promote | Same, but invoked by a durable “improve Elyra” habit |
| **Goals / tasks** | Human or model opens work | Long-lived improvement goals; acceptance = tests green + dogfood |
| **Code / runtime** | Sandbox + host tools (limited) | `grok_build` / worktree / controlled restart (Phase 1–2) |
| **Identity** | Draft/promote self | Careful charter evolution under hard gates |
| **Memory** | Moment tapes | Preferential recall of past failures, decisions, user preferences |
| **MC** | Soft bias + skill text | Optional Stage C package only if process body must be inspectable |

**Person / instrument separation (Grok plan):** Elyra remains the durable person (identity, goals, moments). Grok Build (and similar) is a **high-capability instrument** she can call — not a replacement for presence or glass law.

### 7.3 Questions to settle in the upcoming chat

1. **What is the minimum “dev loop” skill set** shipped as base?  
   (e.g. plan-work + do-work + review-work only, vs a dedicated `improve-self` / `dev-loop` skill.)

2. **How tightly should goals bind development work?**  
   Every code change under a goal with acceptance? Or allow small opportunistic tool fixes?

3. **Where does Grok Build sit in the loop?**  
   One tool among many after verify gates? Only under an open “self-improve” goal? Never mid-social chat without grant?

4. **What must never be self-modifiable without hard host law?**  
   Speak→glass, usage ceilings, promote gates, path jail — already hybrid MC doctrine.

5. **How does memory change the loop?**  
   “Remember last thrash lesson” vs “remember Jim prefers small PRs” — different stores, same workflow shape?

6. **Multi-user and self-improve**  
   Improvement work for the shared person vs work *for* a specific user (provenance already exists; isolation does not).

7. **Promote path to production**  
   When does `grok-improvement` become `main`, and how does that relate to Elyra’s own promote metaphor?

### 7.4 Provisional stance (for discussion, not locked)

- **Base workflow** = skills + ledger + fail-closed growth tools + honest rest; teachable without memory.  
- **Larger self-improvement** = same loop + stronger instruments (Phase 1–2) + memory as peer substrate (Phase 3), still under hard channel laws.  
- **Do not** invent an MC package just to host the workflow; Stage B soft path + playbooks may be enough until self-mod or memory forces inspectable process bodies (Stage C).

---

## 8. Suggested near-term order

1. Small cleanup pass (§6) — especially **doc status truth**  
2. Operator chat on **dev workflow + self-improvement** (§7) → short design note if needed  
3. Then design/integration work for **Grok Build (Phase 1)** and/or **memory (Phase 3)** without fighting stale “Stage B not started” docs or ambiguous glass multi-user expectations  

---

## 9. References

| Doc | Role |
|-----|------|
| [stretch-1.md](stretch-1.md) | Stretch 1 runtime contract |
| [overview.md](overview.md) | Glossary / big picture |
| [grok-improvement-plan/README.md](grok-improvement-plan/README.md) | Grok phases (refresh status) |
| [metacognition.md](grok-improvement-plan/metacognition.md) / [stage-b-mc.md](grok-improvement-plan/stage-b-mc.md) | Soft Decide / Stage B |
| [design-identity-self-other-multi-user.md](design-identity-self-other-multi-user.md) | Identity + multi-user prep |
| [design-glass-aurimago-gold-polish.md](design-glass-aurimago-gold-polish.md) | Glass gold polish |
| [memory-atoms.pdf](memory-atoms.pdf) | Phase 3 memory thesis |
| [engineering-principles.md](engineering-principles.md) | How we write code |

---

## 10. Bottom line

| Question | Answer |
|----------|--------|
| Is Stretch 1 done? | **Yes** |
| Is Grok path + sandbox fitness on gi? | **Yes** |
| Is Stage B MC in code? | **Yes** (some plan READMEs lag) |
| Identity multi-user “real”? | Digests + tools **yes**; per-user chat **no** |
| Memory started? | **No** — essay only |
| Next small work? | Doc truth + calm dogfood + **workflow/self-improve conversation** |
| Next large work? | Grok Build and/or memory — after that conversation |

Elyra is a **working teammate runtime on Grok**, with soft metacognition and a real identity package, on a branch ahead of `main`. Before wiring stronger self-coding and memory, we should tidy status docs and agree how the **base development workflow** sits next to **larger self-improvement** without inventing a second mind.
