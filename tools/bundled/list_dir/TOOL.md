---
name: list_dir
description: List names in a sandbox directory (not recursive). Default path is ".".
kind: read
---

# list_dir

List entry names under a directory in the **persistent sandbox** (not the host
repo). Path is jailed; escapes return a tool error.

- Default `path` is `"."` (sandbox root). An empty root is normal until something
  is written there — do not thrash or switch to host paths via `run`.
- Host trees (`tools/`, `skills/`, source) are **not** visible here. Draft tools
  go through `install_tool_draft`; skills via `install_skill` / `load_skill`.
