"""LLM server connection settings.

Scope: injectable dataclass for llama-server chat endpoint.
Product sampling defaults (KD13): temperature + Gemma card truncation live here;
HttpChatClient falls back when chat_completion kwargs are None.
"""

from __future__ import annotations

from dataclasses import dataclass

from elyra.llm.constants import (
    DEFAULT_CHAT_TEMPERATURE,
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
    # Stage 1 product default (live OFAT on S-mono: 0.6 beat 0.2/0.4 + card trunc).
    temperature: float = DEFAULT_CHAT_TEMPERATURE
    # Gemma card nucleus / top-k truncation (KD7). None → omit from chat payload.
    top_p: float | None = GEMMA_TOP_P
    top_k: int | None = GEMMA_TOP_K

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
