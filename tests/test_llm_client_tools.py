"""Behaviour tests for tools parameter and tool_calls parsing on ChatClient.

Unit tests use stubs / fake HTTP. @pytest.mark.llm hits real llama-server when
model weights and binary are present under model/.
"""

from __future__ import annotations

import http.server
import json
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.llm.client import (
    ChatCompletionResult,
    GatedChatClient,
    HttpChatClient,
    StubChatClient,
    ToolCall,
    parse_tool_calls,
)
from elyra.llm.config import LlamaServerConfig
from elyra.llm.queue import LlamaServerGate
from elyra.llm.server import build_server_command, validate_model_paths

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "llm_tool_responses.json"


def _fixtures() -> dict[str, Any]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Pure parse helpers
# ---------------------------------------------------------------------------


def test_parse_arguments_string_to_dict():
    raw = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "list_dir", "arguments": '{"path": "."}'},
        }
    ]
    calls = parse_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0].id == "call_1"
    assert calls[0].name == "list_dir"
    assert calls[0].arguments == {"path": "."}
    assert calls[0].arguments_raw == '{"path": "."}'
    assert calls[0].arguments_parse_ok is True


def test_malformed_arguments_string_sets_parse_ok_false_without_crash():
    raw = [
        {
            "id": "call_bad",
            "type": "function",
            "function": {"name": "list_dir", "arguments": "{not valid json"},
        }
    ]
    calls = parse_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0].name == "list_dir"
    assert calls[0].arguments == {}
    assert calls[0].arguments_parse_ok is False
    assert "{not valid" in calls[0].arguments_raw


def test_arguments_already_object_is_accepted():
    raw = [
        {
            "id": "call_obj",
            "type": "function",
            "function": {"name": "speak", "arguments": {"text": "hi"}},
        }
    ]
    calls = parse_tool_calls(raw)
    assert calls[0].arguments == {"text": "hi"}
    assert calls[0].arguments_parse_ok is True


def test_parse_tool_calls_empty_and_missing():
    assert parse_tool_calls(None) == []
    assert parse_tool_calls([]) == []
    assert parse_tool_calls("not-a-list") == []


def test_non_serializable_dict_arguments_do_not_raise():
    raw = [
        {
            "id": "call_obj",
            "type": "function",
            "function": {"name": "echo", "arguments": {"a": object()}},
        }
    ]
    calls = parse_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0].name == "echo"
    assert calls[0].arguments == {}
    assert calls[0].arguments_parse_ok is False
    assert calls[0].arguments_raw  # best-effort raw for diagnostics


def test_empty_or_null_function_name_is_skipped():
    raw = [
        {"id": "c1", "type": "function", "function": None},
        {"id": "c2", "type": "function", "function": {}},
        {"id": "c3", "type": "function", "function": {"name": "", "arguments": "{}"}},
        {
            "id": "c4",
            "type": "function",
            "function": {"name": "list_dir", "arguments": '{"path": "."}'},
        },
    ]
    calls = parse_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0].id == "c4"
    assert calls[0].name == "list_dir"


def test_flat_scripted_tool_call_shape_is_accepted():
    """Mini result dicts may use flat {id, name, arguments} without function envelope."""
    raw = [
        {"id": "1", "name": "echo", "arguments": {"text": "x"}},
        {"id": "2", "name": "speak", "arguments": '{"text": "hi"}'},
    ]
    calls = parse_tool_calls(raw)
    assert len(calls) == 2
    assert calls[0].name == "echo"
    assert calls[0].arguments == {"text": "x"}
    assert calls[0].arguments_parse_ok is True
    assert calls[1].name == "speak"
    assert calls[1].arguments == {"text": "hi"}
    assert calls[1].arguments_parse_ok is True


# ---------------------------------------------------------------------------
# Stub scripted sequences
# ---------------------------------------------------------------------------


