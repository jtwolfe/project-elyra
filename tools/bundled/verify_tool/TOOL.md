---
name: verify_tool
description: Run a draft tool package's tests in the sandbox stage and write a hash-bound .verify.json on pass.
kind: mutate
---

# verify_tool

Stages `tools/drafts/<name>/` into `data/sandbox/.verify/<name>/`, runs
allowlisted pytest (`shell=False`), and on pass writes `.verify.json` with
a content hash of the draft tree. Promote requires this record and hash match.
