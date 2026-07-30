# P08 — Version archaeology

| Field | Value |
|-------|--------|
| **Status** | Ready (PR3); **optional** if H1a+H1b already proven |
| **Safety class** | **R1** (read-only; careful) |
| **Prove / disprove** | H3 historical growth; H4 only if still open; H10 historical collapse only |
| **Script** | [`docs/lance-debug1/scripts/version_sample.py`](../scripts/version_sample.py) |
| **Evidence** | `evidence/YYYY-MM-DD-run-NN/version-sample.json` |
| **Writeup** | [../VERSION-ARCHAEOLOGY.md](../VERSION-ARCHAEOLOGY.md) |

## Purpose

Sample Lance versions safely without compact/optimize/cleanup. Document version growth (H3) and whether historical row counts show non-monotonic **collapse** (H10 historical only).

## When to run

Prefer **after** P01 confirms or refutes default-limit (H1a/H1b).

| Situation | P08 priority |
|-----------|----------------|
| H1a+H1b high-confidence | **Optional polish** (ops interest + H10 residual) — not on critical path to provisional dossier |
| H1a/H1b still open | Useful adjunct before elevating fragment-only (H4) theories |
| Need H10 historical claim | **Required** — collapse must be measured, not assumed |

## Prerequisites

- [ ] Read [../SAFETY.md](../SAFETY.md) (R1 allowlist / deny-list)
- [ ] Prefer quarantine URI (`$LANCE_DEBUG_URI`)
- [ ] Live URI only if writer idle / concurrent-read accepted
- [ ] Do **not** run compact/optimize/cleanup on any operator path

## Procedure (executable)

```bash
# Prefer same quarantine as P01
export LANCE_DEBUG_URI=/tmp/lance-q-YYYYMMDD/data/memory/lance
RUN=docs/lance-debug1/evidence/$(date +%Y-%m-%d)-run-01
mkdir -p "$RUN"

python docs/lance-debug1/scripts/version_sample.py \
  --uri "$LANCE_DEBUG_URI" \
  --samples 5 \
  --out "$RUN/version-sample.json"
```

Design CLI alias:

```bash
python docs/lance-debug1/scripts/version_sample.py \
  --dataset "$LANCE_DEBUG_URI" \
  --samples 5 \
  --out "$RUN/version-sample.json"
```

Optional: `--subprocess-native` for per-version `lance.dataset` counts in a child process (segfault isolation). `--list-only` for metadata without per-version row counts.

## Safe sampling plan

1. `Table.list_versions` — inventory; record `n_versions`
2. Sample versions: **first, 25%, 50%, 75%, latest** (default `--samples 5`)
3. Per sample: prefer read-only `checkout` + `count_rows` / `to_lance` — **never** bare `to_arrow` as full count
4. If using `lance.dataset`, prefer **subprocess** with timeout
5. Record `version_id`, `num_rows` when available, optional max `t_start` sample on current
6. **Never** `cleanup_old_versions` / `compact_files` / `optimize`

## H10 residual framing (normative for this package)

| Claim | When valid |
|-------|------------|
| H10 explains **today’s** process-thin vs full disk | **No** — if full APIs already show large latest corpus, active bug is H1→H2 |
| H10 **historical collapse** | Only if samples show non-monotonic row-count drop |
| H10 **residual future risk** | **Always document**: `_migrate_vector_schema` and `_promote_staging_table` still use bare `to_arrow` (see [../TO-ARROW-CALLERS.md](../TO-ARROW-CALLERS.md)) |

Migration sites that still materialize with bare `to_arrow`:

- `elyra/memory/lance_store.py` — `_migrate_vector_schema`
- `elyra/memory/lance_store.py` — `_promote_staging_table`

These are **not** fixed in lance-debug1 PRs. Residual means: a future migrate/reopen could rewrite from a thin set even after `_load` is fixed, unless those call sites are addressed in a later fix design.

## Expected if H3 (healthy write history)

- Large `n_versions` (dogfood often 1000+) under merge_insert growth
- Latest full-read `num_rows` large and non-decreasing under continued use

## Expected if H10 historical unsupported (common after H1a+H1b)

- Sampled row counts monotonic non-decreasing (or incomplete counts)
- Latest `num_rows` large → H10 does **not** explain process-thin
- Still record residual migrate risk in dossier appendix

## Forbidden

| Operation | Why |
|-----------|-----|
| `compact_files` / `cleanup_old_versions` / `optimize` | Destructive; changes archaeology mid-investigation |
| `delete` / `drop_table` | Data loss |
| Delete operator `_versions` | Destroys history |
| Treating P08 as blocking provisional H1a+H1b+H2 dossier | KD14 early-exit |

## Pass / fail (hypotheses)

| Hypothesis | Pass |
|------------|------|
| **H3** | Version history grows; latest full APIs large |
| **H4** | Only if H1a+H1b fail **and** fragment/version shape uniquely explains thin `to_arrow` |
| **H10 historical** | Non-monotonic collapse in sampled row counts |
| **H10 residual** | Documented always when migrate sites still use bare `to_arrow` |

See [../VERSION-ARCHAEOLOGY.md](../VERSION-ARCHAEOLOGY.md) and design §P08.
