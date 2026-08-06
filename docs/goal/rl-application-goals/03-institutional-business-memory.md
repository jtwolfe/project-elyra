# Institutional and business memory

| Field | Value |
|-------|--------|
| **Class** | GOAL |
| **Status** | Draft |
| **Audience** | Product |
| **Normative?** | No — aspirational application goal |
| **Related** | [00-overview.md](00-overview.md), [07-shared-requirements-and-benchmarks.md](07-shared-requirements-and-benchmarks.md) |

## Purpose

Describe team, project, and organisational agents that outlive individual employees and preserve decision context as structured experience rather than as a searchable document pile.

## Goals

- Durable institutional memory: why decisions were made, what was tried, what failed, and under what constraints.
- Onboarding that inherits a traversable history instead of only Slack archives and wikis.
- Organisational “sense of self” that can evolve under explicit governance (charter, culture, strategy digests).
- Project agents that persist across staff turnover with clear provenance of who said what and when.
- Selective redaction and access control without destroying the underlying fabric for authorised roles.

## Why this architecture is a fit

| Elyra mechanism | Institutional mapping |
|-----------------|----------------------|
| Moments + edges | Decision episodes and causal/associative links |
| Ladders | Project phases and organisational chapters |
| Self digests | Living charter / culture model under promote discipline |
| Other digests | Partners, customers, regulators as first-class models |
| Goals ledger | Strategic and project backlogs that outlive individuals |
| Multi-user identity | Role-aware discourse and meal filters |
| Inspectability | Audit-friendly views of what the agent currently holds |

## Architectural changes / extensions required

1. **Multi-party discourse model** — thread/room, speaker, audience; evolution of glass_tail toward discourse_tail.
2. **Provenance and access control on edges and digests** — role-based visibility; redaction without silent rewrite of history.
3. **Shared-ownership goal/ledger patterns** — goals that belong to a team, not only a single operator.
4. **Handoff protocol** — new operator inherits pin + documented memory surface without copying private identity stores by accident.
5. **Governance hooks** — promote gates for organisational self digests; dual-control for high-impact changes where required.
6. **Export for compliance** — structured export of decision-relevant meals and edges under policy.

## Benchmarks / success bars

- Reconstruct a decision from years earlier with atom-backed context (who, when, alternatives considered), not only a document hit.
- Successful hand-off: new operator can continue a project agent without loss of critical directed keep and open goals.
- Access control: unauthorised role cannot read redacted digests or private other-stores.
- Onboarding time: measurable reduction in “tribal knowledge only in people’s heads” for a defined project scope.
- No identity collapse across employees who share the same agent instance under role separation.

## Dependencies on near-horizon polish

- Multi-user continuity and identity design (C12 + identity multi-user design).
- Meal taxonomy lock for multi-party continuity.
- Traverse reliability for “why did we decide X?” queries.
- Inspect surfaces suitable for audit (Glass polish).

## Non-goals (this document)

- Replacing formal document management or legal record systems.
- Automatic merge of memory across separate company PE instances.
- Fully autonomous corporate decision-making without human promote authority.
- Generic “enterprise chatbot” feature parity as the success metric.
