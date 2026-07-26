"""Hermetic tests for xAI HttpChatClient factories, URL join, usage gate.

Covers: exact chat/models URL join (no /v1/v1), payload shape, usage parse,
UsageGatedChatClient, FailingChatClient, import-cycle hygiene.
"""

from __future__ import annotations

import ast
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elyra.llm.client import (
    ChatCompletionResult,
    FailingChatClient,
    HttpChatClient,
    StubChatClient,
    UsageGatedChatClient,
)
from elyra.llm.config import LocalClientConfig, XaiClientConfig
from elyra.llm.models import (
    CURATED_XAI_MODELS,
    DEFAULT_XAI_MODEL,
    label_for_model,
    list_remote_models,
    models_for_picker,
)
from elyra.llm.usage import (
    TokenUsage,
    UsageHardStopError,
    UsageMeter,
    parse_token_usage,
)
from elyra.settings import UsageSettings


# ---------------------------------------------------------------------------
# XaiClientConfig URL join (normative: base /v1 + path /chat/completions)
# ---------------------------------------------------------------------------


def test_xai_config_default_chat_url_exact():
    cfg = XaiClientConfig()
    assert cfg.base_url == "https://api.x.ai/v1"
    assert cfg.chat_path == "/chat/completions"
    assert cfg.chat_url == "https://api.x.ai/v1/chat/completions"


def test_xai_config_default_models_url_exact():
    cfg = XaiClientConfig()
    assert cfg.models_path == "/models"
    assert cfg.models_url == "https://api.x.ai/v1/models"


def test_xai_config_join_strips_trailing_slash_no_double_v1():
    cfg = XaiClientConfig(base_url="https://api.x.ai/v1/")
    assert cfg.chat_url == "https://api.x.ai/v1/chat/completions"
    assert cfg.models_url == "https://api.x.ai/v1/models"
    assert "/v1/v1/" not in cfg.chat_url
    assert "/v1/v1/" not in cfg.models_url


def test_xai_config_join_path_without_leading_slash():
    cfg = XaiClientConfig(
        base_url="https://api.x.ai/v1",
        chat_path="chat/completions",
        models_path="models",
    )
    assert cfg.chat_url == "https://api.x.ai/v1/chat/completions"
    assert cfg.models_url == "https://api.x.ai/v1/models"


def test_local_client_config_interim_url():
    """PR1 interim: host/port still drive chat_url (base_url reshape is PR2)."""
    cfg = LocalClientConfig(host="127.0.0.1", port=8080)
    assert cfg.chat_url == "http://127.0.0.1:8080/v1/chat/completions"
    assert cfg.health_url == "http://127.0.0.1:8080/health"
    assert cfg.model == "local"


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def test_for_local_factory_profile_and_url():
    cfg = LocalClientConfig(host="127.0.0.1", port=9999)
    client = HttpChatClient.for_local(cfg)
    assert client.profile == "local"
    assert client.chat_url == "http://127.0.0.1:9999/v1/chat/completions"


def test_for_local_default_config():
    client = HttpChatClient.for_local()
    assert client.profile == "local"
    assert client.chat_url == LocalClientConfig().chat_url


def test_for_xai_factory_profile_and_exact_url():
    client = HttpChatClient.for_xai(model="grok-4.5", bearer_token="tok-secret")
    assert client.profile == "xai"
    assert client.chat_url == "https://api.x.ai/v1/chat/completions"


def test_for_xai_requires_model_and_bearer():
    with pytest.raises(ValueError, match="model"):
        HttpChatClient.for_xai(model="", bearer_token="tok")
    with pytest.raises(ValueError, match="bearer"):
        HttpChatClient(XaiClientConfig(), profile="xai", model="grok-4.5", bearer_token=None)  # type: ignore[arg-type]


def test_bc_positional_local_config_is_local():
    cfg = LocalClientConfig(host="10.0.0.1", port=1234)
    client = HttpChatClient(cfg)
    assert client.profile == "local"
    assert client.chat_url == "http://10.0.0.1:1234/v1/chat/completions"


