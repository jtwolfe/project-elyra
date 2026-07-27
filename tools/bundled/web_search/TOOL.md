---
name: web_search
description: Search the web (text, news, images, or videos) via the optional ddgs backend. Returns structured title/url/snippet results. Requires elyra[search]. Fail-closed when unavailable or rate-limited.
kind: read
---

# web_search

Native host search. Thin tool — use skill `web-research` for multi-query
judgment, citation, and stop conditions.

- Required: `query` — non-empty search string
- Optional: `type` — `text` (default) | `news` | `images` | `videos`
- Optional: `max_results` — default 8, hard-capped at 20
- Optional: `region` — e.g. `us-en`, `uk-en`
- Optional: `safesearch` — `on` | `moderate` | `off`
- Optional: `timelimit` — `d` | `w` | `m` | `y` (backend-dependent)

## Result

Success payload:

```json
{
  "ok": true,
  "results": [
    {"title": "...", "url": "...", "snippet": "...", "source": "...", "date": null}
  ],
  "warning": null
}
```

Empty backend → `ok: true`, `results: []`, `warning: "empty"` (rephrase or stop;
do not invent sources).

## Errors (`ok: false`)

| `error_reason` | Meaning |
|----------------|---------|
| `search_unavailable` | `ddgs` not installed or backend error — install with `pip install -e '.[search]'` |
| `rate_limited` | Backend blocked / cooldown after consecutive failures |
| `invalid_args` | Missing/empty query, bad type, bad max_results |
| `timeout` | Search exceeded ~15s wall clock |

Never invent results when search fails. Prefer multi-query via skill, not
repeated blind retries on `rate_limited`.
