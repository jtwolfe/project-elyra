# Known-bugs fix branch stack

| Role | Branch | Merges into |
|------|--------|-------------|
| **Integration** | `fix/known-bugs` | `main` (via PR when ready) |
| Per-bug work | `fix/BUG-*` | `fix/known-bugs` |

## Integration tip

`fix/known-bugs` holds the Glass/status/memory UI batch.

## Closed per-bug branches (merged, deleted)

| Branch | Issue | Result |
|--------|-------|--------|
| `fix/BUG-status-01-76` | #76 | **Fixed** — dogfood OK; branch closed |
| `fix/BUG-status-02-77` | #77 | **Fixed** — dogfood OK; branch closed |
| `fix/BUG-status-03-78` | #78 | **Fixed** — dogfood OK; branch closed |
| `fix/BUG-glass-01-70` | #70 | **Partial** on integration; branch closed — further work from `fix/known-bugs` |
| `fix/BUG-mem-ui-01-72` | #72 | **Partial** on integration; branch closed — further work from `fix/known-bugs` |

## Workflow (next bugs)

1. Branch from latest `fix/known-bugs`.
2. PR base = `fix/known-bugs`.
3. After dogfood, batch PR `fix/known-bugs` → `main`.
