# Design: Proactive / autotelic experiment mode (Draft)

| Field | Value |
|-------|-------|
| **Class** | DESIGN |
| **Author** | Design writer (Grok Build) |
| **Date** | 2026-08-07 |
| **Status** | **Draft** — not approved for execution; **not wired** |
| **Product** | project-elyra |
| **Primary tracker** | Optional deferred issue only; package docs live with [#130](https://github.com/jtwolfe/project-elyra/issues/130) refine notes |
| **Parallel only** | [#117](https://github.com/jtwolfe/project-elyra/issues/117) (Phase 3 procedural memory — **not** this mode) |
| **Related continuous** | [design-continuous-work-refine.md](design-continuous-work-refine.md) (Option A / Continue open work) — **orthogonal** |

---

## Overview

**Non-claims:** This document is a **Draft experiment catalog** for a future *proactive / autotelic* mode. It is **not** continuous work, **not** a continuous policy extension, **not** Phase 3 procedural memory (#117), and **not wired** in v0.1 or the continuous-refine package.

**Intent (aspirational):** Explore whether presence can notice opportunities and propose provisional goals beyond “chain on open ledger work,” under strict operator experiment flags, rate limits, and social safety — without pretending the product is always-alive or free to invent obligations.

Continuous “Continue open work” only re-wakes when open goals remain and progress gates pass (plus honest-exit Option A). Proactive/autotelic would address **empty-ledger or soft-opportunity** agency — a different product surface.

---

## Goals & Non-Goals

### Goals (Draft scope)

1. **Catalog the experiment** so it is not silently conflated with continuous or #117.
2. **Sketch v0** opportunity → provisional goals path for later design review.
3. **Bound safety:** rate limits, social priority, operator-only enablement, no auto-promotion of drafts.
4. **Leave open questions explicit** before any implementation plan.

### Non-Goals (explicit)

| Item | Why out |
|------|---------|
| Any switch, scanner, or auto-goal code in continuous-refine / #130 | Package OUT; Draft docs only |
| Extending `should_enqueue_moment_continue` for empty-ledger “initiative” | Continuous keeps K18 `require_open_work`; product framing is open-work chain, not always-alive |
| Phase 3 procedural memory implementation | Parallel **#117 only** |
| Multi-user proactive identity | Separate package (#127–129) |
| Claims of shipped behavior | Status is Draft; no runtime |

---

## Relationship map

```text
Continue open work (continuous)     — progress-gated chain on OPEN ledger work
        │
        ├── honest_exit Option A     — audit then idle (refine design)
        └── NOT this Draft

Proactive / autotelic experiment    — THIS Draft (not wired)
        │
        └── opportunity → soft goal proposals (speculative)

Phase 3 procedural (#117)           — parallel only; memory/procedure, not proactive continuous
```

---

## v0 sketch (non-normative)

### Opportunity → provisional goals

Speculative loop (for discussion only):

1. **Trigger sources (TBD):** idle background wake, operator “experiment ON” flag, soft scanner over recent moments / schedule gaps, optional skill or HOST hint — **none selected as normative**.
2. **Propose, do not commit:** surface a **provisional** goal/task draft the operator (or model under skill) can accept, edit, or discard.
3. **No silent ledger spam:** auto-`create_goal` without review is **OUT** of v0 sketch; prefer propose → human or explicit model tool.
4. **Hand-off to continuous:** once a real open goal exists, existing Continue open work may chain; proactive mode should not re-implement outer gates.

### Rate limits (sketch)

| Knob | Intent |
|------|--------|
| Max proposals / hour | Prevent thrash when experiment flag is ON |
| Cooldown after reject/dismiss | Avoid nag loops |
| Streak cap analogous to continuous | Bound multi-moment initiative without open work |
| Quiet hours / presence OFF | Never override operator rest or continuous OFF semantics |

Exact numbers deferred.

### Social safety

- **User wakes always preempt** any experiment path (same band priority law as continuous).
- **Pending wait / `wait_user`:** no proactive proposals that re-enter do-loop while user is owed a reply.
- **Speak obligation:** social moments still require `speak` for glass; silent auto-work must not bury user-facing debt.
- **No PII exfil / no unsolicited external actions** from an experiment scanner without existing tool gates.

---

## Surfaces (speculative only)

| Surface | Note |
|---------|------|
| Operator experiment flag | Glass/API; default OFF; not the continuous toggle |
| Soft proposal UI | Status or Goals “suggested” tray — undecided |
| HOST / skill line | Thin “consider proposing work” under flag — undecided |
| Scanner / scheduler | Optional future; not continuous `moment_continue` |

**This package and continuous v0.1 path implement none of the above.**

---

## Open questions

| # | Question | Default if unresolved |
|---|----------|------------------------|
| OQ1 | Trigger sources: background-only vs explicit operator “suggest work”? | Prefer explicit flag + idle only |
| OQ2 | Auto-create goals vs propose-only? | **Propose-only** for any first cut |
| OQ3 | Interaction with `rest` / honest_exit under continuous ON? | Continuous laws unchanged; proactive OFF by default |
| OQ4 | Multi-user: whose goals get proposals? | Defer to multi-user package |
| OQ5 | Separate GitHub issue now? | Optional; Draft file alone is enough for #130 docs package |
| OQ6 | Overlap with Phase 3 procedural (#117)? | Keep parallel; do not merge trackers |

---

## References

| Ref | Path / URL |
|-----|------------|
| Continuous refine (Option A) | [design-continuous-work-refine.md](design-continuous-work-refine.md) |
| Shipped continuous | [design-continuous-work-orient-ledger-reset.md](design-continuous-work-orient-ledger-reset.md) |
| STATE continuous §11 | `docs/state/stretch-1.md` |
| Phase 3 procedural | [design-phase-3-procedural.md](../memory/design-phase-3-procedural.md), [#117](https://github.com/jtwolfe/project-elyra/issues/117) |
| #130 package | https://github.com/jtwolfe/project-elyra/issues/130 |

---

## Revision history

| Date | Change |
|------|--------|
| 2026-08-07 | Initial Draft catalog for continuous-refine PR1 / #130 package |
