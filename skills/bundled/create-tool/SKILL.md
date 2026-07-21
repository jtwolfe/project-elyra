---
name: create-tool
description: Draft → verify → promote a tool package. Use when adding a new callable capability. Never skip verify.
---

# Create tool

Fail-closed lifecycle for new tools. Runtime enforces gates even if you ignore this checklist — follow it anyway.

## Hard rules

1. **Never skip verify.** Drafts are not callable. Promote only after green verify.
2. Write **only** under `tools/drafts/<name>/` via `install_tool_draft`.
3. Names must not clash with existing tools (case-normalized).
4. Never overwrite bundled packages or existing promoted local packages.
5. Never call a draft tool (registry will not expose it).

## Checklist (in order)

1. **Name** — valid package name; check it is free in the catalog.
2. **Draft package** — `install_tool_draft` with complete files:
   - `TOOL.md` (name, description, kind)
   - `schema.json` (JSON Schema object for arguments)
   - `runner.json` (allowlisted kind: `sandbox_shell` or `sandbox_python` for model-created tools — not `builtin`)
   - `tests/` (required before promote)
   - optional `impl/`
3. **Verify** — call `verify_tool` on the draft name. If verify fails, stay in drafts; fix via another draft write (invalidates prior verify) and re-verify.
4. **Promote** — only after green verify, call `promote_tool`. That moves drafts → `tools/local/` and makes the tool callable after registry reload.
5. Smoke-check the promoted tool with a safe call.

## Forbidden shortcuts

- Promote without verify
- Hand-editing outside drafts for “just this once”
- Planting `.verify.json` yourself
- Claiming a draft is done because tests “look fine” without `verify_tool`

## After promote

- Document usage in a skill if the workflow is non-obvious (`create-skill`).
- Prefer small tools with clear schemas over kitchen-sink runners.
