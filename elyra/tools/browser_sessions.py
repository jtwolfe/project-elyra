"""Process-wide Playwright browser session manager.

Scope: headless Chromium sessions bound to moment_id; a11y snapshot + refs;
fail-closed when playwright or chromium is missing. Max concurrent sessions.
Out of scope: screenshots → media store; nested Browser-Use agent; guest isolation.

Lifecycle (must stay leak-free):
- ``browser_session_close`` / ``close(session_id)``
- ``close_for_moment(moment_id)`` on presence success finalize + fail_in_flight
- supervisor ``shutdown()`` → ``close_all()``
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

_LOG = logging.getLogger(__name__)

MAX_SESSIONS = 2
SNAPSHOT_MAX_CHARS = 32_000
DEFAULT_NAV_TIMEOUT_MS = 30_000
DEFAULT_WAIT_SECONDS = 0.5
MAX_WAIT_SECONDS = 10.0

HINT_BROWSER_INSTALL = (
    "pip install -e '.[browser]' then playwright install chromium"
)
HINT_CHROMIUM_INSTALL = "playwright install chromium"


class BrowserError(Exception):
    """Base browser session error with tool-facing reason + install hint."""

    reason: str = "browser_error"
    hint: str = ""

    def __init__(self, message: str = "", *, hint: str | None = None) -> None:
        super().__init__(message or self.reason)
        if hint is not None:
            self.hint = hint


class BrowserUnavailableError(BrowserError):
    """playwright package not importable."""

    reason = "browser_unavailable"
    hint = HINT_BROWSER_INSTALL


class ChromiumUnavailableError(BrowserError):
    """playwright present but Chromium binary missing / launch failed."""

    reason = "chromium_unavailable"
    hint = HINT_CHROMIUM_INSTALL


class SessionLimitError(BrowserError):
    reason = "session_limit"
    hint = f"max concurrent browser sessions is {MAX_SESSIONS}; close one first"


class SessionNotFoundError(BrowserError):
    reason = "session_not_found"
    hint = ""


class StaleRefError(BrowserError):
    reason = "stale_ref"
    hint = "call browser_snapshot again after navigation or DOM change"


class BrowserActionError(BrowserError):
    reason = "browser_action_failed"
    hint = ""


@dataclass
class BrowserSession:
    """One live headless context bound to a moment (or unbound)."""

    session_id: str
    moment_id: str
    playwright: Any
    browser: Any
    context: Any
    page: Any
    refs: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_url: str = ""


# Optional injectable launcher for hermetic tests:
#   () -> (playwright, browser, context, page)
Launcher = Callable[[], tuple[Any, Any, Any, Any]]


def _import_playwright_sync() -> Any:
    """Import playwright.sync_api or raise BrowserUnavailableError."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-untyped]
    except ImportError as exc:
        raise BrowserUnavailableError(
            "playwright package not installed",
            hint=HINT_BROWSER_INSTALL,
        ) from exc
    return sync_playwright


def _default_launch() -> tuple[Any, Any, Any, Any]:
    """Start Playwright + headless Chromium; raise typed errors on failure."""
    sync_playwright = _import_playwright_sync()
    try:
        pw = sync_playwright().start()
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, BrowserUnavailableError):
            raise
        raise BrowserUnavailableError(
            f"failed to start playwright: {exc}",
            hint=HINT_BROWSER_INSTALL,
        ) from exc
    try:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=False,
        )
        page = context.new_page()
        page.set_default_timeout(DEFAULT_NAV_TIMEOUT_MS)
        return pw, browser, context, page
    except Exception as exc:  # noqa: BLE001
        try:
            pw.stop()
        except Exception:  # noqa: BLE001
            pass
        # Package import succeeded; binary/launch path failed → chromium_unavailable.
        raise ChromiumUnavailableError(
            f"chromium launch failed: {exc}",
            hint=HINT_CHROMIUM_INSTALL,
        ) from exc


def _walk_ax_node(
    node: dict[str, Any],
    refs: dict[str, dict[str, Any]],
    lines: list[str],
    depth: int,
) -> None:
    """Assign ref=eN and emit a compact YAML-ish a11y line."""
    role = str(node.get("role") or "generic")
    name = str(node.get("name") or "")
    value = node.get("value")
    ref = f"e{len(refs) + 1}"
    refs[ref] = {
        "role": role,
        "name": name,
        "value": value,
        "description": node.get("description"),
    }
    indent = "  " * depth
    label = f'{indent}- {role}'
    if name:
        # Escape quotes lightly for readability
        safe = name.replace('"', '\\"')
        label += f' "{safe}"'
    if value is not None and value != "" and role in (
        "textbox",
        "searchbox",
        "combobox",
        "spinbutton",
    ):
        label += f" value={value!r}"
    label += f" [ref={ref}]"
    lines.append(label)
    for child in node.get("children") or []:
        if isinstance(child, dict):
            _walk_ax_node(child, refs, lines, depth + 1)


