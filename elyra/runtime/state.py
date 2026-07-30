"""Process-level runtime status for the Web UI.

Holds stable provider/credential labels for debugging. Live usage fractions
come from ``UsageMeter.snapshot()`` / ``ProviderRuntime.usage_status_block()``
— never a durable cache on this object. Secrets never land here.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from elyra.llm.models import DEFAULT_XAI_MODEL, DEFAULT_XAI_MODEL_LABEL


@dataclass
class RuntimeState:
    started_at: float = field(default_factory=time.time)
    # Provider-neutral chat/inference posture (KD14) — not local-process-specific.
    chat_ready: bool = False
    chat_error: str | None = None
    # Provider / credential labels (no secrets, no live usage cache)
    provider_name: str = "xai"
    model: str = DEFAULT_XAI_MODEL
    model_label: str = DEFAULT_XAI_MODEL_LABEL
    base_url: str = "https://api.x.ai/v1"
    credential_source: str = "xai_oauth"
    credential_ok: bool = False
    credential_detail: str | None = None
    credential_expires_at: str | None = None
    credential_email: str | None = None
    api_key_configured: bool = False

    def set_chat_posture(
        self,
        *,
        ready: bool,
        error: str | None = None,
    ) -> None:
        """Update chat stack readiness (stub / failing / live HTTP)."""
        self.chat_ready = ready
        self.chat_error = error

    def set_provider(
        self,
        *,
        provider_name: str,
        model: str,
        model_label: str,
        base_url: str,
        credential_source: str,
        credential_ok: bool,
        credential_detail: str | None = None,
        credential_expires_at: str | None = None,
        credential_email: str | None = None,
        api_key_configured: bool = False,
    ) -> None:
        self.provider_name = provider_name
        self.model = model
        self.model_label = model_label
        self.base_url = base_url
        self.credential_source = credential_source
        self.credential_ok = credential_ok
        self.credential_detail = credential_detail
        self.credential_expires_at = credential_expires_at
        self.credential_email = credential_email
        self.api_key_configured = api_key_configured

    def snapshot(self) -> dict[str, Any]:
        return {
            "uptime_s": round(time.time() - self.started_at, 1),
            "chat_ready": self.chat_ready,
            "chat_error": self.chat_error,
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
        }


_state: RuntimeState | None = None
_lock = threading.Lock()


def set_runtime_state(state: RuntimeState) -> None:
    global _state
    with _lock:
        _state = state


def get_runtime_state() -> RuntimeState | None:
    with _lock:
        return _state
