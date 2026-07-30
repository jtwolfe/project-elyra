# Development, operating pin, and multi-party governance

| Field | Value |
|-------|--------|
| **Created** | 2026-07-30 |
| **Status** | Working guidance — **not gospel**, not a freeze contract |
| **Sources** | Prior Grok Web discussion (repo/structure + Stage 0–3 ladder); current Project Elyra state on `grok-improv-radeonvii`; multi-party intent (Jamie, Colin, multiple PE instances); consciousness research briefs prepared for Colin (2026-07-29) |
| **Related** | [README.md](README.md) (v0.1 promotion), [../engineering-principles.md](../engineering-principles.md), [../grok-improvement-plan/README.md](../grok-improvement-plan/README.md) |

This note **updates** earlier “enterprise self-mod git” advice for where Elyra is **now**, and for a future that is **not only self-improvement by one agent**, but a **small committee of humans + PE instances**.

---

## 1. What the older structure got right

Separating three meanings of “the code” remains the right core:

| Meaning | Role |
|---------|------|
| **Public download** | Annotated tags + GitHub Releases (`v0.1.0`, …) |
| **Operating (live)** | What a **running PE instance** actually executes — a **pinned** commit |
| **Development** | Short-lived branches + **worktrees** — never the live tree |

Promotion paths:

```text
worktree / short-lived branch
        ↓  PR + review
      main   (integration candidate)
        ↓  verification
        ├──→ move operating pin + controlled restart   ← that instance goes live
        └──→ tag vX.Y.Z + GitHub Release               ← public snapshot
```

**Safety property:** a live person/process only ever runs a **previously accepted, pinned** commit. Development never mutates the tree it is executing from.

The **Stage 0 → 1 → 2 → 3 ladder** remains a good adoption path: do not jump to full self-mod automation before Project board + PR habit + operating record exist.

---

## 2. What has changed since that discussion (do not treat as gospel)

Project Elyra has advanced hard:

- Full harness + Grok path + Stage B MC + identity prep  
- Stretch 2 Phase 1–2a **code** (semantic, traversal; dogfood still open)  
- GPU embed train (Radeon VII / generic device matrix recorded)  
- Dense docs + known-bugs; branch forest is real (`main`, `grok-improvement*`, radeon tips)  

Implications:

| Older advice | Now |
|--------------|-----|
| Stage 0 “seat of pants” | Largely still true for **git process**, even though **product** is feature-rich |
| Stage 1 “do soon” | **Overdue and high leverage** — Project board + main protection + written map |
| Full operating-pin automation | Still **later** (Stage 3); manual operating record is enough for Stage 1–2 |
| Self-mod as the only next work | **Wrong** — next work is multi-human multi-instance **and** product close (v0.1) |
| Single operator (you) only | **Jamie + Colin** (+ PE instances each may run) — process must scale to **quorum and continuity** |

Treat the Grok Web structure as **loose architectural guidance** to grow into, not a checklist that freezes current dogfood.

---

## 3. Stage ladder (adopt lightly)

### Stage 0 — Where we still mostly are (process)

- Targeted changes, operator-driven testing  
- Branches accumulate; Issues/Projects underused  
- Live instance managed by hand on the machine  

### Stage 1 — Kickoff (do soon — low cost)

1. **One GitHub Project** (Backlog → Doing → Review → Done, or similar).  
2. **Light protection on `main`**: PR required; optional single review; **no** expensive required Actions yet.  
3. **Branch hygiene** documented: short-lived only; delete after merge; `feature/…` `fix/…` `improve/…` `self/…`.  
4. **Write the map** (this file + [README.md](README.md)): `main` integration; releases = tags; later operating pin; worktrees for real changes.  
5. **Running instance stays fully manual** for now.  
6. Optional: issue/PR templates; labels (`bug`, `self-mod`, `public-release`, `memory`, `context-meal`, `multi-instance`).

### Stage 2 — Intermediate (while shipping v0.1)

- Issues + Project = **planning surface of record**  
- Meaningful work → short branch + PR → `main` (even when humans merge their own PRs)  
- Worktrees for larger / multi-person / self-mod work  
- **Local verification** first; Actions optional  
- **Simple operating record** when restarting live: commit SHA in a small file, note, or hand-moved lightweight tag  
- Occasional tags / Releases when stable  
- Constrained tokens for agents later (issues/PRs/projects/contents — **not** admin/protection rules)

