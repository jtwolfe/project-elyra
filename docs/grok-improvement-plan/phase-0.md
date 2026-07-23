# Phase 0 — Concept Design + Implementation Plan

**Status:** Design complete · Implementation plan detailed (code not started)  
**Branch:** `grok-improvement` (integration branch — all Phase 0 work branches stack on it and merge back here; not `main` until later promotion)  
**Execution plan:** [phase-0-execution.md](phase-0-execution.md)  
**Purpose:** Make Project Elyra runnable on xAI Grok models under SuperGrok Heavy with controlled token usage, without changing Stretch 1 runtime semantics.

**Operator target after this work ships:**

```text
elyra start
```

boots with **provider = xai** (default), **model = Grok 4.5 Fast** (selectable), credentials resolved primarily from **Grok Build `~/.grok/auth.json`**, **continuous / auto-operation = OFF** (default), usage meter ON, and the Web UI clearly showing provider, active model, credential source, usage budgets, hard-stop state, and continuous state so the operator can see what is going on.

---

## 1. Goals

Phase 0 has three concrete goals:

1. **Provider path** — Elyra can talk to an xAI Grok endpoint (in addition to the existing local llama.cpp path). On this branch the **product default is xai**, authenticated primarily via **Grok Build `~/.grok/auth.json`**, default model **Grok 4.5 Fast**, with operator **model selection** and an optional **UI-stored API key** that can be selected as the active credential later.
2. **Usage protection** — A hierarchical usage meter prevents normal operation from consuming more than 50% of the real SuperGrok Heavy weekly quota.
3. **Prompt fitness** — System and orient prompts are adjusted so they do not over-constrain Grok the way they currently constrain Gemma. *(Already done on this branch.)*

Success means we can run `elyra start`, open the Web UI, run normal social + tool moments against Grok 4.5 Fast using the Grok Build session, see live status (provider / model / credential source / budgets / continuous), switch models or credential source when desired, and have the system automatically rest when budgets are exhausted — with continuous work remaining opt-in (OFF by default).

## 2. Non-goals (Phase 0)

Explicitly **out of scope** for Phase 0:

- Atomized / hypergraph memory (Stretch 2+)
- Metacognition (MC) implementation or package form (see [metacognition.md](metacognition.md); naming only is fine)
- The `grok_build` tool itself
- Self-modification continuity protocol or worktree workflows
- Continuous-work **policy** changes (the toggle and defaults stay; we only surface them better)
- Sampling / hygiene / flood mitigations that are Gemma-specific
- Removing the local Gemma path
- Any change to moment / do-loop / presence / wake-queue semantics
- Multi-model routing inside a single moment
- Remote Glass / Vercel deployment
- TTS / STT voice integration
- Full OAuth / token-refresh **browser** flow inside Elyra (reading existing Grok Build `auth.json` **is** in scope; operator re-logins outside Elyra if the session dies)
- Emergency override after a hard stop (default: **no**)

Phase 0 is deliberately thin. It only unlocks the Grok path, protects the subscription, and makes the operator glass honest about runtime state.

## 3. Ordering principles

1. Document first (this folder).
2. Implement the usage meter and provider abstraction as small, testable units (**meter first**).
3. Wire supervisor + status surface so `elyra start` and the Web UI work.
4. Verify with live calls under a tight budget before relaxing anything.
5. Only then consider post–Phase 0 work (light MC shape, then Phase 1 `grok_build` tool).

Past projects failed by building sophisticated memory first on models that could not reliably use it. We will not repeat that pattern.

---

## 4. Hierarchical usage meter

### 4.1 Policy target

Treat the real SuperGrok Heavy weekly quota as **100%**.

**Allowed budget for Elyra (normal operation) = 50% of the real weekly quota.**

Light days may use ~2% of real quota; hard days may use ~20%. The 50% target leaves headroom while still protecting the subscription.

### 4.2 Hierarchy

| Level | Share of *allowed* budget | Approx. share of *real* weekly quota | Enforcement |
|-------|---------------------------|--------------------------------------|-------------|
| **Week** | 100% of allowed | **50% of real** | Hard ceiling. System enters minimal/rest mode until renewal. |
| **Day** | ~1/7 of allowed | ~7.1% of real | Hard stop for normal operation. |
| **Hour** (1-hour blocks) | Day / 24 | ~0.3% of real | Hard stop. Refuse further model calls for the remainder of the block; force rest / wait. |

