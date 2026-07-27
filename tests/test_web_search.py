"""Tests for web_search host builtin (PR3).

Mocked DDGS only — no network. Covers present/empty/unavailable/rate/timeout
and arg validation. Process-wide cooldown state is reset between tests.
"""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.tools.builtin import search as search_mod
from elyra.tools.builtin.search import web_search
from elyra.tools.policy import resolve_bundled_tools_root
from elyra.tools.registry import ToolRegistry
from elyra.tools.types import ToolContext


@pytest.fixture(autouse=True)
def _reset_search_state() -> None:
    search_mod._reset_cooldown_state_for_tests()
    yield
    search_mod._reset_cooldown_state_for_tests()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def ctx(home: Path) -> ToolContext:
    return ToolContext(
        paths=resolve_paths(home),
        moment_id="moment-search-1",
        user_id="operator",
    )


def _fake_ddgs_module(
    monkeypatch: pytest.MonkeyPatch,
    *,
    results: list[dict[str, Any]] | None = None,
    side_effect: BaseException | None = None,
    method_name: str = "text",
) -> MagicMock:
    """Install a fake ``ddgs`` package with a controllable DDGS class."""
    results = list(results) if results is not None else []

    client = MagicMock()
    method = MagicMock()
    if side_effect is not None:
        method.side_effect = side_effect
    else:
        method.return_value = results
    setattr(client, method_name, method)
    # other type methods also exist for getattr
    for name in ("text", "news", "images", "videos"):
        if name != method_name:
            setattr(client, name, MagicMock(return_value=[]))

    ddgs_cls = MagicMock(return_value=client)
    fake = SimpleNamespace(DDGS=ddgs_cls)
    monkeypatch.setitem(__import__("sys").modules, "ddgs", fake)
    # find_spec must report available
    real_find_spec = __import__("importlib.util", fromlist=["find_spec"]).find_spec

    def _find_spec(name: str, *a: Any, **k: Any):  # noqa: ANN401
        if name == "ddgs":
            return SimpleNamespace(name="ddgs")
        return real_find_spec(name, *a, **k)

    monkeypatch.setattr(search_mod.importlib.util, "find_spec", _find_spec)
    return client


# ---------------------------------------------------------------------------
# Unavailable / install hint
# ---------------------------------------------------------------------------