### Stage 3 — Final vision (later)

- Worktrees + thin `grok_build` as normal change path  
- Operating pin moved only by explicit promote + controlled restart  
- Public releases = deliberate tags  
- Elyra instances own more day-to-day developer workflow  
- Humans remain **release authority** and protection-rule owners  
- Runtime tokens least-privilege  

---

## 4. Multi-human, multi-instance future

Next stages are **not** only “Elyra improves Elyra.” Expected participants:

| Actor | Role (sketch) |
|-------|----------------|
| **Jamie** | Founder / engineering continuity; PE instance on LuxPrimata (and others); strong vote on architecture and promote |
| **Colin** | Collaborator; data/governance lens; may run **his own PE instance**; committee / ontology / community emphasis |
| **PE instances** | Durable “persons” on pinned commits; each has own `ELYRA_HOME` memory, tools, glass — **not** shared mutable state by default |
| **Later humans + PE instances** | Same pattern: identity, operating pin, promote gates scale by **roles**, not by ad-hoc trust |

### 4.1 Hierarchy of responsibility (proposal)

```text
                    ┌─────────────────────────────┐
                    │  Protection / release        │
                    │  (repo admins: humans only)  │
                    │  branch rules, secrets,      │
                    │  public tag authority        │
                    └──────────────┬──────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │  Integration: main           │
                    │  PR review + local/CI checks │
                    │  multi-party review welcome  │
                    └──────────────┬──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
     Operating pin A        Operating pin B        Public release
     (Jamie’s PE)           (Colin’s PE)           (tags / Releases)
     controlled restart     controlled restart     installable snapshot
```

| Layer | Who decides | What fails if wrong |
|-------|-------------|---------------------|
| **Public release** | Human release authority (Jamie ± committee) | External users, reputation |
| **Operating promote (per instance)** | Instance owner human (Jamie for his PE, Colin for his) + optional second review for risky changes | **That** PE’s continuity, memory, tools |
| **Merge to `main`** | PR review (any qualified human; later PE-assisted drafts) | Integration quality for everyone |
| **Development** | Author human or PE in worktree | Isolated; should not brick others |

**Critical multi-instance rule:** promoting `main` does **not** auto-restart every PE. Each owner promotes **their** operating pin when **they** accept the risk. That is how we avoid “one bad merge broke everyone’s memory.”

### 4.2 Testing so “none of us experience failures”

Minimum bar before something is **safe to promote to operating** on a shared recommendation:

| Gate | Purpose |
|------|---------|
| Hermetic tests | `pytest -m 'not llm'` (and targeted packs) |
| Local smoke | `elyra start` path; sandbox doctor if tools changed |
| Memory smoke | If meal/store/embed touched: encode path + meal rebuild (see promotion README §4–5) |
| Tool honesty | create-tool / MSB fitness if capability surface changed |
| Instance-local backup | Recommend snapshot of `ELYRA_HOME` before promote on that machine |
| Dual-party review (when available) | Jamie **or** Colin review for risky PRs; both for public `vX.Y.Z` when possible |

Public releases should be **stricter** than a single-instance operating promote (more smoke, longer bake on at least one operating pin).

### 4.3 What each PE instance owns

- Own **operating pin** (commit SHA)  
- Own **data plane** (`ELYRA_HOME`: moments, atoms, glass, identity)  
- Own **secrets** / credentials  
- Shared **code lineage** only via git (`main` / tags)  

Memory is **not** merged across instances by default. Continuity is **per person-instance**. Shared knowledge moves via: designs in repo, Issues, Releases notes, optional export formats later — not by copying live Lance dirs into each other without ceremony.

---

## 5. Dual research approaches (Jamie × Colin) — how they shape the project

These are complementary, not competitive. Product structure should hold **both**.

| Axis | Jamie (engineering continuity) | Colin (data / community / committee) |
|------|--------------------------------|--------------------------------------|
| **Unit of mind** | Isolated system that interacts and perceives | Groups, community, **relational** emergence |
| **Information metaphor** | **Containers** with structure applied | **Landscape** with ontologies to extract |
| **Research method** | Linearly derived systems; **single continuity** | Systems of **quorum**, debate, consensus |
| **Self model** | Valuable structure of self (durable identity, pins, moments) | Self as process; committee may hold mind-like properties |
| **Risk focus** | Brick live process; break memory/tools on one machine | Governance lag; unaccountable capability; multi-party harm |

