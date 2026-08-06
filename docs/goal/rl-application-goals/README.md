# RL application goals

| Field | Value |
|-------|--------|
| **Class** | GOAL |
| **Status** | Draft |
| **Audience** | Product |
| **Normative?** | No — aspirational product direction; prefer code on `working` and v0.1 claim docs when conflict |
| **Created** | 2026-08-06 |
| **Related** | [programme.md](../programme.md), [v0.1.md](../v0.1.md), [stretch-2-north-star.md](../stretch-2-north-star.md), [design-identity-self-other-multi-user.md](../../design/identity/design-identity-self-other-multi-user.md), [docs/state/memory/README.md](../../state/memory/README.md) |

> **Posture.** These documents describe what the Elyra architecture *could* support with further polish and focused work on the existing foundation. They are not a commitment against the v0.1 packaging cut, not implementation plans, and not claims that the current tip is ready for these roles.

## Why this package exists

Elyra combines continuous presence, a structured mnemonic substrate (atoms, moments, ladders, edges, labeled meals), durable self/other identity, persistent goals, and grounded tools/skills. That combination is different from session chat or shallow RAG. This package records concrete application directions that lean on those strengths, the architectural deltas each would need, and the benchmarks that would have to be met before the claim would be honest.

## Documents

| Doc | Role |
|-----|------|
| [00-overview.md](00-overview.md) | Framing: distinctiveness, shared prerequisites, reading order |
| [01-embodied-continuous-agents.md](01-embodied-continuous-agents.md) | Humanoid and physical continuous agents |
| [02-lifelong-personal-assistants.md](02-lifelong-personal-assistants.md) | Personal and family lifelong companions |
| [03-institutional-business-memory.md](03-institutional-business-memory.md) | Team and organisational institutional memory |
| [04-scientific-research-companions.md](04-scientific-research-companions.md) | Lab and research continuity agents |
| [05-creative-long-horizon-collaboration.md](05-creative-long-horizon-collaboration.md) | Creative partners with multi-year project memory |
| [06-therapeutic-educational-accessibility.md](06-therapeutic-educational-accessibility.md) | Therapy, education, and cognitive-support roles |
| [07-shared-requirements-and-benchmarks.md](07-shared-requirements-and-benchmarks.md) | Cross-cutting deltas and a maturity ladder |

## Explicit non-claims

- Not part of the v0.1 dogfood product cut ([v0.1.md](../v0.1.md), epic #111).
- Not full DESIGN bodies — implementer detail stays under `docs/design/` when work is scheduled.
- Not a claim of readiness for robots, clinical use, or multi-tenant production.
- Not a second north star that overrides [programme.md](../programme.md) or Stretch 2 goals.

## Dependency on current polish

Nearly every application below depends on the same near-horizon work already tracked for daily dogfood quality: multi-user / group continuity, meal and traverse reliability, Glass inspectability, identity digests, and presence/wake hygiene. See [07-shared-requirements-and-benchmarks.md](07-shared-requirements-and-benchmarks.md).
