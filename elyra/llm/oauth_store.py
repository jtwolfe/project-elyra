"""Reserved xAI OAuth token bundle under ``data/secrets/xai_oauth.json``.

Scope: load/save/delete/public_meta, atomic write (tmp + replace + 0600),
optional flock (lock file first), ``persist_oauth_login`` (disk + optional prefs).
Out of scope: HTTP OAuth, Glass, ProviderRuntime rebind (see complete_oauth_login).

Never put tokens in public meta, logs, or status payloads.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from elyra.llm.auth import SOURCE_XAI_OAUTH, ensure_secrets_dir, secrets_dir

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None  # type: ignore[assignment]

OAUTH_FILENAME = "xai_oauth.json"
OAUTH_TMP_FILENAME = "xai_oauth.json.tmp"
OAUTH_BUNDLE_VERSION = 1
# Re-export for callers that import from oauth_store (same string as auth).
# SOURCE_XAI_OAUTH imported from auth above.

# Process-local write serialization (complements flock across processes).
_write_lock = threading.Lock()


@dataclass(frozen=True)
class OAuthBundle:
    """On-disk OAuth token bundle (version 1)."""

    version: int
    client_id: str
    access_token: str
    refresh_token: str | None
    token_type: str
    scope: str | None
    expires_at: str | None
    email: str | None
    subject: str | None
    obtained_at: str | None
    updated_at: str | None
    auth_method: str | None
    reauth_required: bool


@dataclass(frozen=True)
class OAuthPublicMeta:
    """Status-safe OAuth meta — never includes tokens."""

    configured: bool
    email: str | None
    expires_at: str | None
    updated_at: str | None
    auth_method: str | None
    reauth_required: bool


def oauth_path(data_dir: Path) -> Path:
    return secrets_dir(data_dir) / OAUTH_FILENAME


def oauth_tmp_path(data_dir: Path) -> Path:
    return secrets_dir(data_dir) / OAUTH_TMP_FILENAME


def _utc_now_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_bundle(raw: Any) -> OAuthBundle:
    if not isinstance(raw, dict):
        raise ValueError("invalid_oauth_tokens")
    version = raw.get("version", 1)
    try:
        version_i = int(version)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_oauth_tokens") from exc
    if version_i != OAUTH_BUNDLE_VERSION:
        raise ValueError("invalid_oauth_tokens")

    access = raw.get("access_token")
    if not isinstance(access, str) or not access.strip():
        raise ValueError("invalid_oauth_tokens")

    refresh = raw.get("refresh_token")
    refresh_s: str | None
    if refresh is None or refresh == "":
        refresh_s = None
    elif isinstance(refresh, str) and refresh.strip():
        refresh_s = refresh.strip()
    else:
        raise ValueError("invalid_oauth_tokens")

    client_id = raw.get("client_id")
    client_s = (
        client_id.strip()
        if isinstance(client_id, str) and client_id.strip()
        else ""
    )

    token_type = raw.get("token_type") or "Bearer"
    if not isinstance(token_type, str):
        token_type = "Bearer"

    def _opt_str(key: str) -> str | None:
        v = raw.get(key)
        if v is None or v == "":
            return None
        if isinstance(v, str) and v.strip():
            return v.strip()
        return None

    reauth = raw.get("reauth_required", False)
    if not isinstance(reauth, bool):
        reauth = bool(reauth)

    return OAuthBundle(
        version=version_i,
        client_id=client_s,
        access_token=access.strip(),
        refresh_token=refresh_s,
        token_type=token_type.strip() or "Bearer",
        scope=_opt_str("scope"),
        expires_at=_opt_str("expires_at"),
        email=_opt_str("email"),
        subject=_opt_str("subject"),
        obtained_at=_opt_str("obtained_at"),
        updated_at=_opt_str("updated_at"),
        auth_method=_opt_str("auth_method"),
        reauth_required=reauth,
    )


def load_oauth_bundle(data_dir: Path) -> OAuthBundle:
    """Load and validate OAuth bundle.

    Raises:
        FileNotFoundError: missing file.
        ValueError: unreadable / invalid schema (detail-equivalent
            ``invalid_oauth_tokens``).
    """
    path = oauth_path(data_dir)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    try:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_oauth_tokens") from exc
    return _parse_bundle(raw)


def load_oauth_bundle_optional(data_dir: Path) -> OAuthBundle | None:
    """Load bundle or return None if missing/invalid."""
    try:
        return load_oauth_bundle(data_dir)
    except (FileNotFoundError, ValueError):
        return None


def oauth_is_configured(data_dir: Path) -> bool:
    """True if a readable bundle file exists (even if reauth_required)."""
    return load_oauth_bundle_optional(data_dir) is not None


def public_meta(data_dir: Path) -> OAuthPublicMeta:
    """Status-safe public meta for a data_dir (never tokens)."""
    bundle = load_oauth_bundle_optional(data_dir)
    if bundle is None:
        return OAuthPublicMeta(
            configured=False,
            email=None,
            expires_at=None,
            updated_at=None,
            auth_method=None,
            reauth_required=False,
        )
    return OAuthPublicMeta(
        configured=True,
        email=bundle.email,
        expires_at=bundle.expires_at,
        updated_at=bundle.updated_at,
        auth_method=bundle.auth_method,
        reauth_required=bundle.reauth_required,
    )


def bundle_to_dict(bundle: OAuthBundle) -> dict[str, Any]:
    """Serialize bundle for disk (includes secrets — caller must not log)."""
    d = asdict(bundle)
    return d


def save_oauth_bundle(data_dir: Path, bundle: OAuthBundle) -> Path:
    """Atomically write OAuth bundle: flock → tmp + chmod 0600 + os.replace + fsync.

    Creates ``data/secrets`` with mode 0700. Acquires ``xai_oauth.json.lock``
    **before** creating the shared tmp so concurrent processes cannot unlink
    each other's in-flight tmp (O_EXCL retry path).
    """
    ensure_secrets_dir(data_dir)
    final = oauth_path(data_dir)
    tmp = oauth_tmp_path(data_dir)
    payload = json.dumps(bundle_to_dict(bundle), ensure_ascii=False, indent=2) + "\n"

    with _write_lock:
        return _atomic_write_json(final, tmp, payload)


def _acquire_bundle_lock(final: Path) -> int | None:
    """Open and exclusive-flock ``final.name + '.lock'``. Returns fd or None."""
    if fcntl is None:
        return None
    lock_path = final.parent / (final.name + ".lock")
    lock_fd: int | None = None
    try:
        lock_fd = os.open(str(lock_path), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.warning(
                "oauth bundle write: lock contended on %s (last-writer-wins)",
                final.name,
            )
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        return lock_fd
    except OSError:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        return None


def _release_bundle_lock(lock_fd: int | None) -> None:
    if lock_fd is None or fcntl is None:
        return
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(lock_fd)
    except OSError:
        pass


def _atomic_write_json(final: Path, tmp: Path, payload: str) -> Path:
    # Flock first so O_EXCL tmp create/unlink cannot race another writer.
    lock_fd = _acquire_bundle_lock(final)
    fd: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_EXCL"):
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

        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None  # ownership transferred to fdopen
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
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        _release_bundle_lock(lock_fd)
    return final


def delete_oauth_bundle(data_dir: Path) -> bool:
    """Delete OAuth bundle + leftover tmp. Returns True if final file removed."""
    path = oauth_path(data_dir)
    tmp = oauth_tmp_path(data_dir)
    lock_path = path.parent / (path.name + ".lock")
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
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass
    return removed


def persist_oauth_login(
    data_dir: Path,
    tokens: OAuthBundle | dict[str, Any],
    *,
    activate: bool = True,
) -> OAuthPublicMeta:
    """Atomic write OAuth bundle (``reauth_required=false``) + optional prefs.

    No ProviderRuntime, no rebuild, no set_bearer_token. Returns public meta only.

    When ``activate`` is True (default, KD13): set
    ``credential_source=xai_oauth`` via ``update_provider_prefs`` (load-merge-save).
    CLI ``--no-activate`` / Glass checkbox off pass ``activate=False``.
    """
    if isinstance(tokens, OAuthBundle):
        bundle = tokens
    else:
        # Normalize dict → bundle; force reauth_required false on login.
        data = dict(tokens)
        data["reauth_required"] = False
        if "version" not in data:
            data["version"] = OAUTH_BUNDLE_VERSION
        if not data.get("updated_at"):
            data["updated_at"] = _utc_now_iso_z()
        if not data.get("obtained_at"):
            data["obtained_at"] = data["updated_at"]
        bundle = _parse_bundle(data)

    # Always clear reauth on successful login persist.
    if bundle.reauth_required:
        bundle = OAuthBundle(
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
            updated_at=bundle.updated_at or _utc_now_iso_z(),
            auth_method=bundle.auth_method,
            reauth_required=False,
        )

    save_oauth_bundle(data_dir, bundle)

    if activate:
        from elyra.llm.provider_prefs import update_provider_prefs

        update_provider_prefs(data_dir, credential_source=SOURCE_XAI_OAUTH)

    return public_meta(data_dir)


__all__ = [
    "OAUTH_BUNDLE_VERSION",
    "OAUTH_FILENAME",
    "OAUTH_TMP_FILENAME",
    "OAuthBundle",
    "OAuthPublicMeta",
    "SOURCE_XAI_OAUTH",
    "bundle_to_dict",
    "delete_oauth_bundle",
    "load_oauth_bundle",
    "load_oauth_bundle_optional",
    "oauth_is_configured",
    "oauth_path",
    "oauth_tmp_path",
    "persist_oauth_login",
    "public_meta",
    "save_oauth_bundle",
]
