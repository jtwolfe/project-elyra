# Time and identity

**Runtime freeze:** [stretch-1.md](stretch-1.md). This page is the **life-shell** rules.

---

## Self ≠ user (hard)

| | **Self** | **User** |
|--|----------|----------|
| Question | Who am I? | Who is this person *to me*? |
| Store | `identity/` | `users/<id>/` |
| Change | Rare reflect + `patch_identity` | Gated `patch_user` for that id |
| On wake | Short **SELF** digest | At most **one USER** digest if social wake |

**Bans:** fused persona file; writing user prefs into core self; unlabeled “you and Jim” blob; cross-user profile inject; one “update who” tool for both.

Chatting with Jim must not rewrite who Elyra is.

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

Longer research notes: [archive/](archive/).

---

## Stretch 2 (not implemented)

Moments chain into days; soft day **strain** may prefer rest; **opaque sleep** sparsely links the graph.  
Do not build that in Stretch 1. See [archive/reflection-moments-and-memory-scope.md](archive/reflection-moments-and-memory-scope.md).
