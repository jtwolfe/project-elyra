"""Chat completion client for llama-server.

Scope: POST /v1/chat/completions with optional OpenAI-style tools.
In scope: HTTP client, gated wrapper, stub (incl. scripted tool_calls),
          parse message.tool_calls (arguments string → dict).
Out of scope: do-loop / multi-hop tool execution (loop package).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from elyra.llm.config import LlamaServerConfig
from elyra.llm.queue import LlamaServerGate


@dataclass(frozen=True)
class ToolCall:
    """One function tool call from a chat completion.

    Scope: hold id/name and parsed arguments for host dispatch.
    arguments is {} when JSON parse fails; arguments_parse_ok is False then.
    """

    id: str
    name: str
    arguments: dict[str, Any]
    arguments_raw: str = ""
    arguments_parse_ok: bool = True


@dataclass(frozen=True)
class ChatCompletionResult:
    content: str
    reasoning_content: str
    raw_json: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None


class ChatClient(Protocol):
    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ChatCompletionResult: ...


def parse_tool_calls(raw_tool_calls: Any) -> list[ToolCall]:
    """Parse OpenAI-style message.tool_calls into ToolCall list.

    Scope: id, function.name, function.arguments (string or object).
    Also accepts flat scripted shape: {id, name, arguments} without function.
    Empty/missing name → skipped (not a dispatchable tool call).
    Malformed arguments → arguments={}, arguments_parse_ok=False; never raises.
    """
    if not raw_tool_calls or not isinstance(raw_tool_calls, list):
        return []
    out: list[ToolCall] = []
    for item in raw_tool_calls:
        if not isinstance(item, dict):
            continue
        call_id, name, args_raw_val = _extract_tool_call_fields(item)
        if not name:
            # Structural garbage: no callable name → drop (fail closed for dispatch).
            continue
        parsed, raw_str, ok = _parse_arguments(args_raw_val)
        out.append(
            ToolCall(
                id=call_id,
                name=name,
                arguments=parsed,
                arguments_raw=raw_str,
                arguments_parse_ok=ok,
            )
        )
    return out


def _extract_tool_call_fields(item: dict[str, Any]) -> tuple[str, str, Any]:
    """Return (id, name, arguments_value) from OpenAI wire or flat mini shape."""
    call_id = str(item.get("id") or "")
    fn = item.get("function")
    if isinstance(fn, dict):
        name = str(fn.get("name") or "")
        args_raw_val = fn.get("arguments", "")
        return call_id, name, args_raw_val
    # Flat result-shaped: {"id", "name", "arguments"} (scripted stub DX).
    if "name" in item and fn is None:
        name = str(item.get("name") or "")
        args_raw_val = item.get("arguments", "")
        return call_id, name, args_raw_val
    # function was null/non-dict and no flat name — empty name (skipped by caller).
    return call_id, "", ""


def _parse_arguments(value: Any) -> tuple[dict[str, Any], str, bool]:
    """Return (parsed_dict, raw_string, parse_ok). Never raises."""
    if value is None:
        return {}, "", True
    if isinstance(value, dict):
        try:
            raw = json.dumps(value)
        except (TypeError, ValueError):
            # Non-JSON-serializable dict (e.g. object() values) — fail closed.
            try:
                raw = json.dumps(value, default=str)
            except (TypeError, ValueError):
                raw = str(value)
            return {}, raw, False
        return dict(value), raw, True
    if isinstance(value, str):
        raw = value
        if raw.strip() == "":
            return {}, raw, True
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError:
            return {}, raw, False
        if isinstance(loaded, dict):
            return loaded, raw, True
        # Non-object JSON (array/number/string) is not a valid args object.
        return {}, raw, False
    # Unexpected type — stringify and fail closed.
    try:
        raw = json.dumps(value)
    except (TypeError, ValueError):
        raw = str(value)
    return {}, raw, False


def _coerce_finish_reason(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _result_from_response_data(data: dict[str, Any], raw: str) -> ChatCompletionResult:
    """Map a chat completions JSON body to ChatCompletionResult."""
    choice = (data.get("choices") or [{}])[0]
    if not isinstance(choice, dict):
        choice = {}
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        message = {}
    content = message.get("content")
    if content is None:
        content = ""
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
    tool_calls = parse_tool_calls(message.get("tool_calls"))
    return ChatCompletionResult(
        content=str(content),
        reasoning_content=str(reasoning_content),
        raw_json=raw,
        tool_calls=tool_calls,
        finish_reason=_coerce_finish_reason(choice.get("finish_reason")),
    )


def _result_from_scripted(item: ChatCompletionResult | dict[str, Any]) -> ChatCompletionResult:
    """Normalize a scripted stub entry to ChatCompletionResult."""
    if isinstance(item, ChatCompletionResult):
        return item
    if not isinstance(item, dict):
        raise TypeError(f"scripted response must be ChatCompletionResult or dict, got {type(item)}")
    # Allow either full result fields or a mini OpenAI choice/message shape.
    if "choices" in item or "message" in item:
        raw = json.dumps(item)
        if "choices" in item:
            return _result_from_response_data(item, raw)
        # bare message dict
        wrap = {"choices": [{"message": item, "finish_reason": item.get("finish_reason")}]}
        return _result_from_response_data(wrap, raw)
    tool_calls_raw = item.get("tool_calls", [])
    if tool_calls_raw and isinstance(tool_calls_raw, list) and tool_calls_raw:
        first = tool_calls_raw[0]
        if isinstance(first, ToolCall):
            # Drop ToolCall objects that lack a name (same contract as wire parse).
            tool_calls = [tc for tc in tool_calls_raw if isinstance(tc, ToolCall) and tc.name]
        else:
            tool_calls = parse_tool_calls(tool_calls_raw)
    else:
        tool_calls = []
    finish_reason = _coerce_finish_reason(item.get("finish_reason"))
    if "raw_json" in item and item["raw_json"] is not None:
        raw_json = str(item["raw_json"])
    else:
        # Avoid json.dumps when values include ToolCall objects.
        raw_json = json.dumps(
            {
                "content": item.get("content"),
                "reasoning_content": item.get("reasoning_content"),
                "finish_reason": finish_reason,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "arguments_raw": tc.arguments_raw,
                        "arguments_parse_ok": tc.arguments_parse_ok,
                    }
                    for tc in tool_calls
                ],
            }
        )
    return ChatCompletionResult(
        content=str(item.get("content") or ""),
        reasoning_content=str(item.get("reasoning_content") or ""),
        raw_json=raw_json,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
    )


class StubChatClient:
    """Deterministic chat client for tests.

    Default: echo last user content, empty tool_calls.
    Scripted: sequential responses via constructor or StubChatClient.scripted(...).
    """

    def __init__(
        self,
        *,
        responses: Sequence[ChatCompletionResult | dict[str, Any]]
        | Callable[..., ChatCompletionResult]
        | None = None,
    ) -> None:
        self._callable: Callable[..., ChatCompletionResult] | None = None
        self._script: list[ChatCompletionResult | dict[str, Any]] = []
        self._index = 0
        if responses is None:
            return
        if isinstance(responses, (list, tuple)):
            self._script = list(responses)
            return
        if callable(responses):
            self._callable = responses  # type: ignore[assignment]
            return
        raise TypeError(
            "responses must be a list/tuple of scripted results, a callable, or None; "
            f"got {type(responses).__name__}"
        )

    @classmethod
    def scripted(
        cls,
        responses: Sequence[ChatCompletionResult | dict[str, Any]],
    ) -> StubChatClient:
        """Build a stub that returns each response in order; holds last after exhaustion."""
        return cls(responses=responses)

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        if self._callable is not None:
            return self._callable(
                messages,
                max_tokens=max_tokens,
                reasoning=reasoning,
                temperature=temperature,
                tools=tools,
                tool_choice=tool_choice,
            )
        if self._script:
            idx = min(self._index, len(self._script) - 1)
            self._index += 1
            return _result_from_scripted(self._script[idx])
        last = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last = str(m.get("content", ""))
                break
        return ChatCompletionResult(
            content=f"(stub) Received: {last[:200]}",
            reasoning_content="stub reasoning" if reasoning else "",
            raw_json="{}",
            tool_calls=[],
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
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
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
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

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
        if not isinstance(data, dict):
            raise RuntimeError("chat response is not a JSON object")
        return _result_from_response_data(data, raw)


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
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        return self._gate.submit(
            "chat",
            lambda: self._inner.chat_completion(
                messages,
                max_tokens=max_tokens,
                reasoning=reasoning,
                temperature=temperature,
                tools=tools,
                tool_choice=tool_choice,
            ),
        )
