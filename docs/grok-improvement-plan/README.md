# Grok Improvement Plan

> **Integration tip migration (2026-08-01):** The house integration tip is now **`working`**, which **supersedes `grok-improvement`** as current tip law. Prefer feature / execute-plan branches and PR bases off **`working`**; promote `working` → `main` with full suite. Normative detail: **[docs/branch-law.md](../branch-law.md)**. Historical “Branch / Integration branch: `grok-improvement`” prose in this folder remains Phase 0 context only — do not treat it as the live base.

This folder holds the design documentation for migrating Project Elyra from local Gemma (llama.cpp) onto xAI Grok models under a SuperGrok Heavy subscription, and for enabling safe self-improvement via Grok Build.

## Guiding principles

1. **Do-loop and presence first.** Stretch 1 (presence, moments, goals, identity, fail-closed growth tools) is already solid. We do not rebuild memory or continuous-work before the model path and usage controls are correct.
2. **Correctness over scope.** Every change must be small, testable, and reversible. Prefer configuration and thin adapters over deep rewrites.
3. **Subscription protection.** Elyra must never consume the full SuperGrok weekly quota under normal operation. A hierarchical usage meter with hard stops is a Phase 0 requirement.
4. **Person / Instrument separation.** Elyra remains the durable person (identity, goals, moments). Grok Build is the high-capability coding instrument that Elyra can call later. Phase 0 only prepares the model path and budgets; the `grok_build` tool itself is Phase 1+.
5. **Documentation before code.** Designs live here before implementation begins on the `grok-improvement` branch.
6. **Glue, not ceremony.** Metacognition (MC) is the light path that keeps goals, tasks, skills, tools (and later memory) coherent under continuous work. It is not a second mind. Name it early; give it **shallow shape (Stage B)** after the Grok path is stable; **optional package (Stage C)** only if self-mod or Memory dual force it.
7. **Hybrid Decide.** Soft Decide (bias, orient cadence, handover vocabulary) is the MC surface. Hard host policies (speak→glass, skill-commit, thrash, continuous gates, usage) stay where they are — do not consolidate enforcement into an MC god module. See [metacognition.md](metacognition.md) §3 and [docs/engineering-principles.md](../engineering-principles.md).

## Folder contents

| Document | Purpose |
|----------|---------|
| [phase-0.md](phase-0.md) | Phase 0 concept + detailed implementation plan (provider, usage meter, defaults, Web UI status contract, success criteria) |
| [phase-0-execution.md](phase-0-execution.md) | Phase 0 execution design + operator live smoke checklist |
| [usage-tracking-supergrok-pacing.md](usage-tracking-supergrok-pacing.md) | **Operator notes:** two meters (SuperGrok pool vs Elyra ledger `S`), first-period adoption, burst remaining, override, one-supervisor constraint + **dogfood checklist** |
| [harness-sandbox-fitness.md](harness-sandbox-fitness.md) | **H1–H6** post–Phase 0 harness / warm microsandbox fitness (design + PR plan + **operator create-tool live smoke checklist** §H6) |
| [metacognition.md](metacognition.md) | MC geometry, **hybrid soft Decide / hard policies**, dual with Memory, Stage A→B→C, handover gates, super-future thin-interpreter arc |
| [stage-b-mc.md](stage-b-mc.md) | **Stage B / MC-beta implementation plan** for Grok Build (goal, non-goals, file targets, tests, glass dogfood, what to leave alone) |

Full design (implementation spec, not operator short-form): [`docs/design-usage-tracking-supergrok-pacing.md`](../design-usage-tracking-supergrok-pacing.md).

Phase 3 reference essay (PDF in-repo): [`docs/memory-atoms.pdf`](../memory-atoms.pdf) — *What is wrong with my memory?* (atomized experience / hypergraph thesis). Optional markdown twin `docs/memory-atoms.md` may be added later. Not required to implement Stage B.

## Phase overview

