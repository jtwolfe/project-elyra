# Project Elyra — documentation

Communal digital teammate. Thin **do-loop** harness (Stretch 1), deeper **memory** later (Stretch 2).  
Grok Build for loop/tools/skills ideas — not as the product skin.

## Read in this order

| # | Doc | Role |
|---|-----|------|
| 1 | **[stretch-1.md](stretch-1.md)** | **Build freeze** — how Stretch 1 runs |
| 2 | **[design-stretch-1-implementation.md](design-stretch-1-implementation.md)** | **Implementation design + PR plan** (reviewed) |
| 3 | **[engineering-principles.md](engineering-principles.md)** | **How we write code** — modules, tests, config, dogfood |
| 3 | [overview.md](overview.md) | Big picture, glossary, Stretch 1 vs 2 |
| 4 | [tools-and-skills.md](tools-and-skills.md) | Packages, base catalog, dogfood, create-tool safety |
| 5 | [time-and-identity.md](time-and-identity.md) | Self ≠ user, time layers, speak timing |
| 6 | [inference.md](inference.md) | llama.cpp / Vulkan / Gemma from elyra2 |

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

| Stretch 1 (now) | Stretch 2 (later) |
|-----------------|-------------------|
| Presence, wake queue, do-loops | Hypergraph, auto-ontology |
| Skills + tools + create-tool (safe) | Opaque sleep / sparse linking |
| Sliding context, simple storage | LanceDB / graph migration |
| Gemma via llama.cpp Vulkan | Same model stack, richer memory |

## Run

```bash
./scripts/setup_venv.sh && source .venv/bin/activate
elyra start              # llama + API + UI
elyra start --no-llama   # stub LLM + UI
# http://127.0.0.1:8787/
```

## Status

Start stack + glass UI + simple presence chat loop. Multi-hop tools still ahead of Stretch 1 complete.
