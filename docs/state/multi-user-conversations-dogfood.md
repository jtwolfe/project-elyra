# Multi-user conversations — operator dogfood checklist (C12)

| Field | Value |
|-------|--------|
| **Class** | STATE |
| **Audience** | Operators dogfooding C12 multi-user + group chat continuity |
| **Status** | Active (living checklist) — **code landed on feature stack; live dogfood boxes open** |
| **Normative?** | No — prefer code on tip / `working`; boxes are ops evidence, not product claims |
| **Last verified** | 2026-08-07 — multi-user PR2–PR8 + conv-tool UI/tools on `feature/elyra-conv-tool`; **hermetics green**; **live Gate A/B/C and D1–D4 / T1–T4 not signed** |
| **Normative design** | [design-multi-user-conversations.md](../design/glass/design-multi-user-conversations.md) (**v2 — concurrent `/chat` client sessions**) |
| **Follow-on design** | [design-conversation-list-and-group-tools.md](../design/glass/design-conversation-list-and-group-tools.md) — list discovery poll + `create_group` / `update_group` tools |
| **Related issues** | [#118](https://github.com/jtwolfe/project-elyra/issues/118) C12 packaging bar; [#127](https://github.com/jtwolfe/project-elyra/issues/127) / [#128](https://github.com/jtwolfe/project-elyra/issues/128) / [#129](https://github.com/jtwolfe/project-elyra/issues/129); residual gate [#131](https://github.com/jtwolfe/project-elyra/issues/131) (**hooks only**) |
| **Stack base** | Multi-user tip @ ~`8c9a8c7`; **conv-tool land tip `feature/elyra-conv-tool`** (then multi-user / `working` after dogfood) |
| **Claim today** | **Multi-user implement landed** (store, sessions, speak/wait, glass_tail, orient, operator multi-convo, `/chat`, skills notes). **Conv-tool code landed on `feature/elyra-conv-tool`** (list poll discovery + topology tools) — hermetics only. **Do not mark acceptance #1–#7 or D1–D4 / T1–T4 green until live operator dogfood.** Concurrent principals (KD21–25) ≠ product auth. |

> Maps **1:1** to design § **Demo / dogfood acceptance (C12-level)**. Pass criteria match that table exactly.  
> Discovery + tools bar (D1–D4, T1–T4) maps to [design-conversation-list-and-group-tools.md](../design/glass/design-conversation-list-and-group-tools.md) § dogfood acceptance.  
> Live dogfood is **operator-owned** — not a CI gate. Hermetic suites prove API isolation; they do **not** close Gate B/C alone.

---

## Truth notes

| Claim | Status |
|-------|--------|
| Conversation store + message `conversation_id` | **Code** (PR2) — hermetic `tests/test_conversations.py` |
| Per-client session registry (KD21–25) | **Code** (PR3a) — hermetic T16 / T16b / T18 |
| Message/wake `conversation_id` + `social_kind` | **Code** (PR3b) |
| speak / wait_user resolve + group wait match | **Code** (PR3c) — hermetic T8 / T15 / T9 |
| Conversations REST CRUD | **Code** (PR3d) |
| glass_tail conversation scope / meal isolation | **Code** (PR4) — Gate A live still open |
| Orient participants / active chats / soft recently-active | **Code** (PR5) |
| Operator Glass multi-convo switch | **Code** (PR6) — Gate B live still open |
| `/chat` product shell + honesty footer | **Code** (PR7) — Gate C live still open |
| Skills / TOOL.md multi-user address notes | **Code** (PR8) — `talk`, `speak`, `wait_user` |
| Conversation list discovery poll (`CONVERSATIONS_POLL_MS` 3s; dogfood ≤5s) | **Code** on `feature/elyra-conv-tool` (UI PR1) — live D1–D4 **open** |
| Topology tools `create_group` / `update_group` | **Code** on `feature/elyra-conv-tool` (tools PR2) — hermetics T-G*; live T1–T4 **open** |
| keep_tray per-conversation | **Docstring-only #131 A hook** — still instance-global |
| Concurrent multi-window principals | **Code path ready** — #7 live B partial / C formal **unsigned** |
| #131 keep trays / full presence / real auth | **Out-of-implement** — hooks only (see residuals) |
| Process-global PresenceWorker phase | **Accepted residual** — concurrent ≠ multi-moment |
| Gate B / product multi-tenant security | **Not** this checklist’s done bar |
| Operator forensic admin ≠ social membership | **Honest stance** — view_mode=all / impersonate ≠ auto-member (parent OQ6 / KD-O1) |

**Honest copy (use the same phrases as UI):**

| Surface | Phrase |
|---------|--------|
| Operator rail | **“Session user (impersonate)”** |
| `/chat` footer | **“local dogfood — not authenticated.”** |
| Optional muted note | **“per-tab client session — not login.”** |

`client_id` is **forgeable** dogfood binding, not a credential (#131 C).

---

## Gates A / B / C

Map to design **Dogfood gates**. Mark a gate only after its acceptance rows pass on a real PE/host run. **Code complete ≠ gate green.**

| Gate | After (design) | Accepts rows | Inspect / proof | Status |
|------|----------------|--------------|-----------------|--------|
| **A — Meal truth** | PR4 (+ PR3b/c) | **#1** isolation, **#5** solo continuous | last_compose `glass_tail_meta.conversation_id` + packed speakers; **not** unscoped meal inspect | ☐ open (code landed; live unsigned) |
| **B — Operator UX** | PR6 (+ PR3a) | **#3** switch, **#2** group create/send; **partial #7** concurrent on operator `/` | Glass UI + API; hermetic T16/T16b | ☐ open (code landed; live unsigned) |
| **C — Demo sugar** | PR5 + PR7 | **#2** participants map, **#4** `/chat`, **formal #7** concurrent `/chat` + chrome | Orient + product shell multi-client | ☐ open (code landed; live unsigned) |
| **Closeout** | PR8 | **#6** gaps honesty (+ phase residual honesty) | Residuals explicit in this doc; skills/TOOL notes landed | ☑ residuals documented (live #6 still operator confirm) |

Gate A does **not** require participants map (PR5) or `/chat` (PR7). Concurrent session hermetics landed with **PR3a**. Gate B can partial-prove multi-window on operator `/`. Gate C formalizes `/chat` + honesty footer + full acceptance #7.

---

## Prep

- [ ] Tip includes multi-user stack (or pin SHA); for D1–D4 / T1–T4 use **`feature/elyra-conv-tool`** land tip; hermetic suite green where applicable
- [ ] `elyra start` → Glass `http://127.0.0.1:8787/` and product `http://127.0.0.1:8787/chat`
- [ ] ≥2 identity users available (e.g. **jim**, **sam**) via UsersStore / operator seed
- [ ] Continuous work **OFF** unless exercising #5 solo continuous
- [ ] Prefer **idle between turns** for multi-window demos (shared PresenceWorker phase residual)
- [ ] For concurrent #7 / D1–D2: **separate windows** (or remote browsers) — tab-duplicate may clone `sessionStorage` client_id (KD21 caveat)
- [ ] Confirm UI honesty copy: impersonate rail / dogfood footer

---

## Acceptance checklist (1:1 design table)

Mark each item after a real operator run. Pass criteria match [design acceptance](../design/glass/design-multi-user-conversations.md#demo--dogfood-acceptance-c12-level).  
**Leave boxes open until live evidence** — hermetic green is not a substitute.

### #1 — ≥2 users, separate DMs (Gate A)

- [ ] **#1** Switch jim ↔ sam (**per client**); each DM history isolated  
  Pass: glass_tail on jim wake has **no** sam lines via **last_compose** snapshot (`glass_tail_meta.conversation_id` + packed speakers). Do **not** use unscoped `_compose_meal_for_inspect` to claim isolation.  
  Design: acceptance #1 · Gate **A**.  
  Implement: PR4 glass_tail scope + PR3a sessions.

### #2 — ≥1 group, 2+ members (Gate B → C)

- [ ] **#2** Create group; send as member A; speak lands in group; attribution labels  
  Pass: participants list present (PR5); group assistant rows use role display (null `user_id` / KD20) — not stamped operator.  
  Design: acceptance #2 · Gate **B** then **C**.  
  Implement: PR2 store + PR3c/d + PR5 + PR6 UI.

### #3 — Operator switch without reset (Gate B)

- [ ] **#3** Change user **and** conversation mid-session **on one client**  
  Pass: auto-DM vs keep-group membership correct; **other clients unaffected** (no process-global stomp).  
  Design: acceptance #3 · Gate **B**.  
  Implement: PR3a registry + PR6 operator UI.

### #4 — `/chat` usable (Gate C)

- [ ] **#4** Private Chat + one group send/receive; honesty footer visible  
  Pass: product chrome; footer **“local dogfood — not authenticated.”** (optional per-tab client note).  
  Design: acceptance #4 · Gate **C**.  
  Implement: PR7 product shell.

### #5 — Solo continuous (Gate A)

- [ ] **#5** Some client has active DM; timer/continuous wake payload **null** conversation; meal glass_tail **empty**  
  Pass: continuous/timer does not inherit any client’s DM binding into glass_tail.  
  Design: acceptance #5 · Gate **A**.  
  Implement: PR3b null conversation on non-social + PR4 empty tip.

### #6 — Explicit gaps (Closeout)

- [ ] **#6** Residuals documented and UI copy consistent (operator confirms on tip)  
  Pass: #131 + multi-wait residual + **process-global phase/interject residual** listed; **impersonation ≠ auth** same copy; **client_id ≠ login**.  
  Design: acceptance #6 · Closeout (PR8). See [Out-of-implement / residuals](#out-of-implement--residuals) below.  
  Docs: this section filled at PR8; live UI copy still operator-check.

### #7 — Concurrent multi-principal (Gate B partial / C formal)

- [ ] **#7a — B partial (operator `/`)** Two windows and/or remote browser: Jim Private Chat + Sam same group **simultaneously** (no product shell required)  
  Pass: messages appear in both for shared conversation; **no last-writer-wins** session stomp; waits match per client session membership (`matches_session` when client known).  
  Design: acceptance #7 · Gate **B partial**.  
  Hermetic: T16/T16b prove session isolation only.

- [ ] **#7b — C formal (`/chat`)** Same concurrent bar on `/chat` + chrome + honesty footer  
  Pass: formal multi-client demo path; checklist evidence recorded.  
  Design: acceptance #7 · Gate **C formal**.

#### #7 notes — concurrent residual honesty (shared PresenceWorker phase)

**Guaranteed by v2 (code):**

| Guaranteed | Meaning |
|------------|---------|
| Per-client session binding | No last-writer-wins on who-is-typing / which-thread (KD21 registry) |
| Message ledger isolation | Poll/filter by `conversation_id` |
| Per-client wait match | `matches_session` UI when client known (KD24) |

**Not claimed (accepted residual — not a fail of #7):**

| Not guaranteed | Meaning |
|----------------|---------|
| Independent simultaneous moments | One `PresenceWorker` phase for all clients |
| Interject isolation mid-turn | Shared interject buffer; mid-moment POST from another principal routes global `resolve_user_input` |
| Concurrent multi-pending waits | First-pending only (C12 multi-wait residual) |

**Dogfood policy:** prefer **idle between turns** for clean multi-window demos. Ledger + session isolation is the bar; concurrent model attention is not. Optional cross-conversation overflow-enqueue hardening is **not** required for Gate A.

---

## Discovery + topology tools bar (conv-tool follow-on)

Normative design: [design-conversation-list-and-group-tools.md](../design/glass/design-conversation-list-and-group-tools.md).  
**Stack:** code on **`feature/elyra-conv-tool`** (UI list poll + `create_group` / `update_group` tools). Hermetics landed; **live D1–D4 / T1–T4 still open — do not mark green without evidence.**

Extends Gate B/C acceptance **#2** / **#7** (member sees groups; concurrent discovery). List poll default **`CONVERSATIONS_POLL_MS = 3000`**; pass bar is **≤5s** after create (not “≤2 tick intervals” — 1.5s `tick` ≠ list throttle).

### Operator-admin honesty (forensic ≠ membership)

| Stance | Meaning |
|--------|---------|
| **Operator = forensic admin** | See-all message feed (`view_mode=all`), impersonate session users, debug — **not** automatic social membership |
| **Members = explicit list only** | UI create and tools use the members you pass; **do not** auto-add operator / creator / wake user |
| **Impersonation ≠ auth** | Same as parent KD11 / #131 C — forgeable `client_id`; dogfood only |
| **Bound-group inject** | Creating or already-bound tab may keep a non-member group in select for the current bind — **not** discovery for other clients |

### Discovery (D1–D4)

Leave boxes open until a real multi-window run on `feature/elyra-conv-tool` (or land tip).

| # | Scenario | Pass | Status |
|---|----------|------|--------|
| **D1** | Operator creates group members=**jim+sam** **without** operator; Jim `/chat` open already | Within **≤5s**, Jim conversation select shows the group name **without** full page reload | ☐ open (code landed; live unsigned) |
| **D2** | Same for Sam concurrent window | Same (≤5s, no full reload) | ☐ open |
| **D3** | Operator create tab when operator ∉ members | Notice that operator is not a member; select does **not** auto-bind as member | ☐ open |
| **D4** | Kill API mid-session (or bad proxy) | **One** notice “Conversation list failed…” (or only on error **change**); previous groups remain; select may show `data-error` / title; **not** wiped to Private-only; no toast every 3s | ☐ open |

### Tools (T1–T4)

| # | Scenario | Pass | Status |
|---|----------|------|--------|
| **T1** | In a moment, model / `registry.execute` `create_group(name, members=[jim,sam])` | Store record correct; tool payload returns `group:…` id | ☐ open (hermetic T-G* green; live unsigned) |
| **T2** | Member clients discover tool-created group via poll (no reload) | Same as D1 (**≤5s**) | ☐ open |
| **T3** | `update_group` add member (**full list replace**) | New member discovers on poll; removed member loses list entry on poll | ☐ open |
| **T4** | `create_group` with `ctx.user_id=operator` and members without operator | operator **not** in members | ☐ open |

### Conv-tool residuals (honest; not fail of D/T bar)

| Residual | Notes |
|----------|-------|
| **Poll lag ≤5s** | List throttle 3s + tick skip / slow GET; dogfood bar is ≤5s, not instant |
| **Open-select rebuild** | Successful list refresh rebuilds `<select>` `innerHTML`; open dropdown may collapse ~every 3s — accepted dogfood residual |
| **No push / SSE** | Tool- or UI-created groups do not push to browsers; **poll is the discovery path** |
| **#131** | Real multi-user auth / keep trays / full presence still **out** — same hooks-only stance as parent closeout |

---

## Out-of-implement / residuals

### #131 — hooks only (do not implement in this stack)

| Gap | Issue | Dogfood stance |
|-----|-------|----------------|
| Per-conversation keep trays | **#131 A** | Global tray remains; **docstring-only** hook on `elyra/memory/keep_tray.py` (future key by `conversation_id` or `"_solo"`) — no entry field; no meal keep filter by conversation |
| Full presence product | **#131 B** | Soft “recently active” only (message-based preferred; session touch secondary); not full presence product; `presence.json` name reserved in design only |
| Real multi-user auth | **#131 C** | Session switch = **dogfood impersonation**; forgeable `client_id`; concurrent principals ≠ product security; `/chat` is local dogfood |

### Other C12 residuals (honest gaps, not #131 proper)

| Gap | Notes |
|-----|-------|
| Concurrent multi-pending waits | First pending only (`list_waits()[0]`); dogfood **one armed wait at a time** — do not rely on concurrent jim-wait + operator-wait correctness |
| Process-global phase / interject | Shared PresenceWorker phase; concurrent dogfood ≠ multi-moment (design §7A.10); idle-between-turns |
| Autotelic projective speak engine | Address via `speak(conversation_id=…)` exists; proactive engine separate |
| Multi-tenant SaaS / ACLs | Out — any client on :8787 can mint client_id and switch user |
| Topology push / SSE | Out — conversation list poll only (see Discovery bar residuals) |
| Instant group discovery | Out — poll lag **≤5s** is the pass bar, not zero lag |

---

## Hermetic pointers (not live dogfood)

Hermetics **landed** with the implement stack. Run as default CI / local unit evidence — **not** Gate B/C close.

| Suite / id | Role | Where |
|------------|------|--------|
| T16 / T16b (PR3a) | Two client_ids independent session; speaker from session not mismatched body | `tests/test_client_sessions.py` |
| T18 (PR3a) | Status missing/unknown client does not pollute registry (KD25) | `tests/test_client_sessions.py` |
| T8 (PR3c) | `social_kind=group` + missing conversation → `missing_conversation` (no DM demotion) | `tests/test_speak.py`, `tests/test_tools_social_wait.py` |
| T15 (PR3c) | Group deliver null `user_id` + glass row not operator | `tests/test_speak.py` |
| T9 (PR3c) | Group wait: member on `dm:self` → `matches_session` false; after PUT group → true | `tests/test_api_glass.py` |
| T11 (PR4) | last_compose inspect meta `conversation_id` | meal / glass_tail tests |
| Conversations store + REST | CRUD, ensure_dm, create_group, list filter | `tests/test_conversations.py`, `tests/test_conversations_api.py` |
| Product `/chat` shell | Static HTML product markers + footer | `tests/test_api_glass.py` |
| Skills catalog | Bundled `talk` still loads | `tests/test_skills_catalog.py` |
| List poll / create honesty (conv-tool UI) | app.js needles: poll constant, tick schedule, no silent empty catch, membership gate | `tests/test_api_glass.py` (and related app.js needles) |
| Topology tools (conv-tool) | `create_group` / `update_group` T-G1–T-G10 style pins | `tests/test_tools_conversations_group.py` |

```bash
# Representative hermetic slice (no live PE)
pytest tests/test_client_sessions.py tests/test_conversations.py \
  tests/test_conversations_api.py tests/test_speak.py \
  tests/test_tools_social_wait.py tests/test_meal_glass_tail.py \
  tests/test_api_glass.py tests/test_skills_catalog.py \
  tests/test_tools_conversations_group.py -q
```

---

## Sign-off (fill after live dogfood)

| Field | Value |
|-------|--------|
| Operator | |
| Date | |
| Branch / commit | multi-user stack + `feature/elyra-conv-tool` (list poll + topology tools); pin SHA when live-run |
| Gate A (#1, #5) | ☐ pass / ☐ fail / ☐ n/a |
| Gate B (#2, #3, #7a) | ☐ pass / ☐ fail / ☐ n/a |
| Gate C (#2 map, #4, #7b) | ☐ pass / ☐ fail / ☐ n/a |
| Discovery D1–D4 | ☐ pass / ☐ fail / ☐ n/a (code on conv-tool; live open) |
| Tools T1–T4 | ☐ pass / ☐ fail / ☐ n/a (code on conv-tool; live open) |
| Closeout #6 residuals | ☑ documented in this file (operator still confirms UI copy live) |
| #131 not over-claimed | ☑ confirmed hooks-only (keep docstring; no auth/presence product) |
| Phase residual honesty | ☑ documented (idle-between-turns; multi-wait first-pending) |
| Forensic ≠ membership | ☑ documented (operator admin not auto-member; poll lag / no push residuals) |
| Notes | Hermetics landed multi-user + conv-tool UI/tools; **live Gate A/B/C and D/T bars intentionally open** |

---

## Related files

| Path | Role |
|------|------|
| [docs/design/glass/design-multi-user-conversations.md](../design/glass/design-multi-user-conversations.md) | Normative design v2 (KD21–25, acceptance 1–7, residuals) |
| [docs/design/glass/design-conversation-list-and-group-tools.md](../design/glass/design-conversation-list-and-group-tools.md) | List discovery poll + `create_group` / `update_group` (D1–D4, T1–T4) |
| [docs/state/time-and-identity.md](time-and-identity.md) | Self ≠ user, identity walls |
| [docs/state/architecture.md](architecture.md) | As-implemented runtime map (`conversations/`, client sessions) |
| [docs/state/stretch-1.md](stretch-1.md) | Presence / moment / do-loop contract |
| `skills/bundled/talk/SKILL.md` | Social skill — conversation address, groups; optional topology tools note |
| `tools/bundled/speak/TOOL.md` / `wait_user/TOOL.md` | `conversation_id` resolve + group notes |
| `tools/bundled/create_group/TOOL.md` / `update_group/TOOL.md` | Topology mutators (`kind: mutate`); explicit members only |
| `elyra/memory/keep_tray.py` | #131 A docstring-only future key hook |
| `data/runtime/client_sessions.json` | Per-client session registry (PR3a) |
| `data/runtime/glass_session.json` | Legacy; one-shot import only (KD22) |
| `data/conversations/` | Conversation store on disk |

Full PR stack and module contracts: design § **PR plan**. Tip law: [dev/branch-law.md](../dev/branch-law.md).
