# Time and identity

**Runtime freeze:** [stretch-1.md](stretch-1.md). This page is the **life-shell** rules for self/other and time.

**Design (shipped layout + tools):** [design-identity-self-other-multi-user.md](../design/identity/design-identity-self-other-multi-user.md) — draft→promote, gates, work-origin USER (K13/K19), Glass multi-user prep.

---

## Self ≠ user (hard)

| | **Self** | **User** |
|--|----------|----------|
| Question | Who am I? | Who is this person *to me*? |
| Store | `data/identity/` | `data/users/<id>/` |
| Live body | `current.md` only | `current.md` only |
| Change | Rare reflect → `draft_identity` → hard promote (operator grant) | `draft_identity` → medium promote (social / session user) |
| On wake | Short **SELF** digest (always) | At most **one USER** digest (work-origin; may be empty) |

**Bans:** fused persona file; writing user prefs into core self; unlabeled “you and Jim” blob; cross-user profile inject; one “update who” tool for both; silent live rewrite without promote.

Chatting with Jim must not rewrite who Elyra is. There are **no** `patch_identity` / `patch_user` tools — only the draft/promote trio (process in skills).

---

## Versioned layout

Same shape for self and each user (legacy `self.md` / `profile.md` still read via compat until migrate):

```text
data/identity/                 data/users/<user_id>/
  current.md                     current.md     # live; orient injects this only
  draft.md                       draft.md       # optional; never injects
  meta.json                      meta.json      # labels + versions index
  versions/                      versions/
    <version_id>.md                <version_id>.md
```

| Rule | Detail |
|------|--------|
| Inject | **current only** into orient `{{SELF}}` / `{{USER}}` |
| Draft | Writable anytime; invisible to orient until promote |
| Versions | Archived previous current on promote; inspect via `get_identity` |
| Reset | Preserves all of `identity/**` and `users/**` (including drafts/versions/meta) |

`version_id` is the archive filename stem only (UTC compact + 6 hex), e.g. `20260726T153045Z_a1b2c3`.

### goes_by vs full_name

| Field | Role | Mutability |
|-------|------|------------|
| **`goes_by`** / `display_name` | Living address-as / Glass actor pill | Normal draft→promote |
| **`full_name`** | Stable legal/preferred full name | Host-protected: set/change needs `force_full_name: true` at draft (incl. first non-null) |
| Body markdown | Charter (self) or relationship notes (user) | Full body replace on draft |

Prefer updating **`goes_by`** and relationship prose over thrashing **`full_name`**. Example: address “Joe” → “Papa Joe”; notes gain family context; `full_name` “Joseph Bloggs” stays.

---

## Mutation path (thin tools + skills)

| Surface | Role |
|---------|------|
| `get_identity` | Read current / draft / version + meta; optional version list; user soft `should_name_nudge` |
| `draft_identity` | Write draft body and/or meta only — never updates current |
| `promote_identity` | Draft → current under host gates; archives prior current |
| Skill `review-identity` | Read/compare; speak findings; **never** draft or promote |
| Skill `update-identity` | Draft; self stops for Glass grant; user may promote under medium gate |

**Promote gates (host-enforced):**

| Actor | Gate |
|-------|------|
| **Self** | Hard — operator one-time grant (Glass mint + Promote is primary; model needs real `grant_token`) |
| **User** | Medium — social context + reason + target matches **session** user (Glass admin may promote other profiles) |

Model path updates the **active session user** only (no list-users tool in v1). Details and schemas: [tools-and-skills.md](tools-and-skills.md); full design: [design-identity-self-other-multi-user.md](../design/identity/design-identity-self-other-multi-user.md).

---

## Orient USER inject (work-origin — not operator fallback)

SELF is always Elyra’s current self digest. **USER is not “always operator.”** Resolver (`resolve_orient_user`, K13/K19):

1. **Social wake** (message / wait reply) → speaker `user_id` profile (current only), or empty if missing  
2. **Work wake** with wake-linked goal/task → that entity’s `created_in_context.user_id` profile when present  
3. Else → **empty USER** (autonomous / unlinked work)

| Wake | USER digest |
|------|-------------|
| User message as Jim | Jim’s current profile |
| `task_ready` on a Jim-context goal/task | Jim’s profile (work *with/for* Jim) |
| Continuous / timer with no linked context | **Empty** — not operator, not last-speaker memory |
| Invalid / missing linked user | Empty (fail soft) |

**Never** invent operator as a fake social counterpart. **Never** put Elyra in the USER slot. Shared ledger goals/tasks may carry `created_in_context` (`user_id` + optional `goes_by` snapshot) when created under a social `ctx.user_id`; pure continuous creates leave it null (expected).

### Glass session ≠ orient USER

| Concern | Source |
|---------|--------|
| Who is typing / message attribution | Glass session switcher + `messages[].user_id` |
| Who this *work* is for in orient | Work-origin resolver (may be empty) |
| Actor labels on glass | `meta` display names (`goes_by` / `display_name`), not hard-coded “user” / “assistant” |

Multi-user prep: session switcher, provisional users (goes_by mint), identity panel (current + versions + self grant/promote). Privacy baseline: **one** USER digest per wake; no cross-user profile inject.

---

## Time in three layers

| Layer | What | Role |
|-------|------|------|
| **A. Clock frame** | Small `NOW` (local + UTC, weekday) each wake | “When is it?” |
| **B. Relative labels** | Recomputed on shown events (`2h ago · 13:02`) | Order / recency |
| **C. Structural** | `due_at`, `schedule_wake`, last contact, wait timeouts | When to **wake** / act |

Storage keeps **UTC**. Never persist `"2h ago"` — recompute at assembly.  
Put clock + digests + why-now **near the decision** (end of orient), not only at the top of a long history.

**Speak policy**

- User message → wake (or interjection if mid-moment)  
- Timer / due → wake with reason  
- Pure work → speak only if useful; interim speak allowed  
- Do not invent outreach from raw timestamp dumps  

Use wait / `schedule_wake` / dues — not archaeology.

---

## Gemma notes (short)

- Thin **system** prompt; digests in the wake packet  
- Native tools + reasoning stream: **store** reasoning, **omit** from next meals by default  
- Strip reasoning between user assemblies; keep across in-turn tool hops if required  
- Small tool/skill surface; hop thrash is a harness bug, not a “need more stages” signal  
- Huge system prompts hurt 12B-class models — don’t grow a bible  

Longer research notes: [archive/](../archive/).

---

## Stretch 2 (not implemented)

Moments chain into days; soft day **strain** may prefer rest; **opaque sleep** sparsely links the graph.  
Do not build that in Stretch 1. See [archive/reflection-moments-and-memory-scope.md](../archive/reflection-moments-and-memory-scope.md).
