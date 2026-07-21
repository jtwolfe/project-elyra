---
name: run
description: Run a command in the sandbox (shell=False, cwd=sandbox root). Prefer argv list.
kind: mutate
---

# run

Execute a process with cwd pinned to the sandbox root and scrubbed env.
Never uses a host shell. Prefer ``command`` as an argv array; strings are
shlex-split. Timeout kills the process group. Not a container — child can
still touch host paths/network (local-operator trust boundary).
