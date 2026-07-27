# Design: Capability Growth — Search, Browser, Package VCS, Secrets & Self-Improvement Foundations

| Field | Value |
|-------|--------|
| **Document** | Capability growth: search, browse, package VCS, secrets, workflow skills |
| **Author** | Design (Grok) |
| **Date** | 2026-07-27 |
| **Status** | Draft |
| **Product** | project-elyra |
| **Branch base** | `grok-improvement` |
| **Related** | `docs/tools-and-skills.md`, `docs/time-and-identity.md`, `docs/design-identity-self-other-multi-user.md`, `docs/project-status-pass.md`, `docs/grok-improvement-plan/`, `prompts/system.md` |
| **Parallel pattern** | Identity draft → promote + versions; create-tool draft → verify → promote |

---

## Overview

This design expands Elyra’s tool and skill surface so she can research the web, browse pages, work with git/GitHub, manage secrets safely, and recover from broken self-grown packages. It also lays the rails for the larger self-improvement stack (Grok Build, worktrees, branch discipline, GitHub Projects).

**Core stance (locked):**

- **Thin tools + judgment skills.** Tools do one bounded thing. Skills encode when/how/stop/cite. (Matches Elyra’s own ideal-case guidance on search.)
- **Identity-aligned package VCS.** Tools and skills get the same draft → (verify) → promote → versions → revert culture that identity already has.
- **Secrets never in model context.** Tool-scoped injection / broker pattern; user-managed + Elyra-managed.
- **Agency-preserving.** Harden prompts and skills for safety, verification, and honest stop conditions — do not reduce initiative or invent a second mind.
- **Self-improvement ready.** `github-workflow` skill teaches the conventions that Phase 1 `grok_build` and Phase 2 continuity will use.

**One-sentence outcome:** Elyra gains reliable search, real browser control, versioned package recovery, and secret-safe git/GitHub work, while the skill layer turns those tools into disciplined research and self-improvement loops that Grok Build can later amplify.

---

## Background & Motivation

### Why these capabilities now

Stretch 1 shipped the growth surface (create-tool / create-skill, draft → verify → promote) and identity versioning. The next natural expansion is the research + action surface that makes continuous improvement and external work practical, plus the recovery and secret rails required for safe self-modification.

Current gaps:

| Gap | Impact |
|-----|--------|
| No native search | Research relies on external or hallucinated knowledge; continuous work cannot verify facts |
| No browser primitives | Cannot interact with live pages, forms, or dynamic content |
| Promote is one-way | Bad local tool/skill packages cannot be recovered without external git |
| No secrets model | `gh` / git auth and future API keys cannot be used safely |
| No disciplined research playbook | Search (when added) risks becoming snippet-only or endless loops |
| Self-improvement conventions undocumented | Phase 1 `grok_build` has no ready branch / worktree / Projects culture to land into |

Elyra’s own ideal-case research guidance (operator conversation 2026-07-27) is the north star for the skill layer: multi-query, triage, stop conditions, citations, ledger hooks — not a fatter search API.

### Current architecture (relevant)

- Tools: `tools/{bundled,local,drafts}/<name>/` with `TOOL.md` + `schema.json` + `runner.json`; promote copies draft → local with no archive.
- Skills: `skills/{bundled,local}/<name>/SKILL.md`; install is one-shot.
- Identity: full draft → promote + `versions/` archive + meta index (the pattern we mirror).
- Growth tools: `install_tool_draft`, `verify_tool`, `promote_tool`, `install_skill`.
- Git / GitHub: none native; shell via `run` is possible but unstructured and secret-hostile.
- Secrets: none.
- Glass: Status, Identity panel; no secrets surface.

---

## Goals & Non-Goals

### Goals

