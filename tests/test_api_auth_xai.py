"""Hermetic tests for xAI device-code login/logout API (PR3).

Mocks OIDC HTTP (request_device_code / poll_device_token) — never hits network.
Covers: start/status/cancel/logout, secret hygiene, replace-on-start, success →
complete_oauth_login, loopback Origin check, provider 503, supervisor cancel.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elyra.config import resolve_paths
from elyra.identity import IdentityStore
from elyra.llm.auth import SOURCE_XAI_OAUTH
from elyra.llm.client import StubChatClient
from elyra.llm.oauth_store import load_oauth_bundle, load_oauth_bundle_optional, save_oauth_bundle
from elyra.llm.queue import ChatRequestGate
from elyra.llm.usage import UsageMeter
from elyra.llm.xai_oauth import (
    DETAIL_AUTHORIZATION_PENDING,
    DETAIL_OAUTH_DENIED,
    DETAIL_OAUTH_DEVICE_EXPIRED,
    DeviceCodeResponse,
    TokenPollResult,
    XAI_OAUTH_CLIENT_ID,
    XAI_OAUTH_SCOPE,
)
from elyra.loop.doloop import DoLoopResult
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import TimerService
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.oauth_session import (
    OAuthDeviceSession,
    STATE_CANCELLED,
    STATE_ERROR,
    STATE_IDLE,
    STATE_PENDING,
    STATE_SUCCESS,
)
from elyra.runtime.provider_runtime import ProviderRuntime
from elyra.runtime.state import RuntimeState
from elyra.settings import UsageSettings, default_settings
from elyra.users import UsersStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future(hours: float = 2.0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jwt(payload: dict[str, Any]) -> str:
    def b64(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64(payload)}.sig"


def _device(
    *,
    device_code: str = "SECRET-DEVICE-CODE-NEVER-RETURN",
    user_code: str = "ABCD-EFGH",
    interval: int = 1,
    expires_in: int = 600,
) -> DeviceCodeResponse:
    return DeviceCodeResponse(
        device_code=device_code,
        user_code=user_code,
        verification_uri="https://auth.x.ai/device",
        verification_uri_complete="https://auth.x.ai/device?user_code=ABCD-EFGH",
        expires_in=expires_in,
        interval=interval,
    )


def _pending() -> TokenPollResult:
    return TokenPollResult(
        ok=False,
        pending=True,
        slow_down=False,
        access_token=None,
        refresh_token=None,
        expires_in=None,
        token_type=None,
        scope=None,
        id_token=None,
        detail=DETAIL_AUTHORIZATION_PENDING,
        error="authorization_pending",
    )


def _success(
    *,
    access: str = "access-login-1",
    refresh: str = "refresh-login-1",
    email: str = "op@example.com",
) -> TokenPollResult:
    return TokenPollResult(
        ok=True,
        pending=False,
        slow_down=False,
        access_token=access,
        refresh_token=refresh,
        expires_in=3600,
        token_type="Bearer",
        scope=XAI_OAUTH_SCOPE,
        id_token=_jwt({"email": email, "sub": "sub-1"}),
        detail=None,
        error=None,
    )


def _denied() -> TokenPollResult:
    return TokenPollResult(
        ok=False,
        pending=False,
        slow_down=False,
        access_token=None,
        refresh_token=None,
        expires_in=None,
        token_type=None,
        scope=None,
        id_token=None,
        detail=DETAIL_OAUTH_DENIED,
        error="access_denied",
    )


def _assert_no_secrets(payload: Any) -> None:
    """Assert response never contains tokens or the private device_code value.

    Note: public field ``auth_method: "device_code"`` is allowed (not a secret).
    """
    raw = json.dumps(payload)
    for banned in (
        "SECRET-DEVICE-CODE",
        "access-login",
        "refresh-login",
        "disk-secret-token",
        "http-access",
        "to-delete",
    ):
        assert banned not in raw, f"secret-like string leaked: {banned}"
    if isinstance(payload, dict):
        for key in ("access_token", "refresh_token", "device_code", "id_token", "token"):
            assert key not in payload, f"secret key present: {key}"


def _bundle(
    *,
    access: str = "access-disk",
    refresh: str = "refresh-disk",
    email: str = "disk@example.com",
) -> Any:
    from elyra.llm.oauth_store import OAuthBundle

    return OAuthBundle(
        version=1,
        client_id=XAI_OAUTH_CLIENT_ID,
        access_token=access,
        refresh_token=refresh,
        token_type="Bearer",
        scope=XAI_OAUTH_SCOPE,
        expires_at=_future(),
        email=email,
        subject="sub-disk",
        obtained_at=_future(),
        updated_at=_future(),
        auth_method="device_code",
        reauth_required=False,
    )


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
        credential_source="api_key",
        credential_ok=False,
        credential_detail=None,
        credential_expires_at=None,
        credential_email=None,
        api_key_configured=False,
    )
    defaults.update(kwargs)
    return ProviderRuntime(**defaults)  # type: ignore[arg-type]


def _fake_registry() -> MagicMock:
    reg = MagicMock()
    reg.openai_tools.return_value = []
    reg.execute.return_value = MagicMock(ok=True, payload={}, ends_moment=False)
    return reg


def _stub_loop(**kwargs: Any) -> DoLoopResult:
    ctx = kwargs.get("ctx")
    mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
    return DoLoopResult(
        stop_reason="no_tools",
        hop_count=1,
        moment_id=mid,
        spoke=False,
    )


class _ApiHarness:
    def __init__(self, paths, *, provider: ProviderRuntime | None = None) -> None:
        self.paths = paths
        stop = threading.Event()
        queue = WakeQueue(paths)
        timers = TimerService(paths, queue)
        moments = MomentStore(paths)
        from elyra.goals import GoalsStore

        goals = GoalsStore(paths)
        self.worker = PresenceWorker(
            paths=paths,
            client=StubChatClient(),
            stop_event=stop,
            poll_seconds=0.05,
            settings=default_settings(),
            queue=queue,
            timers=timers,
            moments=moments,
            registry=_fake_registry(),
            goals=goals,
            run_do_loop_fn=_stub_loop,
        )
        self._stop = stop
        config = RuntimeConfig(api_host="127.0.0.1", api_port=0)
        self.state = RuntimeState()
        self.gate = ChatRequestGate()
        self.provider = provider
        self.server, self._api_thread = start_api_server(
            config,
            paths=paths,
            gate=self.gate,
            state=self.state,
            worker=self.worker,
            provider=provider,
            goals=goals,
            moments=moments,
            identity=IdentityStore(paths),
            users=UsersStore(paths),
            tools=None,
            skills=None,
        )
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def close(self) -> None:
        if self.provider is not None:
            try:
                self.provider.stop_background_tasks()
            except Exception:  # noqa: BLE001
                pass
        self._stop.set()
        self.server.shutdown()

    def _req(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        data = None
        hdrs: dict[str, str] = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            headers=hdrs,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"error": raw}
            return exc.code, parsed

    def get(self, path: str, **kw: Any) -> tuple[int, dict]:
        return self._req("GET", path, **kw)

    def post(self, path: str, body: dict | None = None, **kw: Any) -> tuple[int, dict]:
        return self._req("POST", path, body if body is not None else {}, **kw)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


@pytest.fixture
def data_dir(paths) -> Path:
    return paths.data_dir


# ---------------------------------------------------------------------------
# OAuthDeviceSession unit (no HTTP server)
# ---------------------------------------------------------------------------


def test_session_start_returns_public_fields_only(data_dir: Path) -> None:
    device = _device()
    polls = [_pending(), _pending(), _success()]

    with (
        patch(
            "elyra.runtime.oauth_session.request_device_code",
            return_value=device,
        ),
        patch(
            "elyra.runtime.oauth_session.poll_device_token",
            side_effect=polls,
        ),
    ):
        sess = OAuthDeviceSession(data_dir, on_success=None)
        start = sess.start(activate=True)
        assert start["ok"] is True
        assert start["user_code"] == "ABCD-EFGH"
        assert start["verification_uri"] == "https://auth.x.ai/device"
        assert start["pending"] is True
        assert "device_code" not in start
        _assert_no_secrets(start)

        # Wait for success
        deadline = time.time() + 5.0
        while time.time() < deadline:
            st = sess.status()
            if st["state"] == STATE_SUCCESS:
                break
            time.sleep(0.05)
        st = sess.status()
        assert st["state"] == STATE_SUCCESS
        assert st.get("email") == "op@example.com"
        _assert_no_secrets(st)

        loaded = load_oauth_bundle(data_dir)
        assert loaded.access_token == "access-login-1"
        assert loaded.auth_method == "device_code"
        assert loaded.reauth_required is False
        sess.cancel()


def test_session_cancel_stops_poller(data_dir: Path) -> None:
    device = _device(interval=1, expires_in=120)
    call_count = {"n": 0}

    def poll(*_a: Any, **_k: Any) -> TokenPollResult:
        call_count["n"] += 1
        return _pending()

    with (
        patch(
            "elyra.runtime.oauth_session.request_device_code",
            return_value=device,
        ),
        patch(
            "elyra.runtime.oauth_session.poll_device_token",
            side_effect=poll,
        ),
    ):
        sess = OAuthDeviceSession(data_dir)
        sess.start()
        time.sleep(0.15)
        out = sess.cancel()
        assert out["ok"] is True
        assert out["state"] == STATE_CANCELLED
        n_after = call_count["n"]
        time.sleep(0.3)
        # Poller should not keep hammering after cancel.
        assert call_count["n"] <= n_after + 1
        assert sess.status()["state"] == STATE_CANCELLED


def test_session_replace_on_start(data_dir: Path) -> None:
    d1 = _device(device_code="dc-1", user_code="CODE-ONE")
    d2 = _device(device_code="dc-2", user_code="CODE-TWO")
    devices = [d1, d2]

    with (
        patch(
            "elyra.runtime.oauth_session.request_device_code",
            side_effect=devices,
        ),
        patch(
            "elyra.runtime.oauth_session.poll_device_token",
            return_value=_pending(),
        ),
    ):
        sess = OAuthDeviceSession(data_dir)
        s1 = sess.start()
        assert s1["user_code"] == "CODE-ONE"
        s2 = sess.start()
        assert s2["user_code"] == "CODE-TWO"
        assert sess.status()["state"] == STATE_PENDING
        assert sess.status().get("user_code") == "CODE-TWO"
        sess.cancel()


def test_session_denied_terminal_error(data_dir: Path) -> None:
    with (
        patch(
            "elyra.runtime.oauth_session.request_device_code",
            return_value=_device(interval=1),
        ),
        patch(
            "elyra.runtime.oauth_session.poll_device_token",
            return_value=_denied(),
        ),
    ):
        sess = OAuthDeviceSession(data_dir)
        sess.start()
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if sess.status()["state"] == STATE_ERROR:
                break
            time.sleep(0.05)
        st = sess.status()
        assert st["state"] == STATE_ERROR
        assert st.get("detail") == DETAIL_OAUTH_DENIED
        assert load_oauth_bundle_optional(data_dir) is None


def test_session_success_calls_on_success_not_persist_only(data_dir: Path) -> None:
    seen: list[tuple[Any, bool]] = []

    def on_success(tokens: Any, *, activate: bool = True) -> None:
        seen.append((tokens, activate))
        # Mimic complete: write bundle ourselves so we can assert activate flag.
        from elyra.llm.oauth_store import persist_oauth_login

        persist_oauth_login(data_dir, tokens, activate=False)

    with (
        patch(
            "elyra.runtime.oauth_session.request_device_code",
            return_value=_device(interval=1),
        ),
        patch(
            "elyra.runtime.oauth_session.poll_device_token",
            return_value=_success(access="via-callback"),
        ),
    ):
        sess = OAuthDeviceSession(data_dir, on_success=on_success)
        sess.start(activate=True)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if sess.status()["state"] == STATE_SUCCESS:
                break
            time.sleep(0.05)
        assert sess.status()["state"] == STATE_SUCCESS
        assert len(seen) == 1
        tokens, activate = seen[0]
        assert activate is True
        assert tokens["access_token"] == "via-callback"
        assert load_oauth_bundle(data_dir).access_token == "via-callback"


def test_session_expires_by_deadline(data_dir: Path) -> None:
    with (
        patch(
            "elyra.runtime.oauth_session.request_device_code",
            return_value=_device(interval=1, expires_in=1),
        ),
        patch(
            "elyra.runtime.oauth_session.poll_device_token",
            return_value=_pending(),
        ),
    ):
        sess = OAuthDeviceSession(data_dir)
        sess.start()
        deadline = time.time() + 4.0
        while time.time() < deadline:
            if sess.status()["state"] == STATE_ERROR:
                break
            time.sleep(0.1)
        st = sess.status()
        assert st["state"] == STATE_ERROR
        assert st.get("detail") == DETAIL_OAUTH_DEVICE_EXPIRED


# ---------------------------------------------------------------------------
# ProviderRuntime device API
# ---------------------------------------------------------------------------


def test_provider_complete_on_device_success(data_dir: Path) -> None:
    pr = _minimal_pr(data_dir, credential_source="api_key")
    with (
        patch(
            "elyra.runtime.oauth_session.request_device_code",
            return_value=_device(interval=1),
        ),
        patch(
            "elyra.runtime.oauth_session.poll_device_token",
            return_value=_success(access="pr-access", email="pr@x.ai"),
        ),
        patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]),
        patch.object(ProviderRuntime, "rebuild_chat_stack") as rebuild,
    ):
        start = pr.start_xai_device_login(activate=True)
        assert start["ok"] is True
        assert "device_code" not in start
        deadline = time.time() + 3.0
        while time.time() < deadline:
            st = pr.xai_device_status()
            if st["state"] == STATE_SUCCESS:
                break
            time.sleep(0.05)
        st = pr.xai_device_status()
        assert st["state"] == STATE_SUCCESS
        assert st.get("email") == "pr@x.ai"
        _assert_no_secrets(st)
        assert pr.credential_source == SOURCE_XAI_OAUTH
        assert load_oauth_bundle(data_dir).access_token == "pr-access"
        rebuild.assert_called()
    pr.stop_background_tasks()


def test_provider_stop_cancels_device_session(data_dir: Path) -> None:
    pr = _minimal_pr(data_dir)
    with (
        patch(
            "elyra.runtime.oauth_session.request_device_code",
            return_value=_device(interval=1, expires_in=120),
        ),
        patch(
            "elyra.runtime.oauth_session.poll_device_token",
            return_value=_pending(),
        ),
    ):
        pr.start_xai_device_login()
        assert pr.xai_device_status()["state"] == STATE_PENDING
        pr.stop_background_tasks()
        assert pr.xai_device_status()["state"] == STATE_CANCELLED


# ---------------------------------------------------------------------------
# HTTP API hermetic
# ---------------------------------------------------------------------------


@pytest.fixture
def harness(paths):
    pr = _minimal_pr(paths.data_dir)
    h = _ApiHarness(paths, provider=pr)
    try:
        yield h
    finally:
        h.close()


@pytest.fixture
def harness_no_provider(paths):
    h = _ApiHarness(paths, provider=None)
    try:
        yield h
    finally:
        h.close()


def test_api_get_auth_xai_empty(harness: _ApiHarness) -> None:
    code, body = harness.get("/api/auth/xai")
    assert code == 200
    assert body["ok"] is True
    assert body["configured"] is False
    assert body.get("oauth_configured") is False
    _assert_no_secrets(body)


def test_api_get_auth_xai_with_bundle(harness: _ApiHarness, data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle(access="disk-secret-token", email="disk@x.ai"))
    code, body = harness.get("/api/auth/xai")
    assert code == 200
    assert body["configured"] is True
    assert body["email"] == "disk@x.ai"
    assert "disk-secret-token" not in json.dumps(body)
    _assert_no_secrets(body)


def test_api_device_start_status_cancel(harness: _ApiHarness) -> None:
    with (
        patch(
            "elyra.runtime.oauth_session.request_device_code",
            return_value=_device(interval=1, expires_in=120),
        ),
        patch(
            "elyra.runtime.oauth_session.poll_device_token",
            return_value=_pending(),
        ),
    ):
        code, body = harness.post("/api/auth/xai/device/start", {"activate": True})
        assert code == 200
        assert body["ok"] is True
        assert body["user_code"] == "ABCD-EFGH"
        assert body["pending"] is True
        assert "device_code" not in body
        _assert_no_secrets(body)

        code, st = harness.get("/api/auth/xai/device/status")
        assert code == 200
        assert st["state"] == STATE_PENDING
        assert st.get("user_code") == "ABCD-EFGH"
        _assert_no_secrets(st)

        code, cancel = harness.post("/api/auth/xai/device/cancel", {})
        assert code == 200
        assert cancel["ok"] is True
        assert cancel["state"] == STATE_CANCELLED

        code, st2 = harness.get("/api/auth/xai/device/status")
        assert code == 200
        assert st2["state"] == STATE_CANCELLED


def test_api_device_start_success_persists_and_activates(
    harness: _ApiHarness, data_dir: Path
) -> None:
    with (
        patch(
            "elyra.runtime.oauth_session.request_device_code",
            return_value=_device(interval=1),
        ),
        patch(
            "elyra.runtime.oauth_session.poll_device_token",
            return_value=_success(access="http-access", email="http@x.ai"),
        ),
        patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]),
        patch.object(ProviderRuntime, "rebuild_chat_stack"),
    ):
        code, body = harness.post("/api/auth/xai/device/start", {})
        assert code == 200
        assert body["ok"] is True
        deadline = time.time() + 3.0
        st: dict[str, Any] = {}
        while time.time() < deadline:
            _, st = harness.get("/api/auth/xai/device/status")
            if st.get("state") == STATE_SUCCESS:
                break
            time.sleep(0.05)
        assert st.get("state") == STATE_SUCCESS
        assert st.get("email") == "http@x.ai"
        _assert_no_secrets(st)
        loaded = load_oauth_bundle(data_dir)
        assert loaded.access_token == "http-access"
        assert harness.provider is not None
        assert harness.provider.credential_source == SOURCE_XAI_OAUTH


def test_api_logout(harness: _ApiHarness, data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle(access="to-delete"))
    with patch.object(ProviderRuntime, "rebuild_chat_stack"):
        code, body = harness.post("/api/auth/xai/logout", {})
    assert code == 200
    assert body["ok"] is True
    assert body.get("oauth_configured") is False
    assert load_oauth_bundle_optional(data_dir) is None
    _assert_no_secrets(body)


def test_api_provider_unavailable(harness_no_provider: _ApiHarness) -> None:
    code, body = harness_no_provider.post("/api/auth/xai/device/start", {})
    assert code == 503
    assert body.get("ok") is False
    assert "provider" in (body.get("error") or "").lower()

    code, body = harness_no_provider.get("/api/auth/xai/device/status")
    assert code == 503

    # GET public meta still works without provider (disk only).
    code, body = harness_no_provider.get("/api/auth/xai")
    assert code == 200
    assert body["ok"] is True
    assert body["configured"] is False


def test_api_origin_loopback_ok(harness: _ApiHarness) -> None:
    with (
        patch(
            "elyra.runtime.oauth_session.request_device_code",
            return_value=_device(interval=1, expires_in=120),
        ),
        patch(
            "elyra.runtime.oauth_session.poll_device_token",
            return_value=_pending(),
        ),
    ):
        code, body = harness.post(
            "/api/auth/xai/device/start",
            {},
            headers={"Origin": "http://127.0.0.1:8080"},
        )
        assert code == 200
        assert body["ok"] is True
        harness.post("/api/auth/xai/device/cancel", {})


def test_api_origin_non_loopback_forbidden(harness: _ApiHarness) -> None:
    code, body = harness.post(
        "/api/auth/xai/device/start",
        {},
        headers={"Origin": "https://evil.example"},
    )
    assert code == 403
    assert body.get("ok") is False
    assert body.get("error") == "origin_not_allowed"


def test_api_invalid_activate_body(harness: _ApiHarness) -> None:
    code, body = harness.post("/api/auth/xai/device/start", {"activate": "yes"})
    assert code == 400
    assert body.get("ok") is False


def test_api_never_returns_device_code_even_if_session_buggy(
    harness: _ApiHarness,
) -> None:
    """Defense in depth: API strips secret keys from any payload."""
    assert harness.provider is not None
    sess = harness.provider._get_oauth_device_session()
    # Force-poison private field; public status must still not leak.
    sess._device_code = "SECRET-DEVICE-CODE-NEVER-RETURN"  # type: ignore[attr-defined]
    with patch.object(
        type(sess),
        "status",
        return_value={
            "ok": True,
            "state": STATE_PENDING,
            "device_code": "SECRET-DEVICE-CODE-NEVER-RETURN",
            "access_token": "access-login-leaked",
        },
    ):
        code, body = harness.get("/api/auth/xai/device/status")
    assert code == 200
    assert "device_code" not in body
    assert "access_token" not in body
    _assert_no_secrets(body)
