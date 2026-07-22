---
name: verify_tool
description: Run a draft tool package's tests in the sandbox stage and write a hash-bound .verify.json on pass.
kind: mutate
---

# verify_tool

Stages `tools/drafts/<name>/` into `data/sandbox/.verify/<name>/`, runs
allowlisted pytest (`shell=False`), and on pass writes `.verify.json` with
a content hash of the draft tree. Promote requires this record and hash match.

Use via the `load_skill("create-tool")` checklist order:
`install_tool_draft` → `verify_tool` → `promote_tool`. Never skip verify.

## Trust boundary (S1)

Tests run at **process-level** trust only (same residual as sandbox `run`):
scrubbed env (minimal PATH, no host PATH merge, no host secret inherit),
`cwd` = staged package. Not a chroot/container — child code can still open
absolute host paths and use the network.

Fail-closed mitigations:
- Env matches sandbox scrubbing (no host PATH merge).
- If tests create new packages under `tools/local/` during the run, those
  packages are removed and verify **fails** (`verify_local_planted`) so
  planting cannot bypass hash-bound promote.

Full FS/network isolation is out of scope for Stretch 1.
