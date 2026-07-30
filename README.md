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
  ├── LLM (xAI Grok default; local provider reserved / not implemented)
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

Inference: product path is **xAI Grok** (usage meter + SuperGrok pacing + hard-stops; continuous default **OFF**). `provider=local` fails closed (`local_not_implemented`) until a future OpenAI-compat backend lands. Product meals slide at **~50k** input tokens; glass **Context** rail shows last meal vs **~500k** model window (`MODEL_CONTEXT_WINDOW_TOKENS`; `CONTEXT_WINDOW_TOKENS = 86000` remains legacy meal-math ceiling). See [docs/grok-improvement-plan/README.md](docs/grok-improvement-plan/README.md).  
Usage / SuperGrok operator notes + dogfood checklist: [docs/grok-improvement-plan/usage-tracking-supergrok-pacing.md](docs/grok-improvement-plan/usage-tracking-supergrok-pacing.md).  
[docs/inference.md](docs/inference.md) is a **historical freeze — do not follow for setup** (older local-server path removed).

**Status JSON (API/glass):** inference posture fields are `chat_ready` / `chat_error` / `chat_busy` / `chat_operation` (replacing former `llama_*` keys). Clean break — no dual-write.

## Install

Python **3.12+**. Core Elyra is mostly stdlib; optional features are **host venv extras** that fail closed if missing (the supervisor still starts).

### Host vs guest (read this once)

| Layer | Where it lives | What it powers |
|-------|----------------|----------------|
| **Host venv** (`.venv`) | Your machine’s Python env | Supervisor, glass, LLM client, **host builtins** (`web_search`, `browser_*`, secrets, git/gh wrappers) |
| **Guest sandbox** | microVM + tree `sandboxes/sandbox0/` | Isolated `run` / create-tool **verify** when isolation is on |

`web_search` and browser tools are **not** installed into the guest. They need host packages (`elyra[search]`, `elyra[browser]`). The guest is for sandboxed execution, not for pip-installing those backends.

### 1. Base (always)

```bash
# from repo root — creates .venv, upgrades pip, installs editable elyra + pytest
./scripts/setup_venv.sh
source .venv/bin/activate
```

`setup_venv.sh` runs `pip install -e '.[dev]'`. Always activate the venv before `elyra`, `pytest`, or the microsandbox doctor (scripts that call bare `python3` need the venv on `PATH`).

### 2. Full dogfood (recommended)

One shot for isolation + search + browser + tests:

```bash
source .venv/bin/activate

pip install -e '.[dev,sandbox,search,browser]'
playwright install chromium              # browsers are separate from the pip package

./scripts/setup-microsandbox.sh --doctor-only
# optional deeper check: ./scripts/setup-microsandbox.sh --smoke
```

Linux isolation needs **KVM** (`/dev/kvm` readable/writable). Without it, install can succeed but guest warmup may fail — doctor will WARN.

### 3. Install à la carte

| Extra | Install | Enables | If missing |
|-------|---------|---------|------------|
| **dev** | `pip install -e '.[dev]'` | `pytest` | (included by `setup_venv.sh`) |
| **sandbox** | `pip install -e '.[sandbox]'` | Warm microsandbox isolation (`microsandbox`) | Guest `run` / isolation-on `verify_tool` → `sandbox_unavailable:*` |
| **search** | `pip install -e '.[search]'` | Host `web_search` via `ddgs` | `search_unavailable` (+ install hint) |
| **browser** | `pip install -e '.[browser]'` **and** `playwright install chromium` | Host `browser_*` tools | Clear browser unavailable errors |

Combine extras in one install: `pip install -e '.[sandbox,search,browser]'`.

Helper for sandbox only:

```bash
./scripts/setup-microsandbox.sh --install-extra   # pip install -e '.[sandbox]'
./scripts/setup-microsandbox.sh --doctor-only
./scripts/setup-microsandbox.sh --ensure-tree     # seed sandboxes/sandbox0 if needed
./scripts/setup-microsandbox.sh --smoke           # temporary create/exec/remove (not sandbox0)
```

Hermetic / CI host-stub (no guest isolation):

```bash
export ELYRA_SANDBOX=0
```

Product default when `ELYRA_SANDBOX` is **unset**: isolation **on** (needs `elyra[sandbox]` + working KVM for real guest work).

### 4. Host OS tools (optional, not pip)

