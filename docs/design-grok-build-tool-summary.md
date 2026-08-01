# Summary: Elyra Grok Build instrument design

**Output:** `/tmp/grok-1000/grok-design-doc-cf9024a3.md`  
**Date:** 2026-08-01 (revised post-review + re-review cf9024a3)  
**Branch context:** `feature/grok-build-tool` · Issue #109 · GI Phase 1  
**Review:** `/tmp/grok-1000/grok-design-review-cf9024a3.md` — pass1 22/22 + re-review 7/7 addressed  

## What was produced

A full Phase 1 design for Project Elyra’s **Grok Build host instrument** — not MVP-only. Post-review revision closed blocking holes: seeded `GROK_HOME` skills, live `ensure_fresh_access` provider, async-by-default long modes + supervisor reaper, deep_research experimental until spike, human-gate policy, working-branch migration, usage_bridge field map, artifact harvest, validation table, and PR plan reordering.

## Core proposal

| Decision | Choice |
|----------|--------|
| Person vs instrument | Elyra = person; Grok Build = coding instrument only |
| Tool surface | Single host builtin **`grok_build`** with modes |
| Modes | `prompt`, `design`, `implement`, `execute_plan`, `deep_research`, `review` (`pr_babysit` later) |
| Implementation | Broker host **`grok` headless CLI** / skills — do **not** reimplement Grok factories in Python |
| Auth | PE `xai_oauth` via existing `resolve_access_token_for_tool`; access-only; fail-closed |
| Graphite | Optional; product default **plain-git** (`--no-graphite`) |
| Base branch | **`working`** integration tip; promote to `main` with full suite |
| Skills | Extend **github-workflow**; add **self-improve** (L/M/H + H-spine) from the start |
| Modules | Thin `elyra/tools/builtin/grok_build.py` + new `elyra/instrument/*` package (no god file) |

## Codebase anchors used

- `elyra/secrets/inject.py` — `GROK_BUILD_TOOL_NAMES`, access inject (ready, unwired)
- `elyra/llm/xai_oauth.py` — `ensure_fresh_access`, public client `b1a00492-…`
- `elyra/tools/registry.py` + bundled `web_search` / `git_*` / `gh_*` patterns
- `skills/bundled/github-workflow` (rails already mention `grok_build`)
- Host Grok skills: design, implement, execute-plan, review, deep-research slash/workflows
- Engineering principles, tools-and-skills, OAuth design, promotion governance

## Document sections

Overview · Background · Goals/Non-goals · **Key Decisions (KD1–KD14)** · Proposed Design (architecture, sequences, mode table, auth handoff, module layout, TOOL/schema sketches, skill outlines, L/M/H tree, branch law) · API · Data model · Alternatives · Security · Observability · Tests · Dogfood · Rollout · Risks · Open Questions · References · **PR Plan (PR0–PR8)**

## PR stack (matches design)

| PR | Scope |
|----|--------|
| **PR0** | Design + branch law + GI supersession + **github-workflow tip → working** |
| **PR0a** | Headless spike notes (deep_research / human-gate) |
| **PR1** | Pure modes/argv/validate/result/redact |
| **PR2** | Seeded GROK_HOME + live auth_provider (`sys.executable`) + process |
| **PR3** | Jobs + **supervisor reaper** + usage_bridge; wake kind **`background`** |
| **PR4** | Bundled `grok_build` tool package |
| **PR5** | self-improve + github-workflow mode/async rails |
| **PR6** | live_grok dogfood D1–D13 |
| **PR7** | Hardening |
| **PR8** | Merge → `working` (after D3/D6 green) |

### Re-review closures (round 2)
- Completion wake: `background` only (not `instrument_job`)
- Auth provider: absolute `sys.executable`; `expires_in` from `seconds_until_expiry`
- Provider hang backstop: timeout + process group kill
- Tip skill law in **PR0** (not delayed to PR5) 

## Post-approval

Land the design at **`docs/design-grok-build-tool.md`** (called out in PR0). Create **`working`** early as integration tip for execute-plan stacks.

### Final wire note
- Reaper **must** share PresenceWorker’s **one** `WakeQueue` instance (Supervisor injects); private reaper queue is incorrect and would drop completion wakes.