### 4.3 Design requirements

- Prefer reading the **real remaining percentage** from Grok Build's `/usage` (or the underlying account API) when available. Local tracking is the safety net and the source of truth for intra-hour accounting.
- Track usage at the level of individual model calls (and therefore moments).
- Persist usage state under `data/runtime/usage.json` so restarts do not reset the budget.
- Expose current remaining budget (week / day / hour) and any hard-stop reason via `/api/status` (and optionally a light orient slice) so the model **and the human** can see it.
- When a hard stop triggers:
  - Do not open new moments that would require model calls.
  - Allow graceful completion of an in-flight moment only if it can finish without further generation (or force an immediate rest).
  - Log the stop reason clearly and surface it in status.

### 4.4 Configuration knobs

```toml
[usage]
enabled = true
weekly_allowed_fraction = 0.50          # of real SuperGrok weekly quota
hour_block_minutes = 60
```

Exact config surface is implementation detail; the policy numbers above are the design contract. Defaults: meter **enabled**, fraction **0.50**.

### 4.5 Open questions for implementation

- Exact source of truth for "real weekly quota remaining" (Grok Build `/usage` parsing vs. future API). Phase 0 may ship with local tracking only and a documented hook for real remaining later.
- Whether a small emergency override is allowed after a daily hard stop. **Default: no** for Phase 0.
- How to attribute tokens when the provider reports only request counts or dollar cost rather than exact tokens. Prefer response `usage` tokens when present; document fallback.

---

## 5. Provider abstraction

### 5.1 Current state

- `LlamaServerConfig` + `HttpChatClient` are tightly coupled to a local OpenAI-compatible endpoint (`127.0.0.1:8080`).
- Supervisor starts and health-checks the local `llama-server` process.
- Sampling defaults and reasoning budget knobs are Gemma-oriented.
- CLI `elyra start` has no provider concept (`--no-llama` only).

### 5.2 Required change (minimal)

Introduce a thin provider layer so the do-loop continues to talk to a `ChatClient` while the concrete transport can be:

| Provider | Base URL | Auth | Notes |
|----------|----------|------|-------|
| `local` | `http://127.0.0.1:8080` | none | Existing llama.cpp path (still supported) |
| `xai` **(default)** | `https://api.x.ai/v1` | Bearer token | New path; product default on this branch |

Requirements:

- Config selects provider + model id + credentials.
- **Default provider = `xai`.** Local remains fully available via config / CLI override.
- `HttpChatClient` (or a small sibling) adds `Authorization` and `model` fields when talking to xAI.
- Gemma-specific request fields (`top_k`, `thinking_budget_tokens`, etc.) are omitted when the provider is not local.
- Supervisor **skips** starting `llama-server` when provider is `xai`.
- The existing `ChatClient` protocol and do-loop remain unchanged.

### 5.3 Credential handling (Phase 0)

**Primary access path is Grok Build session auth.** The operator's SuperGrok / Grok Build login is the normal way models are reached; a classic API key is optional and secondary.

#### Credential sources

| Source id | What | When used |
|-----------|------|-----------|
| `grok_build` **(default)** | Bearer from `~/.grok/auth.json` (Grok Build OIDC session; field `key` / `access_token`) | Product default when present and selected |
| `api_key` | Operator-supplied xAI API key stored by Elyra (see §9.3) | When the operator **selects** this source after pasting a key in the Web UI (or sets it via env/config for headless use) |
| `env` | `XAI_API_KEY` in the process environment | Convenience for scripts / CI; same material as `api_key`, not a separate product mode |

#### Resolution rules

1. **Active source** is a runtime-selectable preference (default `grok_build`), persisted under `data/runtime/` (e.g. with model preference). Surface it in status as `credential_source`.
2. Resolve the bearer for the active source only:
   - `grok_build` → read `~/.grok/auth.json` using the same shape as `scripts/prototype_xai_grok_auth_smoke.py`. No full OAuth / browser login loop inside Elyra in Phase 0 (operator runs `grok login` / Grok Build login separately). Optional best-effort expiry awareness in status (`credential_expires_at` / `credential_ok`); refresh may remain out of process for Phase 0.
   - `api_key` → load from Elyra-managed secret store (UI-saved key) or, if none stored, fall back to `XAI_API_KEY` env / config for headless.
