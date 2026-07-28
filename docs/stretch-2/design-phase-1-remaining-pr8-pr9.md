# Stretch 2 Phase 1 — remaining work (PR8 + PR9)

| Field | Value |
|-------|--------|
| **Document** | Residual execute-plan for Phase 1 follow-ons only |
| **Product** | project-elyra |
| **Date** | 2026-07-28 |
| **Status** | Ready for `/execute-plan` |
| **Branch** | `grok-improvement-memory` |
| **Parent design** | [design-phase-1-implementation.md](design-phase-1-implementation.md) (full Phase 1 design; **PR1–PR7 shipped**) |
| **Architecture** | [architecture/phase-1-temporal.md](architecture/phase-1-temporal.md) |

## Overview

Phase 1 **core** (atoms, promote, ladder, meal, write path, meal drop-in, architecture note) is already on `grok-improvement-memory`. Defaults: `memory.write_atoms=true`, `memory.enabled=true`. JSONL store + idle compact are live.

This document is the **only** PR Plan that remaining execute-plan runs should use — so PR1–PR7 are **not** re-implemented. Two PRs remain for **Phase 1 operator-complete**:

1. **PR8** — Lance `MemoryStore` backend (storage foundation for Phase 2 vectors).
2. **PR9** — Glass **Memory** page: live context meal inspector, light atom list, Vectors/Graph **stubs**.

Rich vector and hypergraph browsers are **out of scope** here; they fill in **Phase 2** and **Phase 2a** respectively.

## Goals & non-goals

### Goals

- Protocol-complete Lance backend optional via `backend=lance`; CI stays JSONL.
- Operator can open a **Memory** panel and see what the model meal contains and what atoms exist.
- Stable UX slots for future Vectors / Graph tabs.

### Non-goals

- Nemotron / embeddings / ANN ranking.
- Typed hypergraph product or directed-traversal UI.
- Historical glass→atom backfill.
- Re-doing PR1–PR7.

## Key Decisions (residual)

| ID | Decision | Rationale |
|----|----------|-----------|
| **KD-R1** | PR8 is storage-only | Keep Lance reviewable; Phase 2 adds vector columns later |
| **KD-R2** | PR9 is glass + read APIs | Dogfood meal visibility without waiting for Phase 2 |
| **KD-R3** | Vectors/Graph tabs stub only in PR9 | Avoid fake viz; fill after Phase 2 / 2a |
| **KD-R4** | PR9 does not require Lance | Works against JSONL today; backend label can show `jsonl` or `lance` |
| **KD-R5** | Stack base is `grok-improvement-memory` tip | Product memory line; not `main` alone |

## References for implementers

Read fully when implementing:

- [design-phase-1-implementation.md](design-phase-1-implementation.md) — PR8/PR9 packaging, meal labels, store Protocol, flags
- [design-database-choices.md](design-database-choices.md) — Lance rationale, limitations, spike checklist
- [design-context-meal-composition.md](design-context-meal-composition.md) — channel labels for inspector
- Code: `elyra/memory/*`, `elyra/presence/worker.py` (`rebuild_outer`, status), `elyra/runtime/web/*`, `elyra/runtime/api.py`

## PR Plan

Ordered for `/execute-plan`. Linear stack: **PR8 → PR9**.

### PR 1: feat(memory): optional LanceDB MemoryStore backend

| Field | Value |
|-------|--------|
| **Title** | `feat(memory): optional LanceDB MemoryStore backend` |
| **Depends on** | None (code already has Protocol + JSONL on branch tip) |
| **Files/components affected** | `elyra/memory/lance_store.py`, `elyra/memory/store.py` (factory), `elyra/memory/config.py` if needed, `pyproject.toml` optional extra, `tests/test_memory_store_lance.py` (skip if dep missing), optional short note under `docs/stretch-2/architecture/` |
| **Description** | Implement `MemoryStore` on Lance for **Phase 1 atom fields only** (put/get/range/moment/links/walk/health; sequential prev/next). Factory `backend=lance` with JSONL default for CI. Optional install extra. **No** vector columns, ANN, graph product, meal/promote rewrites, or glass UI. Spike: hermetic or skip-marked tests; document install on operator Linux if quirks. |

### PR 2: feat(glass): Memory page — context inspector + Vectors/Graph stubs

| Field | Value |
|-------|--------|
| **Title** | `feat(glass): Memory page — live context meal inspector + Vectors/Graph stubs` |
| **Depends on** | PR 1 (stack order; functionally OK after tip without Lance) |
| **Files/components affected** | `elyra/runtime/web/index.html`, `app.js`, `style.css` (Memory nav/panel), `elyra/runtime/api.py` (read-only inspect endpoints), thin `elyra/memory/` helpers if needed for last-meal snapshot, tests for API/UI contracts |
| **Description** | New **Memory** panel parallel to Moments. **Ship:** (1) live/last **constructed context meal** by channel labels + token estimates + flags/health; (2) lightweight **atom list/timeline** (kind, moment, time, truncated text); (3) **Vectors** stub tab (Phase 2 copy); (4) **Graph** stub tab (Phase 2a+ copy). Optional trivial sequential prev/next strip only if cheap. Read-only APIs; no secrets; fail closed if store down. **Out:** vector projection UI, hypergraph layout, atom edit/delete, backfill, Nemotron. |

## Rollout

1. Land PR8; dogfood `backend=lance` if desired, keep CI on JSONL.
2. Land PR9; dogfood Memory panel while chatting (confirm meal vs glass understanding).
3. Proceed to Stretch 2 **Phase 2** design/execute for Vectors tab + embeddings.
4. Phase **2a** fills Graph tab.

## Open Questions

None blocking. Prefer JSONL in CI always; Lance optional extra.

---

*Residual plan only — do not re-run full design-phase-1-implementation.md through execute-plan unless intentionally rebuilding Phase 1.*