1. **Package VCS** for tools and skills, modeled on identity (get / draft / (verify) / promote + versions archive + revert).
2. **Native search** via `ddgs` (featureful, zero-key default) + later optional `web_fetch`.
3. **Playwright browser primitives** (accessibility-first, headless, session-aware) + `browse` skill.
4. **Structured git + `gh` tools** with secret injection points.
5. **Secrets system** (user-managed + Elyra-managed, tool-scoped, Glass UI).
6. **Judgment skills**: research family (inspired by Elyra’s ideal), `browse`, `github-workflow` (self-mod bridge).
7. **General polish** of existing skills/tools where merited + light prompt hardening that preserves agency.
8. Design is the source of truth for a **Grok Build** implementation pass on `grok-improvement`.

### Non-Goals / Defer

| Deferred | Why |
|----------|-----|
| Full `web_fetch` / content extraction in v1 | Start with search; add fetch next (Elyra’s dependency order) |
| Nested Browser-Use high-level agent as default | Prefer transparent Playwright primitives in the do-loop |
| Full credential broker service / vault product | Start with tool-scoped injection + store; evolve |
| Per-user isolated tool/skill stores | Shared packages + provenance is enough |
| Automatic self-rewrite of skills/tools without promote | Keep draft → promote culture |
| Phase 1 `grok_build` tool itself | This design only prepares the rails |
| Memory-backed research notes | Phase 3 |
| Heavy prompt constraints that reduce initiative | Explicit non-goal |

---

## Key Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| K1 | **Thin tools, judgment in skills** | Elyra’s ideal: tools = contract, skills = procedure + stop rules. Avoid fat “do research” tools. |
| K2 | **Package VCS mirrors identity** | get / draft / (verify) / promote (archives previous) / list_versions / revert. One culture. |
| K3 | **Keep `verify_tool` required** for executable packages | Code is not prose; identity has no verify, tools do. |
| K4 | **`web_search` is the primary search tool** (`ddgs`); optional SearXNG HTTP backend later | Zero-key native; featureful args from day one. |
| K5 | **Browser = Playwright primitives first** (snapshot + refs, click/type by ref, etc.) | Transparent in moments; fully headless; lower overhead than nested agent. |
| K6 | **Secrets never appear in LLM context or general sandbox env** | Tool-scoped injection only. Model sees references / success, not values. |
| K7 | **User-managed + Elyra-managed secrets** | Operator supplies some; system can mint/rotate others under policy. |
| K8 | **Glass panel for user-managed secrets** | List (redacted), set, grant-to-tool, revoke — parallel to identity panel. |
| K9 | **`github-workflow` skill teaches self-mod conventions now** | Branch structure, worktrees, Projects, review/revert — so Grok Build slots in cleanly. |
| K10 | **Research skill family starts with `web-research` (lite)** | Multi-query, triage, cite, stop conditions, ledger hook. Expand when fetch exists. |
| K11 | **Agency-preserving prompt changes only** | Catalog lines, skill-first nudges, verify/cite/stop — no over-constraint. |
| K12 | **Implementation via Grok Build from this design** | Design is the contract; Build executes the PR stack on `grok-improvement`. |
| K13 | **version_id scheme matches identity** | `{UTC compact}_{6hex}` e.g. `20260727T034500Z_a1b2c3` for consistency. |
| K14 | **Promote/revert gates for packages** | Local packages: reason required; destructive revert may take optional grant. Bundled never overwritten. Critical builtins are not grown via this path. |

---

## 1. Tools & Skills VCS (Identity-Aligned)

### Current gap

Promote is one-way (draft → local) with no archive of the previous local package. Identity already archives to `versions/`.

### Proposed surface (thin)

| Tool | Role |
|------|------|
| `get_tool` / `get_skill` | current / draft / version + list_versions (meta + package summary) |
| `draft_tool` (or keep `install_tool_draft`) | write under drafts only |
| `verify_tool` | **required** for tools (tests + content hash) |
| `promote_tool` / `promote_skill` | archive previous local (if any) → versions/, then draft → current |
| `revert_tool` / `revert_skill` | promote a previous version back to current (with reason + gates) |

**Process skills (parallel to identity):**