Consciousness research briefs (2026-07-29, prepared for Colin — **external** to this repo; not re-copied here):

- Survey of theories (functionalism, biological naturalism, IIT, GWT, …)  
- Critique axes (character vs substrate, parallel instantiation, Blockhead, organisational invariance)  
- Relational / committee angles: consciousness as **relationship** or **governance process**; **governance before consciousness**; memory as condition for identity; friction and continuity  

**Product translation (practical):**

| Colin-flavoured need | Mechanism in this governance model |
|----------------------|-------------------------------------|
| Quorum / committee | Multi-reviewer PRs; Project board; dual human promote for public releases |
| Landscape → ontology | Issues + labels + architecture notes extract structure from chat sprawl |
| Relational mind | Multiple PE instances + human discussion; **not** one shared brain by default |
| Governance before capability | Operating pin + PR before live; least-privilege tokens; Stage ladder |

| Jamie-flavoured need | Mechanism |
|----------------------|-----------|
| Single continuity | Per-instance operating pin + controlled restart; no live-tree edits |
| Structured containers | Packages, worktrees, tagged releases, memory atoms as durable units |
| Linear derivation | Trunk-based `main`; short-lived branches; clear promote sequence |

**Neither side should “win” the repo shape.** The shape is intentionally **linear code lineage + multi-party governance surface**.

---

## 6. Naming — project identity

Working product name has been **Project Elyra**. For a multi-party, self-development, committee-shaped programme, a fuller name may help.

### Constraints

- Keep **Elyra** as the agent / product name (person in the loop).  
- Distinguish **programme** (humans + process + instances) from **runtime** (one PE process).  
- Avoid names that imply only one human or only self-mod without community.  
- Prefer something pronounceable in speech and short in git/Issue titles.

### Candidates (discussion, not decided)

| Name | Notes |
|------|--------|
| **Project Elyra** | Keep as product/runtime short name |
| **Project Elyra — Committee of Self-Development** | Explicit committee + self-dev; slightly long; “Committee” fits Colin’s lens |
| **Elyra CSD** (Committee of Self-Development) | Short label for Project board / org |
| **Elyra Continuity Project** | Emphasises Jamie’s continuity + operating pins |
| **Elyra Commons** | Multi-instance / multi-human shared code; soft on self-mod |
| **The Elyra Committee** | Human+agent governance; less “engineering product” |
| **Project Elyra: Person & Committee** | Dual framing (isolated person + group process) |

**Recommendation for now:** keep **Project Elyra** as the **runtime/product** name; use a **programme subtitle** on the GitHub Project and promotion docs, e.g.:

> **Project Elyra** — *Committee of Self-Development*  
> (working programme name; final branding open)

That avoids renaming the package/binary while signalling the multi-party gym.

Open: decide with Colin before renaming org/repo.

---

## 7. How this plugs into v0.1 (promotion README)

Immediate (Stage 1 + v0.1 prep):

1. GitHub Project under programme name above  
2. Light `main` protection + branch hygiene doc (this file linked from root docs)  
3. Manual operating SHA record when restarting Jamie’s (and later Colin’s) PE  
4. Continue product close (memory dogfood, meal/chat-chain, `grok_build`) **without** waiting for Stage 3  

Explicitly **not** required for v0.1:

- Automated operating-pin tool  
- Full self-mod loop  
- Stretch 2 Phase 3  
- Forcing all work through Actions  

---

## 8. Open decisions (for Jamie + Colin)

1. Who holds **repo admin** / protection rules (shared?).  
2. Public release bar: one human or **both** for `v0.1.0`?  
3. Can PE instances open PRs / Issues with constrained tokens before Stage 3?  
4. Programme name finalisation.  
5. Whether Colin’s PE starts from same `main` tip and only diverges on **operating pin** + data (recommended: yes).  
6. Shared dogfood protocol for meal/encode/GPU before either promotes memory-sensitive commits.

---

*Loose guidance 2026-07-30. Prefer living this lightly (Stage 1) over implementing Stage 3 early.*