def test_search_unavailable_without_ddgs(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    monkeypatch.setattr(
        search_mod.importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "ddgs" else None,
    )
    result = web_search({"query": "python typing"}, ctx)
    assert result.ok is False
    assert result.error_reason == "search_unavailable"
    assert result.payload.get("ok") is False
    assert result.payload.get("results") == []
    assert "search" in (result.payload.get("hint") or "")
    assert "pip install" in (result.payload.get("hint") or "")


# ---------------------------------------------------------------------------
# Happy path + normalization
# ---------------------------------------------------------------------------


def test_web_search_structured_results(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    client = _fake_ddgs_module(
        monkeypatch,
        results=[
            {
                "title": "Python docs",
                "href": "https://docs.python.org/3/",
                "body": "Official Python documentation",
            },
            {
                "title": "PEP 484",
                "href": "https://peps.python.org/pep-0484/",
                "body": "Type Hints",
            },
        ],
    )
    result = web_search({"query": "python typing", "max_results": 5}, ctx)
    assert result.ok is True
    assert result.error_reason is None
    assert result.payload["ok"] is True
    assert result.payload.get("warning") is None
    results = result.payload["results"]
    assert len(results) == 2
    assert results[0]["title"] == "Python docs"
    assert results[0]["url"] == "https://docs.python.org/3/"
    assert results[0]["snippet"] == "Official Python documentation"
    assert results[0]["source"] == "docs.python.org"
    assert "html" not in results[0]
    assert "body" not in results[0]
    assert "href" not in results[0]
    client.text.assert_called_once()
    call_kwargs = client.text.call_args
    assert call_kwargs[0][0] == "python typing"
    assert call_kwargs[1]["max_results"] == 5


def test_max_results_hard_cap_20(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    client = _fake_ddgs_module(
        monkeypatch,
        results=[
            {"title": f"R{i}", "href": f"https://example.com/{i}", "body": "x"}
            for i in range(25)
        ],
    )
    result = web_search({"query": "cap", "max_results": 100}, ctx)
    assert result.ok is True
    # Backend called with capped max_results
    assert client.text.call_args[1]["max_results"] == 20
    assert len(result.payload["results"]) == 20


def test_default_max_results_is_8(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    client = _fake_ddgs_module(monkeypatch, results=[])
    web_search({"query": "defaults"}, ctx)
    assert client.text.call_args[1]["max_results"] == 8


# ---------------------------------------------------------------------------
# Empty results
# ---------------------------------------------------------------------------


def test_empty_results_ok_with_warning(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    _fake_ddgs_module(monkeypatch, results=[])
    result = web_search({"query": "zzzz-no-hits-hopefully"}, ctx)
    assert result.ok is True
    assert result.payload["ok"] is True
    assert result.payload["results"] == []
    assert result.payload.get("warning") == "empty"
    assert result.error_reason is None


# ---------------------------------------------------------------------------
# Invalid args
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"query": ""},
        {"query": "   "},
        {"query": 123},
        {"query": "ok", "type": "podcasts"},
        {"query": "ok", "max_results": 0},
        {"query": "ok", "max_results": -3},
        {"query": "ok", "max_results": "many"},
        {"query": "ok", "max_results": True},
        {"query": "ok", "max_results": "--5"},
        {"query": "ok", "max_results": "1.5"},
        {"query": "ok", "region": 1},
    ],
)
def test_invalid_args(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext, args: dict
) -> None:
    _fake_ddgs_module(monkeypatch, results=[])
    result = web_search(args, ctx)
    assert result.ok is False
    assert result.error_reason == "invalid_args"


# ---------------------------------------------------------------------------
# Search types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stype", ["text", "news", "images", "videos"])
def test_search_types(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext, stype: str
) -> None:
    raw = {
        "text": {
            "title": "T",
            "href": "https://example.com/t",
            "body": "snippet",
        },
        "news": {
            "title": "N",
            "url": "https://news.example/n",
            "body": "lede",
            "source": "Example News",
            "date": "2026-01-01",
        },
        "images": {
            "title": "I",
            "url": "https://example.com/page",
            "image": "https://cdn.example/i.jpg",
            "source": "Bing",
        },
        "videos": {
            "title": "V",
            "content": "https://youtube.com/watch?v=1",
            "description": "clip",
            "publisher": "YouTube",
            "published": "2024-07-03T05:30:03.0000000",
        },
    }[stype]
    client = _fake_ddgs_module(monkeypatch, results=[raw], method_name=stype)
    result = web_search({"query": "q", "type": stype}, ctx)
    assert result.ok is True
    assert len(result.payload["results"]) == 1
    item = result.payload["results"][0]
    assert item["title"]
    assert item["url"]
    getattr(client, stype).assert_called_once()


# ---------------------------------------------------------------------------
# Backend errors: rate limit, generic unavailable, timeout
# ---------------------------------------------------------------------------


def test_rate_limited_from_backend(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    _fake_ddgs_module(
        monkeypatch,
        side_effect=RuntimeError("429 Too Many Requests: rate limit"),
    )
    result = web_search({"query": "busy"}, ctx)
    assert result.ok is False
    assert result.error_reason == "rate_limited"
    assert result.payload.get("results") == []


def test_backend_error_search_unavailable(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    _fake_ddgs_module(
        monkeypatch,
        side_effect=RuntimeError("connection reset by peer"),
    )
    result = web_search({"query": "flaky"}, ctx)
    assert result.ok is False
    assert result.error_reason == "search_unavailable"


def test_timeout_path(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    _fake_ddgs_module(monkeypatch, results=[{"title": "x", "href": "https://x"}])

    class _FakeFuture:
        def result(self, timeout: float | None = None) -> Any:
            raise concurrent.futures.TimeoutError()

    class _FakePool:
        def __init__(self, *a: Any, **k: Any) -> None:
            self.shutdown_calls: list[dict[str, Any]] = []

        def submit(self, *a: Any, **k: Any) -> _FakeFuture:
            return _FakeFuture()

        def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
            self.shutdown_calls.append(
                {"wait": wait, "cancel_futures": cancel_futures}
            )

    fake_pools: list[_FakePool] = []

    def _pool_factory(*a: Any, **k: Any) -> _FakePool:
        pool = _FakePool()
        fake_pools.append(pool)
        return pool

    monkeypatch.setattr(
        search_mod.concurrent.futures, "ThreadPoolExecutor", _pool_factory
    )
    result = web_search({"query": "slow"}, ctx)
    assert result.ok is False
    assert result.error_reason == "timeout"
    assert fake_pools
    assert fake_pools[0].shutdown_calls
    assert fake_pools[0].shutdown_calls[0]["wait"] is False


def test_timeout_returns_without_waiting_for_worker(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    """Regression: wall-clock timeout must not block on abandoned search thread.

    Uses a real ThreadPoolExecutor + slow worker; handler must return near the
    short timeout, not after the full sleep.
    """
    work_s = 2.0
    timeout_s = 0.15

    def _slow_search(**_kwargs: Any) -> list[Any]:
        time.sleep(work_s)
        return [{"title": "late", "href": "https://example.com/late", "body": "x"}]

    _fake_ddgs_module(monkeypatch, results=[])
    monkeypatch.setattr(search_mod, "_run_ddgs_search", _slow_search)
    monkeypatch.setattr(search_mod, "_SEARCH_TIMEOUT_S", timeout_s)

    t0 = time.monotonic()
    result = web_search({"query": "slow-real"}, ctx)
    elapsed = time.monotonic() - t0

    assert result.ok is False
    assert result.error_reason == "timeout"
    # Must return near timeout, not after full worker sleep (slack for CI).
    assert elapsed < work_s * 0.6, f"handler blocked ~{elapsed:.2f}s waiting on worker"
    assert elapsed < timeout_s + 1.0


def test_cooldown_after_consecutive_failures(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    _fake_ddgs_module(
        monkeypatch,
        side_effect=RuntimeError("backend down"),
    )
    for _ in range(search_mod._COOLDOWN_AFTER_FAILURES):
        r = web_search({"query": "fail"}, ctx)
        assert r.error_reason == "search_unavailable"

    # Next call short-circuits as rate_limited without hitting backend.
    r = web_search({"query": "fail"}, ctx)
    assert r.ok is False
    assert r.error_reason == "rate_limited"


def test_success_resets_failure_counter(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    """Fail twice (under threshold) → success clears counter → fail again
    must not arm cooldown until threshold is re-hit."""
    # Plan: fail, fail, succeed, fail, fail → still not cooldown; one more fail arms it.
    outcomes: list[str] = ["fail", "fail", "ok", "fail", "fail", "fail"]
    call_n = {"n": 0}

    def _side_effect(*_a: Any, **_k: Any) -> list[Any]:
        i = call_n["n"]
        call_n["n"] += 1
        if i >= len(outcomes) or outcomes[i] == "fail":
            raise RuntimeError("transient backend error")
        return [{"title": "ok", "href": "https://example.com/", "body": "x"}]

    client = _fake_ddgs_module(monkeypatch, results=[])
    client.text.side_effect = _side_effect

    assert web_search({"query": "a"}, ctx).error_reason == "search_unavailable"
    assert web_search({"query": "b"}, ctx).error_reason == "search_unavailable"
    ok = web_search({"query": "c"}, ctx)
    assert ok.ok is True
    # Two more failures after reset — still under threshold of 3.
    assert web_search({"query": "d"}, ctx).error_reason == "search_unavailable"
    assert web_search({"query": "e"}, ctx).error_reason == "search_unavailable"
    # Still not in cooldown — backend still reached.
    assert web_search({"query": "f"}, ctx).error_reason == "search_unavailable"
    cool = web_search({"query": "g"}, ctx)
    assert cool.error_reason == "rate_limited"
    # Backend was not invoked for the short-circuit call.
    assert call_n["n"] == 6  # a–f only; g short-circuited


def test_rate_limited_arms_cooldown_immediately(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    client = _fake_ddgs_module(
        monkeypatch,
        side_effect=RuntimeError("429 Too Many Requests"),
    )
    r1 = web_search({"query": "busy"}, ctx)
    assert r1.error_reason == "rate_limited"
    assert client.text.call_count == 1

    r2 = web_search({"query": "busy-again"}, ctx)
    assert r2.ok is False
    assert r2.error_reason == "rate_limited"
    # Short-circuited: no second backend call.
    assert client.text.call_count == 1


def test_generic_blocked_is_not_rate_limited(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    """Plain 'blocked' / 403 must not arm immediate rate-limit cooldown."""
    _fake_ddgs_module(
        monkeypatch,
        side_effect=RuntimeError("content blocked by policy"),
    )
    r = web_search({"query": "policy"}, ctx)
    assert r.ok is False
    assert r.error_reason == "search_unavailable"
    # Not in cooldown after a single non-rate failure.
    r2 = web_search({"query": "policy2"}, ctx)
    assert r2.error_reason == "search_unavailable"


def test_no_raw_html_keys_in_payload(
    monkeypatch: pytest.MonkeyPatch, ctx: ToolContext
) -> None:
    _fake_ddgs_module(
        monkeypatch,
        results=[
            {
                "title": "Page",
                "href": "https://example.com/",
                "body": "<b>not raw html dump</b>",
                "html": "<html>secret dump</html>",
                "raw_html": "<div/>",
            }
        ],
    )
    result = web_search({"query": "html"}, ctx)
    assert result.ok is True
    item = result.payload["results"][0]
    assert set(item.keys()) <= {"title", "url", "snippet", "source", "date"}
    assert "html" not in item
    assert "raw_html" not in item
    # snippet may contain markup from body text — that is fine; no HTML key dump


# ---------------------------------------------------------------------------
# Bundled package registration
# ---------------------------------------------------------------------------


def test_web_search_bundled_package_discovered(home: Path) -> None:
    registry = ToolRegistry(
        resolve_paths(home),
        bundled_root=resolve_bundled_tools_root(),
    )
    pkg = registry.get("web_search")
    assert pkg is not None
    assert pkg.source == "bundled"
    assert pkg.handler is not None
    assert pkg.meta.name == "web_search"


def test_web_search_via_registry_unavailable(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    monkeypatch.setattr(
        search_mod.importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "ddgs" else None,
    )
    registry = ToolRegistry(
        resolve_paths(home),
        bundled_root=resolve_bundled_tools_root(),
    )
    ctx = ToolContext(
        paths=resolve_paths(home),
        moment_id="m1",
        user_id="operator",
        registry=registry,
    )
    result = registry.execute("web_search", {"query": "x"}, ctx)
    assert result.ok is False
    assert result.error_reason == "search_unavailable"