- `review-tool` / `review-skill` — read/compare, never mutate
- `update-tool` / `update-skill` — draft → verify → promote path; honest stops for gates

**Layout sketch**

```text
$ELYRA_HOME/tools/
  bundled/          # shipped, immutable
  local/<name>/     # current callable
    TOOL.md, schema.json, runner.json, …
    versions/       # archived previous packages
      <version_id>/
  drafts/<name>/    # non-callable

$ELYRA_HOME/skills/
  bundled/
  local/<name>/
    SKILL.md
    versions/
  drafts/           # optional if we want draft for skills
```

**version_id (normative — match identity K4):**

```text
VERSION_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[0-9a-f]{6}$")
# example: 20260727T034500Z_a1b2c3
```

**Promote behaviour (normative):**

1. If local package exists → archive entire package dir under `versions/<version_id>/` + index entry (sha, time, reason).
2. Copy verified draft → local.
3. Reload registry / catalog.
4. GC oldest versions (cap e.g. 20–50).

**Gates:** same spirit as identity — promote of system-critical packages may require grant; revert always requires reason. Bundled packages are never targets of promote/overwrite.

This gives Elyra the ability to recover from a bad promote without external git.

---

## 2. Thin Tools

### 2.1 `web_search` (native ddgs)

**Featureful from day one:**

- Args: `query`, `type` (text|news|images|videos), `max_results`, `region`, `safesearch`, optional time filters.
- Returns structured list: `{title, url, snippet, source, …}`.
- Built-in retry / backoff / rate-limit handling.
- Optional: if `SEARXNG_URL` set, prefer or merge with local SearXNG (still zero-key for default path).

**Later (not v1):** `web_fetch(urls, max_chars=…)` for clean page text — enables full research loop.

### 2.2 Browser primitives (Playwright)

Accessibility-first, headless default, session-aware:

| Tool | Purpose |
|------|---------|
| `browser_launch` / session mgmt | headless, optional persistent context |
| `browser_goto` | navigate |
| `browser_snapshot` | structured a11y tree + stable refs |
| `browser_click` / `browser_type` / `browser_fill` | by ref or selector |
| `browser_get_text` / `browser_get_html` / `browser_screenshot` | extract |
| `browser_wait` / network helpers | stability |

Session keyed by moment or explicit id so tools compose inside a do-loop. Fail-closed if Playwright/browsers missing.

### 2.3 Git + GitHub

Structured wrappers (not raw shell):

- Local: `git_status`, `git_diff`, `git_log`, `git_add`, `git_commit`, `git_branch`, `git_checkout`, `git_worktree_*`, etc.
- GitHub: `gh_pr_*`, `gh_issue_*`, `gh_repo_search`, `gh_api`, `gh_project_*`, auth status.
- All JSON-friendly; destructive actions gated; secrets injected only into the tool runner.

### 2.4 Secrets tools

| Tool | Role |
|------|------|
| `secrets_list` | names + metadata only (redacted) |
| `secrets_set` / draft | user-managed write |
| (injection) | not a normal model-visible tool — runner/broker injects for allow-listed tools |

Elyra-managed secrets can be minted/rotated under policy and appear in the same list with a managed flag.

**Injection model (normative):**

- Primary: at tool runner dispatch, inject allow-listed secret values into the process environment (or a short-lived proxy) for that call only.
- Model never receives secret values in tool results, args, or context.
- For `gh` tools: the builtin handler reads `GH_TOKEN` (or named secret) from the secrets store and never surfaces it.
- Schema may declare required secret refs (e.g. `"requires_secrets": ["gh_token"]`); runner fails closed if missing.

---

## 3. Skills (Judgment Layer)

### 3.1 Research family (food for thought from Elyra, adapted)

**Primary skill: `web-research` (start with lite version)**

Multi-step research loop:

1. Clarify question / success criteria / depth (quick|standard|deep)
2. Split into sub-queries (not one mega-string)
3. `web_search` → triage by source quality
4. (When fetch exists) fetch top N → extract claims
5. Cross-check disagreements
6. Answer with citations + confidence + still-unknown
7. Stop conditions (enough evidence / diminishing returns / time box)
8. Ledger hook for non-trivial research (goal/tasks so continuous can resume)

