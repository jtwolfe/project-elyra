# Phase 0 — Concept Design

**Status:** Design complete (implementation not started)  
**Branch:** `grok-improvement`  
**Purpose:** Make Project Elyra runnable on xAI Grok models under SuperGrok Heavy with controlled token usage, without changing Stretch 1 runtime semantics.

---

## 1. Goals

Phase 0 has three concrete goals:

1. **Provider path** — Elyra can talk to an xAI Grok endpoint (in addition to the existing local llama.cpp path).
2. **Usage protection** — A hierarchical usage meter prevents normal operation from consuming more than 50% of the real SuperGrok Heavy weekly quota.
3. **Prompt fitness** — System and orient prompts are adjusted so they do not over-constrain Grok the way they currently constrain Gemma.

Success means we can start the supervisor against a Grok model, run normal social + tool moments, and have the system automatically rest when budgets are exhausted.

## 2. Non-goals (Phase 0)

Explicitly **out of scope** for Phase 0:

- Atomized / hypergraph memory (Stretch 2+)
- Metacognition (MC) implementation or package form (see [metacognition.md](metacognition.md); naming only is fine)
- The `grok_build` tool itself
- Self-modification continuity protocol or worktree workflows
- Continuous-work policy changes
- Sampling / hygiene / flood mitigations that are Gemma-specific
- Removing the local Gemma path
- Any change to moment / do-loop / presence / wake-queue semantics
- Multi-model routing inside a single moment
- Remote Glass / Vercel deployment
- TTS / STT voice integration

Phase 0 is deliberately thin. It only unlocks the Grok path and protects the subscription.

## 3. Ordering principles

1. Document first (this folder).
2. Implement the usage meter and provider abstraction as small, testable units.
3. Verify with live calls under a tight budget before relaxing anything.
4. Only then consider post–Phase 0 work (light MC shape, then Phase 1 `grok_build` tool).

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
- Persist usage state under `data/` so restarts do not reset the budget.
- Expose current remaining budget (week / day / hour) to orient or a status endpoint so the model (and the human) can see it.
- When a hard stop triggers:
  - Do not open new moments that would require model calls.
  - Allow graceful completion of an in-flight moment only if it can finish without further generation (or force an immediate rest).
  - Log the stop reason clearly.

### 4.4 Configuration knobs (proposed)

```toml
[usage]
enabled = true
weekly_allowed_fraction = 0.50          # of real SuperGrok weekly quota
hour_block_minutes = 60
```

Exact config surface is implementation detail; the policy numbers above are the design contract.

### 4.5 Open questions for implementation

- Exact source of truth for "real weekly quota remaining" (Grok Build `/usage` parsing vs. future API).
- Whether a small emergency override (e.g. user-initiated high-priority moment) is allowed after a daily hard stop. Default recommendation: **no** for Phase 0.
- How to attribute tokens when the provider reports only request counts or dollar cost rather than exact tokens.

---

## 5. Provider abstraction

### 5.1 Current state

- `LlamaServerConfig` + `HttpChatClient` are tightly coupled to a local OpenAI-compatible endpoint (`127.0.0.1:8080`).
- Supervisor starts and health-checks the local `llama-server` process.
- Sampling defaults and reasoning budget knobs are Gemma-oriented.

### 5.2 Required change (minimal)

Introduce a thin provider layer so the do-loop continues to talk to a `ChatClient` while the concrete transport can be:

| Provider | Base URL | Auth | Notes |
|----------|----------|------|-------|
| `local` (default) | `http://127.0.0.1:8080` | none | Existing llama.cpp path |
| `xai` | `https://api.x.ai/v1` | Bearer token (API key or session) | New path |

Requirements:

- Config selects provider + model id + credentials.
- `HttpChatClient` (or a small sibling) adds `Authorization` and `model` fields when talking to xAI.
- Gemma-specific request fields (`top_k`, `thinking_budget_tokens`, etc.) are omitted or mapped only when the provider needs them.
- Supervisor skips starting `llama-server` when provider is `xai`.
- The existing `ChatClient` protocol and do-loop remain unchanged.

### 5.3 Credential handling (Phase 0)

Preferred order for Phase 0:

1. Explicit `XAI_API_KEY` (or config key) — simplest, fully supported public path.
2. Optional later: attempt to read a usable token from `~/.grok/auth.json` if present (Grok Build session; may need refresh logic).

