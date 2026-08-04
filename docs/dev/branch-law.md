# Branch law (normative)

| Field | Value |
|-------|--------|
| **Status** | Normative tip law for Project Elyra development |
| **Created** | 2026-08-01 (PR0 — Grok Build design stack) |
| **Related** | [design-grok-build-tool.md](../design/grok-build/design-grok-build-tool.md), [development-governance.md](development-governance.md), [grok-improvement-plan/README.md](../grok-improvement-plan/README.md), [engineering-principles.md](engineering-principles.md) (§9 development structure), [operating-pins.md](operating-pins.md) (manual pin convention) |

This document is the **current** branch / tip law. Where older prose still says `grok-improvement` is the integration tip, **this file wins**.

---

## Roles

| Ref | Role |
|-----|------|
| **`working`** | **Integration tip.** Base for feature / execute-plan / self-mod / fix work. Where stacks land day to day. |
| **`main`** | **Stable.** Promote from `working` only with a full test suite + noise cleanup review. Not the default PR base for active development. |
| **Operating pin** | **Human-moved SHA** per PE instance (later: PE + grant). Colleagues sync **pin**, not tip. Live process never runs an unpinned moving tip. |
| **Tags** | Public releases from a known-good SHA (`vX.Y.Z` + GitHub Release). |
| **Short-lived branches** | `feature/*`, `fix/*`, `self/*`, `exec/*` (and similar) — delete after merge. |
| **`grok-improvement`** | **Superseded** historical GI integration tip. Do not teach as current base. |

```text
short-lived branch / worktree
        ↓  PR + review
     working   (integration tip)
        ↓  full suite + noise cleanup + human approve
      main     (stable)
        ├──→ tag vX.Y.Z + GitHub Release   (public snapshot)
        └──→ human moves operating pin     (that instance goes live)
```

**Safety property:** a live PE instance only ever executes a **previously accepted, pinned** commit. Development never mutates the tree it is executing from.

---

## Integration tip: `working`

- Prefer feature / `execute-plan/*` / topic branches **on top of `working`**.
- Open PRs against **`working`**, not `main`, for active product and self-mod work.
- Merge short-lived branches **down onto `working`** when work lands.
- Never commit directly to `main` or `working` without an explicit human request; never force-push either.
- If `working` is missing locally/remotely, create and push it (see Migration). Instrument preflight for execute-plan fails closed with `base_branch_missing` until it exists.

---

## Stable: `main`

Promote **`working` → `main`** only when:

1. `pytest -m 'not llm and not live_grok'` (or project-equivalent full hermetic suite) is green.
2. Targeted smokes run if tools / sandbox / auth were touched.
3. Noise cleanup review (docs thrash, sandbox/tmp, accidental logs) is done.
4. PR `working` → `main` has human approval.
5. Tag if this is a public release cut.
6. **Do not** auto-move operating pins; instance owners move pin after bake.

---

## Operating pin

- One **human-moved SHA** (file, note, or lightweight tag) records what each live instance actually runs.
- Later: PE-assisted move + **grant** — still explicit, never silent tip-follow.
- Pin ≠ tip: `working` can move daily; pins move on deliberate promote + restart.
- **Manual convention (C3):** [operating-pins.md](operating-pins.md) — lightweight per-instance record now; **goal:** pin becomes live on `main` promote + **v0.1** cut; git tag reviewed at v0.1 creation (do not create the tag from tip hygiene alone).

---

## Tags / releases

- Annotated tags (`vX.Y.Z`) + GitHub Releases are the **public download** surface.
- Tags come from known-good SHAs (usually on `main` after promote), not from ephemeral feature tips.

---

## Short-lived branches

| Prefix | Typical use |
|--------|-------------|
| `feature/*` | Product / capability work |
| `fix/*` | Bug fixes |
| `self/*` | Self-mod / PE-driven growth |
| `exec/*` or `execute-plan/*` | Execute-plan stack slices |

Delete after merge. Do not pile unrelated edits on `main` or on a long-lived personal tip.

---

## Stale stacks

Stacks (or feature branches) **~10 days or more behind `working`** must be:

- **Restacked** onto current `working`, or  
- **Extended** with a written reason (issue / PR comment / ledger note).

Do not silently build on a stale base.

---

## Graphite vs plain-git

| Choice | Law |
|--------|-----|
| **Default** | **Plain-git** stacks (`--no-graphite` for Grok execute-plan). |
| **Graphite** | Optional when the operator opts in and `gt` is available. Never required for PE dogfood. |

---

## Migration: `working` supersedes `grok-improvement`

| Era | Integration tip | Notes |
|-----|-----------------|-------|
| Historical GI Phase 0+ | `grok-improvement` | Large feature landings; may still exist on remote |
| **Normative now** | **`working`** | All new feature / execute-plan / self-mod PRs base here |
| Stable | `main` | Promote from `working` with full suite + noise review |

**Operator steps:**

1. Create `working` from the agreed tip (prefer latest good `main`, or fast-forward from `grok-improvement` if that tip is still the true integration head — document which in the creating PR/description).
2. Push `origin/working`; protect similarly to `main` when practical.
3. Retarget active open PR bases from `grok-improvement` → `working`.
4. Do **not** delete `grok-improvement` immediately if remote history matters; treat as **read-only / superseded**.
5. Dual-track: if `working` is missing, execute-plan preflight fails closed; skills and docs already teach **`working`**, not `grok-improvement`.

```bash
git fetch origin
# Prefer main, or grok-improvement when migrating an ahead tip — document choice:
git checkout -B working origin/main
git push -u origin working
```

---

## Skills and docs alignment

- Bundled skill `github-workflow` teaches **`working`** as the house integration tip (tip-only; mode/async instrument rails land with the tool) and the **before-any-change** issue + branch workflow.
- [engineering-principles.md](engineering-principles.md) §9 folds branch-law + issue/board hygiene into engineering practice (including Grok Build recommendations).
- [operating-pins.md](operating-pins.md) is the pin-record companion to this tip law.
- [grok-improvement-plan/README.md](../grok-improvement-plan/README.md) historical branch tables remain for Phase 0 context; the banner there points here.
- [development-governance.md](development-governance.md) multi-party governance remains valid; tip law is this file.
- Full Grok Build instrument design: [design-grok-build-tool.md](../design/grok-build/design-grok-build-tool.md).
