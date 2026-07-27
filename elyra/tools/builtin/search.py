"""Host builtin: web_search via optional ddgs (elyra[search] extra).

Scope: structured metasearch results; fail-closed without ddgs; timeout +
process-wide cooldown after consecutive failures.
In scope: text|news|images|videos; max_results hard cap 20; empty-ok.
Out of scope: web_fetch, web-research skill, full SearXNG adapter.
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import logging
import threading
import time
from typing import Any
from urllib.parse import urlparse

from elyra.tools.types import ToolContext, ToolResult

_LOG = logging.getLogger(__name__)

_DEFAULT_MAX_RESULTS = 8
_HARD_CAP_MAX_RESULTS = 20
_SEARCH_TIMEOUT_S = 15.0
_COOLDOWN_AFTER_FAILURES = 3
_COOLDOWN_SECONDS = 30.0
_INSTALL_HINT = "pip install -e '.[search]'"

_SEARCH_TYPES = frozenset({"text", "news", "images", "videos"})

# Process-wide failure/cooldown state (not durable).
_state_lock = threading.Lock()
_consecutive_failures = 0
_cooldown_until = 0.0


def _reset_cooldown_state_for_tests() -> None:
    """Test helper: clear process-wide cooldown counters."""
    global _consecutive_failures, _cooldown_until
    with _state_lock:
        _consecutive_failures = 0
        _cooldown_until = 0.0


def _ddgs_available() -> bool:
    return importlib.util.find_spec("ddgs") is not None


def _unavailable(reason: str = "search_unavailable") -> ToolResult:
    return ToolResult(
        ok=False,
        payload={
            "ok": False,
            "results": [],
            "hint": _INSTALL_HINT,
        },
        error_reason=reason,
    )


def _err(reason: str, **extra: Any) -> ToolResult:
    payload: dict[str, Any] = {"ok": False, "results": [], **extra}
    return ToolResult(ok=False, payload=payload, error_reason=reason)


def _note_success() -> None:
    global _consecutive_failures, _cooldown_until
    with _state_lock:
        _consecutive_failures = 0
        _cooldown_until = 0.0


def _note_failure(*, rate_limited: bool = False) -> None:
    global _consecutive_failures, _cooldown_until
    with _state_lock:
        _consecutive_failures += 1
        if rate_limited or _consecutive_failures >= _COOLDOWN_AFTER_FAILURES:
            _cooldown_until = time.monotonic() + _COOLDOWN_SECONDS


def _in_cooldown() -> bool:
    with _state_lock:
        return time.monotonic() < _cooldown_until


def _parse_max_results(raw: Any) -> tuple[int | None, str | None]:
    if raw is None:
        return _DEFAULT_MAX_RESULTS, None
    if isinstance(raw, bool):
        return None, "invalid_args"
    if isinstance(raw, int):
        n = raw
    elif isinstance(raw, float) and raw.is_integer():
        n = int(raw)
    elif isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        n = int(raw.strip())
    else:
        return None, "invalid_args"
    if n < 1:
        return None, "invalid_args"
    return min(n, _HARD_CAP_MAX_RESULTS), None


def _source_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url).netloc
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _str_or_empty(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _normalize_item(raw: Any, search_type: str) -> dict[str, Any] | None:
    """Map a ddgs result dict to {title, url, snippet, source, date?}."""
    if not isinstance(raw, dict):
        return None

    title = _str_or_empty(
        raw.get("title") or raw.get("name") or raw.get("heading")
    )
    url = _str_or_empty(
        raw.get("href")
        or raw.get("url")
        or raw.get("link")
        or raw.get("content")  # videos
        or raw.get("image")  # images fallback
    )
    snippet = _str_or_empty(
        raw.get("body")
        or raw.get("snippet")
        or raw.get("description")
        or raw.get("excerpt")
    )
    source = _str_or_empty(
        raw.get("source")
        or raw.get("publisher")
        or raw.get("provider")
        or raw.get("uploader")
    )
    if not source and url:
        source = _source_from_url(url)

    date_raw = raw.get("date") or raw.get("published") or raw.get("date_epoch")
    date: str | None
    if date_raw is None or date_raw == "":
        date = None
    else:
        date = _str_or_empty(date_raw) or None

    # Drop completely empty rows (no title and no url).
    if not title and not url:
        return None

    item: dict[str, Any] = {
        "title": title,
        "url": url,
        "snippet": snippet,
        "source": source,
    }
    if date is not None:
        item["date"] = date
    # Ensure no raw HTML dumps leak through accidental keys.
    _ = search_type  # reserved for type-specific tweaks
    return item


def _looks_rate_limited(exc: BaseException) -> bool:
    msg = f"{type(exc).__name__}: {exc}".lower()
    needles = (
        "rate limit",
        "ratelimit",
        "too many requests",
        "429",
        "403 forbidden",
        "blocked",
    )
    return any(n in msg for n in needles)


def _run_ddgs_search(
    *,
    query: str,
    search_type: str,
    max_results: int,
    region: str | None,
    safesearch: str | None,
    timelimit: str | None,
) -> list[Any]:
    """Import ddgs and invoke the matching method. Raises on backend errors."""
    from ddgs import DDGS  # type: ignore[import-not-found]

    kwargs: dict[str, Any] = {"max_results": max_results}
    if region is not None:
        kwargs["region"] = region
    if safesearch is not None:
        kwargs["safesearch"] = safesearch
    if timelimit is not None:
        kwargs["timelimit"] = timelimit

    # DDGS timeout is per-HTTP; wrap call also has an outer wall-clock timeout.
    client = DDGS(timeout=int(_SEARCH_TIMEOUT_S))
    method = getattr(client, search_type)
    return list(method(query, **kwargs) or [])


def web_search(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Search the web via ddgs (optional ``elyra[search]`` extra).

    Args: ``query`` (required), ``type`` (text|news|images|videos, default text),
    ``max_results`` (default 8, hard cap 20), optional ``region``,
    ``safesearch``, ``timelimit``.

    Success → ``ok=True``, payload ``{ok, results, warning?}``.
    Failures → ``search_unavailable`` | ``rate_limited`` | ``invalid_args`` |
    ``timeout``. Empty backend results are success with ``warning: "empty"``.
    """
    _ = ctx  # host context reserved; search needs no sandbox/paths today

    if not _ddgs_available():
        return _unavailable("search_unavailable")

    if _in_cooldown():
        return _err(
            "rate_limited",
            hint="search cooldown active after consecutive failures; retry later",
        )

    raw_query = args.get("query")
    if raw_query is None or (isinstance(raw_query, str) and not raw_query.strip()):
        return _err("invalid_args", hint="query is required and must be non-empty")
    if not isinstance(raw_query, str):
        return _err("invalid_args", hint="query must be a string")
    query = raw_query.strip()

    raw_type = args.get("type", "text")
    if raw_type is None:
        raw_type = "text"
    if not isinstance(raw_type, str) or raw_type.strip() not in _SEARCH_TYPES:
        return _err(
            "invalid_args",
            hint="type must be one of: text, news, images, videos",
        )
    search_type = raw_type.strip()

    max_results, mr_err = _parse_max_results(args.get("max_results"))
    if mr_err is not None or max_results is None:
        return _err(
            "invalid_args",
            hint="max_results must be a positive integer (hard-capped at 20)",
        )

    region = args.get("region")
    if region is not None and not isinstance(region, str):
        return _err("invalid_args", hint="region must be a string when provided")
    if isinstance(region, str) and not region.strip():
        region = None
    elif isinstance(region, str):
        region = region.strip()

    safesearch = args.get("safesearch")
    if safesearch is not None and not isinstance(safesearch, str):
        return _err("invalid_args", hint="safesearch must be a string when provided")
    if isinstance(safesearch, str) and not safesearch.strip():
        safesearch = None
    elif isinstance(safesearch, str):
        safesearch = safesearch.strip()

    timelimit = args.get("timelimit")
    if timelimit is not None and not isinstance(timelimit, str):
        return _err("invalid_args", hint="timelimit must be a string when provided")
    if isinstance(timelimit, str) and not timelimit.strip():
        timelimit = None
    elif isinstance(timelimit, str):
        timelimit = timelimit.strip()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(
                _run_ddgs_search,
                query=query,
                search_type=search_type,
                max_results=max_results,
                region=region,
                safesearch=safesearch,
                timelimit=timelimit,
            )
            raw_results = fut.result(timeout=_SEARCH_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        _note_failure()
        _LOG.warning("web_search timeout query=%r type=%s", query, search_type)
        return _err("timeout", hint=f"search exceeded {_SEARCH_TIMEOUT_S:.0f}s")
    except Exception as exc:  # noqa: BLE001 — backend volatility; fail closed
        rate = _looks_rate_limited(exc)
        _note_failure(rate_limited=rate)
        _LOG.warning(
            "web_search backend error query=%r type=%s: %s",
            query,
            search_type,
            exc,
        )
        if rate:
            return _err(
                "rate_limited",
                hint="backend rate limited or blocked; retry later",
            )
        return _err(
            "search_unavailable",
            hint=f"backend error: {type(exc).__name__}",
        )

    results: list[dict[str, Any]] = []
    for raw in raw_results:
        item = _normalize_item(raw, search_type)
        if item is not None:
            results.append(item)
        if len(results) >= max_results:
            break

    _note_success()

    payload: dict[str, Any] = {
        "ok": True,
        "results": results,
    }
    if not results:
        payload["warning"] = "empty"
    else:
        payload["warning"] = None
    return ToolResult(ok=True, payload=payload)
