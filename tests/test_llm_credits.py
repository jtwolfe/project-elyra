"""Tests for SuperGrok billing credits parse + fail-soft fetch + poller."""

from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from elyra.llm.auth import CredentialResolution
from elyra.llm.credits import (
    STATUS_AUTH_FAILED,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNSUPPORTED,
    CreditsSnapshot,
    canonical_period_id,
    coerce_product_usage,
    fetch_billing,
    parse_billing_payload,
    snapshot_is_ok,
)
from elyra.llm.usage import TokenUsage, UsageMeter
from elyra.runtime.credits_poller import CreditsPoller
from elyra.runtime.provider_runtime import ProviderRuntime
from elyra.settings import UsageSettings

FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "billing_credits_redacted.json"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)


def _settings(**kwargs: object) -> UsageSettings:
    base: dict[str, Any] = dict(
        enabled=True,
        weekly_allowed_tokens=5_000_000,
        credits_poll_enabled=True,
        credits_poll_interval_s=60.0,
        credits_stale_after_s=3600.0,
        credits_base_url="https://cli-chat-proxy.grok.com",
    )
    base.update(kwargs)
    return UsageSettings(**base)  # type: ignore[arg-type]


def _meter(tmp_path: Path, settings: UsageSettings | None = None, **kwargs) -> UsageMeter:
    return UsageMeter.load(tmp_path, settings or _settings(), **kwargs)


def _ok_resolution(token: str = "test-bearer-token") -> CredentialResolution:
    return CredentialResolution(
        ok=True,
        source="grok_build",
        token=token,
        detail=None,
        expires_at=None,
        email=None,
        api_key_configured=False,
    )


def _fail_resolution(detail: str = "missing_token") -> CredentialResolution:
    return CredentialResolution(
        ok=False,
        source="grok_build",
        token=None,
        detail=detail,
        expires_at=None,
        email=None,
        api_key_configured=False,
    )


class _FakeHTTPResponse:
    def __init__(self, body: bytes | str, status: int = 200) -> None:
        if isinstance(body, str):
            body = body.encode("utf-8")
        self._body = body
        self.status = status
        self._fp = io.BytesIO(body)

    def read(self) -> bytes:
        return self._fp.read()

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _urlopen_factory(
    *,
    status: int = 200,
    body: str | bytes | None = None,
    raise_exc: BaseException | None = None,
    delay_s: float = 0.0,
    capture: list | None = None,
) -> Callable[..., Any]:
    fixture_body = FIXTURE_PATH.read_text(encoding="utf-8")
    payload = body if body is not None else fixture_body

    def _open(request: Any, timeout: float = 5.0) -> Any:
        if capture is not None:
            capture.append(
                {
                    "url": getattr(request, "full_url", None)
                    or getattr(request, "get_full_url", lambda: None)(),
                    "timeout": timeout,
                    "headers": dict(getattr(request, "headers", {}) or {}),
                }
            )
        if delay_s > 0:
            time.sleep(delay_s)
        if raise_exc is not None:
            raise raise_exc
        if status != 200:
            raise urllib.error.HTTPError(
                url="https://cli-chat-proxy.grok.com/v1/billing?format=credits",
                code=status,
                msg="err",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b""),
            )
        return _FakeHTTPResponse(payload, status=200)

    return _open


# ---------------------------------------------------------------------------
# parse_billing_payload / fixture
# ---------------------------------------------------------------------------


def test_parse_fixture_to_snapshot():
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    snap = parse_billing_payload(data, fetched_at="2026-07-24T14:00:00Z")
    assert snapshot_is_ok(snap)
    assert snap.status == STATUS_OK
    assert snap.ok is True
    assert snap.credit_usage_percent == 22.0
    assert snap.period_start == "2026-07-23T20:46:53.140891+00:00"
    assert snap.period_end == "2026-07-30T20:46:53.140891+00:00"
    assert snap.period_type == "USAGE_PERIOD_TYPE_WEEKLY"
    assert snap.is_unified is True
    assert snap.period_id == canonical_period_id(
        snap.period_start, snap.period_end  # type: ignore[arg-type]
    )
    assert snap.product_usage is not None
    assert snap.product_usage["GrokBuild"] == 18.0
    assert snap.product_usage["GrokChat"] == 3.0
    assert snap.product_usage["Api"] == 1.0
    assert snap.fetched_at == "2026-07-24T14:00:00Z"


