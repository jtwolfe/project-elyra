"""Daemon SuperGrok credits poller owned by ElyraSupervisor.

HTTP runs only on this thread (KD26). Status / API paths may only *signal*
a poll — never await billing. Meter lock is held only for
``apply_credits_snapshot`` after HTTP completes.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from elyra.llm.auth import resolve_bearer
from elyra.llm.credits import (
    DEFAULT_BILLING_TIMEOUT_S,
    STATUS_AUTH_FAILED,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNSUPPORTED,
    CreditsSnapshot,
    fetch_billing,
)
from elyra.llm.usage import UsageMeter
from elyra.settings import UsageSettings

_LOG = logging.getLogger(__name__)

# WARNING log cooldown for auth_failed (design: 10 minutes).
_AUTH_LOG_COOLDOWN_S = 600.0

# Status-signal floor (never more frequent than 30s).
_STATUS_SIGNAL_FLOOR_S = 30.0

# Status-signal cap vs interval (design: min(interval, 60s)).
_STATUS_SIGNAL_CAP_S = 60.0


class CreditsPoller:
    """Background timer that polls SuperGrok billing and injects snapshots.

    No-op when constructed with ``enabled=False`` (thread still starts but
    idles until stop) — callers normally skip start entirely when usage or
    poll flags are off.
    """

    def __init__(
        self,
        *,
        meter: UsageMeter,
        usage_settings: UsageSettings,
        data_dir: Path,
        credential_source: str,
        grok_auth_path: Path | None = None,
        first_delay_s: float = 1.0,
        http_timeout_s: float = DEFAULT_BILLING_TIMEOUT_S,
        fetch_fn: Callable[..., CreditsSnapshot] | None = None,
        resolve_fn: Callable[..., Any] | None = None,
        get_credential_source: Callable[[], str] | None = None,
        get_usage_settings: Callable[[], UsageSettings] | None = None,
        enabled: bool = True,
    ) -> None:
        self._meter = meter
        self._usage_settings = usage_settings
        self._data_dir = Path(data_dir)
        self._credential_source = credential_source
        self._grok_auth_path = grok_auth_path
        self._first_delay_s = max(0.0, float(first_delay_s))
        self._http_timeout_s = float(http_timeout_s)
        self._fetch_fn = fetch_fn or fetch_billing
        self._resolve_fn = resolve_fn or resolve_bearer
        self._get_credential_source = get_credential_source
        self._get_usage_settings = get_usage_settings
        self._enabled = bool(enabled)

        self._stop = threading.Event()
        self._poll_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._poll_lock = threading.Lock()  # one poll in flight
        self._meta_lock = threading.Lock()
        self._last_attempt_mono: float = 0.0
        self._auth_log_mono: float = 0.0
        # api_key: once unsupported, skip HTTP until credential_source changes.
        self._api_key_unsupported = False
        self._unsupported_for_source: str | None = None

    # --- public API ----------------------------------------------------------

    def start(self) -> None:
        """Start daemon poller thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="elyra-credits-poller",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, join_timeout_s: float | None = None) -> None:
        """Signal stop; best-effort join (daemon so process never hangs).

        Default join waits at least HTTP timeout + margin so an in-flight
        ``urlopen`` can finish and skip apply (see ``_apply`` stop check).
        """
        if join_timeout_s is None:
            join_timeout_s = max(2.0, float(self._http_timeout_s) + 1.0)
        self._stop.set()
        self._poll_requested.set()  # wake waiters
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=float(join_timeout_s))
        self._thread = None

    def request_poll(self) -> None:
        """Non-blocking status-path signal (KD26: never HTTP, never await).

        Debounced: only wakes the poller when
        ``now - last_attempt >= max(30, min(interval, 60))``.
        """
        if not self._enabled or self._stop.is_set():
            return
        settings = self._current_settings()
        if not settings.enabled or not settings.credits_poll_enabled:
            return
        interval = float(settings.credits_poll_interval_s)
        threshold = max(
            _STATUS_SIGNAL_FLOOR_S,
            min(interval, _STATUS_SIGNAL_CAP_S),
        )
        with self._meta_lock:
            last = self._last_attempt_mono
        now = time.monotonic()
        if last > 0.0 and (now - last) < threshold:
            return
        self._poll_requested.set()

    @property
    def last_attempt_mono(self) -> float:
        with self._meta_lock:
            return self._last_attempt_mono

    # --- internals -----------------------------------------------------------

    def _current_settings(self) -> UsageSettings:
        if self._get_usage_settings is not None:
            try:
                return self._get_usage_settings()
            except Exception:  # noqa: BLE001
                pass
        return self._usage_settings

    def _current_credential_source(self) -> str:
        if self._get_credential_source is not None:
            try:
                return str(self._get_credential_source() or self._credential_source)
            except Exception:  # noqa: BLE001
                pass
        return self._credential_source

    def _run(self) -> None:
        # First poll soon after start (design: 0–2s delay).
        if self._first_delay_s > 0:
            if self._stop.wait(timeout=self._first_delay_s):
                return
        while not self._stop.is_set():
            settings = self._current_settings()
            # Clear *before* poll so a status signal during HTTP/apply remains
            # set for the next wait (would be lost if cleared after poll).
            self._poll_requested.clear()
            if (
                self._enabled
                and settings.enabled
                and settings.credits_poll_enabled
            ):
                self._poll_once()
            if self._stop.is_set():
                return
            interval = max(30.0, float(settings.credits_poll_interval_s))
            # Wait interval or until status signals / stop.
            self._poll_requested.wait(timeout=interval)
            if self._stop.is_set():
                return
            # If woken by signal, loop immediately; otherwise interval elapsed.

    def _poll_once(self) -> None:
        # Skip overlapping ticks (one poll in flight).
        if not self._poll_lock.acquire(blocking=False):
            return
        try:
            self._do_poll()
        finally:
            self._poll_lock.release()

    def _do_poll(self) -> None:
        settings = self._current_settings()
        source = self._current_credential_source()
        with self._meta_lock:
            self._last_attempt_mono = time.monotonic()
            # Reset terminal unsupported when source changes.
            if (
                self._api_key_unsupported
                and self._unsupported_for_source is not None
                and self._unsupported_for_source != source
            ):
                self._api_key_unsupported = False
                self._unsupported_for_source = None

        if self._api_key_unsupported and source == "api_key":
            # Still inject unsupported snapshot so detail stays honest; no HTTP.
            snap = CreditsSnapshot(
                status=STATUS_UNSUPPORTED,
                ok=False,
                detail="api_key_billing_unsupported",
            )
            self._apply(snap)
            return

        # Resolve bearer outside meter lock.
        try:
            resolution = self._resolve_fn(
                source=source,
                data_dir=self._data_dir,
                grok_auth_path=self._grok_auth_path,
            )
        except Exception as exc:  # noqa: BLE001
            snap = CreditsSnapshot(
                status="error",
                ok=False,
                detail=f"resolve_failed:{type(exc).__name__}"[:200],
            )
            self._apply(snap)
            return

        if not getattr(resolution, "ok", False) or not getattr(resolution, "token", None):
            detail = getattr(resolution, "detail", None) or "credential_unavailable"
            # Fail-soft only — do NOT set _api_key_unsupported here. Terminal
            # unsupported is reserved for real HTTP 401/403/404 from fetch so
            # cold-start missing key / later repair can still try billing.
            snap = CreditsSnapshot(
                status=STATUS_AUTH_FAILED if source != "api_key" else STATUS_ERROR,
                ok=False,
                detail=str(detail),
            )
            self._apply(snap)
            self._maybe_log_auth(snap)
            return

        # HTTP outside meter lock.
        try:
            snap = self._fetch_fn(
                settings.credits_base_url,
                resolution.token,
                self._http_timeout_s,
                credential_source=source,
            )
        except Exception as exc:  # noqa: BLE001
            # fetch_billing is fail-soft; this is a safety net for injectables.
            snap = CreditsSnapshot(
                status="error",
                ok=False,
                detail=f"fetch_raised:{type(exc).__name__}"[:200],
            )

        # Terminal api_key unsupported only after a real fetch_billing result.
        if source == "api_key" and (snap.status or "") == STATUS_UNSUPPORTED:
            with self._meta_lock:
                self._api_key_unsupported = True
                self._unsupported_for_source = source

        # Skip apply after stop so shutdown does not persist post-teardown.
        if self._stop.is_set():
            return

        self._apply(snap)
        self._maybe_log_auth(snap)
        if (snap.status or "") == STATUS_OK:
            _LOG.debug(
                "credits.poll ok percent=%s period_id=%s",
                snap.credit_usage_percent,
                snap.period_id,
            )

    def _apply(self, snap: CreditsSnapshot) -> None:
        if self._stop.is_set():
            return
        try:
            # apply_credits_snapshot takes meter lock + may adopt/roll + persist.
            self._meter.apply_credits_snapshot(snap)
        except Exception as exc:  # noqa: BLE001
            # Durable state rolled back inside meter; log and continue.
            _LOG.warning("credits.apply_failed: %s", type(exc).__name__)

    def _maybe_log_auth(self, snap: CreditsSnapshot) -> None:
        if (snap.status or "") != STATUS_AUTH_FAILED:
            return
        now = time.monotonic()
        with self._meta_lock:
            if now - self._auth_log_mono < _AUTH_LOG_COOLDOWN_S:
                return
            self._auth_log_mono = now
        # Never log bearer. Do not set chat credential_ok=false solely from this.
        _LOG.warning(
            "credits.poll auth_failed detail=%s (chat credentials unchanged)",
            snap.detail or "auth_failed",
        )
