"""Hermetic tests for CLI ``elyra auth login|logout|status`` (PR5a).

Mocks xAI protocol helpers — never hits the network. Asserts paths-only
``persist_oauth_login`` (no ProviderRuntime construction).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from elyra.cli import (
    _LIVE_REBIND_NOTE,
    build_parser,
    main,
    run_auth_login,
    run_auth_logout,
    run_auth_status,
)
from elyra.config import resolve_paths
from elyra.llm.oauth_store import (
    OAuthBundle,
    load_oauth_bundle,
    oauth_is_configured,
    oauth_path,
    persist_oauth_login,
    public_meta,
    save_oauth_bundle,
)
from elyra.llm.provider_prefs import load_provider_prefs
from elyra.llm.xai_oauth import (
    DETAIL_AUTHORIZATION_PENDING,
    DETAIL_OAUTH_DENIED,
    DETAIL_OAUTH_DEVICE_EXPIRED,
    XAI_OAUTH_CLIENT_ID,
    XAI_OAUTH_SCOPE,
    DeviceCodeResponse,
    TokenPollResult,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("ELYRA_HOME", str(tmp_path))
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def data_dir(home: Path) -> Path:
    return resolve_paths(home).data_dir


def _device(
    *,
    expires_in: int = 600,
    interval: int = 1,
) -> DeviceCodeResponse:
    return DeviceCodeResponse(
        device_code="secret-device-code-never-print",
        user_code="ABCD-EFGH",
        verification_uri="https://auth.x.ai/device",
        verification_uri_complete="https://auth.x.ai/device?user_code=ABCD-EFGH",
        expires_in=expires_in,
        interval=interval,
    )


def _ok_poll(*, email: str = "op@example.com") -> TokenPollResult:
    # Minimal id_token with email claim (unverified decode).
    import base64

    def b64(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    id_token = f"{b64({'alg': 'none'})}.{b64({'email': email, 'sub': 'sub-1'})}.sig"
    return TokenPollResult(
        ok=True,
        pending=False,
        slow_down=False,
        access_token="access-token-secret-cli",
        refresh_token="refresh-token-secret-cli",
        expires_in=3600,
        token_type="Bearer",
        scope=XAI_OAUTH_SCOPE,
        id_token=id_token,
        detail=None,
        error=None,
    )


def _pending_poll(*, slow_down: bool = False) -> TokenPollResult:
    return TokenPollResult(
        ok=False,
        pending=True,
        slow_down=slow_down,
        access_token=None,
        refresh_token=None,
        expires_in=None,
        token_type=None,
        scope=None,
        id_token=None,
        detail=DETAIL_AUTHORIZATION_PENDING,
        error="authorization_pending",
    )


def _denied_poll() -> TokenPollResult:
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


def _bundle() -> OAuthBundle:
    return OAuthBundle(
        version=1,
        client_id=XAI_OAUTH_CLIENT_ID,
        access_token="access-token-secret",
        refresh_token="refresh-token-secret",
        token_type="Bearer",
        scope=XAI_OAUTH_SCOPE,
        expires_at="2026-07-30T18:00:00Z",
        email="op@example.com",
        subject="sub-1",
        obtained_at="2026-07-30T12:00:00Z",
        updated_at="2026-07-30T12:00:00Z",
        auth_method="device_code",
        reauth_required=False,
    )


# --- parser ---


def test_parser_auth_subcommands() -> None:
    p = build_parser()
    ns = p.parse_args(["auth", "login", "--no-activate", "--timeout-s", "30"])
    assert ns.command == "auth"
    assert ns.auth_command == "login"
    assert ns.no_activate is True
    assert ns.timeout_s == 30.0

    ns2 = p.parse_args(["auth", "logout"])
    assert ns2.auth_command == "logout"

    ns3 = p.parse_args(["auth", "status"])
    assert ns3.auth_command == "status"


def test_parser_start_still_works() -> None:
    p = build_parser()
    ns = p.parse_args(["start", "--stub-llm", "--api-port", "9999"])
    assert ns.command == "start"
    assert ns.stub_llm is True
    assert ns.api_port == 9999


# --- login ---


def test_login_success_persist_activate(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    polls = [_pending_poll(), _ok_poll()]
    sleep_calls: list[float] = []

    monkeypatch.setattr(
        "elyra.cli.request_device_code",
        lambda **kwargs: _device(interval=1),
    )
    monkeypatch.setattr(
        "elyra.cli.poll_device_token",
        lambda *a, **k: polls.pop(0),
    )

    out = io.StringIO()
    err = io.StringIO()
    rc = run_auth_login(
        data_dir,
        activate=True,
        sleep=lambda s: sleep_calls.append(s),
        out=out,
        err=err,
    )
    assert rc == 0
    text = out.getvalue()
    assert "ABCD-EFGH" in text
    assert "https://auth.x.ai/device" in text
    assert "secret-device-code" not in text
    assert "access-token-secret-cli" not in text
    assert "refresh-token-secret-cli" not in text
    assert "Login OK" in text
    assert "op@example.com" in text
    assert _LIVE_REBIND_NOTE in text
    assert sleep_calls  # waited once for pending

    assert oauth_is_configured(data_dir)
    bundle = load_oauth_bundle(data_dir)
    assert bundle.access_token == "access-token-secret-cli"
    assert bundle.refresh_token == "refresh-token-secret-cli"
    assert bundle.email == "op@example.com"
    assert bundle.reauth_required is False
    assert bundle.auth_method == "device_code"

    prefs = load_provider_prefs(data_dir)
    assert prefs.credential_source == "xai_oauth"


def test_login_no_activate_leaves_source(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "elyra.cli.request_device_code",
        lambda **kwargs: _device(),
    )
    monkeypatch.setattr(
        "elyra.cli.poll_device_token",
        lambda *a, **k: _ok_poll(),
    )
    out = io.StringIO()
    rc = run_auth_login(
        data_dir,
        activate=False,
        sleep=lambda s: None,
        out=out,
        err=io.StringIO(),
    )
    assert rc == 0
    assert oauth_is_configured(data_dir)
    prefs = load_provider_prefs(data_dir)
    assert prefs.credential_source is None
    assert "activate:         false" in out.getvalue()


def test_login_denied(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "elyra.cli.request_device_code",
        lambda **kwargs: _device(),
    )
    monkeypatch.setattr(
        "elyra.cli.poll_device_token",
        lambda *a, **k: _denied_poll(),
    )
    err = io.StringIO()
    rc = run_auth_login(
        data_dir,
        sleep=lambda s: None,
        out=io.StringIO(),
        err=err,
    )
    assert rc == 3
    assert DETAIL_OAUTH_DENIED in err.getvalue()
    assert not oauth_is_configured(data_dir)


def test_login_device_start_failure(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**kwargs: Any) -> DeviceCodeResponse:
        raise ValueError("device_code_failed:unavailable")

    monkeypatch.setattr("elyra.cli.request_device_code", boom)
    err = io.StringIO()
    rc = run_auth_login(
        data_dir,
        sleep=lambda s: None,
        out=io.StringIO(),
        err=err,
    )
    assert rc == 2
    assert "FAIL device start" in err.getvalue()


def test_login_timeout_expires(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wall-clock deadline ends before success → oauth_device_expired."""
    monkeypatch.setattr(
        "elyra.cli.request_device_code",
        lambda **kwargs: _device(expires_in=2, interval=1),
    )
    monkeypatch.setattr(
        "elyra.cli.poll_device_token",
        lambda *a, **k: _pending_poll(),
    )
    # Force deadline already passed after first loop check via timeout_s tiny
    # and sleep that advances monotonic by not actually waiting — use timeout_s
    # that is positive but sleep drains the wall by monkeypatching monotonic.
    mono = {"t": 1000.0}

    def fake_mono() -> float:
        return mono["t"]

    def fake_sleep(s: float) -> None:
        mono["t"] += float(s) + 0.1

    monkeypatch.setattr("elyra.cli.time.monotonic", fake_mono)
    err = io.StringIO()
    rc = run_auth_login(
        data_dir,
        timeout_s=1.0,
        sleep=fake_sleep,
        out=io.StringIO(),
        err=err,
    )
    assert rc == 5
    assert DETAIL_OAUTH_DEVICE_EXPIRED in err.getvalue()


