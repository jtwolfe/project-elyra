# Usage tracking + SuperGrok pacing (operator notes)

| Field | Value |
|-------|--------|
| **Class** | STATE (operator notes) |
| **Audience** | Operators dogfooding Elyra on SuperGrok / Grok Build |
| **Status** | Operator-facing summary (code stack lands via usage-tracking PRs) |
| **Full design** | [`docs/design/usage/design-usage-tracking-supergrok-pacing.md`](../design/usage/design-usage-tracking-supergrok-pacing.md) |
| **Reorg** | **Leave here for PR4 STATE** — taxonomy targets `docs/state/usage-and-pacing.md` (not moved in PR2c) |

Short product notes for Glass, `usage.json`, and live smoke. **Do not treat this as the implementation spec** — formulas, schema, and PR stack live in the full design.

---

## Two meters

Elyra surfaces **two independent meters**. Do not conflate them with Grok Build’s session pie.

| Meter | What it is | Source of truth |
|-------|------------|-----------------|
| **SuperGrok weekly pool** | Account-wide credit use for the billing period (`creditUsagePercent` + period start/end) | Server billing poll (fail-soft) |
| **Elyra local week ledger `S`** | Cumulative Completions tokens Elyra recorded this week (`week_used_tokens`) | `data/runtime/usage.json` |

**Gates care about:** local week spend `S` vs budget `B`, and account weekly `%` vs `A_hard` (default 95%).  
**Not a gate:** Grok Build `/usage` session block (client Completions sum for that Build session only).

Glass primary = **Elyra week bar** + **SuperGrok pool %**. Day/hour are **soft** secondary labels by default (no hard brick unless you turn the flags on in toml).

---

## First SuperGrok period adoption (retains `S`)

While offline / pre-poll, the meter may use an ISO week id (`YYYY-Www`) as a provisional period.

On the **first successful** SuperGrok credits poll:

1. `period_authority` becomes `supergrok`
2. `period_id` becomes the server period (`start/end`)
3. **`week_used_tokens` (`S`) is unchanged** — no mid-week wipe

**Pace may jump** after that first poll even though `S` did not change: schedule basis (`H` / elapsed `t`) switches from ISO week to SuperGrok `period_start`…`period_end`. That is expected.

A **true roll** (zeros `S`) happens only later, when the server reports a **new** billing period id — not on ISO week rollover while SuperGrok-authoritative.

---

## Burst capacity (model A)

Burst is a **fixed overshoot cushion**, not a draining token bucket.

| Quantity | Formula |
|----------|---------|
| `BurstMax` | `k · (B / H)` (`burst_hours` × linear hourly rate) |
| `over` | `max(0, S − (B/H)·t)` |
| **Glass remaining** | **`max(0, BurstMax − over)`** |

- Remaining is **derived** on each snapshot; it grows back as time catches the linear schedule.
- Burst **does not** raise the hard week budget `B`.
- Hard stop at `S ≥ B` still applies even if burst remaining &gt; 0.

---

## Hard-stop override (kept)

Glass **hard-stop override** is unchanged in intent:

- **OFF (default):** hard stop blocks new Completions when week `S ≥ B` or account `% ≥ A_hard` (and optional day/hour hard flags if enabled).
- **ON:** model calls continue past budget limits; **usage is still recorded**.
- `PATCH /api/usage` mutates **only** `hard_stop_override`.

Override survives period rolls. Would-be hard stop still shows on the banner when override is ON.

---

## One supervisor per `ELYRA_HOME`

**Operability constraint:** run **one** Elyra supervisor process per `ELYRA_HOME`.

`usage.json` uses process-local locking + atomic replace. Multi-process multi-writer is a **non-goal**. Two supervisors sharing the same home can corrupt the ledger or race the override flag.

---

## Operator dogfood checklist

Run with a real SuperGrok / Grok Build session (`elyra start`, provider xai, credentials ok). Continuous may stay OFF. Compare Status / Glass usage card and, if needed, `data/runtime/usage.json` before/after.

- [ ] **First poll retains `S`:** after first successful credits poll, `period_authority=supergrok` and **`week_used_tokens` unchanged** vs pre-poll.
- [ ] **Pace may jump on adopt:** pace badge / ratio can change on that first poll even though `S` is unchanged — expected (`H`/`t` basis switches to SuperGrok period).
- [ ] **Day soft:** day soft-exhausted with week under `B` and day/hour hard flags off → badge **ok** (or pace), **not** hard-stop; override not required.
- [ ] **Override:** ON/OFF still works; PATCH only toggles `hard_stop_override`; recording continues under override.
- [ ] **Burst formula:** burst text moves with overshoot — remaining = `max(0, BurstMax − over)` — not a stuck full bucket after spend spikes.
- [ ] **Non-blocking status:** Status panel stays responsive; billing poll must not hitch `GET /api/status` for multi-second waits.

Pass = all boxes green for the week you care about. Live smoke is **operator-owned** (not a CI gate). Full design test matrix and settings defaults: [design-usage-tracking-supergrok-pacing.md](../design/usage/design-usage-tracking-supergrok-pacing.md).

### Example toml (optional knobs)

```toml
[usage]
enabled = true
weekly_allowed_tokens = 5_000_000
# weekly_allowed_fraction = 0.50  # policy/display only — does not scale hard budget B
day_hard_stop_enabled = false
hour_hard_stop_enabled = false
account_hard_stop_percent = 95.0
pace_yellow_ratio = 1.0
pace_red_ratio = 1.5
burst_hours = 4.0
credits_poll_enabled = true
```

---

## See also

| Doc | Role |
|-----|------|
| [design-usage-tracking-supergrok-pacing.md](../design/usage/design-usage-tracking-supergrok-pacing.md) | Full design (schema, algorithms, PR stack, KD table) |
| [phase-0.md](../design/grok-improvement-plan/phase-0.md) | Original Phase 0 usage meter concept |
| [phase-0-execution.md](../design/grok-improvement-plan/phase-0-execution.md) | Phase 0 execution + earlier live smoke |
| Root [README.md](../../README.md) | Product path: xAI Grok + usage meter |
