---
name: create-tool
description: Draft, verify, and promote a new tool package. Use when a reusable callable capability is missing and has already been judged necessary.
---

# Create tool

This skill is the execution path for adding a new tool. It assumes the decision that a new tool is warranted has already been made.

Most capability gaps should **not** result in a new tool.

## When to use this skill

Use this skill when:

- A reusable callable capability is genuinely missing, and
- Existing tools cannot cover the need, and
- The work is better expressed as a small, well-scoped primitive than as a one-off implementation via Grok Build.

Prefer these alternatives when they fit:

- Compose existing tools
- Use Grok Build for complex or multi-step implementation work
- Encode procedural guidance as a skill instead of a tool

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. Ledger note if needed (`create_goal` / `create_task` / `update_task`) so growth work is visible
2. `install_tool_draft` with a non-empty `files` map (or fix an existing draft)
3. Then `verify_tool` → on green, `promote_tool` → safe smoke call of the promoted tool

If isolation is unusable (`sandbox_unavailable` / not `pyenv_ready`), block the task / `speak` / `rest` — do not thrash sandbox FS or host-path fish with `run`.

## Quality bar

A good tool is:

- Small and focused on one clear job
- Tightly schema’d
- Covered by meaningful tests
- Safe to call repeatedly
- Preferable to a broad or kitchen-sink runner

Reject tools that are thin wrappers, poorly scoped, lightly tested, or that attempt work better handled by Grok Build or by a skill.

## Lifecycle (fail-closed)

```
install_tool_draft  →  verify_tool  →  promote_tool
```

- Drafts are not callable.
- Promotion is allowed only after a green `verify_tool`.
- The runtime enforces these gates. Do not bypass them.

### Paths (do not confuse)

| Path | What it is |
|------|------------|
| Host `tools/drafts/<name>/` | Draft packages — **only** via `install_tool_draft` |
| Host `tools/local/<name>/` | Promoted packages (callable after promote) |
| Sandbox `tools/` (host `sandboxes/sandbox0/tools/`, guest `/workspace/tools/`) | **Staged runtime copies** for execution / verify — **not** drafts |
| Sandbox `tools/.verify/<name>/` | Verify stage (guest pytest when isolation on) |

Sandbox FS tools (`list_dir`, `read_file`, …) **cannot** list host `tools/drafts/`. Seeing packages under sandbox `tools/` does not mean drafts are there.

### Runners (model-created)

- `sandbox_python`: `runner.json` with `module` + optional `function` (default `run`); guest calls `fn(args)` with the model args dict.
- `sandbox_shell`: `runner.json` with `argv`; model args are **not** on argv — the runtime writes guest `tmp/elyra_tool_args_*.json` and sets env **`ELYRA_TOOL_ARGS`** to that path. Shell impls must read that file.
- Invalid shape → `invalid_runner:*` on verify/promote. Do not use `builtin` for model drafts.

### Isolation

- Product default: isolation **on**. `verify_tool` needs guest **mount_ready** + **pyenv_ready** (curated env includes pytest). Failures: `sandbox_unavailable:*`, `guest_pytest_unavailable` — not “retry the same thrash.”
- If the sandbox is unusable, **block the task / speak / rest** honestly. Do not thrash `read_file` or fish host paths with `run`.
- Promoted smoke-check also needs isolation ready when isolation is on (guest exec). Host stub only when `ELYRA_SANDBOX=0` (tests/CI).

## Process

1. Confirm the name is free and the capability is still missing.
2. Draft the package with `install_tool_draft`. Required contents:
   - `TOOL.md` — name, description, kind
   - `schema.json` — JSON Schema for arguments
   - `runner.json` — allowlisted kind (`sandbox_shell` or `sandbox_python` for model-created tools)
   - `tests/` — required before promote
   - optional `impl/`
3. Call `verify_tool`. On failure, remain in drafts, fix, and re-verify.
4. Only after green verify, call `promote_tool`.
5. Smoke-check the promoted tool with a safe call (requires isolation ready when on).
6. If usage is non-obvious, consider a companion skill via `create-skill`.

## Hard rules

- Never skip verify.
- Write only under `tools/drafts/<name>/` via `install_tool_draft` (sandbox FS tools cannot see host drafts; sandbox `tools/` ≠ drafts; do not thrash empty `list_dir` / host path fishing via `run` as a substitute).
- Never overwrite bundled tools or existing promoted local tools.
- Never call a draft tool.
- Prefer small, clear tools over large multi-purpose ones.
- Prefer Grok Build over creating a new tool when the need is primarily complex implementation rather than a reusable primitive — **only when that instrument exists**; until then, block the ledger honestly or ask the operator rather than rewriting the host runtime.

## Ledger and review

Tool creation is a meaningful capability change. Keep it visible:

- Work should normally sit under an explicit goal or task.
- After promotion, update the relevant ledger entries.
- Prefer `review-work` before treating the new capability as fully done.