def test_coerce_product_usage_list_and_dict():
    assert coerce_product_usage(
        [{"product": "Api", "usagePercent": 2.5}, {"product": "X", "usagePercent": 1}]
    ) == {"Api": 2.5, "X": 1.0}
    assert coerce_product_usage({"Api": 1.0, "GrokChat": 2}) == {
        "Api": 1.0,
        "GrokChat": 2.0,
    }
    assert coerce_product_usage("nope") is None
    assert coerce_product_usage([]) is None


def test_parse_missing_config_error():
    snap = parse_billing_payload({"no": "config"}, fetched_at="t")
    assert snap.status == STATUS_ERROR
    assert snap.ok is False
    assert snap.detail == "missing_config"


def test_parse_missing_percent_error_keeps_period():
    data = {
        "config": {
            "currentPeriod": {
                "type": "USAGE_PERIOD_TYPE_WEEKLY",
                "start": "2026-07-21T00:00:00Z",
                "end": "2026-07-28T00:00:00Z",
            }
        }
    }
    snap = parse_billing_payload(data, fetched_at="t")
    assert snap.status == STATUS_ERROR
    assert snap.period_start == "2026-07-21T00:00:00Z"
    assert snap.credit_usage_percent is None


def test_parse_billing_period_fallback_fields():
    data = {
        "config": {
            "creditUsagePercent": 10.0,
            "billingPeriodStart": "2026-01-01T00:00:00Z",
            "billingPeriodEnd": "2026-01-08T00:00:00Z",
            "isUnifiedBillingUser": True,
        }
    }
    snap = parse_billing_payload(data, fetched_at="t")
    assert snap.status == STATUS_OK
    assert snap.period_start == "2026-01-01T00:00:00Z"
    assert snap.period_end == "2026-01-08T00:00:00Z"


# ---------------------------------------------------------------------------
# fetch_billing fail-soft
# ---------------------------------------------------------------------------


def test_fetch_billing_200_ok():
    capture: list = []
    snap = fetch_billing(
        "https://cli-chat-proxy.grok.com",
        "secret-token",
        5.0,
        fetched_at="2026-07-24T14:00:00Z",
        urlopen=_urlopen_factory(capture=capture),
    )
    assert snap.status == STATUS_OK
    assert snap.credit_usage_percent == 22.0
    assert capture
    assert capture[0]["url"].endswith("/v1/billing?format=credits")
    assert capture[0]["timeout"] == 5.0
    # Bearer present but never asserted as logged.
    auth = capture[0]["headers"].get("Authorization") or capture[0]["headers"].get(
        "authorization"
    )
    assert auth == "Bearer secret-token"


def test_fetch_billing_401_auth_failed():
    snap = fetch_billing(
        "https://cli-chat-proxy.grok.com",
        "tok",
        urlopen=_urlopen_factory(status=401),
    )
    assert snap.status == STATUS_AUTH_FAILED
    assert snap.ok is False
    assert snap.detail == "http_401"


def test_fetch_billing_403_auth_failed():
    snap = fetch_billing(
        "https://cli-chat-proxy.grok.com",
        "tok",
        urlopen=_urlopen_factory(status=403),
    )
    assert snap.status == STATUS_AUTH_FAILED
    assert snap.detail == "http_403"


def test_fetch_billing_500_error():
    snap = fetch_billing(
        "https://cli-chat-proxy.grok.com",
        "tok",
        urlopen=_urlopen_factory(status=500),
    )
    assert snap.status == STATUS_ERROR
    assert snap.detail == "http_500"


def test_fetch_billing_network_error():
    snap = fetch_billing(
        "https://cli-chat-proxy.grok.com",
        "tok",
        urlopen=_urlopen_factory(
            raise_exc=urllib.error.URLError("connection refused")
        ),
    )
    assert snap.status == STATUS_ERROR
    assert snap.detail is not None
    assert "network" in snap.detail


