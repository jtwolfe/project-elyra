"""LLM client connection settings.

Scope: injectable dataclass for local OpenAI-compatible chat endpoints and
xAI OpenAI-compatible base URL join rules.

LocalClientConfig (PR1 interim): keeps host/port/health_url/use_reasoning/
reasoning_budget so server.py launch argv and supervisor health waits still
compile. Wire HTTP payload is OpenAI-compat (model required; no reasoning /
thinking_budget on wire; top_p/top_k optional None). Final base_url-only
reshape lands when server.py is removed.
"""

from __future__ import annotations

from dataclasses import dataclass

from elyra.llm.constants import (
    DEFAULT_CHAT_TEMPERATURE,
    DEFAULT_REASONING_BUDGET_TOKENS,
)


@dataclass(frozen=True)
class LocalClientConfig:
    """Local / self-hosted OpenAI-compatible chat endpoint.

    PR1 interim: host/port for launch + health; wire payload is OpenAI subset.
    """

    host: str = "127.0.0.1"
    port: int = 8080
    chat_path: str = "/v1/chat/completions"
    # Required on the wire for OpenAI-compat local POST (intentional change).
    model: str = "local"
    # Interim: still read by build_server_command argv (not HTTP payload).
    use_reasoning: bool = True
    # None / -1 = no CLI --reasoning-budget (per-request only).
    reasoning_budget: int | None = None
    connect_timeout: float = 10.0
    read_timeout: float = 600.0
    # Product temperature default; local sampling no longer ships Gemma trunc.
    temperature: float = DEFAULT_CHAT_TEMPERATURE
    # Optional nucleus / top-k; None → omit from chat payload.
    top_p: float | None = None
    top_k: int | None = None
    # Retained for config constructors / live_eval until launch path dies;
    # local HTTP payload no longer emits thinking_budget_tokens.
    default_reasoning_budget_tokens: int | None = DEFAULT_REASONING_BUDGET_TOKENS
    # Optional Bearer for for_local (unit-test / future self-hosted auth).
    # Never log; never put in status JSON.
    api_key: str | None = None

    @property
    def chat_url(self) -> str:
        path = self.chat_path if self.chat_path.startswith("/") else f"/{self.chat_path}"
        return f"http://{self.host}:{self.port}{path}"

    @property
    def health_url(self) -> str:
        return f"http://{self.host}:{self.port}/health"

    @property
    def request_timeout(self) -> float:
        return max(self.connect_timeout, self.read_timeout)


@dataclass(frozen=True)
class XaiClientConfig:
    """xAI OpenAI-compatible API config.

    ``base_url`` includes ``/v1`` (matches smoke ``API_BASE``). Paths are
    relative to that root — do **not** repeat ``/v1`` (avoids ``/v1/v1/...``).
    """

    base_url: str = "https://api.x.ai/v1"
    # Paths relative to base_url — NOT /v1/chat/completions.
    chat_path: str = "/chat/completions"
    models_path: str = "/models"
    connect_timeout: float = 10.0
    read_timeout: float = 120.0
    temperature: float = 0.7
    top_p: float | None = None
    # Always omit top_k on the wire for xai even if set.
    top_k: int | None = None
    use_reasoning: bool = False

    @staticmethod
    def _join(base: str, path: str) -> str:
        return base.rstrip("/") + (path if path.startswith("/") else f"/{path}")

    @property
    def chat_url(self) -> str:
        # → https://api.x.ai/v1/chat/completions
        return self._join(self.base_url, self.chat_path)

    @property
    def models_url(self) -> str:
        # → https://api.x.ai/v1/models
        return self._join(self.base_url, self.models_path)

    @property
    def request_timeout(self) -> float:
        return max(self.connect_timeout, self.read_timeout)
