"""In-memory xAI device-code login session + daemon poller (PR3).

Lifecycle: idle | pending | success | error | cancelled.
Holds ``device_code`` only in process memory — never logs it, never returns it
in status/API payloads. Poller never holds ProviderRuntime lock across HTTP.

Success path: call injected ``on_success`` (live ``complete_oauth_login``) when
present; else ``persist_oauth_login`` (disk-only / CLI paths).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from elyra.llm.oauth_store import persist_oauth_login
from elyra.llm.xai_oauth import (
    DETAIL_NETWORK,
    DETAIL_OAUTH_DEVICE_EXPIRED,
    DETAIL_OAUTH_DENIED,
    DETAIL_OAUTH_REFRESH_FAILED,
    MAX_POLL_INTERVAL_S,
    XAI_OAUTH_CLIENT_ID,
    XAI_OAUTH_SCOPE,
    DeviceCodeResponse,
    UrlOpenFn,
    email_and_subject_from_id_token,
    expires_at_from_expires_in,
    next_poll_interval,
    poll_device_token,
    request_device_code,
)

_LOG = logging.getLogger(__name__)

STATE_IDLE = "idle"
STATE_PENDING = "pending"
STATE_SUCCESS = "success"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"

# Secrets that must never appear in public status/API payloads.
_SECRET_KEYS = frozenset(
    {
        "access_token",
        "refresh_token",
        "device_code",
        "id_token",
        "token",
    }
)

# on_success(tokens_dict, *, activate: bool) -> Any
OnSuccessFn = Callable[..., Any]

DEFAULT_JOIN_TIMEOUT_S = 2.0


class OAuthDeviceSession:
    """Single in-process device-code session (replace-on-start)."""

    def __init__(
        self,
        data_dir: Path,
        *,
        on_success: OnSuccessFn | None = None,
        urlopen: UrlOpenFn | None = None,
        join_timeout_s: float = DEFAULT_JOIN_TIMEOUT_S,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._on_success = on_success
        self._urlopen = urlopen
        self._join_timeout_s = float(join_timeout_s)
        self._lock = threading.Lock()
        self._poll_stop: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._state: str = STATE_IDLE
        self._detail: str | None = None
        self._activate: bool = True
        # Public success meta only (never tokens / device_code).
        self._email: str | None = None
        self._expires_at: str | None = None
        # Public pending fields for status (user_code is public by OAuth design).
        self._user_code: str | None = None
        self._verification_uri: str | None = None
        self._verification_uri_complete: str | None = None
        self._expires_in: int | None = None
        self._interval: int | None = None
        # Private — never exposed via status()/start() return.
        self._device_code: str | None = None

    # ── Public control plane ─────────────────────────────────────────────

    def start(self, *, activate: bool = True) -> dict[str, Any]:
        """Cancel any prior pending session, mint device codes, start poller.

        Returns public start fields only (never ``device_code`` / tokens).
        On device-authorization failure returns ``ok=False`` with state error.
        """
        self._stop_previous_poller()

        try:
            device = request_device_code(urlopen=self._urlopen)
        except (OSError, ValueError) as exc:
            detail = DETAIL_OAUTH_REFRESH_FAILED
            _LOG.warning("device start failed: %s", type(exc).__name__)
            with self._lock:
                self._state = STATE_ERROR
                self._detail = detail
                self._clear_pending_public_unlocked()
                self._device_code = None
            return {
                "ok": False,
                "state": STATE_ERROR,
                "detail": detail,
                "pending": False,
            }

        stop = threading.Event()
        with self._lock:
            self._poll_stop = stop
            self._state = STATE_PENDING
            self._detail = None
            self._activate = bool(activate)
            self._email = None
            self._expires_at = None
            self._user_code = device.user_code
            self._verification_uri = device.verification_uri
            self._verification_uri_complete = device.verification_uri_complete
            self._expires_in = int(device.expires_in)
            self._interval = int(device.interval)
            self._device_code = device.device_code  # private
            thread = threading.Thread(
                target=self._poll_loop,
                args=(stop, device, bool(activate)),
                name="elyra-oauth-device-poll",
                daemon=True,
            )
            self._thread = thread
        thread.start()

        return self._public_start_payload(device)

    def cancel(self) -> dict[str, Any]:
        """Signal poller stop; set state cancelled; join best-effort."""
        with self._lock:
            stop = self._poll_stop
            thread = self._thread
            self._state = STATE_CANCELLED
            self._detail = None
            self._device_code = None
            self._clear_pending_public_unlocked()
        if stop is not None:
            stop.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self._join_timeout_s)
        with self._lock:
            if self._thread is thread:
                self._thread = None
            if self._poll_stop is stop:
                self._poll_stop = None
            self._state = STATE_CANCELLED
        return {"ok": True, "state": STATE_CANCELLED}

    def status(self, *, provider_fields: dict[str, Any] | None = None) -> dict[str, Any]:
        """Public session status — never tokens / device_code."""
        with self._lock:
            out: dict[str, Any] = {
                "ok": True,
                "state": self._state,
            }
            if self._detail is not None:
                out["detail"] = self._detail
            if self._email is not None:
                out["email"] = self._email
            if self._expires_at is not None:
                out["expires_at"] = self._expires_at
            if self._state == STATE_PENDING:
                if self._user_code is not None:
                    out["user_code"] = self._user_code
                if self._verification_uri is not None:
                    out["verification_uri"] = self._verification_uri
                if self._verification_uri_complete is not None:
                    out["verification_uri_complete"] = self._verification_uri_complete
                if self._expires_in is not None:
                    out["expires_in"] = self._expires_in
                if self._interval is not None:
                    out["interval"] = self._interval
        if provider_fields:
            for key in (
                "credential_source",
                "credential_ok",
                "credential_detail",
                "credential_email",
                "credential_expires_at",
                "oauth_configured",
            ):
                if key in provider_fields and provider_fields[key] is not None:
                    out[key] = provider_fields[key]
        return _strip_secrets(out)

    # ── Internals ────────────────────────────────────────────────────────

    def _stop_previous_poller(self) -> None:
        with self._lock:
            stop = self._poll_stop
            thread = self._thread
            self._poll_stop = None
            self._thread = None
        if stop is not None:
            stop.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=self._join_timeout_s)

    def _clear_pending_public_unlocked(self) -> None:
        self._user_code = None
        self._verification_uri = None
        self._verification_uri_complete = None
        self._expires_in = None
        self._interval = None

    @staticmethod
    def _public_start_payload(device: DeviceCodeResponse) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ok": True,
            "user_code": device.user_code,
            "verification_uri": device.verification_uri,
            "expires_in": int(device.expires_in),
            "interval": int(device.interval),
            "pending": True,
            "state": STATE_PENDING,
        }
        if device.verification_uri_complete:
            out["verification_uri_complete"] = device.verification_uri_complete
        return _strip_secrets(out)

    def _poll_loop(
        self,
        stop: threading.Event,
        device: DeviceCodeResponse,
        activate: bool,
    ) -> None:
        """Daemon poller: stop Event before each HTTP and between sleeps."""
        deadline = time.monotonic() + max(1, int(device.expires_in))
        interval = max(1, min(int(device.interval), MAX_POLL_INTERVAL_S))
        device_code = device.device_code

        try:
            while not stop.is_set():
                if time.monotonic() >= deadline:
                    self._set_terminal(STATE_ERROR, DETAIL_OAUTH_DEVICE_EXPIRED, stop)
                    return

                # Check stop before network I/O (no ProviderRuntime lock here).
                if stop.is_set():
                    return

                try:
                    result = poll_device_token(
                        device_code,
                        urlopen=self._urlopen,
                    )
                except Exception:  # noqa: BLE001 — treat as retryable network
                    _LOG.debug("device poll exception", exc_info=True)
                    result = None

                if stop.is_set():
                    return

                if result is not None and result.ok and result.access_token:
                    self._handle_success(result, activate=activate, stop=stop)
                    return

                # Pending / slow_down / transient network → keep polling.
                if result is None or result.pending or result.detail == DETAIL_NETWORK:
                    slow = bool(result.slow_down) if result is not None else False
                    interval = next_poll_interval(interval, slow_down=slow)
                    if stop.wait(timeout=float(interval)):
                        return
                    continue

                # Terminal OAuth error.
                detail = result.detail or DETAIL_OAUTH_REFRESH_FAILED
                if detail == DETAIL_OAUTH_DENIED:
                    detail = DETAIL_OAUTH_DENIED
                self._set_terminal(STATE_ERROR, detail, stop)
                return
        finally:
            # Drop private device_code when this poller exits (success/error/cancel/replace).
            with self._lock:
                if self._poll_stop is stop:
                    self._device_code = None

    def _handle_success(
        self,
        result: Any,
        *,
        activate: bool,
        stop: threading.Event,
    ) -> None:
        access = result.access_token
        if not isinstance(access, str) or not access.strip():
            self._set_terminal(STATE_ERROR, DETAIL_OAUTH_REFRESH_FAILED, stop)
            return

        email, subject = email_and_subject_from_id_token(result.id_token)
        expires_at: str | None = None
        if result.expires_in is not None:
            try:
                expires_at = expires_at_from_expires_in(int(result.expires_in))
            except (TypeError, ValueError):
                expires_at = None

        now_tokens: dict[str, Any] = {
            "version": 1,
            "client_id": XAI_OAUTH_CLIENT_ID,
            "access_token": access.strip(),
            "refresh_token": result.refresh_token,
            "token_type": (result.token_type or "Bearer"),
            "scope": result.scope or XAI_OAUTH_SCOPE,
            "expires_at": expires_at,
            "email": email,
            "subject": subject,
            "auth_method": "device_code",
            "reauth_required": False,
        }

        try:
            if self._on_success is not None:
                # Live path: complete_oauth_login (persist outside PR lock + rebuild).
                self._on_success(now_tokens, activate=activate)
            else:
                persist_oauth_login(self._data_dir, now_tokens, activate=activate)
        except Exception:  # noqa: BLE001
            _LOG.exception("oauth login complete failed")
            self._set_terminal(STATE_ERROR, DETAIL_OAUTH_REFRESH_FAILED, stop)
            return

        with self._lock:
            if self._poll_stop is not stop:
                return  # superseded
            self._state = STATE_SUCCESS
            self._detail = None
            self._email = email
            self._expires_at = expires_at
            self._device_code = None
            self._clear_pending_public_unlocked()

    def _set_terminal(self, state: str, detail: str | None, stop: threading.Event) -> None:
        with self._lock:
            if self._poll_stop is not stop and state != STATE_CANCELLED:
                # Superseded by a newer start(); do not clobber new session state.
                return
            self._state = state
            self._detail = detail
            self._device_code = None
            if state != STATE_PENDING:
                self._clear_pending_public_unlocked()


def _strip_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    """Defensive: drop any secret-looking keys from a public dict."""
    return {k: v for k, v in payload.items() if k not in _SECRET_KEYS}


__all__ = [
    "DEFAULT_JOIN_TIMEOUT_S",
    "OAuthDeviceSession",
    "STATE_CANCELLED",
    "STATE_ERROR",
    "STATE_IDLE",
    "STATE_PENDING",
    "STATE_SUCCESS",
]
