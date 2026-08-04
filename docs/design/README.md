# DESIGN / PLAN

Design documents and PR plans that implement goals. Living catalogue during [#121](https://github.com/jtwolfe/project-elyra/issues/121) reorg.

**Taxonomy:** [docs-reorg-taxonomy.md](docs-reorg-taxonomy.md) (Active) — hub-first, non-destructive.  
**Hub:** [docs/README.md](../README.md) four-class index.  
**Paths below are current** until PR2 / PR2b / PR2c `git mv`s files under `docs/design/<topic>/`. Do not treat this folder as the only DESIGN home yet.

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
| [docs-reorg-taxonomy.md](docs-reorg-taxonomy.md) | Active | Taxonomy reorg plan (#121). Hub-first; physical moves PR1–PR5; principles § PR6. |

---

## Root `docs/design-*.md` (flat — move in PR2)

| Doc | Status (assessed) | Topic (target) |
|-----|-------------------|----------------|
| [design-stretch-1-implementation.md](../design-stretch-1-implementation.md) | Shipped | stretch-1 |
| [design-continuous-work-orient-ledger-reset.md](../design-continuous-work-orient-ledger-reset.md) | Shipped (mostly) | stretch-1 |
| [design-post-skill-commitment.md](../design-post-skill-commitment.md) | Shipped / historical | stretch-1 |
| [design-tool-thrash-recovery.md](../design-tool-thrash-recovery.md) | Shipped / historical | stretch-1 |
| [design-gemma-sampling-hygiene-staged.md](../design-gemma-sampling-hygiene-staged.md) | **Superseded / Freeze** | stretch-1 |
| [design-remove-gemma-local-stub.md](../design-remove-gemma-local-stub.md) | Shipped | stretch-1 |
| [design-identity-self-other-multi-user.md](../design-identity-self-other-multi-user.md) | Shipped | identity |
| [design-glass-aurimago-gold-polish.md](../design-glass-aurimago-gold-polish.md) | Shipped | glass |
| [design-glass-multimodal-attachments.md](../design-glass-multimodal-attachments.md) | Active / next stack | glass |
| [design-capability-growth-search-browse-vcs-secrets.md](../design-capability-growth-search-browse-vcs-secrets.md) | Shipped | capability |
| [design-capability-growth-implementation-plan.md](../design-capability-growth-implementation-plan.md) | Shipped | capability |
| [design-capability-integrity-run-search-browser-sandbox.md](../design-capability-integrity-run-search-browser-sandbox.md) | Draft/shipped mix | capability |
| [design-guest-package-stage-reliability.md](../design-guest-package-stage-reliability.md) | Draft/shipped mix | capability |
| [design-usage-tracking-supergrok-pacing.md](../design-usage-tracking-supergrok-pacing.md) | Shipped-ish | usage |
| [design-xai-oauth-browser-login.md](../design-xai-oauth-browser-login.md) | Proposed / Active | usage |
| [design-grok-build-tool.md](../design-grok-build-tool.md) | Active / implemented stack | grok-build |
| [design-grok-build-tool-summary.md](../design-grok-build-tool-summary.md) | Active summary | grok-build |
| [design-grok-build-functionalization.md](../design-grok-build-functionalization.md) | Draft / Active | grok-build |
| [grok-build-headless-spike.md](../grok-build-headless-spike.md) | Spike | grok-build |
| [design-embed-async-encode-worker.md](../design-embed-async-encode-worker.md) | Implemented (residuals open) | embed |
| [design-v0.1-ready-board-recategorization.md](../design-v0.1-ready-board-recategorization.md) | Approved / board ops | board |

---

## stretch-2 designs + spikes (current paths — move in PR2b)

Architecture manuals under `stretch-2/architecture/phase-*.md` are **STATE** (not listed here). Spikes and `design-*` are DESIGN.  
Status assessed for catalog routing; formal banners land with PR2b moves.

| Doc | Status (assessed) | Notes |
|-----|-------------------|-------|
| [design-phase-1-temporal.md](../stretch-2/design-phase-1-temporal.md) | Shipped | Phase 1 design |
| [design-phase-1-implementation.md](../stretch-2/design-phase-1-implementation.md) | Shipped | Phase 1 PR plan |
| [design-phase-1-remaining-pr8-pr9.md](../stretch-2/design-phase-1-remaining-pr8-pr9.md) | Shipped | Phase 1 residual |
| [design-phase-2-semantic.md](../stretch-2/design-phase-2-semantic.md) | Shipped (code; dogfood pending) | Phase 2 design |
| [design-phase-2-implementation.md](../stretch-2/design-phase-2-implementation.md) | Shipped (code; dogfood pending) | Phase 2 PR plan |
| [design-phase-2-rectification.md](../stretch-2/design-phase-2-rectification.md) | Shipped | Phase 2 rectification |
| [design-phase-2a-directed-traversal.md](../stretch-2/design-phase-2a-directed-traversal.md) | Shipped (code; dogfood pending) | Phase 2a design |
| [design-phase-2a-implementation.md](../stretch-2/design-phase-2a-implementation.md) | Shipped (code; dogfood pending) | Phase 2a PR plan |
| [design-phase-3-procedural.md](../stretch-2/design-phase-3-procedural.md) | Draft / experimental | Phase 3 design |
| [design-context-meal-composition.md](../stretch-2/design-context-meal-composition.md) | Provisional / Active | Meal design only (not STATE) |
| [design-database-choices.md](../stretch-2/design-database-choices.md) | Shipped / planning baseline | Storage choices |
| [design-nemotron-runtime.md](../stretch-2/design-nemotron-runtime.md) | Shipped-ish | Embed runtime |
| [design-episodic-summary-ladder-llm.md](../stretch-2/design-episodic-summary-ladder-llm.md) | Active / design | Summary ladder |
| [design-instance-continuity-glass-tail-directed-keep.md](../stretch-2/design-instance-continuity-glass-tail-directed-keep.md) | Active | Continuity design |
| [design-instance-continuity-implement-plan.md](../stretch-2/design-instance-continuity-implement-plan.md) | Active | Continuity plan |
| [design-instance-continuity-product-implement.md](../stretch-2/design-instance-continuity-product-implement.md) | Active | Continuity product |
| [design-meal-formation-continuity-review-plan.md](../stretch-2/design-meal-formation-continuity-review-plan.md) | Active / plan | Meal review plan |
| [inspiration-activity-model-and-storage.md](../stretch-2/inspiration-activity-model-and-storage.md) | Planning baseline | DESIGN, not short GOAL |
| [architecture/spikes/lance-emb-migration.md](../stretch-2/architecture/spikes/lance-emb-migration.md) | Spike | Pre-ship spike |
| [architecture/spikes/nemotron-runtime.md](../stretch-2/architecture/spikes/nemotron-runtime.md) | Spike | Pre-ship spike |

---

## GI / harness / ROCm design (current paths — move in PR2c)

| Doc | Status | Notes |
|-----|--------|-------|
| [phase-0.md](../grok-improvement-plan/phase-0.md) | Freeze / DESIGN | GI Phase 0 |
| [phase-0-execution.md](../grok-improvement-plan/phase-0-execution.md) | Freeze / DESIGN | GI Phase 0 execution |
| [stage-b-mc.md](../grok-improvement-plan/stage-b-mc.md) | Shipped | Stage B soft MC |
| [metacognition.md](../grok-improvement-plan/metacognition.md) | Shipped | Metacognition notes |
| [harness-sandbox-fitness.md](../grok-improvement-plan/harness-sandbox-fitness.md) | Shipped | Full H1–H6 design+plan (STATE gets short checklist later) |
| [design-rocm-venv-gpu-embed-smoke.md](../radeon-vii-dev/design-rocm-venv-gpu-embed-smoke.md) | DESIGN | ROCm embed smoke design |

Operator-facing GI usage notes stay STATE: [usage-tracking-supergrok-pacing.md](../grok-improvement-plan/usage-tracking-supergrok-pacing.md).

---

## Investigation designs (sealed islands — PR5)

| Doc | Notes |
|-----|-------|
| [lance-debug1/](../lance-debug1/) | Sealed forensic package (designs + evidence) |
| [stretch-2/meal-continuity-review/](../stretch-2/meal-continuity-review/) | Meal continuity investigation |

See taxonomy design § inventory for full classification and move plan.
