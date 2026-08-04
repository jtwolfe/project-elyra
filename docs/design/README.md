# DESIGN / PLAN

Design documents and PR plans that implement goals. Status-indexed catalogue (four-class taxonomy [#121](https://github.com/jtwolfe/project-elyra/issues/121)).

**Taxonomy:** [docs-reorg-taxonomy.md](docs-reorg-taxonomy.md) (**Shipped**) — hub-first, non-destructive.  
**Hub:** [docs/README.md](../README.md) four-class index.  
**Layout:** root designs under `docs/design/<topic>/` (PR2). stretch-2 designs under `memory/` (PR2b). GI phase designs + harness + design-rocm under DESIGN (PR2c).

| Status | Meaning |
|--------|---------|
| **Active** | Normative for ongoing work |
| **Shipped** | Implemented; keep as contract / decision archaeology |
| **Superseded / Freeze** | Do not follow for new setup; body kept |
| **Draft** | Not yet approved for execution |
| **Spike** | Exploration; archive-leaning |

---

## This reorg

| Doc | Status | Notes |
|-----|--------|-------|
| [docs-reorg-taxonomy.md](docs-reorg-taxonomy.md) | **Shipped** | Taxonomy reorg (#121) complete: hub + DEV + DESIGN + STATE + archive/investigations + principles [§10](../dev/engineering-principles.md) + link sweep. |

---

## stretch-1/

| Doc | Status | Notes |
|-----|--------|-------|
| [design-stretch-1-implementation.md](stretch-1/design-stretch-1-implementation.md) | Shipped | Stretch 1 design + PR plan |
| [design-continuous-work-orient-ledger-reset.md](stretch-1/design-continuous-work-orient-ledger-reset.md) | Shipped (mostly) | Continuous work + orient/ledger + reset |
| [design-post-skill-commitment.md](stretch-1/design-post-skill-commitment.md) | Shipped / historical | Post-skill commitment |
| [design-tool-thrash-recovery.md](stretch-1/design-tool-thrash-recovery.md) | Shipped / historical | Tool thrash recovery |
| [design-gemma-sampling-hygiene-staged.md](stretch-1/design-gemma-sampling-hygiene-staged.md) | **Superseded / Freeze** | Do not follow for setup; superseded by remove-gemma |
| [design-remove-gemma-local-stub.md](stretch-1/design-remove-gemma-local-stub.md) | Shipped | Remove llama.cpp/Gemma path; stub `provider=local` |

---

## identity/

| Doc | Status | Notes |
|-----|--------|-------|
| [design-identity-self-other-multi-user.md](identity/design-identity-self-other-multi-user.md) | Shipped | Identity + multi-user prep |

---

## glass/

| Doc | Status | Notes |
|-----|--------|-------|
| [design-glass-aurimago-gold-polish.md](glass/design-glass-aurimago-gold-polish.md) | Shipped | Glass gold theme polish |
| [design-glass-multimodal-attachments.md](glass/design-glass-multimodal-attachments.md) | Active / next stack | STT/TTS, durable attachments, Grok vision |

---

## capability/

| Doc | Status | Notes |
|-----|--------|-------|
| [design-capability-growth-search-browse-vcs-secrets.md](capability/design-capability-growth-search-browse-vcs-secrets.md) | Shipped | Product design (search, browse, VCS, secrets) |
| [design-capability-growth-implementation-plan.md](capability/design-capability-growth-implementation-plan.md) | Shipped | Execute-plan PR DAG + promote algorithm |
| [design-capability-integrity-run-search-browser-sandbox.md](capability/design-capability-integrity-run-search-browser-sandbox.md) | Draft/shipped mix | Integrity stack |
| [design-guest-package-stage-reliability.md](capability/design-guest-package-stage-reliability.md) | Draft/shipped mix | Guest package stage-once gate |
| [harness-sandbox-fitness.md](capability/harness-sandbox-fitness.md) | **Shipped** | Full H1–H6 design+plan (KD14; short STATE checklist: [sandbox-fitness-checklist.md](../state/sandbox-fitness-checklist.md)) |

---

## grok-build/

| Doc | Status | Notes |
|-----|--------|-------|
| [design-grok-build-tool.md](grok-build/design-grok-build-tool.md) | Active / implemented stack | Host instrument design |
| [design-grok-build-tool-summary.md](grok-build/design-grok-build-tool-summary.md) | Active summary | Short summary of full design |
| [design-grok-build-functionalization.md](grok-build/design-grok-build-functionalization.md) | Draft / Active | Auth seed + zombie/finalize honesty |
| [grok-build-headless-spike.md](grok-build/grok-build-headless-spike.md) | Spike | deep_research / human-gate spike (D7) |

---

## usage/

| Doc | Status | Notes |
|-----|--------|-------|
| [design-usage-tracking-supergrok-pacing.md](usage/design-usage-tracking-supergrok-pacing.md) | Shipped-ish | Usage ledger + SuperGrok pacing |
| [design-xai-oauth-browser-login.md](usage/design-xai-oauth-browser-login.md) | Proposed / Active | In-browser xAI OIDC login |

---

## board/

| Doc | Status | Notes |
|-----|--------|-------|
| [design-v0.1-ready-board-recategorization.md](board/design-v0.1-ready-board-recategorization.md) | Approved / board ops | v0.1-ready board recategorization |

---

## embed/

| Doc | Status | Notes |
|-----|--------|-------|
| [design-embed-async-encode-worker.md](embed/design-embed-async-encode-worker.md) | Implemented (residuals open) | EncodeWorker + EmbedderGate continuous encode |

---

## memory/

Stretch-2 memory designs (PR2b / KD4). Architecture manuals: [docs/state/memory/architecture/](../state/memory/architecture/) (PR4 STATE).

| Doc | Status | Notes |
|-----|--------|-------|
| [design-phase-1-temporal.md](memory/design-phase-1-temporal.md) | Shipped | Phase 1 design |
| [design-phase-1-implementation.md](memory/design-phase-1-implementation.md) | Shipped | Phase 1 PR plan |
| [design-phase-1-remaining-pr8-pr9.md](memory/design-phase-1-remaining-pr8-pr9.md) | Shipped | Phase 1 residual |
| [design-phase-2-semantic.md](memory/design-phase-2-semantic.md) | Shipped (code; dogfood pending) | Phase 2 design |
| [design-phase-2-implementation.md](memory/design-phase-2-implementation.md) | Shipped (code; dogfood pending) | Phase 2 PR plan |
| [design-phase-2-rectification.md](memory/design-phase-2-rectification.md) | Shipped | Phase 2 rectification |
| [design-mm-embed-buildout.md](memory/design-mm-embed-buildout.md) | **Shipped (code; dogfood pending)** | Multimodal semantic loop complete in code (PR0–PR7 #124); ready for `working` merge; operator checklist [mm-embed-dogfood.md](../state/memory/mm-embed-dogfood.md); **not** Gate B default-on |
| [design-phase-2a-directed-traversal.md](memory/design-phase-2a-directed-traversal.md) | Shipped (code; dogfood pending) | Phase 2a design |
| [design-phase-2a-implementation.md](memory/design-phase-2a-implementation.md) | Shipped (code; dogfood pending) | Phase 2a PR plan |
| [design-phase-3-procedural.md](memory/design-phase-3-procedural.md) | Draft / experimental | Phase 3 design |
| [design-context-meal-composition.md](memory/design-context-meal-composition.md) | Provisional / Active | Meal design only (not STATE; KD14) |
| [design-database-choices.md](memory/design-database-choices.md) | Shipped / planning baseline | Storage choices |
| [design-nemotron-runtime.md](memory/design-nemotron-runtime.md) | Shipped-ish | Embed runtime |
| [design-episodic-summary-ladder-llm.md](memory/design-episodic-summary-ladder-llm.md) | Active / design | Summary ladder |
| [design-instance-continuity-glass-tail-directed-keep.md](memory/design-instance-continuity-glass-tail-directed-keep.md) | Active | Continuity design |
| [design-instance-continuity-implement-plan.md](memory/design-instance-continuity-implement-plan.md) | Active | Continuity plan |
| [design-instance-continuity-product-implement.md](memory/design-instance-continuity-product-implement.md) | Active | Continuity product |
| [design-meal-formation-continuity-review-plan.md](memory/design-meal-formation-continuity-review-plan.md) | Active / plan | Meal review plan |
| [inspiration-activity-model-and-storage.md](memory/inspiration-activity-model-and-storage.md) | Planning baseline | DESIGN, not short GOAL |
| [spikes/lance-emb-migration.md](memory/spikes/lance-emb-migration.md) | Spike | Pre-ship spike |
| [spikes/nemotron-runtime.md](memory/spikes/nemotron-runtime.md) | Spike | Pre-ship spike |

---

## grok-improvement-plan/ (phase designs — PR2c)

GI README (short phase map / history) stays under [../grok-improvement-plan/README.md](../grok-improvement-plan/README.md). Operator usage notes: [usage-and-pacing.md](../state/usage-and-pacing.md).

| Doc | Status | Notes |
|-----|--------|-------|
| [phase-0.md](grok-improvement-plan/phase-0.md) | Freeze / Shipped | GI Phase 0 concept + success criteria |
| [phase-0-execution.md](grok-improvement-plan/phase-0-execution.md) | Freeze / Shipped | GI Phase 0 execution + live smoke checklist |
| [stage-b-mc.md](grok-improvement-plan/stage-b-mc.md) | Shipped | Stage B soft MC implementation plan |
| [metacognition.md](grok-improvement-plan/metacognition.md) | Shipped | MC geometry + hybrid soft Decide |

---

## memory/ (ROCm design — PR2c; stretch-2 designs → PR2b)

| Doc | Status | Notes |
|-----|--------|-------|
| [design-rocm-venv-gpu-embed-smoke.md](memory/design-rocm-venv-gpu-embed-smoke.md) | Shipped (standalone) | ROCm venv + Nemotron encode smoke design |

---

## Investigation designs (sealed islands — PR5)

| Doc | Notes |
|-----|-------|
| [investigations/lance-debug1/](../investigations/lance-debug1/) | Sealed forensic package (designs + evidence) |
| [investigations/meal-continuity-review/](../investigations/meal-continuity-review/) | Meal continuity investigation |

See taxonomy design § inventory for full classification and move plan.
