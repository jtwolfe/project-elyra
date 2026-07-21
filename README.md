# Project Elyra

Communal digital teammate: always-on **presence**, **moments** (multi-hop **do-loops**), tools & skills, self ≠ user, goals.  
**Stretch 1 is shipped** — full harness, not a one-shot chat scaffold.

## Architecture (Stretch 1)

```text
elyra start (supervisor)
  ├── llama-server (optional; stub for CI / --no-llama)
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
| **Sandbox** | One persistent workspace for FS tools and `run` |
| **Glass UI** | Chat, wait choices, goals, moments, tools, identity, status |

Inference: **Gemma 4 Q4** via **llama.cpp Vulkan**. Server `-c` is a **KV ceiling** (default 86000); product meals slide at **~24k** input tokens. See [docs/inference.md](docs/inference.md).

## Quick start

```bash
# once
./scripts/setup_venv.sh
source .venv/bin/activate

# full stack (needs model/ → elyra2 model tree)
elyra start

# UI + API only, stub LLM (no GPU)
elyra start --no-llama
```

Open **http://127.0.0.1:8787/**

| Flag | Effect |
|------|--------|
| `--no-llama` | Skip llama-server; stub chat |
| `--stub-llm` | Stub client even if llama is up |
| `--api-host` / `--api-port` | Bind (default `127.0.0.1:8787`) |
| `--context-tokens N` | llama `-c` KV ceiling (default 86000; lower if VRAM crashes) |

Model files stay in `aurimago/project-elyra2/model`. Setup links them as `./model` when present:

```bash
ln -sfn ../aurimago/project-elyra2/model model
```

Optional knobs: `elyra.toml` under `ELYRA_HOME` (defaults include `loop.sliding_input_tokens = 24000`). CLI overrides win over toml.

## Testing

```bash
source .venv/bin/activate

# Default CI / local pack (no GPU, no live model)
pytest -m 'not llm'

# Real Gemma via llama-server (needs model/ GGUF + Vulkan-capable GPU)
pytest -m llm
```

### Real LLM tests (`@pytest.mark.llm`)

Marked tests live in `tests/test_doloop.py` and `tests/test_llm_client_tools.py`. They:

1. Skip if `model/` is missing or incomplete (`validate_model_paths`).
2. Reuse a healthy server on `:8080`, or start a short-lived `llama-server` on a free port.
3. Exercise tool_calls through the HTTP client and multi-hop do-loop.

Requirements: `./model` symlink (or tree) with Gemma GGUF + mmproj + `llama.cpp/llama-server`, and enough VRAM for the chosen `-c` (tests often use a smaller `-c` like 8192).

```bash
# example: only real-model do-loop smoke
pytest -m llm tests/test_doloop.py -q
```

### Stretch 1 done-when regression

`tests/test_stretch1_donewhen.py` pins that each freeze **Done when** claim has covering tests and that PR13 create-tool gates remain present. It does not re-run the full suite; it maps claims → modules and asserts gate modules/symbols exist.

| Done-when claim | Primary tests |
|-----------------|---------------|
| Presence + wake + single worker do-loops | `test_presence_worker`, `test_wake_queue`, `test_doloop` |
| Moments/beats persist; restart-safe | `test_moment_store` |
| Base tools + sandbox; speak transport | `test_tools_fs`, `test_sandbox`, `test_speak`, `test_tool_registry` |
| Wait + multi-choice + timeout | `test_tools_social_wait`, `test_timers` |
| Skills mid-loop; base skills | `test_skills_catalog`, `test_doloop` |
| Goals/tasks + review bias | `test_goals`, `test_tools_ledger` |
| create-tool / create-skill fail-closed | **`test_create_tool_gates` (PR13)** |
| llama path + context policy | `test_config`, `test_loop_context`, `test_*` `@pytest.mark.llm` |
| Interjections mid-moment | `test_interject`, `test_api_routing` |

**create-tool checkbox requires PR13 gates** (path jail, drafts not callable, hash verify, promote only after verify, no overwrite bundled, install_skill local-only). Those gates live in `elyra/tools/verify.py`, `promote.py`, `builtin/growth.py` — they are not deferred “hardening.”

## Stretch 1 done-when status

See [docs/stretch-1.md](docs/stretch-1.md) § Done when. All Stretch 1 criteria are **checked**:

- [x] Presence + wake queue + single worker do-loops
- [x] Moments/beats persist; restart-safe
- [x] Base tools + sandbox; speak with transport feedback
- [x] Wait + multi-choice + timeout path
- [x] Skills loadable mid-loop; base skills present
- [x] Goals/tasks + review-before-close bias
- [x] create-tool / create-skill fail-closed (PR13)
- [x] llama.cpp Gemma path works; context policy documented
- [x] Interjections mid-moment

Out of scope (Stretch 2+): hypergraph / sleep, Lance graph, multi-sandbox, subagents.

## Documentation

| Doc | Role |
|-----|------|
| [docs/stretch-1.md](docs/stretch-1.md) | Runtime contract + done-when |
| [docs/design-stretch-1-implementation.md](docs/design-stretch-1-implementation.md) | Implementation design + PR plan |
| [docs/engineering-principles.md](docs/engineering-principles.md) | How we build |
| [docs/overview.md](docs/overview.md) | Glossary |
| [docs/inference.md](docs/inference.md) | llama.cpp / Gemma (`-c` vs sliding 24k) |
| [docs/tools-and-skills.md](docs/tools-and-skills.md) | Packages, catalog, create-tool safety |
| [docs/README.md](docs/README.md) | Full index |