def test_stub_default_returns_empty_tool_calls():
    client = StubChatClient()
    result = client.chat_completion(
        [{"role": "user", "content": "hello"}],
        tools=_fixtures()["tools_list_dir"],
    )
    assert result.tool_calls == []
    assert "hello" in result.content


def test_stub_scripted_tool_calls_sequence():
    fx = _fixtures()
    first = fx["openai_response_list_dir"]
    second = {
        "content": None,
        "tool_calls": [
            {
                "id": "call_2",
                "type": "function",
                "function": {
                    "name": "speak",
                    "arguments": '{"text": "hi"}',
                },
            }
        ],
        "finish_reason": "tool_calls",
    }
    third = ChatCompletionResult(
        content="done",
        reasoning_content="",
        raw_json="{}",
        tool_calls=[],
        finish_reason="stop",
    )
    client = StubChatClient.scripted([first, second, third])

    r1 = client.chat_completion([{"role": "user", "content": "list"}])
    assert len(r1.tool_calls) == 1
    assert r1.tool_calls[0].name == "list_dir"
    assert r1.tool_calls[0].arguments == {"path": "."}
    assert r1.content == ""
    assert r1.finish_reason == "tool_calls"

    r2 = client.chat_completion([{"role": "user", "content": "again"}])
    assert r2.tool_calls[0].name == "speak"
    assert r2.tool_calls[0].arguments == {"text": "hi"}

    r3 = client.chat_completion([{"role": "user", "content": "end"}])
    assert r3.content == "done"
    assert r3.tool_calls == []

    # Exhausted script holds last response
    r4 = client.chat_completion([{"role": "user", "content": "extra"}])
    assert r4.content == "done"


def test_stub_scripted_with_toolcall_objects():
    client = StubChatClient.scripted(
        [
            {
                "content": "",
                "tool_calls": [
                    ToolCall(
                        id="c1",
                        name="echo",
                        arguments={"text": "x"},
                        arguments_raw='{"text": "x"}',
                        arguments_parse_ok=True,
                    )
                ],
            }
        ]
    )
    result = client.chat_completion([{"role": "user", "content": "x"}])
    assert result.tool_calls[0].name == "echo"
    assert result.tool_calls[0].arguments == {"text": "x"}


def test_stub_scripted_flat_tool_calls_mini_dict():
    client = StubChatClient.scripted(
        [
            {
                "content": "",
                "finish_reason": "tool_calls",
                "tool_calls": [
                    {"id": "1", "name": "echo", "arguments": {"text": "x"}},
                ],
            }
        ]
    )
    result = client.chat_completion([{"role": "user", "content": "x"}])
    assert result.tool_calls[0].name == "echo"
    assert result.tool_calls[0].arguments == {"text": "x"}
    assert result.finish_reason == "tool_calls"


def test_stub_scripted_finish_reason_coerced_to_str():
    client = StubChatClient.scripted(
        [{"content": "ok", "finish_reason": 0, "tool_calls": []}]
    )
    result = client.chat_completion([{"role": "user", "content": "x"}])
    assert result.finish_reason == "0"
    assert isinstance(result.finish_reason, str)