3. If the **active** source cannot resolve a token with provider=xai, **fail clearly** (log + status: `credential_ok=false`, reason) — do **not** silently fall back to another source or to local. Operator can switch source in the UI or force local with config/CLI.
4. **Never** put raw tokens or API keys in `/api/status`, logs at info level, or the Web UI DOM beyond a one-time paste field that does not re-display the secret.

#### Why this order

This matches how the operator actually has access: SuperGrok Heavy / Grok Build session first. API key is preserved for later or alternate billing paths without becoming the default assumption.

### 5.4 Model selection

| Knob | Default | Notes |
|------|---------|-------|
| **Product default** | **Grok 4.5 Fast** | Presence / do-loop: fast, high-throughput; cost and latency matter |
| **Wire model id** | Config-owned string (e.g. current xAI id for 4.5 Fast) | Verify against `GET /v1/models` with the active credential; do not freeze a stale id in code forever |
| **Selectable** | Yes | Operator can change model via Web UI and/or `elyra.toml` / CLI; selection persists across restarts |

Requirements:

- Default model for provider=xai is **Grok 4.5 Fast** (exact API id in config; label in UI can be human-friendly).
- **Model list** for the picker: prefer live `GET /v1/models` when credential_ok; fall back to a small curated allowlist (including the default) if listing fails.
- Changing model applies to **subsequent** completions / moments — **not** mid-moment multi-model routing (still a Phase 0 non-goal).
- Later coding instrument (Phase 1+) may default to a stronger / Heavy path via Grok Build; Phase 0 presence stays on Fast unless the operator picks otherwise.

---

## 6. Prompt adjustments

### 6.1 Current situation

`prompts/system.md` and `prompts/orient.md` have already received a Phase 0 fitness pass on this branch (hard walls kept, Gemma-specific language softened, light usage-budget rest note present). **No further prompt rewrite is required for Phase 0** unless live Grok behaviour surfaces a concrete problem.

### 6.2 Design rules (retained)

**Keep (hard walls):** Self ≠ user; only successful `speak` reaches glass; fail-closed growth path; exact skill vs tool names; prefer tools over speculation; honest idle is allowed.

**Already softened:** Gemma-only failure-mode language, over-prescriptive sampling / private-channel instructions, low-capability assumptions.

**Already added lightly:** "When a usage limit is active, resting is correct behaviour."

### 6.3 Scope limit

Phase 0 does **not** rewrite identity (`self.md`), the full skill catalog, or continuous-work prompts.

---

## 7. Continuity notes (Phase 0 limited)

Person continuity already lives on disk (identity, goals/tasks, moments, continuous-work state, sandbox). Phase 0 does not change that contract.

**Continuous / auto-operation remains default OFF.**  
`ContinuousSettings.enabled = False` is already the product default. The operator enables continuous work explicitly via the Web UI rail toggle (or `PATCH /api/continuous`). Phase 0 must preserve this default and surface continuous state clearly in status; it must not turn continuous on as a side-effect of the Grok path.

Self-modification continuity is Phase 1/2. Design the provider and usage meter so later restart-based handoff remains possible.

---

## 8. Operator defaults and `elyra start` contract

After Phase 0 implementation, the following is the **default posture**:

| Knob | Default | Notes |
|------|---------|-------|
| `provider` | **`xai`** | Override to `local` via `elyra.toml` or CLI |
| `model` | **Grok 4.5 Fast** | Selectable; wire id in config / runtime preference |
| `credential_source` | **`grok_build`** | `~/.grok/auth.json` first; `api_key` selectable later |
| Continuous / auto | **OFF** | Already true; keep; surface in UI |
| Usage meter | **ON** | `weekly_allowed_fraction = 0.50` |
| llama-server | **Not started** when provider=xai | Started only for local |

### 8.1 CLI behaviour

```text
elyra start
```

- Resolves credentials for the active source (default: Grok Build `~/.grok/auth.json`).
- Builds RuntimeConfig with `provider=xai` and default model Grok 4.5 Fast (unless overridden).
- Skips llama-server start.
- Starts API + Web UI + presence worker with the xAI chat client gated by the usage meter.
- Prints clear startup lines, e.g.:

