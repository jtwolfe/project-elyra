"""Browser session manager + builtins — hermetic mocks (no real Playwright)."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.tools.browser_sessions import (
    HINT_BROWSER_LAUNCH_FAILED,
    HINT_CHROMIUM_INSTALL,
    MAX_REFS,
    MAX_SESSIONS,
    BrowserLaunchFailedError,
    BrowserSessionManager,
    BrowserUnavailableError,
    ChromiumUnavailableError,
    SessionLimitError,
    SnapshotUnavailableError,
    StaleRefError,
    build_snapshot_from_aria_yaml,
    build_snapshot_from_ax_tree,
    capture_page_snapshot,
    get_browser_session_manager,
    page_has_structured_a11y_api,
    reset_browser_session_manager_for_tests,
    set_browser_session_manager,
)
from elyra.tools.builtin import browser as browser_tools
from elyra.tools.types import ToolContext


# ---------------------------------------------------------------------------
# Fake Playwright surface (aria_snapshot primary; no legacy accessibility)
# ---------------------------------------------------------------------------


class _FakeLocator:
    def __init__(
        self,
        page: "_FakePage",
        role: str = "",
        name: str = "",
        *,
        selector: str = "",
    ) -> None:
        self._page = page
        self._role = role
        self._name = name
        self._selector = selector
        self.first = self

    def click(self) -> None:
        self._page.clicks.append((self._role, self._name))

    def fill(self, text: str) -> None:
        self._page.fills.append((self._role, self._name, text))

    def type(self, text: str) -> None:
        self._page.types.append((self._role, self._name, text))

    def inner_text(self) -> str:
        return f"text:{self._role}:{self._name}"

    def aria_snapshot(self) -> str:
        return self._page.aria_yaml


class _FakePage:
    def __init__(self, *, with_aria: bool = True) -> None:
        self.url = "about:blank"
        self.clicks: list[tuple[str, str]] = []
        self.fills: list[tuple[str, str, str]] = []
        self.types: list[tuple[str, str, str]] = []
        self.waited_ms: list[int] = []
        self.with_aria = with_aria
        self.aria_yaml = (
            '- heading "Test" [level=1]\n'
            '- link "Home"\n'
            '- textbox "Search"\n'
            '- button "Go"\n'
        )
        # Intentionally no .accessibility — modern Playwright 1.57+ surface.

    def set_default_timeout(self, ms: int) -> None:
        self.default_timeout = ms

    def goto(self, url: str, wait_until: str = "load", timeout: int = 0) -> Any:
        self.url = url
        resp = MagicMock()
        resp.status = 200
        return resp

    def get_by_role(self, role: str, name: str | None = None) -> _FakeLocator:
        return _FakeLocator(self, role, name or "")

    def locator(self, selector: str) -> Any:
        if not self.with_aria:
            return object()  # no aria_snapshot
        return _FakeLocator(self, selector=selector)

    def aria_snapshot(self) -> str:
        if not self.with_aria:
            raise RuntimeError("aria_snapshot disabled")
        return self.aria_yaml

    def inner_text(self, selector: str = "body") -> str:
        return f"body-text:{self.url}"

    def wait_for_timeout(self, ms: int) -> None:
        self.waited_ms.append(ms)


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.closed = False
        self.fail_close = False

    def new_page(self) -> _FakePage:
        return self._page

    def close(self) -> None:
        if self.fail_close:
            raise RuntimeError("context close boom")
        self.closed = True


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.closed = False
        self.fail_close = False
        self._proc_alive = False

    def new_context(self, **kwargs: Any) -> _FakeContext:
        return _FakeContext(self._page)

    def close(self) -> None:
        if self.fail_close:
            raise RuntimeError("browser close boom")
        self.closed = True
        self._proc_alive = False

    def process(self) -> Any:
        if not self._proc_alive:
            return None

        class _P:
            pid = 4242

            def poll(self) -> int | None:
                return None

            def kill(self) -> None:
                pass

        return _P()


class _FakePlaywright:
    def __init__(self) -> None:
        self.stopped = False
        self.fail_stop = False

    def stop(self) -> None:
        if self.fail_stop:
            raise RuntimeError("pw stop boom")
        self.stopped = True


def _fake_launcher(
    *,
    with_aria: bool = True,
    fail_context_close: bool = False,
) -> Any:
    def launch() -> tuple[Any, Any, Any, Any]:
        page = _FakePage(with_aria=with_aria)
        pw = _FakePlaywright()
        browser = _FakeBrowser(page)
        context = browser.new_context()
        context.fail_close = fail_context_close
        return pw, browser, context, page

    return launch


@pytest.fixture(autouse=True)
def _reset_browser_singleton() -> None:
    reset_browser_session_manager_for_tests()
    yield
    reset_browser_session_manager_for_tests()


def _ctx(moment_id: str = "mom_1") -> ToolContext:
    return ToolContext(paths=resolve_paths(), moment_id=moment_id)


def _mgr(**kwargs: Any) -> BrowserSessionManager:
    mgr = BrowserSessionManager(launcher=_fake_launcher(**kwargs))
    set_browser_session_manager(mgr)
    return mgr


# ---------------------------------------------------------------------------
# Snapshot pure helpers
# ---------------------------------------------------------------------------


def test_build_snapshot_from_aria_yaml_assigns_refs() -> None:
    yaml = (
        '- heading "Todos" [level=1]\n'
        '- textbox "What needs to be done?"\n'
        "- list:\n"
        "  - listitem:\n"
        '    - checkbox "Toggle" [checked]\n'
        '    - text: "buy milk"\n'
    )
    text, refs, truncated = build_snapshot_from_aria_yaml(yaml)
    assert truncated is False
    assert "ref=e1" in text
    assert "heading" in text
    assert any(r.get("role") == "textbox" for r in refs.values())
    assert any(r.get("role") == "button" or r.get("role") == "checkbox" for r in refs.values())
    assert len(refs) >= 4


def test_build_snapshot_from_aria_yaml_caps_chars_and_refs() -> None:
    lines = [f'- button "B{i}"' for i in range(200)]
    yaml = "\n".join(lines)
    text, refs, truncated = build_snapshot_from_aria_yaml(yaml, max_chars=120)
    assert truncated is True
    assert "truncated" in text
    assert len(text) <= 120 + len("\n… [truncated]") + 5
    # refs only for emitted lines
    assert len(refs) < 200
    assert len(refs) <= MAX_REFS


def test_build_snapshot_from_ax_tree_stops_at_cap() -> None:
    tree = {
        "role": "WebArea",
        "name": "Doc",
        "children": [{"role": "button", "name": f"A{i}"} for i in range(300)],
    }
    text, refs, truncated = build_snapshot_from_ax_tree(tree, max_chars=200)
    assert truncated is True
    assert "truncated" in text
    assert len(refs) < 300
    # Must not retain full unbounded map relative to char budget
    assert len(refs) <= 50


def test_capture_prefers_aria_snapshot() -> None:
    page = _FakePage(with_aria=True)
    text, refs, truncated = capture_page_snapshot(page)
    assert truncated is False
    assert "ref=e" in text
    assert refs
    assert any(i.get("role") == "button" for i in refs.values())


def test_capture_raises_without_structured_api() -> None:
    page = object()  # no aria, no accessibility
    assert page_has_structured_a11y_api(page) is False
    with pytest.raises(SnapshotUnavailableError) as ei:
        capture_page_snapshot(page)
    assert ei.value.reason == "snapshot_unavailable"


def test_capture_legacy_accessibility_fallback() -> None:
    class _Ax:
        def snapshot(self) -> dict[str, Any]:
            return {
                "role": "WebArea",
                "name": "Legacy",
                "children": [{"role": "button", "name": "OK"}],
            }

    class _LegacyPage:
        accessibility = _Ax()
        url = "about:blank"

    text, refs, _ = capture_page_snapshot(_LegacyPage())
    assert "button" in text
    assert refs


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------


def test_open_goto_snapshot_click_close() -> None:
    mgr = _mgr()
    sid = mgr.open(moment_id="m1")
    assert sid.startswith("bs_")
    assert mgr.session_count == 1

    nav = mgr.goto(sid, "https://example.com/")
    assert nav["url"] == "https://example.com/"

    snap = mgr.snapshot(sid)
    assert snap["ref_count"] >= 3
    assert "ref=e" in snap["snapshot"]
    assert snap["truncated"] is False

    button_ref = None
    for ref, info in mgr.get(sid).refs.items():
        if info.get("role") == "button":
            button_ref = ref
            break
    assert button_ref is not None
    clicked = mgr.click(sid, button_ref)
    assert clicked["clicked"] == button_ref
    assert mgr.get(sid).refs == {}

    assert mgr.close(sid) is True
    assert mgr.session_count == 0
    assert mgr.close(sid) is False


def test_max_sessions_two() -> None:
    mgr = _mgr()
    s1 = mgr.open(moment_id="m1")
    s2 = mgr.open(moment_id="m1")
    assert s1 != s2
    with pytest.raises(SessionLimitError) as ei:
        mgr.open(moment_id="m2")
    assert ei.value.reason == "session_limit"
    assert MAX_SESSIONS == 2
    mgr.close(s1)
    s3 = mgr.open(moment_id="m2")
    assert s3
    mgr.close_all()
    assert mgr.session_count == 0


def test_close_for_moment_only_bound() -> None:
    mgr = _mgr()
    a = mgr.open(moment_id="momA")
    b = mgr.open(moment_id="momB")
    closed = mgr.close_for_moment("momA")
    assert closed == 1
    assert mgr.session_count == 1
    with pytest.raises(Exception):
        mgr.get(a)
    assert mgr.get(b).session_id == b
    assert mgr.close_for_moment("momB") == 1
    assert mgr.session_count == 0


def test_close_all() -> None:
    mgr = _mgr()
    mgr.open(moment_id="x")
    mgr.open(moment_id="y")
    assert mgr.close_all() == 2
    assert mgr.session_count == 0


def test_stale_ref_raises() -> None:
    mgr = _mgr()
    sid = mgr.open(moment_id="m")
    mgr.snapshot(sid)
    with pytest.raises(StaleRefError) as ei:
        mgr.click(sid, "e999")
    assert ei.value.reason == "stale_ref"
    ref = next(iter(mgr.get(sid).refs))
    mgr.goto(sid, "https://example.com/next")
    with pytest.raises(StaleRefError):
        mgr.click(sid, ref)


def test_type_fill_get_text_wait() -> None:
    mgr = _mgr()
    sid = mgr.open(moment_id="m")
    mgr.snapshot(sid)
    tb = None
    for ref, info in mgr.get(sid).refs.items():
        if info.get("role") == "textbox":
            tb = ref
            break
    assert tb
    mgr.type_text(sid, tb, "hi")
    # refs cleared after type
    assert mgr.get(sid).refs == {}
    mgr.snapshot(sid)
    tb2 = next(
        r for r, i in mgr.get(sid).refs.items() if i.get("role") == "textbox"
    )
    mgr.fill(sid, tb2, "full")
    mgr.snapshot(sid)
    body = mgr.get_text(sid)
    assert "body-text" in body["text"]
    waited = mgr.wait(sid, seconds=0.01)
    assert waited["waited_seconds"] == 0.01


def test_close_retains_slot_when_teardown_fails() -> None:
    """Issue 2: do not free slot before successful teardown (force=False)."""
    mgr = _mgr(fail_context_close=True)
    sid = mgr.open(moment_id="m")
    # Make browser.close also fail so teardown is incomplete.
    session = mgr.get(sid)
    session.browser.fail_close = True
    session.playwright.fail_stop = True

    removed = mgr.close(sid, force=False)
    assert removed is False
    assert mgr.session_count == 1
    assert mgr.get(sid).teardown_failed is True

    # force frees the slot after best-effort teardown
    assert mgr.close(sid, force=True) is True
    assert mgr.session_count == 0


def test_close_all_force_frees_after_failed_teardown() -> None:
    mgr = _mgr(fail_context_close=True)
    sid = mgr.open(moment_id="m")
    session = mgr.get(sid)
    session.browser.fail_close = True
    session.playwright.fail_stop = True
    n = mgr.close_all(force=True)
    assert n == 1
    assert mgr.session_count == 0


# ---------------------------------------------------------------------------
# Fail-closed: unavailable paths
# ---------------------------------------------------------------------------


def test_browser_unavailable_on_import(monkeypatch: pytest.MonkeyPatch) -> None:
    import elyra.tools.browser_sessions as bs

    def _boom() -> Any:
        raise BrowserUnavailableError("no package")

    monkeypatch.setattr(bs, "_import_playwright_sync", _boom)

    def bad_launch() -> tuple[Any, Any, Any, Any]:
        bs._import_playwright_sync()
        raise AssertionError("should not reach")

    mgr = BrowserSessionManager(launcher=bad_launch)
    set_browser_session_manager(mgr)
    with pytest.raises(BrowserUnavailableError) as ei:
        mgr.open(moment_id="m")
    assert ei.value.reason == "browser_unavailable"
    assert "pip install" in ei.value.hint or "browser" in ei.value.hint

    result = browser_tools.browser_session_open({}, _ctx())
    assert result.ok is False
    assert result.error_reason == "browser_unavailable"
    assert "pip install" in result.payload.get("hint", "")
    assert "playwright install chromium" in result.payload.get("hint", "")


def test_chromium_unavailable_on_launch() -> None:
    def bad_launch() -> tuple[Any, Any, Any, Any]:
        raise ChromiumUnavailableError(
            "Executable doesn't exist",
            hint=HINT_CHROMIUM_INSTALL,
        )

    mgr = BrowserSessionManager(launcher=bad_launch)
    set_browser_session_manager(mgr)
    result = browser_tools.browser_session_open({}, _ctx())
    assert result.ok is False
    assert result.error_reason == "chromium_unavailable"
    assert "playwright install chromium" in result.payload.get("hint", "")


def test_browser_launch_failed_not_install_hint() -> None:
    """Post-import Sync/start failures must not look like missing pip package."""

    def bad_launch() -> tuple[Any, Any, Any, Any]:
        raise BrowserLaunchFailedError(
            "failed to start playwright: Sync API inside the asyncio loop",
            hint=HINT_BROWSER_LAUNCH_FAILED,
        )

    mgr = BrowserSessionManager(launcher=bad_launch)
    set_browser_session_manager(mgr)
    with pytest.raises(BrowserLaunchFailedError) as ei:
        mgr.open(moment_id="m")
    assert ei.value.reason == "browser_launch_failed"
    assert ei.value.hint == HINT_BROWSER_LAUNCH_FAILED
    assert "pip install" not in ei.value.hint

    result = browser_tools.browser_session_open({}, _ctx())
    assert result.ok is False
    assert result.error_reason == "browser_launch_failed"
    hint = result.payload.get("hint", "")
    assert hint == HINT_BROWSER_LAUNCH_FAILED
    assert "host browser backend" in hint
    assert "pip install" not in hint
    assert "playwright install chromium" not in hint


def test_default_launch_maps_start_failure_to_launch_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_default_launch: after import, start() boom → browser_launch_failed."""
    import elyra.tools.browser_sessions as bs

    class _FakeSync:
        def start(self) -> Any:
            raise RuntimeError(
                "It looks like you are using Playwright Sync API "
                "inside the asyncio loop"
            )

    def fake_import() -> Any:
        return lambda: _FakeSync()

    monkeypatch.setattr(bs, "_import_playwright_sync", fake_import)
    with pytest.raises(BrowserLaunchFailedError) as ei:
        bs._default_launch()
    assert ei.value.reason == "browser_launch_failed"
    assert "pip install" not in ei.value.hint