# ---------------------------------------------------------------------------
# Payload shape + headers (mocked HTTP)
# ---------------------------------------------------------------------------


def _ok_chat_body(**overrides: Any) -> bytes:
    body: dict[str, Any] = {
        "choices": [
            {
                "message": {"content": "hi", "role": "assistant"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }
    body.update(overrides)
    return json.dumps(body).encode("utf-8")


class _FakeHTTPResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_xai_payload_includes_model_bearer_omits_gemma_fields():
    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8") if req.data else b"{}")
        return _FakeHTTPResponse(_ok_chat_body())

    client = HttpChatClient.for_xai(
        model="grok-4.5",
        bearer_token="secret-token-xyz",
    )
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client.chat_completion(
            [{"role": "user", "content": "ping"}],
            max_tokens=32,
            reasoning=True,
            top_k=64,
            reasoning_budget_tokens=2048,
        )

    assert captured["url"] == "https://api.x.ai/v1/chat/completions"
    assert captured["headers"].get("authorization") == "Bearer secret-token-xyz"
    assert captured["headers"].get("content-type") == "application/json"
    body = captured["body"]
    assert body["model"] == "grok-4.5"
    assert body["messages"][0]["content"] == "ping"
    assert body["max_tokens"] == 32
    assert body["stream"] is False
    # Gemma-only / local-only wire fields must be absent.
    assert "top_k" not in body
    assert "thinking_budget_tokens" not in body
    assert "reasoning" not in body
    assert "reasoning_budget_tokens" not in body
    # Usage parsed onto result.
    assert result.usage is not None
    assert result.usage.total_tokens == 15
    assert result.usage.billable_tokens == 15
    assert result.content == "hi"


def test_xai_set_model_and_bearer_affect_next_request():
    captured: list[dict[str, Any]] = []

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        captured.append(
            {
                "auth": dict(req.header_items()).get("Authorization")
                or dict(req.header_items()).get("authorization"),
                "body": json.loads(req.data.decode("utf-8") if req.data else b"{}"),
            }
        )
        # header_items may normalize; also check via get_header
        auth = req.get_header("Authorization") or req.get_header("authorization")
        captured[-1]["auth"] = auth
        return _FakeHTTPResponse(_ok_chat_body())

    client = HttpChatClient.for_xai(model="grok-4.5", bearer_token="tok-a")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.chat_completion([{"role": "user", "content": "1"}])
        client.set_model("grok-4.3")
        client.set_bearer_token("tok-b")
        client.chat_completion([{"role": "user", "content": "2"}])

    assert captured[0]["body"]["model"] == "grok-4.5"
    assert captured[0]["auth"] == "Bearer tok-a"
    assert captured[1]["body"]["model"] == "grok-4.3"
    assert captured[1]["auth"] == "Bearer tok-b"


def test_local_payload_openai_compat_model_no_reasoning():
    """Local wire: model required; omit reasoning/thinking_budget; no GEMMA defaults."""
    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = json.loads(req.data.decode("utf-8") if req.data else b"{}")
        return _FakeHTTPResponse(_ok_chat_body())

    cfg = LocalClientConfig(host="127.0.0.1", port=8080, use_reasoning=True)
    client = HttpChatClient.for_local(cfg)
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.chat_completion(
            [{"role": "user", "content": "hi"}],
            max_tokens=16,
            reasoning=True,
            reasoning_budget_tokens=2048,
            top_k=64,
        )

    assert captured["url"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert "authorization" not in captured["headers"]
    body = captured["body"]
    assert body["model"] == "local"
    assert body["max_tokens"] == 16
    assert body["stream"] is False
    # Optional top_k only when resolved non-None (kwarg provided above).
    assert body["top_k"] == 64
    # Product defaults no longer ship GEMMA top_p.
    assert "top_p" not in body
    assert "reasoning" not in body
    assert "thinking_budget_tokens" not in body
    assert "reasoning_budget_tokens" not in body


def test_local_payload_omits_top_k_when_none():
    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        captured["body"] = json.loads(req.data.decode("utf-8") if req.data else b"{}")
        return _FakeHTTPResponse(_ok_chat_body())

    cfg = LocalClientConfig(host="127.0.0.1", port=8080, top_p=None, top_k=None)
    client = HttpChatClient.for_local(cfg)
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.chat_completion([{"role": "user", "content": "hi"}], max_tokens=8)

    body = captured["body"]
    assert body["model"] == "local"
    assert "top_p" not in body
    assert "top_k" not in body
    assert "reasoning" not in body
    assert "thinking_budget_tokens" not in body


def test_for_local_optional_bearer_when_api_key_set():
    """Unit-test path: LocalClientConfig.api_key → Authorization Bearer."""
    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        # Also via get_header (urllib normalizes)
        auth = req.get_header("Authorization") or req.get_header("authorization")
        captured["auth"] = auth
        captured["body"] = json.loads(req.data.decode("utf-8") if req.data else b"{}")
        return _FakeHTTPResponse(_ok_chat_body())

    cfg = LocalClientConfig(
        host="127.0.0.1",
        port=8080,
        api_key="local-secret-key",
        model="ollama-model",
    )
    client = HttpChatClient.for_local(cfg)
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.chat_completion([{"role": "user", "content": "hi"}], max_tokens=8)

    assert captured["auth"] == "Bearer local-secret-key"
    assert captured["body"]["model"] == "ollama-model"
    # Never leak into body
    assert "api_key" not in captured["body"]
    assert "local-secret-key" not in json.dumps(captured["body"])


def test_for_local_omits_authorization_when_api_key_none():
    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeHTTPResponse(_ok_chat_body())

    client = HttpChatClient.for_local(LocalClientConfig(host="127.0.0.1", port=8080))
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.chat_completion([{"role": "user", "content": "hi"}], max_tokens=8)

    assert "authorization" not in captured["headers"]


def test_for_local_omits_authorization_when_api_key_empty():
    """Design Bearer rule: empty api_key omits Authorization (same as None)."""
    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return _FakeHTTPResponse(_ok_chat_body())

    cfg = LocalClientConfig(host="127.0.0.1", port=8080, api_key="")
    client = HttpChatClient.for_local(cfg)
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        client.chat_completion([{"role": "user", "content": "hi"}], max_tokens=8)

    assert "authorization" not in captured["headers"]


def test_http_error_message_omits_bearer_token():
    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=__import__("io").BytesIO(b'{"error":"bad token"}'),
        )

    client = HttpChatClient.for_xai(model="grok-4.5", bearer_token="super-secret-token")
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(RuntimeError) as ei:
            client.chat_completion([{"role": "user", "content": "x"}])
    msg = str(ei.value)
    assert "401" in msg
    assert "super-secret-token" not in msg
    assert "Bearer" not in msg or "super-secret" not in msg


def test_local_http_error_message_omits_api_key():
    """Symmetric non-leak: local Bearer key never appears in HTTP error text."""
    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        raise urllib.error.HTTPError(
            url=req.full_url,
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=__import__("io").BytesIO(b'{"error":"bad token"}'),
        )

    cfg = LocalClientConfig(
        host="127.0.0.1",
        port=8080,
        api_key="local-super-secret-key",
    )
    client = HttpChatClient.for_local(cfg)
    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(RuntimeError) as ei:
            client.chat_completion([{"role": "user", "content": "x"}])
    msg = str(ei.value)
    assert "401" in msg
    assert "local-super-secret-key" not in msg


# ---------------------------------------------------------------------------
# Usage parse on ChatCompletionResult
# ---------------------------------------------------------------------------


def test_result_from_response_parses_usage():
    client = HttpChatClient.for_xai(model="grok-4.5", bearer_token="t")

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        return _FakeHTTPResponse(
            _ok_chat_body(
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                    "completion_tokens_details": {"reasoning_tokens": 12},
                }
            )
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client.chat_completion([{"role": "user", "content": "x"}])
    assert result.usage is not None
    assert result.usage.prompt_tokens == 100
    assert result.usage.completion_tokens == 50
    assert result.usage.total_tokens == 150
    assert result.usage.reasoning_tokens == 12
    assert result.usage.billable_tokens == 150


def test_result_missing_usage_is_none():
    client = HttpChatClient.for_local(LocalClientConfig(use_reasoning=False))

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        return _FakeHTTPResponse(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": "ok"},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ).encode("utf-8")
        )

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        result = client.chat_completion([{"role": "user", "content": "x"}])
    assert result.usage is None


def test_parse_token_usage_helper_still_works():
    u = parse_token_usage({"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})
    assert u is not None
    assert u.billable_tokens == 3


# ---------------------------------------------------------------------------
# UsageGatedChatClient
# ---------------------------------------------------------------------------


def _settings(**kwargs: object) -> UsageSettings:
    base: dict[str, object] = dict(
        enabled=True,
        weekly_allowed_tokens=100,
        weekly_allowed_fraction=0.50,
        hour_block_minutes=60,
        day_allowed_tokens=None,
        hour_allowed_tokens=None,
    )
    base.update(kwargs)
    return UsageSettings(**base)  # type: ignore[arg-type]


def test_usage_gate_records_after_success(tmp_path: Path):
    meter = UsageMeter.load(tmp_path, _settings(weekly_allowed_tokens=10_000))
    inner = StubChatClient.scripted(
        [
            {
                "content": "ok",
                "usage": TokenUsage(
                    prompt_tokens=10, completion_tokens=5, total_tokens=15
                ),
            }
        ]
    )
    gated = UsageGatedChatClient(inner, meter)
    result = gated.chat_completion([{"role": "user", "content": "hi"}])
    assert result.content == "ok"
    snap = meter.snapshot()
    assert snap.week_used_tokens == 15


def test_usage_gate_raises_hard_stop_when_over_budget(tmp_path: Path):
    meter = UsageMeter.load(tmp_path, _settings(weekly_allowed_tokens=10))
    meter.record(TokenUsage(total_tokens=10))
    assert meter.can_call() is False

    calls = {"n": 0}

    def _boom(*_a: Any, **_k: Any) -> ChatCompletionResult:
        calls["n"] += 1
        return ChatCompletionResult(content="x", reasoning_content="", raw_json="{}")

    gated = UsageGatedChatClient(StubChatClient(responses=_boom), meter)
    with pytest.raises(UsageHardStopError) as ei:
        gated.chat_completion([{"role": "user", "content": "nope"}])
    assert ei.value.level in ("week", "day", "hour")
    assert calls["n"] == 0  # inner never invoked
    # Exception path must not record further.
    assert meter.snapshot().week_used_tokens == 10


def test_usage_gate_records_under_override_even_when_over(tmp_path: Path):
    meter = UsageMeter.load(tmp_path, _settings(weekly_allowed_tokens=10))
    meter.record(TokenUsage(total_tokens=10))
    meter.set_hard_stop_override(True)
    assert meter.can_call() is True

    inner = StubChatClient.scripted(
        [
            {
                "content": "still works",
                "usage": TokenUsage(total_tokens=7),
            }
        ]
    )
    gated = UsageGatedChatClient(inner, meter)
    result = gated.chat_completion([{"role": "user", "content": "x"}])
    assert result.content == "still works"
    assert meter.snapshot().week_used_tokens == 17
    assert meter.snapshot().override_active is True


def test_usage_gate_none_meter_is_passthrough():
    inner = StubChatClient()
    gated = UsageGatedChatClient(inner, None)
    result = gated.chat_completion([{"role": "user", "content": "hello-world"}])
    assert "hello-world" in result.content


def test_usage_gate_does_not_record_on_inner_failure(tmp_path: Path):
    meter = UsageMeter.load(tmp_path, _settings(weekly_allowed_tokens=10_000))

    class BoomClient:
        def chat_completion(self, messages, **kwargs):  # noqa: ANN001, ANN003
            raise RuntimeError("upstream failed")

    gated = UsageGatedChatClient(BoomClient(), meter)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="upstream failed"):
        gated.chat_completion([{"role": "user", "content": "x"}])
    assert meter.snapshot().week_used_tokens == 0


# ---------------------------------------------------------------------------
# FailingChatClient
# ---------------------------------------------------------------------------


def test_failing_chat_client_raises_stable_message():
    client = FailingChatClient("missing_auth_json")
    with pytest.raises(RuntimeError, match="llm unavailable: missing_auth_json"):
        client.chat_completion([{"role": "user", "content": "secret user text"}])


def test_failing_chat_client_does_not_echo_user_content():
    client = FailingChatClient("token_expired")
    with pytest.raises(RuntimeError) as ei:
        client.chat_completion(
            [{"role": "user", "content": "please never echo THIS_SECRET"}]
        )
    assert "THIS_SECRET" not in str(ei.value)
    assert "token_expired" in str(ei.value)


# ---------------------------------------------------------------------------
# models helpers
# ---------------------------------------------------------------------------


def test_label_for_model_and_defaults():
    assert DEFAULT_XAI_MODEL == "grok-4.5"
    assert label_for_model("grok-4.5") == "Grok 4.5 Fast"
    assert label_for_model("unknown-id") == "unknown-id"


def test_models_for_picker_fallback_and_current():
    assert models_for_picker(None) == list(CURATED_XAI_MODELS)
    assert models_for_picker([]) == list(CURATED_XAI_MODELS)
    out = models_for_picker(
        ["grok-4.3", "grok-image-gen", "custom-model"],
        current="my-current",
    )
    # curated order first among listed chat models
    assert out[0] == "my-current"
    assert "grok-4.3" in out
    assert "custom-model" in out


def test_list_remote_models_url_and_filter():
    captured: dict[str, Any] = {}

    def fake_urlopen(req: urllib.request.Request, timeout: float = 0):  # noqa: ARG001
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        body = {
            "data": [
                {"id": "grok-4.5"},
                {"id": "grok-imagine-image"},
                {"id": "grok-tts-voice"},
                {"id": "grok-4.3"},
                {"id": 123},
            ]
        }
        return _FakeHTTPResponse(json.dumps(body).encode("utf-8"))

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ids = list_remote_models("https://api.x.ai/v1", "tok")
    assert captured["url"] == "https://api.x.ai/v1/models"
    assert captured["auth"] == "Bearer tok"
    assert ids == ["grok-4.5", "grok-4.3"]


# ---------------------------------------------------------------------------
# Import cycle hygiene
# ---------------------------------------------------------------------------


def test_usage_never_imports_client_ast():
    """elyra.llm.usage must never import elyra.llm.client (cycle-free)."""
    usage_path = Path(__file__).resolve().parents[1] / "elyra" / "llm" / "usage.py"
    source = usage_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "elyra.llm.client"
                assert "client" not in alias.name.split(".")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert mod != "elyra.llm.client"
            assert not mod.endswith(".client")


def test_client_and_usage_import_smoke():
    """Import both modules; client may depend on usage, never the reverse."""
    import importlib

    usage = importlib.import_module("elyra.llm.usage")
    client = importlib.import_module("elyra.llm.client")
    assert hasattr(client, "UsageGatedChatClient")
    assert hasattr(client, "FailingChatClient")
    assert hasattr(client, "HttpChatClient")
    assert hasattr(usage, "UsageMeter")
    # usage module object graph must not hold client module symbols
    for name, val in vars(usage).items():
        if name.startswith("_"):
            continue
        mod_of = getattr(val, "__module__", "") or ""
        assert not mod_of.startswith("elyra.llm.client"), name


def test_client_exports_usage_types_on_result():
    r = ChatCompletionResult(
        content="x",
        reasoning_content="",
        raw_json="{}",
        usage=TokenUsage(total_tokens=1),
    )
    assert r.usage is not None
    assert r.usage.billable_tokens == 1
