# P08 — Version archaeology

| Field | Value |
|-------|--------|
| **Status** | Stub (filled in PR3 with `version_sample.py`) |
| **Safety class** | **R1** (read-only; careful) |
| **Prove / disprove** | H3 historical growth; H4 only if still open; H10 historical collapse only |
| **When** | **After** H1a/H1b; optional polish if default-limit proven |

## Purpose

Sample Lance versions safely without compact/optimize/cleanup.

## Rules

- Prefer `Table.list_versions` / read-only checkout
- Never `cleanup_old_versions` / `compact_files` / `optimize`
- If using `lance.dataset`, prefer subprocess with timeout
- H10 supported only for non-monotonic historical **collapse**; not active process-thin when full APIs already large

## Forbidden

Destructive version ops on live or quarantine (except disposable quarantine experiments explicitly logged — still no compact as primary path).