```text
Elyra home:  …
Web UI:      http://127.0.0.1:8787/
Provider:    xai  (model=grok-…-fast · source=grok_build)
Continuous:  off
Usage:       week remaining …% · day … · hour …
```

Optional CLI flags (suggested, minimal):

- `--provider local|xai` (override default)
- `--model <id>` (override default Grok 4.5 Fast)
- `--credential-source grok_build|api_key` (override active source)
- Existing `--no-llama` / `--stub-llm` remain useful for local/stub paths
- Existing `--api-host` / `--api-port` / `--context-tokens` unchanged

### 8.2 Config surface (proposed)

```toml
[provider]
name = "xai"                    # default on this branch
model = "…"                     # wire id for Grok 4.5 Fast; verify via /v1/models
base_url = "https://api.x.ai/v1"
credential_source = "grok_build"  # grok_build | api_key
# grok_build: ~/.grok/auth.json (primary)
# api_key: UI-stored key and/or XAI_API_KEY env for headless

[usage]
enabled = true
weekly_allowed_fraction = 0.50
hour_block_minutes = 60

[continuous]
enabled = false                 # product default; also runtime-toggled
```

Runtime preferences (model, credential_source, presence of stored api_key — never the secret itself in plain status) may also live under `data/runtime/` so Web UI changes survive restarts without rewriting `elyra.toml`.

Settings loading already supports sectioned `elyra.toml`. Add frozen dataclasses / sections without breaking existing loop/wait/tools/goals keys.

---

## 9. Web UI and status contract (operator visibility)

The operator must be able to **see what is going on** without reading logs. Phase 0 extends the existing status surface; it does not rebuild the glass.

### 9.1 Backend: `/api/status`

Extend `RuntimeState.snapshot()` and the merge in `ElyraApiHandler` so status includes at least:

```json
{
  "provider": "xai",
  "model": "…",
  "model_label": "Grok 4.5 Fast",
  "models_available": ["…", "…"],
  "credential_source": "grok_build",
  "credential_ok": true,
  "credential_detail": null,
  "api_key_configured": false,
  "llama_ready": false,
  "llama_error": null,
  "usage": {
    "enabled": true,
    "week_remaining_fraction": 0.42,
    "day_remaining_fraction": 0.8,
    "hour_remaining_fraction": 0.9,
    "hard_stop": null,
    "hard_stop_reason": null,
    "last_record_at": "…"
  },
  "continuous": { "enabled": false, "streak": 0, "…": "…" },
  "phase": "idle",
  "…": "existing worker fields"
}
```

- **Never** put secrets (API keys / session tokens) in status. `api_key_configured` is a boolean only.
- `credential_source` is the **active** source (`grok_build` | `api_key`).
- `hard_stop` / `hard_stop_reason` must be non-null when the meter is refusing calls.
- Continuous block already exists via the worker; keep and ensure the UI reads it.

### 9.2 Frontend: rail + Status panel

File targets: `elyra/runtime/web/app.js`, `index.html`, light CSS if needed.

**Rail pills (minimum):**

- Replace or dual the current llama-only pill with a **provider** pill, e.g. `xai ready` / `xai stop` / `local ready` / `credential missing`.
- Optional short model hint on rail or Status only (avoid clutter).
- Keep worker + phase pills.
- Keep continuous / autopilot pill behaviour (hidden when continuous OFF).

**Status panel:**

- Card for **Provider / model**: name, active model (with selector — see §9.3), credential_source, credential_ok.
- Card for **Usage budget**: week / day / hour remaining + hard-stop reason when active.
- Existing continuous summary card stays; ensure it is visible and accurate at default OFF.
- Keep the raw JSON dump for power users.

**Behaviour on hard stop:**

- Status shows the reason.
- New model-using moments are refused; glass remains usable for inspection, credential/model controls, turning continuous off, or waiting for budget renewal.

No new admin pages. No multi-user glass. Stay inside the existing SPA.

### 9.3 Operator controls: model + credentials (Phase 0)

Still inside the Status panel (or a small adjacent card on the same panel) — not a second app.

