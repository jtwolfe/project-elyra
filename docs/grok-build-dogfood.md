# Grok Build instrument — operator dogfood checklist (PR6)

| Field | Value |
|-------|--------|
| **Audience** | Operators dogfooding Phase 1 `grok_build` |
| **Normative matrix** | [design-grok-build-tool.md](design-grok-build-tool.md) § **Dogfood matrix / acceptance** (D1–D13) |
| **Live tests** | `tests/test_live_grok_build.py` (`@pytest.mark.live_grok`) |
| **Spike (D7)** | [grok-build-headless-spike.md](grok-build-headless-spike.md) (PR0a) · design **KD16** |
| **Merge gate** | **D3 and D6 green before PR8** merge to `working` |

Live suite is **operator-owned** — never default CI. Hermetic pack::

```bash
pytest -m 'not llm and not live_grok'
```

Skeleton discover + validate only (no heavy spend)::

```bash
ELYRA_LIVE_GROK=1 pytest tests/test_live_grok_build.py -q
```

Prerequisites: host `grok` binary (`GROK_BIN` or `~/.grok/bin` / PATH), PE `xai_oauth` for spawn modes, `working` branch for D6, continuous work preferably **OFF**.

---

## Checklist (links design dogfood matrix)

Mark each item after a real PE or headless run. Pass criteria match the design table exactly.

### Core modes

- [ ] **D1 — `mode=prompt` “summarize README” in repo**  
  Pass: ok summary; **no token leakage** in `result.json`.  
  Design: [Dogfood matrix D1](design-grok-build-tool.md).

- [ ] **D2 — Missing OAuth**  
  Pass: `auth_unavailable`; task can block honestly.  
  Design: [Dogfood matrix D2](design-grok-build-tool.md).

- [ ] **D3 — `mode=design` small fixture (async)** ⚠️ **required before PR8**  
  Pass: `job_id` → poll `completed` or `needs_human`; `artifacts/design.md` present; **presence worker not blocked** for ~90m.  
  Design: [Dogfood matrix D3](design-grok-build-tool.md). H-spine readiness needs D3+D6.

- [ ] **D4 — `mode=implement` effort=1 tiny change (async)**  
  Pass: job completes; tests green; branch not `main` / `working` tip hijack.  
  Design: [Dogfood matrix D4](design-grok-build-tool.md).

- [ ] **D5 — `mode=review` local (async)**  
  Pass: `artifacts/review.md`; honest findings.  
  Design: [Dogfood matrix D5](design-grok-build-tool.md).

- [ ] **D6 — `mode=execute_plan` mini design (1–2 PRs), plain-git** ⚠️ **required before PR8**  
  Pass: PE preflight `working`; meta argv has `--no-graphite`; stack base `working` (or **documented residual** if Grok ignored prose); presence free during run.  
  Design: [Dogfood matrix D6](design-grok-build-tool.md). H-spine readiness needs D3+D6.

### Deep research (spike-gated)

- [ ] **D7 — `mode=deep_research`** — **per spike doc (PR0a)**  
  Pass: **only after PR0a** signs strategy **(1)** or **(2)**; otherwise honest `mode_experimental` if not enabled.  
  Do **not** treat background-launch ack text as a completed report.  
  Follow: [grok-build-headless-spike.md](grok-build-headless-spike.md) operator checklist + sign-off block; design **[KD16](design-grok-build-tool.md)** / [Dogfood matrix D7](design-grok-build-tool.md).  
  Ship default remains strategy **(3)** (`mode_experimental`) until signed.

### Cross-cutting

- [ ] **D8 — Usage**  
  Pass: headless-shaped usage recorded via adapter; hard-stop prevents launch.  
  Design: [Dogfood matrix D8](design-grok-build-tool.md).

- [ ] **D9 — Skill routing**  
  Pass: self-improve **M** → `implement` without `execute_plan`; async poll steps followed.  
  Design: [Dogfood matrix D9](design-grok-build-tool.md).

- [ ] **D10 — Guest / secret_env law**  
  Pass: no OAuth in `secret_env`; guest paths clean.  
  Mostly hermetic unit coverage; still confirm live path never assigns access into `ctx.extras["secret_env"]`.  
  Design: [Dogfood matrix D10](design-grok-build-tool.md).

- [ ] **D11 — Skill seed**  
  Pass: isolated `GROK_HOME` resolves **design** + **implement** (discover gate).  
  Skeleton: `find_grok_binary` / `find_real_bundled` under `ELYRA_LIVE_GROK=1`.  
  Design: [Dogfood matrix D11](design-grok-build-tool.md).

- [ ] **D12 — Mid-run auth**  
  Pass: multi-hour or forced `GROK_AUTH_EXPIRED` path gets fresh access (mock or live).  
  Design: [Dogfood matrix D12](design-grok-build-tool.md); spike auth notes.

- [ ] **D13 — Reaper restart**  
  Pass: kill PE mid-job → on restart job `interrupted`, tokens shredded.  
  Design: [Dogfood matrix D13](design-grok-build-tool.md).

---

## Gates and done-when

| Gate | Rule |
|------|------|
| **CI default** | `pytest -m 'not llm and not live_grok'` green — no live `grok` calls |
| **Phase 1 callable surface** | **D1–D6 + D8–D13** green (**D7 per spike**) |
| **H-spine ready** | **D3 + D6** green |
| **PR8 merge → `working`** | PR0–PR7 + above; **do not merge without D3/D6** |
| **D7 enable non-experimental** | Signed strategy (1) or (2) in [grok-build-headless-spike.md](grok-build-headless-spike.md) |

Full PR stack and module contracts: [design-grok-build-tool.md](design-grok-build-tool.md) · summary [design-grok-build-tool-summary.md](design-grok-build-tool-summary.md). Tip law: [branch-law.md](branch-law.md).

---

## Related files

| Path | Role |
|------|------|
| `tests/test_live_grok_build.py` | Opt-in skeletons (`ELYRA_LIVE_GROK=1`); D1–D13 comments |
| `tests/test_builtin_grok_build.py` | Hermetic tool handler |
| `tests/test_instrument_*.py` | Pure instrument unit coverage |
| `docs/grok-build-headless-spike.md` | PR0a deep_research / human-gate spike (D7) |
| `elyra/instrument/discover.py` | Binary + skill seed gate |
| `elyra/instrument/validate.py` | Mode-conditional dry-run validation |