def test_default_launch_maps_missing_binary_to_chromium(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import elyra.tools.browser_sessions as bs

    class _FakeChromium:
        def launch(self, **kwargs: Any) -> Any:
            raise RuntimeError("Executable doesn't exist at /missing/chrome")

    class _FakePW:
        chromium = _FakeChromium()

        def stop(self) -> None:
            pass

    class _FakeSync:
        def start(self) -> _FakePW:
            return _FakePW()

    monkeypatch.setattr(bs, "_import_playwright_sync", lambda: (lambda: _FakeSync()))
    with pytest.raises(ChromiumUnavailableError) as ei:
        bs._default_launch()
    assert ei.value.reason == "chromium_unavailable"
    assert "playwright install chromium" in ei.value.hint


def test_snapshot_unavailable_surfaces_tool_error() -> None:
    """No silent body-text happy path when structured a11y API missing."""

    class _BarePage:
        """No aria_snapshot, no accessibility, no locator.aria_snapshot."""

        url = "about:blank"

        def set_default_timeout(self, ms: int) -> None:
            pass

        def inner_text(self, selector: str = "body") -> str:
            return "should-not-be-used-as-structured-snapshot"

        def locator(self, selector: str) -> object:
            return object()

        def get_by_role(self, role: str, name: str | None = None) -> Any:
            raise RuntimeError("no roles")

    def launch() -> tuple[Any, Any, Any, Any]:
        page = _BarePage()
        pw = _FakePlaywright()
        browser = _FakeBrowser(_FakePage())
        context = browser.new_context()
        # Use bare page instead of fake
        return pw, browser, context, page

    mgr = BrowserSessionManager(launcher=launch)
    set_browser_session_manager(mgr)
    sid = mgr.open(moment_id="m")
    with pytest.raises(SnapshotUnavailableError):
        mgr.snapshot(sid)
    result = browser_tools.browser_snapshot({"session_id": sid}, _ctx())
    assert result.ok is False
    assert result.error_reason == "snapshot_unavailable"
    assert "aria_snapshot" in result.payload.get("hint", "")


def test_builtin_open_goto_snapshot_click_close() -> None:
    _mgr()
    ctx = _ctx("moment_x")
    opened = browser_tools.browser_session_open({}, ctx)
    assert opened.ok
    sid = opened.payload["session_id"]

    g = browser_tools.browser_goto(
        {"session_id": sid, "url": "https://example.com/"}, ctx
    )
    assert g.ok
    snap = browser_tools.browser_snapshot({"session_id": sid}, ctx)
    assert snap.ok
    assert snap.payload["ref_count"] >= 1
    refs_line = [
        line
        for line in snap.payload["snapshot"].splitlines()
        if "button" in line and "ref=" in line
    ]
    assert refs_line
    ref = refs_line[0].split("ref=")[1].rstrip("]")
    c = browser_tools.browser_click({"session_id": sid, "ref": ref}, ctx)
    assert c.ok
    stale = browser_tools.browser_click({"session_id": sid, "ref": ref}, ctx)
    assert stale.ok is False
    assert stale.error_reason == "stale_ref"

    closed = browser_tools.browser_session_close({"session_id": sid}, ctx)
    assert closed.ok


def test_builtin_session_limit() -> None:
    _mgr()
    ctx = _ctx()
    assert browser_tools.browser_session_open({}, ctx).ok
    assert browser_tools.browser_session_open({}, ctx).ok
    third = browser_tools.browser_session_open({}, ctx)
    assert third.ok is False
    assert third.error_reason == "session_limit"


def test_builtin_missing_args() -> None:
    _mgr()
    ctx = _ctx()
    assert browser_tools.browser_goto({}, ctx).error_reason == "missing_session_id"
    assert (
        browser_tools.browser_goto(
            {"session_id": "x", "url": "ftp://nope"}, ctx
        ).error_reason
        == "invalid_url"
    )


def test_screenshot_not_implemented() -> None:
    r = browser_tools.browser_screenshot({}, _ctx())
    assert r.ok is False
    assert r.error_reason == "not_implemented"


def test_snapshot_truncation_via_manager() -> None:
    def launcher() -> tuple[Any, Any, Any, Any]:
        page = _FakePage()
        page.aria_yaml = "\n".join(
            f'- text "n{"x" * 80}"' for _ in range(100)
        )
        pw = _FakePlaywright()
        browser = _FakeBrowser(page)
        context = browser.new_context()
        return pw, browser, context, page

    mgr = BrowserSessionManager(launcher=launcher)
    set_browser_session_manager(mgr)
    sid = mgr.open(moment_id="m")
    snap = mgr.snapshot(sid, max_chars=500)
    assert snap["truncated"] is True
    assert len(snap["snapshot"]) <= 500 + 40


# ---------------------------------------------------------------------------
# Dual worker paths + supervisor close_all
# ---------------------------------------------------------------------------


def test_close_for_moment_helper_closes_bound_sessions() -> None:
    from elyra.presence.worker import PresenceWorker

    mgr = _mgr()
    sid = mgr.open(moment_id="mom_fin")
    other = mgr.open(moment_id="other")
    assert mgr.session_count == 2

    PresenceWorker._close_browser_sessions_for_moment("mom_fin")
    assert mgr.session_count == 1
    assert mgr.get(other).session_id == other
    assert mgr.close(sid) is False

    PresenceWorker._close_browser_sessions_for_moment("other")
    assert mgr.session_count == 0


def test_fail_in_flight_closes_browser(tmp_path: Any) -> None:
    from elyra.presence.worker import PresenceWorker

    mgr = _mgr()
    mgr.open(moment_id="mom_err")
    assert mgr.session_count == 1

    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()

    worker = object.__new__(PresenceWorker)
    worker._lock = __import__("threading").RLock()
    worker._moments = MagicMock()
    worker._moments.get_moment.return_value = {"ended_at": None}
    worker._queue = MagicMock()
    worker._queue.status.return_value = "claimed"
    worker._hop_count = 0
    worker._timers = MagicMock()
    worker._timers.list_waits.return_value = []
    worker._flush_interjects_as_wakes_unlocked = MagicMock()
    worker._phase_from_pending_waits_unlocked = MagicMock(return_value="idle")
    worker._busy = True
    worker._active_moment_id = "mom_err"
    worker._worker_error = None
    worker._phase = "in_moment"

    wake = MagicMock()
    wake.id = "w1"
    worker._fail_in_flight(wake, "mom_err", RuntimeError("boom"))
    assert mgr.session_count == 0
    worker._moments.close_moment.assert_called()


def test_finalize_moment_invokes_close_for_moment(tmp_path: Any) -> None:
    from elyra.loop.continuous_policy import ContinuousRuntimeState
    from elyra.loop.doloop import DoLoopResult
    from elyra.moment.types import STOP_REASONS
    from elyra.presence.worker import PresenceWorker

    mgr = _mgr()
    mgr.open(moment_id="mom_ok")

    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()

    worker = object.__new__(PresenceWorker)
    worker._lock = __import__("threading").RLock()
    worker._moments = MagicMock()
    worker._queue = MagicMock()
    worker._hop_count = 0
    worker._continue_injects = 0
    worker._last_tool = None
    worker._timers = MagicMock()
    worker._timers.list_waits.return_value = []
    worker._flush_interjects_as_wakes_unlocked = MagicMock()
    worker._ensure_wait_armed_unlocked = MagicMock()
    worker._last_tool_from_tape = MagicMock(return_value=None)
    worker._maybe_enqueue_moment_continue_unlocked = MagicMock()
    worker._phase_from_pending_waits_unlocked = MagicMock(return_value="idle")
    worker._busy = True
    worker._active_moment_id = "mom_ok"
    worker._phase = "in_moment"
    worker._worker_error = None
    worker._continuous = ContinuousRuntimeState(enabled=False, streak=0)
    # Encode/traversal fields optional after defensive teardown, but set for realism.
    worker._encode_epoch = 0
    worker._encode_worker = None
    worker._embedder = None
    worker._embedder_open_lock = __import__("threading").Lock()
    worker._traversal = MagicMock()

    wake = MagicMock()
    wake.id = "wake_ok"
    wake.kind = "task_ready"
    stop = next(iter(STOP_REASONS))
    result = DoLoopResult(stop_reason=stop, hop_count=2)

    PresenceWorker._finalize_moment(
        worker, wake, "mom_ok", result, skills_used=[]
    )
    assert mgr.session_count == 0
    worker._moments.close_moment.assert_called()
    worker._maybe_enqueue_moment_continue_unlocked.assert_called()


def test_worker_run_finally_closes_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """Issue 2: worker run() finally closes sessions on the owner thread."""
    from elyra.presence.worker import PresenceWorker

    mgr = _mgr()
    mgr.open(moment_id="leftover")
    assert mgr.session_count == 1

    worker = object.__new__(PresenceWorker)
    worker._stop = __import__("threading").Event()
    worker._stop.set()  # exit loop immediately
    worker._poll = 0.01
    worker._startup_recover = MagicMock()
    worker._started = False
    # run() finally always tears down encode path — minimal fields for bare worker.
    worker._encode_epoch = 0
    worker._encode_worker = None
    worker._encode_owner = "none"
    worker._embedder = None
    worker._embedder_open_lock = __import__("threading").Lock()
    # Short-circuit body of run() before heavy memory ensure when stop already set:
    # still need _ensure_memory_store not to blow if called — mock it.
    worker._ensure_memory_store = MagicMock()  # type: ignore[method-assign]
    worker._start_encode_worker_if_needed = MagicMock()  # type: ignore[method-assign]
    worker._maybe_restart_encode_worker = MagicMock()  # type: ignore[method-assign]
    worker._claim_and_open = MagicMock(return_value=None)  # type: ignore[method-assign]
    worker._lock = __import__("threading").RLock()
    worker._fire_due_unlocked = MagicMock()  # type: ignore[method-assign]
    worker._idle_memory_ladder = MagicMock()  # type: ignore[method-assign]
    worker._idle_memory_encode = MagicMock()  # type: ignore[method-assign]
    worker._gap_drain_if_needed = MagicMock()  # type: ignore[method-assign]
    worker._idle_memory_joint_repair = MagicMock()  # type: ignore[method-assign]
    worker._idle_memory_optimize = MagicMock()  # type: ignore[method-assign]
    worker._idle_traversal_ttl = MagicMock()  # type: ignore[method-assign]

    PresenceWorker.run(worker)
    assert mgr.session_count == 0


def test_supervisor_shutdown_calls_close_all(monkeypatch: pytest.MonkeyPatch) -> None:
    from elyra.runtime.supervisor import ElyraSupervisor

    mgr = _mgr()
    mgr.open(moment_id="s1")
    assert mgr.session_count == 1

    closed: list[int] = []
    real_close_all = mgr.close_all

    def spy_close_all(*, force: bool = True) -> int:
        n = real_close_all(force=force)
        closed.append(n)
        return n

    monkeypatch.setattr(mgr, "close_all", spy_close_all)

    sup = object.__new__(ElyraSupervisor)
    sup._stop = __import__("threading").Event()
    sup._sandbox_stop = __import__("threading").Event()
    sup._gate = MagicMock()
    sup._credits_poller = None
    sup.provider_runtime = None
    sup._worker_thread = None
    sup._sandbox_warm_thread = None
    sup._sandbox = None
    sup._api_server = None
    sup._api_thread = None
    sup._instrument_reaper = None

    import elyra.runtime.supervisor as sup_mod

    monkeypatch.setattr(sup_mod, "clear_sandbox_lifecycle", lambda: None)
    monkeypatch.setattr(
        "elyra.tools.browser_sessions.get_browser_session_manager",
        lambda: mgr,
    )

    ElyraSupervisor.shutdown(sup)
    assert closed == [1]
    assert mgr.session_count == 0


def test_get_browser_session_manager_singleton() -> None:
    a = get_browser_session_manager()
    b = get_browser_session_manager()
    assert a is b


def test_bundled_packages_discoverable() -> None:
    from elyra.tools.registry import ToolRegistry

    reg = ToolRegistry()
    names = {
        "browser_session_open",
        "browser_session_close",
        "browser_goto",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_fill",
        "browser_get_text",
        "browser_wait",
    }
    for n in names:
        pkg = reg.get(n)
        assert pkg is not None, n
        assert pkg.source == "bundled"
        assert pkg.handler is not None


def test_browse_skill_exists() -> None:
    from pathlib import Path

    skill = Path("skills/bundled/browse/SKILL.md")
    assert skill.is_file()
    text = skill.read_text(encoding="utf-8")
    assert "snapshot" in text.lower()
    assert "browser_session_open" in text
    assert "stale" in text.lower()
    assert "browser_launch_failed" in text


def test_tool_md_documents_launch_failed_taxonomy() -> None:
    from pathlib import Path

    tool_md = Path("tools/bundled/browser_session_open/TOOL.md")
    assert tool_md.is_file()
    text = tool_md.read_text(encoding="utf-8")
    assert "browser_launch_failed" in text
    assert "browser_unavailable" in text
    assert "chromium_unavailable" in text


# ---------------------------------------------------------------------------
# BrowserThread: owner_ident, pollution isolation, lock not across launch
# ---------------------------------------------------------------------------


def test_session_owner_ident_is_browser_thread() -> None:
    launch_idents: list[int] = []

    def launcher() -> tuple[Any, Any, Any, Any]:
        launch_idents.append(threading.get_ident())
        return _fake_launcher()()

    mgr = BrowserSessionManager(launcher=launcher)
    set_browser_session_manager(mgr)
    sid = mgr.open(moment_id="m")
    session = mgr.get(sid)
    assert session.owner_ident == mgr.browser_thread.ident
    assert launch_idents
    assert launch_idents[0] == mgr.browser_thread.ident
    # Caller is not the browser thread
    assert session.owner_ident != threading.get_ident()
    mgr.close(sid)


def test_open_succeeds_when_caller_has_running_asyncio_loop() -> None:
    """KD13: caller-thread loop pollution must not break launch (BrowserThread)."""
    launch_idents: list[int] = []
    launch_saw_running_loop: list[bool] = []

    def launcher() -> tuple[Any, Any, Any, Any]:
        launch_idents.append(threading.get_ident())
        saw = False
        try:
            loop = asyncio.get_running_loop()
            saw = loop.is_running()
        except RuntimeError:
            saw = False
        launch_saw_running_loop.append(saw)
        # Simulate Playwright Sync refusal if called on a polluted thread.
        if saw:
            raise RuntimeError(
                "Playwright Sync API inside the asyncio loop"
            )
        return _fake_launcher()()

    mgr = BrowserSessionManager(launcher=launcher)
    set_browser_session_manager(mgr)

    result: dict[str, Any] = {}
    loop = asyncio.new_event_loop()

    def call_open_while_loop_running() -> None:
        try:
            # This runs on the loop's thread *while* the loop is running.
            result["sid"] = mgr.open(moment_id="polluted")
        except Exception as exc:  # noqa: BLE001
            result["err"] = exc
        finally:
            loop.stop()

    loop.call_soon(call_open_while_loop_running)
    loop.run_forever()
    loop.close()

    assert "err" not in result, result.get("err")
    assert result.get("sid", "").startswith("bs_")
    assert launch_idents == [mgr.browser_thread.ident]
    assert launch_saw_running_loop == [False]


def test_page_ops_run_on_browser_thread() -> None:
    op_idents: list[int] = []

    class _TrackingPage(_FakePage):
        def goto(self, url: str, wait_until: str = "load", timeout: int = 0) -> Any:
            op_idents.append(threading.get_ident())
            return super().goto(url, wait_until=wait_until, timeout=timeout)

        def aria_snapshot(self) -> str:
            op_idents.append(threading.get_ident())
            return super().aria_snapshot()

    def launcher() -> tuple[Any, Any, Any, Any]:
        page = _TrackingPage()
        pw = _FakePlaywright()
        browser = _FakeBrowser(page)
        context = browser.new_context()
        return pw, browser, context, page

    mgr = BrowserSessionManager(launcher=launcher)
    set_browser_session_manager(mgr)
    sid = mgr.open(moment_id="m")
    mgr.goto(sid, "https://example.com/")
    mgr.snapshot(sid)
    bt = mgr.browser_thread.ident
    assert op_idents
    assert all(i == bt for i in op_idents)
    mgr.close(sid)


def test_open_does_not_hold_lock_across_launch() -> None:
    """Registry RLock must not cover the long launcher (deadlock / stall risk)."""
    entered = threading.Event()
    release = threading.Event()

    def slow_launcher() -> tuple[Any, Any, Any, Any]:
        entered.set()
        if not release.wait(timeout=3.0):
            raise TimeoutError("release not signaled")
        return _fake_launcher()()

    mgr = BrowserSessionManager(launcher=slow_launcher)
    set_browser_session_manager(mgr)

    errors: list[BaseException] = []

    def open_bg() -> None:
        try:
            mgr.open(moment_id="slow")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=open_bg, name="open-bg")
    t.start()
    assert entered.wait(timeout=2.0)
    # While launch is in flight, registry ops must not block on the same lock.
    t0 = time.monotonic()
    assert mgr.session_count == 0
    assert time.monotonic() - t0 < 0.5
    release.set()
    t.join(timeout=3.0)
    assert not t.is_alive()
    assert not errors
    assert mgr.session_count == 1
    mgr.close_all()


