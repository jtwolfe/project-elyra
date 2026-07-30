# SAFETY — Lance debug1 inspection

**Scope:** Rules for all work under `docs/lance-debug1/`. Inspection only; no product memory patches.

**Key decisions reflected:** KD1 (inspection-only), KD3 (full memory-root quarantine), KD11 (store open = W1), KD12 (no dual live connect), KD13 (script deny-list), KD15 (canonical marker only).

---

## Safety classes

| Class | Meaning | Allowed |
|-------|---------|---------|
| **R0** | Pure read of code / docs | Always |
| **R1** | Read-only **client** probes (`count_rows`, `head`, bare `to_arrow`, `to_lance`, H1b chain, `list_versions`) — no store open, no put/delete/migrate/compact | Quarantine preferred; live URI only when writer idle **or** explicitly accepted as concurrent-read risk (P03 exception) |
| **R2** | Read of running process via glass HTTP / logs | Live instance |
| **W1** | Any path that may write: **`LanceMemoryStore` open**, joint repair, put, migrate, meta ensure | Quarantine **data_dir** under marked `$QUARANTINE_ROOT` only |
| **FORBIDDEN** | `drop_table`, migrate on live, `delete` rows, `compact_files`, `cleanup_old_versions`, `optimize` (table), force compact live, delete operator `_versions` | **Never** in this investigation |

### Why store open is W1

`LanceMemoryStore.__init__` runs `_ensure_layout` (may write `meta.json`) and may `repair_joint_copies` (`merge_insert` on loaded rows). Even “read parity” open is classified **W1** and requires a marked quarantine tree.

---

## Concrete rules

1. **Quarantine default.** Copy the **full memory root** into `$QUARANTINE_ROOT/data/memory/` before W1. Never open the product store against the live tree for parity probes.
2. **Concurrent writer / rsync.** Prefer **stop or pause Elyra** (or wait until idle — no promote/encode writes) before quarantine copy. Live `merge_insert` creates ~1 version per put; concurrent `rsync`/`cp -a` of multi-file `atoms.lance` can yield a **torn** snapshot (partial `_versions` / data fragments). If concurrent copy is unavoidable: record writer PID, uptime, and `possibly_torn` in marker + evidence; do not treat torn counts as definitive H1b failures without a clean idle copy.
3. **P03 dual-connect.** Prefer **R2 glass** + **R1 on quarantine snapshot taken just after restart** (before heavy promote). Dual `lancedb.connect` on the **live** URI while the writer is open is **discouraged**; only with explicit operator accept and documented multi-connect hazard. **No dual live connect** as the default path.
4. **Script allowlist (read probes):** `connect`, `open_table`, `table_names`, `count_rows`, `head`, bare `to_arrow`, `to_lance` (+ dataset `to_table` / `count_rows` read-only), `list_versions`, `checkout` (read-only if non-mutating), schema inspection; **optional** private/async query-limit only inside H1b chain step 2 (documented, not primary).
5. **Script deny-list:** `merge_insert`, `add`, `delete`, `drop_table`, `create_table` (except fixture builders under `scripts/fixtures/`), `compact_files`, `cleanup_old_versions`, `optimize`, any migrate helper that rewrites tables on operator paths.
6. **Marker (canonical only):** W1 scripts require  
   `Path(data_dir).resolve().parent / ".lance-debug1-quarantine"`  
   i.e. **`$QUARANTINE_ROOT/.lance-debug1-quarantine`** when `data_dir=$QUARANTINE_ROOT/data`. Optional override: `ELYRA_LANCE_ALLOW_WRITE=1` **and** the same marker path still present (marker is **never** optional for W1). Refuse otherwise with the expected absolute path in the error.
7. **Version sampling:** use `Table.list_versions` / read-only checkout only — **never** `optimize` / `compact_files` / `cleanup_old_versions`.
8. **No production code changes.** Do not edit `elyra/memory/**` or product defaults in lance-debug1 PRs. Merging `BUG-DOSSIER.md` does **not** authorize `_load` changes.

---

## Quarantine layout (mandatory for store open)

`LanceMemoryStore` does **not** open a bare Lance URI alone. Open path uses:

