"""Process-wide Playwright browser session manager.

Scope: headless Chromium sessions bound to moment_id; a11y snapshot + refs;
fail-closed when playwright or chromium is missing. Max concurrent sessions.
Out of scope: screenshots → media store; nested Browser-Use agent; guest isolation.

Lifecycle (must stay leak-free):
- ``browser_session_close`` / ``close(session_id)``
- ``close_for_moment(moment_id)`` on presence success finalize + fail_in_flight
- presence worker ``run()`` ``finally`` → ``close_all`` (work runs on BrowserThread)
- supervisor ``shutdown()`` → ``close_all`` safety net after worker join

Thread model (KD13): all Sync Playwright launch / page ops / teardown run on one
dedicated ``BrowserThread`` so caller-thread asyncio loop pollution cannot break
Sync API. ``owner_ident`` is the BrowserThread id. ``RLock`` is not held across
launch (reserve slot → hop → register).

Snapshot uses Playwright ``aria_snapshot`` (1.49+ locator / page API). Legacy
``page.accessibility.snapshot`` is a fallback only (removed in Playwright 1.57).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import queue
import re
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

_LOG = logging.getLogger(__name__)

T = TypeVar("T")

MAX_SESSIONS = 2
SNAPSHOT_MAX_CHARS = 32_000
# Hard guard so pathological trees cannot allocate unbounded ref maps.
MAX_REFS = 500
DEFAULT_NAV_TIMEOUT_MS = 30_000
# Supervisor join should cover a mid-goto shutdown (nav timeout + margin).
WORKER_JOIN_TIMEOUT_S = 5.0
WORKER_JOIN_TIMEOUT_WITH_BROWSER_S = 35.0
DEFAULT_WAIT_SECONDS = 0.5
MAX_WAIT_SECONDS = 10.0
# Bound for a single hop onto BrowserThread (launch / page op / teardown).
BROWSER_THREAD_OP_TIMEOUT_S = 120.0
BROWSER_THREAD_JOIN_S = 5.0

HINT_BROWSER_INSTALL = (
    "pip install -e '.[browser]' then playwright install chromium"
)
HINT_CHROMIUM_INSTALL = "playwright install chromium"
HINT_BROWSER_LAUNCH_FAILED = "host browser backend failed; see detail"

# Playwright aria YAML lines look like:
#   - heading "Title" [level=1]
#   - link "Home"
#   - list:
#     - listitem:
#       - text: "buy milk"
_ARIA_ITEM_RE = re.compile(
    r"^(\s*)-\s+"
    r"([^\s:\"\[]+)"  # role
    r":?"  # optional trailing colon (container / text: value forms)
    r"(?:\s+\"((?:\\.|[^\"\\])*)\")?"  # optional "name"
    r"(.*)$"  # attrs / : text value / rest
)


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
    """playwright present but Chromium binary missing."""

    reason = "chromium_unavailable"
    hint = HINT_CHROMIUM_INSTALL


class BrowserLaunchFailedError(BrowserError):
    """Playwright imported but Sync start/launch failed (env / Sync-in-asyncio).

    Never carries a pip-install / playwright-install hint — import already
    succeeded; the failure is host backend / thread environment.
    """

    reason = "browser_launch_failed"
    hint = HINT_BROWSER_LAUNCH_FAILED


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


class SnapshotUnavailableError(BrowserError):
    """No structured a11y API on this page/playwright build."""

    reason = "snapshot_unavailable"
    hint = (
        "need Playwright aria_snapshot (playwright>=1.49); "
        "upgrade: pip install -e '.[browser]' && playwright install chromium"
    )


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
    # Thread that opened the session (Playwright sync is not cross-thread safe).
    owner_ident: int = 0
    teardown_failed: bool = False


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


def _looks_like_missing_chromium(exc: BaseException) -> bool:
    """Heuristic: Playwright binary / executable missing messages."""
    msg = str(exc).lower()
    if "executable" in msg and ("exist" in msg or "not found" in msg):
        return True
    if "download new browsers" in msg:
        return True
    if "playwright install" in msg and (
        "browser" in msg or "chromium" in msg or "install" in msg
    ):
        return True
    if "chromium" in msg and ("missing" in msg or "not found" in msg):
        return True
    return False


def _looks_like_sync_in_asyncio(exc: BaseException) -> bool:
    """Heuristic: Playwright Sync API used while an asyncio loop is running."""
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "asyncio",
            "event loop",
            "sync api inside",
            "cannot be called from",
            "greenlet",
            "playwright sync api",
        )
    )


def _log_caller_loop_diagnostics(*, phase: str = "pre_launch") -> None:
    """Log thread-local asyncio state (DEBUG/INFO only; no secrets)."""
    tid = threading.get_ident()
    tname = threading.current_thread().name
    has_running = False
    is_running: bool | None = None
    try:
        loop = asyncio.get_running_loop()
        has_running = True
        try:
            is_running = loop.is_running()
        except Exception:  # noqa: BLE001
            is_running = None
    except RuntimeError:
        has_running = False
        is_running = False
    _LOG.info(
        "browser diagnostics phase=%s thread_ident=%s thread_name=%s "
        "has_running_loop=%s loop_is_running=%s",
        phase,
        tid,
        tname,
        has_running,
        is_running,
    )


def _default_launch() -> tuple[Any, Any, Any, Any]:
    """Start Playwright + headless Chromium; raise typed errors on failure.

    Must run on BrowserThread (no running asyncio loop). Taxonomy:
    - import failure → ``BrowserUnavailableError``
    - Sync start / env / Sync-in-asyncio after import → ``BrowserLaunchFailedError``
    - Chromium binary missing → ``ChromiumUnavailableError``
    """
    sync_playwright = _import_playwright_sync()
    try:
        pw = sync_playwright().start()
    except BrowserUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        # Import already succeeded — never emit pip-install as the primary hint.
        raise BrowserLaunchFailedError(
            f"failed to start playwright: {exc}",
            hint=HINT_BROWSER_LAUNCH_FAILED,
        ) from exc
    browser = None
    context = None
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
        # Partial launch: close what we opened before stop.
        for obj, method in (
            (context, "close"),
            (browser, "close"),
        ):
            if obj is None:
                continue
            try:
                getattr(obj, method)()
            except Exception:  # noqa: BLE001
                pass
        try:
            pw.stop()
        except Exception:  # noqa: BLE001
            pass
        if _looks_like_sync_in_asyncio(exc):
            raise BrowserLaunchFailedError(
                f"chromium launch failed (sync/asyncio): {exc}",
                hint=HINT_BROWSER_LAUNCH_FAILED,
            ) from exc
        if _looks_like_missing_chromium(exc):
            raise ChromiumUnavailableError(
                f"chromium launch failed: {exc}",
                hint=HINT_CHROMIUM_INSTALL,
            ) from exc
        # Other launch failures after import: backend/env, not install hint.
        raise BrowserLaunchFailedError(
            f"chromium launch failed: {exc}",
            hint=HINT_BROWSER_LAUNCH_FAILED,
        ) from exc


def _abort_playwright_stack(
    pw: Any,
    browser: Any,
    context: Any,
    page: Any = None,
) -> None:
    """Best-effort close context → browser → playwright.stop (any thread).

    ``page`` is accepted for call-site symmetry with launch tuples but is not
    closed separately (context.close covers it).
    """
    del page  # owned by context
    for obj, method in (
        (context, "close"),
        (browser, "close"),
        (pw, "stop"),
    ):
        if obj is None:
            continue
        try:
            fn = getattr(obj, method, None)
            if callable(fn):
                fn()
        except Exception:  # noqa: BLE001
            pass


class BrowserThread:
    """Dedicated worker thread for all Sync Playwright session ops + teardown.

    Playwright Sync is not cross-thread safe and refuses a running asyncio loop
    on the calling thread. Callers (PresenceWorker, tool handlers) hop here via
    ``submit``; re-entrant ``submit`` from this thread runs inline.

    On wait timeout the Future is **not** discarded: ``on_timeout_done`` (if
    given) runs when the worker later finishes, so callers can abort late
    launch success and keep slot accounting honest.
    """

    def __init__(self, *, name: str = "elyra-browser") -> None:
        self._name = name
        self._q: queue.Queue[
            tuple[Callable[[], Any], concurrent.futures.Future[Any]] | None
        ] = queue.Queue()
        self._ident: int = 0
        self._closed = False
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=self._name,
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("BrowserThread failed to start")

    def _run(self) -> None:
        self._ident = threading.get_ident()
        self._ready.set()
        while True:
            item = self._q.get()
            if item is None:
                break
            fn, fut = item
            if fut.set_running_or_notify_cancel():
                try:
                    fut.set_result(fn())
                except BaseException as exc:  # noqa: BLE001
                    fut.set_exception(exc)

    @property
    def ident(self) -> int:
        return self._ident

    @property
    def closed(self) -> bool:
        return self._closed

    def submit(
        self,
        fn: Callable[[], T],
        *,
        timeout: float | None = None,
        on_timeout_done: Callable[[concurrent.futures.Future[Any]], None]
        | None = None,
    ) -> T:
        """Run ``fn`` on the browser thread; block until done or timeout.

        ``timeout`` defaults to ``BROWSER_THREAD_OP_TIMEOUT_S`` (looked up at
        call time so tests can monkeypatch the module constant).

        If the wait times out, the worker may still complete ``fn``. When
        ``on_timeout_done`` is set it is invoked from the Future's done
        callback (typically on the browser thread via ``set_result`` /
        ``set_exception``). Without a callback, late success is logged as a
        discarded result so leaks are visible.
        """
        if timeout is None:
            timeout = BROWSER_THREAD_OP_TIMEOUT_S
        if self._closed:
            raise RuntimeError("BrowserThread is shut down")
        # Inline when already on the worker — avoids self-deadlock.
        if threading.get_ident() == self._ident:
            return fn()
        fut: concurrent.futures.Future[Any] = concurrent.futures.Future()
        self._q.put((fn, fut))
        try:
            return fut.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            _LOG.warning(
                "BrowserThread op timed out after %ss; work still running "
                "on worker (late completion will be handled or logged)",
                timeout,
            )

            def _late(f: concurrent.futures.Future[Any]) -> None:
                if on_timeout_done is not None:
                    try:
                        on_timeout_done(f)
                    except Exception as cb_exc:  # noqa: BLE001
                        _LOG.warning(
                            "BrowserThread on_timeout_done failed: %s", cb_exc
                        )
                    return
                try:
                    if f.cancelled():
                        return
                    err = f.exception()
                    if err is not None:
                        _LOG.warning(
                            "BrowserThread timed-out op completed with error: %s",
                            err,
                        )
                    else:
                        _LOG.warning(
                            "BrowserThread timed-out op completed successfully "
                            "with no late-result cleanup (result discarded)"
                        )
                except Exception as probe_exc:  # noqa: BLE001
                    _LOG.debug(
                        "BrowserThread late future probe failed: %s", probe_exc
                    )

            fut.add_done_callback(_late)
            raise TimeoutError(
                f"BrowserThread op timed out after {timeout}s"
            ) from exc

    def shutdown(self, *, join_timeout: float = BROWSER_THREAD_JOIN_S) -> None:
        """Stop the worker after draining is abandoned (sentinel)."""
        if self._closed:
            return
        self._closed = True
        try:
            self._q.put(None)
        except Exception:  # noqa: BLE001
            pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=join_timeout)
            if thread.is_alive():
                _LOG.warning(
                    "BrowserThread %s did not join within %.1fs",
                    self._name,
                    join_timeout,
                )


def _walk_ax_node(
    node: dict[str, Any],
    refs: dict[str, dict[str, Any]],
    lines: list[str],
    depth: int,
    *,
    max_chars: int,
    max_refs: int,
    char_count: list[int],
) -> bool:
    """Assign ref=eN and emit a compact line. Returns False if cap hit (stop)."""
    if len(refs) >= max_refs or char_count[0] >= max_chars:
        return False

    role = str(node.get("role") or "generic")
    name = str(node.get("name") or "")
    value = node.get("value")
    ref = f"e{len(refs) + 1}"
    indent = "  " * depth
    label = f"{indent}- {role}"
    if name:
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

    # Projected length including newline separator.
    projected = char_count[0] + len(label) + (1 if lines else 0)
    if projected > max_chars and lines:
        return False

    refs[ref] = {
        "role": role,
        "name": name,
        "value": value,
        "description": node.get("description"),
    }
    lines.append(label)
    char_count[0] = projected if lines[:-1] else len(label)

    for child in node.get("children") or []:
        if not isinstance(child, dict):
            continue
        if not _walk_ax_node(
            child,
            refs,
            lines,
            depth + 1,
            max_chars=max_chars,
            max_refs=max_refs,
            char_count=char_count,
        ):
            return False
    return True


def build_snapshot_from_ax_tree(
    tree: dict[str, Any] | None,
    *,
    max_chars: int = SNAPSHOT_MAX_CHARS,
    max_refs: int = MAX_REFS,
) -> tuple[str, dict[str, dict[str, Any]], bool]:
    """Build text snapshot + ref map from a legacy accessibility tree dict.

    Walk stops once projected text length hits ``max_chars`` or ref count hits
    ``max_refs`` (memory/CPU cap). Returns ``(text, refs, truncated)``.
    """
    refs: dict[str, dict[str, Any]] = {}
    lines: list[str] = []
    char_count = [0]
    truncated = False
    if isinstance(tree, dict) and tree:
        ok = _walk_ax_node(
            tree,
            refs,
            lines,
            0,
            max_chars=max_chars,
            max_refs=max_refs,
            char_count=char_count,
        )
        truncated = not ok
    text = "\n".join(lines) if lines else "(empty accessibility tree)"
    if truncated:
        if len(text) > max_chars:
            text = text[:max_chars].rstrip()
        text = text + ("\n… [truncated]" if text else "… [truncated]")
    return text, refs, truncated


def build_snapshot_from_aria_yaml(
    yaml_text: str,
    *,
    max_chars: int = SNAPSHOT_MAX_CHARS,
    max_refs: int = MAX_REFS,
) -> tuple[str, dict[str, dict[str, Any]], bool]:
    """Parse Playwright aria YAML, assign ``ref=eN``, cap chars/refs.

    Returns ``(text_with_refs, refs, truncated)``.
    """
    refs: dict[str, dict[str, Any]] = {}
    out_lines: list[str] = []
    char_count = 0
    truncated = False

    if not isinstance(yaml_text, str) or not yaml_text.strip():
        return "(empty aria snapshot)", refs, False

    for raw_line in yaml_text.splitlines():
        if char_count >= max_chars or len(refs) >= max_refs:
            truncated = True
            break
        line = raw_line.rstrip()
        if not line.strip():
            continue

        match = _ARIA_ITEM_RE.match(line)
        if not match:
            # Pass-through non-item lines (rare) with cap.
            if char_count + len(line) + 1 > max_chars and out_lines:
                truncated = True
                break
            out_lines.append(line)
            char_count += len(line) + (1 if char_count else 0)
            continue

        indent, role, name, rest = match.groups()
        name = (name or "").replace('\\"', '"')
        rest = rest or ""
        # Drop any pre-existing ref= markers Playwright might emit.
        rest = re.sub(r"\s*\[ref=[^\]]*\]", "", rest)
        # text: value form — name may be empty; value after colon in rest
        value = None
        text_val = re.match(r"^\s*:\s*(.*)$", rest)
        if text_val and not name:
            value = text_val.group(1).strip().strip('"')
            rest = ""

        ref = f"e{len(refs) + 1}"
        new_line = f"{indent}- {role}"
        if name:
            safe = name.replace('"', '\\"')
            new_line += f' "{safe}"'
        if rest.strip():
            new_line += rest.rstrip()
        elif value is not None and value != "":
            new_line += f": {value}"
        new_line += f" [ref={ref}]"

        projected = char_count + len(new_line) + (1 if out_lines else 0)
        if projected > max_chars and out_lines:
            truncated = True
            break

        refs[ref] = {
            "role": role,
            "name": name,
            "value": value,
        }
        out_lines.append(new_line)
        char_count = projected

    text = "\n".join(out_lines) if out_lines else "(empty aria snapshot)"
    if truncated:
        text = text + ("\n… [truncated]" if text else "… [truncated]")
    return text, refs, truncated


def page_has_structured_a11y_api(page: Any) -> bool:
    """True if page exposes aria_snapshot and/or legacy accessibility.snapshot."""
    if hasattr(page, "aria_snapshot") and callable(getattr(page, "aria_snapshot")):
        return True
    locator_factory = getattr(page, "locator", None)
    if callable(locator_factory):
        try:
            loc = locator_factory("body")
            if hasattr(loc, "aria_snapshot") and callable(
                getattr(loc, "aria_snapshot")
            ):
                return True
        except Exception:  # noqa: BLE001
            pass
    ax = getattr(page, "accessibility", None)
    if ax is not None and hasattr(ax, "snapshot") and callable(ax.snapshot):
        return True
    return False


def capture_page_snapshot(
    page: Any,
    *,
    max_chars: int = SNAPSHOT_MAX_CHARS,
    max_refs: int = MAX_REFS,
) -> tuple[str, dict[str, dict[str, Any]], bool]:
    """Capture structured a11y snapshot + refs from a Playwright page.

    Preference order:
    1. ``page.aria_snapshot()`` (modern)
    2. ``page.locator("body").aria_snapshot()`` (1.49+)
    3. legacy ``page.accessibility.snapshot()`` (pre-1.57 only)

    Raises ``SnapshotUnavailableError`` when no structured API is present
    (does **not** silently fall back to body text for the happy path).
    """
    # 1–2: aria YAML
    yaml_text: str | None = None
    if hasattr(page, "aria_snapshot") and callable(getattr(page, "aria_snapshot")):
        try:
            raw = page.aria_snapshot()
            if isinstance(raw, str) and raw.strip():
                yaml_text = raw
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("page.aria_snapshot failed: %s", exc)

    if yaml_text is None:
        locator_factory = getattr(page, "locator", None)
        if callable(locator_factory):
            for sel in ("body", "html", ":root"):
                try:
                    loc = locator_factory(sel)
                    if hasattr(loc, "aria_snapshot") and callable(
                        getattr(loc, "aria_snapshot")
                    ):
                        raw = loc.aria_snapshot()
                        if isinstance(raw, str) and raw.strip():
                            yaml_text = raw
                            break
                except Exception as exc:  # noqa: BLE001
                    _LOG.debug("locator(%r).aria_snapshot failed: %s", sel, exc)

    if yaml_text is not None:
        return build_snapshot_from_aria_yaml(
            yaml_text, max_chars=max_chars, max_refs=max_refs
        )

    # 3: legacy accessibility tree (removed in Playwright 1.57+)
    try:
        ax = getattr(page, "accessibility", None)
        if ax is not None and hasattr(ax, "snapshot") and callable(ax.snapshot):
            tree = ax.snapshot()
            if isinstance(tree, dict) and tree:
                return build_snapshot_from_ax_tree(
                    tree, max_chars=max_chars, max_refs=max_refs
                )
    except Exception as exc:  # noqa: BLE001
        _LOG.debug("accessibility.snapshot failed: %s", exc)

    if not page_has_structured_a11y_api(page):
        raise SnapshotUnavailableError(
            "page has neither aria_snapshot nor accessibility.snapshot"
        )
    raise SnapshotUnavailableError(
        "structured a11y API present but returned empty/failed snapshot"
    )


class BrowserSessionManager:
    """Process-scoped store of live browser sessions (max ``MAX_SESSIONS``).

    All Sync Playwright work runs on ``browser_thread`` (default: a private
    ``BrowserThread``). The registry ``RLock`` is never held across launch.
    """

    def __init__(
        self,
        *,
        launcher: Launcher | None = None,
        browser_thread: BrowserThread | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, BrowserSession] = {}
        # Slots reserved for in-flight opens (checked under lock; launch outside).
        self._pending_opens = 0
        self._launcher: Launcher = launcher or _default_launch
        self._owns_thread = browser_thread is None
        self._browser_thread = browser_thread or BrowserThread()

    @property
    def browser_thread(self) -> BrowserThread:
        return self._browser_thread

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _run_sync(self, fn: Callable[[], T]) -> T:
        """Run Sync Playwright work on BrowserThread."""
        return self._browser_thread.submit(fn)

    def open(self, moment_id: str = "") -> str:
        """Launch headless Chromium on BrowserThread; return ``session_id``.

        Raises
        ------
        BrowserUnavailableError
            playwright package not importable.
        ChromiumUnavailableError
            Chromium binary missing.
        BrowserLaunchFailedError
            Sync start / env failure after import (not an install-hint path),
            or launch hop timed out (late success is aborted asynchronously).
        SessionLimitError
            already at ``MAX_SESSIONS``.
        """
        with self._lock:
            in_use = len(self._sessions) + self._pending_opens
            if in_use >= MAX_SESSIONS:
                raise SessionLimitError(
                    f"already have {in_use} sessions "
                    f"(max {MAX_SESSIONS})"
                )
            self._pending_opens += 1
        launched: tuple[Any, Any, Any, Any] | None = None
        # If the launch hop times out, pending stays reserved until the late
        # done-callback aborts (on success) and releases the slot.
        pending_held_for_late = False
        try:
            _log_caller_loop_diagnostics(phase="pre_launch")

            def _on_timeout_launch_done(
                fut: concurrent.futures.Future[Any],
            ) -> None:
                """Abort late-success launch; always release reserved slot."""
                try:
                    if fut.cancelled():
                        return
                    err = fut.exception()
                    if err is not None:
                        _LOG.warning(
                            "browser open timed out; late launch failure: %s",
                            err,
                        )
                        return
                    resources = fut.result()
                    if (
                        isinstance(resources, tuple)
                        and len(resources) >= 3
                    ):
                        _LOG.warning(
                            "browser open timed out; aborting late-success "
                            "launch to avoid Chromium leak"
                        )
                        # Done-callback runs on the thread that set the result
                        # (BrowserThread) — safe to call Sync teardown inline.
                        pw, browser, context = resources[0], resources[1], resources[2]
                        page = resources[3] if len(resources) > 3 else None
                        _abort_playwright_stack(pw, browser, context, page)
                    else:
                        _LOG.warning(
                            "browser open timed out; late result not a launch "
                            "tuple: %r",
                            type(resources),
                        )
                finally:
                    with self._lock:
                        self._pending_opens = max(0, self._pending_opens - 1)

            # Launch OUTSIDE the registry lock on BrowserThread (KD13).
            try:
                launched = self._browser_thread.submit(
                    self._launcher,
                    on_timeout_done=_on_timeout_launch_done,
                )
            except TimeoutError as exc:
                pending_held_for_late = True
                raise BrowserLaunchFailedError(
                    f"browser launch timed out after "
                    f"{BROWSER_THREAD_OP_TIMEOUT_S}s",
                    hint=HINT_BROWSER_LAUNCH_FAILED,
                ) from exc
            pw, browser, context, page = launched
            with self._lock:
                session_id = f"bs_{uuid.uuid4().hex[:12]}"
                session = BrowserSession(
                    session_id=session_id,
                    moment_id=moment_id or "",
                    playwright=pw,
                    browser=browser,
                    context=context,
                    page=page,
                    owner_ident=self._browser_thread.ident,
                )
                self._sessions[session_id] = session
                # Ownership transferred before any fallible follow-up work
                # (logging) so except-path cannot double-teardown a registered
                # session.
                launched = None
                _LOG.info(
                    "browser session open session_id=%s moment_id=%s "
                    "count=%s owner_ident=%s",
                    session_id,
                    moment_id or "",
                    len(self._sessions),
                    session.owner_ident,
                )
                return session_id
        except Exception:
            # If launch succeeded but registration failed, tear down on thread.
            if launched is not None:
                pw, browser, context, page = launched

                def _abort() -> None:
                    _abort_playwright_stack(pw, browser, context, page)

                try:
                    self._run_sync(_abort)
                except Exception as abort_exc:  # noqa: BLE001
                    _LOG.warning(
                        "browser open abort teardown failed: %s", abort_exc
                    )
            raise
        finally:
            if not pending_held_for_late:
                with self._lock:
                    self._pending_opens = max(0, self._pending_opens - 1)

    def get(self, session_id: str) -> BrowserSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(f"unknown session_id={session_id!r}")
            return session

    def close(self, session_id: str, *, force: bool = False) -> bool:
        """Close one session.

        Teardown runs on BrowserThread **before** freeing the registry slot.
        On teardown failure the session stays registered (unless ``force=True``)
        so slot accounting and a later retry still know about the live Chromium
        process.

        Returns True if the session was known (and removed when teardown ok or
        forced).
        """
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
        try:
            ok = self._run_sync(lambda: self._teardown(session))
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "browser teardown hop failed session_id=%s: %s",
                session_id,
                exc,
            )
            ok = False
        with self._lock:
            if ok or force:
                self._sessions.pop(session_id, None)
                return True
            # Leave registered for retry; mark failed.
            if session_id in self._sessions:
                self._sessions[session_id].teardown_failed = True
            _LOG.error(
                "browser teardown incomplete session_id=%s; slot retained "
                "(force=False)",
                session_id,
            )
            return False

    def close_for_moment(self, moment_id: str, *, force: bool = True) -> int:
        """Close all sessions bound to ``moment_id``. Returns count removed."""
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
            if self.close(sid, force=force):
                closed += 1
        if closed:
            _LOG.info(
                "browser close_for_moment moment_id=%s closed=%s",
                moment_id,
                closed,
            )
        return closed

    def close_all(self, *, force: bool = True) -> int:
        """Close every session. Default force frees slots after best-effort teardown.

        Teardown always hops to BrowserThread (session ``owner_ident``). Callers
        on other threads only *request* close — they do not run Sync Playwright.
        """
        with self._lock:
            ids = list(self._sessions.keys())
        closed = 0
        for sid in ids:
            if self.close(sid, force=force):
                closed += 1
            elif force:
                pass
            else:
                if self.close(sid, force=True):
                    closed += 1
        if closed:
            _LOG.info("browser close_all closed=%s", closed)
        return closed

    def shutdown(self) -> None:
        """Close all sessions and stop the owned BrowserThread (if any)."""
        try:
            self.close_all(force=True)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("browser manager shutdown close_all failed: %s", exc)
        if self._owns_thread:
            try:
                self._browser_thread.shutdown()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("BrowserThread shutdown failed: %s", exc)

    def goto(
        self,
        session_id: str,
        url: str,
        *,
        timeout_ms: int = DEFAULT_NAV_TIMEOUT_MS,
    ) -> dict[str, Any]:
        def _op() -> dict[str, Any]:
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

        return self._run_sync(_op)

    def snapshot(
        self,
        session_id: str,
        *,
        max_chars: int = SNAPSHOT_MAX_CHARS,
    ) -> dict[str, Any]:
        def _op() -> dict[str, Any]:
            session = self.get(session_id)
            text, refs, truncated = capture_page_snapshot(
                session.page, max_chars=max_chars
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

        return self._run_sync(_op)

    def click(self, session_id: str, ref: str) -> dict[str, Any]:
        def _op() -> dict[str, Any]:
            session = self.get(session_id)
            locator = self._locator_for_ref(session, ref)
            try:
                locator.click()
            except Exception as exc:  # noqa: BLE001
                raise BrowserActionError(
                    f"click failed for ref={ref}: {exc}"
                ) from exc
            # DOM likely changed — force re-snapshot next.
            session.refs = {}
            return {"clicked": ref}

        return self._run_sync(_op)

    def type_text(
        self, session_id: str, ref: str, text: str, *, clear: bool = False
    ) -> dict[str, Any]:
        """Type into element by ref. ``clear=True`` fills (replace); else appends."""

        def _op() -> dict[str, Any]:
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
            # Input may change accessible name/value — prefer re-snapshot.
            session.refs = {}
            return {"ref": ref, "text_len": len(text), "cleared": clear}

        return self._run_sync(_op)

    def fill(self, session_id: str, ref: str, text: str) -> dict[str, Any]:
        return self.type_text(session_id, ref, text, clear=True)

    def get_text(self, session_id: str, ref: str | None = None) -> dict[str, Any]:
        def _op() -> dict[str, Any]:
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

        return self._run_sync(_op)

    def wait(
        self,
        session_id: str,
        *,
        seconds: float = DEFAULT_WAIT_SECONDS,
    ) -> dict[str, Any]:
        def _op() -> dict[str, Any]:
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
                    raise BrowserActionError(
                        f"wait failed: {exc}"
                    ) from sleep_exc
            return {"waited_seconds": secs}

        return self._run_sync(_op)

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

    def _teardown(self, session: BrowserSession) -> bool:
        """Best-effort close context → browser → playwright. True if all ok.

        Must run on BrowserThread (session owner).
        """
        me = threading.get_ident()
        if session.owner_ident and me != session.owner_ident:
            _LOG.warning(
                "browser teardown on non-owner thread session_id=%s "
                "owner=%s current=%s",
                session.session_id,
                session.owner_ident,
                me,
            )

        ok = True
        # Prefer context → browser → playwright.stop (N1: explicit close order).
        steps: list[tuple[str, Any, str]] = [
            ("context.close", getattr(session, "context", None), "close"),
            ("browser.close", getattr(session, "browser", None), "close"),
            ("playwright.stop", getattr(session, "playwright", None), "stop"),
        ]
        for label, obj, method in steps:
            if obj is None:
                continue
            try:
                fn = getattr(obj, method, None)
                if callable(fn):
                    fn()
                else:
                    ok = False
                    _LOG.warning("browser teardown missing %s", label)
            except Exception as exc:  # noqa: BLE001
                ok = False
                _LOG.warning("browser teardown %s failed: %s", label, exc)

        # Last-resort: kill browser subprocess if still alive and exposed.
        try:
            browser = getattr(session, "browser", None)
            proc_fn = getattr(browser, "process", None) if browser is not None else None
            if callable(proc_fn):
                proc = proc_fn()
                if proc is not None and getattr(proc, "poll", lambda: 0)() is None:
                    _LOG.error(
                        "browser process still alive after close; killing "
                        "session_id=%s pid=%s",
                        session.session_id,
                        getattr(proc, "pid", "?"),
                    )
                    try:
                        proc.kill()
                    except Exception as kill_exc:  # noqa: BLE001
                        ok = False
                        _LOG.warning("browser process kill failed: %s", kill_exc)
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("browser process probe failed: %s", exc)

        session.refs = {}
        if ok:
            session.teardown_failed = False
            _LOG.info("browser session closed session_id=%s", session.session_id)
        else:
            session.teardown_failed = True
            _LOG.error(
                "browser session teardown incomplete session_id=%s",
                session.session_id,
            )
        return ok


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
    """Replace the process singleton (tests). Shuts down previous if set."""
    global _manager
    with _manager_lock:
        old = _manager
        _manager = manager
    if old is not None and old is not manager:
        try:
            old.shutdown()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("previous browser manager shutdown failed: %s", exc)


def reset_browser_session_manager_for_tests() -> None:
    """Close all sessions, stop BrowserThread, drop the singleton (tests)."""
    set_browser_session_manager(None)
