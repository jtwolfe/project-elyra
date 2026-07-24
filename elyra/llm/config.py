"""LLM server connection settings.

Scope: injectable dataclass for llama-server chat endpoint and xAI OpenAI-
compatible base URL join rules.
Product sampling defaults (KD13): temperature + Gemma card truncation +
default_reasoning_budget_tokens live here; HttpChatClient falls back when
chat_completion kwargs are None.
"""

from __future__ import annotations

from dataclasses import dataclass

from elyra.llm.constants import (
    DEFAULT_CHAT_TEMPERATURE,
    DEFAULT_REASONING_BUDGET_TOKENS,
    GEMMA_TOP_K,
    GEMMA_TOP_P,
)


@dataclass(frozen=True)
class LlamaServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    chat_path: str = "/v1/chat/completions"
    use_reasoning: bool = True
    # None / -1 = no CLI --reasoning-budget (per-request only).
    reasoning_budget: int | None = None
    connect_timeout: float = 10.0
    read_timeout: float = 600.0
    # Product default from constants (dogfood thrash: 1.0; Stage 1 OFAT was 0.6).
    temperature: float = DEFAULT_CHAT_TEMPERATURE
    # Gemma card nucleus / top-k truncation (KD7). None → omit from chat payload.
    top_p: float | None = GEMMA_TOP_P
    top_k: int | None = GEMMA_TOP_K
    # Stage 2: per-request private channel cap (Python → wire thinking_budget_tokens).
    # None → omit when reasoning=True. Product ships non-None after live OFAT.
    default_reasoning_budget_tokens: int | None = DEFAULT_REASONING_BUDGET_TOKENS

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
