# Investigations

Sealed forensic packages, post-ship review islands, and host-specific freeze bags.  
**Not product law** — prefer code on `working` and living [STATE](../state/README.md) / [DESIGN](../design/README.md).

| Field | Value |
|-------|--------|
| **Class** | INVESTIGATION / ARCHIVE |
| **Status** | Archive (sealed bags) |
| **Audience** | Archaeology / operators replaying evidence |
| **Normative?** | No — do not rewrite sealed `evidence/**` as new truth |
| **Related** | [../archive/](../archive/) · [../design/docs-reorg-taxonomy.md](../design/docs-reorg-taxonomy.md) KD6, KD12 |

## Index

| Package | Role | Old path (pre-PR5) |
|---------|------|--------------------|
| [lance-debug1/](lance-debug1/) | Sealed Lance load-truncation inspection + product-fix design notes | `docs/lance-debug1/` |
| [meal-continuity-review/](meal-continuity-review/) | BUG-meal-03 meal/continuity fault isolation package | `docs/investigations/meal-continuity-review/` |
| [radeon-vii-freezes/](radeon-vii-freezes/) | LuxPrimata pip / GPU-stack freezes (forensics; not portable restore) | `docs/investigations/radeon-vii-freezes/` |

## Rules (KD12)

| May change in docs reorg | Must not change |
|--------------------------|-----------------|
| Package README banners / path pointers | Sealed `evidence/**` JSON and sealed run notes **content** |
| Test path constants (e.g. lance `SCRIPTS`) | Rewriting historical hypothesis tables as new product truth |
| Script **usage/help** path strings | Deleting investigation trees |

## Related living paths

| Living doc | Role |
|------------|------|
| [state/radeon-vii/](../state/radeon-vii/) | Radeon VII operator start + NOTES/VENV/STACK |
| [radeon-vii-dev/scripts/](../radeon-vii-dev/scripts/) | ROCm / embed smoke scripts (not freezes) |
| [state/known-bugs.md](../state/known-bugs.md) | Product bug registry (links into investigation packages) |
| [state/memory/](../state/memory/) | Memory phase honesty |
| [archive/](../archive/) | Early research notes + status snapshots |