def test_fetch_billing_invalid_json_error():
    snap = fetch_billing(
        "https://cli-chat-proxy.grok.com",
        "tok",
        urlopen=_urlopen_factory(body="not-json{"),
    )
    assert snap.status == STATUS_ERROR
    assert snap.detail == "invalid_json"


def test_fetch_billing_api_key_401_unsupported():
    snap = fetch_billing(
        "https://cli-chat-proxy.grok.com",
        "sk-test",
        credential_source="api_key",
        urlopen=_urlopen_factory(status=401),
    )
    assert snap.status == STATUS_UNSUPPORTED
    assert snap.detail == "http_401"


def test_fetch_billing_api_key_404_unsupported():
    snap = fetch_billing(
        "https://cli-chat-proxy.grok.com",
        "sk-test",
        credential_source="api_key",
        urlopen=_urlopen_factory(status=404),
    )
    assert snap.status == STATUS_UNSUPPORTED
    assert snap.detail == "http_404"


def test_fetch_billing_grok_build_404_is_error_not_unsupported():
    snap = fetch_billing(
        "https://cli-chat-proxy.grok.com",
        "tok",
        credential_source="grok_build",
        urlopen=_urlopen_factory(status=404),
    )
    assert snap.status == STATUS_ERROR
    assert snap.detail == "http_404"


def test_fetch_billing_missing_bearer():
    snap = fetch_billing("https://cli-chat-proxy.grok.com", "  ")
    assert snap.status == STATUS_ERROR
    assert snap.detail == "missing_bearer"


# ---------------------------------------------------------------------------
# apply + keep last good on error / first adoption
# ---------------------------------------------------------------------------


def test_apply_ok_then_error_keeps_last_good_percent(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, _settings(account_hard_stop_percent=95.0), clock=clock)
    m.record(TokenUsage(total_tokens=1000))
    m.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=22.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
            status=STATUS_OK,
            ok=True,
        )
    )
    assert m.snapshot().credit_usage_percent == 22.0
    assert m.snapshot().period_authority == "supergrok"
    s_before = m.snapshot().week_used_tokens

    # Error poll — keep last good A / S.
    m.apply_credits_snapshot(
        CreditsSnapshot(
            status=STATUS_ERROR,
            ok=False,
            detail="http_500",
            fetched_at="2026-07-24T14:05:00Z",
        )
    )
    snap = m.snapshot()
    assert snap.credit_usage_percent == 22.0
    assert snap.week_used_tokens == s_before
    assert snap.period_authority == "supergrok"


def test_first_adoption_via_parsed_fixture(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, clock=clock)
    m.record(TokenUsage(total_tokens=1_200_000))
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    snap = parse_billing_payload(data, fetched_at="2026-07-24T14:00:00Z")
    m.apply_credits_snapshot(snap)
    out = m.snapshot()
    assert out.period_authority == "supergrok"
    assert out.week_used_tokens == 1_200_000
    assert out.credit_usage_percent == 22.0


# ---------------------------------------------------------------------------
# CreditsPoller
# ---------------------------------------------------------------------------


def test_poller_noop_when_usage_disabled(tmp_path: Path):
    m = _meter(tmp_path, _settings(enabled=False))
    calls: list[int] = []

    def fetch(*_a, **_k):
        calls.append(1)
        return CreditsSnapshot(status=STATUS_OK, ok=True, credit_usage_percent=1.0)

    poller = CreditsPoller(
        meter=m,
        usage_settings=_settings(enabled=False),
        data_dir=tmp_path,
        credential_source="grok_build",
        first_delay_s=0.0,
        fetch_fn=fetch,
        resolve_fn=lambda **_k: _ok_resolution(),
        enabled=True,  # thread runs but settings.enabled=false → idle
    )
    poller.start()
    time.sleep(0.15)
    poller.stop()
    assert calls == []


