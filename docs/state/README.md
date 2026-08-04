# STATE — living product behaviour

| Field | Value |
|-------|--------|
| **Class** | STATE |
| **Audience** | Operators / users |
| **Role** | As-implemented behaviour, run/deploy, honest limits |
| **Conflict** | Prefer **code on `working`** over this prose |
| **Hub** | [docs/README.md](../README.md) |
| **Root entry** | [README.md](../../README.md) (primary operator onboarding) |

Living docs for **what the product does today**. Designs and PR plans live under [docs/design/](../design/). Process law under [docs/dev/](../dev/). Short north stars under [docs/goal/](../goal/).

---

## Architecture & runtime

| Doc | Role |
|-----|------|
| [architecture.md](architecture.md) | **As-implemented map** — process topology, `elyra/*` packages, data layout, limits |
| [stretch-1.md](stretch-1.md) | **Runtime contract** — presence, moments, do-loop, done-when (basename kept; still law) |
| [overview.md](overview.md) | Big picture, glossary, Stretch 1 vs 2 |
| [sandbox-fitness-checklist.md](sandbox-fitness-checklist.md) | Short operator isolation / create-tool smoke (H6 extract) |

## Tools, identity, ops

| Doc | Role |
|-----|------|
| [tools-and-skills.md](tools-and-skills.md) | Packages, base catalog, VCS, search/browser/secrets, dogfood checklist |
| [time-and-identity.md](time-and-identity.md) | Self ≠ user, draft/promote, work-origin USER, time layers |
| [known-bugs.md](known-bugs.md) | Deferred product bugs / dogfood backlog |
| [grok-build-dogfood.md](grok-build-dogfood.md) | Operator checklist for current Grok Build instrument |
| [usage-and-pacing.md](usage-and-pacing.md) | SuperGrok pool vs Elyra ledger, burst, override |

## Memory (as implemented)

| Doc | Role |
|-----|------|
| [memory/README.md](memory/README.md) | Phase honesty — Phase 1 done; 2/2a code done (dogfood pending); Phase 3 experimental |
| [memory/architecture/phase-1-temporal.md](memory/architecture/phase-1-temporal.md) | Temporal memory manual |
| [memory/architecture/phase-2-semantic.md](memory/architecture/phase-2-semantic.md) | Semantic memory manual |
| [memory/architecture/phase-2a-directed-traversal.md](memory/architecture/phase-2a-directed-traversal.md) | Directed traversal manual |

Memory **designs** (not manuals): [docs/design/memory/](../design/memory/).

## Hardware dogfood (ROCm / Radeon VII)

| Doc | Role |
|-----|------|
| [radeon-vii/README.md](radeon-vii/README.md) | Operator start path (+ NOTES / VENV / STACK) |
| [radeon-vii/NOTES-DOGFOOD.md](radeon-vii/NOTES-DOGFOOD.md) | Switch / inject / encode dogfood notes |
| [radeon-vii/VENV-ROCM-SWITCH.md](radeon-vii/VENV-ROCM-SWITCH.md) | Venv ROCm switch runbook |
| [radeon-vii/STACK-INVENTORY.md](radeon-vii/STACK-INVENTORY.md) | Stack inventory |

ROCm smoke **scripts** remain under [docs/radeon-vii-dev/scripts/](../radeon-vii-dev/scripts/). **Freezes** live under [investigations/radeon-vii-freezes/](../investigations/radeon-vii-freezes/). ROCm design: [design/memory/design-rocm-venv-gpu-embed-smoke.md](../design/memory/design-rocm-venv-gpu-embed-smoke.md).

---

## Run (quick)

```bash
./scripts/setup_venv.sh && source .venv/bin/activate
pip install -e '.[sandbox]'   # optional but needed for guest isolation (default ON)
./scripts/setup-microsandbox.sh --doctor-only

elyra start              # API + UI (xAI Grok product default)
# http://127.0.0.1:8787/
```

Full install matrix and extras: root [README.md](../../README.md). Sandbox smoke: [sandbox-fitness-checklist.md](sandbox-fitness-checklist.md).

## Related freezes (not setup law)

| Path | Note |
|------|------|
| [docs/inference.md](../inference.md) | Gemma/llama freeze — product path is Grok + [usage-and-pacing.md](usage-and-pacing.md) |
| [docs/live-eval.md](../live-eval.md) | Historical live protocol |
| [docs/investigations/](../investigations/) | Sealed forensic packages (lance, meal continuity, radeon freezes) |
