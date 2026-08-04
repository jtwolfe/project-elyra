# Sandbox fitness — operator smoke checklist

| Field | Value |
|-------|--------|
| **Class** | STATE (ops extract) |
| **Status** | Active |
| **Audience** | Operators claiming isolation / create-tool works end-to-end |
| **Normative full design** | [design/capability/harness-sandbox-fitness.md](../design/capability/harness-sandbox-fitness.md) §H6 |
| **Normative?** | Prefer code on `working`; this is a short ops extract only |

> Extracted from harness **H6** for day-to-day use. Full H1–H6 design+PR plan stays DESIGN (KD14).  
> Live create-tool smoke is **operator-owned** — not a CI gate. Do **not** record “H6 live green” in-repo unless you actually ran it against real MSB + Grok.

---

## Ownership

| Layer | Owner | Proves |
|-------|-------|--------|
| Hermetic / CI | Implementers | Lifecycle, host-stub, create-tool gates, status shape |
| **This checklist** | Operator | Warm MSB, `mount_ready` + `pyenv_ready`, guest verify → promote → call |

Continuous remains default **OFF** during this smoke.

---

## Hard readiness (before create-tool)

| Flag | Meaning | Observe |
|------|---------|---------|
| **`mount_ready`** | Host tree + guest mount OK; guest `python3` works | `GET /api/status` → `sandbox.mount_ready` |
| **`pyenv_ready`** | Curated env (+ pytest) ready | `sandbox.pyenv_ready` |
| Product **`ready`** | `mount_ready && (pyenv_ready \|\| isolation off)` | Isolation-on path needs **both** |

`elyra start` must not hang for minutes (async warm). Poll status until both flags true before draft/verify/promote.

---

## Preflight

- [ ] `pip install -e '.[sandbox]'` and `python -c 'import microsandbox'` OK  
- [ ] `./scripts/setup-microsandbox.sh --doctor-only` acceptable (KVM / virt as applicable)  
- [ ] `elyra start` → chat up; poll until `mount_ready` + `pyenv_ready`  
- [ ] Provider xAI, credential OK; continuous **OFF**; usage not hard-stopped  
- [ ] Note glass sandbox pill: **ready** / **warming** / **unusable**

---

## Create-tool path (pass = all required green)

| # | Check | Pass criteria |
|---|--------|----------------|
| 0 | Readiness | Both flags true; pill **ready**; continuous off |
| 1 | Status surface | Pill + `/api/status` agree; no secrets/host abs paths in JSON |
| 2 | FS truth | `list_dir` on `.` shows sandbox seed layout — not host home |
| 3 | Guest python | `run`: `python3 -c 'print(1)'` in guest |
| 4 | Curated import | Guest: `import requests` and `import pytest` |
| 5 | Draft | `install_tool_draft` tiny tool with tests; install `ok` |
| 6 | FS negative | FS tools do **not** list host `tools/drafts/` |
| 7 | Verify | `verify_tool` green via **guest** pytest |
| 8 | Promote | Tool in catalog and callable |
| 9 | Call | Promoted tool once succeeds (not host-fish) |
| 12 | Shutdown | SIGINT stop-only; restart reconnects without wiping seed tree |
| 13 | Continuous | Still **OFF** after session |

Optional advanced: negative isolation (MSB down → structured `sandbox_unavailable`) and thrash non-regression (no host-fish absolute drafts paths).

---

## After smoke

| Outcome | Action |
|---------|--------|
| All required green | You may claim live isolation smoke for this machine/session |
| Failed / degraded | Document reason; fix env; re-run from readiness — do not flip product docs to “live green” |
| Hermetic CI only | **Insufficient** for create-tool dogfood claim |

Hermetic substitutes and full policy tables: [harness-sandbox-fitness.md](../design/capability/harness-sandbox-fitness.md) §H6.

Related: [tools-and-skills.md](tools-and-skills.md) · [stretch-1.md](stretch-1.md) · root [README.md](../../README.md) install/sandbox.