| Phase | Focus |
|-------|--------|
| **Phase 0** | xAI / Grok provider path (**default ON**), credentials **primarily Grok Build `auth.json`**, default model **Grok 4.5 Fast**, hierarchical usage meter (50% weekly / day / 1-hour hard stops), light prompt fitness (done), continuous/auto **default OFF**, Web UI status + model/credential controls. **No MC implementation.** |
| **After Phase 0 stable** | **Stage B MC (MC-beta):** ledger-aware soft bias + short Decide cadence + status/answer-speak vocabulary. Still **no** new subsystem / package. Plan: [stage-b-mc.md](stage-b-mc.md). |
| **Phase 1** | `grok_build` tool + self-improvement goal scaffolding. MC package is **not** required; Stage B should preferably land first so glass handoffs are coherent under a stronger instrument. |
| **Phase 2** | Self-modification continuity (worktree, verify, promote, controlled restart / resume). |
| **Phase 3** | Atomized memory substrate (memory-atom / hypergraph model) as equal peer to MC. Soft Decide patterns become absorbable structure; channel laws stay outer. |

Later (unscoped): remote Glass (Vercel + auth), TTS/STT voice.

### Stage C (optional; not a numbered Grok phase)

MC **package** form only if self-mod needs an inspectable process body or Memory needs a durable process peer. **Not** “first real MC” — Stage B is the first behavioral MC. See [metacognition.md](metacognition.md).

## Operator target after Phase 0

```text
elyra start
```

→ provider **xai** (default), model **Grok 4.5 Fast** (selectable), credential source **Grok Build session** (optional API key paste + select later), continuous **OFF**, usage meter ON, Web UI shows provider / model / credential source / usage remaining / hard-stop / continuous. Local path remains available via override.

## Branch

> **Superseded tip:** The live integration tip is **`working`** — see [docs/branch-law.md](../branch-law.md). Table below is **historical** Phase 0 / GI-era process.

**Historical integration branch:** `grok-improvement` (created from `main`; **superseded by `working`**).

| Rule | Detail |
|------|--------|
| Work branches | *(historical)* Phase 0 / Stage B PR branches sat on top of `grok-improvement` — **now base on `working`** |
| PR base | Open new PRs against **`working`**, not `main` (and not `grok-improvement`) |
| Push + merge | Merge short-lived branches onto **`working`** when the work lands |
| Promote to main | Promote **`working` → `main`** after full suite + noise cleanup — not automatic with individual PRs |

Execution detail (Phase 0 PR stack): [phase-0-execution.md](phase-0-execution.md).  
Stage B execution: [stage-b-mc.md](stage-b-mc.md).

## Status

- **Phase 0**: **Implementation complete** on `grok-improvement` (provider path, credentials, usage meter + hard-stop override, supervisor/CLI defaults, status API, Web UI). Concept + success criteria in [phase-0.md](phase-0.md); execution design + **live smoke checklist** in [phase-0-execution.md](phase-0-execution.md). Prompt fitness applied. **Live smoke against xAI is operator-run** — operator reports roughly green; promote `grok-improvement` → `main` remains a separate step when fully signed off.
- **Usage tracking + SuperGrok pacing**: evolves Phase 0 meter (week ledger + pace/burst + SuperGrok pool poll; hard-stop override kept). Operator short-form + **dogfood checklist**: [usage-tracking-supergrok-pacing.md](usage-tracking-supergrok-pacing.md). Full design: [design-usage-tracking-supergrok-pacing.md](../design-usage-tracking-supergrok-pacing.md).
- **H1–H6 Harness / Sandbox Fitness**: **Implementation complete** on `grok-improvement`. Design + **operator create-tool live smoke checklist** in [harness-sandbox-fitness.md](harness-sandbox-fitness.md) §H6. Operator reports tool creation working. Continuous remains default **OFF**. Prefer H-series confidence before or alongside Stage B live dogfood.
- **Metacognition / Stage B**: **Concept + hybrid ontology + Stage B implementation plan complete** in-repo ([metacognition.md](metacognition.md), [stage-b-mc.md](stage-b-mc.md)). **Code not started.** Next: Grok Build executes Stage B (ledger-aware bias, orient Decide cadence, answer-speak soft path). Nickname **MC-beta** = Stage B only. Stage C package remains optional and is **not** the default next implementation.
- **Memory essay**: Operator may place *What is wrong with my memory?* at `docs/memory-atoms.md` as Phase 3 design reference; not a Stage B blocker.
