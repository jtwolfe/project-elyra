# Known-bugs fix branch stack

| Field | Value |
|-------|--------|
| **Class** | DEV (archive-leaning) |
| **Status** | Archive |
| **Audience** | Jamie + Grok Build |
| **Normative?** | No — not tip law; historical fix-branch map only |
| **Last verified** | 2026-08-04 |

> **Status: Archive** — historical fix-branch map for the known-bugs batch. Not tip law.
> Prefer [branch-law.md](branch-law.md) for integration tip. Living product bugs: [known-bugs.md](../known-bugs.md). Prefer code on `working`.

| Role | Branch | Merges into |
|------|--------|-------------|
| **Integration** | `fix/known-bugs` | `main` (via PR when ready) |
| Per-bug work | `fix/BUG-*` | `fix/known-bugs` |

## Integration tip

`fix/known-bugs` batch merged to **main** (Glass/status/memory UI). Further bugs: branch from `main`.

## Closed per-bug branches (merged, deleted)

| Branch | Issue | Result |
|--------|-------|--------|
| `fix/BUG-status-01-76` | #76 | **Fixed** on main |
| `fix/BUG-status-02-77` | #77 | **Fixed** on main |
| `fix/BUG-status-03-78` | #78 | **Fixed** on main |
| `fix/BUG-glass-01-70` | #70 | **Fixed** on main |
| `fix/BUG-mem-ui-01-72` | #72 | **Fixed** on main (Context chrome); ladder residual separate |
| `fix/known-bugs` | #71 #74 #86 (partial) + batch | **Merged to main** |

## Workflow (next bugs)

1. Branch from latest `main`.
2. PR base = `main`.
3. Update [known-bugs.md](../known-bugs.md) + board when landing.