def test_poller_noop_when_credits_poll_disabled(tmp_path: Path):
    m = _meter(tmp_path)
    calls: list[int] = []

    def fetch(*_a, **_k):
        calls.append(1)
        return CreditsSnapshot(status=STATUS_OK, ok=True, credit_usage_percent=1.0)

    poller = CreditsPoller(
        meter=m,
        usage_settings=_settings(credits_poll_enabled=False),
        data_dir=tmp_path,
        credential_source="grok_build",
        first_delay_s=0.0,
        fetch_fn=fetch,
        resolve_fn=lambda **_k: _ok_resolution(),
    )
    poller.start()
    time.sleep(0.15)
    poller.stop()
    assert calls == []


def test_poller_first_poll_soon_and_applies(tmp_path: Path):
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, clock=clock)
    m.record(TokenUsage(total_tokens=50_000))
    applied = threading.Event()

    def fetch(base_url, bearer, timeout, **kwargs):
        assert bearer == "test-bearer-token"
        assert "cli-chat-proxy" in base_url
        data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        snap = parse_billing_payload(data, fetched_at="2026-07-24T14:00:00Z")
        applied.set()
        return snap

    poller = CreditsPoller(
        meter=m,
        usage_settings=_settings(credits_poll_interval_s=3600.0),
        data_dir=tmp_path,
        credential_source="grok_build",
        first_delay_s=0.05,
        fetch_fn=fetch,
        resolve_fn=lambda **_k: _ok_resolution(),
    )
    poller.start()
    assert applied.wait(timeout=2.0), "first poll did not run soon after start"
    # Allow apply to finish.
    deadline = time.time() + 1.0
    while time.time() < deadline and m.snapshot().period_authority != "supergrok":
        time.sleep(0.02)
    poller.stop()
    snap = m.snapshot()
    assert snap.period_authority == "supergrok"
    assert snap.week_used_tokens == 50_000
    assert snap.credit_usage_percent == 22.0


def test_poller_api_key_unsupported_skips_further_http(tmp_path: Path):
    m = _meter(tmp_path)
    calls: list[int] = []

    def fetch(*_a, **kwargs):
        calls.append(1)
        return CreditsSnapshot(
            status=STATUS_UNSUPPORTED,
            ok=False,
            detail="http_401",
        )

    poller = CreditsPoller(
        meter=m,
        usage_settings=_settings(credits_poll_interval_s=30.0),
        data_dir=tmp_path,
        credential_source="api_key",
        first_delay_s=0.0,
        fetch_fn=fetch,
        resolve_fn=lambda **_k: CredentialResolution(
            ok=True,
            source="api_key",
            token="sk-x",
            detail=None,
            expires_at=None,
            email=None,
            api_key_configured=True,
        ),
    )
    poller.start()
    time.sleep(0.2)
    # Force another poll via request
    poller.request_poll()
    time.sleep(0.15)
    poller.stop()
    # First poll hits HTTP once; subsequent should short-circuit without fetch.
    assert len(calls) == 1


def test_poller_api_key_resolve_fail_does_not_terminalize(tmp_path: Path):
    """Resolve not-ok must not set terminal unsupported; repair can retry HTTP."""
    m = _meter(tmp_path)
    fetch_calls: list[int] = []
    resolve_n = {"n": 0}

    def resolve(**_k):
        resolve_n["n"] += 1
        if resolve_n["n"] == 1:
            return CredentialResolution(
                ok=False,
                source="api_key",
                token=None,
                detail="missing_api_key",
                expires_at=None,
                email=None,
                api_key_configured=False,
            )
        return CredentialResolution(
            ok=True,
            source="api_key",
            token="sk-repaired",
            detail=None,
            expires_at=None,
            email=None,
            api_key_configured=True,
        )

    def fetch(*_a, **_k):
        fetch_calls.append(1)
        return CreditsSnapshot(
            status=STATUS_OK,
            ok=True,
            credit_usage_percent=5.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
        )

    poller = CreditsPoller(
        meter=m,
        usage_settings=_settings(credits_poll_interval_s=30.0),
        data_dir=tmp_path,
        credential_source="api_key",
        first_delay_s=0.0,
        fetch_fn=fetch,
        resolve_fn=resolve,
    )
    poller.start()
    time.sleep(0.15)
    assert fetch_calls == []  # first attempt: resolve fail, no HTTP
    assert poller._api_key_unsupported is False  # noqa: SLF001 — intentional
    # Bypass debounce and force another poll attempt after "repair".
    with poller._meta_lock:  # noqa: SLF001
        poller._last_attempt_mono = 0.0
    poller.request_poll()
    deadline = time.time() + 2.0
    while time.time() < deadline and not fetch_calls:
        time.sleep(0.02)
    poller.stop()
    assert fetch_calls == [1], "second poll after resolve repair must call fetch"


