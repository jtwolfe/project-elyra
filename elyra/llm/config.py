"""LLM client connection settings.

Scope: injectable dataclass for local OpenAI-compatible chat endpoints and
xAI OpenAI-compatible base URL join rules.

LocalClientConfig is OpenAI-compat endpoint shape only (no process/launch
fields). Supervisor / ProviderRuntime never call HttpChatClient.for_local
this pass — local provider fails closed. for_local remains unit-tested.
"""

from __future__ import annotations

from dataclasses import dataclass

from elyra.llm.constants import DEFAULT_CHAT_TEMPERATURE


@dataclass(frozen=True)
class LocalClientConfig:
    """OpenAI-compatible local/self-hosted chat endpoint (future use).

    Not launched; not wired from supervisor. Unit-test factory only.
    """

    base_url: str = "http://127.0.0.1:8080/v1"
    chat_path: str = "/chat/completions"  # join like XaiClientConfig
    model: str = "local"
    connect_timeout: float = 10.0
    read_timeout: float = 600.0
    temperature: float = DEFAULT_CHAT_TEMPERATURE
    top_p: float | None = None
    top_k: int | None = None  # omit on wire when None
    api_key: str | None = None  # optional Bearer — never log / never status

    @staticmethod
    def _join(base: str, path: str) -> str:
        return base.rstrip("/") + (path if path.startswith("/") else f"/{path}")

    @property
    def chat_url(self) -> str:
        # e.g. http://127.0.0.1:8080/v1/chat/completions
        return self._join(self.base_url, self.chat_path)

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
