# Archive (not freeze)

Long research notes, stale status snapshots, and historical freezes kept for history.  
**Do not treat these as build law** — prefer code on `working`, living [STATE](../state/README.md), and [DEV](../dev/README.md) process law.

| Field | Value |
|-------|--------|
| **Class** | ARCHIVE |
| **Status** | Archive |
| **Audience** | Archaeology |
| **Normative?** | No |
| **Related** | [../investigations/](../investigations/) · [../design/docs-reorg-taxonomy.md](../design/docs-reorg-taxonomy.md) §3 archival criteria |

## Contents

| File | Was about |
|------|-----------|
| [reflection-memory-and-lineage.md](reflection-memory-and-lineage.md) | Prior Elyra gens vs greenfield; Stretch 2 memory placement options |
| [reflection-moments-and-memory-scope.md](reflection-moments-and-memory-scope.md) | Moments, linear atoms, strain, opaque sleep |
| [project-status-pass.md](project-status-pass.md) | 2026-07-26 whole-project status snapshot (stale tip names; superseded by board + root README + STATE) |

Useful ideas already folded into Stretch 1 / overview: moment = do-loop, self≠user, sparse sleep later, separate wake queue.

## Archival criteria (when to put something here)

Age alone is **not** enough. Prefer `archive/` or `investigations/` + banner over delete (KD6).

| Criterion | Action | Examples |
|-----------|--------|----------|
| Explicitly superseded setup path | Freeze banner + archive move (or leave DESIGN with Freeze) | Gemma/llama `inference.md` body (still at `docs/inference.md` until full STATE Grok inference page exists) |
| Sealed evidence bag; product fix landed | `investigations/` + Archive banner; do not rewrite sealed JSON | `investigations/lance-debug1/` |
| Investigation island post-ship | `investigations/` | `investigations/meal-continuity-review/` |
| Status snapshot superseded by board/code | Archive | `project-status-pass.md` |
| HW freezes / pip freezes | `investigations/` | `investigations/radeon-vii-freezes/` |
| Early research folded into Stretch 1 | Already here | reflection-*.md |
| Long-unupdated but still sole description of shipped behaviour | **Do not archive** — promote to STATE or banner Shipped | `state/stretch-1.md` runtime contract |
| Active design / open implementation | DESIGN Active — never archive by age | board recategorization, open embed residuals |

### Freezes still at `docs/` root (not yet moved)

| Path | Why still here | Partial successor |
|------|----------------|-------------------|
| [../inference.md](../inference.md) | Historical Gemma/llama freeze; test pins content; full STATE Grok inference page not landed | [../state/usage-and-pacing.md](../state/usage-and-pacing.md) + root README |
| [../live-eval.md](../live-eval.md) | Live 3-attempt protocol freeze (Gemma stages historical) | `scripts/live_eval/` fail-closed + hermetic scenario tests |

Move these into `archive/` (e.g. `archive/inference-gemma-llama.md`) only after a STATE Grok inference page exists or path-pin tests are updated deliberately.

## Investigations (sibling class)

Sealed forensic / HW islands live under **[../investigations/](../investigations/)** — not under this folder — so evidence bags stay whole-tree movable without mixing with prose research notes.
