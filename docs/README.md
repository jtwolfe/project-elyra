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
| 5 | [tools-and-skills.md](tools-and-skills.md) | Packages, base catalog, dogfood, create-tool safety |
| 6 | [time-and-identity.md](time-and-identity.md) | Self ≠ user, draft/promote, work-origin USER, time layers |
| 7 | [inference.md](inference.md) | llama.cpp / Vulkan / Gemma; `-c` vs sliding ~24k; **ship knobs** (temp 0.6, top_p/k, budget, hygiene, RC re-feed, hop-0 speak pin) |
| 8 | [live-eval.md](live-eval.md) | Live 3-attempt qualitative protocol; how to run `scripts/live_eval`; A/B failure modes; continuous `S-cont-*` |
| 9 | [grok-improvement-plan/README.md](grok-improvement-plan/README.md) | Grok migration phases (Phase 0–3); **refresh status if it lags code** |
| 10 | [design-identity-self-other-multi-user.md](design-identity-self-other-multi-user.md) | Identity + multi-user prep (shipped on gi) |
| 11 | [design-glass-aurimago-gold-polish.md](design-glass-aurimago-gold-polish.md) | Glass gold theme polish (shipped on gi) |
| 12 | [design-gemma-sampling-hygiene-staged.md](design-gemma-sampling-hygiene-staged.md) | Staged sampling / hygiene (**superseded** by remove-gemma design; freeze body) |
| 13 | [design-continuous-work-orient-ledger-reset.md](design-continuous-work-orient-ledger-reset.md) | Continuous work + orient/ledger + full reset (mostly shipped) |
| 14 | [design-remove-gemma-local-stub.md](design-remove-gemma-local-stub.md) | Remove llama.cpp/Gemma path; stub `provider=local` (**shipped on gi**) |
| 15 | **[design-glass-multimodal-attachments.md](design-glass-multimodal-attachments.md)** | **Next stack:** glass STT/TTS, durable attachments in/out, RO sandbox media, Grok vision/Files |

**Conflict rule:** [stretch-1.md](stretch-1.md) wins for Stretch 1 runtime. Prefer **code on `grok-improvement`** over stale phase README status lines.  
**Superseded (do not follow for setup):** [inference.md](inference.md), [live-eval.md](live-eval.md) Gemma/llama steps, and [design-gemma-sampling-hygiene-staged.md](design-gemma-sampling-hygiene-staged.md) are historical freezes — freeze bodies stay until a docs modernization pass rewrites them.  
**Archive:** longer research notes under [archive/](archive/) (not freeze). Phase 3 essay: [memory-atoms.pdf](memory-atoms.pdf).

## Stance (short)

- **One mind**, continuous presence, single worker  
- **Moment = one do-loop** (tools until stop) — not one tool hop  
- **Skills = how, tools = do, goals = what, self/users = who**  
- **Self ≠ user** (separate stores)  
- **Voice = `speak` tool** (with transport feedback)  
- **No language debt** — no “organs” cast; skills/tools/host jobs only  
- **Dogfood** — created tools/skills use the same formats as builtins  
- **Memory graph later** — Stretch 1 only emits moments + linear tapes  

## Stretch 1 vs Stretch 2

| Stretch 1 (shipped) | Stretch 2 (later) |
|---------------------|-------------------|
| Presence, wake queue, do-loops | Hypergraph, auto-ontology |
| Skills + tools + create-tool (fail-closed, PR13) | Opaque sleep / sparse linking |
| Sliding context (~24k under `-c` ceiling) | LanceDB / graph migration |
| Gemma via llama.cpp Vulkan | Same model stack, richer memory |

## Run

```bash
./scripts/setup_venv.sh && source .venv/bin/activate
pip install -e '.[sandbox]'   # optional but needed for guest isolation (default ON)
./scripts/setup-microsandbox.sh --doctor-only
elyra start              # API + UI (+ llama or xai per provider settings)
elyra start --no-llama   # stub LLM + UI
# hermetic host-stub: ELYRA_SANDBOX=0
# http://127.0.0.1:8787/
```

Sandbox fitness (MSB, runners, honesty): [grok-improvement-plan/harness-sandbox-fitness.md](grok-improvement-plan/harness-sandbox-fitness.md).

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

**Stretch 1 complete** on the foundation path. Integration branch **`grok-improvement`** also carries Grok-by-default (Phase 0), sandbox fitness, Stage B soft MC, identity draft/promote, multi-user prep, and gold glass — see [project-status-pass.md](project-status-pass.md). `main` may lag. Phase 3 memory not started (essay only).

