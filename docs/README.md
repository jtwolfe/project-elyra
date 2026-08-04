# Project Elyra — documentation

Communal digital teammate. Thin **do-loop** harness (**Stretch 1 shipped**), deeper **memory** later (Stretch 2).  
Grok Build for loop/tools/skills ideas — not as the product skin.

## Four-class hub

Documents are organised by **class** (audience + role), not chronology. Prefer **code on `working`** over stale prose.

| Class | Audience | For | Index |
|-------|----------|-----|-------|
| **STATE** | Operators / users | As-implemented behaviour, run/deploy, honest limits | [state/README.md](state/README.md) |
| **GOAL** | Product direction | North stars, phase goals, v0.1 claim — *what* / *why* | [goal/README.md](goal/README.md) (stub → current paths) |
| **DESIGN / PLAN** | Implementers | Designs, PR plans, freezes (history kept) | [design/README.md](design/README.md) |
| **DEV** | Jamie + Grok Build | Engineering principles, branch-law, pins, governance | [dev/README.md](dev/README.md) |
| **Archive / investigations** | Archaeology | Superseded freezes, sealed bags | [archive/](archive/) · islands below |

**Taxonomy status:** partial until engineering-principles docs rules land (**PR6** of [#121](https://github.com/jtwolfe/project-elyra/issues/121)).  
**Design of this reorg:** [design/docs-reorg-taxonomy.md](design/docs-reorg-taxonomy.md) (hub-first; phased `git mv`).  
**DEV** process law lives under [dev/](dev/). **DESIGN** under [design/](design/) topic folders (PR2 + PR2b/PR2c). **STATE** living ops under [state/](state/) (PR4) — architecture map + runtime/memory/ops moves.

**Conflict rule:** code on `working` > STATE > GOAL prose. DESIGN freezes are archaeology unless Status is Active. DEV wins for tip/branch/pin law ([dev/branch-law.md](dev/branch-law.md)).

---

## STATE — living product behaviour

Index: **[state/README.md](state/README.md)**.

| Doc | Role |
|-----|------|
| Root [README.md](../README.md) | Best operator entry — vision, harness, run, honest limits |
| [state/architecture.md](state/architecture.md) | **As-implemented map** — process topology, `elyra/*` packages, data layout |
| [state/stretch-1.md](state/stretch-1.md) | **Runtime contract** — how Stretch 1 runs + done-when (still law) |
| [state/overview.md](state/overview.md) | Big picture, glossary, Stretch 1 vs 2 |
| [state/tools-and-skills.md](state/tools-and-skills.md) | Packages, base catalog, VCS, search/browser/secrets, dogfood checklist |
| [state/time-and-identity.md](state/time-and-identity.md) | Self ≠ user, draft/promote, work-origin USER, time layers |
| [state/known-bugs.md](state/known-bugs.md) | Deferred product bugs / dogfood backlog |
| [state/grok-build-dogfood.md](state/grok-build-dogfood.md) | Operator checklist for current Grok Build instrument |
| [state/usage-and-pacing.md](state/usage-and-pacing.md) | Operator notes: SuperGrok pool vs ledger, burst, override |
| [state/sandbox-fitness-checklist.md](state/sandbox-fitness-checklist.md) | Short operator isolation / create-tool smoke (H6 extract) |
| [state/memory/README.md](state/memory/README.md) | Memory phase honesty — Phase 1 done; 2/2a code done (dogfood pending); Phase 3 experimental |
| [state/memory/architecture/](state/memory/architecture/) | As-implemented temporal / semantic / directed-traversal manuals |
| [state/radeon-vii/README.md](state/radeon-vii/README.md) | Radeon VII / ROCm dogfood start path (+ NOTES / VENV / STACK) |

### Run

```bash
./scripts/setup_venv.sh && source .venv/bin/activate
pip install -e '.[sandbox]'   # optional but needed for guest isolation (default ON)
./scripts/setup-microsandbox.sh --doctor-only

# Optional capability-growth extras (fail closed if missing):
#   pip install -e '.[search]'            # web_search (ddgs)
#   pip install -e '.[browser]'           # Playwright browser_* tools
#   playwright install chromium           # after browser extra
#   pip install -e '.[search,browser]'    # both

elyra start              # API + UI (xAI Grok product default)
elyra start --stub-llm   # stub LLM + UI (hermetic)
# hermetic host-stub: ELYRA_SANDBOX=0
# http://127.0.0.1:8787/
```

**New terminal (everyday):** `cd` to repo → `source .venv/bin/activate` → `elyra start` → UI `http://127.0.0.1:8787/`.

**Radeon VII / LuxPrimata ROCm dogfood** (Tensile inject, `embed_device=rocm`, post-torch recovery): [state/radeon-vii/README.md](state/radeon-vii/README.md) § *New terminal session — start Elyra*.

Sandbox fitness (full H1–H6 design+plan — DESIGN class): [design/capability/harness-sandbox-fitness.md](design/capability/harness-sandbox-fitness.md).  
Tools/skills catalog + dogfood checklist: [state/tools-and-skills.md](state/tools-and-skills.md). Sandbox smoke: [state/sandbox-fitness-checklist.md](state/sandbox-fitness-checklist.md).

### Tests

```bash
pytest -m 'not llm'   # default pack
pytest -m llm         # real Gemma (needs model/ + GPU)

# Live qualitative stage gates (3-attempt protocol; needs model + GPU)
python scripts/live_eval/run_stage.py --stage 0 --all-scenarios --tries 3
```

Done-when map: root [README.md](../README.md) testing section and `tests/test_stretch1_donewhen.py`.  
Live protocol (historical Gemma stages — freeze): [live-eval.md](live-eval.md). Ship knobs freeze: [inference.md](inference.md).

---

## GOAL — product direction (short north stars)

| Doc | Role |
|-----|------|
| [promotion-discussion/README.md](promotion-discussion/README.md) | **v0.1 promotion / gym** — pillars, meal/context, Phase 3 defer (long form; short claim lands in PR3) |
| [state/memory/README.md](state/memory/README.md) | Stretch 2 phase goals + status tables (also STATE honesty) |
| [stretch-2/philosophical-soft-guidance.md](stretch-2/philosophical-soft-guidance.md) | Soft conceptual influences (explicit non-deliverable; GOAL move later) |
| [grok-improvement-plan/README.md](grok-improvement-plan/README.md) | Grok migration phase map (Phase 0–3); refresh if it lags code |
| [memory-atoms.pdf](memory-atoms.pdf) | Philosophy reference (GOAL + STATE) |

GOAL stays **short** when new pages are added (PR3); full designs stay under DESIGN.

---

## DESIGN / PLAN — implementers

Full status-indexed catalog: **[design/README.md](design/README.md)**.

| Cluster | Start | Notes |
|---------|-------|-------|
| **Reorg (#121)** | [design/docs-reorg-taxonomy.md](design/docs-reorg-taxonomy.md) | This taxonomy; Active |
| Stretch 1 stack | [design/stretch-1/](design/stretch-1/) | Shipped; + continuous-work, post-skill, thrash, remove-gemma |
| Capability growth | [design/capability/](design/capability/) | Product design + implementation plan |
| Grok Build | [design/grok-build/](design/grok-build/) | Host instrument (+ summary, functionalization, headless spike) |
| Identity / glass | [design/identity/](design/identity/) · [design/glass/](design/glass/) | Multi-user prep; multimodal next stack |
| Usage / OAuth | [design/usage/](design/usage/) | Pacing design; browser OIDC |
| Board | [design/board/](design/board/) | v0.1-ready board ops |
| Embed | [design/embed/](design/embed/) | Async EncodeWorker (residuals open) |
| Stretch 2 memory | [design/memory/](design/memory/) | Phase designs + meal + spikes (PR2b); arch manuals under [state/memory/architecture/](state/memory/architecture/) (PR4) |
| GI phases | [design/grok-improvement-plan/](design/grok-improvement-plan/) | phase-0*, stage-b-mc, metacognition (PR2c) |
| Harness / sandbox | [design/capability/harness-sandbox-fitness.md](design/capability/harness-sandbox-fitness.md) | Full H1–H6 design+plan (PR2c; Shipped) |
| ROCm design | [design/memory/design-rocm-venv-gpu-embed-smoke.md](design/memory/design-rocm-venv-gpu-embed-smoke.md) | ROCm smoke design (PR2c) |

**Superseded freeze (do not follow for setup):** [design/stretch-1/design-gemma-sampling-hygiene-staged.md](design/stretch-1/design-gemma-sampling-hygiene-staged.md).

---

## DEV — how we work

Index: **[dev/README.md](dev/README.md)**.

| Doc | Role |
|-----|------|
| [dev/engineering-principles.md](dev/engineering-principles.md) | How we write code — modules, tests, config, dogfood |
| [dev/branch-law.md](dev/branch-law.md) | **Normative tip law** — `working` integration tip; promote → `main`; pins; tags |
| [dev/operating-pins.md](dev/operating-pins.md) | Manual operating pin convention (C3) |
| [dev/development-governance.md](dev/development-governance.md) | Multi-party governance + operating pin ladder |
| [dev/known-bugs-BRANCHES.md](dev/known-bugs-BRANCHES.md) | Historical fix-branch map (archive-leaning; not tip law) |

---

## Archive / investigations

| Path | Role |
|------|------|
| [archive/](archive/) | Early research notes (reflection-*); expand criteria in later PR |
| [project-status-pass.md](project-status-pass.md) | Stale status snapshot (archive-candidate; tip names lag) |
| [inference.md](inference.md) | **Freeze** — Gemma/llama setup; not product path |
| [live-eval.md](live-eval.md) | **Freeze** — live 3-attempt protocol (Gemma stages historical) |
| [lance-debug1/](lance-debug1/) | Sealed forensic investigation (product fix outside bag) |
| [stretch-2/meal-continuity-review/](stretch-2/meal-continuity-review/) | Meal continuity investigation package |
| [radeon-vii-dev/freezes/](radeon-vii-dev/freezes/) | HW / pip freezes (not operator start path) |

---

## Stance (short)

- **One mind**, continuous presence, single worker  
- **Moment = one do-loop** (tools until stop) — not one tool hop  
- **Skills = how, tools = do, goals = what, self/users = who**  
- **Self ≠ user** (separate stores)  
- **Voice = `speak` tool** (with transport feedback)  
- **No language debt** — no “organs” cast; skills/tools/host jobs only  
- **Dogfood** — created tools/skills use the same formats as builtins  
- **Memory graph later** — Stretch 1 only emits moments + linear tapes; Stretch 2 planning on historical `grok-improvement-memory`

## Stretch 1 vs Stretch 2

| Stretch 1 (shipped) | Stretch 2 (memory) |
|---------------------|--------------------|
| Presence, wake queue, do-loops | Atomized memory; moments as groups of atoms |
| Skills + tools + create-tool (fail-closed) | Rolling summary ladder; labeled context meal |
| Sliding context meal | Temporal + episodic + later semantic/procedural channels |
| Grok product path | LanceDB direction; Nemotron embed runtime (Phase 2) |

Start at [state/memory/README.md](state/memory/README.md). Runtime law: [state/stretch-1.md](state/stretch-1.md).

## Status

**Stretch 1 complete** on the foundation path. Integration tip is **`working`** ([dev/branch-law.md](dev/branch-law.md)); promote to **`main`** with full suite. Historical **`grok-improvement`** is **superseded** as tip law. **`main`** may lag the tip. **Stretch 2 memory** history may still name **`grok-improvement-memory`** — [state/memory/README.md](state/memory/README.md).

Docs taxonomy reorg: issue [#121](https://github.com/jtwolfe/project-elyra/issues/121) — hub + DEV + DESIGN + **STATE (PR4)** landed; archive/investigations PR5; principles § PR6.
