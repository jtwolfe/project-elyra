"""xAI OIDC protocol client (device-code + refresh).

Scope: discovery, device authorization, token poll helpers, refresh, and pure
``ensure_fresh_access`` with process-wide single-flight lock.
Out of scope: Glass UI, resolve_bearer wiring, ProviderRuntime rebind, device
session poller thread (see runtime/oauth_session in later PRs).

Uses stdlib urllib only (match ``HttpChatClient``). Never logs tokens or
``device_code``. Import rule: may import ``oauth_store``; must not import
runtime / client.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from elyra.llm.oauth_store import (
    OAuthBundle,
    load_oauth_bundle,
    save_oauth_bundle,
)

logger = logging.getLogger(__name__)

# --- OpenClaw-compatible public OIDC client (protocol constants) ---

XAI_OIDC_ISSUER = "https://auth.x.ai"
XAI_OIDC_DISCOVERY = "https://auth.x.ai/.well-known/openid-configuration"
XAI_OAUTH_CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
XAI_OAUTH_SCOPE = "openid profile email offline_access grok-cli:access api:access"
XAI_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
XAI_DEVICE_CODE_URL = "https://auth.x.ai/oauth2/device/code"  # fallback
XAI_TOKEN_URL = "https://auth.x.ai/oauth2/token"  # fallback

DEFAULT_SKEW_S = 120
DEFAULT_HTTP_TIMEOUT_S = 30.0
MAX_POLL_INTERVAL_S = 60
SLOW_DOWN_INCREMENT_S = 5

# Status-safe detail codes (never secrets)
DETAIL_MISSING_OAUTH_TOKENS = "missing_oauth_tokens"
DETAIL_INVALID_OAUTH_TOKENS = "invalid_oauth_tokens"
DETAIL_OAUTH_TOKEN_EXPIRED = "oauth_token_expired"
DETAIL_OAUTH_REFRESH_FAILED = "oauth_refresh_failed"
DETAIL_OAUTH_REAUTH_REQUIRED = "oauth_reauth_required"
DETAIL_OAUTH_DENIED = "oauth_denied"
DETAIL_OAUTH_DEVICE_EXPIRED = "oauth_device_expired"
DETAIL_OAUTH_INELIGIBLE = "oauth_ineligible"
DETAIL_OAUTH_PENDING = "oauth_pending"
DETAIL_AUTHORIZATION_PENDING = "authorization_pending"
DETAIL_SLOW_DOWN = "slow_down"
DETAIL_NETWORK = "oauth_network_error"

UrlOpenFn = Callable[..., Any]

# Process-lifetime discovery cache + single-flight refresh lock.
_discovery_lock = threading.Lock()
_discovery_cache: DiscoveryDocument | None = None
_refresh_lock = threading.Lock()


@dataclass(frozen=True)
class DiscoveryDocument:
    """Cached OpenID discovery fields we care about."""

    issuer: str
    device_authorization_endpoint: str
    token_endpoint: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class DeviceCodeResponse:
    """Device authorization start result (holds device_code in-memory only)."""

    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int


@dataclass(frozen=True)
class TokenPollResult:
    """One token-endpoint poll outcome for device grant or refresh."""

    ok: bool
    pending: bool
    slow_down: bool
    access_token: str | None
    refresh_token: str | None
    expires_in: int | None
    token_type: str | None
    scope: str | None
    id_token: str | None
    detail: str | None
    error: str | None


@dataclass(frozen=True)
class FreshAccessResult:
    """Pure ensure_fresh_access result — no rebind side effects.

    ``rotated`` is True iff this call wrote a new access_token to disk.
    """

    ok: bool
    access_token: str | None
    expires_at: str | None
    email: str | None
    detail: str | None
    rotated: bool


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso_z() -> str:
    return _utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def expires_at_from_expires_in(expires_in: int, *, now: datetime | None = None) -> str:
    """Return ISO-Z expiry timestamp from ``expires_in`` seconds."""
    base = now if now is not None else _utc_now()
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    exp = base + timedelta(seconds=int(expires_in))
    return exp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def seconds_until_expiry(expires_at: str | None, *, now: datetime | None = None) -> float | None:
    exp = parse_iso_utc(expires_at)
    if exp is None:
        return None
    base = now if now is not None else _utc_now()
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return (exp - base.astimezone(timezone.utc)).total_seconds()


def claims_from_id_token(id_token: str | None) -> dict[str, Any]:
    """Decode JWT payload claims without verification (email/sub only).

    Fail-soft: malformed tokens yield {}. Never raises.
    """
    if not id_token or not isinstance(id_token, str):
        return {}
    parts = id_token.split(".")
    if len(parts) < 2:
        return {}
    payload_b64 = parts[1]
    # base64url padding
    pad = "=" * (-len(payload_b64) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload_b64 + pad)
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def email_and_subject_from_id_token(
    id_token: str | None,
) -> tuple[str | None, str | None]:
    claims = claims_from_id_token(id_token)
    email = claims.get("email")
    sub = claims.get("sub")
    email_s = email.strip() if isinstance(email, str) and email.strip() else None
    sub_s = sub.strip() if isinstance(sub, str) and sub.strip() else None
    return email_s, sub_s


def _default_urlopen(req: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(req, timeout=timeout)


def _http_get_json(
    url: str,
    *,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    urlopen: UrlOpenFn | None = None,
) -> dict[str, Any]:
    open_fn = urlopen if urlopen is not None else _default_urlopen
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": "elyra-xai-oauth/0.1",
        },
    )
    with open_fn(req, timeout=float(timeout)) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("discovery_not_object")
    return data


def _http_form_post(
    url: str,
    fields: dict[str, str],
    *,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    urlopen: UrlOpenFn | None = None,
) -> tuple[int, dict[str, Any]]:
    """POST application/x-www-form-urlencoded; return (status, json_object).

    HTTPError bodies that are JSON are returned as (code, body) rather than
    raising, so device-poll pending/denied paths stay structured.
    """
    open_fn = urlopen if urlopen is not None else _default_urlopen
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "elyra-xai-oauth/0.1",
        },
    )
    try:
        with open_fn(req, timeout=float(timeout)) as resp:
            raw = resp.read().decode("utf-8")
            code = int(getattr(resp, "status", None) or resp.getcode() or 200)
    except urllib.error.HTTPError as exc:
        code = int(exc.code)
        try:
            raw = exc.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            raw = ""
        data: dict[str, Any] = {}
        if raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    data = parsed
            except json.JSONDecodeError:
                data = {"error": "http_error", "error_description": raw[:200]}
        else:
            data = {"error": "http_error", "error_description": f"HTTP {code}"}
        return code, data
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        raise OSError(f"network:{reason!s}"[:200] if reason is not None else "network") from exc

    if not raw.strip():
        return code, {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"token_response_not_json:{raw[:80]!r}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("token_response_not_object")
    return code, parsed


def clear_discovery_cache() -> None:
    """Test helper: drop process-lifetime discovery cache."""
    global _discovery_cache
    with _discovery_lock:
        _discovery_cache = None


def fetch_discovery(
    *,
    discovery_url: str = XAI_OIDC_DISCOVERY,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    urlopen: UrlOpenFn | None = None,
    use_cache: bool = True,
) -> DiscoveryDocument:
    """GET OpenID discovery document; cache for process lifetime.

    On failure with empty cache, returns a document built from fallbacks.
    """
    global _discovery_cache
    if use_cache:
        with _discovery_lock:
            if _discovery_cache is not None:
                return _discovery_cache

    try:
        raw = _http_get_json(discovery_url, timeout=timeout, urlopen=urlopen)
        device_ep = raw.get("device_authorization_endpoint")
        token_ep = raw.get("token_endpoint")
        issuer = raw.get("issuer") or XAI_OIDC_ISSUER
        if not isinstance(device_ep, str) or not device_ep.strip():
            device_ep = XAI_DEVICE_CODE_URL
        if not isinstance(token_ep, str) or not token_ep.strip():
            token_ep = XAI_TOKEN_URL
        doc = DiscoveryDocument(
            issuer=str(issuer),
            device_authorization_endpoint=device_ep.strip(),
            token_endpoint=token_ep.strip(),
            raw=raw,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("xai oauth discovery failed (%s); using fallbacks", type(exc).__name__)
        with _discovery_lock:
            if _discovery_cache is not None:
                return _discovery_cache
        doc = DiscoveryDocument(
            issuer=XAI_OIDC_ISSUER,
            device_authorization_endpoint=XAI_DEVICE_CODE_URL,
            token_endpoint=XAI_TOKEN_URL,
            raw={},
        )
        return doc

    if use_cache:
        with _discovery_lock:
            _discovery_cache = doc
    return doc


def request_device_code(
    *,
    client_id: str = XAI_OAUTH_CLIENT_ID,
    scope: str = XAI_OAUTH_SCOPE,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    urlopen: UrlOpenFn | None = None,
    discovery: DiscoveryDocument | None = None,
) -> DeviceCodeResponse:
    """POST device authorization; return codes + verification URIs.

    Raises OSError/ValueError on network or protocol failure.
    """
    disc = discovery if discovery is not None else fetch_discovery(timeout=timeout, urlopen=urlopen)
    code, data = _http_form_post(
        disc.device_authorization_endpoint,
        {
            "client_id": client_id,
            "scope": scope,
        },
        timeout=timeout,
        urlopen=urlopen,
    )
    if code != 200:
        err = data.get("error") or f"http_{code}"
        raise ValueError(f"device_code_failed:{err}")

    device_code = data.get("device_code")
    user_code = data.get("user_code")
    verification_uri = data.get("verification_uri") or data.get("verification_url")
    if not (
        isinstance(device_code, str)
        and device_code.strip()
        and isinstance(user_code, str)
        and user_code.strip()
        and isinstance(verification_uri, str)
        and verification_uri.strip()
    ):
        raise ValueError("device_code_response_incomplete")

    v_complete = data.get("verification_uri_complete") or data.get("verification_url_complete")
    v_complete_s = (
        v_complete.strip()
        if isinstance(v_complete, str) and v_complete.strip()
        else None
    )
    expires_in = data.get("expires_in")
    try:
        expires_i = int(expires_in) if expires_in is not None else 600
    except (TypeError, ValueError):
        expires_i = 600
    interval = data.get("interval")
    try:
        interval_i = int(interval) if interval is not None else 5
    except (TypeError, ValueError):
        interval_i = 5
    if interval_i < 1:
        interval_i = 1
    if interval_i > MAX_POLL_INTERVAL_S:
        interval_i = MAX_POLL_INTERVAL_S

    return DeviceCodeResponse(
        device_code=device_code.strip(),
        user_code=user_code.strip(),
        verification_uri=verification_uri.strip(),
        verification_uri_complete=v_complete_s,
        expires_in=expires_i,
        interval=interval_i,
    )


def _token_poll_result_from_body(code: int, data: dict[str, Any]) -> TokenPollResult:
    if code == 200 and data.get("access_token"):
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        expires_in = data.get("expires_in")
        try:
            expires_i = int(expires_in) if expires_in is not None else None
        except (TypeError, ValueError):
            expires_i = None
        token_type = data.get("token_type")
        scope = data.get("scope")
        id_token = data.get("id_token")
        return TokenPollResult(
            ok=True,
            pending=False,
            slow_down=False,
            access_token=str(access).strip() if isinstance(access, str) else None,
            refresh_token=(
                str(refresh).strip() if isinstance(refresh, str) and refresh.strip() else None
            ),
            expires_in=expires_i,
            token_type=str(token_type) if isinstance(token_type, str) else None,
            scope=str(scope) if isinstance(scope, str) else None,
            id_token=str(id_token) if isinstance(id_token, str) else None,
            detail=None,
            error=None,
        )

    err = data.get("error")
    err_s = str(err) if err is not None else f"http_{code}"
    err_l = err_s.lower()

    if err_l == "authorization_pending":
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
            error=err_s,
        )
    if err_l == "slow_down":
        return TokenPollResult(
            ok=False,
            pending=True,
            slow_down=True,
            access_token=None,
            refresh_token=None,
            expires_in=None,
            token_type=None,
            scope=None,
            id_token=None,
            detail=DETAIL_SLOW_DOWN,
            error=err_s,
        )
    if err_l in {"access_denied", "authorization_declined"}:
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
            error=err_s,
        )
    if err_l in {"expired_token", "expired_token_code"}:
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
            detail=DETAIL_OAUTH_DEVICE_EXPIRED,
            error=err_s,
        )
    if err_l == "invalid_grant":
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
            detail=DETAIL_OAUTH_REAUTH_REQUIRED,
            error=err_s,
        )
    # Account / client eligibility style errors
    if err_l in {"unauthorized_client", "access_denied", "invalid_scope"}:
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
            detail=DETAIL_OAUTH_INELIGIBLE,
            error=err_s,
        )

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
        detail=DETAIL_OAUTH_REFRESH_FAILED if err_l else f"http_{code}",
        error=err_s,
    )


def poll_device_token(
    device_code: str,
    *,
    client_id: str = XAI_OAUTH_CLIENT_ID,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    urlopen: UrlOpenFn | None = None,
    discovery: DiscoveryDocument | None = None,
) -> TokenPollResult:
    """Single token-endpoint poll for device grant (no sleep)."""
    disc = discovery if discovery is not None else fetch_discovery(timeout=timeout, urlopen=urlopen)
    try:
        code, data = _http_form_post(
            disc.token_endpoint,
            {
                "grant_type": XAI_DEVICE_GRANT,
                "device_code": device_code,
                "client_id": client_id,
            },
            timeout=timeout,
            urlopen=urlopen,
        )
    except OSError:
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
            detail=DETAIL_NETWORK,
            error="network",
        )
    return _token_poll_result_from_body(code, data)


def next_poll_interval(current: int, *, slow_down: bool) -> int:
    """Compute next sleep interval (cap 60s); +5s on slow_down."""
    interval = int(current) if current else 5
    if slow_down:
        interval += SLOW_DOWN_INCREMENT_S
    if interval < 1:
        interval = 1
    if interval > MAX_POLL_INTERVAL_S:
        interval = MAX_POLL_INTERVAL_S
    return interval


def refresh_access_token(
    refresh_token: str,
    *,
    client_id: str = XAI_OAUTH_CLIENT_ID,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    urlopen: UrlOpenFn | None = None,
    discovery: DiscoveryDocument | None = None,
) -> TokenPollResult:
    """POST refresh_token grant; return TokenPollResult (ok or error detail)."""
    disc = discovery if discovery is not None else fetch_discovery(timeout=timeout, urlopen=urlopen)
    try:
        code, data = _http_form_post(
            disc.token_endpoint,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            timeout=timeout,
            urlopen=urlopen,
        )
    except OSError:
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
            detail=DETAIL_OAUTH_REFRESH_FAILED,
            error="network",
        )
    return _token_poll_result_from_body(code, data)


def bundle_from_token_success(
    result: TokenPollResult,
    *,
    existing: OAuthBundle | None = None,
    auth_method: str = "device_code",
    client_id: str = XAI_OAUTH_CLIENT_ID,
    now: datetime | None = None,
) -> OAuthBundle:
    """Build an OAuthBundle from a successful token response."""
    if not result.ok or not result.access_token:
        raise ValueError("token_result_not_ok")
    base = now if now is not None else _utc_now()
    now_s = base.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_in = result.expires_in if result.expires_in is not None else 3600
    expires_at = expires_at_from_expires_in(expires_in, now=base)
    email, subject = email_and_subject_from_id_token(result.id_token)
    if email is None and existing is not None:
        email = existing.email
    if subject is None and existing is not None:
        subject = existing.subject
    refresh = result.refresh_token
    if not refresh and existing is not None:
        refresh = existing.refresh_token
    scope = result.scope or (existing.scope if existing else XAI_OAUTH_SCOPE)
    token_type = result.token_type or "Bearer"
    obtained = existing.obtained_at if existing and existing.obtained_at else now_s
    return OAuthBundle(
        version=1,
        client_id=client_id,
        access_token=result.access_token,
        refresh_token=refresh,
        token_type=token_type,
        scope=scope,
        expires_at=expires_at,
        email=email,
        subject=subject,
        obtained_at=obtained,
        updated_at=now_s,
        auth_method=auth_method if existing is None else existing.auth_method,
        reauth_required=False,
    )


def ensure_fresh_access(
    data_dir: Path,
    *,
    skew_s: int = DEFAULT_SKEW_S,
    force: bool = False,
    timeout: float = DEFAULT_HTTP_TIMEOUT_S,
    urlopen: UrlOpenFn | None = None,
    discovery: DiscoveryDocument | None = None,
) -> FreshAccessResult:
    """Load OAuth bundle and refresh if near expiry (single-flight).

    PURE: disk I/O + refresh HTTP only. No ProviderRuntime / set_bearer_token.
    Honors durable ``reauth_required`` on every load (cold-start safe).
    """
    with _refresh_lock:
        return _ensure_fresh_access_locked(
            data_dir,
            skew_s=skew_s,
            force=force,
            timeout=timeout,
            urlopen=urlopen,
            discovery=discovery,
        )


def _ensure_fresh_access_locked(
    data_dir: Path,
    *,
    skew_s: int,
    force: bool,
    timeout: float,
    urlopen: UrlOpenFn | None,
    discovery: DiscoveryDocument | None,
) -> FreshAccessResult:
    try:
        bundle = load_oauth_bundle(data_dir)
    except FileNotFoundError:
        return FreshAccessResult(
            ok=False,
            access_token=None,
            expires_at=None,
            email=None,
            detail=DETAIL_MISSING_OAUTH_TOKENS,
            rotated=False,
        )
    except ValueError:
        return FreshAccessResult(
            ok=False,
            access_token=None,
            expires_at=None,
            email=None,
            detail=DETAIL_INVALID_OAUTH_TOKENS,
            rotated=False,
        )

    if bundle.reauth_required:
        return FreshAccessResult(
            ok=False,
            access_token=None,
            expires_at=bundle.expires_at,
            email=bundle.email,
            detail=DETAIL_OAUTH_REAUTH_REQUIRED,
            rotated=False,
        )

    if not bundle.access_token or not isinstance(bundle.access_token, str):
        return FreshAccessResult(
            ok=False,
            access_token=None,
            expires_at=bundle.expires_at,
            email=bundle.email,
            detail=DETAIL_INVALID_OAUTH_TOKENS,
            rotated=False,
        )

    remaining = seconds_until_expiry(bundle.expires_at)
    still_fresh = remaining is not None and remaining > float(skew_s)

    if not force and still_fresh:
        return FreshAccessResult(
            ok=True,
            access_token=bundle.access_token,
            expires_at=bundle.expires_at,
            email=bundle.email,
            detail=None,
            rotated=False,
        )

    if not bundle.refresh_token:
        return FreshAccessResult(
            ok=False,
            access_token=None,
            expires_at=bundle.expires_at,
            email=bundle.email,
            detail=DETAIL_OAUTH_REAUTH_REQUIRED,
            rotated=False,
        )

    poll = refresh_access_token(
        bundle.refresh_token,
        client_id=bundle.client_id or XAI_OAUTH_CLIENT_ID,
        timeout=timeout,
        urlopen=urlopen,
        discovery=discovery,
    )

    if poll.ok and poll.access_token:
        new_bundle = bundle_from_token_success(
            poll,
            existing=bundle,
            auth_method=bundle.auth_method or "device_code",
            client_id=bundle.client_id or XAI_OAUTH_CLIENT_ID,
        )
        save_oauth_bundle(data_dir, new_bundle)
        return FreshAccessResult(
            ok=True,
            access_token=new_bundle.access_token,
            expires_at=new_bundle.expires_at,
            email=new_bundle.email,
            detail=None,
            rotated=True,
        )

    if poll.detail == DETAIL_OAUTH_REAUTH_REQUIRED or (
        poll.error and str(poll.error).lower() == "invalid_grant"
    ):
        # KD15: durable reauth_required before return; keep tokens for forensics.
        marked = OAuthBundle(
            version=bundle.version,
            client_id=bundle.client_id,
            access_token=bundle.access_token,
            refresh_token=bundle.refresh_token,
            token_type=bundle.token_type,
            scope=bundle.scope,
            expires_at=bundle.expires_at,
            email=bundle.email,
            subject=bundle.subject,
            obtained_at=bundle.obtained_at,
            updated_at=_utc_now_iso_z(),
            auth_method=bundle.auth_method,
            reauth_required=True,
        )
        try:
            save_oauth_bundle(data_dir, marked)
        except OSError as exc:
            logger.warning("oauth reauth_required write failed: %s", type(exc).__name__)
        return FreshAccessResult(
            ok=False,
            access_token=None,
            expires_at=bundle.expires_at,
            email=bundle.email,
            detail=DETAIL_OAUTH_REAUTH_REQUIRED,
            rotated=False,
        )

    # Transient: grace if access still not expired
    access_still_valid = remaining is not None and remaining > 0
    if access_still_valid:
        return FreshAccessResult(
            ok=True,
            access_token=bundle.access_token,
            expires_at=bundle.expires_at,
            email=bundle.email,
            detail=None,
            rotated=False,
        )

    return FreshAccessResult(
        ok=False,
        access_token=None,
        expires_at=bundle.expires_at,
        email=bundle.email,
        detail=DETAIL_OAUTH_REFRESH_FAILED,
        rotated=False,
    )


__all__ = [
    "DEFAULT_HTTP_TIMEOUT_S",
    "DEFAULT_SKEW_S",
    "DETAIL_AUTHORIZATION_PENDING",
    "DETAIL_INVALID_OAUTH_TOKENS",
    "DETAIL_MISSING_OAUTH_TOKENS",
    "DETAIL_NETWORK",
    "DETAIL_OAUTH_DENIED",
    "DETAIL_OAUTH_DEVICE_EXPIRED",
    "DETAIL_OAUTH_INELIGIBLE",
    "DETAIL_OAUTH_PENDING",
    "DETAIL_OAUTH_REAUTH_REQUIRED",
    "DETAIL_OAUTH_REFRESH_FAILED",
    "DETAIL_OAUTH_TOKEN_EXPIRED",
    "DETAIL_SLOW_DOWN",
    "DeviceCodeResponse",
    "DiscoveryDocument",
    "FreshAccessResult",
    "MAX_POLL_INTERVAL_S",
    "TokenPollResult",
    "XAI_DEVICE_CODE_URL",
    "XAI_DEVICE_GRANT",
    "XAI_OAUTH_CLIENT_ID",
    "XAI_OAUTH_SCOPE",
    "XAI_OIDC_DISCOVERY",
    "XAI_OIDC_ISSUER",
    "XAI_TOKEN_URL",
    "bundle_from_token_success",
    "claims_from_id_token",
    "clear_discovery_cache",
    "email_and_subject_from_id_token",
    "ensure_fresh_access",
    "expires_at_from_expires_in",
    "fetch_discovery",
    "next_poll_interval",
    "parse_iso_utc",
    "poll_device_token",
    "refresh_access_token",
    "request_device_code",
    "seconds_until_expiry",
]
