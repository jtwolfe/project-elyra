# Project Elyra — documentation

Communal digital teammate. Thin **do-loop** harness (**Stretch 1 shipped**), deeper **memory** later (Stretch 2).  
Grok Build for loop/tools/skills ideas — not as the product skin.

## Read in this order

| # | Doc | Role |
|---|-----|------|
| 1 | **[stretch-1.md](stretch-1.md)** | **Runtime contract** — how Stretch 1 runs + done-when |
| 2 | **[design-stretch-1-implementation.md](design-stretch-1-implementation.md)** | **Implementation design + PR plan** (reviewed) |
| 3 | **[engineering-principles.md](engineering-principles.md)** | **How we write code** — modules, tests, config, dogfood |
| 4 | [overview.md](overview.md) | Big picture, glossary, Stretch 1 vs 2 |
| 5 | [tools-and-skills.md](tools-and-skills.md) | Packages, base catalog, dogfood, create-tool safety |
| 6 | [time-and-identity.md](time-and-identity.md) | Self ≠ user, time layers, speak timing |
| 7 | [inference.md](inference.md) | llama.cpp / Vulkan / Gemma; `-c` vs sliding ~24k; **ship knobs** (temp 0.6, top_p/k, budget, hygiene, RC re-feed, hop-0 speak pin) |
| 8 | [live-eval.md](live-eval.md) | Live 3-attempt qualitative protocol; how to run `scripts/live_eval`; A/B failure modes; continuous `S-cont-*` |
| 9 | [design-gemma-sampling-hygiene-staged.md](design-gemma-sampling-hygiene-staged.md) | Staged plan for sampling / hygiene / tool-speak (design freeze) |
| 10 | [design-continuous-work-orient-ledger-reset.md](design-continuous-work-orient-ledger-reset.md) | Continuous work + orient/ledger + full reset design, PR plan, eval plan |

**Conflict rule:** [stretch-1.md](stretch-1.md) wins for Stretch 1 runtime.  
**Archive:** longer research notes under [archive/](archive/) (not freeze).

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

**Stretch 1 complete.** Presence worker → moments (multi-hop do-loops) → tools/skills → speak/wait → glass panels; create-tool gates from PR13; no one-shot chat path. See [stretch-1.md](stretch-1.md) Done when (all checked).
