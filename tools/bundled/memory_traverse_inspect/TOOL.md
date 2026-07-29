---
name: memory_traverse_inspect
description: Read capped body slices for considered memory atoms before keeping. Use when 80-char frontier labels are insufficient for keep decisions.
kind: read
---

# memory_traverse_inspect

Normative mid-walk body access (KD-A17). Caps:

- Max ids: `traverse_inspect_max_ids` (default 4)
- Chars per id: `traverse_inspect_chars_per_id` (default 800)
- Total chars: `traverse_inspect_max_total_chars` (default 2400)

- Required: `atom_ids` — list of durable atom ids

## Result

```json
{ "ok": true, "atoms": [{ "atom_id": "…", "kind": "…", "body": "…", "truncated": false }] }
```

## Errors

| `error_reason` | Meaning |
|----------------|---------|
| `traverse_disabled` | Feature flag off |
| `traverse_unavailable` | Ports missing |
| `atom_not_found` | One or more ids missing (fail closed; no invented bodies) |
| `invalid_args` | Empty / missing atom_ids |
