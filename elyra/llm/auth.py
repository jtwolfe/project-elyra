"""Credential resolver + API key secret store (fail-closed).

Scope: Grok Build ``~/.grok/auth.json`` parse (smoke-compatible), active-source
resolution (``xai_oauth`` | ``api_key`` | ``grok_build`` — no silent fallback),
atomic API key file under ``data/secrets/``, env ``XAI_API_KEY`` only for
``api_key``, pure OAuth resolve via ``ensure_fresh_access``.
Out of scope: HTTP chat clients, Glass UI, ProviderRuntime rebind (callers).

Never log or return secrets into status payloads. ``CredentialResolution.token``
is for bearer injection only. ``resolve_bearer`` is pure (no set_bearer_token).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

logger = logging.getLogger(__name__)

SOURCE_XAI_OAUTH = "xai_oauth"
SOURCE_GROK_BUILD = "grok_build"
SOURCE_API_KEY = "api_key"
# SSOT — settings / provider_prefs / CLI must import this frozenset.
VALID_SOURCES = frozenset({SOURCE_XAI_OAUTH, SOURCE_API_KEY, SOURCE_GROK_BUILD})

ENV_XAI_API_KEY = "XAI_API_KEY"

API_KEY_FILENAME = "xai_api_key"
API_KEY_TMP_FILENAME = "xai_api_key.tmp"
SECRETS_DIRNAME = "secrets"

# Failure details (status-safe; never secrets)
DETAIL_MISSING_AUTH_JSON = "missing_auth_json"
DETAIL_INVALID_AUTH_JSON = "invalid_auth_json"
DETAIL_MISSING_TOKEN = "missing_token"
DETAIL_TOKEN_EXPIRED = "token_expired"
DETAIL_MISSING_API_KEY = "missing_api_key"
DETAIL_UNKNOWN_SOURCE = "unknown_source"
DETAIL_EMPTY_API_KEY = "empty_api_key"
# OAuth detail codes (mirrored from xai_oauth for status messaging)
DETAIL_MISSING_OAUTH_TOKENS = "missing_oauth_tokens"
DETAIL_INVALID_OAUTH_TOKENS = "invalid_oauth_tokens"
DETAIL_OAUTH_TOKEN_EXPIRED = "oauth_token_expired"
DETAIL_OAUTH_REFRESH_FAILED = "oauth_refresh_failed"
DETAIL_OAUTH_REAUTH_REQUIRED = "oauth_reauth_required"
DETAIL_OAUTH_DENIED = "oauth_denied"


@dataclass(frozen=True)
class CredentialResolution:
    """Result of resolving the active credential source.

    ``token`` must never be logged or placed in status/API responses.
    ``rotated`` is True iff this call refreshed OAuth access on disk
    (callers may rebind live clients; auth itself never rebinds).
    """

    ok: bool
    source: str
    token: str | None
    detail: str | None
    expires_at: str | None
    email: str | None
    api_key_configured: bool
    rotated: bool = False


class GrokAuthError(Exception):
    """Raised by ``load_grok_build_session`` with a status-safe detail code."""

    def __init__(self, detail: str, message: str = "") -> None:
        self.detail = detail
        super().__init__(message or detail)


def default_grok_auth_path() -> Path:
    """Default Grok Build session path: ``~/.grok/auth.json``."""
    return Path.home() / ".grok" / "auth.json"


def secrets_dir(data_dir: Path) -> Path:
    return Path(data_dir) / SECRETS_DIRNAME


def api_key_path(data_dir: Path) -> Path:
    return secrets_dir(data_dir) / API_KEY_FILENAME


def ensure_secrets_dir(data_dir: Path) -> Path:
    """Create ``data/secrets`` with mode ``0700`` (best-effort chmod on POSIX)."""
    d = secrets_dir(data_dir)
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def load_grok_build_session(
    path: Path | None = None,
) -> tuple[str, dict]:
    """Load access token + safe metadata from Grok Build auth.json.

    Shape matches ``scripts/prototype_xai_grok_auth_smoke.py``:
    - nested ``{ "https://auth.x.ai::<client_id>": { key, ... } }`` (first entry)
    - or flat ``{ access_token | key, ... }``

    Token fields: ``key`` or ``access_token``.

    Returns ``(token, meta)`` where ``meta`` never includes the raw token
    (only ``token_len`` / truncated prefix for diagnostics).

    Raises:
        GrokAuthError: missing file, invalid JSON/shape, or missing token field.
    """
    auth_path = Path(path) if path is not None else default_grok_auth_path()
    if not auth_path.is_file():
        raise GrokAuthError(
            DETAIL_MISSING_AUTH_JSON,
            f"missing {auth_path}",
        )
    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GrokAuthError(
            DETAIL_INVALID_AUTH_JSON,
            f"cannot read auth.json: {exc}",
        ) from exc

    if not isinstance(raw, dict) or not raw:
        raise GrokAuthError(
            DETAIL_INVALID_AUTH_JSON,
            f"unexpected auth.json root type: {type(raw).__name__}",
        )

    # Nested Grok Build shape: first entry; else treat root as flat entry.
    entry_key = next(iter(raw))
    entry = raw[entry_key]
    if not isinstance(entry, dict):
        entry = raw
        entry_key = "(flat)"
    elif not any(k in entry for k in ("key", "access_token", "expires_at", "email")):
        # First value not a session object — try flat root
        if any(k in raw for k in ("key", "access_token")):
            entry = raw
            entry_key = "(flat)"

    token = entry.get("key") or entry.get("access_token")
    if not token or not isinstance(token, str) or not token.strip():
        raise GrokAuthError(
            DETAIL_MISSING_TOKEN,
            "no access token field (key/access_token) in auth entry",
        )
    token = token.strip()

    meta: dict = {
        "auth_path": str(auth_path),
        "entry": (
            (str(entry_key)[:48] + "…")
            if len(str(entry_key)) > 48
            else str(entry_key)
        ),
        "auth_mode": entry.get("auth_mode"),
        "expires_at": entry.get("expires_at"),
        "email": entry.get("email"),
        "token_len": len(token),
        "token_prefix": token[:8] + "…" if len(token) >= 8 else "…",
    }
    exp = entry.get("expires_at")
    if isinstance(exp, str):
        try:
            exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
            meta["expires_in_s"] = int(
                (exp_dt - datetime.now(timezone.utc)).total_seconds()
            )
        except ValueError:
            pass
    return token, meta


def _is_expired(expires_at: str | None) -> bool:
    if not expires_at or not isinstance(expires_at, str):
        return False
    try:
        exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if exp_dt.tzinfo is None:
        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
    return exp_dt <= datetime.now(timezone.utc)


def read_stored_api_key(data_dir: Path) -> str | None:
    """Read stripped API key from ``data/secrets/xai_api_key``, or None."""
    path = api_key_path(data_dir)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None
    return text or None


def write_stored_api_key(data_dir: Path, api_key: str) -> Path:
    """Atomically write API key: temp + chmod 0600 + os.replace + chmod final.

    Creates ``data/secrets`` with mode 0700. Raises ValueError if key empty
    after strip. Cleans up the temp file if write fails before replace.
    """
    key = (api_key or "").strip()
    if not key:
        raise ValueError(DETAIL_EMPTY_API_KEY)

    ensure_secrets_dir(data_dir)
    final = api_key_path(data_dir)
    tmp = secrets_dir(data_dir) / API_KEY_TMP_FILENAME
    payload = key + "\n"

    # Exclusive create when possible so concurrent writers do not share a tmp.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_EXCL"):
        # Prefer exclusive; if tmp left over from crash, remove and retry once.
        try:
            fd = os.open(str(tmp), flags | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            fd = os.open(str(tmp), flags | os.O_EXCL, 0o600)
    else:
        fd = os.open(str(tmp), flags, 0o600)

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
        try:
            os.chmod(final, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return final


def delete_stored_api_key(data_dir: Path) -> bool:
    """Delete stored API key file. Returns True if the final key file was removed.

    Always attempts to remove a leftover ``xai_api_key.tmp`` (missing_ok) so a
    crash between tmp write and ``os.replace`` cannot leave secret material on
    disk after operator DELETE.
    """
    path = api_key_path(data_dir)
    tmp = secrets_dir(data_dir) / API_KEY_TMP_FILENAME
    removed = False
    if path.is_file():
        try:
            path.unlink()
            removed = True
        except OSError:
            removed = False
    try:
        tmp.unlink(missing_ok=True)
    except OSError:
        pass
    return removed


def api_key_is_configured(
    data_dir: Path,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    """True if stored key file is non-empty or env ``XAI_API_KEY`` is set."""
    if read_stored_api_key(data_dir):
        return True
    environ = env if env is not None else os.environ
    val = environ.get(ENV_XAI_API_KEY, "")
    return bool(isinstance(val, str) and val.strip())


def auth_secret_values_for_redaction(data_dir: Path) -> list[str]:
    """Return auth secret strings that must be scrubbed from tool results.

    Unions stored API key with OAuth access + refresh when present on disk.
    Never invents values/ entries solely for redaction. Best-effort: missing
    or unreadable files yield an empty contribution.
    """
    out: list[str] = []
    key = read_stored_api_key(data_dir)
    if key:
        out.append(key)
    try:
        from elyra.llm.oauth_store import load_oauth_bundle_optional

        bundle = load_oauth_bundle_optional(data_dir)
    except Exception:  # noqa: BLE001 — redaction best-effort
        bundle = None
    if bundle is not None:
        if bundle.access_token:
            out.append(bundle.access_token)
        if bundle.refresh_token:
            out.append(bundle.refresh_token)
    return out


def resolve_bearer(
    *,
    source: str,
    data_dir: Path,
    grok_auth_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> CredentialResolution:
    """Resolve bearer token for the **active** credential source only.

    No silent fallback between sources. Fail-closed: missing / expired /
    invalid → ``ok=False`` with a status-safe ``detail``.

    PURE for OAuth: disk I/O + optional refresh HTTP via ``ensure_fresh_access``;
    never calls ``set_bearer_token`` or touches ProviderRuntime.

    Env ``XAI_API_KEY`` is consulted only when ``source == api_key`` and no
    stored key file is present.
    """
    configured = api_key_is_configured(data_dir, env=env)
    src = (source or "").strip()

    if src not in VALID_SOURCES:
        logger.warning("credential resolve: unknown source %r", src)
        return CredentialResolution(
            ok=False,
            source=src or "unknown",
            token=None,
            detail=DETAIL_UNKNOWN_SOURCE,
            expires_at=None,
            email=None,
            api_key_configured=configured,
        )

    if src == SOURCE_XAI_OAUTH:
        return _resolve_xai_oauth(
            data_dir=data_dir,
            api_key_configured=configured,
        )

    if src == SOURCE_GROK_BUILD:
        return _resolve_grok_build(
            data_dir=data_dir,
            grok_auth_path=grok_auth_path,
            api_key_configured=configured,
        )

    return _resolve_api_key(
        data_dir=data_dir,
        env=env,
        api_key_configured=configured,
    )


def _resolve_xai_oauth(
    *,
    data_dir: Path,
    api_key_configured: bool,
) -> CredentialResolution:
    """Pure OAuth resolve via ensure_fresh_access — no rebind side effects."""
    # Local import: oauth_store/xai_oauth import auth helpers (cycle-safe).
    from elyra.llm.xai_oauth import ensure_fresh_access

    try:
        fresh = ensure_fresh_access(data_dir)
    except OSError as exc:
        # Durable reauth_required write failed after invalid_grant (KD15).
        logger.warning(
            "credential resolve xai_oauth: ensure_fresh OSError %s",
            type(exc).__name__,
        )
        return CredentialResolution(
            ok=False,
            source=SOURCE_XAI_OAUTH,
            token=None,
            detail=DETAIL_OAUTH_REFRESH_FAILED,
            expires_at=None,
            email=None,
            api_key_configured=api_key_configured,
            rotated=False,
        )

    if not fresh.ok:
        logger.warning(
            "credential resolve xai_oauth failed: detail=%s",
            fresh.detail,
        )
        return CredentialResolution(
            ok=False,
            source=SOURCE_XAI_OAUTH,
            token=None,
            detail=fresh.detail or DETAIL_MISSING_OAUTH_TOKENS,
            expires_at=fresh.expires_at,
            email=fresh.email,
            api_key_configured=api_key_configured,
            rotated=False,
        )

    return CredentialResolution(
        ok=True,
        source=SOURCE_XAI_OAUTH,
        token=fresh.access_token,
        detail=None,
        expires_at=fresh.expires_at,
        email=fresh.email,
        api_key_configured=api_key_configured,
        rotated=bool(fresh.rotated),
    )


def _resolve_grok_build(
    *,
    data_dir: Path,
    grok_auth_path: Path | None,
    api_key_configured: bool,
) -> CredentialResolution:
    path = Path(grok_auth_path) if grok_auth_path is not None else default_grok_auth_path()
    try:
        token, meta = load_grok_build_session(path)
    except GrokAuthError as exc:
        logger.warning(
            "credential resolve grok_build failed: detail=%s path=%s",
            exc.detail,
            path,
        )
        return CredentialResolution(
            ok=False,
            source=SOURCE_GROK_BUILD,
            token=None,
            detail=exc.detail,
            expires_at=None,
            email=None,
            api_key_configured=api_key_configured,
        )

    expires_at = meta.get("expires_at")
    if isinstance(expires_at, str):
        expires_s = expires_at
    else:
        expires_s = None
    email = meta.get("email")
    email_s = email if isinstance(email, str) else None

    if _is_expired(expires_s):
        logger.warning(
            "credential resolve grok_build: token_expired expires_at=%s",
            expires_s,
        )
        return CredentialResolution(
            ok=False,
            source=SOURCE_GROK_BUILD,
            token=None,
            detail=DETAIL_TOKEN_EXPIRED,
            expires_at=expires_s,
            email=email_s,
            api_key_configured=api_key_configured,
        )

    return CredentialResolution(
        ok=True,
        source=SOURCE_GROK_BUILD,
        token=token,
        detail=None,
        expires_at=expires_s,
        email=email_s,
        api_key_configured=api_key_configured,
    )


def _resolve_api_key(
    *,
    data_dir: Path,
    env: Mapping[str, str] | None,
    api_key_configured: bool,
) -> CredentialResolution:
    stored = read_stored_api_key(data_dir)
    if stored:
        return CredentialResolution(
            ok=True,
            source=SOURCE_API_KEY,
            token=stored,
            detail=None,
            expires_at=None,
            email=None,
            api_key_configured=True,
        )

    environ = env if env is not None else os.environ
    env_key = environ.get(ENV_XAI_API_KEY, "")
    if isinstance(env_key, str) and env_key.strip():
        return CredentialResolution(
            ok=True,
            source=SOURCE_API_KEY,
            token=env_key.strip(),
            detail=None,
            expires_at=None,
            email=None,
            api_key_configured=True,
        )

    logger.warning("credential resolve api_key: missing_api_key")
    return CredentialResolution(
        ok=False,
        source=SOURCE_API_KEY,
        token=None,
        detail=DETAIL_MISSING_API_KEY,
        expires_at=None,
        email=None,
        api_key_configured=False,
    )
