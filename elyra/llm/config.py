"""LLM server connection settings.

Scope: injectable dataclass for llama-server chat endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass


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
    temperature: float = 0.2
    # None → omit from chat payload (server default). Product ship later.
    top_p: float | None = None
    top_k: int | None = None

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