def build_snapshot_from_ax_tree(
    tree: dict[str, Any] | None,
    *,
    max_chars: int = SNAPSHOT_MAX_CHARS,
) -> tuple[str, dict[str, dict[str, Any]], bool]:
    """Build text snapshot + ref map from Playwright accessibility tree.

    Returns ``(text, refs, truncated)``.
    """
    refs: dict[str, dict[str, Any]] = {}
    lines: list[str] = []
    if isinstance(tree, dict) and tree:
        _walk_ax_node(tree, refs, lines, 0)
    text = "\n".join(lines) if lines else "(empty accessibility tree)"
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n… [truncated]"
        truncated = True
    return text, refs, truncated


class BrowserSessionManager:
    """Process-scoped store of live browser sessions (max ``MAX_SESSIONS``)."""

    def __init__(self, *, launcher: Launcher | None = None) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, BrowserSession] = {}
        self._launcher: Launcher = launcher or _default_launch

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def open(self, moment_id: str = "") -> str:
        """Launch headless Chromium; return ``session_id``.

        Raises
        ------
        BrowserUnavailableError
            playwright import/start failed.
        ChromiumUnavailableError
            binary missing or launch failed.
        SessionLimitError
            already at ``MAX_SESSIONS``.
        """
        with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                raise SessionLimitError(
                    f"already have {len(self._sessions)} sessions (max {MAX_SESSIONS})"
                )
            pw, browser, context, page = self._launcher()
            session_id = f"bs_{uuid.uuid4().hex[:12]}"
            session = BrowserSession(
                session_id=session_id,
                moment_id=moment_id or "",
                playwright=pw,
                browser=browser,
                context=context,
                page=page,
            )
            self._sessions[session_id] = session
            _LOG.info(
                "browser session open session_id=%s moment_id=%s count=%s",
                session_id,
                moment_id or "",
                len(self._sessions),
            )
            return session_id

    def get(self, session_id: str) -> BrowserSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(f"unknown session_id={session_id!r}")
            return session

    def close(self, session_id: str) -> bool:
        """Close one session. Returns True if it existed."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        self._teardown(session)
        return True

    def close_for_moment(self, moment_id: str) -> int:
        """Close all sessions bound to ``moment_id``. Returns count closed."""
        if not moment_id:
            return 0
        with self._lock:
            to_close = [
                sid
                for sid, s in self._sessions.items()
                if s.moment_id == moment_id
            ]
        closed = 0
        for sid in to_close:
            if self.close(sid):
                closed += 1
        if closed:
            _LOG.info(
                "browser close_for_moment moment_id=%s closed=%s",
                moment_id,
                closed,
            )
        return closed

    def close_all(self) -> int:
        """Close every session (supervisor shutdown). Returns count closed."""
        with self._lock:
            ids = list(self._sessions.keys())
        closed = 0
        for sid in ids:
            if self.close(sid):
                closed += 1
        if closed:
            _LOG.info("browser close_all closed=%s", closed)
        return closed

    def goto(
        self,
        session_id: str,
        url: str,
        *,
        timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    ) -> dict[str, Any]:
        session = self.get(session_id)
        # Clear refs — stale after navigation.
        session.refs = {}
        try:
            response = session.page.goto(
                url, wait_until="load", timeout=timeout_ms
            )
        except Exception as exc:  # noqa: BLE001
            raise BrowserActionError(
                f"goto failed: {exc}",
            ) from exc
        try:
            session.last_url = session.page.url or url
        except Exception:  # noqa: BLE001
            session.last_url = url
        status = None
        if response is not None:
            try:
                status = response.status
            except Exception:  # noqa: BLE001
                status = None
        return {"url": session.last_url, "status": status}

    def snapshot(
        self,
        session_id: str,
        *,
        max_chars: int = SNAPSHOT_MAX_CHARS,
    ) -> dict[str, Any]:
        session = self.get(session_id)
        tree = self._ax_snapshot(session.page)
        text, refs, truncated = build_snapshot_from_ax_tree(
            tree, max_chars=max_chars
        )
        session.refs = refs
        try:
            url = session.page.url or session.last_url
        except Exception:  # noqa: BLE001
            url = session.last_url
        session.last_url = url or session.last_url
        return {
            "snapshot": text,
            "url": session.last_url,
            "ref_count": len(refs),
            "truncated": truncated,
            "max_chars": max_chars,
        }

    def click(self, session_id: str, ref: str) -> dict[str, Any]:
        session = self.get(session_id)
        locator = self._locator_for_ref(session, ref)
        try:
            locator.click()
        except Exception as exc:  # noqa: BLE001
            raise BrowserActionError(f"click failed for ref={ref}: {exc}") from exc
        # DOM likely changed — force re-snapshot next.
        session.refs = {}
        return {"clicked": ref}

    def type_text(
        self, session_id: str, ref: str, text: str, *, clear: bool = False
    ) -> dict[str, Any]:
        """Type into element by ref. ``clear=True`` fills (replace); else appends."""
        session = self.get(session_id)
        locator = self._locator_for_ref(session, ref)
        try:
            if clear:
                locator.fill(text)
            else:
                locator.click()
                locator.type(text)
        except Exception as exc:  # noqa: BLE001
            action = "fill" if clear else "type"
            raise BrowserActionError(
                f"{action} failed for ref={ref}: {exc}"
            ) from exc
        return {"ref": ref, "text_len": len(text), "cleared": clear}

    def fill(self, session_id: str, ref: str, text: str) -> dict[str, Any]:
        return self.type_text(session_id, ref, text, clear=True)

    def get_text(self, session_id: str, ref: str | None = None) -> dict[str, Any]:
        session = self.get(session_id)
        try:
            if ref:
                locator = self._locator_for_ref(session, ref)
                text = locator.inner_text()
            else:
                text = session.page.inner_text("body")
        except StaleRefError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BrowserActionError(f"get_text failed: {exc}") from exc
        if not isinstance(text, str):
            text = str(text) if text is not None else ""
        truncated = False
        if len(text) > SNAPSHOT_MAX_CHARS:
            text = text[:SNAPSHOT_MAX_CHARS].rstrip() + "\n… [truncated]"
            truncated = True
        return {"text": text, "ref": ref, "truncated": truncated}

    def wait(
        self,
        session_id: str,
        *,
        seconds: float = DEFAULT_WAIT_SECONDS,
    ) -> dict[str, Any]:
        session = self.get(session_id)
        try:
            secs = float(seconds)
        except (TypeError, ValueError) as exc:
            raise BrowserActionError("invalid wait seconds") from exc
        if secs < 0:
            secs = 0.0
        if secs > MAX_WAIT_SECONDS:
            secs = MAX_WAIT_SECONDS
        try:
            session.page.wait_for_timeout(int(secs * 1000))
        except Exception as exc:  # noqa: BLE001
            # Some fakes expose sleep instead of wait_for_timeout
            try:
                import time

                time.sleep(secs)
            except Exception as sleep_exc:  # noqa: BLE001
                raise BrowserActionError(f"wait failed: {exc}") from sleep_exc
        return {"waited_seconds": secs}

    def _locator_for_ref(self, session: BrowserSession, ref: str) -> Any:
        if not ref or not isinstance(ref, str):
            raise StaleRefError("missing ref")
        info = session.refs.get(ref)
        if info is None:
            raise StaleRefError(
                f"ref={ref!r} not in last snapshot (re-snapshot required)"
            )
        role = info.get("role") or "generic"
        name = info.get("name") or ""
        page = session.page
        try:
            if name:
                locator = page.get_by_role(role, name=name)
            else:
                locator = page.get_by_role(role)
            return locator.first
        except Exception as exc:  # noqa: BLE001
            raise BrowserActionError(
                f"could not resolve ref={ref}: {exc}"
            ) from exc

    def _ax_snapshot(self, page: Any) -> dict[str, Any] | None:
        """Best-effort accessibility tree (Playwright ``page.accessibility``)."""
        try:
            ax = getattr(page, "accessibility", None)
            if ax is not None and hasattr(ax, "snapshot"):
                tree = ax.snapshot()
                if isinstance(tree, dict):
                    return tree
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("accessibility.snapshot failed: %s", exc)
        # Fallback: single root with body text so callers still get content.
        try:
            body = page.inner_text("body")
        except Exception:  # noqa: BLE001
            body = ""
        if not isinstance(body, str):
            body = str(body) if body is not None else ""
        return {
            "role": "WebArea",
            "name": "",
            "children": [
                {"role": "generic", "name": body[:2000] if body else ""},
            ],
        }

    def _teardown(self, session: BrowserSession) -> None:
        for closer, obj in (
            ("context.close", getattr(session, "context", None)),
            ("browser.close", getattr(session, "browser", None)),
            ("playwright.stop", getattr(session, "playwright", None)),
        ):
            if obj is None:
                continue
            try:
                if closer.endswith("stop") and hasattr(obj, "stop"):
                    obj.stop()
                elif hasattr(obj, "close"):
                    obj.close()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("browser teardown %s failed: %s", closer, exc)
        session.refs = {}
        _LOG.info("browser session closed session_id=%s", session.session_id)


# --- process singleton -------------------------------------------------------

_manager_lock = threading.Lock()
_manager: BrowserSessionManager | None = None


def get_browser_session_manager() -> BrowserSessionManager:
    """Return the process-wide session manager (created on first use)."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = BrowserSessionManager()
        return _manager


def set_browser_session_manager(manager: BrowserSessionManager | None) -> None:
    """Replace the process singleton (tests). Closes previous if set to new/None."""
    global _manager
    with _manager_lock:
        old = _manager
        _manager = manager
    if old is not None and old is not manager:
        try:
            old.close_all()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("previous browser manager close_all failed: %s", exc)


def reset_browser_session_manager_for_tests() -> None:
    """Close all sessions and drop the singleton (hermetic tests)."""
    set_browser_session_manager(None)
