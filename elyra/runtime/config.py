"""Runtime process configuration."""

from __future__ import annotations

from dataclasses import dataclass, field

from elyra.llm.config import LlamaServerConfig


@dataclass
class RuntimeConfig:
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    start_llama_server: bool = True
    llama: LlamaServerConfig = field(default_factory=LlamaServerConfig)
    llama_health_timeout: float = 180.0
    # KV ceiling; lower if VRAM crashes (see docs/inference.md).
    context_tokens: int | None = None
