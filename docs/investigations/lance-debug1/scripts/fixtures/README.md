# Fixtures (tests only)

Tiny synthetic lancedb tables for hermetic CI / unit probes of `api_matrix.py`.

| File | Role |
|------|------|
| `build_tiny_atoms.py` | Creates a URI dir with table `atoms` (default 25 rows) + `.lance-debug1-fixture` marker |

**Not operator data.** `create_table` is allowed only under this fixtures directory (SAFETY deny-list exception).

```bash
python docs/investigations/lance-debug1/scripts/fixtures/build_tiny_atoms.py --out /tmp/tiny-lance
python docs/investigations/lance-debug1/scripts/api_matrix.py --uri /tmp/tiny-lance --out /tmp/api-matrix.json
```

Prefer Python 3.12 when native lancedb is broken on 3.14.
