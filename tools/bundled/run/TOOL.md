---
name: run
description: Run a command in the sandbox (shell=False, cwd=sandbox root). Prefer argv list.
kind: mutate
---

# run

Execute a process with cwd pinned to the **sandbox root** (`/workspace` in guest)
and scrubbed env. Never uses a host shell. Prefer ``command`` as an argv array;
strings are shlex-split. Timeout kills the process group (host) or ends guest
exec (isolation on).

## Isolation

| Mode | Backend |
|------|---------|
| Isolation **on** (product default) | **Guest only** via warm microsandbox. Fail closed (`sandbox_unavailable:*`) when the guest is missing/unusable — **no** silent host fallback. |
| Isolation **off** (`ELYRA_SANDBOX=0`) | Host `Sandbox.run` under `sandboxes/sandbox0/` (hermetic tests/CI). |

Payload includes `executor_backend`: `microsandbox` | `host_stub`.

**Trust boundary:** guest path cannot open host-absolute paths outside mounts.
Host path (isolation off) is process-level only — child may still open absolute
host paths under local-operator trust. Prefer sandbox-relative work. Do not use
`run` to fish host `tools/` / product source when the capability gap is
“missing tool” — use `load_skill("create-tool")` and `install_tool_draft`
instead.
