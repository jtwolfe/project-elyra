# Shared architectural requirements and benchmarks

| Field | Value |
|-------|--------|
| **Class** | GOAL |
| **Status** | Draft |
| **Audience** | Product |
| **Normative?** | No — cross-cutting maturity guidance for application claims |
| **Related** | [README.md](README.md), [00-overview.md](00-overview.md), [v0.1.md](../v0.1.md), epic #111 product bars C10–C14 |

## Purpose

Extract the common technical and product requirements that appear across the domain documents, and propose a maturity ladder: what must be solid before an application-class claim is honest.

## Cross-cutting requirements

### 1. Multi-user social continuity

- Session / work-origin USER honesty.
- Self and other digests that do not collapse distinct people.
- Meal behaviour that supports multi-party discourse without warehouse dump.
- Design baseline: identity multi-user design; product bar C12.

### 2. Meal and traverse reliability

- Hard constraints and functional dedupe for context meals.
- Out-of-meal / cold-seed traversal with fallback or structured miss (not silent live-strip-only failure).
- Complete directed keep clear path when the user directs pins to drop.
- Product bar C13 and related critical residuals.

### 3. Edges and graph honesty

- Edge creation and quantification good enough for directed walks.
- Inspectable edge kinds on expand paths.
- Product bar C14; polish residuals tracked separately.

### 4. Identity versioning and correction

- Draft → promote remains the path for durable self/other changes.
- User (or authorised operator) can correct digests without bricking the fabric.
- Sensitive domains require stronger grant and audit surfaces.

### 5. Presence and wake hygiene

- No stale task_ready / timer storms as the default continuous experience.
- Post-restart sanitation so old threads are not resumed as live work without reconfirm.
- Required for any “always-on life” claim (embodied or digital).

### 6. Inspectability and operator control

- Glass (or successor) surfaces that do not hard-rebuild away selection and inspectors.
- Visible meals, keeps, digests, goals, and traverse sessions.
- Product bar C10 and related Glass residuals.

### 7. Safety and isolation for grounded action

- Sandbox / grant discipline for tools that affect the world (files today; actuators later).
- Fail closed without grant; no silent capability escalation.
- Embodied and institutional deployments raise the bar further.

### 8. Prompt and orient coherence

- System and orient text that guides without rigid over-constraint of presence and tone.
- Domain skills can specialise; core walls stay honest and light where intended.

## Maturity ladder (application claims)

| Level | Meaning | Minimum bar |
|-------|---------|-------------|
| **L0 — Research narrative** | This package (Draft GOAL) | Written goals, deltas, benchmarks; no product claim |
| **L1 — Dogfood credible** | Daily single-operator use feels continuous and inspectable | C10–C14 residuals addressed or explicitly waived; wake hygiene acceptable; traverse out-of-meal path honest |
| **L2 — Multi-user credible** | Two+ users / roles without identity collapse | C12 dogfood green; meal taxonomy locked for multi-party; grants enforced in meal paths |
| **L3 — Domain pilot** | Bounded pilot in one application class under human oversight | Domain-specific deltas (safety, consent, project scope, protocol skills) implemented; benchmarks measured; no unsupervised high-stakes action |
| **L4 — Production claim** | External users or physical/clinical deployment | Governance, compliance, and reliability beyond this GOAL package; separate DESIGN and STATE contracts |

**Rule:** do not advertise L3+ readiness while L1–L2 bars remain open. Prefer honest L1 dogfood over inflated application marketing.

## Relationship to v0.1

The v0.1 packaging cut is a **dogfood-ready product cut** of Elyra + mnemonic substrate. It is necessary foundation for these application goals; it is not itself any of the domain claims above. Completing v0.1 gates (especially C10–C14) is the practical near-term path that makes L1–L2 reachable.

## Suggested use of this package

- Product discussion and prioritisation after v0.1.
- Source of success bars when opening future DESIGN work.
- Shared language with collaborators about what “lifelong,” “institutional,” or “embodied” would actually require of the architecture.

## Non-goals

- Scheduling implementation work in this document.
- Replacing Stretch 2 north star or programme.md.
- Defining regulatory or certification processes.