| Control | Behaviour |
|---------|-----------|
| **Model select** | Dropdown (or equivalent) bound to `models_available` + current `model`. `PATCH` (or POST) updates runtime preference; applies to **next** completion/moment. Default selection = Grok 4.5 Fast. |
| **Credential source** | Toggle / select: **Grok Build session** (`grok_build`) vs **API key** (`api_key`). Default = Grok Build. Switching only succeeds if that source can resolve; otherwise show clear error and leave previous source active. |
| **API key paste** | Password-style input + Save. Persists to Elyra secret store under the data home (file mode restricted; never committed). Sets `api_key_configured=true`. Does **not** auto-switch source unless the operator also selects API key. Clear/remove control allowed. |
| **Session hint** | For `grok_build`: show non-secret meta if known (e.g. email from auth.json, expiry / ok). Link-style note: log in via Grok Build / `grok login` if missing. |

Suggested endpoints (names flexible):

- `PATCH /api/provider` — body `{ "model"?: string, "credential_source"?: "grok_build"|"api_key" }`
- `PUT /api/provider/api-key` — body `{ "api_key": "…" }` (write-only; never echoed)
- `DELETE /api/provider/api-key` — clear stored key
- `GET /api/status` — already carries picker state (no secrets)

Local-only glass assumption remains: no multi-user auth on the API in Phase 0.

---

## 10. Implementation plan (ordered, file-level)

Prefer configuration + thin adapters. Tests ship with each unit. Scope comments on non-trivial functions.

### Step 1 — Config surface

- Add provider + usage sections to settings / runtime config (defaults: provider=`xai`, model=Grok 4.5 Fast wire id, `credential_source=grok_build`, usage enabled, continuous remains OFF).
- Files: `elyra/settings.py`, `elyra/runtime/config.py`, possibly generalize `elyra/llm/config.py`.
- CLI: `elyra/cli.py` — default provider xai; print provider/model/source/continuous/usage lines; optional `--provider`, `--model`, `--credential-source`.

### Step 2 — Usage meter (first substantive code)

- New pure module, e.g. `elyra/llm/usage.py` (or `elyra/usage/meter.py`).
- Record per call (prefer response `usage` tokens), hierarchy math, persist `data/runtime/usage.json`, `can_call()` / `hard_stop_reason()` / `remaining()`.
- Unit tests: rollover, persistence, hard-stop decisions, restart survival.
- No emergency override in Phase 0.

### Step 3 — Credentials + provider client path

- Keep `ChatClient` protocol.
- Generalize `HttpChatClient` (or thin sibling): Authorization Bearer, `model` field, omit Gemma-only fields for xai.
- Credential resolver module (e.g. `elyra/llm/auth.py`):
  - **Primary:** `~/.grok/auth.json` reader (lift from `scripts/prototype_xai_grok_auth_smoke.py`).
  - **Secondary:** UI/env API key store under data home (write-only API; file perms restricted).
  - Active source selection + fail-closed resolve (no silent fallback).
- Runtime preference store for model + credential_source (and api_key presence).
- Parse response `usage` into a field the meter can record (`ChatCompletionResult` may need a small extension).
- Optional: list models via `GET /v1/models` for the picker.
- Files: `elyra/llm/client.py`, `elyra/llm/config.py`, `elyra/llm/auth.py` (or similar).
- Tests: request payload shape by provider; resolve order; no secrets in logs/status.

### Step 4 — Supervisor + gate wiring

- When provider=xai: skip llama start/health; build xAI client; wrap with usage gate before every `chat_completion`.
- When provider=local: existing path unchanged.
- On hard stop: refuse new model-using work; log + set status fields.
- Files: `elyra/runtime/supervisor.py`, injection point used by PresenceWorker / loop, `elyra/runtime/state.py`.

### Step 5 — Status API + Web UI

- Extend `RuntimeState.snapshot` and `/api/status` merge with provider, model, models_available, credential_source, credential_ok, api_key_configured, usage block, hard_stop.
- Add model select + credential source select + API key paste/clear (§9.3) in Status panel.
- Wire `PATCH /api/provider` and put/delete API key routes; never echo secrets.
- Update rail pills + Status panel cards in `elyra/runtime/web/{app.js,index.html}` (and CSS if needed).
- Ensure continuous OFF is obvious on first load; default model label shows Grok 4.5 Fast; default source shows Grok Build.
- Manual check: open Web UI after `elyra start` and confirm all fields + controls work without digging into JSON only.

### Step 6 — Tests + live smoke