def test_poller_auth_failed_does_not_raise(tmp_path: Path):
    m = _meter(tmp_path)
    poller = CreditsPoller(
        meter=m,
        usage_settings=_settings(),
        data_dir=tmp_path,
        credential_source="grok_build",
        first_delay_s=0.0,
        fetch_fn=lambda *a, **k: CreditsSnapshot(
            status=STATUS_AUTH_FAILED, ok=False, detail="http_401"
        ),
        resolve_fn=lambda **_k: _ok_resolution(),
    )
    poller.start()
    time.sleep(0.15)
    poller.stop()
    # Meter still usable (tokens-only); no exception.
    assert m.can_call() is True


def test_status_path_signals_only_never_awaits_http(tmp_path: Path):
    """KD26: usage_status_block returns quickly while transport is delayed 5s+.

    Holds fetch on an Event (simulates ≥5s / unbounded network) until after
    status returns — status must stay signal-only and sub-250ms.
    """
    clock = _Clock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    m = _meter(tmp_path, clock=clock)
    fetch_entered = threading.Event()
    release_fetch = threading.Event()

    def slow_fetch(*_a, **_k):
        fetch_entered.set()
        # Design bar: status stays responsive while transport is delayed 5s+.
        # Event hold is stronger than a fixed sleep (unbounded stall).
        assert release_fetch.wait(timeout=30.0), "status never released fetch"
        return CreditsSnapshot(
            status=STATUS_OK,
            ok=True,
            credit_usage_percent=10.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
        )

    poller = CreditsPoller(
        meter=m,
        usage_settings=_settings(credits_poll_interval_s=30.0),
        data_dir=tmp_path,
        credential_source="grok_build",
        first_delay_s=0.0,
        fetch_fn=slow_fetch,
        resolve_fn=lambda **_k: _ok_resolution(),
    )
    # Build minimal ProviderRuntime for usage_status_block.
    pr = ProviderRuntime(
        meter=m,
        http_client=None,
        chat_client=MagicMock(),
        worker=None,
        usage_settings=_settings(),
        xai_config=None,
        local_config=None,
        gate=None,
        prefs_path=tmp_path / "prefs.json",
        data_dir=tmp_path,
        provider_name="xai",
        model="grok",
        model_label="Grok",
        credential_source="grok_build",
        credential_ok=True,
        credential_detail=None,
        credential_expires_at=None,
        credential_email=None,
        api_key_configured=False,
        credits_poller=poller,
    )
    poller.start()
    assert fetch_entered.wait(timeout=2.0)
    # Mid-flight HTTP held open (≥ design 5s bar): status must not block.
    t0 = time.perf_counter()
    block = pr.usage_status_block()
    elapsed = time.perf_counter() - t0
    release_fetch.set()
    poller.stop()
    assert elapsed < 0.25, f"status blocked {elapsed:.3f}s (expected signal-only)"
    assert block["enabled"] is True
    assert "week_used_tokens" in block


def test_status_signal_debounced(tmp_path: Path):
    m = _meter(tmp_path)
    poll_count = {"n": 0}
    lock = threading.Lock()

    def fetch(*_a, **_k):
        with lock:
            poll_count["n"] += 1
        return CreditsSnapshot(status=STATUS_ERROR, ok=False, detail="x")

    poller = CreditsPoller(
        meter=m,
        usage_settings=_settings(credits_poll_interval_s=300.0),
        data_dir=tmp_path,
        credential_source="grok_build",
        first_delay_s=0.0,
        fetch_fn=fetch,
        resolve_fn=lambda **_k: _ok_resolution(),
    )
    poller.start()
    time.sleep(0.2)
    with lock:
        after_first = poll_count["n"]
    assert after_first >= 1
    # Rapid signals should not immediately re-poll (debounce 60s for interval 300).
    for _ in range(20):
        poller.request_poll()
    time.sleep(0.15)
    with lock:
        after_signals = poll_count["n"]
    poller.stop()
    assert after_signals == after_first


