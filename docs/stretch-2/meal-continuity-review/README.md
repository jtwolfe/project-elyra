# Meal formation & continuity edge review — findings package

| Field | Value |
|-------|--------|
| **Bug** | `BUG-meal-03` / [#93](https://github.com/jtwolfe/project-elyra/issues/93) |
| **Review plan** | [`../../design/memory/design-meal-formation-continuity-review-plan.md`](../../design/memory/design-meal-formation-continuity-review-plan.md) |
| **Product draft refined by this report** | [`../../design/memory/design-instance-continuity-glass-tail-directed-keep.md`](../../design/memory/design-instance-continuity-glass-tail-directed-keep.md) |
| **Date** | 2026-07-30 |
| **Scope** | WP1–WP2 executed (static SA-1…SA-9b + offline recompose). WP3 live **skipped** (host API down). |
| **Product code** | **Unchanged** — documentation / evidence only |

## Package contents

| Path | Role |
|------|------|
| [`REPORT.md`](REPORT.md) | Primary fault report (exec summary, faults F-01…, fix portfolio, DRAFT-EXTENSIONS, OQs) |
| [`CODE-PATH-MAP.md`](CODE-PATH-MAP.md) | Confirmed call graphs with file:line symbols |
| [`EDGE-MATRIX.md`](EDGE-MATRIX.md) | Path matrix P1–P9 observed mechanisms + tip must/nice |
| [`evidence/sa9b-e6d460f2/`](evidence/sa9b-e6d460f2/) | Offline recompose + tape/glass forensics for rockets class |

## Dogfood anchor

- **Moment:** `e6d460f2-4087-42cd-870f-d34a89b6feaf`
- **Glass user:** `04f85fc6-195a-4b3c-b0bf-8b307c7baa2f` — *what is the coolest thing you remember about rockets?*
- **Glass assistant (fail):** `37ec1721-930d-4045-9d0c-819c3c1c1baf` — closed philosophy/fabric status
- **Wait:** `c13ae60a-40ed-45c6-a75a-035c1a78f05c` prompt *Anything else?* (waits.json / prior tape — **not** on glass)

## How to re-run offline recompose (SA-9b)

Read-only. Point at a dogfood data root with `messages.jsonl` + moments + memory jsonl (do **not** open lance if the process segfaults on unsupported wheels):

```bash
# From repo root; use jsonl MemorySettings only for safety
python3 -c '
from pathlib import Path
from elyra.config import resolve_paths
from elyra.memory import MemorySettings
from elyra.memory.jsonl_store import JsonlMemoryStore
from elyra.memory.types import Atom
from elyra.memory.meal import compose_meal, compose_outer_messages
from elyra.loop.orient_slice import format_skill_bias
from elyra.loop.context import fill_orient
from elyra.prompts.loader import load_prompt

MOMENT = "e6d460f2-4087-42cd-870f-d34a89b6feaf"
MSG_ID = "04f85fc6-195a-4b3c-b0bf-8b307c7baa2f"
ROCKETS = "what is the coolest thing you remember about rockets?"
WHY = "wait reply (wait_id=c13ae60a-40ed-45c6-a75a-035c1a78f05c)"
paths = resolve_paths(Path("/path/to/project-elyra"))
store = JsonlMemoryStore(paths, MemorySettings(backend="jsonl", semantic_enabled=False))
obs = Atom(atom_id="a_recompose", kind="observation", moment_id=MOMENT,
           t_start="2026-07-30T08:47:53Z", content_text=ROCKETS, media_ids=(),
           meta={"wake_message_id": MSG_ID})
system = load_prompt("system", paths=paths)
orient = fill_orient(load_prompt("orient", paths=paths), now="…", self_digest="",
                     user_digest="", why_now=WHY, goals="", skill_catalog="",
                     skill_bias=format_skill_bias("wait_reply"))
pkg = compose_meal(store, open_moment_id=MOMENT, budget_tokens=50000,
                   system_text=system, orient_text=orient, open_moment_atoms=[obs])
print(pkg.channels_present, pkg.total_tokens)
'
```

Live capture (when host warm) — **not** used for this PR:

```bash
curl -sS http://127.0.0.1:8787/api/memory/context | jq '{source, channels: .meal.channels_present, tokens: .meal.channel_token_totals}'
# Expect source == rebuild_outer. Do not use ?compose=1 as historical rockets frame.
```

## PR-R3 exit criteria checklist

| Criterion | Status |
|-----------|--------|
| EDGE-MATRIX P1, P2, P4, P5 static | ✅ `EDGE-MATRIX.md` |
| P2 offline recompose + carrier ranking | ✅ `evidence/sa9b-e6d460f2/` |
| Live E-P2 | ⏭ **live skipped** (API down) |
| DRAFT-EXTENSIONS glass-tail + keep B5/B5b + OQ4/6/7 | ✅ `REPORT.md` § DRAFT-EXTENSIONS |
| Rockets fix eval includes tail-only vs tail+orient (B12) | ✅ F-01 / F-02 |
| B10 only as post-S1 risk | ✅ F-10 note |
| No product meal behavior code changes | ✅ |
