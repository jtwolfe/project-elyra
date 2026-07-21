"""Chat completion client for llama-server.

Scope: POST /v1/chat/completions.
In scope: HTTP client, gated wrapper, stub for tests.
Out of scope: tool-call parsing loop (loop package).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

from elyra.llm.config import LlamaServerConfig
from elyra.llm.queue import LlamaServerGate


@dataclass(frozen=True)
class ChatCompletionResult:
    content: str
    reasoning_content: str
    raw_json: str


class ChatClient(Protocol):
    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
    ) -> ChatCompletionResult: ...


class StubChatClient:
    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
    ) -> ChatCompletionResult:
        last = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last = str(m.get("content", ""))
                break
        return ChatCompletionResult(
            content=f"(stub) Received: {last[:200]}",
            reasoning_content="stub reasoning" if reasoning else "",
            raw_json="{}",
        )


class HttpChatClient:
    def __init__(self, config: LlamaServerConfig | None = None) -> None:
        self._config = config or LlamaServerConfig()

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
    ) -> ChatCompletionResult:
        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": (
                temperature if temperature is not None else self._config.temperature
            ),
            "stream": False,
        }
        if self._config.use_reasoning:
            payload["reasoning"] = bool(reasoning)

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._config.chat_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._config.request_timeout
            ) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"chat HTTP {exc.code}: {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"chat connection failed: {exc.reason}") from exc

        data = json.loads(raw)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        reasoning_content = (
            message.get("reasoning_content")
            or message.get("reasoning")
            or ""
        )
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )
        return ChatCompletionResult(
            content=str(content),
            reasoning_content=str(reasoning_content),
            raw_json=raw,
        )


class GatedChatClient:
    def __init__(self, inner: ChatClient, gate: LlamaServerGate) -> None:
        self._inner = inner
        self._gate = gate

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
    ) -> ChatCompletionResult:
        return self._gate.submit(
            "chat",
            lambda: self._inner.chat_completion(
                messages,
                max_tokens=max_tokens,
                reasoning=reasoning,
                temperature=temperature,
            ),
        )