**Companion / section skills (keep thin):**

- `query-craft` — rewrite, narrow/broaden, primary-source thinking
- `source-triage` — primary vs secondary, recency, conflict handling
- `tool-health-check` — empty/error search → retry / rephrase / degrade honestly

**Domain skins** only when repeated pain (later).

**What we explicitly avoid:** giant “know everything” skill with no stop rules; skills that invent scraping without verify; duplicating tool contracts inside skills.

#### web-research-lite SKILL.md outline (first shippable form)

```markdown
---
name: web-research
description: Disciplined multi-query research with citations and stop conditions. Use for non-trivial factual questions.
---

# Web research (lite)

## Inputs
- question / success criteria
- depth: quick | standard | deep
- constraints (must-cite, region, …)

## First actions
1. Clarify the question and what “done” looks like.
2. Craft 2–4 focused sub-queries (use query-craft thinking).
3. Call web_search for each; collect structured results.
4. Triage sources (primary vs secondary, recency, credibility).
5. Synthesize answer with inline citations + confidence + open questions.
6. If non-trivial and incomplete → open ledger goal/tasks so continuous can resume.
7. Stop when evidence is sufficient, returns diminish, or time box hits.
   Never invent when search fails; report “search unreliable / insufficient” honestly.

## Stop rules
- Enough evidence for the stated depth → answer and stop.
- Empty or error results after one rephrase → degrade gracefully, do not hallucinate.
- Deep questions without fetch → label low confidence and recommend follow-up when fetch lands.
```

### 3.2 `browse`

Orchestrates Playwright primitives (or a future high-level task). Teaches snapshot-first, ref-based interaction, session hygiene, when to stop and speak.

### 3.3 `github-workflow` (self-improvement bridge)

This skill is the early scaffolding for complex self-mod:

- **Branch discipline:** work on top of `grok-improvement` tip, or create `feature/<topic>` / `execute-plan/<id>` branches; never direct to `main`.
- **Worktrees:** prefer worktrees for isolation when changes are parallel or risky.
- **Tracking:** GitHub Issues / Projects + ledger goal with acceptance = tests + dogfood for multi-step improvement.
- **Package changes:** use the new VCS tools (draft → verify → promote / revert).
- **Instrument preference:** when a stronger coding instrument (`grok_build`) is available, prefer it for multi-file or complex coding tasks *inside this same workflow*; fall back to `search_replace` + `run` for small edits.
- **Person vs instrument:** Elyra owns goals, identity, moments; `gh`, git, and later `grok_build` are instruments under policy and gates.
- Honest stops for human grants on high-impact actions (force-push, main merges, critical package revert, etc.).

Design the skill body so Phase 1 becomes an upgrade to an existing playbook, not a new paradigm.

### 3.4 General polish pass

- Tighten existing growth skills (`create-tool`, `create-skill`, `review-work`, etc.) for clarity and stop conditions, and to surface the new VCS tools.
- Ensure catalog descriptions stay short; bodies carry the judgment.
- Align any remaining soft bias / orient language with Stage B and the new skills.

---

## 4. Secrets System + Glass UI

### Model

- **User-managed**: operator-supplied (tokens, keys). Stored under restricted path (e.g. `data/secrets/`) outside normal model-visible FS.
- **Elyra-managed**: system-generated / rotated under policy.
- **Injection**: only into the specific tool execution environment (or via broker). Model never sees raw values in context, transcripts, or general sandbox env.
- **Grants**: secret usable only by allow-listed tools (or under additional conditions).

### Glass

- New or extended panel (Status sub-card or dedicated Secrets): list (redacted names + which tools can use them), set/update user-managed, revoke, mint Elyra-managed where appropriate.
- Parallel visual language to Identity panel (versions, draft/promote culture where it fits).

### Security stance

