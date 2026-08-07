"""Tests for speak transport and speak tool (PR8a + PR8 multimodal).

Behaviour names: delivery to glass, failure reasons, counts_as_speak, registry,
sandbox path ingest, re-send by attachment_ids, empty-text+attachments reject.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.media import MediaStore, get_attachment, put_bytes
from elyra.messages import list_messages
from elyra.sandbox import Sandbox
from elyra.speak import SpeakDelivery, SpeakTransport
from elyra.tools import ToolContext, ToolRegistry, ToolResult
from elyra.tools.builtin.social import speak as speak_handler
from elyra.tools.policy import resolve_bundled_tools_root

FIXTURE_PNG = Path(__file__).parent / "fixtures" / "media" / "1x1.png"


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


def test_deliver_append_returns_none_does_not_raise(paths) -> None:
    """Contract: never raise when inject/double returns non-Message."""
    transport = SpeakTransport(paths, append=lambda *_a, **_k: None)
    result = transport.deliver("will not land")
    assert result.ok is False
    assert result.reason == "append_failed:invalid_return"
    assert result.message_id is None
    assert result.as_payload()["transport_ok"] is False
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


def test_speak_non_string_text_is_invalid_text(ctx: ToolContext, paths) -> None:
    """Present but wrong type → invalid_text (not missing_text)."""
    for bad in (42, True, ["x"], {"t": "x"}, None):
        result = speak_handler({"text": bad}, ctx)
        assert result.ok is False
        assert result.counts_as_speak is False
        assert result.error_reason == "invalid_text"
        assert result.payload["reason"] == "invalid_text"
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


# ---------------------------------------------------------------------------
# PR8 — speak attachments: sandbox path ingest + re-send by id
# ---------------------------------------------------------------------------


@pytest.fixture
def sandbox(paths) -> Sandbox:
    return Sandbox(paths)


@pytest.fixture
def ctx_sandbox(paths, transport: SpeakTransport, sandbox: Sandbox) -> ToolContext:
    return ToolContext(
        paths=paths,
        speak=transport,
        sandbox=sandbox,
        moment_id="moment-1",
        user_id="operator",
    )


def _write_sandbox_png(sandbox: Sandbox, rel: str = "tmp/plot.png") -> Path:
    data = FIXTURE_PNG.read_bytes()
    dest = sandbox.resolve(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


def test_deliver_with_attachments_writes_inventory_and_binds(
    transport: SpeakTransport, paths
) -> None:
    store = MediaStore(paths)
    att = store.put_bytes(
        FIXTURE_PNG.read_bytes(),
        filename="shot.png",
        origin="speak",
    )
    assert att.bound_message_id is None

    result = transport.deliver(
        "Here is a shot.",
        user_id="operator",
        moment_id="m-att",
        attachments=[att],
    )
    assert result.ok is True
    assert result.message_id
    assert len(result.attachments) == 1
    assert result.as_payload()["attachment_ids"] == [att.id]

    rows = _assistant_rows(paths)
    assert len(rows) == 1
    assert rows[0]["content"] == "Here is a shot."
    assert rows[0]["attachments"]
    assert rows[0]["attachments"][0]["id"] == att.id
    assert rows[0]["attachments"][0]["kind"] == "image"

    bound = store.get(att.id)
    assert bound is not None
    assert bound.bound_message_id == result.message_id


def test_deliver_empty_text_rejects_even_with_attachments(
    transport: SpeakTransport, paths
) -> None:
    att = put_bytes(
        b"x",
        filename="note.txt",
        origin="speak",
        paths=paths,
    )
    result = transport.deliver("   ", attachments=[att])
    assert result.ok is False
    assert result.reason == "empty_text"
    assert _assistant_rows(paths) == []
    # Unbound meta left as-is (transport did not bind).
    assert get_attachment(att.id, paths=paths).bound_message_id is None


def test_speak_sandbox_path_ingest_projects_ro(
    ctx_sandbox: ToolContext, paths, sandbox: Sandbox
) -> None:
    _write_sandbox_png(sandbox, "tmp/plot.png")
    result = speak_handler(
        {
            "text": "Here is the plot from the run.",
            "attachments": [{"path": "tmp/plot.png"}],
        },
        ctx_sandbox,
    )
    assert result.ok is True
    assert result.counts_as_speak is True
    assert result.payload["transport_ok"] is True
    ids = result.payload.get("attachment_ids") or []
    assert len(ids) == 1
    aid = ids[0]
    assert aid.startswith("att_")

    att = get_attachment(aid, paths=paths)
    assert att is not None
    assert att.kind == "image"
    assert att.mime == "image/png"
    assert att.origin == "speak"
    assert att.bound_message_id == result.payload["message_id"]
    assert att.sandbox_relpath == f"media/{aid}/plot.png"

    # RO projection under sandbox media/
    mirror = sandbox.resolve(att.sandbox_relpath)
    assert mirror.is_file()
    assert mirror.read_bytes() == FIXTURE_PNG.read_bytes()

    rows = _assistant_rows(paths)
    assert len(rows) == 1
    assert rows[0]["attachments"][0]["id"] == aid
    assert rows[0]["content"] == "Here is the plot from the run."


def test_speak_empty_text_with_attachments_rejects_without_ingest(
    ctx_sandbox: ToolContext, paths, sandbox: Sandbox
) -> None:
    """KD8 / R1: caption required; do not orphan media on empty text."""
    _write_sandbox_png(sandbox, "tmp/orphan.png")
    result = speak_handler(
        {"text": "", "attachments": [{"path": "tmp/orphan.png"}]},
        ctx_sandbox,
    )
    assert result.ok is False
    assert result.error_reason == "empty_text"
    assert result.counts_as_speak is False
    assert _assistant_rows(paths) == []
    # No media meta written (empty text checked before ingest).
    store = MediaStore(paths)
    assert store.list_meta_ids() == []


def test_speak_whitespace_text_with_attachment_ids_rejects(
    ctx_sandbox: ToolContext, paths
) -> None:
    att = put_bytes(
        FIXTURE_PNG.read_bytes(),
        filename="a.png",
        origin="tool",
        paths=paths,
    )
    result = speak_handler(
        {"text": "\n\t  ", "attachment_ids": [att.id]},
        ctx_sandbox,
    )
    assert result.ok is False
    assert result.error_reason == "empty_text"
    assert _assistant_rows(paths) == []
    # Original remains unbound (no re-send clone).
    assert get_attachment(att.id, paths=paths).bound_message_id is None


def test_speak_resend_by_attachment_id_new_att_same_sha(
    ctx_sandbox: ToolContext, paths
) -> None:
    """KD16: re-send creates new att_id pointing at same sha."""
    store = MediaStore(paths)
    original = store.put_bytes(
        FIXTURE_PNG.read_bytes(),
        filename="prior.png",
        origin="user_upload",
    )
    store.bind_message(original.id, "prior-msg-1")

    result = speak_handler(
        {
            "text": "Here is that plot again.",
            "attachment_ids": [original.id],
        },
        ctx_sandbox,
    )
    assert result.ok is True
    new_ids = result.payload["attachment_ids"]
    assert len(new_ids) == 1
    new_id = new_ids[0]
    assert new_id != original.id

    cloned = get_attachment(new_id, paths=paths)
    assert cloned is not None
    assert cloned.sha256 == original.sha256
    assert cloned.bound_message_id == result.payload["message_id"]
    assert cloned.source_message_id == "prior-msg-1"
    assert cloned.origin == "speak"
    # Original binding unchanged
    assert get_attachment(original.id, paths=paths).bound_message_id == "prior-msg-1"
    # Same blob file on disk
    assert store.blob_path(original.sha256).is_file()
    assert store.read_bytes(new_id) == store.read_bytes(original.id)

    rows = _assistant_rows(paths)
    assert rows[0]["attachments"][0]["id"] == new_id


def test_speak_unknown_attachment_id(ctx_sandbox: ToolContext, paths) -> None:
    result = speak_handler(
        {"text": "missing media", "attachment_ids": ["att_doesnotexist000"]},
        ctx_sandbox,
    )
    assert result.ok is False
    assert result.error_reason == "attachment_not_found"
    assert result.counts_as_speak is False
    assert _assistant_rows(paths) == []


def test_speak_missing_sandbox_path(ctx_sandbox: ToolContext, paths) -> None:
    result = speak_handler(
        {
            "text": "no file",
            "attachments": [{"path": "tmp/does-not-exist.png"}],
        },
        ctx_sandbox,
    )
    assert result.ok is False
    assert result.error_reason == "not_found"
    assert _assistant_rows(paths) == []


def test_speak_path_escape_rejected(ctx_sandbox: ToolContext, paths) -> None:
    result = speak_handler(
        {
            "text": "escape",
            "attachments": [{"path": "../outside.png"}],
        },
        ctx_sandbox,
    )
    assert result.ok is False
    assert result.error_reason == "path_escape"
    assert _assistant_rows(paths) == []


def test_speak_combined_path_and_resend(
    ctx_sandbox: ToolContext, paths, sandbox: Sandbox
) -> None:
    _write_sandbox_png(sandbox, "tmp/new.png")
    prior = put_bytes(
        b"prior-bytes-for-resend",
        filename="old.txt",
        origin="tool",
        paths=paths,
    )
    result = speak_handler(
        {
            "text": "new and prior",
            "attachments": [{"path": "tmp/new.png", "filename": "display.png"}],
            "attachment_ids": [prior.id],
        },
        ctx_sandbox,
    )
    assert result.ok is True
    ids = result.payload["attachment_ids"]
    assert len(ids) == 2
    # Path ingest first, then id clone
    path_att = get_attachment(ids[0], paths=paths)
    clone_att = get_attachment(ids[1], paths=paths)
    assert path_att is not None and path_att.kind == "image"
    assert path_att.filename == "display.png"
    assert clone_att is not None and clone_att.sha256 == prior.sha256
    assert clone_att.id != prior.id


def test_speak_too_many_attachments(ctx_sandbox: ToolContext, paths) -> None:
    ids = []
    for i in range(9):
        att = put_bytes(
            f"blob-{i}".encode(),
            filename=f"f{i}.txt",
            origin="tool",
            paths=paths,
        )
        ids.append(att.id)
    result = speak_handler(
        {"text": "too many", "attachment_ids": ids},
        ctx_sandbox,
    )
    assert result.ok is False
    assert result.error_reason == "too_many_attachments"
    assert _assistant_rows(paths) == []


def test_openai_tools_speak_schema_includes_attachment_fields(
    registry: ToolRegistry,
) -> None:
    tools = registry.openai_tools()
    speak_tool = next(t for t in tools if t["function"]["name"] == "speak")
    props = speak_tool["function"]["parameters"]["properties"]
    assert "attachment_ids" in props
    assert "attachments" in props
    assert "text" in speak_tool["function"]["parameters"]["required"]
    assert props["attachments"]["items"]["required"] == ["path"]


def test_registry_execute_speak_with_path(
    registry: ToolRegistry, paths, sandbox: Sandbox
) -> None:
    _write_sandbox_png(sandbox, "tmp/reg.png")
    ctx = ToolContext(
        paths=paths,
        speak=SpeakTransport(paths),
        sandbox=sandbox,
        user_id="operator",
        moment_id="reg-att",
    )
    result = registry.execute(
        "speak",
        {
            "text": "via registry with plot",
            "attachments": [{"path": "tmp/reg.png"}],
        },
        ctx,
    )
    assert result.ok is True
    assert result.counts_as_speak is True
    assert result.payload.get("attachment_ids")
    rows = _assistant_rows(paths)
    assert rows[0]["attachments"]


# ---------------------------------------------------------------------------
# PR3c — KD3 resolve + KD20 group null user_id (T8, T15, DM peer stamp)
# ---------------------------------------------------------------------------


def test_t8_group_social_kind_missing_conversation_no_dm_demotion(paths) -> None:
    """T8: social_kind=group, conversation_id=None, user_id set → missing_conversation.

    Must not write dm:jim (no silent group→DM demotion).
    """
    transport = SpeakTransport(paths)
    ctx = ToolContext(
        paths=paths,
        speak=transport,
        moment_id="m-t8",
        user_id="jim",
        conversation_id=None,
        extras={"social_kind": "group"},
    )
    result = speak_handler({"text": "should not land"}, ctx)
    assert result.ok is False
    assert result.counts_as_speak is False
    assert result.error_reason == "missing_conversation"
    assert result.payload["reason"] == "missing_conversation"
    assert result.payload["transport_ok"] is False
    assert _assistant_rows(paths) == []
    # No glass row with dm:jim either
    all_rows = list_messages(paths=paths)
    assert all_rows == []


def test_t8_group_social_kind_user_id_arg_still_missing_conversation(paths) -> None:
    """Bare user_id arg under social_kind=group does not open a DM."""
    transport = SpeakTransport(paths)
    ctx = ToolContext(
        paths=paths,
        speak=transport,
        user_id="jim",
        conversation_id=None,
        extras={"social_kind": "group"},
    )
    result = speak_handler({"text": "nope", "user_id": "jim"}, ctx)
    assert result.ok is False
    assert result.error_reason == "missing_conversation"
    assert _assistant_rows(paths) == []


def test_t15_group_speak_delivery_user_id_none(paths) -> None:
    """T15: group speak → SpeakDelivery.user_id None + glass row null, not operator."""
    from elyra.conversations import ConversationsStore

    store = ConversationsStore(paths)
    store.create_group(
        name="Room",
        members=["jim", "sam"],
        conversation_id="group:room1",
    )
    transport = SpeakTransport(paths)
    ctx = ToolContext(
        paths=paths,
        speak=transport,
        moment_id="m-t15",
        user_id="jim",
        conversation_id="group:room1",
        extras={"social_kind": "group"},
    )
    result = speak_handler({"text": "hello room"}, ctx)
    assert result.ok is True, result.error_reason
    assert result.counts_as_speak is True
    # Payload JSON null for user_id (not omitted as inventing operator)
    assert "user_id" in result.payload
    assert result.payload["user_id"] is None
    assert result.payload["conversation_id"] == "group:room1"
    assert result.payload["transport_ok"] is True

    rows = _assistant_rows(paths)
    assert len(rows) == 1
    row = rows[0]
    assert row["content"] == "hello room"
    assert row.get("conversation_id") == "group:room1"
    assert row.get("user_id") is None
    assert row.get("user_id") != "operator"
    # Hard pin: never coerce group to operator
    assert row.get("user_id") not in ("operator", "jim", "sam")


def test_deliver_group_conversation_forces_null_user_id(transport: SpeakTransport, paths) -> None:
    """Transport KD20: conversation_id=group:* → user_id None regardless of input."""
    result = transport.deliver(
        "group hi",
        user_id="operator",
        conversation_id="group:g1",
        moment_id="m",
    )
    assert result.ok is True
    assert result.user_id is None
    assert result.conversation_id == "group:g1"
    payload = result.as_payload()
    assert payload["user_id"] is None
    assert payload["conversation_id"] == "group:g1"
    row = _assistant_rows(paths)[0]
    assert row.get("user_id") is None
    assert row.get("conversation_id") == "group:g1"


def test_speak_dm_peer_stamp_via_user_id_shorthand(paths) -> None:
    """DM shorthand user_id=jim → conversation dm:jim + peer stamp on assistant row."""
    transport = SpeakTransport(paths)
    ctx = ToolContext(
        paths=paths,
        speak=transport,
        user_id="operator",
        moment_id="m-dm",
    )
    result = speak_handler({"text": "for jim", "user_id": "jim"}, ctx)
    assert result.ok is True
    assert result.payload["user_id"] == "jim"
    assert result.payload["conversation_id"] == "dm:jim"
    row = _assistant_rows(paths)[0]
    assert row["user_id"] == "jim"
    assert row["conversation_id"] == "dm:jim"


def test_speak_dm_peer_stamp_via_conversation_id(paths) -> None:
    """Explicit conversation_id=dm:sam stamps peer even when ctx.user_id differs."""
    transport = SpeakTransport(paths)
    ctx = ToolContext(
        paths=paths,
        speak=transport,
        user_id="operator",
        moment_id="m-dm2",
    )
    result = speak_handler(
        {"text": "hi sam", "conversation_id": "dm:sam"},
        ctx,
    )
    assert result.ok is True
    assert result.payload["conversation_id"] == "dm:sam"
    assert result.payload["user_id"] == "sam"
    row = _assistant_rows(paths)[0]
    assert row["conversation_id"] == "dm:sam"
    # Assistant peer stamp is the DM peer (sam), not the session speaker.
    assert row["user_id"] == "sam"


def test_speak_ctx_conversation_id_group_without_arg(paths) -> None:
    """ctx.conversation_id group is used when arg omitted."""
    from elyra.conversations import ConversationsStore

    ConversationsStore(paths).create_group(
        name="G", members=["alice"], conversation_id="group:gctx"
    )
    transport = SpeakTransport(paths)
    ctx = ToolContext(
        paths=paths,
        speak=transport,
        user_id="alice",
        conversation_id="group:gctx",
        extras={"social_kind": "group"},
    )
    result = speak_handler({"text": "from ctx"}, ctx)
    assert result.ok is True
    assert result.payload["user_id"] is None
    assert result.payload["conversation_id"] == "group:gctx"
    assert _assistant_rows(paths)[0].get("user_id") is None


def test_speak_pure_work_missing_conversation_fail_closed(paths) -> None:
    """No address + no user on pure work → missing_conversation."""
    transport = SpeakTransport(paths)
    ctx = ToolContext(
        paths=paths,
        speak=transport,
        moment_id="solo",
        user_id=None,
        conversation_id=None,
        extras={"social_kind": "none"},
    )
    result = speak_handler({"text": "projective?"}, ctx)
    assert result.ok is False
    assert result.error_reason == "missing_conversation"
    assert _assistant_rows(paths) == []


def test_speak_unknown_group_conversation_not_found(paths) -> None:
    transport = SpeakTransport(paths)
    ctx = ToolContext(paths=paths, speak=transport, user_id="jim")
    result = speak_handler(
        {"text": "?", "conversation_id": "group:does-not-exist"},
        ctx,
    )
    assert result.ok is False
    assert result.error_reason == "conversation_not_found"
    assert _assistant_rows(paths) == []