def test_supervisor_start_stop_credits_poller_flags(tmp_path: Path):
    """``_start_credits_poller`` respects usage/poll flags; stop joins cleanly."""
    from elyra.config import ElyraPaths
    from elyra.runtime.config import RuntimeConfig
    from elyra.runtime.supervisor import ElyraSupervisor

    home = tmp_path / "home"
    data = tmp_path / "data"
    for p in (home, data):
        p.mkdir(parents=True, exist_ok=True)
    (data / "runtime").mkdir(parents=True, exist_ok=True)
    paths = ElyraPaths(
        home=home,
        model_dir=home / "models",
        data_dir=data,
        skills_dir=home / "skills",
        tools_dir=home / "tools",
        prompts_dir=home / "prompts",
    )

    def _pr(meter: UsageMeter | None, usage: UsageSettings) -> ProviderRuntime:
        return ProviderRuntime(
            meter=meter,
            http_client=None,
            chat_client=MagicMock(),
            worker=None,
            usage_settings=usage,
            xai_config=None,
            local_config=None,
            gate=None,
            prefs_path=data / "runtime" / "prefs.json",
            data_dir=data,
            provider_name="xai",
            model="grok",
            model_label="Grok",
            credential_source="grok_build",
            credential_ok=False,
            credential_detail="x",
            credential_expires_at=None,
            credential_email=None,
            api_key_configured=False,
        )

    # Disabled: usage.enabled=false → no poller.
    usage_off = _settings(enabled=False, credits_poll_enabled=True)
    sup = ElyraSupervisor(paths=paths, config=RuntimeConfig(usage=usage_off))
    meter = _meter(data, usage_off)
    pr = _pr(meter, usage_off)
    sup.provider_runtime = pr
    sup._start_credits_poller(meter=meter, pr=pr)  # noqa: SLF001
    assert sup._credits_poller is None  # noqa: SLF001
    assert pr.credits_poller is None

    # Disabled: credits_poll_enabled=false → no poller.
    usage_no_poll = _settings(enabled=True, credits_poll_enabled=False)
    sup2 = ElyraSupervisor(paths=paths, config=RuntimeConfig(usage=usage_no_poll))
    meter2 = _meter(data, usage_no_poll)
    pr2 = _pr(meter2, usage_no_poll)
    sup2.provider_runtime = pr2
    sup2._start_credits_poller(meter=meter2, pr=pr2)  # noqa: SLF001
    assert sup2._credits_poller is None  # noqa: SLF001

    # Enabled: poller starts; shutdown stop clears handle.
    fetch_calls: list[int] = []

    def fetch(*_a, **_k):
        fetch_calls.append(1)
        return CreditsSnapshot(status=STATUS_ERROR, ok=False, detail="x")

    usage_on = _settings(enabled=True, credits_poll_enabled=True, credits_poll_interval_s=3600.0)
    sup3 = ElyraSupervisor(paths=paths, config=RuntimeConfig(usage=usage_on))
    meter3 = _meter(data, usage_on)
    pr3 = _pr(meter3, usage_on)
    sup3.provider_runtime = pr3
    # Inject controlled poller by patching after start path: call start helper
    # then replace fetch via constructing poller like supervisor does — exercise
    # the real _start_credits_poller with a resolve that has no token so no net.
    sup3._start_credits_poller(meter=meter3, pr=pr3)  # noqa: SLF001
    poller = sup3._credits_poller  # noqa: SLF001
    assert poller is not None
    assert pr3.credits_poller is poller
    assert poller._thread is not None and poller._thread.is_alive()  # noqa: SLF001
    # shutdown path used by serve_until_stopped
    sup3._credits_poller = poller  # noqa: SLF001
    poller.stop()
    sup3._credits_poller = None  # noqa: SLF001
    pr3.credits_poller = None
    assert poller._thread is None  # noqa: SLF001
