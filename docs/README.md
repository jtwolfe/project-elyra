# Project Elyra — documentation

Communal digital teammate. Thin **do-loop** harness (**Stretch 1 shipped**), deeper **memory** later (Stretch 2).  
Grok Build for loop/tools/skills ideas — not as the product skin.

## Read in this order

| # | Doc | Role |
|---|-----|------|
| 0 | **[project-status-pass.md](project-status-pass.md)** | **Where we are now** — shipped vs not, doc drift, small prep before Grok Build / memory, self-improve chat seed |
| 1 | **[stretch-1.md](stretch-1.md)** | **Runtime contract** — how Stretch 1 runs + done-when |
| 2 | **[design-stretch-1-implementation.md](design-stretch-1-implementation.md)** | **Implementation design + PR plan** (historical; Stretch 1 shipped) |
| 3 | **[engineering-principles.md](engineering-principles.md)** | **How we write code** — modules, tests, config, dogfood |
| 4 | [overview.md](overview.md) | Big picture, glossary, Stretch 1 vs 2 |
| 5 | [tools-and-skills.md](tools-and-skills.md) | Packages, base catalog, package VCS, search/browser/secrets/git·gh, dogfood checklist, create-tool safety |
| 5a | [design-capability-growth-search-browse-vcs-secrets.md](design-capability-growth-search-browse-vcs-secrets.md) | **Capability growth product design** (search, browse, package VCS, secrets, workflow skills) |
| 5b | [design-capability-growth-implementation-plan.md](design-capability-growth-implementation-plan.md) | **Capability growth execute-plan** (PR DAG, promote algorithm, acceptance) |
| 6 | [time-and-identity.md](time-and-identity.md) | Self ≠ user, draft/promote, work-origin USER, time layers |
| 7 | [inference.md](inference.md) | llama.cpp / Vulkan / Gemma; `-c` vs sliding ~24k; **ship knobs** (temp 0.6, top_p/k, budget, hygiene, RC re-feed, hop-0 speak pin) |
| 8 | [live-eval.md](live-eval.md) | Live 3-attempt qualitative protocol; how to run `scripts/live_eval`; A/B failure modes; continuous `S-cont-*` |
| 9 | [grok-improvement-plan/README.md](grok-improvement-plan/README.md) | Grok migration phases (Phase 0–3); **refresh status if it lags code** |
| 9a | [grok-improvement-plan/usage-tracking-supergrok-pacing.md](grok-improvement-plan/usage-tracking-supergrok-pacing.md) | **Operator notes:** SuperGrok pool vs Elyra ledger, burst, override, dogfood checklist (full design: [design-usage-tracking-supergrok-pacing.md](design-usage-tracking-supergrok-pacing.md)) |
| 10 | [design-identity-self-other-multi-user.md](design-identity-self-other-multi-user.md) | Identity + multi-user prep (shipped on gi) |
| 11 | [design-glass-aurimago-gold-polish.md](design-glass-aurimago-gold-polish.md) | Glass gold theme polish (shipped on gi) |
| 12 | [design-gemma-sampling-hygiene-staged.md](design-gemma-sampling-hygiene-staged.md) | Staged sampling / hygiene (**superseded** by remove-gemma design; freeze body) |
| 13 | [design-continuous-work-orient-ledger-reset.md](design-continuous-work-orient-ledger-reset.md) | Continuous work + orient/ledger + full reset (mostly shipped) |
| 14 | [design-remove-gemma-local-stub.md](design-remove-gemma-local-stub.md) | Remove llama.cpp/Gemma path; stub `provider=local` (**shipped on gi**) |
| 15 | **[design-glass-multimodal-attachments.md](design-glass-multimodal-attachments.md)** | **Next stack:** glass STT/TTS, durable attachments in/out, RO sandbox media, Grok vision/Files |
| 16 | [known-bugs.md](known-bugs.md) | Deferred product bugs (e.g. stale timer/`task_ready` wake storms → moment bloat) |
| 17 | **[stretch-2/README.md](stretch-2/README.md)** | **Stretch 2 memory planning** (`grok-improvement-memory`) — phases, storage, meal composition |
| 17a | [stretch-2/inspiration-activity-model-and-storage.md](stretch-2/inspiration-activity-model-and-storage.md) | Activity model, data prototype, storage requirements |
| 17b | [stretch-2/design-context-meal-composition.md](stretch-2/design-context-meal-composition.md) | Provisional labeled meal + slide-off |
| 17c | [stretch-2/philosophical-soft-guidance.md](stretch-2/philosophical-soft-guidance.md) | Soft conceptual influences (not goals) |

**Conflict rule:** [stretch-1.md](stretch-1.md) wins for Stretch 1 runtime. Prefer **code on `grok-improvement`** over stale phase README status lines.  
**Superseded (do not follow for setup):** [inference.md](inference.md), [live-eval.md](live-eval.md) Gemma/llama steps, and [design-gemma-sampling-hygiene-staged.md](design-gemma-sampling-hygiene-staged.md) are historical freezes — freeze bodies stay until a docs modernization pass rewrites them.  
**Archive:** longer research notes under [archive/](archive/) (not freeze). Memory essay: [memory-atoms.pdf](memory-atoms.pdf).

## Stance (short)

- **One mind**, continuous presence, single worker  
- **Moment = one do-loop** (tools until stop) — not one tool hop  
- **Skills = how, tools = do, goals = what, self/users = who**  
- **Self ≠ user** (separate stores)  
- **Voice = `speak` tool** (with transport feedback)  
- **No language debt** — no “organs” cast; skills/tools/host jobs only  
- **Dogfood** — created tools/skills use the same formats as builtins  
- **Memory graph later** — Stretch 1 only emits moments + linear tapes; Stretch 2 planning on `grok-improvement-memory`  

## Stretch 1 vs Stretch 2

| Stretch 1 (shipped) | Stretch 2 (planning on `grok-improvement-memory`) |
|---------------------|--------------------------------------------------|
| Presence, wake queue, do-loops | Atomized memory; moments as groups of atoms |
| Skills + tools + create-tool (fail-closed, PR13) | Rolling summary ladder; labeled context meal |
| Sliding context meal | Temporal + episodic + later semantic/procedural channels |
| Grok product path on gi | LanceDB direction; Nemotron embed runtime (Phase 2) |

Start at [stretch-2/README.md](stretch-2/README.md).

## Run

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

Sandbox fitness (MSB, runners, honesty): [grok-improvement-plan/harness-sandbox-fitness.md](grok-improvement-plan/harness-sandbox-fitness.md).  
Tools/skills catalog + dogfood checklist: [tools-and-skills.md](tools-and-skills.md).

## Tests

```bash
pytest -m 'not llm'   # default pack
pytest -m llm         # real Gemma (needs model/ + GPU)

# Live qualitative stage gates (3-attempt protocol; needs model + GPU)
python scripts/live_eval/run_stage.py --stage 0 --all-scenarios --tries 3
```

Done-when map: root [README.md](../README.md) testing section and `tests/test_stretch1_donewhen.py`.  
Live protocol: [live-eval.md](live-eval.md). Ship knobs: [inference.md](inference.md).

## Status

**Stretch 1 complete** on the foundation path. Integration branch **`grok-improvement`** also carries Grok-by-default (Phase 0), sandbox fitness, Stage B soft MC, identity draft/promote, multi-user prep, and gold glass — see [project-status-pass.md](project-status-pass.md). `main` may lag. **Stretch 2 memory** is in planning on **`grok-improvement-memory`** — [stretch-2/README.md](stretch-2/README.md).