Treat the model as untrusted with respect to secret values (prompt-injection resistant by construction). Redact any accidental leakage in tool results. Fail closed if a required secret is missing for an allow-listed tool.

---

## 5. Prompt & Catalog Hardening (Agency-Preserving)

Light, targeted changes only:

- `prompts/system.md`: catalog lines for the new tool families + short note that research/browse/github work prefers the corresponding skills.
- Orient / skill-load language: prefer loading `web-research` / `browse` / `github-workflow` when the job matches, without forbidding direct tool use.
- Skill bodies: strong stop conditions, cite requirements, “do not invent when search fails”, ledger hooks.
- **Do not** add heavy constitutional constraints that reduce initiative or force every action through a skill.

Agency remains: Elyra can still choose tools directly; skills multiply judgment when the work is non-trivial.

---

## 6. Implementation Pathway (Grok Build)

This design is the contract. Implementation should be driven by Grok Build (or equivalent) from this document on `grok-improvement`.

### Suggested PR / work units

1. **Package VCS foundation** — extend promote to archive, add get/list/revert tools, update growth skills; version_id matches identity.
2. **`web_search` + `web-research` (lite)** — ddgs builtin + primary research skill.
3. **Playwright primitives + `browse` skill**.
4. **Secrets store + tool-scoped injection + Glass panel**.
5. **git/`gh` tools + `github-workflow` skill** (with self-mod conventions and grok_build preference hook).
6. **Polish pass** — existing skills/tools, prompt catalog, docs (`tools-and-skills.md`, status-pass, etc.).
7. **Optional follow-ups**: `web_fetch`, SearXNG backend, fuller broker, high-level browser task.

Each unit stays reviewable; verify gates and fail-closed behaviour preserved throughout.

### Dogfood checklist (high level)

- [ ] Promote a tool, confirm previous version archived and listable.
- [ ] Revert a tool to previous version; registry updates.
- [ ] `web_search` returns structured results; rate-limit degrades honestly.
- [ ] `web-research` multi-query + cites + stop conditions; can open ledger goal.
- [ ] Browser snapshot + click-by-ref works headless; session cleans up.
- [ ] Secret set in Glass; `gh` tool succeeds; raw secret never appears in moment tape or model context.
- [ ] `github-workflow` skill produces sensible branch/PR steps and stops for grants; prefers grok_build when present.
- [ ] Existing create-tool / identity paths still green.

---

## Risks & Leave-Alone

| Risk | Mitigation |
|------|------------|
| VCS archive bloat | Cap + GC on promote |
| Model treats secrets as readable | Never inject into context; redact results |
| Research skill becomes endless | Explicit stop conditions + depth param + ledger |
| Browser state leaks across moments | Session keyed + explicit close |
| Over-constraining prompts | Only catalog + skill judgment; preserve direct tool use |
| Scope creep into Phase 1 | This design stops at rails; `grok_build` is separate |

**Leave alone:** do-loop core, Stage B MC formulas, identity gates, sandbox isolation protocol, speak→glass law, continuous defaults.

---

## References

- Elyra’s ideal-case research guidance (operator conversation, 2026-07-27)
- `docs/design-identity-self-other-multi-user.md` (pattern source)
- `docs/tools-and-skills.md`, `elyra/tools/promote.py`, `elyra/tools/registry.py`
- `docs/project-status-pass.md` §7 (dev workflow / self-improvement conversation)
- Playwright accessibility snapshot patterns; `ddgs` / SearXNG ecosystem
- Credential broker patterns (tool-scoped injection)

---

## Summary for implementers

Start with package VCS (so recovery exists), then `web_search` + research skill, then browser, then secrets + git/gh + `github-workflow`. Keep tools thin and skills judgment-heavy. Preserve agency. Use this document as the source of truth for a Grok Build implementation pass on `grok-improvement`.

The highest-leverage early win is exactly what Elyra described: a disciplined `web-research` skill on top of a reliable thin search tool, plus the ability to revert a broken package when growth goes wrong.