def test_pending_opens_count_toward_session_limit() -> None:
    """Concurrent opens cannot exceed MAX_SESSIONS via launch-outside-lock.

    BrowserThread serializes launch, but pending slots are reserved under the
    registry lock *before* the hop — a third open must fail while two are in flight.
    """
    release = threading.Event()
    first_in_launch = threading.Event()

    def gated_launcher() -> tuple[Any, Any, Any, Any]:
        first_in_launch.set()
        release.wait(timeout=3.0)
        return _fake_launcher()()

    mgr = BrowserSessionManager(launcher=gated_launcher)
    set_browser_session_manager(mgr)

    results: list[Any] = []
    lock = threading.Lock()

    def try_open() -> None:
        try:
            sid = mgr.open(moment_id="c")
            with lock:
                results.append(sid)
        except Exception as exc:  # noqa: BLE001
            with lock:
                results.append(exc)

    t1 = threading.Thread(target=try_open)
    t2 = threading.Thread(target=try_open)
    t1.start()
    assert first_in_launch.wait(timeout=2.0)
    t2.start()
    # Wait until second open has reserved its pending slot (queued on BrowserThread).
    deadline = time.time() + 2.0
    while time.time() < deadline:
        with mgr._lock:  # noqa: SLF001 — test inspects reservation
            if mgr._pending_opens >= 2:  # noqa: SLF001
                break
        time.sleep(0.01)
    else:
        release.set()
        t1.join(timeout=1)
        t2.join(timeout=1)
        pytest.fail("second open never reserved pending slot")

    with pytest.raises(SessionLimitError) as ei:
        mgr.open(moment_id="third")
    assert ei.value.reason == "session_limit"
    release.set()
    t1.join(timeout=3.0)
    t2.join(timeout=3.0)
    sids = [r for r in results if isinstance(r, str)]
    assert len(sids) == 2
    mgr.close_all()


