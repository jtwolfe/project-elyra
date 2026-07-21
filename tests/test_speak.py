"""Tests for speak transport and speak tool (PR8a).

Behaviour names: delivery to glass, failure reasons, counts_as_speak, registry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.messages import list_messages
from elyra.speak import SpeakDelivery, SpeakTransport
from elyra.tools import ToolContext, ToolRegistry, ToolResult
from elyra.tools.builtin.social import speak as speak_handler
from elyra.tools.policy import resolve_bundled_tools_root


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


@pytest.fixture
def transport(paths) -> SpeakTransport:
    return SpeakTransport(paths)


@pytest.fixture
def ctx(paths, transport: SpeakTransport) -> ToolContext:
    return ToolContext(
        paths=paths,
        speak=transport,
        moment_id="moment-1",
        user_id="operator",
    )


@pytest.fixture
def registry(home: Path) -> ToolRegistry:
    return ToolRegistry(
        resolve_paths(home),
        bundled_root=resolve_bundled_tools_root(),
    )


def _assistant_rows(paths) -> list[dict[str, Any]]:
    return [r for r in list_messages(paths=paths) if r.get("role") == "assistant"]


# ---------------------------------------------------------------------------
# SpeakTransport unit
# ---------------------------------------------------------------------------


def test_deliver_writes_assistant_glass_row(transport: SpeakTransport, paths) -> None:
    result = transport.deliver("Hello from Elyra", user_id="operator", moment_id="m1")
    assert result.ok is True
    assert result.reason is None
    assert result.message_id
    assert result.text == "Hello from Elyra"
    assert result.user_id == "operator"

    rows = _assistant_rows(paths)
    assert len(rows) == 1
    assert rows[0]["content"] == "Hello from Elyra"
    assert rows[0]["role"] == "assistant"
    assert rows[0]["user_id"] == "operator"
    assert rows[0]["moment_id"] == "m1"
    assert rows[0]["id"] == result.message_id


def test_deliver_empty_text_does_not_write_glass(
    transport: SpeakTransport, paths
) -> None:
    result = transport.deliver("   ", user_id="operator")
    assert result.ok is False
    assert result.reason == "empty_text"
    assert result.message_id is None
    assert _assistant_rows(paths) == []


def test_deliver_invalid_text_type(transport: SpeakTransport, paths) -> None:
    result = transport.deliver(None)  # type: ignore[arg-type]
    assert result.ok is False
    assert result.reason == "invalid_text"
    assert _assistant_rows(paths) == []


def test_deliver_append_failure_returns_reason(paths) -> None:
    def boom(*_a, **_k):
        raise OSError("disk full")

    transport = SpeakTransport(paths, append=boom)
    result = transport.deliver("will fail")
    assert result.ok is False
    assert result.reason == "append_failed:OSError"
    assert result.as_payload()["transport_ok"] is False
    assert result.as_payload()["reason"] == "append_failed:OSError"
    assert _assistant_rows(paths) == []


def test_deliver_defaults_blank_user_id_to_operator(
    transport: SpeakTransport, paths
) -> None:
    result = transport.deliver("hi", user_id="")
    assert result.ok is True
    assert result.user_id == "operator"
    assert _assistant_rows(paths)[0]["user_id"] == "operator"


def test_speak_delivery_as_payload_includes_transport_ok() -> None:
    ok = SpeakDelivery(
        ok=True,
        text="x",
        user_id="operator",
        message_id="mid",
        moment_id="m",
    )
    payload = ok.as_payload()
    assert payload["transport_ok"] is True
    assert payload["message_id"] == "mid"
    assert "reason" not in payload

    bad = SpeakDelivery(
        ok=False, text="", user_id="operator", reason="empty_text"
    )
    bad_payload = bad.as_payload()
    assert bad_payload["transport_ok"] is False
    assert bad_payload["reason"] == "empty_text"


# ---------------------------------------------------------------------------
# speak tool handler
# ---------------------------------------------------------------------------


def test_speak_success_counts_as_speak(ctx: ToolContext, paths) -> None:
    result = speak_handler({"text": "Ready when you are."}, ctx)
    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert result.counts_as_speak is True
    assert result.error_reason is None
    assert result.payload["transport_ok"] is True
    assert result.payload["text"] == "Ready when you are."
    assert result.payload["user_id"] == "operator"
    assert result.payload.get("message_id")
    assert result.payload.get("moment_id") == "moment-1"

    rows = _assistant_rows(paths)
    assert len(rows) == 1
    assert rows[0]["content"] == "Ready when you are."
    assert rows[0]["moment_id"] == "moment-1"


def test_speak_empty_text_ok_false_reason_in_payload(ctx: ToolContext, paths) -> None:
    result = speak_handler({"text": ""}, ctx)
    assert result.ok is False
    assert result.counts_as_speak is False
    assert result.error_reason == "empty_text"
    assert result.payload["transport_ok"] is False
    assert result.payload["reason"] == "empty_text"
    assert _assistant_rows(paths) == []


def test_speak_missing_text(ctx: ToolContext, paths) -> None:
    result = speak_handler({}, ctx)
    assert result.ok is False
    assert result.counts_as_speak is False
    assert result.error_reason == "missing_text"
    assert result.payload["reason"] == "missing_text"
    assert _assistant_rows(paths) == []


def test_speak_transport_failure_reason_in_payload(paths) -> None:
    def boom(*_a, **_k):
        raise RuntimeError("nope")

    transport = SpeakTransport(paths, append=boom)
    ctx = ToolContext(paths=paths, speak=transport, user_id="operator")
    result = speak_handler({"text": "hello"}, ctx)
    assert result.ok is False
    assert result.counts_as_speak is False
    assert result.error_reason == "append_failed:RuntimeError"
    assert result.payload["transport_ok"] is False
    assert result.payload["reason"] == "append_failed:RuntimeError"
    assert _assistant_rows(paths) == []


def test_speak_uses_args_user_id_over_context(paths) -> None:
    transport = SpeakTransport(paths)
    ctx = ToolContext(
        paths=paths,
        speak=transport,
        user_id="operator",
        moment_id="m2",
    )
    result = speak_handler({"text": "for jim", "user_id": "jim"}, ctx)
    assert result.ok is True
    assert result.payload["user_id"] == "jim"
    assert _assistant_rows(paths)[0]["user_id"] == "jim"


def test_speak_defaults_user_id_from_context(paths) -> None:
    transport = SpeakTransport(paths)
    ctx = ToolContext(paths=paths, speak=transport, user_id="alice")
    result = speak_handler({"text": "hi alice"}, ctx)
    assert result.ok is True
    assert result.payload["user_id"] == "alice"


def test_speak_constructs_transport_from_paths_when_unset(paths) -> None:
    """Thin wrapper may build SpeakTransport from ctx.paths."""
    ctx = ToolContext(paths=paths, user_id="operator", moment_id="auto")
    result = speak_handler({"text": "path-only"}, ctx)
    assert result.ok is True
    assert result.counts_as_speak is True
    assert _assistant_rows(paths)[0]["content"] == "path-only"


def test_speak_never_writes_on_failure(ctx: ToolContext, paths) -> None:
    speak_handler({"text": "  \n\t  "}, ctx)
    speak_handler({}, ctx)
    assert list_messages(paths=paths) == []


# ---------------------------------------------------------------------------
# Registry integration — bundled speak package
# ---------------------------------------------------------------------------


def test_registry_discovers_bundled_speak(registry: ToolRegistry) -> None:
    assert registry.has("speak")
    pkg = registry.get("speak")
    assert pkg is not None
    assert pkg.meta.kind == "speak"
    assert pkg.meta.name == "speak"
    assert "text" in pkg.meta.parameters.get("properties", {})
    assert "text" in pkg.meta.parameters.get("required", [])
    assert pkg.runner.kind == "builtin"
    assert pkg.handler is not None


def test_registry_execute_speak_success(registry: ToolRegistry, paths) -> None:
    transport = SpeakTransport(paths)
    ctx = ToolContext(
        paths=paths,
        speak=transport,
        moment_id="reg-1",
        user_id="operator",
    )
    result = registry.execute("speak", {"text": "via registry"}, ctx)
    assert result.ok is True
    assert result.counts_as_speak is True
    assert result.payload["transport_ok"] is True
    rows = _assistant_rows(paths)
    assert len(rows) == 1
    assert rows[0]["content"] == "via registry"


def test_registry_execute_speak_failure_keeps_counts_false(
    registry: ToolRegistry, paths
) -> None:
    ctx = ToolContext(paths=paths, moment_id="reg-2")
    result = registry.execute("speak", {"text": ""}, ctx)
    assert result.ok is False
    assert result.counts_as_speak is False
    assert result.payload.get("reason") == "empty_text"
    assert _assistant_rows(paths) == []


def test_registry_preserves_counts_as_speak_for_speak_kind(
    registry: ToolRegistry, paths
) -> None:
    """kind=speak is allowlisted so counts_as_speak is not stripped."""
    ctx = ToolContext(paths=paths, user_id="operator")
    result = registry.execute("speak", {"text": "allowed flag"}, ctx)
    assert result.counts_as_speak is True


def test_openai_tools_includes_speak(registry: ToolRegistry) -> None:
    tools = registry.openai_tools()
    names = [t["function"]["name"] for t in tools]
    assert "speak" in names
    speak_tool = next(t for t in tools if t["function"]["name"] == "speak")
    params = speak_tool["function"]["parameters"]
    assert "text" in params["properties"]
    assert "text" in params["required"]


def test_only_speak_transport_writes_assistant_via_product_path(
    transport: SpeakTransport, paths
) -> None:
    """Product path: glass assistant rows come from SpeakTransport.deliver.

    Bare content must not be written by the speak tool path; transport is
    the sole owner of assistant glass delivery for tools.
    """
    # User rows are fine (API path) — not under speak ownership.
    from elyra.messages import append_message

    append_message("user", "hi", user_id="operator", paths=paths)

    # Speak product path
    d = transport.deliver("reply", user_id="operator")
    assert d.ok

    rows = list_messages(paths=paths)
    assistants = [r for r in rows if r["role"] == "assistant"]
    users = [r for r in rows if r["role"] == "user"]
    assert len(users) == 1
    assert len(assistants) == 1
    assert assistants[0]["content"] == "reply"
    # No orphan assistant without going through transport
    assert assistants[0]["id"] == d.message_id