Do **not** implement a full browser OAuth flow inside Elyra in Phase 0.

### 5.4 Model selection guidance

- Presence / do-loop → a fast, high-throughput Grok variant (cost and latency matter).
- Later coding instrument (Phase 1+) → stronger / Heavy path via Grok Build.

Exact model IDs are left to config so they can track xAI's current naming.

---

## 6. Prompt adjustments

### 6.1 Current situation

`prompts/system.md` and `prompts/orient.md` contain language that was necessary for Gemma (exact tool-name hygiene, strong speak-first pinning, flood/hygiene concerns, reasoning-channel behaviour). Some of that language is over-constraining for Grok.

### 6.2 Design rules for the Grok pass

**Keep (hard walls):**

- Self ≠ user
- Only successful `speak` reaches glass
- Fail-closed growth path (`install_tool_draft` → `verify_tool` → `promote_tool`)
- Exact skill names (hyphenated) vs tool names (snake_case)
- Prefer tools over speculation; honest idle is allowed

**Soften or remove:**

- Language that exists only to suppress Gemma-specific failure modes (channel floods, tool-name invention, hop-0 speak pin as a product-level law)
- Overly prescriptive sampling / private-channel instructions that Grok does not need
- Any wording that assumes a local, low-capability model

**Add (lightly):**

- Awareness that a usage budget exists and that resting when the budget is exhausted is correct behaviour
- Clear statement that Elyra is running under a Grok / xAI path when that is true (optional, for identity honesty)
- Optional: light naming of the Decide / metacognition role if it helps Grok without adding constraints (see [metacognition.md](metacognition.md) Stage A)

### 6.3 Scope limit

Phase 0 does **not** rewrite identity (`self.md`), the full skill catalog, or continuous-work prompts. Only the thin system + orient surfaces that every moment sees.

---

## 7. Continuity notes (Phase 0 limited)

Person continuity already lives on disk:

- `data/identity/self.md`
- Goals / tasks ledger
- Moment index + beat tapes
- Continuous-work state
- Sandbox

These survive process restart by design. Phase 0 does not change that contract.

Self-modification continuity (Elyra calling Grok Build to edit its own code, then continuing as the same person) is **Phase 1/2**. The only Phase 0 implication is: do not design the provider or usage meter in a way that makes later restart-based handoff impossible.

---

## 8. Success criteria

Phase 0 is complete when all of the following hold:

1. Config can select `provider = "xai"` (or equivalent) and the supervisor runs without starting llama-server.
2. A normal social moment and a normal tool-using moment complete successfully against a Grok model.
3. The usage meter correctly tracks consumption and enforces:
   - 1-hour hard stop
   - Daily hard stop
   - Weekly 50%-of-real ceiling
4. System and orient prompts no longer contain Gemma-only over-constraints that actively hurt Grok behaviour.
5. Local Gemma path still works (no regression).
6. All of the above is covered by hermetic tests where possible and a short live smoke checklist.

## 9. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Token burn before meter is solid | Implement meter first; start with a tighter temporary fraction if needed |
| Provider differences break tool calling | Keep request surface minimal; test tools early |
| Prompt softening re-introduces old Gemma failure modes on the local path | Keep local path on the original prompts or gate prompt set by provider |
| Scope creep into memory, MC form, voice, or `grok_build` | Enforce the non-goals list above |

## 10. Related later work (preview only)

After Phase 0 is verified:

- **Metacognition Stage B** — shallow shape only: ledger-aware soft bias + short Decide cadence in orient. See [metacognition.md](metacognition.md). No package required.
- **Phase 1** — `grok_build` tool (headless CLI, reuses existing Grok Build session) + self-improvement goal scaffolding.
- **Phase 2** — Self-modification continuity (worktree, verify, promote, controlled restart / resume).
- **Phase 3** — Atomized memory substrate guided by the memory-atom / hypergraph model (equal peer to MC).
- **Later** — Remote Glass (FE on Vercel + auth + tunnel/VPS), TTS/STT voice (Grok Build already uses `wss://api.x.ai/v1/stt` with refreshable OAuth/session bearer).

Do not start Phase 1 (or MC Stage B form beyond optional naming) until Phase 0 success criteria are met.
