---
name: web-research
description: Multi-query web research with triage, citations, and stop conditions. Use when a question needs external evidence beyond one search.
---

# Web research (lite)

Disciplined research loop over the thin `web_search` tool. Prefer this skill for non-trivial questions; a single opportunistic `web_search` is fine for one-shot lookups.

## When to use

- A question needs **external evidence** (facts, news, docs, comparisons)
- One search is unlikely to be enough — multi-query + cross-check
- You must answer with **citations** and honest unknowns

## When not to use

- Pure ledger / sandbox work with no external info need → `do-work`
- Social-only wake → `talk` first; research only if the human needs it
- Search backend missing and install is not this moment’s job → note `search_unavailable`, do not invent
- Deep page interaction (forms, multi-step sites) → wait for `browse` when available; do not fake browse with search alone

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry. Do not answer with free-text only.

Pick the first that applies:

1. If criteria are already clear in orient / task notes → `web_search` with the first sub-query
2. Else ledger: `get_task` / `get_goal` / `list_goals` to restate the question and success criteria, then `web_search`
3. If the human is waiting on glass for the answer path → short `speak` ack only when needed, then search (final answer still goes through `speak` when social)

## Hard rules

1. **Never invent sources or facts** when search fails, is empty, rate-limited, or unavailable.
2. Use the **exact** tool name `web_search` (snake_case). Skill name is `web-research` (hyphenated).
3. **Multi-query:** split into **2–4** sub-queries; do not hammer one failed query.
4. **Cite** every non-obvious claim with a result URL (or title+URL). Mark confidence and still-unknown.
5. **Stop** when: enough evidence for the criteria, diminishing returns, time box, `search_unavailable`, or `rate_limited` after a brief rephrase attempt.
6. Prefer **few good queries** over endless search loops. No blind retries on hard `rate_limited` / cooldown.
7. Non-trivial incomplete research → open or update a **ledger** goal/task so continuous work can resume (do not rely on private free-text alone).

## Process

1. **Clarify** the question, success criteria, and depth: `quick` | `standard` | `deep`.
2. **Split** into 2–4 sub-queries (different angles, not synonyms only).
3. **`web_search`** each sub-query (sensible `max_results`; default is fine). Optional `type` / `region` / `timelimit` when useful.
4. **Triage** results: prefer primary sources, recent docs, and independent coverage; discard junk / SEO filler.
5. **Cross-check** disagreements; note confidence. (Page fetch is out of scope for lite — work from titles/snippets/URLs only.)
6. **Answer** with inline citations, confidence, and still-unknown. On social wakes, **`speak`** the answer on glass.
7. **Stop** per hard rules. If incomplete and worth continuing: `create_goal` / `create_task` / `update_task` with acceptance and notes (what was searched, what remains).

## Depth guide

| Depth | Queries (approx) | Stop bias |
|-------|------------------|-----------|
| `quick` | 1–2 | First solid answer + cite |
| `standard` | 2–4 | Cross-check + cite; stop on diminishing returns |
| `deep` | up to ~4 focused rounds | More angles; still hard-stop on unavailable / rate limit / time box |

## Failure modes

| Signal | Action |
|--------|--------|
| `search_unavailable` | Stop inventing; note install hint (`pip install -e '.[search]'`) in ledger/speak if operator must act |
| `rate_limited` | One rephrase at most; then stop or ledger; do not spin |
| empty `results` + `warning: empty` | Rephrase once or change angle; then stop honestly |
| `timeout` / `invalid_args` | Fix args or stop; do not fabricate |

## Quality / completion

Done when:

- Criteria are met with cited evidence, or
- You stopped honestly with known gaps, or
- Incomplete work is on the ledger with clear next queries / acceptance

## Out of scope

- Inventing a know-everything encyclopedia skill
- Duplicating full `web_search` schema contracts in prose (schemas win)
- Browser automation (future `browse` skill)
- Memory-backed research notes (later phase)
- Endless multi-hop loops that thrash the backend
