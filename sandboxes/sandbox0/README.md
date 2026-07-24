# Elyra primary sandbox (`sandbox0`)

Host tree at `{ELYRA_HOME}/sandboxes/sandbox0/`. Product FS tools
(`list_dir`, `read_file`, `grep`, `search_replace`) path-jail here.
When isolation is enabled (product default), this tree is mounted into the
warm microsandbox guest under fixed root **`/workspace`**. Prepared by
`ensure_host_tree` / `host_primary_root`.

## Layout

| Path | Guest (isolation on) | Mode |
|------|----------------------|------|
| `lib/` | `/workspace/lib` | RO — shared helpers for tool implementations |
| `general/` | `/workspace/general` | RO — small seed utilities (e.g. `now.py`) |
| `fixtures/` | `/workspace/fixtures` | RO — fake data for sandbox tests only |
| `tmp/` | `/workspace/tmp` | RW — scratch + tool args JSON |
| `tools/` | `/workspace/tools` | RW — staged runtime package copies |
| `README.md` | optional | RO |

## Rules

- Read only under the sandbox root (parent of `general/`, or guest `/workspace`).
- Write only under `tmp/` (and `tools/` for staged packages) unless a tool
  declares otherwise.
- Guest egress defaults to microsandbox `public_only` (outbound internet).
  Override with `ELYRA_SANDBOX_NETWORK=none|public_only|allow_all` at create time.
- Guest exec uses `python3 -B` / `PYTHONDONTWRITEBYTECODE=1` so RO mounts never
  need `__pycache__`.
- Never mount host `data/`, secrets, model weights, or the repo wholesale.

## Curated Python env (H3b)

`lib/requirements-curated.txt` is installed into the guest after mount readiness
(async warm). Marker: `tmp/.elyra_pyenv_ready`. Includes **pytest** (required for
isolation-on `verify_tool`) plus light tool-author libraries (`requests`,
`httpx`, `beautifulsoup4`, `pyyaml`, `python-dateutil`, `regex`,
`jinja2`). Not “any PyPI package.” Overlay wipe requires re-bootstrap (minutes).

See `docs/grok-improvement-plan/harness-sandbox-fitness.md`.
