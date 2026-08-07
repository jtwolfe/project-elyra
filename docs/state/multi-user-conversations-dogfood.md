# Multi-user conversations — operator dogfood checklist (C12)

| Field | Value |
|-------|--------|
| **Class** | STATE |
| **Audience** | Operators dogfooding C12 multi-user + group chat continuity |
| **Status** | Active (living checklist) — **placeholders** until implement stack lands |
| **Normative?** | No — prefer code on `working`; boxes are ops evidence, not product claims |
| **Last verified** | — (implement not yet on tip; checklist structure only) |
| **Normative design** | [design-multi-user-conversations.md](../design/glass/design-multi-user-conversations.md) (**v2 — concurrent `/chat` client sessions**) |
| **Related issues** | [#118](https://github.com/jtwolfe/project-elyra/issues/118) C12 packaging bar; [#127](https://github.com/jtwolfe/project-elyra/issues/127) / [#128](https://github.com/jtwolfe/project-elyra/issues/128) / [#129](https://github.com/jtwolfe/project-elyra/issues/129); residual gate [#131](https://github.com/jtwolfe/project-elyra/issues/131) (**hooks only**) |
| **Stack base** | `working` @ `598282f` (land target `working`) |
| **Claim today** | **Docs placeholders only.** No multi-user conversation code claim. Do **not** mark acceptance rows green until implement + live dogfood. Concurrent principals (KD21–25) are design-locked; not product auth. |

> Maps **1:1** to design § **Demo / dogfood acceptance (C12-level)**. Pass criteria match that table exactly.  
> Live dogfood is **operator-owned** — not a CI gate. Hermetic suites (T16/T16b, etc.) prove API isolation; they do **not** close Gate B/C alone.

---

## Truth notes

| Claim | Status |
|-------|--------|
| Conversation store + message `conversation_id` | **Not yet** (PR2+) |
| Per-client session registry (KD21–25) | **Not yet** (PR3a) |
| glass_tail conversation scope / meal isolation | **Not yet** (PR4) — Gate A |
| Operator Glass multi-convo switch | **Not yet** (PR6) — Gate B |
| `/chat` product shell + honesty footer | **Not yet** (PR7) — Gate C |
| Concurrent multi-window principals | **Not yet** — #7 B partial / C formal |
| #131 keep trays / full presence / real auth | **Out-of-implement** — hooks only (see residuals) |
| Process-global PresenceWorker phase | **Accepted residual** — concurrent ≠ multi-moment |
| Gate B / product multi-tenant security | **Not** this checklist’s done bar |

**Honest copy (use the same phrases as UI):**

| Surface | Phrase |
|---------|--------|
| Operator rail | **“Session user (impersonate)”** |
| `/chat` footer | **“local dogfood — not authenticated.”** |
| Optional muted note | **“per-tab client session — not login.”** |

`client_id` is **forgeable** dogfood binding, not a credential (#131 C).

---

## Gates A / B / C (placeholders)

Map to design **Dogfood gates**. Mark a gate only after its acceptance rows pass on a real PE/host run.

| Gate | After (design) | Accepts rows | Inspect / proof | Status |
|------|----------------|--------------|-----------------|--------|
| **A — Meal truth** | PR4 (+ PR3b/c) | **#1** isolation, **#5** solo continuous | last_compose `glass_tail_meta.conversation_id` + packed speakers; **not** unscoped meal inspect | ☐ open |
| **B — Operator UX** | PR6 (+ PR3a) | **#3** switch, **#2** group create/send; **partial #7** concurrent on operator `/` (two windows, no product shell) | Glass UI + API; hermetic T16/T16b | ☐ open |
| **C — Demo sugar** | PR5 + PR7 | **#2** participants map, **#4** `/chat`, **formal #7** concurrent `/chat` + chrome | Orient + product shell multi-client | ☐ open |
| **Closeout** | PR8 | **#6** gaps honesty (+ phase residual honesty) | This checklist filled; residuals explicit | ☐ open |

Gate A does **not** require participants map (PR5) or `/chat` (PR7). Concurrent session hermetics land with **PR3a**. Gate B can partial-prove multi-window on operator `/`. Gate C formalizes `/chat` + honesty footer + full acceptance #7.

---

## Prep

- [ ] Tip includes multi-user stack (or pin SHA) on `working` / feature land; hermetic suite green where applicable
- [ ] `elyra start` → Glass `http://127.0.0.1:8787/` (and later `/chat`)
- [ ] ≥2 identity users available (e.g. **jim**, **sam**) via UsersStore / operator seed
- [ ] Continuous work **OFF** unless exercising #5 solo continuous
- [ ] Prefer **idle between turns** for multi-window demos (shared PresenceWorker phase residual)
- [ ] For concurrent #7: **separate windows** (or remote browsers) — tab-duplicate may clone `sessionStorage` client_id (KD21 caveat)
- [ ] Confirm UI honesty copy: impersonate rail / dogfood footer (after PR6/PR7)

---

## Acceptance checklist (1:1 design table)

Mark each item after a real operator run. Pass criteria match [design acceptance](../design/glass/design-multi-user-conversations.md#demo--dogfood-acceptance-c12-level).

### #1 — ≥2 users, separate DMs (Gate A)

- [ ] **#1** Switch jim ↔ sam (**per client**); each DM history isolated  
  Pass: glass_tail on jim wake has **no** sam lines via **last_compose** snapshot (`glass_tail_meta.conversation_id` + packed speakers). Do **not** use unscoped `_compose_meal_for_inspect` to claim isolation.  
  Design: acceptance #1 · Gate **A**.

### #2 — ≥1 group, 2+ members (Gate B → C)

- [ ] **#2** Create group; send as member A; speak lands in group; attribution labels  
  Pass: participants list present (**after PR5**); group assistant rows use role display (null `user_id` / KD20) — not stamped operator.  
  Design: acceptance #2 · Gate **B** then **C**.

### #3 — Operator switch without reset (Gate B)

- [ ] **#3** Change user **and** conversation mid-session **on one client**  
  Pass: auto-DM vs keep-group membership correct; **other clients unaffected** (no process-global stomp).  
  Design: acceptance #3 · Gate **B**.

### #4 — `/chat` usable (Gate C)

- [ ] **#4** Private Chat + one group send/receive; honesty footer visible  
  Pass: product chrome; footer **“local dogfood — not authenticated.”** (optional per-tab client note).  
  Design: acceptance #4 · Gate **C**.

### #5 — Solo continuous (Gate A)

- [ ] **#5** Some client has active DM; timer/continuous wake payload **null** conversation; meal glass_tail **empty**  
  Pass: continuous/timer does not inherit any client’s DM binding into glass_tail.  
  Design: acceptance #5 · Gate **A**.

### #6 — Explicit gaps (Closeout)

- [ ] **#6** Residuals documented and UI copy consistent  
  Pass: #131 + multi-wait residual + **process-global phase/interject residual** listed; **impersonation ≠ auth** same copy; **client_id ≠ login**.  
  Design: acceptance #6 · Closeout (PR8). See [Out-of-implement / residuals](#out-of-implement--residuals) below.

### #7 — Concurrent multi-principal (Gate B partial / C formal)

- [ ] **#7a — B partial (operator `/`)** Two windows and/or remote browser: Jim Private Chat + Sam same group **simultaneously** (no product shell required)  
  Pass: messages appear in both for shared conversation; **no last-writer-wins** session stomp; waits match per client session membership (`matches_session` when client known).  
  Design: acceptance #7 · Gate **B partial**.

- [ ] **#7b — C formal (`/chat`)** Same concurrent bar on `/chat` + chrome + honesty footer  
  Pass: formal multi-client demo path; checklist evidence recorded.  
  Design: acceptance #7 · Gate **C formal**. API hermetic T16/T16b land with PR3a (not a substitute for live #7).

#### #7 notes — concurrent residual honesty (shared PresenceWorker phase)

**Guaranteed by v2 (when implemented):**

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

## Out-of-implement / residuals

### #131 — hooks only (do not implement in this stack)

| Gap | Issue | Dogfood stance |
|-----|-------|----------------|
| Per-conversation keep trays | **#131 A** | Global tray remains; **docstring-only** hook — no entry field; no meal keep filter by conversation |
| Full presence product | **#131 B** | Soft “recently active” only (message-based preferred); not full presence product |
| Real multi-user auth | **#131 C** | Session switch = **dogfood impersonation**; forgeable `client_id`; concurrent principals ≠ product security |

### Other C12 residuals (honest gaps, not #131 proper)

| Gap | Notes |
|-----|-------|
| Concurrent multi-pending waits | First pending only; dogfood **one armed wait at a time** |
| Process-global phase / interject | Shared PresenceWorker phase; concurrent dogfood ≠ multi-moment (design §7A.10) |
| Autotelic projective speak engine | Address may exist later; engine separate |
| Multi-tenant SaaS / ACLs | Out — any client on :8787 can mint client_id and switch user |

---

## Hermetic pointers (not live dogfood)

| Suite / id | Role |
|------------|------|
| T16 / T16b (PR3a) | Two client_ids independent session; speaker from session not mismatched body |
| T18 (PR3a) | Status missing/unknown client does not pollute registry (KD25) |
| T15 (PR3c) | Group deliver null `user_id` + glass row |
| T11 (PR4) | last_compose inspect meta `conversation_id` |

Run hermetic default CI as usual. Live rows above remain operator-owned.

---

## Sign-off (fill at closeout)

| Field | Value |
|-------|--------|
| Operator | |
| Date | |
| Branch / commit | |
| Gate A (#1, #5) | ☐ pass / ☐ fail / ☐ n/a |
| Gate B (#2, #3, #7a) | ☐ pass / ☐ fail / ☐ n/a |
| Gate C (#2 map, #4, #7b) | ☐ pass / ☐ fail / ☐ n/a |
| Closeout #6 residuals | ☐ documented |
| #131 not over-claimed | ☐ confirmed hooks-only |
| Phase residual honesty | ☐ confirmed (idle-between-turns noted) |
| Notes | |

---

## Related files

| Path | Role |
|------|------|
| [docs/design/glass/design-multi-user-conversations.md](../design/glass/design-multi-user-conversations.md) | Normative design v2 (KD21–25, acceptance 1–7, residuals) |
| [docs/state/time-and-identity.md](time-and-identity.md) | Self ≠ user, identity walls |
| [docs/state/architecture.md](architecture.md) | As-implemented runtime map |
| [docs/state/stretch-1.md](stretch-1.md) | Presence / moment / do-loop contract |
| `data/runtime/client_sessions.json` | Per-client session registry (after PR3a) |
| `data/runtime/glass_session.json` | Legacy; one-shot import only (KD22) |

Full PR stack and module contracts: design § **PR plan**. Tip law: [dev/branch-law.md](../dev/branch-law.md).