def test_login_uses_persist_only_not_complete_oauth(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI login must call persist_oauth_login, never complete_oauth_login."""
    monkeypatch.setattr(
        "elyra.cli.request_device_code",
        lambda **kwargs: _device(),
    )
    monkeypatch.setattr(
        "elyra.cli.poll_device_token",
        lambda *a, **k: _ok_poll(),
    )

    persist_calls: list[tuple[Any, ...]] = []
    real_persist = persist_oauth_login

    def tracking_persist(data_dir_arg: Path, tokens: Any, *, activate: bool = True) -> Any:
        persist_calls.append((data_dir_arg, activate))
        return real_persist(data_dir_arg, tokens, activate=activate)

    monkeypatch.setattr("elyra.cli.persist_oauth_login", tracking_persist)

    # complete_oauth_login lives on ProviderRuntime — ensure CLI module does not call it.
    assert not hasattr(
        __import__("elyra.cli", fromlist=["*"]),
        "complete_oauth_login",
    )

    rc = run_auth_login(
        data_dir,
        activate=True,
        sleep=lambda s: None,
        out=io.StringIO(),
        err=io.StringIO(),
    )
    assert rc == 0
    assert len(persist_calls) == 1
    assert persist_calls[0][0] == data_dir
    assert persist_calls[0][1] is True
    assert oauth_is_configured(data_dir)


def test_main_auth_login_via_elyra_home(
    home: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "elyra.cli.request_device_code",
        lambda **kwargs: _device(),
    )
    monkeypatch.setattr(
        "elyra.cli.poll_device_token",
        lambda *a, **k: _ok_poll(email="main@example.com"),
    )
    # Avoid real sleep if any pending path; success first poll.
    monkeypatch.setattr("elyra.cli.time.sleep", lambda s: None)

    out = io.StringIO()
    err = io.StringIO()
    with patch("sys.stdout", out), patch("sys.stderr", err):
        rc = main(["auth", "login"])
    assert rc == 0
    assert oauth_is_configured(data_dir)
    assert load_oauth_bundle(data_dir).email == "main@example.com"
    blob = out.getvalue()
    assert "secret-device-code" not in blob
    assert "access-token-secret" not in blob
    assert "refresh-token-secret" not in blob


# --- logout / status ---


def test_logout_removes_bundle(data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle())
    assert oauth_path(data_dir).is_file()
    out = io.StringIO()
    rc = run_auth_logout(data_dir, out=out)
    assert rc == 0
    assert not oauth_is_configured(data_dir)
    assert "bundle_removed:    true" in out.getvalue()
    assert "oauth_configured:  false" in out.getvalue()


def test_logout_idempotent_when_missing(data_dir: Path) -> None:
    out = io.StringIO()
    rc = run_auth_logout(data_dir, out=out)
    assert rc == 0
    assert "bundle_removed:    false" in out.getvalue()


def test_status_unconfigured(data_dir: Path) -> None:
    out = io.StringIO()
    rc = run_auth_status(data_dir, out=out)
    assert rc == 0
    text = out.getvalue()
    assert "oauth_configured:   false" in text
    assert "access-token" not in text
    assert "credential_source:  (unset)" in text


def test_status_configured_public_only(data_dir: Path) -> None:
    persist_oauth_login(data_dir, _bundle(), activate=True)
    out = io.StringIO()
    rc = run_auth_status(data_dir, out=out)
    assert rc == 0
    text = out.getvalue()
    assert "oauth_configured:   true" in text
    assert "op@example.com" in text
    assert "credential_source:  xai_oauth" in text
    assert "access-token-secret" not in text
    assert "refresh-token-secret" not in text
    # Meta must match store public_meta
    meta = public_meta(data_dir)
    assert meta.email == "op@example.com"
    assert meta.configured is True


def test_main_auth_status_and_logout(
    home: Path, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_oauth_bundle(data_dir, _bundle())
    out = io.StringIO()
    with patch("sys.stdout", out):
        assert main(["auth", "status"]) == 0
        assert main(["auth", "logout"]) == 0
        assert main(["auth", "status"]) == 0
    text = out.getvalue()
    assert "oauth_configured:   true" in text
    assert "oauth_configured:   false" in text
    assert "access-token-secret" not in text
    assert not oauth_is_configured(data_dir)


def test_login_help_mentions_rebind() -> None:
    p = build_parser()
    # Find login subparser help text
    auth = None
    for action in p._subparsers._group_actions:  # noqa: SLF001
        if action.dest == "command":
            auth = action.choices.get("auth")
            break
    assert auth is not None
    login = None
    for action in auth._subparsers._group_actions:  # noqa: SLF001
        if action.dest == "auth_command":
            login = action.choices.get("login")
            break
    assert login is not None
    help_text = login.format_help()
    assert "persist_oauth_login" in help_text or "disk" in help_text.lower()
    assert "restart" in help_text.lower() or "Glass" in help_text
