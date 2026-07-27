"""Host builtin browser tools (Playwright primitives).

Scope: session open/close, goto, a11y snapshot+refs, click/type/fill by ref,
get_text, wait. Fail-closed when playwright or chromium missing.
Out of scope: screenshots (deferred / not_implemented), eval, guest isolation.

Entry points referenced by tools/bundled/browser_*/runner.json.
"""

from __future__ import annotations

from typing import Any

from elyra.tools.browser_sessions import (
    HINT_BROWSER_INSTALL,
    HINT_CHROMIUM_INSTALL,
    MAX_WAIT_SECONDS,
    BrowserActionError,
    BrowserError,
    BrowserUnavailableError,
    ChromiumUnavailableError,
    SessionLimitError,
    SessionNotFoundError,
    StaleRefError,
    get_browser_session_manager,
)
from elyra.tools.types import ToolContext, ToolResult


def _err(
    reason: str,
    *,
    hint: str = "",
    **extra: Any,
) -> ToolResult:
    payload: dict[str, Any] = {"reason": reason, **extra}
    if hint:
        payload["hint"] = hint
    return ToolResult(ok=False, payload=payload, error_reason=reason)


def _from_browser_error(exc: BaseException) -> ToolResult:
    if isinstance(exc, BrowserError):
        return _err(exc.reason, hint=exc.hint or "", detail=str(exc))
    return _err("browser_action_failed", detail=str(exc))


def _session_id_arg(args: dict[str, Any]) -> str | ToolResult:
    raw = args.get("session_id")
    if raw is None and "session_id" not in args:
        return _err("missing_session_id")
    if not isinstance(raw, str) or not raw.strip():
        return _err("invalid_session_id")
    return raw.strip()


def _ref_arg(args: dict[str, Any]) -> str | ToolResult:
    raw = args.get("ref")
    if raw is None and "ref" not in args:
        return _err("missing_ref")
    if not isinstance(raw, str) or not raw.strip():
        return _err("invalid_ref")
    return raw.strip()


def _str_arg(args: dict[str, Any], key: str) -> str | None:
    raw = args.get(key)
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped if stripped else None


