"""Shared live provider handles for supervisor + API (not serialized to status).

Owns rebuild_chat_stack / can_open_model_moment. Worker.client is rebindable
after credential repair without process restart. OAuth live rebind:
``on_access_refreshed`` / ``complete_oauth_login`` / keep-alive (KD17).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from elyra.llm.auth import (
    SOURCE_API_KEY,
    SOURCE_XAI_OAUTH,
    VALID_SOURCES,
    api_key_is_configured,
    auth_secret_values_for_redaction,
    delete_stored_api_key,
    resolve_bearer,
    write_stored_api_key,
)
from elyra.llm.client import (
    ChatClient,
    FailingChatClient,
    HttpChatClient,
    StubChatClient,
    UsageGatedChatClient,
)
from elyra.llm.config import LocalClientConfig, XaiClientConfig
from elyra.llm.models import (
    CURATED_XAI_MODELS,
    label_for_model,
    list_remote_models,
    models_for_picker,
)
from elyra.llm.oauth_store import (
    delete_oauth_bundle,
    oauth_is_configured,
    persist_oauth_login,
)
from elyra.llm.provider_prefs import (
    DEFAULT_REASONING_EFFORT,
    resolve_reasoning_effort,
    resolve_reasoning_effort_strict,
    update_provider_prefs,
)
from elyra.llm.queue import ChatRequestGate
from elyra.llm.usage import UsageMeter, UsageSnapshot
from elyra.llm.xai_oauth import DEFAULT_SKEW_S, ensure_fresh_access
from elyra.settings import UsageSettings

if TYPE_CHECKING:
    from elyra.presence.worker import PresenceWorker
    from elyra.runtime.credits_poller import CreditsPoller
    from elyra.runtime.state import RuntimeState

_LOG = logging.getLogger(__name__)

# OAuth keep-alive: check interval when access is still far from expiry.
_OAUTH_KEEPALIVE_INTERVAL_S = 60.0


def _usage_status_disabled_placeholder() -> dict[str, Any]:
    """Stable shape when meter missing or usage.enabled is false."""
    return {
        "enabled": False,
        "week_remaining_fraction": 1.0,
        "day_remaining_fraction": 1.0,
        "hour_remaining_fraction": 1.0,
        "hard_stop": None,
        "hard_stop_reason": None,
        "override_active": False,
        "pace_band": "green",
        "pace_ratio": 0.0,
        "burst_remaining_tokens": 0,
        "burst_max_tokens": 0,
        "period_id": "",
        "period_authority": "iso",
        "day_hard_stop_enabled": False,
        "hour_hard_stop_enabled": False,
        "day_soft_exhausted": False,
        "hour_soft_exhausted": False,
        "week_cached_tokens": 0,
        "week_stt_calls": 0,
        "week_tts_calls": 0,
        "elyra_week_budget_tokens": 0,
        "weekly_allowed_fraction": 0.5,
        "credit_usage_percent": None,
        "credits_status": None,
        "throttle_advice": {
            "band": "green",
            "pace_ratio": 0.0,
            "suggest_economy_model": False,
            "delay_factor": 1.0,
        },
        "supergrok": None,
    }


def _supergrok_status_from_snap(
    snap: UsageSnapshot, meter: UsageMeter
) -> dict[str, Any] | None:
    """Build nested supergrok status from meter cache + snapshot fields."""
    if hasattr(meter, "supergrok_for_status"):
        try:
            block = meter.supergrok_for_status()
            if block is not None:
                return block
        except Exception:  # noqa: BLE001 — fail-soft for status path
            _LOG.debug("supergrok_for_status failed", exc_info=True)
    # Fallback from snapshot-only fields (ISO / no poll yet).
    if (
        snap.credit_usage_percent is None
        and snap.credits_status is None
        and snap.period_authority != "supergrok"
    ):
        return None
    return {
        "credit_usage_percent": snap.credit_usage_percent,
        "period_start": None,
        "period_end": None,
        "period_id": snap.period_id,
        "period_authority": snap.period_authority,
        "product_usage": None,
        "fetched_at": None,
        "status": snap.credits_status,
        "stale": False,
    }


@dataclass
class ProviderRuntime:
    """Shared live handles for API + supervisor (not serialized to status)."""

    meter: UsageMeter | None
    http_client: HttpChatClient | None  # None if Failing/Stub only
    chat_client: ChatClient  # outermost client currently on worker
    worker: PresenceWorker | None  # for rebinding worker.client after rebuild
    usage_settings: UsageSettings
    xai_config: XaiClientConfig | None
    local_config: LocalClientConfig | None
    gate: ChatRequestGate | None
    prefs_path: Path
    data_dir: Path
    provider_name: str
    model: str
    model_label: str
    credential_source: str
    credential_ok: bool
    credential_detail: str | None
    credential_expires_at: str | None
    credential_email: str | None
    api_key_configured: bool
    models_available: list[str] = field(default_factory=list)
    base_url: str = "https://api.x.ai/v1"
    grok_auth_path: Path | None = None
    request_timeout_s: float = 120.0
    state: RuntimeState | None = None
    # Durable --stub-llm session flag: rebuild/apply_model must not install
    # live HTTP or Failing(local) and erase hermetic Stub posture.
    stub_llm: bool = False
    # SuperGrok credits poller (supervisor-owned); status may only *signal*.
    credits_poller: CreditsPoller | None = None
    # Resolved wire effort (always low|medium|high; default high).
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    # Last-known auth secret strings for tool-result redaction (never status).
    _auth_redaction_values: list[str] = field(default_factory=list, repr=False)
    # OAuth keep-alive daemon (started when source=xai_oauth + credential_ok).
    _oauth_keepalive_stop: threading.Event = field(
        default_factory=threading.Event, repr=False
    )
    _oauth_keepalive_thread: threading.Thread | None = field(
        default=None, repr=False
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def status_provider_fields(self) -> dict[str, Any]:
        """Non-secret provider block for /api/status."""
        with self._lock:
            oauth_cfg = oauth_is_configured(self.data_dir)
            return {
                "provider": self.provider_name,
                "model": self.model,
                "model_label": self.model_label,
                "base_url": self.base_url,
                "credential_source": self.credential_source,
                "credential_ok": self.credential_ok,
                "credential_detail": self.credential_detail,
                "credential_expires_at": self.credential_expires_at,
                "credential_email": self.credential_email,
                "api_key_configured": self.api_key_configured,
                "oauth_configured": oauth_cfg,
                "models_available": list(self.models_available),
                "reasoning_effort": resolve_reasoning_effort(self.reasoning_effort),
            }

    def auth_redaction_values(self) -> list[str]:
        """Return copy of last-known auth secrets for tool redaction."""
        with self._lock:
            if self._auth_redaction_values:
                return list(self._auth_redaction_values)
        # Fall back to disk read when snapshot empty.
        try:
            return auth_secret_values_for_redaction(self.data_dir)
        except Exception:  # noqa: BLE001
            return []

    def _refresh_auth_redaction_snapshot_unlocked(self) -> None:
        """Update in-memory redaction set from disk (caller holds lock optional)."""
        try:
            self._auth_redaction_values = auth_secret_values_for_redaction(self.data_dir)
        except Exception:  # noqa: BLE001
            self._auth_redaction_values = []

    def usage_status_block(self) -> dict[str, Any]:
        """Live meter.snapshot() or disabled placeholder. Called every GET.

        KD26: may only *signal* the credits poller — never billing HTTP and
        never await the poller. Returns immediately from last applied snapshot.

        Expands UsageSnapshot design fields for Glass: pace/burst, soft day/hour
        flags, SuperGrok object, throttle_advice. PATCH still only mutates
        hard_stop_override.
        """
        with self._lock:
            meter = self.meter
            enabled = bool(self.usage_settings.enabled)
            poller = self.credits_poller
            usage_settings = self.usage_settings
        # Non-blocking signal only (debounced inside poller).
        if poller is not None:
            try:
                poller.request_poll()
            except Exception:  # noqa: BLE001
                _LOG.debug("credits poller signal failed", exc_info=True)
        if meter is None or not enabled:
            return _usage_status_disabled_placeholder()
        snap = meter.snapshot()
        sg = _supergrok_status_from_snap(snap, meter)
        band = snap.pace_band
        suggest = bool(
            usage_settings.auto_throttle_model and band in ("yellow", "red")
        )
        return {
            "enabled": snap.enabled,
            "week_remaining_fraction": snap.week_remaining_fraction,
            "day_remaining_fraction": snap.day_remaining_fraction,
            "hour_remaining_fraction": snap.hour_remaining_fraction,
            "hard_stop": snap.hard_stop,
            "hard_stop_reason": snap.hard_stop_reason,
            "override_active": snap.override_active,
            "week_used_tokens": snap.week_used_tokens,
            "day_used_tokens": snap.day_used_tokens,
            "hour_used_tokens": snap.hour_used_tokens,
            "week_limit_tokens": snap.week_limit_tokens,
            "day_limit_tokens": snap.day_limit_tokens,
            "hour_limit_tokens": snap.hour_limit_tokens,
            "last_record_at": snap.last_record_at,
            # v2 pace / burst / period (primary Glass meters)
            "pace_band": snap.pace_band,
            "pace_ratio": snap.pace_ratio,
            "burst_remaining_tokens": snap.burst_remaining_tokens,
            "burst_max_tokens": snap.burst_max_tokens,
            "period_id": snap.period_id,
            "period_authority": snap.period_authority,
            "day_hard_stop_enabled": snap.day_hard_stop_enabled,
            "hour_hard_stop_enabled": snap.hour_hard_stop_enabled,
            "day_soft_exhausted": snap.day_soft_exhausted,
            "hour_soft_exhausted": snap.hour_soft_exhausted,
            "week_cached_tokens": snap.week_cached_tokens,
            "week_stt_calls": int(getattr(snap, "week_stt_calls", 0) or 0),
            "week_tts_calls": int(getattr(snap, "week_tts_calls", 0) or 0),
            "elyra_week_budget_tokens": snap.week_limit_tokens,
            "weekly_allowed_fraction": float(usage_settings.weekly_allowed_fraction),
            "credit_usage_percent": snap.credit_usage_percent,
            "credits_status": snap.credits_status,
            "throttle_advice": {
                "band": band,
                "pace_ratio": float(snap.pace_ratio),
                "suggest_economy_model": suggest,
                "delay_factor": 1.0,
            },
            "supergrok": sg,
        }

    def media_remote_success_cb(self) -> Any:
        """Optional callback for media layer: ``kind in {stt,tts}`` → meter.

        Binds ``meter.record_media_call`` without media importing usage
        (cycle-free). Returns None when meter unbound.
        """
        with self._lock:
            meter = self.meter
        if meter is None:
            return None

        def _cb(kind: str) -> None:
            meter.record_media_call(kind)

        return _cb

    def can_open_model_moment(self) -> bool:
        """Pre-claim gate: safe to open a model-using moment.

        provider=xai: credential_ok and (meter.can_call() if meter present and
          usage enabled; if meter missing while usage enabled → False).
          meter.can_call() is True when under all hard ceilings (account/week/
          optional day/hour) OR hard_stop_override ON. Soft yellow/red pace
          bands never refuse. No auto model throttle or hop-delay here.
        provider=local: FailingChatClient → False (local_not_implemented).
        Never opens moments that would only hit FailingChatClient noise.
        """
        with self._lock:
            provider = self.provider_name
            credential_ok = self.credential_ok
            meter = self.meter
            usage_enabled = bool(self.usage_settings.enabled)
            chat = self.chat_client

        if isinstance(chat, FailingChatClient):
            return False
        if provider == "xai" and not credential_ok:
            return False
        if not usage_enabled:
            return True if provider != "xai" or credential_ok else False
        if meter is None:
            # Usage enabled but meter not loaded yet — refuse until rebuild.
            return False
        return meter.can_call()

    def set_hard_stop_override(self, active: bool) -> dict[str, Any]:
        """Delegate to meter.set_hard_stop_override; return usage status block."""
        with self._lock:
            meter = self.meter
        if meter is None:
            return self.usage_status_block()
        meter.set_hard_stop_override(bool(active))
        return self.usage_status_block()

    def rebuild_chat_stack(self) -> None:
        """Normative live repair / rebind after credential or model material changes.

        Builds the new stack off to the side, then swaps under lock so a failed
        rebuild leaves the previous client and credential fields intact until
        a clean Failing fallback is committed.

        When ``stub_llm`` is set (``elyra start --stub-llm``), always reinstall
        StubChatClient — never live HTTP and never local Failing.
        """
        with self._lock:
            stub_llm = self.stub_llm
            provider = self.provider_name
            model = self.model
            source = self.credential_source
            data_dir = self.data_dir
            grok_path = self.grok_auth_path
            base_url = self.base_url
            timeout_s = self.request_timeout_s
            usage_settings = self.usage_settings
            xai_config = self.xai_config
            local_config = self.local_config
            meter = self.meter
            reasoning_effort = resolve_reasoning_effort(self.reasoning_effort)

        if stub_llm:
            self._rebuild_stub(usage_settings=usage_settings, meter=meter)
            return

        if provider == "local":
            self._rebuild_local(
                usage_settings=usage_settings,
                local_config=local_config,
                meter=meter,
            )
            return

        # --- xai path ---
        # Pure resolve (no rebind hook on this path — new client *is* the rebind).
        resolution = resolve_bearer(
            source=source,
            data_dir=data_dir,
            grok_auth_path=grok_path,
        )
        configured = resolution.api_key_configured

        if not resolution.ok or not resolution.token:
            failing = FailingChatClient(resolution.detail or "credential_unavailable")
            with self._lock:
                self.credential_ok = False
                self.credential_detail = resolution.detail
                self.credential_expires_at = resolution.expires_at
                self.credential_email = resolution.email
                self.api_key_configured = configured
                self.http_client = None
                self.chat_client = failing
                self._refresh_auth_redaction_snapshot_unlocked()
                self._bind_worker_unlocked()
                self._sync_state_unlocked()
            self._stop_oauth_keepalive()
            if self.state is not None:
                # Cred failures stay on credential_*; chat stack not ready (KD14).
                self.state.set_chat_posture(ready=False, error=None)
            return

        # Ensure meter so repair keeps window state (even after cold-start fail).
        if meter is None:
            meter = UsageMeter.load(data_dir, usage_settings)
            with self._lock:
                self.meter = meter

        cfg = xai_config or XaiClientConfig(
            base_url=base_url,
            read_timeout=timeout_s,
        )
        # Wire 401 refresh_cb only for xai_oauth (KD17 path C).
        refresh_cb = (
            self._make_chat_refresh_cb() if source == SOURCE_XAI_OAUTH else None
        )
        try:
            http = HttpChatClient.for_xai(
                cfg,
                model=model,
                bearer_token=resolution.token,
                reasoning_effort=reasoning_effort,
                refresh_cb=refresh_cb,
            )
            if usage_settings.enabled:
                outer: ChatClient = UsageGatedChatClient(http, meter)
            else:
                outer = http
        except Exception:
            _LOG.exception("rebuild_chat_stack: failed to build xai client")
            failing = FailingChatClient("client_build_failed")
            with self._lock:
                self.credential_ok = False
                self.credential_detail = "client_build_failed"
                self.credential_expires_at = resolution.expires_at
                self.credential_email = resolution.email
                self.api_key_configured = configured
                self.http_client = None
                self.chat_client = failing
                self._bind_worker_unlocked()
                self._sync_state_unlocked()
            self._stop_oauth_keepalive()
            if self.state is not None:
                self.state.set_chat_posture(ready=False, error=None)
            return

        with self._lock:
            self.credential_ok = True
            self.credential_detail = None
            self.credential_expires_at = resolution.expires_at
            self.credential_email = resolution.email
            self.api_key_configured = configured
            self.http_client = http
            self.chat_client = outer
            self.xai_config = cfg
            self._refresh_auth_redaction_snapshot_unlocked()
            self._bind_worker_unlocked()
            self._sync_state_unlocked()
        if self.state is not None:
            self.state.set_chat_posture(ready=True, error=None)

        if source == SOURCE_XAI_OAUTH:
            self._start_oauth_keepalive()
        else:
            self._stop_oauth_keepalive()

        # Best-effort models refresh (network); never undo a successful rebuild.
        try:
            self.refresh_models()
        except Exception:  # noqa: BLE001 — best-effort
            _LOG.debug("refresh_models after rebuild failed", exc_info=True)

    def _mark_oauth_credential_failed(
        self,
        *,
        detail: str | None,
        expires_at: str | None = None,
        email: str | None = None,
    ) -> None:
        """Fail-closed oauth status for Glass CTA (short lock; no network)."""
        with self._lock:
            self.credential_ok = False
            self.credential_detail = detail or "oauth_refresh_failed"
            if expires_at is not None:
                self.credential_expires_at = expires_at
            if email is not None:
                self.credential_email = email
            self._sync_state_unlocked()
        if self.state is not None:
            self.state.set_chat_posture(ready=False, error=None)

    def _make_chat_refresh_cb(self):
        """Build 401 refresh_cb: force ensure_fresh → on_access_refreshed → token.

        On failed force refresh: set credential_ok=false + detail so Glass shows
        re-auth CTA (design live-refresh fail path).
        """

        def _cb() -> str | None:
            try:
                fresh = ensure_fresh_access(self.data_dir, force=True)
            except Exception:  # noqa: BLE001
                _LOG.warning("chat 401 refresh_cb: ensure_fresh failed", exc_info=True)
                self._mark_oauth_credential_failed(detail="oauth_refresh_failed")
                return None
            if not fresh.ok or not fresh.access_token:
                self._mark_oauth_credential_failed(
                    detail=fresh.detail or "oauth_refresh_failed",
                    expires_at=fresh.expires_at,
                    email=fresh.email,
                )
                return None
            self.on_access_refreshed(
                fresh.access_token,
                fresh.expires_at,
                fresh.email,
            )
            return fresh.access_token

        return _cb

    def on_access_refreshed(
        self,
        access: str | None,
        expires_at: str | None = None,
        email: str | None = None,
    ) -> None:
        """Rebind live HttpChatClient bearer after OAuth rotation (KD17 path B).

        Hold lock only for field/bearer swap — never network I/O.
        Clears prior fail-closed (credential_ok=True) so keep-alive recovery
        and 401 success both restore Glass posture.
        """
        if not access or not isinstance(access, str) or not access.strip():
            return
        token = access.strip()
        with self._lock:
            http = self.http_client
            if http is not None:
                http.set_bearer_token(token)
            self.credential_expires_at = expires_at
            if email is not None:
                self.credential_email = email
            # Recover credential_ok after rebind (even if http missing, status
            # reflects fresh access so status/Glass clear re-auth CTA).
            self.credential_ok = True
            self.credential_detail = None
            self._refresh_auth_redaction_snapshot_unlocked()
            self._sync_state_unlocked()
        if self.state is not None and http is not None:
            self.state.set_chat_posture(ready=True, error=None)
        _LOG.debug("oauth access rebound on live chat client")

    def complete_oauth_login(
        self,
        tokens: Any,
        *,
        activate: bool = True,
    ) -> dict[str, Any]:
        """Persist OAuth login then live rebind/rebuild (KD13).

        Lock/I/O order: (1) disk+prefs outside lock, (2) short lock for source
        + redaction, (3) rebuild outside lock when activate or source is oauth.
        """
        # (1) Disk + prefs WITHOUT holding ProviderRuntime._lock
        persist_oauth_login(self.data_dir, tokens, activate=activate)

        # (2) Under lock: in-memory source + redaction snapshot only
        with self._lock:
            if activate:
                self.credential_source = SOURCE_XAI_OAUTH
            self._refresh_auth_redaction_snapshot_unlocked()
            # do not rebuild under this lock
            active_source = self.credential_source

        # (3) Outside lock: rebuild uses its own lock discipline
        if activate or active_source == SOURCE_XAI_OAUTH:
            self.rebuild_chat_stack()

        return self.status_provider_fields()

    def logout_xai_oauth(self) -> dict[str, Any]:
        """Delete OAuth bundle + tmp; clear redaction; rebuild if source oauth."""
        delete_oauth_bundle(self.data_dir)
        with self._lock:
            self._auth_redaction_values = []
            source = self.credential_source
        if source == SOURCE_XAI_OAUTH:
            self.rebuild_chat_stack()
        else:
            # Leave other source stack intact; still refresh status oauth flag.
            pass
        return self.status_provider_fields()

    def _start_oauth_keepalive(self) -> None:
        """Start OAuth keep-alive daemon if not already running."""
        t = self._oauth_keepalive_thread
        if t is not None and t.is_alive():
            return
        self._oauth_keepalive_stop.clear()
        thread = threading.Thread(
            target=self._oauth_keepalive_loop,
            name="elyra-oauth-keepalive",
            daemon=True,
        )
        self._oauth_keepalive_thread = thread
        thread.start()

    def _stop_oauth_keepalive(self) -> None:
        """Signal keep-alive thread to stop (best-effort)."""
        self._oauth_keepalive_stop.set()
        t = self._oauth_keepalive_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=1.0)
        self._oauth_keepalive_thread = None

    def stop_background_tasks(self) -> None:
        """Stop oauth keep-alive (supervisor shutdown hook)."""
        self._stop_oauth_keepalive()

    def _oauth_keepalive_loop(self) -> None:
        """Proactive ensure_fresh + rebind (KD17 path B).

        Always calls ensure_fresh while source is xai_oauth (does **not** gate
        on credential_ok). Transient ``oauth_refresh_failed`` stays retryable;
        durable ``oauth_reauth_required`` / missing tokens still fail-closed
        each tick (disk-only cheap). Success after failure rebinds via
        ``on_access_refreshed`` so credential_ok recovers without restart.
        """
        stop = self._oauth_keepalive_stop
        while not stop.is_set():
            with self._lock:
                source = self.credential_source
                prior_ok = self.credential_ok
                provider = self.provider_name
            if provider != "xai" or source != SOURCE_XAI_OAUTH:
                # Wrong source/provider — idle until rebuild switches us.
                if stop.wait(timeout=_OAUTH_KEEPALIVE_INTERVAL_S):
                    return
                continue
            try:
                # Never hold provider lock across refresh HTTP.
                fresh = ensure_fresh_access(self.data_dir, skew_s=DEFAULT_SKEW_S)
            except Exception:  # noqa: BLE001
                _LOG.debug("oauth keep-alive ensure_fresh failed", exc_info=True)
                # Treat as transient — leave status; retry next tick.
                fresh = None
            if fresh is not None and fresh.ok and fresh.access_token:
                # Rebind when rotated, or recover after prior fail-closed.
                if fresh.rotated or not prior_ok:
                    self.on_access_refreshed(
                        fresh.access_token,
                        fresh.expires_at,
                        fresh.email,
                    )
            elif fresh is not None and not fresh.ok:
                # Fail closed (invalid_grant durable or transient expired).
                # Keep looping so retryable oauth_refresh_failed can recover.
                self._mark_oauth_credential_failed(
                    detail=fresh.detail,
                    expires_at=fresh.expires_at,
                    email=fresh.email,
                )
            # Wake every ~60s (or sooner if stop set).
            if stop.wait(timeout=_OAUTH_KEEPALIVE_INTERVAL_S):
                return

    def _rebuild_stub(
        self,
        *,
        usage_settings: UsageSettings,
        meter: UsageMeter | None,
    ) -> None:
        """Hermetic --stub-llm posture: Stub only; never HTTP / never local Failing."""
        if meter is None and usage_settings.enabled:
            meter = UsageMeter.load(self.data_dir, usage_settings)
            with self._lock:
                self.meter = meter
        with self._lock:
            provider = self.provider_name
            self.http_client = None
            self.chat_client = StubChatClient()
            # Match cold-start stub: credential_ok True; posture via chat_error.
            self.credential_ok = True
            self.credential_detail = None
            if provider == "local":
                self.models_available = ["local"]
            self._bind_worker_unlocked()
            self._sync_state_unlocked()
        if self.state is not None:
            self.state.set_chat_posture(ready=False, error="stub_llm")

    def _rebuild_local(
        self,
        *,
        usage_settings: UsageSettings,
        local_config: LocalClientConfig | None,
        meter: UsageMeter | None,
    ) -> None:
        """Local provider is unimplemented — Failing only; never for_local HTTP."""
        if meter is None and usage_settings.enabled:
            meter = UsageMeter.load(self.data_dir, usage_settings)
            with self._lock:
                self.meter = meter
        cfg = local_config or LocalClientConfig()
        failing = FailingChatClient("local_not_implemented")
        with self._lock:
            self.credential_ok = True  # local has no xai creds
            self.credential_detail = "local_not_implemented"
            self.http_client = None
            self.chat_client = failing
            self.local_config = cfg
            self.models_available = ["local"]
            self._bind_worker_unlocked()
            self._sync_state_unlocked()
        if self.state is not None:
            self.state.set_chat_posture(ready=False, error="local_not_implemented")

    def _bind_worker_unlocked(self) -> None:
        worker = self.worker
        if worker is not None:
            worker.client = self.chat_client

    def _sync_state_unlocked(self) -> None:
        state = self.state
        if state is None:
            return
        state.set_provider(
            provider_name=self.provider_name,
            model=self.model,
            model_label=self.model_label,
            base_url=self.base_url,
            credential_source=self.credential_source,
            credential_ok=self.credential_ok,
            credential_detail=self.credential_detail,
            credential_expires_at=self.credential_expires_at,
            credential_email=self.credential_email,
            api_key_configured=self.api_key_configured,
        )

    def refresh_models(self) -> list[str]:
        """Populate models_available (best-effort remote list for xai)."""
        with self._lock:
            provider = self.provider_name
            model = self.model
            base_url = self.base_url
            source = self.credential_source
            data_dir = self.data_dir
            grok_path = self.grok_auth_path
            credential_ok = self.credential_ok
            timeout_s = self.request_timeout_s

        if provider == "local":
            available = ["local"]
            with self._lock:
                self.models_available = available
            return list(available)

        listed: list[str] | None = None
        if credential_ok:
            resolution = resolve_bearer(
                source=source,
                data_dir=data_dir,
                grok_auth_path=grok_path,
            )
            if resolution.ok and resolution.token:
                try:
                    listed = list_remote_models(
                        base_url,
                        resolution.token,
                        timeout=min(30.0, timeout_s),
                    )
                except Exception as exc:  # noqa: BLE001 — fall back to curated
                    _LOG.warning("list_remote_models failed: %s", exc)
                    listed = None

        available = models_for_picker(listed, fallback=CURATED_XAI_MODELS, current=model)
        with self._lock:
            self.models_available = available
        return list(available)

    def apply_model(self, model: str) -> None:
        """Validate, persist prefs, set_model on http_client if present,
        else rebuild_chat_stack if credential_ok.

        Under ``stub_llm``, only the model id / prefs update — the client stays
        Stub (never live HTTP, never local Failing).
        """
        mid = (model or "").strip()
        if not mid:
            raise ValueError("model must be a non-empty string")
        with self._lock:
            available = list(self.models_available)
            data_dir = self.data_dir
        if available and mid not in available and mid != "local":
            # Allow unknown when list is only curated fallback (operator override).
            # Strict unknown check is for API layer; rebuild still accepts wire ids.
            pass
        label = label_for_model(mid)
        # Load-merge-save so credential_source / reasoning_effort are not clobbered.
        update_provider_prefs(data_dir, model=mid)
        with self._lock:
            self.model = mid
            self.model_label = label
            self._sync_state_unlocked()
            http = self.http_client
            credential_ok = self.credential_ok
            stub_llm = self.stub_llm
        if stub_llm:
            # Durable hermetic posture: prefs updated; keep StubChatClient.
            return
        if http is not None:
            http.set_model(mid)
        elif credential_ok:
            self.rebuild_chat_stack()

    def apply_credential_source(self, source: str) -> Any:
        """Resolve target first; on ok persist + rebuild_chat_stack(); on fail leave previous."""
        src = (source or "").strip()
        if src not in VALID_SOURCES:
            raise ValueError(f"unknown credential_source: {source!r}")
        with self._lock:
            data_dir = self.data_dir
            grok_path = self.grok_auth_path
            prev_source = self.credential_source
        resolution = resolve_bearer(
            source=src,
            data_dir=data_dir,
            grok_auth_path=grok_path,
        )
        if not resolution.ok:
            # Leave previous source/stack intact.
            return resolution
        # Load-merge-save so model / reasoning_effort are not clobbered.
        update_provider_prefs(data_dir, credential_source=src)
        with self._lock:
            self.credential_source = src
            self._sync_state_unlocked()
        self.rebuild_chat_stack()
        if prev_source != src:
            _LOG.info("credential_source switched %s → %s", prev_source, src)
        return resolution

    def apply_reasoning_effort(self, effort: str) -> None:
        """Validate, persist prefs, set_reasoning_effort on http_client if present.

        Does not rebuild the chat stack. Credential / bearer / meter unchanged.
        Under stub_llm: prefs + runtime field update only.
        """
        e = resolve_reasoning_effort_strict(effort)
        with self._lock:
            data_dir = self.data_dir
        # Load-merge-save so model / credential_source are not clobbered.
        update_provider_prefs(data_dir, reasoning_effort=e)
        with self._lock:
            self.reasoning_effort = e
            http = self.http_client
        if http is not None:
            http.set_reasoning_effort(e)
        _LOG.info("reasoning_effort set to %s", e)

    def put_api_key(self, api_key: str) -> None:
        """write_stored_api_key (atomic); api_key_configured=True;
        if active source is api_key: rebuild_chat_stack().
        Does not auto-switch source.
        """
        write_stored_api_key(self.data_dir, api_key)
        with self._lock:
            self.api_key_configured = True
            source = self.credential_source
            self._sync_state_unlocked()
        if source == SOURCE_API_KEY:
            self.rebuild_chat_stack()

    def delete_api_key(self) -> None:
        """Delete file; if active source api_key and no env: rebuild → Failing."""
        delete_stored_api_key(self.data_dir)
        configured = api_key_is_configured(self.data_dir)
        with self._lock:
            self.api_key_configured = configured
            source = self.credential_source
            self._sync_state_unlocked()
        if source == SOURCE_API_KEY:
            self.rebuild_chat_stack()


def credential_detail_message(detail: str | None) -> str | None:
    """Human-readable one-liner for CLI posture (status-safe codes only)."""
    if not detail:
        return None
    messages = {
        "missing_auth_json": (
            "missing auth.json — run `grok login` or paste API key in Status"
        ),
        "invalid_auth_json": "invalid auth.json — re-run `grok login`",
        "missing_token": "auth.json has no access token — re-run `grok login`",
        "token_expired": (
            "Grok Build token expired — run `grok login` or switch to API key"
        ),
        "missing_api_key": (
            "missing API key — set XAI_API_KEY or paste key in Status"
        ),
        "empty_api_key": "empty API key rejected",
        "unknown_source": "unknown credential source",
        "client_build_failed": "failed to build chat client",
        "credential_unavailable": "credentials unavailable",
        # OAuth (Elyra-owned login — not `grok login`)
        "missing_oauth_tokens": (
            "missing xAI login — use Glass “Log in with xAI” or `elyra auth login`"
        ),
        "invalid_oauth_tokens": (
            "invalid xAI login tokens — log in again via Glass or `elyra auth login`"
        ),
        "oauth_token_expired": (
            "xAI access expired — refresh failed; log in again via Glass"
        ),
        "oauth_refresh_failed": (
            "xAI token refresh failed — check network or log in again via Glass"
        ),
        "oauth_reauth_required": (
            "xAI login revoked — log in again via Glass or `elyra auth login`"
        ),
        "oauth_denied": "xAI login denied — try again via Glass or `elyra auth login`",
    }
    return messages.get(detail, detail)


def format_usage_posture(meter: UsageMeter | None, *, enabled: bool) -> str:
    """One-line usage remaining for startup print."""
    if not enabled or meter is None:
        return "disabled"
    snap = meter.snapshot()
    return (
        f"week {snap.week_remaining_fraction:.0%} · "
        f"day {snap.day_remaining_fraction:.0%} · "
        f"hour {snap.hour_remaining_fraction:.0%} remaining"
    )
