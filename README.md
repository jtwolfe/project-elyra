# Project Elyra

<p align="center">
  <img src="ourlady.png" alt="Our Lady — Elyra" width="420" />
</p>

**Project Elyra** is a multi-use, standalone AI system whose long-term goal is a **mnemonic substrate modeled on human memory**—so that an autonomous agent can sustain identity, motives, and learning over time in a way that could be described, anthropomorphically, as a path toward something like **consciousness**.

That is the north star. It is not a claim of what the system already is.

---

## Vision

Human memory is not a warehouse of facts. It is **organized experience**: instances in time, woven by context, recency, association, and intentional keep. Elyra aims to build engineering analogs of that structure—atoms, moments, period narratives, edges, and labeled working context—so an always-on agent can:

- Remember *what happened* with usable structure, not only the last chat window  
- Hold a durable **self** distinct from **users**  
- Pursue and revise **goals** across sessions  
- Improve its own tools and skills under operator oversight  
- Eventually support multi-party, multi-use deployment without collapsing into a single disposable chat thread  

The product direction is a **presence process**: continuous life, interrupted by wakes, not a one-shot assistant.

---

## Where we are (honest)

**Today, under real technical limits, Elyra is a chain-of-thought (CoT) engine attached to a mnemonic substrate.**

The CoT (do-loop / moment harness) is mature enough for serious dogfood. The memory stack is past the “empty chat log” stage—durable atoms, labeled meals, glass-tail continuity, directed keep, and an episodic ladder—but it is still early relative to the full vision. Gaps remain in multi-user scale, fidelity of stored experience, and deep integration of learning with self-improvement. Those are active workstreams, not finished product claims.

### What works now

| Area | Status (high level) |
|------|---------------------|
| **Presence & moments** | Always-on worker, wake queue, multi-hop do-loops |
| **Identity** | Novel **self / other** stores (draft → promote); multi-user prep |
| **Goals & tasks** | Durable goal/task management separate from the wake queue |
| **Skills & tools** | Catalog + on-demand skills; host tools + optional guest sandbox |
| **Memory substrate** | Atoms, moments, context meal, glass-tail, keep, period ladder |
| **Glass UI** | Local web UI: chat, status, memory, goals, identity, tools |
| **Inference** | **Grok / xAI–focused** product path (usage meter, SuperGrok pacing) |

### What remains (no deep dive)

Long-horizon memory quality and policy, multi-party discourse, fuller edge fabric and traversal, safer autonomy boundaries, broader model backends, and the full self-improvement loop. Treat Stretch 1 as the **runtime harness**; Stretch 2+ as **memory and growth**—shipped pieces exist; the anthropomorphic vision is still ahead.

---

## Development posture

