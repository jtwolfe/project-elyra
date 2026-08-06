# Overview — future applications of the Elyra architecture

| Field | Value |
|-------|--------|
| **Class** | GOAL |
| **Status** | Draft |
| **Audience** | Product |
| **Normative?** | No — aspirational framing |
| **Related** | [README.md](README.md), [programme.md](../programme.md), [stretch-2-north-star.md](../stretch-2-north-star.md) |

## Purpose

State why Elyra’s architecture opens a different class of applications than session-based or shallow-memory agents, and orient the domain documents that follow.

## What is distinctive

Most current systems are either:

- stateless or shallowly stateful chat interfaces, or
- retrieval over documents with no durable “life,” identity, or continuous motive.

Elyra is built around:

| Capability | Role in future applications |
|------------|-----------------------------|
| **Continuous presence** | Always-on process, wake queue, moments as units of action — not one-shot replies |
| **Mnemonic substrate** | Atoms, moments, period ladders, edges, labeled context meals — structured experience, not a warehouse dump |
| **Self / other identity** | Versioned digests, draft → promote, distinct models of self and others |
| **Goals and tasks ledger** | Work that outlives a single moment or session |
| **Tools / skills + sandbox** | Grounded action under isolation and promote discipline |
| **Inspectability (Glass)** | Operator-visible memory, identity, and process state |

Applications that need an agent to *live with* people or institutions over months and years lean on this combination. Applications that only need the next answer do not need this architecture.

## Shared prerequisites

Before any domain claim is honest, the following must be solid enough for daily dogfood:

1. **Multi-user social continuity** — session honesty, identity digests, multi-party meal behaviour.
2. **Meal and traverse reliability** — including cold-seed fallback and directed keep clear paths.
3. **Inspectable memory surfaces** — Glass that does not thrash selection or hide provenance.
4. **Coherent identity over time** — draft → promote, correctable self/other material.
5. **Presence / wake hygiene** — no storm of stale wakes; post-restart awareness.

These map to work already elevated under the v0.1 packaging epic and related memory/Glass issues. Domain documents assume that foundation rather than re-deriving it.

## How to read the package

1. This overview (distinctiveness + shared bar).
2. Domain documents for the applications of interest (goals, fit, deltas, benchmarks).
3. [07-shared-requirements-and-benchmarks.md](07-shared-requirements-and-benchmarks.md) for the cross-cutting maturity ladder.

## Tone

Aspirational but honest: *with further focused work and polish on the existing foundation, these roles become reachable.* Each document separates goals, required architectural deltas, and testable benchmarks from non-goals and later work.
