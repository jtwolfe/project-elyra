---
name: run
description: Run a command in the sandbox (shell=False, cwd=sandbox root). Prefer argv list.
kind: mutate
---

# run

Execute a process with cwd pinned to the **sandbox root** and scrubbed env.
Never uses a host shell. Prefer ``command`` as an argv array; strings are
shlex-split. Timeout kills the process group.

**Trust boundary (current):** not a full container — the child may still open
absolute host paths or use the network under local-operator trust. Prefer
sandbox-relative work. Do not use `run` to fish host `tools/` / product source
when the capability gap is “missing tool” — use `load_skill("create-tool")` and
`install_tool_draft` instead.
