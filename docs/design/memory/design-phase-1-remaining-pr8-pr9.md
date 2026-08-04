# Stretch 2 Phase 1 — remaining work (PR8 + PR9)

| Field | Value |
|-------|--------|
| **Class** | DESIGN |
| **Document** | Residual execute-plan for Phase 1 follow-ons only |
| **Product** | project-elyra |
| **Date** | 2026-07-28 |
| **Status** | **Done** (PR8 + PR9 shipped; Phase 1 operator-complete) |
| **Branch** | `grok-improvement-memory` |
| **Parent design** | [design-phase-1-implementation.md](design-phase-1-implementation.md) (full Phase 1 design; **PR1–PR7 shipped**) |
| **Architecture** | [architecture/phase-1-temporal.md](../../state/memory/architecture/phase-1-temporal.md) |
| **Close-out** | [README.md](../../state/memory/README.md) § Phase 1 close-out — bug caveats in [known-bugs.md](../../state/known-bugs.md) |

## Overview

Phase 1 **core** (atoms, promote, ladder, meal, write path, meal drop-in, architecture note) plus residual **PR8** (Lance backend) and **PR9** (glass Memory page) are **shipped** on `grok-improvement-memory`. Defaults: `memory.write_atoms=true`, `memory.enabled=true`. JSONL remains the CI/default backend; operator dogfood may set `memory.backend = "lance"` with `elyra[memory-lance]`.

This document is **historical** for the residual stack. Do **not** re-run `/execute-plan` against it unless intentionally rebuilding PR8/PR9.

## Shipped residual PRs

| PR | Title | Result |
|----|--------|--------|
| **PR8** | Optional LanceDB `MemoryStore` backend | `elyra/memory/lance_store.py`; factory `backend=lance`; optional extra; Phase 1 fields only |
| **PR9** | Glass Memory page | Context meal inspector, atoms list, Vectors/Graph **stubs** |

## Non-goals (unchanged — next phases)

- Nemotron / embeddings / ANN ranking → **Phase 2**
- Typed hypergraph product or directed-traversal UI → **Phase 2a**
- Historical glass→atom backfill
- Glass beautify / system-prompt soften (see known-bugs; not Phase 1 reopen)

## Rollout (completed)

1. ~~Land PR8; dogfood `backend=lance` if desired, keep CI on JSONL.~~
2. ~~Land PR9; dogfood Memory panel while chatting.~~
3. Proceed to Stretch 2 **Phase 2** design/execute for Vectors tab + embeddings.
4. Phase **2a** fills Graph tab.

---

*Phase 1 residual plan closed 2026-07-28.*