| Path helper | Layout |
|-------------|--------|
| `ElyraPaths.data_dir` | e.g. `/tmp/lance-q-…/data` |
| `memory_root` | `{data_dir}/memory` |
| `lance_root` | `{data_dir}/memory/lance` (lancedb connect URI) |
| `memory_meta_path` | `{data_dir}/memory/meta.json` (**sibling** of `lance/`, not inside it) |
| blobs | `{data_dir}/memory/atoms/` (content_ref hydration) |
| ladder (optional) | `{data_dir}/memory/ladder/` — may be empty |

Copy at least:

1. `data/memory/lance/` (entire tree, including `_versions` / data fragments)
2. `data/memory/meta.json`
3. `data/memory/atoms/` if present (blob spill)
4. `data/memory/ladder/` if present (or create empty `ladder/` for layout parity)

### Canonical tree

```text
$QUARANTINE_ROOT/                          # e.g. /tmp/lance-q-20260729
  .lance-debug1-quarantine                 # CANONICAL marker — only this path
  data/                                    # ELYRA_DATA_DIR / LANCE_DEBUG_DATA_DIR
    memory/                                # memory root (copy target of data/memory)
      meta.json
      lance/                               # lancedb URI → …/data/memory/lance
      atoms/                               # optional blobs
      ladder/                              # optional / empty
```

| Name | Canonical path | Used by |
|------|----------------|---------|
| **Quarantine root** | `$QUARANTINE_ROOT` | marker parent |
| **Marker (only)** | `$QUARANTINE_ROOT/.lance-debug1-quarantine` | `quarantine_copy.sh` (writes), `load_parity.py` (requires) |
| **data_dir** | `$QUARANTINE_ROOT/data` | `ElyraPaths`, `LANCE_DEBUG_DATA_DIR` |
| **memory root** | `$QUARANTINE_ROOT/data/memory` | copy source layout destination |
| **Lance URI** | `$QUARANTINE_ROOT/data/memory/lance` | `api_matrix.py --uri`, `LANCE_DEBUG_URI` |

### Marker algorithm (implement exactly — no alternate locations)

1. `quarantine_copy.sh` always creates/updates **`$QUARANTINE_ROOT/.lance-debug1-quarantine`** (JSON or one-line stamp: source path, UTC time, optional writer PID, `possibly_torn` bool).
2. `load_parity.py` resolves `data_dir` from `--data-dir` / `LANCE_DEBUG_DATA_DIR`, then requires:  
   `Path(data_dir).resolve().parent / ".lance-debug1-quarantine"`  
   i.e. **`{data_dir}/../.lance-debug1-quarantine`** with `data_dir` ending in `…/data`. Refuse with a clear error listing the expected absolute path if missing.
3. Do **not** accept markers at `data/.lance-debug1-quarantine`, `data/memory/.lance-debug1-quarantine`, or the memory-root parent alone.
4. Refuse if `$QUARANTINE_ROOT` resolves under live workspace `data/`.

---

## FORBIDDEN operations (never)

| Operation | Why |
|-----------|-----|
| `drop_table` on live / operator data | Data loss |
| Migrate / rewrite tables on live | Partial rewrite risk (H10 residual) |
| `delete` rows on live | Data loss |
| `compact_files` / `cleanup_old_versions` / table `optimize` on live | Destructive; may change read behavior mid-investigation |
| Delete operator `_versions` | Destroys archaeology |
| Open W1 against unmarked live `data/` | Product corruption risk |
| Dual `lancedb.connect` on live URI as default while writer open | Multi-connect / torn hazard |
| Product patches to `_load` inside this package’s PRs | Violates inspection-only mandate |

---

## Operator checklist (before any W1)

- [ ] Elyra stopped or idle (no promote / encode writes)
- [ ] Quarantine copy of **full** `data/memory` → `$QUARANTINE_ROOT/data/memory`
- [ ] Marker present at `$QUARANTINE_ROOT/.lance-debug1-quarantine` only
- [ ] `LANCE_DEBUG_DATA_DIR=$QUARANTINE_ROOT/data`
- [ ] `LANCE_DEBUG_URI=$QUARANTINE_ROOT/data/memory/lance`
- [ ] Evidence run dir created; `possibly_torn` noted if copy was concurrent
- [ ] Deny-list operations not invoked
