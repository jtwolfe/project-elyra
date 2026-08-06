# Therapeutic, educational, and accessibility applications

| Field | Value |
|-------|--------|
| **Class** | GOAL |
| **Status** | Draft |
| **Audience** | Product |
| **Normative?** | No — aspirational application goal; heightened ethical constraints |
| **Related** | [00-overview.md](00-overview.md), [07-shared-requirements-and-benchmarks.md](07-shared-requirements-and-benchmarks.md), [design-identity-self-other-multi-user.md](../../design/identity/design-identity-self-other-multi-user.md) |

## Purpose

Describe careful long-horizon uses in therapy/coaching support, education, and cognitive accessibility — where inspectability, correctable identity material, and user agency are load-bearing.

## Goals

- Longitudinal models of a person that remain **inspectable and correctable** by the user (and, where explicitly granted, by a clinician or educator).
- Support for externalised autobiographical memory and executive function without seizing control from the human.
- Educational tutors that carry a multi-year model of knowledge, misconceptions, and motivation — not only spaced-repetition cards.
- Strong ethical boundaries around identity digests, sensitive keeps, and third-party access.
- Clear auditability of what changed in the agent’s model of the person and why.

## Why this architecture is a fit

| Elyra mechanism | Care / learning mapping |
|-----------------|-------------------------|
| Draft → promote | Sensitive self material changes only under explicit promotion |
| Self / other digests | Separable models of client, clinician, learner, caregiver |
| Directed keep | User-controlled “important for my care/learning” pins |
| Moments + ladders | Session history and longer life or curriculum chapters |
| Glass inspectability | User and authorised professional can see current state |
| Goals ledger | Therapeutic or learning objectives that persist across sessions |
| Multi-user grants | Controlled sharing with clinicians/teachers |

## Architectural changes / extensions required

1. **Enhanced draft → promote workflows** for sensitive identity and health-adjacent material.
2. **Consent and grant models** — time-bounded, purpose-limited access for third parties; revocation that is actually enforced in meal and export paths.
3. **High inspectability** of current beliefs about the user — no opaque “profile score” as the only interface.
4. **Safeguards against unwanted identity drift** — orient and skill policy that prefers reconfirm over silent update of core self material.
5. **Curriculum / care-plan goal patterns** — shared goals with explicit ownership (learner vs tutor, client vs clinician).
6. **Crisis and scope boundaries** — product-level refusal and escalation patterns (not clinical protocols themselves).

## Benchmarks / success bars

- Multi-year *simulated* continuity with user corrections successfully incorporated into digests and subsequent meals.
- Audit: user can list identity and keep changes over a period with timestamps and promote provenance.
- Grant revocation: after revoke, third party no longer receives restricted channels in meal or export.
- Educational: tutor retrieves prior misconceptions relevant to a new topic via directed traversal, not only keyword match.
- Accessibility: externalised memory queries return atom-backed results or honest misses; user remains able to delete or pin.
- No unsupervised clinical decision claims in speak behaviour under default skills.

## Dependencies on near-horizon polish

- Identity multi-user design and C12 dogfood.
- Prompt/orient softening and honesty (#79-class).
- Meal/traverse reliability (C13).
- Glass inspectability (C10).
- Strong culture of draft → promote already present in identity design — must remain non-negotiable.

## Non-goals (this document)

- Providing medical, psychiatric, or legal advice; diagnosis; or crisis intervention protocols.
- Replacing licensed clinicians or teachers.
- Covert monitoring or non-consensual profiling.
- Claiming therapeutic efficacy without separate clinical validation.
- Weakening user agency in the name of “engagement.”

## Ethical posture

These applications amplify both benefit and harm. The architecture’s emphasis on inspectability, draft → promote, and self ≠ other is a **prerequisite**, not a nice-to-have. Domain deployment requires policy, consent UX, and often regulation beyond what this GOAL package can specify.
