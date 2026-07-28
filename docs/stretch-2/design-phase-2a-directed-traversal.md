# Phase 2a — Directed Traversal

**Status:** Design draft  
**Depends on:** Phase 1 + Phase 2 (atoms + semantic seeds exist)

## Goal

Give Elyra the ability to *actively* explore the memory graph around semantically relevant seeds.

- Start from atoms returned by temporal + semantic search.
- Walk temporal, sequential, and early structural edges to gather neighbours.
- The model (or a thin controller) decides which neighbours are worth keeping.
- **Temporary context hygiene:** any material pulled in solely for the traversal is flagged as temporary. After the model confirms the final selection, temporary items are discarded; only the chosen atoms remain in the durable temporal / working context.

This realises the “model-managed / directed search” bucket of context construction.

## Care points

- Traversal must not permanently pollute the temporal context.
- Budget limits (max atoms, max depth, max tokens) are mandatory.
- Clear observability in glass / logs so the operator can see what was considered vs kept.

## Success criteria

- [ ] Directed walk API exists and respects budgets.
- [ ] Temporary vs durable distinction is enforced.
- [ ] Selected neighbours can be added to the context package for the remainder of the moment.
- [ ] Tests cover flagging, selection, and cleanup.
