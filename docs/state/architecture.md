# Architecture (as implemented)

| Field | Value |
|-------|--------|
| **Class** | STATE |
| **Status** | Active (living) |
| **Audience** | Operators / implementers needing a map of *what ships today* |
| **Normative?** | Prefer **code on `working`** when this page drifts |
| **Last verified** | 2026-08-04 (package inventory from `ls elyra/`) |
| **Related** | Root [README.md](../../README.md) harness · [stretch-1.md](stretch-1.md) runtime contract · [memory/README.md](memory/README.md) · [DEV branch-law](../dev/branch-law.md) |

> Living map of process topology, packages, data layout, and product boundaries.  
> **Not** a design PR stack. Archaeology lives under [docs/design/](../design/).  
> Package table authored from the live tree — not from historical principles sketches.

---

## 1. Process topology

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

Runtime contract detail: [stretch-1.md](stretch-1.md). Operator run path: root [README.md](../../README.md).

**Status JSON (high level):** `chat_ready` / `chat_error` / `chat_busy` / `chat_operation` (legacy `llama_*` keys removed). Sandbox readiness: `mount_ready` / `pyenv_ready` (see [sandbox-fitness-checklist.md](sandbox-fitness-checklist.md)).

---

## 2. Package map (`elyra/*`)

Inventory from the live package tree (top-level domains + one-line role). Root modules `cli.py`, `config.py`, `settings.py`, `messages.py` sit beside these packages.

| Package | Role (as shipped) |
|---------|-------------------|
| **`presence/`** | Wake queue, timers, single-thread `PresenceWorker`, interjections |
| **`loop/`** | Do-loop internals: context meal, continue/stop policy, thrash / skill-commit policy |
| **`moment/`** | Moment open/close + beat tape persistence |
| **`runtime/`** | Supervisor, HTTP API, glass web assets, provider runtime, usage/credits poller, reset |
| **`llm/`** | xAI Grok client, auth/OAuth, usage ledger, credits snapshot, reasoning hygiene |
| **`tools/`** | Registry, schema, runner, create-tool verify/promote, builtins (+ guest exec) |
| **`skills/`** | Catalog + on-disk playbook load (bundled / local / drafts) |
| **`sandbox/`** | Path jail + warm microsandbox (MSB) client, health, host-stub when isolation off |
| **`memory/`** | Atoms, meal, ladder, graph, Lance/JSONL stores, temporal, keep tray; `memory/embed/` |
| **`identity/`** | Self digest store, gates/grants, orient-user resolver |
| **`users/`** | Per-user digest store (other; multi-user prep) |
| **`goals/`** | Durable goals/tasks ledger (*what*; separate from wakes) |
| **`speak/`** | Glass delivery of assistant rows (sole writer path for speak) |
| **`media/`** | Attachments, STT/TTS hooks, meal expand, GC |
| **`secrets/`** | Named secrets store, inject hook, redaction (coexists with `llm.auth` key) |
| **`instrument/`** | Grok Build host instrument: modes, jobs, reaper, auth handoff, redact |
| **`prompts/`** | Prompt file load under `prompts/` |
| **`util/`** | Shared helpers (version ids, etc.) |

Entry points operators care about: `elyra start` → `runtime.supervisor` + `presence.worker` + glass on `:8787`.

---

## 3. Data layout (`ELYRA_HOME` / `data/`)

Default home is the repo (or `ELYRA_HOME`). Runtime data lives under `data/` (created by `ElyraPaths.ensure_data_dirs`):

| Path under `data/` | Contents |
|--------------------|----------|
| `moments/` | Moment records + beat tapes |
| `wakes/` | Durable wake queue |
| `identity/` | Self digest (versioned; draft → promote) |
| `users/` | Other-user digests |
| `goals/` | Goals / tasks ledger |
| `memory/` | Stretch 2 atom store (JSONL / optional Lance) |
| `runtime/` | `continuous.json`, `provider.json`, `usage.json`, prefs |
| `secrets/` | Mode `0700` — API key + named `values/` |
| `media/` | Content-addressed attachments |
| `browser/` | Optional Playwright session data |
| `sandbox/` | Legacy/host sandbox staging (product FS tools use host tree `sandboxes/sandbox0/`) |

Also seeded: `skills/{local,drafts}/`, `tools/{local,drafts}/`. Guest isolation mounts the product sandbox tree; see tools catalog and harness DESIGN for boundary detail.

---

## 4. Memory regimes (as implemented)

Honesty table lives in [memory/README.md](memory/README.md). Short map:

| Regime | Status | Manual |
|--------|--------|--------|
| **Temporal / episodic (Phase 1)** | **Done** — atoms, meal spine, ladder | [architecture/phase-1-temporal.md](memory/architecture/phase-1-temporal.md) |
| **Semantic (Phase 2)** | **Code rectified**; flags default **off**; dogfood pending | [architecture/phase-2-semantic.md](memory/architecture/phase-2-semantic.md) |
| **Directed traversal (Phase 2a)** | **Code shipped**; flags default **off**; dogfood pending | [architecture/phase-2a-directed-traversal.md](memory/architecture/phase-2a-directed-traversal.md) |
| **Procedural (Phase 3)** | Planned / experimental | DESIGN under [design/memory/](../design/memory/) |

Meal channels (when enabled): episodic, semantic, keep / glass-tail, temporal. Semantic encode: continuous `EncodeWorker` + `EmbedderGate` when flags on — see phase-2 manual. Designs and spikes stay under DESIGN; manuals here are **as-shipped**.

---

## 5. Tools / skills / sandbox boundary

| Layer | Rule of thumb |
|-------|----------------|
| **Tools** | Callable actions (`tools/` packages + builtins); drafts not callable until promote |
| **Skills** | Markdown playbooks (`SKILL.md`); loaded mid-loop; not executable surface alone |
| **Host builtins** | Search, browser, secrets, git/gh wrappers — host venv extras; fail closed if missing |
| **Guest sandbox** | Isolation default ON (`elyra[sandbox]` + KVM); `run` / verify under MSB; host-stub only when `ELYRA_SANDBOX=0` |
| **Grok Build** | Host instrument (`instrument/` + builtin); operator checklist [grok-build-dogfood.md](grok-build-dogfood.md) |

Catalog + dogfood: [tools-and-skills.md](tools-and-skills.md). Short isolation smoke: [sandbox-fitness-checklist.md](sandbox-fitness-checklist.md). Full H1–H6 design: [design/capability/harness-sandbox-fitness.md](../design/capability/harness-sandbox-fitness.md).

---

## 6. Inference product path

| Topic | Where |
|-------|--------|
| **Product LLM** | xAI Grok (primary dogfood path) |
| **Usage / SuperGrok pacing** | [usage-and-pacing.md](usage-and-pacing.md) (operator notes) |
| **Full usage design** | [design/usage/design-usage-tracking-supergrok-pacing.md](../design/usage/design-usage-tracking-supergrok-pacing.md) |
| **Gemma / llama setup freeze** | [docs/inference.md](../inference.md) — **historical freeze; not product setup** |
| **GI phase map** | [grok-improvement-plan/README.md](../grok-improvement-plan/README.md) |

Local `provider=local` is a reserved/stub surface — not the day-to-day path.

---

## 7. Honest limits

| Area | Limit (high level) |
|------|---------------------|
| Memory fidelity | Substrate past empty chat log; long-horizon quality still early |
| Semantic / 2a | Code present; **default flags off**; live dogfood not claimed complete |
| Multi-user | Prep landed (identity/users); not full multi-party product |
| GPU embed (Radeon VII / ROCm) | Dev path under [radeon-vii/](radeon-vii/); Tensile inject machine-local — [known-bugs.md](known-bugs.md) BUG-mem-gpu-01 |
| Inference freezes | Do not use Gemma `inference.md` / historical live-eval stages for setup |
| Autonomy / safety | External host controls required — root README **DANGER** |

Deferred product bugs and dogfood backlog: [known-bugs.md](known-bugs.md). Memory phase honesty: [memory/README.md](memory/README.md).

---

## 8. Prefer code + process law

1. **Code on `working`** beats any STATE prose when they conflict.  
2. STATE describes behaviour; GOAL is short north star; DESIGN is implementer archaeology/plans; DEV is tip/branch/pin law.  
3. Tip law: [docs/dev/branch-law.md](../dev/branch-law.md). Principles: [docs/dev/engineering-principles.md](../dev/engineering-principles.md).  
4. Taxonomy design: [docs/design/docs-reorg-taxonomy.md](../design/docs-reorg-taxonomy.md) (#121).

---

## Quick links

| Doc | Role |
|-----|------|
| [README.md](README.md) | STATE index |
| [stretch-1.md](stretch-1.md) | Runtime contract |
| [overview.md](overview.md) | Glossary / big picture |
| [tools-and-skills.md](tools-and-skills.md) | Packages, extras, dogfood |
| [time-and-identity.md](time-and-identity.md) | Self ≠ user; time layers |
| [memory/README.md](memory/README.md) | Stretch 2 phase status |
| Root [README.md](../../README.md) | Primary operator entry |
