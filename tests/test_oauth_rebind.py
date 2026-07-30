"""Hermetic tests for OAuth live rebind, 401 retry, credits rotated signal (PR2).

No live network — urllib/urlopen mocked. Covers KD17 / KD21 / KD22.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elyra.config import resolve_paths
from elyra.llm.auth import SOURCE_XAI_OAUTH, resolve_bearer
from elyra.llm.client import HttpChatClient
from elyra.llm.oauth_store import OAuthBundle, load_oauth_bundle, save_oauth_bundle
from elyra.llm.usage import UsageMeter
from elyra.llm.xai_oauth import (
    DETAIL_OAUTH_REAUTH_REQUIRED,
    XAI_OAUTH_CLIENT_ID,
    XAI_OAUTH_SCOPE,
    ensure_fresh_access,
)
from elyra.runtime.credits_poller import CreditsPoller
from elyra.runtime.provider_runtime import ProviderRuntime, credential_detail_message
from elyra.settings import UsageSettings


def _future(hours: float = 2.0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _near(seconds: float = 30.0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bundle(
    *,
    access: str = "access-old",
    refresh: str | None = "refresh-1",
    expires_at: str | None = None,
    reauth: bool = False,
    email: str | None = "op@example.com",
) -> OAuthBundle:
    return OAuthBundle(
        version=1,
        client_id=XAI_OAUTH_CLIENT_ID,
        access_token=access,
        refresh_token=refresh,
        token_type="Bearer",
        scope=XAI_OAUTH_SCOPE,
        expires_at=expires_at or _future(),
        email=email,
        subject="sub-1",
        obtained_at=_future(),
        updated_at=_future(),
        auth_method="device_code",
        reauth_required=reauth,
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return paths.data_dir


class _FakeHTTPResponse:
    def __init__(self, body: bytes, code: int = 200) -> None:
        self._body = body
        self.status = code
        self.code = code

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _ok_chat_body() -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "hi"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
    ).encode("utf-8")


def _minimal_pr(data_dir: Path, **kwargs: Any) -> ProviderRuntime:
    usage = UsageSettings(enabled=False)
    defaults = dict(
        meter=None,
        http_client=None,
        chat_client=MagicMock(),
        worker=None,
        usage_settings=usage,
        xai_config=None,
        local_config=None,
        gate=None,
        prefs_path=data_dir / "runtime" / "provider.json",
        data_dir=data_dir,
        provider_name="xai",
        model="grok-4.5",
        model_label="Grok 4.5",
        credential_source=SOURCE_XAI_OAUTH,
        credential_ok=False,
        credential_detail=None,
        credential_expires_at=None,
        credential_email=None,
        api_key_configured=False,
    )
    defaults.update(kwargs)
    return ProviderRuntime(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# set_bearer_token rebind
# ---------------------------------------------------------------------------


def test_set_bearer_token_affects_next_authorization():
    auths: list[str | None] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        auth = req.get_header("Authorization") or req.get_header("authorization")
        auths.append(auth)
        return _FakeHTTPResponse(_ok_chat_body())

    client = HttpChatClient.for_xai(model="grok-4.5", bearer_token="tok-old")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.chat_completion([{"role": "user", "content": "1"}])
        client.set_bearer_token("tok-new")
        client.chat_completion([{"role": "user", "content": "2"}])

    assert auths[0] == "Bearer tok-old"
    assert auths[1] == "Bearer tok-new"


def test_on_access_refreshed_rebinds_live_client(data_dir: Path):
    client = HttpChatClient.for_xai(model="grok-4.5", bearer_token="tok-old")
    pr = _minimal_pr(
        data_dir,
        http_client=client,
        chat_client=client,
        credential_ok=True,
        credential_email="old@x.ai",
    )
    save_oauth_bundle(data_dir, _bundle(access="tok-new", refresh="r2"))

    pr.on_access_refreshed("tok-new", expires_at=_future(), email="new@x.ai")

    auths: list[str | None] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        auths.append(req.get_header("Authorization") or req.get_header("authorization"))
        return _FakeHTTPResponse(_ok_chat_body())

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.chat_completion([{"role": "user", "content": "x"}])

    assert auths == ["Bearer tok-new"]
    assert pr.credential_email == "new@x.ai"
    assert pr.credential_ok is True
    assert "tok-new" in pr.auth_redaction_values()


# ---------------------------------------------------------------------------
# 401 intercept before RuntimeError + single retry
# ---------------------------------------------------------------------------


def test_401_refresh_cb_retries_once_with_new_bearer():
    calls = {"n": 0}
    refresh_calls = {"n": 0}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        calls["n"] += 1
        auth = req.get_header("Authorization") or req.get_header("authorization")
        if calls["n"] == 1:
            assert auth == "Bearer tok-stale"
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=401,
                msg="Unauthorized",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b'{"error":"expired"}'),
            )
        assert auth == "Bearer tok-fresh"
        return _FakeHTTPResponse(_ok_chat_body())

    def refresh_cb() -> str | None:
        refresh_calls["n"] += 1
        return "tok-fresh"

    client = HttpChatClient.for_xai(
        model="grok-4.5",
        bearer_token="tok-stale",
        refresh_cb=refresh_cb,
    )
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client.chat_completion([{"role": "user", "content": "x"}])

    assert result.content == "hi"
    assert calls["n"] == 2
    assert refresh_calls["n"] == 1


def test_401_without_refresh_cb_raises_runtime_error_not_parsed():
    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error":"nope"}'),
        )

    client = HttpChatClient.for_xai(model="grok-4.5", bearer_token="secret-tok")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(RuntimeError) as ei:
            client.chat_completion([{"role": "user", "content": "x"}])
    assert "401" in str(ei.value)
    assert "secret-tok" not in str(ei.value)


def test_401_refresh_fail_raises_after_one_attempt():
    calls = {"n": 0}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        calls["n"] += 1
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error":"still bad"}'),
        )

    client = HttpChatClient.for_xai(
        model="grok-4.5",
        bearer_token="tok",
        refresh_cb=lambda: None,
    )
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(RuntimeError) as ei:
            client.chat_completion([{"role": "user", "content": "x"}])
    assert "401" in str(ei.value)
    assert calls["n"] == 1  # no retry when refresh returns None


def test_non_401_does_not_call_refresh_cb():
    refresh_calls = {"n": 0}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=500,
            msg="err",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"boom"),
        )

    def refresh_cb() -> str | None:
        refresh_calls["n"] += 1
        return "x"

    client = HttpChatClient.for_xai(
        model="grok-4.5",
        bearer_token="tok",
        refresh_cb=refresh_cb,
    )
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(RuntimeError) as ei:
            client.chat_completion([{"role": "user", "content": "x"}])
    assert "500" in str(ei.value)
    assert refresh_calls["n"] == 0


# ---------------------------------------------------------------------------
# rebuild + complete_oauth_login + keep-alive style rebind
# ---------------------------------------------------------------------------


def test_rebuild_chat_stack_wires_refresh_cb_for_oauth(data_dir: Path):
    save_oauth_bundle(data_dir, _bundle(access="access-1"))
    pr = _minimal_pr(data_dir, credential_source=SOURCE_XAI_OAUTH)
    with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
        pr.rebuild_chat_stack()
    assert pr.credential_ok is True
    assert pr.http_client is not None
    assert pr.http_client._refresh_cb is not None  # type: ignore[attr-defined]
    assert pr.credential_email == "op@example.com"
    pr.stop_background_tasks()


def test_complete_oauth_login_persist_and_rebuild(data_dir: Path):
    pr = _minimal_pr(data_dir, credential_source="api_key")
    tokens = _bundle(access="login-access", email="login@x.ai")
    with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
        fields = pr.complete_oauth_login(tokens, activate=True)
    assert fields["credential_source"] == SOURCE_XAI_OAUTH
    assert fields["credential_ok"] is True
    assert fields["oauth_configured"] is True
    assert pr.http_client is not None
    loaded = load_oauth_bundle(data_dir)
    assert loaded.access_token == "login-access"
    assert loaded.reauth_required is False
    pr.stop_background_tasks()


def test_complete_oauth_login_activate_false_leaves_source(data_dir: Path):
    pr = _minimal_pr(data_dir, credential_source="api_key", credential_ok=False)
    with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
        pr.complete_oauth_login(_bundle(access="later-access"), activate=False)
    assert pr.credential_source == "api_key"
    # Bundle on disk for later
    assert load_oauth_bundle(data_dir).access_token == "later-access"
    pr.stop_background_tasks()


def test_rebuild_fail_closed_on_reauth_required(data_dir: Path):
    save_oauth_bundle(data_dir, _bundle(access="jwt", reauth=True))
    pr = _minimal_pr(data_dir, credential_source=SOURCE_XAI_OAUTH)
    with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
        pr.rebuild_chat_stack()
    assert pr.credential_ok is False
    assert pr.credential_detail == DETAIL_OAUTH_REAUTH_REQUIRED
    assert pr.http_client is None
    from elyra.llm.client import FailingChatClient

    assert isinstance(pr.chat_client, FailingChatClient)


def test_resolve_bearer_pure_no_rebind_side_effect(data_dir: Path):
    """resolve_bearer must not call set_bearer_token (KD21 purity)."""
    save_oauth_bundle(data_dir, _bundle(access="access-pure", expires_at=_future()))
    client = HttpChatClient.for_xai(model="grok-4.5", bearer_token="frozen")
    set_calls: list[str | None] = []
    orig = client.set_bearer_token

    def tracking(tok: str | None) -> None:
        set_calls.append(tok)
        orig(tok)

    client.set_bearer_token = tracking  # type: ignore[method-assign]
    r = resolve_bearer(source=SOURCE_XAI_OAUTH, data_dir=data_dir, env={})
    assert r.ok is True
    assert r.token == "access-pure"
    assert set_calls == []  # purity


# ---------------------------------------------------------------------------
# Keep-alive recovery + 401 fail-closed status (review Issue 1 / 2)
# ---------------------------------------------------------------------------


def test_keepalive_retries_after_transient_fail_and_recovers(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """After oauth_refresh_failed, keep-alive must keep calling ensure_fresh and
    restore credential_ok via on_access_refreshed when access becomes ok again.
    """
    from elyra.llm.xai_oauth import DETAIL_OAUTH_REFRESH_FAILED, FreshAccessResult

    client = HttpChatClient.for_xai(model="grok-4.5", bearer_token="tok-old")
    pr = _minimal_pr(
        data_dir,
        http_client=client,
        chat_client=client,
        credential_ok=True,
        credential_source=SOURCE_XAI_OAUTH,
    )
    # Short interval so test drives multiple ticks quickly.
    monkeypatch.setattr(
        "elyra.runtime.provider_runtime._OAUTH_KEEPALIVE_INTERVAL_S",
        0.05,
    )

    calls = {"n": 0}

    def fake_ensure(data_dir_arg, **_k):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            return FreshAccessResult(
                ok=False,
                access_token=None,
                expires_at=_near(10),
                email="op@example.com",
                detail=DETAIL_OAUTH_REFRESH_FAILED,
                rotated=False,
            )
        # Subsequent ticks: access ok again (e.g. network recovered; still fresh
        # so rotated=False) — prior_ok is False so rebind must still fire.
        return FreshAccessResult(
            ok=True,
            access_token="tok-recovered",
            expires_at=_future(),
            email="op@example.com",
            detail=None,
            rotated=False,
        )

    monkeypatch.setattr(
        "elyra.runtime.provider_runtime.ensure_fresh_access",
        fake_ensure,
    )
    pr._start_oauth_keepalive()
    import time

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if calls["n"] >= 2 and pr.credential_ok is True:
            break
        time.sleep(0.05)
    pr.stop_background_tasks()

    assert calls["n"] >= 2, "keep-alive must keep calling ensure_fresh after fail"
    assert pr.credential_ok is True
    assert pr.credential_detail is None
    # Bearer rebound to recovered token
    auths: list[str | None] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        auths.append(req.get_header("Authorization") or req.get_header("authorization"))
        return _FakeHTTPResponse(_ok_chat_body())

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.chat_completion([{"role": "user", "content": "x"}])
    assert auths == ["Bearer tok-recovered"]


def test_keepalive_keeps_trying_on_reauth_required_but_stays_fail_closed(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """invalid_grant / reauth_required: still fail-closed each tick, but loop continues."""
    from elyra.llm.xai_oauth import DETAIL_OAUTH_REAUTH_REQUIRED, FreshAccessResult

    client = HttpChatClient.for_xai(model="grok-4.5", bearer_token="tok")
    pr = _minimal_pr(
        data_dir,
        http_client=client,
        chat_client=client,
        credential_ok=True,
        credential_source=SOURCE_XAI_OAUTH,
    )
    monkeypatch.setattr(
        "elyra.runtime.provider_runtime._OAUTH_KEEPALIVE_INTERVAL_S",
        0.05,
    )
    calls = {"n": 0}

    def fake_ensure(*_a, **_k):
        calls["n"] += 1
        return FreshAccessResult(
            ok=False,
            access_token=None,
            expires_at=_future(),
            email="op@example.com",
            detail=DETAIL_OAUTH_REAUTH_REQUIRED,
            rotated=False,
        )

    monkeypatch.setattr(
        "elyra.runtime.provider_runtime.ensure_fresh_access",
        fake_ensure,
    )
    pr._start_oauth_keepalive()
    import time

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and calls["n"] < 3:
        time.sleep(0.05)
    pr.stop_background_tasks()
    assert calls["n"] >= 3
    assert pr.credential_ok is False
    assert pr.credential_detail == DETAIL_OAUTH_REAUTH_REQUIRED


def test_401_refresh_cb_fail_sets_credential_ok_false(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    """Force-refresh failure must fail-closed status for Glass CTA (Issue 2)."""
    from elyra.llm.xai_oauth import DETAIL_OAUTH_REAUTH_REQUIRED, FreshAccessResult

    client = HttpChatClient.for_xai(model="grok-4.5", bearer_token="tok-stale")
    pr = _minimal_pr(
        data_dir,
        http_client=client,
        chat_client=client,
        credential_ok=True,
        credential_source=SOURCE_XAI_OAUTH,
        credential_detail=None,
    )
    client.set_refresh_cb(pr._make_chat_refresh_cb())

    def fake_ensure(*_a, **_k):
        return FreshAccessResult(
            ok=False,
            access_token=None,
            expires_at=_near(5),
            email="op@example.com",
            detail=DETAIL_OAUTH_REAUTH_REQUIRED,
            rotated=False,
        )

    monkeypatch.setattr(
        "elyra.runtime.provider_runtime.ensure_fresh_access",
        fake_ensure,
    )

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b'{"error":"expired"}'),
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(RuntimeError) as ei:
            client.chat_completion([{"role": "user", "content": "x"}])
    assert "401" in str(ei.value)
    assert pr.credential_ok is False
    assert pr.credential_detail == DETAIL_OAUTH_REAUTH_REQUIRED


# ---------------------------------------------------------------------------
# Credits poller rotated → on_access_refreshed
# ---------------------------------------------------------------------------


def test_credits_poller_signals_rebind_on_rotated(data_dir: Path, tmp_path: Path):
    from elyra.llm.auth import CredentialResolution
    from elyra.llm.credits import STATUS_OK, CreditsSnapshot

    rebind_calls: list[tuple] = []

    def on_refreshed(access, expires_at=None, email=None):
        rebind_calls.append((access, expires_at, email))

    def resolve_fn(**_k):
        return CredentialResolution(
            ok=True,
            source=SOURCE_XAI_OAUTH,
            token="access-rotated",
            detail=None,
            expires_at=_future(),
            email="e@x.ai",
            api_key_configured=False,
            rotated=True,
        )

    meter = UsageMeter.load(data_dir, UsageSettings(enabled=True))
    poller = CreditsPoller(
        meter=meter,
        usage_settings=UsageSettings(
            enabled=True,
            credits_poll_enabled=True,
            credits_poll_interval_s=300.0,
        ),
        data_dir=data_dir,
        credential_source=SOURCE_XAI_OAUTH,
        first_delay_s=0.0,
        fetch_fn=lambda *a, **k: CreditsSnapshot(
            status=STATUS_OK, ok=True, credit_usage_percent=1.0
        ),
        resolve_fn=resolve_fn,
        on_access_refreshed=on_refreshed,
        enabled=True,
    )
    poller.start()
    # Wait for first poll
    import time

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not rebind_calls:
        time.sleep(0.05)
    poller.stop()
    assert rebind_calls, "expected on_access_refreshed after rotated resolve"
    assert rebind_calls[0][0] == "access-rotated"


def test_credits_poller_signals_rebind_on_token_string_change(data_dir: Path):
    from elyra.llm.auth import CredentialResolution
    from elyra.llm.credits import STATUS_OK, CreditsSnapshot

    rebind_calls: list[str] = []
    tokens = iter(["tok-a", "tok-b", "tok-b"])

    def resolve_fn(**_k):
        t = next(tokens)
        return CredentialResolution(
            ok=True,
            source=SOURCE_XAI_OAUTH,
            token=t,
            detail=None,
            expires_at=_future(),
            email=None,
            api_key_configured=False,
            rotated=False,
        )

    meter = UsageMeter.load(data_dir, UsageSettings(enabled=True))
    poller = CreditsPoller(
        meter=meter,
        usage_settings=UsageSettings(
            enabled=True,
            credits_poll_enabled=True,
            credits_poll_interval_s=300.0,
        ),
        data_dir=data_dir,
        credential_source=SOURCE_XAI_OAUTH,
        first_delay_s=0.0,
        fetch_fn=lambda *a, **k: CreditsSnapshot(
            status=STATUS_OK, ok=True, credit_usage_percent=1.0
        ),
        resolve_fn=resolve_fn,
        on_access_refreshed=lambda a, *rest: rebind_calls.append(a),
        enabled=True,
    )
    # Drive two polls synchronously via _do_poll
    poller._do_poll()  # first: establishes last token, no rebind (prev None)
    poller._do_poll()  # second: tok-b != tok-a → rebind
    assert rebind_calls == ["tok-b"]


# ---------------------------------------------------------------------------
# Detail messages + redaction union
# ---------------------------------------------------------------------------


def test_credential_detail_message_oauth_points_to_elyra_login():
    msg = credential_detail_message("oauth_reauth_required") or ""
    assert "Glass" in msg or "elyra auth" in msg
    assert "grok login" not in msg
    msg2 = credential_detail_message("missing_oauth_tokens") or ""
    assert "elyra auth" in msg2 or "Glass" in msg2


def test_auth_redaction_scrubs_oauth_from_tool_payload(data_dir: Path):
    from elyra.llm.auth import auth_secret_values_for_redaction, write_stored_api_key
    from elyra.secrets.inject import redact_tool_result_payload

    save_oauth_bundle(
        data_dir,
        _bundle(access="super-secret-access-xyz", refresh="super-secret-refresh-xyz"),
    )
    write_stored_api_key(data_dir, "super-secret-apikey-xyz")
    vals = auth_secret_values_for_redaction(data_dir)
    payload = {
        "echo": "Bearer super-secret-access-xyz and key=super-secret-apikey-xyz "
        "refresh=super-secret-refresh-xyz"
    }
    redacted = redact_tool_result_payload(payload, vals)
    blob = json.dumps(redacted)
    assert "super-secret-access-xyz" not in blob
    assert "super-secret-refresh-xyz" not in blob
    assert "super-secret-apikey-xyz" not in blob
    assert "***" in blob


def test_ensure_fresh_then_resolve_reauth_cold_start(data_dir: Path):
    """invalid_grant marks disk; subsequent resolve_bearer (new call) stays fail-closed."""
    save_oauth_bundle(data_dir, _bundle(access="access-1", expires_at=_near(10)))

    class _Resp:
        def __init__(self, body: bytes, code: int = 200) -> None:
            self._body = body
            self.status = code
            self.code = code

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    def urlopen(req: Any, timeout: float = 30.0) -> Any:
        url = req.get_full_url()
        if "openid-configuration" in url:
            return _Resp(
                json.dumps(
                    {
                        "issuer": "https://auth.x.ai",
                        "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                        "token_endpoint": "https://auth.x.ai/oauth2/token",
                    }
                ).encode()
            )
        # token endpoint → invalid_grant
        raise urllib.error.HTTPError(
            url=url,
            code=400,
            msg="Bad Request",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(json.dumps({"error": "invalid_grant"}).encode()),
        )

    r = ensure_fresh_access(data_dir, skew_s=120, urlopen=urlopen)
    assert r.ok is False
    assert r.detail == DETAIL_OAUTH_REAUTH_REQUIRED
    assert load_oauth_bundle(data_dir).reauth_required is True

    # Cold resolve (no network needed) honors durable flag
    r2 = resolve_bearer(source=SOURCE_XAI_OAUTH, data_dir=data_dir, env={})
    assert r2.ok is False
    assert r2.token is None
    assert r2.detail == DETAIL_OAUTH_REAUTH_REQUIRED
