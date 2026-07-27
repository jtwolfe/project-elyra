---
name: sandbox_pip_update
description: Allowlist-add (or narrow-remove) packages in the guest curated requirements, then re-warm pyenv. Fail-closed for unknown names, isolation-off, and network=none. Host reverts requirements on install failure; guest site may stay dirty.
kind: mutate
---

# sandbox_pip_update

Host builtin. Mutates `sandboxes/sandbox0/lib/requirements-curated.txt` with
**allowlisted** packages only, clears the pyenv marker, and re-runs guest
`pip install --user -r …`. **Not free pip.**

## Args

- **`action`** (required): `add` | `remove` — `set_file` is **not** supported (v1).
- **`packages`** (required): array of distribution names or name+pin lines
  (e.g. `["regex"]`, `["httpx>=0.27,<1"]`). Max 10 per call.

## Hard walls

| Rule | Error |
|------|--------|
| Name not on `lib/requirements-allowlist.txt` | `package_not_allowlisted` |
| Would drop required curated (e.g. `pytest`) | `missing_required_package` |
| Isolation off (`ELYRA_SANDBOX=0`) | `isolation_required` |
| Guest network `none` | `network_policy_blocks_pip` |
| URL / VCS / path / shell-looking specs | `invalid_package_spec` |

Expanding the allowlist is an **operator** change (edit seed file), not model free agency.

## Result honesty

- **`host_reverted`**: host requirements file restored after install failure (files only).
- **`guest_site_may_be_dirty`**: guest user-site may still have wheels after a failed install, or after a successful **remove** (bookkeeping + re-warm, not uninstall).
- On install failure the pyenv marker is cleared (not blindly restored).

Success (`add`):

```json
{
  "ok": true,
  "action": "add",
  "packages": ["markdown"],
  "requirements_hash": "<sha256>",
  "pyenv_ready": true,
  "host_reverted": false,
  "guest_site_may_be_dirty": false
}
```

Install failure:

```json
{
  "ok": false,
  "error_reason": "pyenv_install_failed",
  "host_reverted": true,
  "guest_site_may_be_dirty": true,
  "detail": "... pip tail ...",
  "hint": "Host requirements restored; marker cleared. Guest user-site may still contain partially installed wheels..."
}
```