| Tool | Used by | Notes |
|------|---------|--------|
| `git` | `git_*` tools | On `PATH` |
| `gh` | `gh_*` tools | On `PATH`; auth via `gh auth login` when needed |
| Grok / xAI | Product LLM | `elyra auth login` (preferred), glass **Status** login, `XAI_API_KEY`, or legacy `grok login` |

### 5. Sanity checks

```bash
source .venv/bin/activate
python -c "import elyra; print('elyra OK')"
python -c "import microsandbox; print('sandbox extra OK')"   # after .[sandbox]
python -c "import ddgs; print('search extra OK')"           # after .[search]
python -c "import playwright; print('browser extra OK')"    # after .[browser]
./scripts/setup-microsandbox.sh --doctor-only
```

Catalog, dogfood checklist, secrets/git notes: [docs/tools-and-skills.md](docs/tools-and-skills.md).

## Run

```bash
source .venv/bin/activate

# Product path (xAI Grok) — auth first if needed:
#   elyra auth login          # device-code → data/secrets/xai_oauth.json
#   elyra auth status
#   # or: export XAI_API_KEY=...  /  glass Status paste / legacy grok login
elyra start

# UI + API only, stub LLM (no remote calls / hermetic glass)
elyra start --stub-llm
```

`elyra auth login` is **paths-only** (no supervisor): it writes tokens via `persist_oauth_login`. Cold `elyra start` picks them up. If an instance is **already running**, restart it or complete login in Glass for live chat rebind. See `elyra auth login --help`.

Open **http://127.0.0.1:8787/**

| Flag / command | Effect |
|------|--------|
| `--stub-llm` | StubChatClient only (hermetic UI; no remote LLM) |
| `--provider xai\|local` | Product default `xai`; `local` fails closed (not implemented) |
| `--api-host` / `--api-port` | Bind (default `127.0.0.1:8787`) |
| `elyra auth login\|logout\|status` | Headless xAI OAuth (device-code); never prints tokens |

Optional knobs: `elyra.toml` under `ELYRA_HOME` (defaults include `loop.sliding_input_tokens = 50000`). CLI overrides win over toml.

## Testing

```bash
source .venv/bin/activate

# Default CI / local pack (no live remote model)
pytest -m 'not llm'

# Reserved marker for optional future OpenAI-compat live path
# (not wired; skips/unavailable without endpoint)
pytest -m llm
```

### Live qualitative stage gates

Fixed scenarios + full product path (presence → moment → do-loop). Historical protocol: [docs/live-eval.md](docs/live-eval.md) (**historical freeze**). Harness is **fail-closed** (Gemma/llama path removed): [scripts/live_eval/README.md](scripts/live_eval/README.md). Hermetic scenario loader tests remain in `tests/test_live_eval_scenarios.py`.

```bash
python scripts/live_eval/run_stage.py --stage 0 --all-scenarios --tries 3
# exits 2 — use xAI dogfood (`elyra start`) or a future OpenAI-compat harness
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
| [docs/tools-and-skills.md](docs/tools-and-skills.md) | Packages, package VCS, search/browser/secrets/git·gh, dogfood checklist |
| [docs/design-capability-growth-search-browse-vcs-secrets.md](docs/design-capability-growth-search-browse-vcs-secrets.md) | Capability growth product design |
| [docs/design-capability-growth-implementation-plan.md](docs/design-capability-growth-implementation-plan.md) | Capability growth PR plan / execute contract |
| [docs/inference.md](docs/inference.md) | **Historical freeze — do not follow for setup** (older local-server path removed) |
| [docs/live-eval.md](docs/live-eval.md) | **Historical freeze** — live qualitative protocol |
| [docs/design-remove-gemma-local-stub.md](docs/design-remove-gemma-local-stub.md) | Remove local-server path; stub `provider=local` |
| [docs/grok-improvement-plan/README.md](docs/grok-improvement-plan/README.md) | Grok migration phases (refresh if status lags code) |
| [docs/grok-improvement-plan/usage-tracking-supergrok-pacing.md](docs/grok-improvement-plan/usage-tracking-supergrok-pacing.md) | Usage + SuperGrok pacing — operator notes + dogfood checklist |
| [docs/README.md](docs/README.md) | Full index |

## Branch tip

Work and dogfood on **`grok-improvement`**. Promote to **`main`** is a separate operator step after sign-off.
