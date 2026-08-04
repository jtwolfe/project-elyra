# Engineering principles

How we build Elyra. Applies from the first module.  
**What the system does:** [stretch-1.md](../state/stretch-1.md), [overview.md](../state/overview.md).  
**This doc:** how we structure and ship code without re-growing bloat.

---

## 1. Modular packages, no god modules

| Rule | Meaning |
|------|---------|
| **One job per module** | If the name needs “and”, split it |
| **Narrow public API** | Internals stay private; compose via imports |
| **Reuse over copy** | One implementation of shared logic |
| **No god objects** | Nothing knows users + tools + LLM + time + prompts at once |
| **New capability → new module** | Not another branch in a 2k-line file |

### Suggested layout (Stretch 1)

```text
elyra/
  config.py           # paths, ELYRA_HOME
  presence/           # wake queue, worker, interjections
  moment/             # open/close, tape/beats
  loop/               # do-loop: model ↔ tools
  tools/              # registry, runners, verify/promote
  skills/             # catalog + load
  goals/              # goals + tasks ledger
  identity/           # self store
  users/              # per-user stores
  speak/              # speak tool + transports
  sandbox/            # one persistent workspace
  llm/                # llama client, server launch
  prompts/ loader     # loads files — does not own text
cli / runtime entry
tests/                # mirrors package layout
prompts/              # skill bodies may live under skills/; shared system prompts here
skills/               # SKILL.md packages (or under ELYRA_HOME)
tools/                # tool packages on disk (or under ELYRA_HOME)
```

Higher layers **orchestrate**; domains stay usable alone.

**Checklist before merge:** one-sentence description? Importable without unrelated baggage? Clear boundary if deleted?

---

## 2. Small units with explicit scope

Functions/classes stay **holdable in the head** (~50 lines of logic or less).

Every non-trivial unit declares scope:

```python
# Scope: parse ISO-8601 duration into timedelta.
# In scope: PT5M, 5m, rejection of empty/negative.
# Out of scope: natural language ("next Tuesday").
```

**Split when:** deep nesting, hard to name, hard to test, mixed I/O + pure logic.

**Preferred shape:** parse → pure compute → persist (separate functions).

---

## 3. Tests are part of the feature

No module ships without tests. “Temporary” and “simple” are not exemptions.

| Layer | What |
|-------|------|
| **Unit** | Pure logic, parsers, path resolution — fast, no network |
| **Contract** | Public APIs and documented edges |
| **Integration** | Wiring (e.g. registry + sandbox stub) |
| **Regression** | Every bugfix gets a test that would have failed |

- Layout: `elyra/foo.py` → `tests/test_foo.py` (or package mirror).  
- Prefer **deterministic** tests; mark anything that needs real LLM / GPU.  
- Name after behaviour: `test_speak_failure_returns_reason`, not `test_case_3`.  
- Documented edge case without a test = **not done**.

Confidence over coverage percentage.

---

## 4. Skills and prompts live on disk

| Kind | Location | Rule |
|------|----------|------|
| **Skills** | `skills/.../SKILL.md` (or under `ELYRA_HOME`) | Playbooks; code loads, does not embed |
| **Shared system / orient prompts** | `prompts/` | One concern per file; YAML frontmatter when useful |
| **Tool packages** | `tools/{bundled,local,drafts}/` | Same format for created and builtin |

- No multi-page prompt strings buried in Python.  
- Catalog short; full skill body on load.  
- create-tool / create-skill must produce the **same formats** as hand-written packages (dogfood).  
- Fail-closed: drafts not callable; verify before promote ([tools-and-skills.md](../state/tools-and-skills.md)).

---

## 5. Configuration: defaults first, few env vars

| Prefer | Over |
|--------|------|
| Sensible defaults from layout | New env var per feature |
| `elyra.toml` (or similar) for overrides | Flag sprawl |
| One home root | Path env vars for every folder |

**Canonical env (keep short):**

| Variable | Purpose | Default |
|----------|---------|---------|
| `ELYRA_HOME` | Root for data, config, runtime | Project root |

Everything else: `ELYRA_HOME` + config file + conventional dirs (`model/`, `data/`, `skills/`, `tools/`).

New env vars only if they cannot live in config, are documented, tested, and stay rare.

---

## 6. Language and design debt

- **Skills / tools / moments / wakes / host jobs** — not *organs*, faculties, or stage machines.  
- **Reasoning** = provider private stream; not a product monologue subsystem.  
- **Contract over ceremony:** if behaviour needs a new forever-on phase, redesign as skill/tool/queue instead.  
- **Stretch discipline:** Stretch 1 code must not pull in Stretch 2 graph/sleep products “just in case.” Leave migratable hooks, not half-built hypergraphs.  
- Prefer deleting flags over stacking recovery lattices.

---

## 7. Reliability patterns