def test_stub_rejects_mapping_as_responses():
    with pytest.raises(TypeError, match="list/tuple"):
        StubChatClient(responses={"step": "oops"})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# HttpChatClient: tools in payload + response parse (fake server)
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Minimal fake llama-server: records body, returns fixture response."""

    last_body: bytes = b""
    response_payload: dict[str, Any] = {}

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        _RecordingHandler.last_body = self.rfile.read(length)
        body = json.dumps(_RecordingHandler.response_payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return


@pytest.fixture
def fake_chat_server():
    port = _free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_client_includes_tools_in_payload(fake_chat_server):
    fx = _fixtures()
    _RecordingHandler.response_payload = fx["openai_response_list_dir"]
    config = LlamaServerConfig(host="127.0.0.1", port=fake_chat_server, use_reasoning=False)
    client = HttpChatClient(config)
    tools = fx["tools_list_dir"]
    result = client.chat_completion(
        [{"role": "user", "content": "list files"}],
        tools=tools,
        tool_choice="required",
        max_tokens=64,
        reasoning=False,
    )
    sent = json.loads(_RecordingHandler.last_body.decode("utf-8"))
    assert sent["tools"] == tools
    assert sent["tool_choice"] == "required"
    assert "messages" in sent
    assert result.tool_calls[0].name == "list_dir"
    assert result.tool_calls[0].arguments == {"path": "."}
    assert result.content == ""
    assert result.finish_reason == "tool_calls"


def test_http_client_parses_malformed_args_from_response(fake_chat_server):
    fx = _fixtures()
    _RecordingHandler.response_payload = fx["openai_response_malformed_args"]
    config = LlamaServerConfig(host="127.0.0.1", port=fake_chat_server, use_reasoning=False)
    client = HttpChatClient(config)
    result = client.chat_completion([{"role": "user", "content": "x"}])
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].arguments_parse_ok is False
    assert result.tool_calls[0].arguments == {}


def test_http_client_handles_args_already_object(fake_chat_server):
    fx = _fixtures()
    _RecordingHandler.response_payload = fx["openai_response_args_object"]
    config = LlamaServerConfig(host="127.0.0.1", port=fake_chat_server, use_reasoning=False)
    client = HttpChatClient(config)
    result = client.chat_completion([{"role": "user", "content": "x"}])
    assert result.tool_calls[0].arguments == {"text": "hello"}
    assert result.tool_calls[0].arguments_parse_ok is True


def test_http_client_omits_tools_keys_when_none(fake_chat_server):
    fx = _fixtures()
    _RecordingHandler.response_payload = fx["openai_response_content_only"]
    config = LlamaServerConfig(host="127.0.0.1", port=fake_chat_server, use_reasoning=False)
    client = HttpChatClient(config)
    result = client.chat_completion(
        [{"role": "user", "content": "hi"}],
        max_tokens=32,
        reasoning=False,
    )
    sent = json.loads(_RecordingHandler.last_body.decode("utf-8"))
    assert "tools" not in sent
    assert "tool_choice" not in sent
    assert result.tool_calls == []
    assert result.content == "Just text, no tools."
    assert result.finish_reason == "stop"
    assert result.reasoning_content == "thinking"


def test_http_client_omits_top_p_top_k_when_none(fake_chat_server):
    """Default config leaves top_p/top_k None → keys omitted from wire payload."""
    fx = _fixtures()
    _RecordingHandler.response_payload = fx["openai_response_content_only"]
    config = LlamaServerConfig(host="127.0.0.1", port=fake_chat_server, use_reasoning=False)
    assert config.top_p is None
    assert config.top_k is None
    client = HttpChatClient(config)
    client.chat_completion(
        [{"role": "user", "content": "hi"}],
        max_tokens=32,
        reasoning=False,
    )
    sent = json.loads(_RecordingHandler.last_body.decode("utf-8"))
    assert "top_p" not in sent
    assert "top_k" not in sent


def test_http_client_includes_top_p_top_k_from_kwargs(fake_chat_server):
    """Explicit kwargs override and appear on the wire even when config is None."""
    fx = _fixtures()
    _RecordingHandler.response_payload = fx["openai_response_content_only"]
    config = LlamaServerConfig(host="127.0.0.1", port=fake_chat_server, use_reasoning=False)
    client = HttpChatClient(config)
    client.chat_completion(
        [{"role": "user", "content": "hi"}],
        max_tokens=32,
        reasoning=False,
        top_p=0.95,
        top_k=64,
    )
    sent = json.loads(_RecordingHandler.last_body.decode("utf-8"))
    assert sent["top_p"] == 0.95
    assert sent["top_k"] == 64


def test_http_client_falls_back_to_config_top_p_top_k(fake_chat_server):
    """When kwargs are None, config values are sent on the wire."""
    fx = _fixtures()
    _RecordingHandler.response_payload = fx["openai_response_content_only"]
    config = LlamaServerConfig(
        host="127.0.0.1",
        port=fake_chat_server,
        use_reasoning=False,
        top_p=0.9,
        top_k=40,
    )
    client = HttpChatClient(config)
    client.chat_completion(
        [{"role": "user", "content": "hi"}],
        max_tokens=32,
        reasoning=False,
    )
    sent = json.loads(_RecordingHandler.last_body.decode("utf-8"))
    assert sent["top_p"] == 0.9
    assert sent["top_k"] == 40


def test_http_client_kwarg_overrides_config_top_p_top_k(fake_chat_server):
    """Explicit kwargs win over config defaults."""
    fx = _fixtures()
    _RecordingHandler.response_payload = fx["openai_response_content_only"]
    config = LlamaServerConfig(
        host="127.0.0.1",
        port=fake_chat_server,
        use_reasoning=False,
        top_p=0.9,
        top_k=40,
    )
    client = HttpChatClient(config)
    client.chat_completion(
        [{"role": "user", "content": "hi"}],
        max_tokens=32,
        reasoning=False,
        top_p=0.5,
        top_k=16,
    )
    sent = json.loads(_RecordingHandler.last_body.decode("utf-8"))
    assert sent["top_p"] == 0.5
    assert sent["top_k"] == 16


def test_gated_client_forwards_tools_kwargs():
    captured: dict[str, Any] = {}

    def fake_inner(
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 4096,
        reasoning: bool = True,
        temperature: float | None = None,
        top_p: float | None = None,
        top_k: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> ChatCompletionResult:
        captured["tools"] = tools
        captured["tool_choice"] = tool_choice
        captured["top_p"] = top_p
        captured["top_k"] = top_k
        return ChatCompletionResult(content="ok", reasoning_content="", raw_json="{}")

    class _Inner:
        chat_completion = staticmethod(fake_inner)

    gate = LlamaServerGate()
    client = GatedChatClient(_Inner(), gate)  # type: ignore[arg-type]
    tools = _fixtures()["tools_echo"]
    client.chat_completion(
        [{"role": "user", "content": "x"}],
        tools=tools,
        tool_choice="auto",
        top_p=0.95,
        top_k=64,
    )
    assert captured["tools"] == tools
    assert captured["tool_choice"] == "auto"
    assert captured["top_p"] == 0.95
    assert captured["top_k"] == 64


# ---------------------------------------------------------------------------
# Real model (@pytest.mark.llm)
# ---------------------------------------------------------------------------


def _model_available() -> bool:
    paths = resolve_paths()
    return not validate_model_paths(paths)


def _server_healthy(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


@pytest.fixture(scope="module")
def live_llama_server():
    """Use an already-running server on :8080, or start one for the module.

    Skips cleanly when model files are missing.
    """
    if not _model_available():
        problems = validate_model_paths(resolve_paths())
        pytest.skip("model not available: " + "; ".join(problems))
    paths = resolve_paths()
    default_config = LlamaServerConfig()
    health = default_config.health_url
    owned_proc: subprocess.Popen[bytes] | None = None
    port = default_config.port

    if _server_healthy(health):
        # Reuse existing server
        yield LlamaServerConfig(host="127.0.0.1", port=port)
        return

    # Start with a modest context to load faster / use less VRAM for tool smoke.
    port = _free_port()
    config = LlamaServerConfig(host="127.0.0.1", port=port)
    cmd = build_server_command(
        paths,
        config,
        context_tokens=8192,
        batch_size=512,
        ubatch_size=512,
    )
    # Drop mmproj if it slows start; tools tests are text-only. Keep as built
    # by build_server_command for fidelity with production argv.
    owned_proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(paths.home),
    )
    deadline = time.time() + 300
    ready = False
    try:
        while time.time() < deadline:
            if owned_proc.poll() is not None:
                out = b""
                if owned_proc.stdout:
                    out = owned_proc.stdout.read() or b""
                pytest.skip(
                    f"llama-server exited early (code {owned_proc.returncode}): "
                    f"{out[-1500:].decode('utf-8', errors='replace')}"
                )
            if _server_healthy(config.health_url, timeout=1.0):
                ready = True
                break
            time.sleep(1.0)
        if not ready:
            owned_proc.terminate()
            try:
                owned_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                owned_proc.kill()
            pytest.skip("llama-server did not become healthy within 300s")
        yield config
    finally:
        if owned_proc is not None and owned_proc.poll() is None:
            owned_proc.terminate()
            try:
                owned_proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                owned_proc.kill()
                owned_proc.wait(timeout=10)


@pytest.mark.llm
def test_real_model_accepts_tools_schema_and_emits_tool_call(live_llama_server):
    """OpenAI tools schema accepted; model emits at least one tool_call with name.

    Note: tool_choice=\"required\" can trip Gemma peg-format errors on some
    llama.cpp builds; pin the function instead (same wire path for tools[]).
    """
    config = live_llama_server
    fx = _fixtures()
    tools = fx["tools_echo"]
    client = HttpChatClient(config)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a tool-using assistant. You MUST call the echo tool. "
                "Do not answer in plain text; always use a tool call."
            ),
        },
        {
            "role": "user",
            "content": "Please echo the word hello using the echo tool.",
        },
    ]
    result = client.chat_completion(
        messages,
        max_tokens=256,
        reasoning=False,
        temperature=0.1,
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "echo"}},
    )

    assert isinstance(result, ChatCompletionResult)
    assert result.raw_json, "expected raw response body"
    assert len(result.tool_calls) >= 1, (
        f"expected tool_calls from real model; content={result.content!r} "
        f"finish={result.finish_reason!r} raw_snip={result.raw_json[:800]!r}"
    )
    tc = result.tool_calls[0]
    assert tc.name == "echo", f"unexpected tool name: {tc.name!r}"
    assert tc.id, "tool call id should be non-empty"
    # Parsed shape: either ok dict or flagged parse failure (no crash either way)
    assert isinstance(tc.arguments, dict)
    if tc.arguments_parse_ok:
        # Prefer text key when present
        if "text" in tc.arguments:
            assert isinstance(tc.arguments["text"], str)


@pytest.mark.llm
def test_real_model_client_parses_tool_calls_shape(live_llama_server):
    """Client correctly surfaces id, name, arguments dict from live completion."""
    config = live_llama_server
    tools = _fixtures()["tools_list_dir"]
    client = HttpChatClient(config)
    messages = [
        {
            "role": "system",
            "content": (
                "You must use the list_dir tool. Call list_dir with path '.' "
                "and nothing else."
            ),
        },
        {"role": "user", "content": "List the current directory using list_dir."},
    ]
    result = client.chat_completion(
        messages,
        max_tokens=256,
        reasoning=False,
        temperature=0.1,
        tools=tools,
        tool_choice={
            "type": "function",
            "function": {"name": "list_dir"},
        },
    )
    assert result.tool_calls, (
        f"no tool_calls; content={result.content!r} raw={result.raw_json[:800]!r}"
    )
    for tc in result.tool_calls:
        assert isinstance(tc.id, str)
        assert isinstance(tc.name, str) and tc.name
        assert isinstance(tc.arguments, dict)
        assert isinstance(tc.arguments_raw, str)
        assert isinstance(tc.arguments_parse_ok, bool)


def test_malformed_args_partial_json_is_safe_via_parser():
    """Invalid partial JSON arguments never crash parse (Http contract path).

    Live models rarely emit broken JSON; unit/fake-HTTP cover this mode.
    Kept as a unit test (not @pytest.mark.llm) — no llama-server call.
    """
    broken = [
        {
            "id": "call_live_bad",
            "type": "function",
            "function": {"name": "list_dir", "arguments": '{"path":'},
        }
    ]
    calls = parse_tool_calls(broken)
    assert calls[0].arguments_parse_ok is False
    assert calls[0].arguments == {}
    assert calls[0].name == "list_dir"
