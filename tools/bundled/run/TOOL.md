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

**Use `run` primarily to execute** (e.g. `python path/to/script.py`, `pytest`).
Prefer `search_replace` for edits to **existing** files. For **new** files under
the guest command cap (16 KiB UTF-8), one `run` with `Path.write_text(...)` is
ok; for tool packages use `install_tool_draft` — not multi-KB `python -c` /
heredocs as a file bus.

## Isolation

| Mode | Backend |
|------|---------|
| Isolation **on** (product default) | **Guest only** via warm microsandbox. Fail closed (`sandbox_unavailable:*`) when the guest is missing/unusable — **no** silent host fallback. Guest command max **16 KiB** UTF-8 (`command_too_large` with soft FS hint when exceeded). |
| Isolation **off** (`ELYRA_SANDBOX=0`) | Host `Sandbox.run` under `sandboxes/sandbox0/` (hermetic tests/CI). |

Payload includes `executor_backend`: `microsandbox` | `host_stub`.

**Trust boundary:** guest path cannot open host-absolute paths outside mounts.
Host path (isolation off) is process-level only — child may still open absolute
host paths under local-operator trust. Prefer sandbox-relative work. Do not use
`run` to fish host `tools/` / product source when the capability gap is
“missing tool” — use `load_skill("create-tool")` and `install_tool_draft`
instead.