def test_teardown_runs_on_browser_thread() -> None:
    close_idents: list[int] = []

    class _TrackCtx(_FakeContext):
        def close(self) -> None:
            close_idents.append(threading.get_ident())
            super().close()

    def launcher() -> tuple[Any, Any, Any, Any]:
        page = _FakePage()
        pw = _FakePlaywright()
        browser = _FakeBrowser(page)
        context = _TrackCtx(page)
        return pw, browser, context, page

    mgr = BrowserSessionManager(launcher=launcher)
    set_browser_session_manager(mgr)
    sid = mgr.open(moment_id="m")
    assert mgr.close(sid) is True
    assert close_idents
    assert close_idents[0] == mgr.browser_thread.ident


def test_open_aborts_launch_when_registration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue 2: successful launch + failed register must teardown stack."""
    import elyra.tools.browser_sessions as bs

    closed = {"context": 0, "browser": 0, "pw": 0}

    class _TrackCtx(_FakeContext):
        def close(self) -> None:
            closed["context"] += 1
            super().close()

    class _TrackBrowser(_FakeBrowser):
        def close(self) -> None:
            closed["browser"] += 1
            super().close()

        def new_context(self, **kwargs: Any) -> _TrackCtx:
            return _TrackCtx(self._page)

    class _TrackPW(_FakePlaywright):
        def stop(self) -> None:
            closed["pw"] += 1
            super().stop()

    def launcher() -> tuple[Any, Any, Any, Any]:
        page = _FakePage()
        pw = _TrackPW()
        browser = _TrackBrowser(page)
        context = browser.new_context()
        return pw, browser, context, page

    def boom_uuid() -> Any:
        raise RuntimeError("uuid boom")

    monkeypatch.setattr(bs.uuid, "uuid4", boom_uuid)
    mgr = BrowserSessionManager(launcher=launcher)
    set_browser_session_manager(mgr)
    with pytest.raises(RuntimeError, match="uuid boom"):
        mgr.open(moment_id="m")
    assert closed["context"] == 1
    assert closed["browser"] == 1
    assert closed["pw"] == 1
    assert mgr.session_count == 0
    with mgr._lock:  # noqa: SLF001
        assert mgr._pending_opens == 0  # noqa: SLF001


def test_open_timeout_aborts_late_success_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Issue 1: hop timeout must not leak Chromium when launch later succeeds."""
    import elyra.tools.browser_sessions as bs

    monkeypatch.setattr(bs, "BROWSER_THREAD_OP_TIMEOUT_S", 0.05)

    release = threading.Event()
    closed = {"context": 0, "browser": 0, "pw": 0}
    launched_ok = threading.Event()

    class _TrackCtx(_FakeContext):
        def close(self) -> None:
            closed["context"] += 1
            super().close()

    class _TrackBrowser(_FakeBrowser):
        def close(self) -> None:
            closed["browser"] += 1
            super().close()

        def new_context(self, **kwargs: Any) -> _TrackCtx:
            return _TrackCtx(self._page)

    class _TrackPW(_FakePlaywright):
        def stop(self) -> None:
            closed["pw"] += 1
            super().stop()

    def slow_launcher() -> tuple[Any, Any, Any, Any]:
        if not release.wait(timeout=3.0):
            raise TimeoutError("test release not signaled")
        page = _FakePage()
        pw = _TrackPW()
        browser = _TrackBrowser(page)
        context = browser.new_context()
        launched_ok.set()
        return pw, browser, context, page

    mgr = BrowserSessionManager(launcher=slow_launcher)
    set_browser_session_manager(mgr)

    with pytest.raises(BrowserLaunchFailedError) as ei:
        mgr.open(moment_id="slow")
    assert ei.value.reason == "browser_launch_failed"
    assert "timed out" in str(ei.value).lower()
    # Slot still reserved until late callback finishes.
    with mgr._lock:  # noqa: SLF001
        assert mgr._pending_opens == 1  # noqa: SLF001
    assert mgr.session_count == 0

    release.set()
    assert launched_ok.wait(timeout=2.0)
    # Late done-callback aborts stack and releases pending.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        with mgr._lock:  # noqa: SLF001
            pending = mgr._pending_opens  # noqa: SLF001
        if pending == 0 and closed["pw"] >= 1:
            break
        time.sleep(0.01)
    assert closed["context"] >= 1
    assert closed["browser"] >= 1
    assert closed["pw"] >= 1
    with mgr._lock:  # noqa: SLF001
        assert mgr._pending_opens == 0  # noqa: SLF001
    assert mgr.session_count == 0