- Hermetic: meter, client payload, supervisor skip-llama, status shape.
- Regression: local path still works (`--provider local` or config).
- Live smoke checklist:
  1. `elyra start` with credentials → Web UI up, provider=xai, continuous off, usage numbers present.
  2. Social moment succeeds; tool-using moment succeeds.
  3. Tight temporary budget → hard stop surfaces in UI; further model calls refused; rest behaviour.
  4. Local override still boots llama path.
  5. Existing auth smoke script remains valid pathfinding aid.

### Step 7 — Docs status

- Flip this document's status to implementation complete once §11 criteria hold.
- Light note in [README.md](README.md) Phase 0 row.

---

## 11. Success criteria

Phase 0 is complete when **all** of the following hold:

1. **`elyra start`** (no flags) runs with **provider=xai** and **model=Grok 4.5 Fast** by default, does **not** start llama-server, and resolves credentials primarily from **Grok Build `~/.grok/auth.json`** (clear failure if the active source is missing).
2. **Continuous / auto-operation is OFF by default** and remains operator-controlled via the existing UI toggle; no path turns it on as a side-effect.
3. A normal social moment and a normal tool-using moment complete successfully against Grok 4.5 Fast (or the operator-selected model).
4. The usage meter tracks consumption and enforces 1-hour, daily, and weekly (50%-of-real) hard stops; hard-stop reason is logged and visible in status.
5. **Web UI adequately shows what is going on:** provider, model, credential_source, credential_ok, api_key_configured (boolean), usage remaining (week/day/hour), hard-stop state, continuous state (rail + Status panel).
6. **Operator can select models** in the UI (and via config/CLI); selection persists and applies to subsequent work.
7. **Operator can paste an API key in the UI**, store it, and **select** API key vs Grok Build as the active credential source later — without secrets appearing in status or logs.
8. System and orient prompts remain fitness-passed for Grok (already true).
9. Local Gemma path still works via explicit override (no regression).
10. Covered by hermetic tests where possible and the live smoke checklist above.

## 12. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Token burn before meter is solid | Meter first; optional tighter temporary fraction |
| Provider differences break tool calling | Minimal request surface; test tools early on live xAI |
| Prompt softening re-introduces Gemma issues on local | Shared fitness-passed prompts; keep local path unless regression appears |
| Default xai without Grok Build session confuses operator | Fail clearly; status points at `grok login` / Grok Build; API key path and local override remain |
| Session token expires mid-run | Surface credential_ok false; no silent infinite retry; Phase 0 may require re-login outside Elyra |
| Status noise or secret leakage | No secrets in status; API key write-only endpoints; small fixed field set |
| Wrong / stale model id for "4.5 Fast" | Config-owned id; verify with `/v1/models`; UI list from live catalog when possible |
| Scope creep into MC / memory / grok_build / continuous policy | Enforce non-goals; continuous default stays OFF |

## 13. Related later work (preview only)

After Phase 0 is verified:

- **Metacognition Stage B** — shallow shape only (ledger-aware soft bias + short Decide cadence). See [metacognition.md](metacognition.md).
- **Phase 1** — `grok_build` tool + self-improvement goal scaffolding.
- **Phase 2** — Self-modification continuity.
- **Phase 3** — Atomized memory as equal peer to MC.
- **Later** — Remote Glass, TTS/STT.

Do not start Phase 1 (or MC Stage B form beyond optional naming) until Phase 0 success criteria are met.

---

## 14. Ready for Grok Build

**Exact next actions (in order):**

1. Add provider + usage config sections with **defaults: provider=xai, model=Grok 4.5 Fast, credential_source=grok_build, usage on, continuous off**; CLI prints the posture.
2. Implement **UsageMeter** + persistence + unit tests (meter first).
3. Thin **xAI client path** + **credential resolver** (auth.json primary, optional stored/env API key, no silent fallback) + model id field omission rules.
4. **Supervisor** skip-llama for xai + usage gate on every chat completion.
5. Extend **RuntimeState / `/api/status`** and **Web UI**: provider pill, model select, credential source select, API key paste/clear, usage card, hard-stop visibility; continuous remains clear at OFF.
6. Live smoke under constrained budget with Grok Build session; confirm model switch + API key path + local override; flip status in this doc when §11 holds.
