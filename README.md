# Project Elyra

<p align="center">
  <img src="ourlady.png" alt="Our Lady — Elyra" width="420" />
</p>

Communal digital teammate: always-on **presence**, **moments** (multi-hop **do-loops**), tools & skills, self ≠ user, goals, and a glass UI.

**Stretch 1 is shipped** — full harness, not a one-shot chat scaffold.  
**Current integration tip:** branch **`grok-improvement`** (Grok-by-default, sandbox fitness, Stage B soft MC, identity draft/promote, multi-user prep, gold glass). **`main` may lag.**  
Status snapshot: [docs/project-status-pass.md](docs/project-status-pass.md).

## Architecture (Stretch 1)

```text
elyra start (supervisor)
  ├── LLM (xAI Grok default on grok-improvement; local Gemma/llama optional)
  ├── HTTP API + glass UI  →  http://127.0.0.1:8787/
  └── PresenceWorker (single thread)
        wake queue + timers
             │
             ▼
        open MOMENT  (= one do-loop)
          model ↔ tools until stop / wait
          skills load mid-loop; speak / wait / sandbox
             │
             ▼
        close moment · persist beats
             │
             ▼
        next wake item
```

| Unit | Role |
|------|------|
| **Presence** | Always-on host process; claims wakes, runs one moment at a time |
| **Wake queue** | What starts the next do-loop (user, wait timeout, timer, task ready, …) |
| **Moment** | One full do-loop until stop / wait — not a single tool hop |
| **Do-loop** | Sliding context meal + model tool calls + results until stop |
| **Tools / skills** | Callable actions + markdown playbooks (catalog in orient; body on demand) |
| **Goals / tasks** | Durable *what* (separate from the wake queue) |
| **Self / users** | Durable *who* (separate stores; draft → promote) |
| **Sandbox** | Host tree `sandboxes/sandbox0/`; guest exec when isolation on (default) |
| **Glass UI** | Chat, wait choices, goals, moments, tools, identity, status |

Inference: product path on **`grok-improvement`** is **xAI Grok** (usage meter + hard-stops; continuous default **OFF**). Local **Gemma** via **llama.cpp** remains available for offline/CI. Server `-c` is a **KV ceiling** (default 86000); product meals slide at **~24k** input tokens. See [docs/inference.md](docs/inference.md) and [docs/grok-improvement-plan/README.md](docs/grok-improvement-plan/README.md).

## Quick start

```bash
# once
./scripts/setup_venv.sh
source .venv/bin/activate

# optional: warm microsandbox isolation (product default ON when ELYRA_SANDBOX unset)
pip install -e '.[sandbox]'
./scripts/setup-microsandbox.sh --doctor-only   # KVM / import checks
# hermetic host-stub (tests/CI): export ELYRA_SANDBOX=0

# full stack (Grok credentials on gi path, or local model/ + llama)
elyra start

# UI + API only, stub LLM (no GPU)
elyra start --no-llama
```

Without `elyra[sandbox]`, chat still starts; guest `run` / `sandbox_*` / isolation-on `verify_tool` fail closed (`sandbox_unavailable:*`). Install the extra so create-tool does not look broken.

Open **http://127.0.0.1:8787/**

| Flag | Effect |
|------|--------|
| `--no-llama` | Skip llama-server; stub chat |
| `--stub-llm` | Stub client even if llama is up |
| `--api-host` / `--api-port` | Bind (default `127.0.0.1:8787`) |
| `--context-tokens N` | llama `-c` KV ceiling (default 86000; lower if VRAM crashes) |

Local model files (optional) live under `aurimago/project-elyra2/model`. Setup links them as `./model` when present:

```bash
ln -sfn ../aurimago/project-elyra2/model model
```

Optional knobs: `elyra.toml` under `ELYRA_HOME` (defaults include `loop.sliding_input_tokens = 24000`). CLI overrides win over toml.

## Testing

```bash
source .venv/bin/activate

# Default CI / local pack (no GPU, no live model)
pytest -m 'not llm'

# Real local Gemma via llama-server (needs model/ GGUF + Vulkan-capable GPU)
pytest -m llm
```

### Real LLM tests (`@pytest.mark.llm`)

Marked tests live in `tests/test_doloop.py` and `tests/test_llm_client_tools.py`. They:

1. Skip if `model/` is missing or incomplete (`validate_model_paths`).
2. Reuse a healthy server on `:8080`, or start a short-lived `llama-server` on a free port.
3. Exercise tool_calls through the HTTP client and multi-hop do-loop.

Requirements: `./model` symlink (or tree) with Gemma GGUF + mmproj + `llama.cpp/llama-server`, and enough VRAM for the chosen `-c` (tests often use a smaller `-c` like 8192).

### Live qualitative stage gates

Fixed scenarios + full product path (presence → moment → do-loop). **3-attempt protocol** and failure modes: [docs/live-eval.md](docs/live-eval.md). Harness: [scripts/live_eval/README.md](scripts/live_eval/README.md).

```bash
python scripts/live_eval/run_stage.py --stage 0 --all-scenarios --tries 3
```

### Stretch 1 done-when regression

`tests/test_stretch1_donewhen.py` maps freeze **Done when** claims → covering tests and create-tool gate modules (`test_create_tool_gates`; historical PR13 surface). See [docs/stretch-1.md](docs/stretch-1.md) § Done when (all Stretch 1 criteria checked).

Out of scope (Stretch 2+ / later phases): hypergraph memory, Lance graph, multi-sandbox, subagents, full per-user chat glass, Phase 1 `grok_build` self-improve instrument.

## Documentation

| Doc | Role |
|-----|------|
| [docs/project-status-pass.md](docs/project-status-pass.md) | **Where we are now** — shipped vs gaps, prep before Build/memory |
| [docs/stretch-1.md](docs/stretch-1.md) | Runtime contract + done-when |
| [docs/engineering-principles.md](docs/engineering-principles.md) | How we build |
| [docs/overview.md](docs/overview.md) | Glossary |
| [docs/time-and-identity.md](docs/time-and-identity.md) | Self ≠ user; draft/promote; work-origin USER |
| [docs/tools-and-skills.md](docs/tools-and-skills.md) | Packages, catalog, create-tool safety |
| [docs/inference.md](docs/inference.md) | llama / Gemma path; context and sampling knobs |
| [docs/live-eval.md](docs/live-eval.md) | Live qualitative protocol |
| [docs/grok-improvement-plan/README.md](docs/grok-improvement-plan/README.md) | Grok migration phases (refresh if status lags code) |
| [docs/README.md](docs/README.md) | Full index |

## Branch tip

Work and dogfood on **`grok-improvement`**. Promote to **`main`** is a separate operator step after sign-off.
