# lance-debug1 scripts

Hermetic helpers colocated with the inspection package. **Not** product entrypoints.

| Script | Safety | PR | Role |
|--------|--------|-----|------|
| `caller_grep_report.py` | R0 | PR1 | Regenerate `to_arrow` / related call pins from repo |
| `env_check.py` | R0/R1 | PR2 | Package versions, paths, backend settings |
| `api_matrix.py` | R1 | PR2 | Preferred full-read probe order + H1a/H1b |
| `quarantine_copy.sh` | copy → enables W1 | PR2 | Full memory-root copy + canonical marker |
| `load_parity.py` | **W1** | PR3 | Open store on quarantine; compare to api_matrix |
| `version_sample.py` | R1 | PR3 | `list_versions` / read-only checkout only |
| `consumer_compare.py` | R1 | PR4 | Optional GraphView thin vs full row sets |
| `fixtures/` | R0 | PR2+ | Tiny synthetic tables for CI probes |

---

## Environment

```bash
# Prefer quarantine layout (after quarantine_copy.sh exists):
export LANCE_DEBUG_DATA_DIR=/tmp/lance-q-YYYYMMDD/data
export LANCE_DEBUG_URI=$LANCE_DEBUG_DATA_DIR/memory/lance
# Marker must be: $LANCE_DEBUG_DATA_DIR/../.lance-debug1-quarantine

# PYTHONPATH: repo root so `elyra` imports work for W1 scripts (later PRs)
export PYTHONPATH=.
```

---

## Safety (mandatory)

- Read [../SAFETY.md](../SAFETY.md) before running anything beyond R0.
- **Allowlist (R1):** `connect`, `open_table`, `table_names`, `count_rows`, `head`, bare `to_arrow`, `to_lance`, `list_versions`, schema inspect.
- **Deny-list:** `merge_insert`, `add`, `delete`, `drop_table`, `compact_files`, `cleanup_old_versions`, `optimize`, live migrate.
- **W1** scripts require marker at `{data_dir}/../.lance-debug1-quarantine` only.
- **No dual live connect** while the writer is open (default path: glass R2 + quarantine R1).

---

## PR1 available now

```bash
# From repo root — print markdown table of to_arrow / count_rows / related sites
python docs/lance-debug1/scripts/caller_grep_report.py
python docs/lance-debug1/scripts/caller_grep_report.py --root .
```

Later PRs add the remaining scripts; do not invent product entrypoints under top-level `scripts/`.
