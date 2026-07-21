# Project Elyra

Communal digital teammate: thin **do-loop** harness, skills & tools, self ≠ user, goals.  
**Stretch 1** ships a full **start stack**: llama-server, API, Web UI, presence worker.

## Quick start

```bash
# once
./scripts/setup_venv.sh
source .venv/bin/activate

# full stack (needs model/ → elyra2 model tree)
elyra start

# UI only + stub LLM (no GPU)
elyra start --no-llama
```

Open **http://127.0.0.1:8787/**

| Flag | Effect |
|------|--------|
| `--no-llama` | Skip llama-server; stub chat |
| `--stub-llm` | Stub client even if llama is up |
| `--api-host` / `--api-port` | Bind (default `127.0.0.1:8787`) |
| `--context-tokens N` | llama `-c` (default 86000; lower if VRAM crashes) |

Model files stay in `aurimago/project-elyra2/model`. Setup links them as `./model`.

## Documentation

| Doc | Role |
|-----|------|
| [docs/stretch-1.md](docs/stretch-1.md) | Build freeze |
| [docs/design-stretch-1-implementation.md](docs/design-stretch-1-implementation.md) | Implementation design + PR plan |
| [docs/engineering-principles.md](docs/engineering-principles.md) | How we build |
| [docs/overview.md](docs/overview.md) | Glossary |
| [docs/inference.md](docs/inference.md) | llama.cpp / Gemma |
| [docs/README.md](docs/README.md) | Full index |

## Status

Runnable **start process** + glass UI + simple chat do-loop. Full tool registry / multi-hop: see design PR plan.
