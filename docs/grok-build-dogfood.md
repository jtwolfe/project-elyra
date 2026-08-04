# Grok Build instrument — operator dogfood checklist (PR6 + functionalization)

| Field | Value |
|-------|--------|
| **Audience** | Operators dogfooding Phase 1 `grok_build` |
| **Normative matrix** | [design-grok-build-tool.md](design/grok-build/design-grok-build-tool.md) § **Dogfood matrix / acceptance** (D1–D13) |
| **Functionalization** | [design-grok-build-functionalization.md](design/grok-build/design-grok-build-functionalization.md) (auth seed + zombie/finalize honesty) |
| **Live tests** | `tests/test_live_grok_build.py` (`@pytest.mark.live_grok`) — skeletons only |
| **Spike (D7)** | [grok-build-headless-spike.md](design/grok-build/grok-build-headless-spike.md) (PR0a) · design **KD16** |
| **Pinned CLI** | **Grok 0.2.118** (auth.json ExternalBinary cold-start + provider refresh behavior verified against this version) |
| **Completion tip** | Advanced `feature/grok-build-tool` (not `main` / not house `working` tip law change) |
| **No smoke script** | **Checklist only** — do not ship / require `scripts/smoke_grok_build_auth.py` |
| **Merge gate (later)** | **D3 and D6 green before PR8** merge to `working` (operator/product after D1) |

Live suite is **operator-owned** — never default CI. Hermetic pack::

```bash
pytest -m 'not llm and not live_grok'
```

Skeleton discover + validate only (no heavy spend)::

```bash
ELYRA_LIVE_GROK=1 pytest tests/test_live_grok_build.py -q
```

Prerequisites: host `grok` binary (**0.2.118** preferred; `GROK_BIN` or `~/.grok/bin` / PATH), PE `xai_oauth` for spawn modes, continuous work preferably **OFF**. For D6 later: `working` branch preflight / plain-git. For full D1–D13 on the advanced tip: run against a tip that includes functionalization (auth seed + zombie reaper).

---

## Truth notes (post live-dogfood failure 2026-08-03)

Live implement job `fdaf572ce9454bc299b2e246330e4d8f` failed auth immediately yet looked `running` for a long time. Root causes and fixes (see functionalization design):

| Issue | Reality on Grok **0.2.118** | Fix (instrument) |
|-------|-----------------------------|------------------|
| **Cold-start auth** | Headless `-p` does **not** mint a session from `auth_provider_command` alone when `auth.json` is absent. Provider is for **refresh** of an existing ExternalBinary session. | Seed access-only `GROK_HOME/auth.json` (`auth_mode=external`, no `refresh_token`) + keep live provider for mid-run mint. Single `ensure_fresh_access` per spawn. |
| **Zombie / finalize** | Child exits (“Not signed in”) but becomes a **zombie**; `os.kill(pid, 0)` treats zombies as alive → reaper never finalizes; poll stays `running`. | Zombie-aware `is_pid_alive` + mandatory `waitpid` reaping; poll-path opportunistic finalize. |
| **False success** | `exit_code=None` into harvest could map **completed**. | Dead + unknown exit ⇒ **failed** (never completed); gated auth/headless classify before success mapping; async finalize redacts access. |
| **cwd footgun** | Guest-relative `cwd` → host VCS jail `not_a_repo`. | TOOL.md / skills: **host-absolute** path under jail. |

**Do not** treat background-launch ack text as a completed report (especially D7).

---

## Gates (restated)

| Gate | Rule |
|------|------|
| **CI default** | `pytest -m 'not llm and not live_grok'` green — no live `grok` calls |
| **This workstream (functionalization)** | Hermetic units green; **D1 required green** on advanced `feature/grok-build-tool` tip **before any PR8 → `working` discussion** |
| **Preferred with D1** | **D2, D8, D11, D13** when practical |
| **H-spine / PR8 (later)** | **D3 + D6** green — **operator/product-owned after D1**; not a hard gate of the functionalization stack alone |
| **Phase 1 callable surface (full)** | **D1–D6 + D8–D13** green (**D7 per spike**) |
| **D7 enable non-experimental** | Signed strategy (1) or (2) in [grok-build-headless-spike.md](design/grok-build/grok-build-headless-spike.md); until then **experimental only** |
| **Smoke** | **No** dedicated smoke script — operator path = hermetic pytest + this checklist |

Manual D1 (example, continuous work OFF)::

```text
grok_build mode=prompt cwd=<host-abs repo under jail> prompt="summarize README in 3 bullets" async=false
# Expect: ok summary; no token leakage in result.json
```

---

## Checklist (links design dogfood matrix)

Mark each item after a real PE or headless run. Pass criteria match the design table exactly.

### Core modes

- [ ] **D1 — `mode=prompt` “summarize README” in repo** ⚠️ **required on advanced feature tip before PR8 discussion**  
  Pass: ok summary; **no token leakage** in `result.json`. Use **host-absolute** `cwd`.  
  Design: [Dogfood matrix D1](design/grok-build/design-grok-build-tool.md). Functionalization: auth seed must allow cold-start.

- [ ] **D2 — Missing OAuth** (preferred with D1)  
  Pass: `auth_unavailable`; task can block honestly.  
  Design: [Dogfood matrix D2](design/grok-build/design-grok-build-tool.md).

