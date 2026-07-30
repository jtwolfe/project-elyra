"""Hermetic unit tests for xAI OIDC protocol client (PR1).

Mocks urllib urlopen — never hits the network.
"""

from __future__ import annotations

import base64
import io
import json
import threading
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.llm.oauth_store import (
    OAuthBundle,
    load_oauth_bundle,
    save_oauth_bundle,
)
from elyra.llm.xai_oauth import (
    DETAIL_AUTHORIZATION_PENDING,
    DETAIL_INVALID_OAUTH_TOKENS,
    DETAIL_MISSING_OAUTH_TOKENS,
    DETAIL_OAUTH_DENIED,
    DETAIL_OAUTH_DEVICE_EXPIRED,
    DETAIL_OAUTH_REAUTH_REQUIRED,
    DETAIL_OAUTH_REFRESH_FAILED,
    DETAIL_SLOW_DOWN,
    XAI_DEVICE_GRANT,
    XAI_OAUTH_CLIENT_ID,
    XAI_OAUTH_SCOPE,
    XAI_OIDC_DISCOVERY,
    XAI_TOKEN_URL,
    claims_from_id_token,
    clear_discovery_cache,
    email_and_subject_from_id_token,
    ensure_fresh_access,
    fetch_discovery,
    next_poll_interval,
    poll_device_token,
    refresh_access_token,
    request_device_code,
)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return paths.data_dir


@pytest.fixture(autouse=True)
def _clear_discovery() -> None:
    clear_discovery_cache()
    yield
    clear_discovery_cache()


