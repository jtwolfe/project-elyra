---
name: verify_tool
description: Run a draft tool package's tests in the sandbox stage and write a hash-bound .verify.json on pass.
kind: mutate
---

# verify_tool

Stages `tools/drafts/<name>/` into `sandboxes/sandbox0/tools/.verify/<name>/`
(guest-visible RW), runs allowlisted pytest, and on pass writes `.verify.json`
on the **host draft** with a content hash of the draft tree. Promote requires
this record and hash match.

Use via the `load_skill("create-tool")` checklist order:
`install_tool_draft` → `verify_tool` → `promote_tool`. Never skip verify.

## Isolation

| Mode | Behaviour |
|------|-----------|
| Isolation **on** + `mount_ready` + `pyenv_ready` | Guest `python3 -m pytest tests/ -q --tb=short` (pytest from curated env). |
| Isolation **on** + mount ready but **not** `pyenv_ready` | Fail closed: `error_reason=guest_pytest_unavailable` (not a mysterious `No module named pytest`). |
| Isolation **on** + sandbox unusable | Fail closed: `sandbox_unavailable:*`. Never claim green on host-only pytest. |
| Isolation **off** (`ELYRA_SANDBOX=0`) | Host `sys.executable -m pytest` (hermetic CI); `executor_backend=host_stub`. |

Curated guest packages (incl. **pytest**) live in
`sandboxes/sandbox0/lib/requirements-curated.txt`. Status field `pyenv_ready`
tracks the install marker at `sandboxes/sandbox0/.elyra_pyenv_ready`
(host-only; not under guest-mounted `tmp/`).

## Fail-closed mitigations

- If tests create new packages under `tools/local/` during the run, those
  packages are removed and verify **fails** (`verify_local_planted`) so
  planting cannot bypass hash-bound promote.
- Backend is recorded in the result / `.verify.json` as `executor_backend`.