def browser_session_open(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Open a headless Chromium session bound to ``ctx.moment_id``.

    Returns ``session_id``. Max 2 concurrent process-wide sessions.
    """
    del args  # no required args; moment from ctx
    mgr = get_browser_session_manager()
    moment_id = ctx.moment_id or ""
    try:
        session_id = mgr.open(moment_id=moment_id)
    except BrowserUnavailableError as exc:
        return _err(
            "browser_unavailable",
            hint=exc.hint or HINT_BROWSER_INSTALL,
            detail=str(exc),
        )
    except ChromiumUnavailableError as exc:
        return _err(
            "chromium_unavailable",
            hint=exc.hint or HINT_CHROMIUM_INSTALL,
            detail=str(exc),
        )
    except SessionLimitError as exc:
        return _err(
            "session_limit",
            hint=exc.hint,
            detail=str(exc),
            max_sessions=2,
        )
    except BrowserError as exc:
        return _from_browser_error(exc)
    except Exception as exc:  # noqa: BLE001
        # Never crash the supervisor / worker on optional browser dep.
        return _err(
            "browser_unavailable",
            hint=HINT_BROWSER_INSTALL,
            detail=str(exc),
        )
    return ToolResult(
        ok=True,
        payload={
            "session_id": session_id,
            "moment_id": moment_id,
            "headless": True,
        },
    )


def browser_session_close(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Close a browser session and free Chromium resources."""
    del ctx
    sid = _session_id_arg(args)
    if isinstance(sid, ToolResult):
        return sid
    mgr = get_browser_session_manager()
    try:
        existed = mgr.close(sid)
    except Exception as exc:  # noqa: BLE001
        return _err("browser_action_failed", detail=str(exc))
    if not existed:
        return _err("session_not_found", session_id=sid)
    return ToolResult(ok=True, payload={"session_id": sid, "closed": True})


def browser_goto(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Navigate session to URL and wait for load."""
    del ctx
    sid = _session_id_arg(args)
    if isinstance(sid, ToolResult):
        return sid
    url = _str_arg(args, "url")
    if url is None:
        return _err("missing_url")
    if not (url.startswith("http://") or url.startswith("https://")):
        return _err(
            "invalid_url",
            detail="url must start with http:// or https://",
            url=url,
        )
    mgr = get_browser_session_manager()
    try:
        result = mgr.goto(sid, url)
    except SessionNotFoundError as exc:
        return _from_browser_error(exc)
    except BrowserError as exc:
        return _from_browser_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _err("browser_action_failed", detail=str(exc))
    return ToolResult(ok=True, payload={"session_id": sid, **result})


def browser_snapshot(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Capture accessibility tree + refs (size-capped). Re-snapshot after nav."""
    del ctx
    sid = _session_id_arg(args)
    if isinstance(sid, ToolResult):
        return sid
    mgr = get_browser_session_manager()
    try:
        result = mgr.snapshot(sid)
    except SessionNotFoundError as exc:
        return _from_browser_error(exc)
    except BrowserError as exc:
        return _from_browser_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _err("browser_action_failed", detail=str(exc))
    return ToolResult(ok=True, payload={"session_id": sid, **result})


def browser_click(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Click element by snapshot ``ref`` (primary). Re-snapshot after click."""
    del ctx
    sid = _session_id_arg(args)
    if isinstance(sid, ToolResult):
        return sid
    ref = _ref_arg(args)
    if isinstance(ref, ToolResult):
        return ref
    mgr = get_browser_session_manager()
    try:
        result = mgr.click(sid, ref)
    except StaleRefError as exc:
        return _from_browser_error(exc)
    except SessionNotFoundError as exc:
        return _from_browser_error(exc)
    except BrowserError as exc:
        return _from_browser_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _err("browser_action_failed", detail=str(exc))
    return ToolResult(ok=True, payload={"session_id": sid, **result})


def browser_type(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Type text into element by ref (appends; use browser_fill to replace)."""
    del ctx
    sid = _session_id_arg(args)
    if isinstance(sid, ToolResult):
        return sid
    ref = _ref_arg(args)
    if isinstance(ref, ToolResult):
        return ref
    if "text" not in args:
        return _err("missing_text")
    text = args.get("text")
    if not isinstance(text, str):
        return _err("invalid_text")
    mgr = get_browser_session_manager()
    try:
        result = mgr.type_text(sid, ref, text, clear=False)
    except StaleRefError as exc:
        return _from_browser_error(exc)
    except SessionNotFoundError as exc:
        return _from_browser_error(exc)
    except BrowserError as exc:
        return _from_browser_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _err("browser_action_failed", detail=str(exc))
    return ToolResult(ok=True, payload={"session_id": sid, **result})


def browser_fill(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Fill (replace) text in element by ref."""
    del ctx
    sid = _session_id_arg(args)
    if isinstance(sid, ToolResult):
        return sid
    ref = _ref_arg(args)
    if isinstance(ref, ToolResult):
        return ref
    if "text" not in args:
        return _err("missing_text")
    text = args.get("text")
    if not isinstance(text, str):
        return _err("invalid_text")
    mgr = get_browser_session_manager()
    try:
        result = mgr.fill(sid, ref, text)
    except StaleRefError as exc:
        return _from_browser_error(exc)
    except SessionNotFoundError as exc:
        return _from_browser_error(exc)
    except BrowserError as exc:
        return _from_browser_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _err("browser_action_failed", detail=str(exc))
    return ToolResult(ok=True, payload={"session_id": sid, **result})


def browser_get_text(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Extract text by optional ref, or body text when ref omitted."""
    del ctx
    sid = _session_id_arg(args)
    if isinstance(sid, ToolResult):
        return sid
    ref: str | None = None
    if "ref" in args and args.get("ref") is not None:
        raw = args.get("ref")
        if not isinstance(raw, str) or not raw.strip():
            return _err("invalid_ref")
        ref = raw.strip()
    mgr = get_browser_session_manager()
    try:
        result = mgr.get_text(sid, ref=ref)
    except StaleRefError as exc:
        return _from_browser_error(exc)
    except SessionNotFoundError as exc:
        return _from_browser_error(exc)
    except BrowserError as exc:
        return _from_browser_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _err("browser_action_failed", detail=str(exc))
    return ToolResult(ok=True, payload={"session_id": sid, **result})


def browser_wait(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Short stability wait (seconds, capped)."""
    del ctx
    sid = _session_id_arg(args)
    if isinstance(sid, ToolResult):
        return sid
    seconds = 0.5
    if "seconds" in args and args.get("seconds") is not None:
        raw = args.get("seconds")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return _err("invalid_seconds")
        seconds = float(raw)
        if seconds < 0:
            return _err("invalid_seconds")
        if seconds > MAX_WAIT_SECONDS:
            seconds = MAX_WAIT_SECONDS
    mgr = get_browser_session_manager()
    try:
        result = mgr.wait(sid, seconds=seconds)
    except SessionNotFoundError as exc:
        return _from_browser_error(exc)
    except BrowserError as exc:
        return _from_browser_error(exc)
    except Exception as exc:  # noqa: BLE001
        return _err("browser_action_failed", detail=str(exc))
    return ToolResult(ok=True, payload={"session_id": sid, **result})


def browser_screenshot(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Deferred (IK12) — screenshots to media store not implemented in v1."""
    del args, ctx
    return _err(
        "not_implemented",
        hint="browser screenshots deferred; use browser_snapshot (a11y text)",
    )
