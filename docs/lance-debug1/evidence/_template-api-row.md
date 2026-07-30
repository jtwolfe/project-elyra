# API measurement row template

One row per probe in an evidence run. Prefer structured JSON in `api-matrix.json`; this markdown form is for notes or hand capture.

| Field | Value |
|-------|--------|
| **Run id** | YYYY-MM-DD-run-NN |
| **UTC** | |
| **URI** | |
| **Table** | `atoms` |
| **API** | `count_rows` / `head` / `to_arrow` / `to_lance` / H1b step / … |
| **Args** | e.g. `head(n=386)`, bare, `limit` |
| **num_rows** | |
| **First N atom_ids** | (≤ 20) |
| **Kind histogram** | |
| **embedding_status histogram** | (if columns present) |
| **Duration ms** | |
| **Error** | none / message |
| **Safety class** | R1 |
| **Supports** | H1 / H1a / H1b / … |
| **Bucket** | A / … |

### H1a / H1b checklist

| Check | Result |
|-------|--------|
| `arrow_ids == head(10) ids` (order) | |
| H1b path attempted | `query_public` / `private_async` / `head_n_full` / `to_lance` |
| H1b success path | |
| `n_arrow ≪ n_full` | |

### Example JSON fragment

```json
{
  "api": "to_arrow",
  "num_rows": 10,
  "atom_ids_prefix": ["…"],
  "kind_hist": {"summary": 6, "tool": 4}
}
```
