---
name: browse
description: Snapshot-first web browsing with Playwright primitives — open session, snapshot, act by ref, re-snapshot, close. Use when a live page must be read or driven.
---

# Browse

This skill orchestrates host **browser_*** tools (Playwright, headless Chromium).
Prefer compact **accessibility snapshots + refs** over guessing selectors or dumping HTML.

## When to use

- A real page must be opened, read, or clicked (forms, docs, dashboards)
- Search snippets are not enough and you need on-page structure or text
- Multi-step UI flow (navigate → snapshot → act → re-snapshot)

## When not to use

- Simple fact lookup that `web_search` (or future fetch) can answer
- Screenshots / visual QA (not implemented — use `browser_snapshot` text)
- Guest-isolated sandbox work (browser runs **host-side**, not in the guest)

## First tool call (mandatory)

After this playbook loads, your **next** completion must include a `tool_calls` entry.

Pick the first that applies:

1. `browser_session_open` if you have no live `session_id`
2. Else `browser_goto` / `browser_snapshot` for the active session
3. On `browser_unavailable` / `chromium_unavailable`: `speak` or block honestly with the install hint — do not thrash open

## Snapshot-first loop

```text
browser_session_open
  → browser_goto(url)
  → browser_snapshot          # get ref=eN markers
  → browser_click|type|fill(ref)
  → browser_snapshot          # ALWAYS after navigation or DOM change
  → …
  → browser_session_close     # or rely on moment-end cleanup
```

### Hard rules

1. **Refs expire.** Only use refs from the **latest** `browser_snapshot`. After `goto`, `click`, or any DOM-changing action, **re-snapshot** before the next ref action.
2. **Stale ref → re-snapshot.** On `stale_ref` / missing element, call `browser_snapshot` again; do not invent selectors.
3. **Max 2 sessions** process-wide. Prefer one session per moment. Close early when done.
4. **Fail-closed install errors:**
   - `browser_unavailable` → need `pip install -e '.[browser]'` then `playwright install chromium`
   - `chromium_unavailable` → need `playwright install chromium`
   Do not retry open in a loop; surface the hint to the operator / ledger.
5. **No eval / no arbitrary JS.** Use only the provided primitives.
6. **Size caps.** Snapshots and get_text are truncated — work from structure, not full page dumps.
7. **Session hygiene.** Explicit `browser_session_close` when finished early. Host also closes sessions on moment success finalize, moment error (`fail_in_flight`), and supervisor shutdown.
8. **Speak vs continue.** On social wakes, `speak` progress/answers on glass; do not leave the user with only free-text. For pure task wakes, update ledger notes when blocked.

## Tool map

| Tool | Role |
|------|------|
| `browser_session_open` | headless Chromium; returns `session_id` |
| `browser_session_close` | free resources |
| `browser_goto` | navigate + wait load |
| `browser_snapshot` | a11y tree + `[ref=eN]` |
| `browser_click` | click by ref |
| `browser_type` | append text by ref |
| `browser_fill` | replace text by ref |
| `browser_get_text` | extract text (optional ref) |
| `browser_wait` | short stability wait (capped) |

Screenshots: **not implemented** — do not call a screenshot tool; use snapshot + get_text.

## Process

1. Open a session (or reuse one still open in this moment).
2. `browser_goto` the target URL (`http`/`https` only).
3. `browser_snapshot` — plan the next action from refs and roles.
4. Act with `browser_click` / `browser_fill` / `browser_type` / `browser_get_text`.
5. Re-snapshot after each meaningful change; short `browser_wait` only if needed.
6. Close the session when finished, or end the moment (host cleanup).
7. If blocked on missing browser/chromium, set ledger blocked notes and/or `speak` the install hint.

## Quality / completion

Done when:

- The page goal is met (content extracted, form submitted, or honest blocker), and
- No orphan session is left intentionally open when work is finished, and
- User-visible outcomes (if social) were delivered via `speak`

## Out of scope

- Nested Browser-Use / autonomous sub-agent
- PNG screenshots to media store
- Downloading and executing untrusted files
- Claiming guest sandbox isolation for Chromium