Elyra is entering a phase where **development is dogfooded on the system itself**, with **[Grok Build](https://x.ai/)** as an integral engineering tool—not only as the chat model. Day-to-day work assumes an operator running a live instance, reading Context and Moments, and iterating against real continuity failures.

**Present implementation focus:** Grok (xAI) as the primary LLM. Other providers may appear later; they are not the dogfood path today.

---

## Architecture (current harness)

```text
elyra start (supervisor)
  ├── LLM (xAI Grok product path; local provider reserved / not implemented)
  ├── HTTP API + glass UI  →  http://127.0.0.1:8787/
  └── PresenceWorker (single thread)
        wake queue + timers
             │
             ▼
        open MOMENT  (= one do-loop)
          model ↔ tools until stop / wait
          skills load mid-loop; speak / wait / sandbox
          memory meal rebuilds outer context when enabled
             │
             ▼
        close moment · persist beats / atoms
             │
             ▼
        next wake item
```

| Unit | Role |
|------|------|
| **Presence** | Always-on host process; claims wakes, runs one moment at a time |
| **Wake queue** | What starts the next do-loop (user, wait timeout, timer, task ready, …) |
| **Moment** | One full do-loop until stop / wait — not a single tool hop |
| **Do-loop** | Context meal + model tool calls + results until stop |
| **Tools / skills** | Callable actions + markdown playbooks |
| **Goals / tasks** | Durable *what* (separate from the wake queue) |
| **Self / users** | Durable *who* (separate stores; draft → promote) |
| **Memory** | Atoms, ladder summaries, labeled meal (episodic / semantic / keep / glass-tail / temporal) |
| **Sandbox** | Host tree `sandboxes/sandbox0/`; guest exec when isolation on (default) |
| **Glass UI** | Chat, wait choices, goals, moments, memory, tools, identity, status |

Inference notes, meal budget, and SuperGrok pacing: [docs/inference.md](docs/inference.md) (historical freeze for *setup*—use this README + current start path), [docs/grok-improvement-plan/README.md](docs/grok-improvement-plan/README.md), [docs/state/usage-and-pacing.md](docs/state/usage-and-pacing.md). Architecture map: [docs/state/architecture.md](docs/state/architecture.md).

Memory philosophy reference: [docs/memory-atoms.pdf](docs/memory-atoms.pdf) (*What is wrong with my memory?*).

**Status JSON:** `chat_ready` / `chat_error` / `chat_busy` / `chat_operation` (legacy `llama_*` keys removed).

---

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

Catalog, dogfood checklist, secrets/git notes: [docs/state/tools-and-skills.md](docs/state/tools-and-skills.md).

---

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

Optional knobs: `elyra.toml` under `ELYRA_HOME` (defaults include `loop.sliding_input_tokens = 250000` as settings fallback; product meal size is runtime `meal_budget_fraction`, default 0.5 → ~250k of ~500k model window; slider max **75%** of model window unless raised). CLI: `elyra start --max-meal-override 100` raises the meal slider ceiling to 100% of the model window (persists `max_fraction` in `data/runtime/meal_budget.json`). Other CLI overrides win over toml.

---

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

Fixed scenarios + full product path (presence → moment → do-loop). Historical protocol: [docs/live-eval.md](docs/live-eval.md) (**historical freeze**). Harness is **fail-closed** for removed local paths: [scripts/live_eval/README.md](scripts/live_eval/README.md). Hermetic scenario loader tests remain in `tests/test_live_eval_scenarios.py`.

```bash
python scripts/live_eval/run_stage.py --stage 0 --all-scenarios --tries 3
# exits 2 — use xAI dogfood (`elyra start`) or a future OpenAI-compat harness
```

### Stretch 1 done-when regression

`tests/test_stretch1_donewhen.py` maps freeze **Done when** claims → covering tests. See [docs/state/stretch-1.md](docs/state/stretch-1.md).

create-tool / create-skill fail-closed is covered by **PR13** gates in `tests/test_create_tool_gates.py` (not deferred hardening).

---

## Documentation

Four-class hub: **[docs/README.md](docs/README.md)** (STATE · GOAL · DESIGN · DEV · archive/investigations).

| Doc | Role |
|-----|------|
| [docs/README.md](docs/README.md) | **Hub** — class index + run/test pointers |
| [docs/state/architecture.md](docs/state/architecture.md) | As-implemented architecture map |
| [docs/state/stretch-1.md](docs/state/stretch-1.md) | Runtime contract + done-when |
| [docs/state/overview.md](docs/state/overview.md) | Glossary / big picture |
| [docs/state/tools-and-skills.md](docs/state/tools-and-skills.md) | Packages, search/browser/secrets/git·gh, dogfood checklist |
| [docs/state/time-and-identity.md](docs/state/time-and-identity.md) | Self ≠ user; draft/promote |
| [docs/state/memory/README.md](docs/state/memory/README.md) | Memory / Stretch 2 index |
| [docs/state/usage-and-pacing.md](docs/state/usage-and-pacing.md) | SuperGrok pool vs ledger, burst, override |
| [docs/dev/engineering-principles.md](docs/dev/engineering-principles.md) | How we build (+ **§10** docs taxonomy) |
| [docs/dev/branch-law.md](docs/dev/branch-law.md) | Integration tip `working` → promote → `main` |
| [docs/design/README.md](docs/design/README.md) | DESIGN catalog (status-indexed) |
| [docs/grok-improvement-plan/README.md](docs/grok-improvement-plan/README.md) | Grok migration / dogfood phases |
| [docs/memory-atoms.pdf](docs/memory-atoms.pdf) | Memory philosophy (*What is wrong with my memory?*) |
| [docs/archive/project-status-pass.md](docs/archive/project-status-pass.md) | Historical status snapshot (prefer hub + board) |

---

## Sponsors

If this work is useful to you, sponsorship helps keep autonomous-agent and memory research independent:

<iframe src="https://github.com/sponsors/jtwolfe/button" title="Sponsor jtwolfe" height="32" width="114" style="border: 0; border-radius: 6px;"></iframe>

[Sponsor @jtwolfe on GitHub](https://github.com/sponsors/jtwolfe)

---

## DANGER

**THIS IS AN ANTHROPOMORPHIC MEMORY AND MOTIVE SYSTEM FOR AUTONOMOUS GOAL SETTING, LONG-TERM LEARNING, AND SELF-IMPROVEMENT.**

Elyra is designed to retain experience, form durable motives, and act across time under operator configuration. It is **not** a sandboxed chat demo with built-in institutional guarantees.

**ALL SAFEGUARDS MUST BE SET ON EXTERNAL SYSTEMS.**

Do not rely on in-process prompts alone for safety, isolation, network policy, spend limits, or access control. Constrain the host, credentials, network, sandbox, and billing/usage caps **outside** the agent. Run only in environments you control. Assume the agent will try to complete goals you (or prior sessions) gave it.

If you are not prepared to own those external controls, **do not run Elyra unattended**.