- [ ] **D3 — `mode=design` small fixture (async)** ⚠️ **required before PR8** (operator later, after D1)  
  Pass: `job_id` → poll `completed` or `needs_human`; `artifacts/design.md` present; **presence worker not blocked** for ~90m.  
  Design: [Dogfood matrix D3](design/grok-build/design-grok-build-tool.md). H-spine readiness needs D3+D6.

- [ ] **D4 — `mode=implement` effort=1 tiny change (async)**  
  Pass: job completes; tests green; branch not `main` / `working` tip hijack.  
  Design: [Dogfood matrix D4](design/grok-build/design-grok-build-tool.md).

- [ ] **D5 — `mode=review` local (async)**  
  Pass: `artifacts/review.md`; honest findings.  
  Design: [Dogfood matrix D5](design/grok-build/design-grok-build-tool.md).

- [ ] **D6 — `mode=execute_plan` mini design (1–2 PRs), plain-git** ⚠️ **required before PR8** (operator later, after D1)  
  Pass: PE preflight `working`; meta argv has `--no-graphite`; stack base `working` (or **documented residual** if Grok ignored prose); presence free during run.  
  Design: [Dogfood matrix D6](design/grok-build/design-grok-build-tool.md). H-spine readiness needs D3+D6.

### Deep research (spike-gated)

- [ ] **D7 — `mode=deep_research`** — **experimental only** until PR0a signs  
  Pass: **only after PR0a** signs strategy **(1)** or **(2)**; otherwise honest `mode_experimental` if not enabled.  
  Do **not** treat background-launch ack text as a completed report.  
  Follow: [grok-build-headless-spike.md](design/grok-build/grok-build-headless-spike.md) operator checklist + sign-off block; design **[KD16](design/grok-build/design-grok-build-tool.md)** / [Dogfood matrix D7](design/grok-build/design-grok-build-tool.md).  
  Ship default remains strategy **(3)** (`mode_experimental`) until signed.

### Cross-cutting

- [ ] **D8 — Usage** (preferred with D1)  
  Pass: headless-shaped usage recorded via adapter; hard-stop prevents launch.  
  Design: [Dogfood matrix D8](design/grok-build/design-grok-build-tool.md).

- [ ] **D9 — Skill routing**  
  Pass: self-improve **M** → `implement` without `execute_plan`; async poll steps followed.  
  Design: [Dogfood matrix D9](design/grok-build/design-grok-build-tool.md).

- [ ] **D10 — Guest / secret_env law**  
  Pass: no OAuth in `secret_env`; guest paths clean.  
  Mostly hermetic unit coverage; still confirm live path never assigns access into `ctx.extras["secret_env"]`.  
  Design: [Dogfood matrix D10](design/grok-build/design-grok-build-tool.md).

- [ ] **D11 — Skill seed** (preferred with D1)  
  Pass: isolated `GROK_HOME` resolves **design** + **implement** (discover gate).  
  Skeleton: `find_grok_binary` / `find_real_bundled` under `ELYRA_LIVE_GROK=1`.  
  Design: [Dogfood matrix D11](design/grok-build/design-grok-build-tool.md).

- [ ] **D12 — Mid-run auth**  
  Pass: multi-hour or forced `GROK_AUTH_EXPIRED` path gets fresh access (mock or live). Seeded ExternalBinary session should re-run provider on expiry without writing `refresh_token`.  
  Design: [Dogfood matrix D12](design/grok-build/design-grok-build-tool.md); spike auth notes; functionalization KD-F2.

- [ ] **D13 — Reaper restart** (preferred with D1)  
  Pass: kill PE mid-job → on restart job `interrupted`, tokens shredded. Auth-death jobs must terminalize promptly (zombie reaped; not wall-timeout `running`).  
  Design: [Dogfood matrix D13](design/grok-build/design-grok-build-tool.md); functionalization KD-F6/F14.

---

## Related files

| Path | Role |
|------|------|
| `tests/test_live_grok_build.py` | Opt-in skeletons (`ELYRA_LIVE_GROK=1`); D1–D13 comments; **not** a substitute for this checklist |
| `tests/test_builtin_grok_build.py` | Hermetic tool handler |
| `tests/test_instrument_*.py` | Pure instrument unit coverage (auth seed, reaper, jobs, …) |
| `docs/design/grok-build/grok-build-headless-spike.md` | PR0a deep_research / human-gate spike (D7) |
| `docs/design/grok-build/design-grok-build-functionalization.md` | Auth seed + zombie/finalize design (PR-A…E) |
| `docs/tools-and-skills.md` | Catalog surface: `grok_build` on feature tip |
| `elyra/instrument/discover.py` | Binary + skill seed gate |
| `elyra/instrument/auth_handoff.py` | Isolated home + access-only `auth.json` seed |
| `elyra/instrument/jobs.py` / `reaper.py` | Liveness, reap, finalize |
| `elyra/instrument/validate.py` | Mode-conditional dry-run validation |

Full PR stack and module contracts: [design-grok-build-tool.md](design/grok-build/design-grok-build-tool.md) · summary [design-grok-build-tool-summary.md](design/grok-build/design-grok-build-tool-summary.md). Tip law: [dev/branch-law.md](dev/branch-law.md).