| Area | Practice |
|------|----------|
| **LLM** | Serialize llama-server access; long timeouts; never assume speak succeeded without tool result |
| **I/O** | Pure logic separate from disk/network; append-only where possible for tapes |
| **Errors** | Typed/explicit failure reasons to the model and to glass; no silent swallow |
| **Concurrency** | Single do-loop worker; interjections into the current moment, not a second mind |
| **Deps** | Pin consciously; Python 3.12 aligned with prior Elyra unless we document a change |
| **Docs** | Code follows [stretch-1.md](../state/stretch-1.md); if behaviour changes, update the contract in the same change |

---

## 8. Definition of done (per change)

A change is done when:

1. Scope is clear (in/out)  
2. Tests cover the behaviour and edges  
3. Public API is minimal and documented  
4. No new god-module growth  
5. Prompts/skills/tools on disk if AI-facing  
6. Config defaults work without a forest of env vars  
7. Stretch 1 non-goals still hold  
8. Issue + board hygiene completed for the change (see §9) when the work is tracked work  

---

## 9. Development structure (branch + issue hygiene)

Every bit of product work after the v0.1 board rework must fall within this structure — including work the operator asks for via Grok Build. Agents and humans **recommend and follow** the same workflow; do not invent a parallel tip or silent board.

### Normative pointers

| Topic | Authority |
|-------|-----------|
| Tip / branch / promote / pins | [branch-law.md](branch-law.md) |
| Operating pin convention (manual now; live on v0.1) | [operating-pins.md](operating-pins.md) |
| Package + Projects judgment playbook | skill `github-workflow` · [tools-and-skills.md](../state/tools-and-skills.md) |
| Packaging priority labels | Exactly one of `v0.1-gate` \| `backlog` \| `research` on every open issue (see design-v0.1-ready-board-recategorization.md) |

### Branch law (short)

| Rule | Detail |
|------|--------|
| Integration tip | **`working`** — base for feature / fix / execute-plan / self-mod work |
| PR base | Open PRs against **`working`**, not `main` |
| Stable | **`main`** — promote from `working` only with full suite + noise review + human approve |
| Short-lived branches | `feature/*`, `fix/*`, `self/*`, `exec/*` / `execute-plan/*` — delete after merge |
| Never | Commit directly to `main`/`working` without explicit human request; force-push either; auto-merge to `main`; silent operating-pin move |

### Before any change (issue + branch workflow)

Grok Build, `github-workflow`, and human operators should **always** recommend this sequence for multi-step repo work:

1. **Inspect issues** — search open issues / Project #2 for an existing home; read body and packaging label.
2. **Update or create** — update the issue (scope, residual framing, acceptance); create a new issue if none fits. Apply packaging label with the triad recipe (remove `v0.1-gate`/`backlog`/`research` then add exactly one). Parent under the right epic when it is a packaging gate.
3. **Branch type** — short-lived branch from current `working` (`feature/…`, `fix/…`, etc.). Prefer worktree isolation for multi-file work.
4. **Work** — implement; tests; docs with the change. Do not pile unrelated edits on tip branches.
5. **Update issue / board** — status honesty (Todo / In Progress / Done / Deferred), dates if gated, close only when acceptance is met or a **named successor** owns residual work. Comment evidence without secrets.

Even when the operator asks via Grok Build for a “quick fix,” prefer: inspect → issue update/create if required → typed branch → work → board update. Skip only trivial one-line ops the human explicitly scopes as untracked.

### After board rework

- Packaging-critical path lives under epic **#111** (`v0.1-ready: packaging & dogfood checkpoints`) and exit criteria **#112**.
- Do not re-grow long-lived personal tips; restack stale stacks onto `working` (branch-law ~10-day rule).
- Tip map honesty: document `working` / `main` / operating pin; do not teach superseded `grok-improvement` as the integration tip.

Docs for this structure live under the **DEV** class (§10). When adding or moving docs, follow the taxonomy rules below rather than inventing a parallel shelf.

---

## 10. Docs taxonomy (STATE / GOAL / DESIGN / DEV)

