# Creative and long-horizon collaboration

| Field | Value |
|-------|--------|
| **Class** | GOAL |
| **Status** | Draft |
| **Audience** | Product |
| **Normative?** | No — aspirational application goal |
| **Related** | [00-overview.md](00-overview.md), [07-shared-requirements-and-benchmarks.md](07-shared-requirements-and-benchmarks.md) |

## Purpose

Map what creative collaboration agents could become on the Elyra foundation: partners that retain the evolution of a work across years, including abandoned branches and aesthetic decisions, without collapsing into session chat.

## Goals

- Continuity across long gaps and abandoned directions without treating abandoned work as still active.
- Intentional pinning of motifs, constraints, and irreplaceable reference material via directed keep.
- Clear separation of agent contributions from human contributions in the memory fabric.
- Support for exploring alternate creative timelines without destroying the main line.
- Project-scoped working sets that remain coherent when the human returns after months away.

## Why this architecture is a fit

| Elyra mechanism | Creative mapping |
|-----------------|------------------|
| Moments | Work sessions and critique episodes |
| Ladders | Chapters, acts, album arcs, design phases |
| Directed keep | Motifs, constraints, reference pins |
| Edges | Variant-of, inspired-by, rejects, continues |
| Goals ledger | Open creative objectives and deadlines |
| Self ≠ other | Agent collaborator identity distinct from the human artist |
| Glass inspectability | Human can see what the partner currently holds as canon |

## Architectural changes / extensions required

1. **Project-scoped memory fabrics** — isolation between concurrent works so meals do not blend unrelated projects.
2. **Working-set continuity across long gaps** — orient and meal policy after extended idle time on a project.
3. **Branch / alternate timeline support** — edges and keeps that mark speculative variants without automatic promotion to canon.
4. **Contribution provenance** — atoms or edges that record human vs agent authorship where collaboration credit matters.
5. **Export into creative toolchains** — practical hand-off of text, structure, or notes without requiring the full PE runtime everywhere.
6. **Aesthetic constraint pins** — first-class directed keep patterns for style rules the human wants enforced.

## Benchmarks / success bars

- After a multi-month gap, the agent resumes a project with correct canon motifs and open goals, without resurrecting explicitly abandoned branches as live work.
- Human can retrieve an early aesthetic decision with atom-backed context.
- Alternate timeline exploration leaves main-line digests and keeps intact unless explicitly promoted.
- Inspect view shows current directed keep and project goals clearly.
- Multi-project use does not leak motifs from project A into project B’s meal by default.

## Dependencies on near-horizon polish

- Meal/traverse reliability and directed keep clear (C13).
- Identity and multi-user boundaries if multiple collaborators share an instance (C12).
- Glass inspect surfaces (C10).
- System/orient prompts that do not over-constrain creative tone (#79-class).

## Non-goals (this document)

- Replacing DAWs, IDEs, or professional creative suites.
- Fully autonomous generation of finished commercial works without human promote.
- Training or fine-tuning claims based on the memory fabric alone.
- Copyright or ownership legal frameworks (policy outside this architecture note).
