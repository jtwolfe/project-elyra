"""Chat completion client for local OpenAI-compat endpoints and xAI Grok.

Scope: POST chat/completions with optional OpenAI-style tools.
In scope: HTTP client (for_local / for_xai factories), gated wrapper,
          usage gate, failing client, stub (incl. scripted tool_calls),
          parse message.tool_calls (arguments string → dict).
Local wire is OpenAI subset (model required; no reasoning / thinking_budget).
Out of scope: do-loop / multi-hop tool execution (loop package); supervisor wiring.

**Import rule:** this module may import ``elyra.llm.usage``; usage must NEVER
import this module (cycle-free).
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from elyra.llm.config import LocalClientConfig, XaiClientConfig
from elyra.llm.queue import ChatRequestGate
from elyra.llm.usage import (
    TokenUsage,
    UsageHardStopError,
    UsageMeter,
    parse_token_usage,
)


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
    usage: TokenUsage | None = None


class ChatClient(Protocol):
    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        reasoning_budget_tokens: int | None = None,
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
    usage = parse_token_usage(data.get("usage"))
    return ChatCompletionResult(
        content=str(content),
        reasoning_content=str(reasoning_content),
        raw_json=raw,
        tool_calls=tool_calls,
        finish_reason=_coerce_finish_reason(choice.get("finish_reason")),
        usage=usage,
    )


def _coerce_usage(value: Any) -> TokenUsage | None:
    """Accept TokenUsage, OpenAI-style usage dict, or None."""
    if value is None:
        return None
    if isinstance(value, TokenUsage):
        return value
    return parse_token_usage(value)


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
        wrap = {
            "choices": [{"message": item, "finish_reason": item.get("finish_reason")}],
            "usage": item.get("usage"),
        }
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
    usage = _coerce_usage(item.get("usage"))
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
        usage=usage,
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
        top_p: float | None = None,
        top_k: int | None = None,
        reasoning_budget_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        if self._callable is not None:
            return self._callable(
                messages,
                max_tokens=max_tokens,
                reasoning=reasoning,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                reasoning_budget_tokens=reasoning_budget_tokens,
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
    """OpenAI-compatible chat HTTP client for local endpoints and xAI.

    Prefer factories ``for_local`` / ``for_xai`` — avoid inconsistent
    config + kwargs combos. Positional ``HttpChatClient(LocalClientConfig)``
    remains supported for local regression (same as ``for_local``).
    """

    def __init__(
        self,
        config: LocalClientConfig | XaiClientConfig | None = None,
        *,
        profile: str | None = None,
        model: str | None = None,
        bearer_token: str | None = None,
    ) -> None:
        """Internal / BC constructor. Prefer ``for_local`` / ``for_xai``.

        ``profile`` in ``{'local','xai'}``. When omitted: ``XaiClientConfig``
        → xai; otherwise local (``LocalClientConfig`` or default).
        """
        if profile is None:
            if isinstance(config, XaiClientConfig):
                profile = "xai"
            else:
                profile = "local"
        if profile not in ("local", "xai"):
            raise ValueError(f"profile must be 'local' or 'xai', got {profile!r}")

        self._profile = profile
        self._lock = threading.Lock()

        if profile == "local":
            if config is None:
                self._local_config: LocalClientConfig | None = LocalClientConfig()
            elif isinstance(config, LocalClientConfig):
                self._local_config = config
            else:
                raise TypeError(
                    "local profile requires LocalClientConfig or None; "
                    f"got {type(config).__name__}"
                )
            self._xai_config: XaiClientConfig | None = None
            self._model: str | None = None
            self._bearer_token: str | None = None
        else:
            if config is None:
                self._xai_config = XaiClientConfig()
            elif isinstance(config, XaiClientConfig):
                self._xai_config = config
            else:
                raise TypeError(
                    "xai profile requires XaiClientConfig or None; "
                    f"got {type(config).__name__}"
                )
            self._local_config = None
            if not model or not isinstance(model, str):
                raise ValueError("xai profile requires a non-empty model string")
            if bearer_token is None or not isinstance(bearer_token, str):
                raise ValueError("xai profile requires a bearer_token string")
            self._model = model
            self._bearer_token = bearer_token

        # BC alias used by older tests/callers: ``client._config`` for local.
        self._config: LocalClientConfig | XaiClientConfig
        if profile == "local":
            assert self._local_config is not None
            self._config = self._local_config
        else:
            assert self._xai_config is not None
            self._config = self._xai_config

    @classmethod
    def for_local(cls, config: LocalClientConfig | None = None) -> HttpChatClient:
        """Build a local OpenAI-compat client (optional Bearer when api_key set)."""
        return cls(config if config is not None else LocalClientConfig(), profile="local")

    @classmethod
    def for_xai(
        cls,
        config: XaiClientConfig | None = None,
        *,
        model: str,
        bearer_token: str,
    ) -> HttpChatClient:
        """Build an xAI client (Bearer + model; omit top_k / reasoning wire keys)."""
        return cls(
            config if config is not None else XaiClientConfig(),
            profile="xai",
            model=model,
            bearer_token=bearer_token,
        )

    def set_model(self, model: str) -> None:
        """Thread-safe; next chat_completion uses the new model (xai only)."""
        if not model or not isinstance(model, str):
            raise ValueError("model must be a non-empty string")
        with self._lock:
            if self._profile != "xai":
                return
            self._model = model

    def set_bearer_token(self, token: str | None) -> None:
        """Thread-safe; never log token. Empty/None clears Authorization."""
        with self._lock:
            if self._profile != "xai":
                return
            self._bearer_token = token

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def chat_url(self) -> str:
        return self._config.chat_url

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        reasoning_budget_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        """POST OpenAI-compatible chat/completions.

        Local: body includes ``model`` (from config); optional ``top_p`` /
        ``top_k`` when resolved non-None; **never** ``reasoning`` or
        ``thinking_budget_tokens``. Optional ``Authorization: Bearer`` when
        ``config.api_key`` is set. Kwargs ``reasoning`` /
        ``reasoning_budget_tokens`` are accepted for Protocol BC but ignored
        on the wire.

        xai: Authorization Bearer; body includes ``model``; omit ``top_k``,
        ``thinking_budget_tokens``, and ``reasoning`` wire keys.

        Never emit chat-body key ``reasoning_budget_tokens``.
        Never put Authorization values into exception messages.
        """
        # Copy mutables under lock; release before HTTP I/O.
        with self._lock:
            profile = self._profile
            chat_url = self._config.chat_url
            request_timeout = self._config.request_timeout
            if profile == "local":
                assert self._local_config is not None
                cfg_local = self._local_config
                model: str | None = None
                bearer: str | None = None
            else:
                assert self._xai_config is not None
                cfg_xai = self._xai_config
                model = self._model
                bearer = self._bearer_token

        if profile == "local":
            payload = self._build_local_payload(
                cfg_local,
                messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                tools=tools,
                tool_choice=tool_choice,
            )
            headers = {"Content-Type": "application/json"}
            # Optional Bearer for self-hosted OpenAI-compat (never log key).
            if cfg_local.api_key:
                headers["Authorization"] = f"Bearer {cfg_local.api_key}"
        else:
            payload = self._build_xai_payload(
                cfg_xai,
                messages,
                model=model or "",
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                tools=tools,
                tool_choice=tool_choice,
            )
            headers = {"Content-Type": "application/json"}
            if bearer:
                headers["Authorization"] = f"Bearer {bearer}"

        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            chat_url,
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=request_timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            # Never include Authorization header values in the message.
            raise RuntimeError(f"chat HTTP {exc.code}: {detail[:500]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"chat connection failed: {exc.reason}") from exc

        data = json.loads(raw)
        if not isinstance(data, dict):
            raise RuntimeError("chat response is not a JSON object")
        return _result_from_response_data(data, raw)

    @staticmethod
    def _build_local_payload(
        config: LocalClientConfig,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        temperature: float | None,
        top_p: float | None,
        top_k: int | None,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        # OpenAI-compat subset: model required; no reasoning / thinking_budget.
        resolved_top_p = top_p if top_p is not None else config.top_p
        resolved_top_k = top_k if top_k is not None else config.top_k
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": (
                temperature if temperature is not None else config.temperature
            ),
            "stream": False,
        }
        if resolved_top_p is not None:
            payload["top_p"] = resolved_top_p
        if resolved_top_k is not None:
            payload["top_k"] = resolved_top_k
        # Intentionally never set reasoning / thinking_budget_tokens.
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        return payload

    @staticmethod
    def _build_xai_payload(
        config: XaiClientConfig,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_tokens: int,
        temperature: float | None,
        top_p: float | None,
        tools: list[dict[str, Any]] | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        # xai: model required; omit top_k / thinking_budget_tokens / reasoning.
        resolved_top_p = top_p if top_p is not None else config.top_p
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": (
                temperature if temperature is not None else config.temperature
            ),
            "stream": False,
        }
        if resolved_top_p is not None:
            payload["top_p"] = resolved_top_p
        # Intentionally never set top_k / reasoning / thinking_budget_tokens.
        if tools is not None:
            payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        return payload


class GatedChatClient:
    def __init__(self, inner: ChatClient, gate: ChatRequestGate) -> None:
        self._inner = inner
        self._gate = gate

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        reasoning_budget_tokens: int | None = None,
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
                top_p=top_p,
                top_k=top_k,
                reasoning_budget_tokens=reasoning_budget_tokens,
                tools=tools,
                tool_choice=tool_choice,
            ),
        )


class UsageGatedChatClient:
    """ChatClient wrapper that enforces UsageMeter hard stops.

    Lives in client.py (not usage.py) so usage never imports client.

    - refuse when ``!meter.can_call`` → raise ``UsageHardStopError``
      (can_call is True when override_active even if over budget);
    - on success → ``meter.record(result.usage)`` always (override does not
      skip record; failures do not record — durable state stays consistent);
    - when ``meter`` is None, pure pass-through.
    """

    def __init__(self, inner: ChatClient, meter: UsageMeter | None) -> None:
        self._inner = inner
        self._meter = meter

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        reasoning_budget_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        meter = self._meter
        if meter is not None and not meter.can_call():
            snap = meter.snapshot()
            level = snap.hard_stop or "week"
            reason = snap.hard_stop_reason or "usage hard stop"
            raise UsageHardStopError(reason, level=level)

        result = self._inner.chat_completion(
            messages,
            max_tokens=max_tokens,
            reasoning=reasoning,
            temperature=temperature,
            top_p=top_p,
            top_k=top_k,
            reasoning_budget_tokens=reasoning_budget_tokens,
            tools=tools,
            tool_choice=tool_choice,
        )
        # Record only after success so exception paths leave meter unchanged.
        if meter is not None:
            meter.record(result.usage)
        return result


class FailingChatClient:
    """Required when provider=xai and credentials cannot resolve.

    ``chat_completion`` always raises ``RuntimeError`` with a stable,
    non-secret message (includes ``credential_detail``). Never echoes
    user content. Live repair replaces ``worker.client`` via rebuild.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        reasoning_budget_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        raise RuntimeError(f"llm unavailable: {self.detail}")
