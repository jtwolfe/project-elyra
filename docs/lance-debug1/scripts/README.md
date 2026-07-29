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
| `fixtures/` | R0 + fixture create | PR2 | Tiny synthetic tables for CI probes |

---

## Environment

```bash
# Prefer quarantine layout (after quarantine_copy.sh):
export LANCE_DEBUG_DATA_DIR=/tmp/lance-q-YYYYMMDD/data
export LANCE_DEBUG_URI=$LANCE_DEBUG_DATA_DIR/memory/lance
# Marker must be: $LANCE_DEBUG_DATA_DIR/../.lance-debug1-quarantine

# PYTHONPATH: repo root so `elyra` imports work for W1 scripts (later PRs)
export PYTHONPATH=.
```

Prefer **Python 3.12** for R1 probes if 3.14 native lancedb connect segfaults (`env_check.py` reports interpreter).

---

## Safety (mandatory)

- Read [../SAFETY.md](../SAFETY.md) before running anything beyond R0.
- **Allowlist (R1):** `connect`, `open_table`, `table_names`, `count_rows`, `head`, bare `to_arrow`, `to_lance`, `list_versions`, schema inspect; optional private async query only inside H1b step 2.
- **Deny-list:** `merge_insert`, `add`, `delete`, `drop_table`, `compact_files`, `cleanup_old_versions`, `optimize`, live migrate. (`create_table` only in `fixtures/` builders.)
- **W1** scripts require marker at `{data_dir}/../.lance-debug1-quarantine` only.
- **No dual live connect** while the writer is open (default path: glass R2 + quarantine R1).

---

## PR2 available now

```bash
# Versions / paths
python docs/lance-debug1/scripts/env_check.py

# Quarantine full memory root + canonical marker
./docs/lance-debug1/scripts/quarantine_copy.sh data/memory /tmp/lance-q-$(date +%Y%m%d)
export LANCE_DEBUG_URI=/tmp/lance-q-$(date +%Y%m%d)/data/memory/lance

# R1 API matrix (H1 / H1a / H1b)
python docs/lance-debug1/scripts/api_matrix.py \
  --uri "$LANCE_DEBUG_URI" \
  --out docs/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01/api-matrix.json

# Hermetic fixture (no operator data)
python docs/lance-debug1/scripts/fixtures/build_tiny_atoms.py --out /tmp/tiny-lance
python docs/lance-debug1/scripts/api_matrix.py --uri /tmp/tiny-lance --out /tmp/api-matrix.json

# R0 caller grep (PR1)
python docs/lance-debug1/scripts/caller_grep_report.py
```

See [../REPRO-RECIPES.md](../REPRO-RECIPES.md) R1 and [../procedures/P01-offline-api-matrix.md](../procedures/P01-offline-api-matrix.md).

Later PRs add `load_parity.py` / `version_sample.py`; do not invent product entrypoints under top-level `scripts/`.