Normative process rules for documentation layout, banners, freezes, and supersession.  
**Authoritative design + inventory:** [docs-reorg-taxonomy.md](../design/docs-reorg-taxonomy.md) (issue [#121](https://github.com/jtwolfe/project-elyra/issues/121)).  
**Hub:** [docs/README.md](../README.md). Class indexes: [state/](../state/) · [goal/](../goal/) · [design/](../design/) · [dev/](./) · [archive/](../archive/) · [investigations/](../investigations/).

### Four classes

| Class | Audience | Contents | Living? |
|-------|----------|----------|---------|
| **STATE** | Operators / users | As-implemented behaviour, run/deploy, honest limits, known bugs, ops checklists | Yes — prefer code on `working` |
| **GOAL** | Product direction | North stars, phase goals, v0.1 claim — *what* / *why*, success bars | Yes but **short**; link to DESIGN |
| **DESIGN / PLAN** | Implementers | Designs and PR plans; freeze / shipped / superseded banners; history kept | Frozen or active |
| **DEV** | Developer + Grok Build | This file, branch-law, operating pins, governance, board hygiene | Yes — normative process |

**Conflict rule:** code on `working` > STATE > GOAL prose. DESIGN freezes are archaeology unless Status is **Active**. **DEV** wins for tip / branch / pin law ([branch-law.md](branch-law.md)).

### Prefer code on `working`

- Living STATE and DEV describe intended law; if prose and tip disagree, **trust the code on `working`** and fix the doc in the same change when behaviour is intentional.
- Do not invent a second “source of truth” folder outside the four classes + archive/investigations.

### Status banner schema

Every substantial doc (especially DESIGN, freezes, archive/investigation packages) gets a **metadata table** near the top. Living STATE/DEV may use a shorter form.

| Status | Meaning |
|--------|---------|
| **Active** | Normative for ongoing work |
| **Shipped** | Implemented; keep as contract or decision archaeology |
| **Superseded** | Replaced by a **named** successor; do not follow for new work |
| **Freeze** | Historical procedure frozen in place; not setup law |
| **Archive** | Research / snapshot / sealed bag; not build law |
| **Draft** | Not yet approved for execution (DESIGN only) |

Template (fields as needed):

```markdown
| Field | Value |
|-------|--------|
| **Class** | STATE \| GOAL \| DESIGN \| DEV \| ARCHIVE \| INVESTIGATION |
| **Status** | Active \| Shipped \| Superseded \| Freeze \| Archive \| Draft |
| **Audience** | Operators \| Product \| Implementers \| Developer+Grok Build |
| **Normative?** | Yes \| No — prefer code on `working` when conflict |
| **Successor** | path or issue (required if Superseded) |
| **Supersedes** | path (optional) |
| **Last verified** | YYYY-MM-DD (STATE/DEV living docs) |
| **Related** | links |
```

Non-Active / non-living docs: one-line callout under the H1 (`Status: Superseded / Freeze / Archive` + successor). Full schema and examples: design [§2 banner schema](../design/docs-reorg-taxonomy.md#2-document-status-banner-schema).

### Freeze non-edit

- **Freeze** and **Archive** bodies are not bulk-rewritten in reorg or drive-by PRs (banners + path/index fixes only).
- Sealed investigation evidence (`investigations/**/evidence/**`, sealed run notes) is **not** rewritten — update package README, test path constants, and script usage/help only when needed.
- Prefer `archive/` or `investigations/` + banner **over delete** (non-destructive).

### GOAL short; DESIGN long

- GOAL pages stay **short** (north star, success bars, links) — roughly one to two screens.
- Full designs, PR plans, and long discussion live under **DESIGN** (or long-form GOAL discussion with a short claim entry — not a second full design dump).
- No dual full design bodies in GOAL **or STATE** (KD3).

### Dual-home rule

- A file lives in **exactly one** class folder. The hub indexes by class; do not leave a second full copy elsewhere.
- Ambiguous cases follow the dual-home table in the design ([§4.7a](../design/docs-reorg-taxonomy.md#47a-dual-home-resolution-table-authoritative--kd14) / KD14): e.g. full harness → DESIGN + short STATE checklist extract; meal composition → DESIGN only; architecture manuals → STATE; phase `design-*.md` → DESIGN.

### Archive decision tree

Age alone is **not** the primary signal. Classify with the decision tree and criteria in the design:

- [§3 Archival criteria and decision tree](../design/docs-reorg-taxonomy.md#3-archival-criteria-and-decision-tree)
- Living criteria also summarized in [archive/README.md](../archive/README.md)

Route operators → STATE; product north star → GOAL; implementers → DESIGN; Developer/agents → DEV; archaeology → archive/investigations.

### When docs change with code

- Behaviour change → update STATE (or runtime contract) in the **same** change (§7 / §8).
- New design or PR plan → DESIGN with banner; link from hub/catalog; do not park full designs only under GOAL.
- Process / tip / pin law → DEV (this file, branch-law, pins).
- Superseding a path → set Status **Superseded** or **Freeze**, name **Successor**, and prefer archive move when it is no longer living law.

---

## Summary

| # | Principle | One line |
|---|-----------|----------|
| 1 | Modularity | One job per module; compose; no gods |
| 2 | Small units | Explicit scope; parse / compute / persist |
| 3 | Tests | Every module; behaviour names; edges prove done |
| 4 | Disk AI text | Skills + prompts + tools as packages; load, don’t embed |
| 5 | Config | Defaults + few env vars (`ELYRA_HOME`) |
| 6 | Language / stretch | No jargon debt; no Stretch 2 smuggled early |
| 7 | Reliability | Serialize LLM; honest tool errors; single worker |
| 8 | Done | Tests + boundaries + docs with the change |
| 9 | Development structure | Issue inspect/update → typed branch from `working` → board honesty |
| 10 | Docs taxonomy | STATE/GOAL/DESIGN/DEV; banners; freeze non-edit; GOAL short; dual-home |

These principles are how we avoid another 12k-line cycle file and another flag lattice. Product shape stays in [stretch-1.md](../state/stretch-1.md); this doc keeps the code honest.
