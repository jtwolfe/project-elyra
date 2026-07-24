"""Shared live provider handles for supervisor + API (not serialized to status).

Owns rebuild_chat_stack / can_open_model_moment. Worker.client is rebindable
after credential repair without process restart.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from elyra.llm.auth import (
    SOURCE_API_KEY,
    VALID_SOURCES,
    api_key_is_configured,
    delete_stored_api_key,
    resolve_bearer,
    write_stored_api_key,
)
from elyra.llm.client import (
    ChatClient,
    FailingChatClient,
    GatedChatClient,
    HttpChatClient,
    StubChatClient,
    UsageGatedChatClient,
)
from elyra.llm.config import LlamaServerConfig, XaiClientConfig
from elyra.llm.models import (
    CURATED_XAI_MODELS,
    label_for_model,
    list_remote_models,
    models_for_picker,
)
from elyra.llm.provider_prefs import ProviderPrefs, provider_prefs_path, save_provider_prefs
from elyra.llm.queue import LlamaServerGate
from elyra.llm.usage import UsageMeter
from elyra.settings import UsageSettings

if TYPE_CHECKING:
    from elyra.presence.worker import PresenceWorker
    from elyra.runtime.state import RuntimeState

_LOG = logging.getLogger(__name__)


@dataclass
class ProviderRuntime:
    """Shared live handles for API + supervisor (not serialized to status)."""

    meter: UsageMeter | None
    http_client: HttpChatClient | None  # None if Failing/Stub only
    chat_client: ChatClient  # outermost client currently on worker
    worker: PresenceWorker | None  # for rebinding worker.client after rebuild
    usage_settings: UsageSettings
    xai_config: XaiClientConfig | None
    llama_config: LlamaServerConfig | None
    gate: LlamaServerGate | None
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
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def status_provider_fields(self) -> dict[str, Any]:
        """Non-secret provider block for /api/status."""
        with self._lock:
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
                "models_available": list(self.models_available),
            }

    def usage_status_block(self) -> dict[str, Any]:
        """Live meter.snapshot() or disabled placeholder. Called every GET."""
        with self._lock:
            meter = self.meter
            enabled = bool(self.usage_settings.enabled)
        if meter is None or not enabled:
            return {
                "enabled": False,
                "week_remaining_fraction": 1.0,
                "day_remaining_fraction": 1.0,
                "hour_remaining_fraction": 1.0,
                "hard_stop": None,
                "hard_stop_reason": None,
                "override_active": False,
            }
        snap = meter.snapshot()
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
        }

    def can_open_model_moment(self) -> bool:
        """Pre-claim gate: safe to open a model-using moment.

        provider=xai: credential_ok and (meter.can_call() if meter present and
          usage enabled; if meter missing while usage enabled → False).
          meter.can_call() is True when under budget OR hard_stop_override ON.
        provider=local: meter.can_call() if enabled else True (llama readiness
          is separate; worker still needs a real client).
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
        """
        with self._lock:
            provider = self.provider_name
            model = self.model
            source = self.credential_source
            data_dir = self.data_dir
            grok_path = self.grok_auth_path
            base_url = self.base_url
            timeout_s = self.request_timeout_s
            usage_settings = self.usage_settings
            xai_config = self.xai_config
            llama_config = self.llama_config
            gate = self.gate
            meter = self.meter

        if provider == "local":
            self._rebuild_local(
                model=model,
                usage_settings=usage_settings,
                llama_config=llama_config,
                gate=gate,
                meter=meter,
            )
            return

        # --- xai path ---
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
                self._bind_worker_unlocked()
                self._sync_state_unlocked()
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
        try:
            http = HttpChatClient.for_xai(
                cfg,
                model=model,
                bearer_token=resolution.token,
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
            self._bind_worker_unlocked()
            self._sync_state_unlocked()

        # Best-effort models refresh (network); never undo a successful rebuild.
        try:
            self.refresh_models()
        except Exception:  # noqa: BLE001 — best-effort
            _LOG.debug("refresh_models after rebuild failed", exc_info=True)

    def _rebuild_local(
        self,
        *,
        model: str,
        usage_settings: UsageSettings,
        llama_config: LlamaServerConfig | None,
        gate: LlamaServerGate | None,
        meter: UsageMeter | None,
    ) -> None:
        cfg = llama_config or LlamaServerConfig()
        if meter is None and usage_settings.enabled:
            meter = UsageMeter.load(self.data_dir, usage_settings)
            with self._lock:
                self.meter = meter
        try:
            http = HttpChatClient.for_local(cfg)
            if gate is not None:
                gated: ChatClient = GatedChatClient(http, gate)
            else:
                gated = http
            if usage_settings.enabled and meter is not None:
                outer: ChatClient = UsageGatedChatClient(gated, meter)
            else:
                outer = gated
        except Exception:
            _LOG.exception("rebuild_chat_stack: failed to build local client")
            with self._lock:
                self.credential_ok = True  # local has no xai creds
                self.credential_detail = "local_client_build_failed"
                self.http_client = None
                self.chat_client = StubChatClient()
                self._bind_worker_unlocked()
                self._sync_state_unlocked()
            return

        with self._lock:
            self.credential_ok = True
            self.credential_detail = None
            self.http_client = http
            self.chat_client = outer
            self.llama_config = cfg
            self.models_available = ["local"]
            self._bind_worker_unlocked()
            self._sync_state_unlocked()

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
        else rebuild_chat_stack if credential_ok."""
        mid = (model or "").strip()
        if not mid:
            raise ValueError("model must be a non-empty string")
        with self._lock:
            available = list(self.models_available)
            http = self.http_client
            credential_ok = self.credential_ok
            data_dir = self.data_dir
            source = self.credential_source
        if available and mid not in available and mid != "local":
            # Allow unknown when list is only curated fallback (operator override).
            # Strict unknown check is for API layer; rebuild still accepts wire ids.
            pass
        label = label_for_model(mid)
        save_provider_prefs(
            data_dir,
            ProviderPrefs(model=mid, credential_source=source),
        )
        with self._lock:
            self.model = mid
            self.model_label = label
            self._sync_state_unlocked()
            http = self.http_client
            credential_ok = self.credential_ok
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
            model = self.model
        resolution = resolve_bearer(
            source=src,
            data_dir=data_dir,
            grok_auth_path=grok_path,
        )
        if not resolution.ok:
            # Leave previous source/stack intact.
            return resolution
        save_provider_prefs(
            data_dir,
            ProviderPrefs(model=model, credential_source=src),
        )
        with self._lock:
            self.credential_source = src
            self._sync_state_unlocked()
        self.rebuild_chat_stack()
        if prev_source != src:
            _LOG.info("credential_source switched %s → %s", prev_source, src)
        return resolution

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