def _future(hours: float = 2.0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past(hours: float = 1.0) -> str:
    return (
        datetime.now(timezone.utc) - timedelta(hours=hours)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _near(seconds: float = 30.0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jwt(payload: dict[str, Any]) -> str:
    def b64(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64(payload)}.sig"


class _FakeResp:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _json_bytes(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj).encode("utf-8")


def _urlopen_router(routes: dict[str, Any]):
    """Build a urlopen that dispatches by URL substring / exact key.

    routes values:
      - dict → 200 JSON body
      - (status, dict) → HTTPError with JSON body (or 200 if status==200)
      - callable(req) → return value
    """

    def urlopen(req: Any, timeout: float = 30.0) -> Any:
        url = getattr(req, "full_url", None) or req.get_full_url()
        for key, val in routes.items():
            if key in url:
                if callable(val) and not isinstance(val, type):
                    return val(req)
                if isinstance(val, tuple):
                    status, body = val
                    raw = _json_bytes(body) if isinstance(body, dict) else body
                    if status == 200:
                        return _FakeResp(raw, 200)
                    raise urllib.error.HTTPError(
                        url, status, "err", hdrs=None, fp=io.BytesIO(raw)  # type: ignore[arg-type]
                    )
                return _FakeResp(_json_bytes(val), 200)
        raise AssertionError(f"unexpected url: {url}")

    return urlopen


# --- discovery ---


def test_fetch_discovery_caches(tmp_path: Path) -> None:
    calls = {"n": 0}

    def urlopen(req: Any, timeout: float = 30.0) -> Any:
        calls["n"] += 1
        return _FakeResp(
            _json_bytes(
                {
                    "issuer": "https://auth.x.ai",
                    "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                    "token_endpoint": "https://auth.x.ai/oauth2/token",
                }
            )
        )

    d1 = fetch_discovery(urlopen=urlopen)
    d2 = fetch_discovery(urlopen=urlopen)
    assert d1.token_endpoint == XAI_TOKEN_URL
    assert d1.device_authorization_endpoint.endswith("/device/code")
    assert d2 is d1 or d2.token_endpoint == d1.token_endpoint
    assert calls["n"] == 1


def test_fetch_discovery_fallback_on_failure() -> None:
    def urlopen(req: Any, timeout: float = 30.0) -> Any:
        raise urllib.error.URLError("down")

    doc = fetch_discovery(urlopen=urlopen, use_cache=False)
    assert doc.device_authorization_endpoint
    assert doc.token_endpoint


# --- device code ---


def test_request_device_code() -> None:
    urlopen = _urlopen_router(
        {
            "openid-configuration": {
                "issuer": "https://auth.x.ai",
                "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                "token_endpoint": "https://auth.x.ai/oauth2/token",
            },
            "/device/code": {
                "device_code": "dc-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://auth.x.ai/device",
                "verification_uri_complete": "https://auth.x.ai/device?user_code=ABCD-EFGH",
                "expires_in": 600,
                "interval": 5,
            },
        }
    )
    resp = request_device_code(urlopen=urlopen)
    assert resp.device_code == "dc-secret"
    assert resp.user_code == "ABCD-EFGH"
    assert resp.verification_uri == "https://auth.x.ai/device"
    assert resp.verification_uri_complete is not None
    assert resp.expires_in == 600
    assert resp.interval == 5


# --- token poll ---


def test_poll_device_token_pending_and_success() -> None:
    urlopen = _urlopen_router(
        {
            "openid-configuration": {
                "issuer": "https://auth.x.ai",
                "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                "token_endpoint": "https://auth.x.ai/oauth2/token",
            },
            "/token": (400, {"error": "authorization_pending"}),
        }
    )
    r = poll_device_token("dc", urlopen=urlopen)
    assert r.pending is True
    assert r.ok is False
    assert r.detail == DETAIL_AUTHORIZATION_PENDING

    urlopen2 = _urlopen_router(
        {
            "openid-configuration": {
                "issuer": "https://auth.x.ai",
                "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                "token_endpoint": "https://auth.x.ai/oauth2/token",
            },
            "/token": {
                "access_token": "atk",
                "refresh_token": "rtk",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": XAI_OAUTH_SCOPE,
                "id_token": _jwt({"email": "op@example.com", "sub": "sub1"}),
            },
        }
    )
    clear_discovery_cache()
    r2 = poll_device_token("dc", urlopen=urlopen2)
    assert r2.ok is True
    assert r2.access_token == "atk"
    assert r2.refresh_token == "rtk"
    email, sub = email_and_subject_from_id_token(r2.id_token)
    assert email == "op@example.com"
    assert sub == "sub1"


def test_poll_slow_down_denied_expired() -> None:
    for err, detail, slow in (
        ("slow_down", DETAIL_SLOW_DOWN, True),
        ("access_denied", DETAIL_OAUTH_DENIED, False),
        ("expired_token", DETAIL_OAUTH_DEVICE_EXPIRED, False),
    ):
        clear_discovery_cache()
        urlopen = _urlopen_router(
            {
                "openid-configuration": {
                    "issuer": "https://auth.x.ai",
                    "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                    "token_endpoint": "https://auth.x.ai/oauth2/token",
                },
                "/token": (400, {"error": err}),
            }
        )
        r = poll_device_token("dc", urlopen=urlopen)
        assert r.detail == detail
        assert r.slow_down is slow
        assert r.ok is False


def test_next_poll_interval_caps() -> None:
    assert next_poll_interval(5, slow_down=False) == 5
    assert next_poll_interval(5, slow_down=True) == 10
    assert next_poll_interval(58, slow_down=True) == 60
    assert next_poll_interval(100, slow_down=False) == 60


def test_claims_from_id_token_malformed() -> None:
    assert claims_from_id_token(None) == {}
    assert claims_from_id_token("not-a-jwt") == {}
    assert claims_from_id_token("") == {}


# --- ensure_fresh_access ---


def _bundle(
    *,
    access: str = "access-1",
    refresh: str | None = "refresh-1",
    expires_at: str | None = None,
    reauth: bool = False,
    email: str | None = "a@b.c",
) -> OAuthBundle:
    return OAuthBundle(
        version=1,
        client_id=XAI_OAUTH_CLIENT_ID,
        access_token=access,
        refresh_token=refresh,
        token_type="Bearer",
        scope=XAI_OAUTH_SCOPE,
        expires_at=expires_at if expires_at is not None else _future(),
        email=email,
        subject="sub",
        obtained_at=_past(hours=2),
        updated_at=_past(hours=1),
        auth_method="device_code",
        reauth_required=reauth,
    )


def test_ensure_fresh_missing(data_dir: Path) -> None:
    r = ensure_fresh_access(data_dir)
    assert r.ok is False
    assert r.detail == DETAIL_MISSING_OAUTH_TOKENS
    assert r.access_token is None
    assert r.rotated is False


def test_ensure_fresh_still_valid_no_http(data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle(expires_at=_future(hours=2)))

    def urlopen(req: Any, timeout: float = 30.0) -> Any:
        raise AssertionError("should not hit network when still fresh")

    r = ensure_fresh_access(data_dir, skew_s=120, urlopen=urlopen)
    assert r.ok is True
    assert r.access_token == "access-1"
    assert r.rotated is False


def test_ensure_fresh_refresh_success(data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle(expires_at=_near(30)))

    urlopen = _urlopen_router(
        {
            "openid-configuration": {
                "issuer": "https://auth.x.ai",
                "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                "token_endpoint": "https://auth.x.ai/oauth2/token",
            },
            "/token": {
                "access_token": "access-2",
                "refresh_token": "refresh-2",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        }
    )
    r = ensure_fresh_access(data_dir, skew_s=120, urlopen=urlopen)
    assert r.ok is True
    assert r.access_token == "access-2"
    assert r.rotated is True
    loaded = load_oauth_bundle(data_dir)
    assert loaded.access_token == "access-2"
    assert loaded.refresh_token == "refresh-2"
    assert loaded.reauth_required is False


def test_ensure_fresh_invalid_grant_sets_reauth(data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle(expires_at=_near(10)))

    urlopen = _urlopen_router(
        {
            "openid-configuration": {
                "issuer": "https://auth.x.ai",
                "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                "token_endpoint": "https://auth.x.ai/oauth2/token",
            },
            "/token": (400, {"error": "invalid_grant"}),
        }
    )
    r = ensure_fresh_access(data_dir, skew_s=120, urlopen=urlopen)
    assert r.ok is False
    assert r.access_token is None
    assert r.detail == DETAIL_OAUTH_REAUTH_REQUIRED
    assert r.rotated is False
    loaded = load_oauth_bundle(data_dir)
    assert loaded.reauth_required is True
    # tokens retained for forensics
    assert loaded.access_token == "access-1"
    assert loaded.refresh_token == "refresh-1"

    # Cold start: reauth_required durable → still fail even if JWT unexpired
    r2 = ensure_fresh_access(
        data_dir,
        skew_s=120,
        urlopen=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no net")),
    )
    assert r2.ok is False
    assert r2.detail == DETAIL_OAUTH_REAUTH_REQUIRED
    assert r2.access_token is None


def test_ensure_fresh_transient_grace_when_access_valid(data_dir: Path) -> None:
    # Expires in 60s, skew 120 → needs refresh, but access still not expired
    save_oauth_bundle(data_dir, _bundle(expires_at=_near(60)))

    def urlopen(req: Any, timeout: float = 30.0) -> Any:
        url = req.get_full_url()
        if "openid-configuration" in url:
            return _FakeResp(
                _json_bytes(
                    {
                        "issuer": "https://auth.x.ai",
                        "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                        "token_endpoint": "https://auth.x.ai/oauth2/token",
                    }
                )
            )
        raise urllib.error.URLError("timeout")

    r = ensure_fresh_access(data_dir, skew_s=120, urlopen=urlopen)
    assert r.ok is True
    assert r.access_token == "access-1"
    assert r.rotated is False


def test_ensure_fresh_transient_fail_when_expired(data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle(expires_at=_past(1)))

    def urlopen(req: Any, timeout: float = 30.0) -> Any:
        url = req.get_full_url()
        if "openid-configuration" in url:
            return _FakeResp(
                _json_bytes(
                    {
                        "issuer": "https://auth.x.ai",
                        "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                        "token_endpoint": "https://auth.x.ai/oauth2/token",
                    }
                )
            )
        raise urllib.error.URLError("timeout")

    r = ensure_fresh_access(data_dir, skew_s=120, urlopen=urlopen)
    assert r.ok is False
    assert r.detail == DETAIL_OAUTH_REFRESH_FAILED
    assert r.access_token is None


def test_ensure_fresh_invalid_schema(data_dir: Path) -> None:
    path = data_dir / "secrets" / "xai_oauth.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    r = ensure_fresh_access(data_dir)
    assert r.ok is False
    assert r.detail == DETAIL_INVALID_OAUTH_TOKENS


def test_ensure_fresh_force_refreshes(data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle(expires_at=_future(hours=5)))
    urlopen = _urlopen_router(
        {
            "openid-configuration": {
                "issuer": "https://auth.x.ai",
                "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                "token_endpoint": "https://auth.x.ai/oauth2/token",
            },
            "/token": {
                "access_token": "forced",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        }
    )
    r = ensure_fresh_access(data_dir, force=True, urlopen=urlopen)
    assert r.ok is True
    assert r.access_token == "forced"
    assert r.rotated is True
    # refresh_token retained when response omits it
    assert load_oauth_bundle(data_dir).refresh_token == "refresh-1"


def test_ensure_fresh_single_flight(data_dir: Path) -> None:
    """Concurrent ensure_fresh_access serializes via process lock (no overlap)."""
    save_oauth_bundle(data_dir, _bundle(expires_at=_near(10)))
    calls = {"n": 0, "in_flight": 0, "max_in_flight": 0}
    lock = threading.Lock()

    def urlopen(req: Any, timeout: float = 30.0) -> Any:
        url = req.get_full_url()
        if "openid-configuration" in url:
            return _FakeResp(
                _json_bytes(
                    {
                        "issuer": "https://auth.x.ai",
                        "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                        "token_endpoint": "https://auth.x.ai/oauth2/token",
                    }
                )
            )
        with lock:
            calls["n"] += 1
            calls["in_flight"] += 1
            calls["max_in_flight"] = max(calls["max_in_flight"], calls["in_flight"])
            n = calls["n"]
        # Hold the "network" long enough that a concurrent call would overlap
        # without the ensure_fresh single-flight lock.
        import time

        time.sleep(0.05)
        with lock:
            calls["in_flight"] -= 1
        return _FakeResp(
            _json_bytes(
                {
                    "access_token": f"rot-{n}",
                    "refresh_token": "r2",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            )
        )

    results: list[Any] = [None, None]

    def worker(i: int) -> None:
        results[i] = ensure_fresh_access(data_dir, skew_s=120, urlopen=urlopen)

    t0 = threading.Thread(target=worker, args=(0,))
    t1 = threading.Thread(target=worker, args=(1,))
    t0.start()
    t1.start()
    t0.join(timeout=10)
    t1.join(timeout=10)
    assert results[0] is not None and results[1] is not None
    assert results[0].ok and results[1].ok
    # Token posts never overlap (single-flight lock).
    assert calls["max_in_flight"] == 1
    # First refresh writes a long-lived token; second typically short-circuits
    # after lock release (still fresh) → 1 token post. If both refresh, 2 is ok.
    assert 1 <= calls["n"] <= 2
    loaded = load_oauth_bundle(data_dir)
    assert loaded.access_token.startswith("rot-")


def test_refresh_access_token_direct() -> None:
    urlopen = _urlopen_router(
        {
            "openid-configuration": {
                "issuer": "https://auth.x.ai",
                "device_authorization_endpoint": "https://auth.x.ai/oauth2/device/code",
                "token_endpoint": "https://auth.x.ai/oauth2/token",
            },
            "/token": {
                "access_token": "new",
                "expires_in": 100,
                "token_type": "Bearer",
            },
        }
    )
    r = refresh_access_token("rt", urlopen=urlopen)
    assert r.ok is True
    assert r.access_token == "new"


def test_constants_match_openclaw() -> None:
    assert XAI_OAUTH_CLIENT_ID == "b1a00492-073a-47ea-816f-4c329264a828"
    assert "offline_access" in XAI_OAUTH_SCOPE
    assert "grok-cli:access" in XAI_OAUTH_SCOPE
    assert XAI_DEVICE_GRANT.startswith("urn:ietf:params:oauth:grant-type:")
    assert XAI_OIDC_DISCOVERY.endswith("openid-configuration")
