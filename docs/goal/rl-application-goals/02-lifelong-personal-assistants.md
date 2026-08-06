# Lifelong personal assistants

| Field | Value |
|-------|--------|
| **Class** | GOAL |
| **Status** | Draft |
| **Audience** | Product |
| **Normative?** | No — aspirational application goal |
| **Related** | [00-overview.md](00-overview.md), [07-shared-requirements-and-benchmarks.md](07-shared-requirements-and-benchmarks.md), [design-identity-self-other-multi-user.md](../../design/identity/design-identity-self-other-multi-user.md) |

## Purpose

Describe personal (and family-shared) digital companions that operate across years rather than sessions, grounded in Elyra’s continuous presence and structured memory.

## Goals

- Co-management of long-horizon life goals (health, career, relationships, creative projects) with a real episodic backbone.
- Structured autobiographical memory the user can inspect, correct, and selectively keep or forget.
- Family or household shared use with clear identity boundaries and privacy controls.
- Continuity across device restarts, model upgrades, and long gaps in interaction.
- Honest “search my past” behaviour that returns atom-backed material or structured misses — not invented continuity.

## Why this architecture is a fit

| Elyra mechanism | Personal-assistant mapping |
|-----------------|----------------------------|
| Continuous presence | Always-available companion, not a new chat each time |
| Period ladders | Life chapters the user can read and edit |
| Glass-tail + temporal meal channels | Dialogue continuity without warehouse dump |
| Directed keep | Intentional “remember this” pins |
| Self / other digests | Distinct models of user and significant others |
| Goals ledger | Multi-year objectives that outlive sessions |
| Draft → promote | User-controlled identity and memory promotion |

## Architectural changes / extensions required

1. **Robust multi-user digests and work-origin USER** — switch/session honesty for household use.
2. **User-editable ladder summaries and self digests** — correction paths that do not brick the fabric.
3. **Privacy and grant models** — per-user and per-room meal filters; explicit sharing of digests.
4. **Reliable out-of-meal traversal** — cold-seed fallbacks and skill policy so “find something not in current context” works.
5. **Export / backup / migrate** — practical paths for a user’s `ELYRA_HOME` memory across machines without silent merge of identities.
6. **Long-gap continuity policy** — orient and meal behaviour after weeks or months of idle time.

## Benchmarks / success bars

- Multi-year *simulated* continuity: ladder chapters remain coherent; older episodic material remains reachable.
- Out-of-context retrieval: user request for older material yields atom-backed hits or clean structured failure.
- Multi-party meal continuity: two users in sequence do not poison each other’s identity digests.
- Correction: user can promote/correct identity or keep material and see the change reflected in subsequent meals.
- Restart resilience: process restart does not resume abandoned sandbox threads as live work (post-restart sanitation).
- Inspectability: user can see what the assistant currently holds as self, other, and directed keep.

## Dependencies on near-horizon polish

- C12 multi-user dogfood.
- C13 meal/traverse (especially #103-class cold seed and #104 directed keep clear).
- System prompt / orient softening where hard walls over-constrain presence (#79-class).
- Glass soft-refresh so inspect surfaces remain usable (#86-class).

## Non-goals (this document)

- Full multi-tenant SaaS product design.
- Medical diagnosis or regulated health claims.
- Replacing the user’s own judgment with autonomous life decisions.
- Silent cloud sync of raw memory fabrics across users.
