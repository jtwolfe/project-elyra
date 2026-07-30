# Known-bugs fix branch stack

| Role | Branch | Merges into |
|------|--------|-------------|
| **Integration** | `fix/known-bugs` | `main` (via PR when ready) |
| Per-bug work | `fix/BUG-*` | `fix/known-bugs` |

## Active per-bug branches

| Branch | Issue | Bug ID |
|--------|-------|--------|
| `fix/BUG-status-01-76` | #76 | BUG-status-01 — Status page does not scroll |
| `fix/BUG-status-02-77` | #77 | BUG-status-02 — Dev speed dual control |
| `fix/BUG-status-03-78` | #78 | BUG-status-03 — Hard-stop override OFF persistence |
| `fix/BUG-glass-01-70` | #70 | BUG-glass-01 — Moments panel beautify |
| `fix/BUG-mem-ui-01-72` | #72 | BUG-mem-ui-01 — Memory Context beautify + summary review |

## Workflow

1. Start from latest `fix/known-bugs` (or rebase your bug branch onto it).
2. Implement + test on the per-bug branch only.
3. Open PR: **base = `fix/known-bugs`**, head = per-bug branch; link `Fixes #NN`.
4. After merge to integration, delete the short-lived bug branch.
5. When the integration batch is dogfooded, PR `fix/known-bugs` → `main`.

Do not open per-bug PRs directly to `main` unless intentionally unbundling.
