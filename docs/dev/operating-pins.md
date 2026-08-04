# Operating pins (manual convention)

| Field | Value |
|-------|--------|
| **Status** | Lightweight manual convention (automation later) |
| **Created** | 2026-08-04 (C3 / #113 — Day 4 packaging) |
| **Related** | [branch-law.md](branch-law.md), [engineering-principles.md](engineering-principles.md), epic #111 / #113 |
| **Git tag** | Public `vX.Y.Z` tags are **reviewed on creation of v0.1** — do not invent the tag process here beyond the goal below |

This document is the **operator-facing operating pin convention**. Tip / promote law remains [branch-law.md](branch-law.md); this file only specifies **how a live PE instance records what it runs**.

---

## Goal (future — not implemented in this doc)

When **`working` is promoted to `main`** and a **v0.1** packaging cut is declared:

1. The **operating pin becomes live** for dogfood instances: each instance records a **known-good SHA** (normally the promoted `main` tip or a release tag SHA).
2. A public **git tag** (e.g. `v0.1.0`) may be created from that SHA — **reviewed at v0.1 creation time**, not assumed by this convention.
3. Automation (PE-assisted pin move + grant) may follow later; **never** silent tip-follow.

Until then: **manual pin only**. Do **not** create the v0.1 tag from this convention alone. Do **not** auto-move pins on every merge to `working`.

---

## What a pin is

| Term | Meaning |
|------|---------|
| **Integration tip** | Moving branch head for development — house tip is **`working`** |
| **Stable tip** | **`main`** after promote |
| **Operating pin** | **Human-moved SHA** a live PE instance is actually executing |
| **Tag / Release** | Public download snapshot (`vX.Y.Z` + GitHub Release) — not the same as “this laptop’s pin” |

**Safety property (branch-law):** a live PE instance only ever executes a **previously accepted, pinned** commit. Development never mutates the tree it is executing from without an explicit restart/pin move.

```text
feature / fix / execute-plan  →  PR into working  →  promote working→main
                                                      │
                                                      ├─→ optional tag vX.Y.Z (public)
                                                      └─→ human moves operating pin (instance goes live)
```

---

## Manual convention (now)

### Per-instance record

Each PE instance (dev laptop, demo host, colleague machine) keeps a **small, human-edited pin record**:

| Item | Recommendation |
|------|----------------|
| **Where** | Prefer `$ELYRA_HOME/data/runtime/operating_pin.json` (instance-local; not committed as product law). Fallback: a short note in the instance’s operator runbook or Project status comment. |
| **What** | SHA (full 40-char preferred), optional short ref (`main`, `v0.1.0`), UTC date moved, who moved it, one-line reason |
| **Who moves** | Human instance owner only. PE/Grok Build must **stop for grant** — never invent pin success |
| **When** | After deliberate promote + restart of that instance; not on every `working` merge |

Example shape (illustrative — not required schema until automation):

```json
{
  "sha": "0123456789abcdef0123456789abcdef01234567",
  "ref": "main",
  "moved_at": "2026-08-20T18:00:00Z",
  "moved_by": "operator",
  "reason": "v0.1 dogfood pin after working→main promote"
}
```

### Operator checklist (manual)

1. Confirm the target SHA is on **`main`** (or an annotated release tag) unless the human explicitly pins a pre-release SHA for a named experiment.
2. Update the instance pin file / note.
3. Restart (or redeploy) the instance so the running tree matches the pin.
4. Optional: comment on the packaging epic or dogfood issue with SHA + date (no secrets).

### Multi-instance dogfood

- Colleagues sync the **pin**, not the moving `working` tip.
- Different instances may lag different pins; that is expected.
- Do not force all instances onto tip for convenience.

---

## Non-goals (this convention)

| Non-goal | Rationale |
|----------|-----------|
| Auto-merge `working` → `main` | Branch-law human promote |
| Silent pin move on tip advance | Safety property |
| Creating `v0.1` / release tags now | Reviewed when the cut is declared |
| Replacing branch-law tip rules | This file is pin record only |
| Requiring Graphite or CI green for pin | Orthogonal |

---

## Relationship to packaging (#113 / C3)

- **C3 acceptance** = this written convention exists and is linked from #113, branch-law, and engineering principles.
- **Live pin on v0.1** = process goal at promote time; tracked by packaging epic exit criteria (#112), not by inventing tags in this doc.
- Residual automation (file watcher, PE grant tool, Glass pin panel) is **out of C3** and may be backlog/research later.

---

## References

- [branch-law.md](branch-law.md) — tip, promote, pin safety property
- [engineering-principles.md](engineering-principles.md) §9 — development structure
- [development-governance.md](development-governance.md) — multi-party governance
- Epic #111 · checkpoint #113
