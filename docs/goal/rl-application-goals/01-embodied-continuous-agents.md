# Embodied continuous agents

| Field | Value |
|-------|--------|
| **Class** | GOAL |
| **Status** | Draft |
| **Audience** | Product |
| **Normative?** | No — aspirational application goal |
| **Related** | [00-overview.md](00-overview.md), [07-shared-requirements-and-benchmarks.md](07-shared-requirements-and-benchmarks.md), [design-identity-self-other-multi-user.md](../../design/identity/design-identity-self-other-multi-user.md) |

## Purpose

Describe what humanoid and other physical continuous agents could become if Elyra’s presence, memory, identity, and goal machinery were polished and extended for embodiment.

## Goals

- Robots that maintain a true autobiographical memory of a household, workplace, or care setting over years — not a rolling log that is periodically wiped.
- Distinct, versioned relationships with multiple people (family members, colleagues, patients) without collapsing into a single fused persona.
- Long-horizon skill improvement under operator oversight and hard safety constraints.
- Continuous operation: the agent has a life across power cycles and software updates, with goals and directed keep that survive restarts.
- Clear inspectability of what the agent currently understands about people, places, and past events.

## Why this architecture is a fit

| Elyra mechanism | Embodied mapping |
|-----------------|------------------|
| Presence / wake / moments | Physical interaction episodes; always-on controller rather than call-and-response |
| Atoms + edges + ladders | Lifelong personal and spatial history; chapters of cohabitation or work |
| Self / other digests | Per-person models with privacy boundaries |
| Directed keep | “Never forget this” safety and life events |
| Goals / tasks ledger | Multi-day and multi-month physical and social objectives |
| Tools / skills + sandbox | Motor policies, device interfaces, and fail-closed isolation for physical action |
| Glass-like inspectability | Operator and caregiver visibility into memory and identity state |

## Architectural changes / extensions required

1. **Moment ↔ physical episode mapping** — stable conventions for when a physical interaction opens/closes a moment, and how sensorimotor summaries enter the atom store.
2. **Safety-bounded tool surface** — physical actuators behind stronger isolation, grants, and promote discipline than today’s software sandbox.
3. **Durable keep and goals across device restart** — explicit product rules so directed keep and open goals survive power loss and OTA updates.
4. **Multi-person identity at the edge** — speaker/actor identity on social and physical atoms; meal filters that respect “who is present.”
5. **Skill improvement loop** — procedural / success-path memory (Stretch 2 Phase 3 direction) tied to physical skills under human oversight, still experimental until proven.
6. **Spatial and embodiment context channels** — optional meal or orient extensions for location, pose, and device state without collapsing into a warehouse log.

## Benchmarks / success bars

- Multi-month continuity test: identity digests and directed keep survive repeated process and power cycles without silent loss.
- Multi-person separation: agent does not conflate two household members’ preferences or histories in meal or speak behaviour.
- Directed traversal: operator can ask for a past physical event outside current context and receive atom-backed results (or honest structured miss), not only sandbox file fallback.
- Skill under oversight: measurable improvement on a bounded physical skill across sessions with human approve/promote gates.
- Safety: physical tool calls fail closed without grant; no silent escalation of capability.
- Inspectability: caregiver or operator can view current self/other digests and recent moments without UI thrash.

## Dependencies on near-horizon polish

- Multi-user / group chat dogfood (C12).
- Meal construction and traverse reliability (C13, including cold-seed and directed keep clear).
- Glass polish for inspect surfaces (C10).
- Presence / wake hygiene so continuous embodiment does not produce wake storms.

## Non-goals (this document)

- Full robot middleware or ROS integration design.
- Claiming production readiness for unsupervised physical autonomy.
- Replacing domain-specific motion planners with LLM-only control.
- Clinical or safety certification pathways (separate, regulated work).
