"""Multi-hop do-loop tests (PR11).

Scripted StubChatClient covers contracts; @pytest.mark.llm hits real model when
model/ + llama-server are available.
"""

from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.llm.client import ChatCompletionResult, HttpChatClient, StubChatClient
from elyra.llm.config import LlamaServerConfig
from elyra.llm.reasoning_hygiene import sanitize_completion
from elyra.llm.server import build_server_command, validate_model_paths
from elyra.loop.context import assemble_outer_meal
from elyra.loop.continuous_policy import WORK_CONTINUE_HOST, work_continue_host_message
from elyra.loop.doloop import (
    NO_SPEAK_NUDGE,
    DoLoopResult,
    _is_host_inject,
    assistant_message_from_result,
    enforce_in_turn_budget,
    run_do_loop,
    social_first_hop_tool_choice,
    tool_result_to_content,
    truncate_tool_content,
)
from elyra.messages import list_messages
from elyra.moment import MomentStore
from elyra.presence import TimerService, WakeQueue
from elyra.prompts.loader import load_prompt
from elyra.sandbox import Sandbox
from elyra.settings import Settings, default_settings
from elyra.speak import SpeakTransport
from elyra.tools import ToolContext, ToolRegistry, ToolResult
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
def sandbox(paths) -> Sandbox:
    sb = Sandbox(paths)
    # Seed a file so list_dir is non-empty / useful for multi-hop.
    (sb.root / "notes.txt").write_text("hello from sandbox\n", encoding="utf-8")
    return sb


@pytest.fixture
def registry(paths) -> ToolRegistry:
    return ToolRegistry(paths, bundled_root=resolve_bundled_tools_root())


@pytest.fixture
def speak(paths) -> SpeakTransport:
    return SpeakTransport(paths)


@pytest.fixture
def timers(paths) -> TimerService:
    return TimerService(paths, WakeQueue(paths))


@pytest.fixture
def moments(paths) -> MomentStore:
    return MomentStore(paths)


@pytest.fixture
def ctx(paths, sandbox, speak, timers, registry) -> ToolContext:
    return ToolContext(
        paths=paths,
        sandbox=sandbox,
        settings=default_settings(),
        moment_id="moment-doloop-1",
        user_id="operator",
        registry=registry,
        speak=speak,
        timers=timers,
        skills_used=[],
    )


def _settings(**loop_overrides: Any) -> Settings:
    base = default_settings()
    loop = replace(base.loop, **loop_overrides) if loop_overrides else base.loop
    return replace(base, loop=loop)


def _settings_continuous(**continuous_overrides: Any) -> Settings:
    """Settings with continuous knobs (enabled OFF by default in base)."""
    base = default_settings()
    cont = (
        replace(base.continuous, **continuous_overrides)
        if continuous_overrides
        else base.continuous
    )
    return replace(base, continuous=cont)


def _tc(
    name: str,
    arguments: dict[str, Any] | str,
    *,
    call_id: str = "call_1",
    parse_ok: bool = True,
) -> dict[str, Any]:
    """Scripted assistant turn with one tool call (flat stub shape)."""
    if not parse_ok:
        # Raw invalid JSON string → arguments_parse_ok=False via parser.
        return {
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": (
                            arguments if isinstance(arguments, str) else "{not json"
                        ),
                    },
                }
            ],
            "finish_reason": "tool_calls",
        }
    args = arguments if isinstance(arguments, dict) else {}
    return {
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "name": name,
                "arguments": args,
            }
        ],
        "finish_reason": "tool_calls",
    }


def _batch(*calls: dict[str, Any]) -> dict[str, Any]:
    """Scripted turn with multiple tool_calls (serial execute order)."""
    tool_calls = []
    for i, c in enumerate(calls):
        tool_calls.append(
            {
                "id": c.get("id", f"call_{i + 1}"),
                "name": c["name"],
                "arguments": c.get("arguments", {}),
            }
        )
    return {"content": "", "tool_calls": tool_calls, "finish_reason": "tool_calls"}


def _text(content: str = "orphan thoughts") -> dict[str, Any]:
    return {"content": content, "tool_calls": [], "finish_reason": "stop"}


def _outer() -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": "You are Elyra. Use tools."},
        {"role": "user", "content": "Please list sandbox files and say hi"},
    ]


def _run(
    client: StubChatClient,
    ctx: ToolContext,
    registry: ToolRegistry,
    *,
    settings: Settings | None = None,
    moments: MomentStore | None = None,
    social_wake: bool = False,
    **kwargs: Any,
) -> DoLoopResult:
    return run_do_loop(
        client=client,
        registry=registry,
        ctx=ctx,
        outer_prefix=_outer(),
        settings=settings or default_settings(),
        moments=moments,
        social_wake=social_wake,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_truncate_tool_content_appends_marker():
    out = truncate_tool_content("a" * 100, 40)
    assert out.endswith("…[truncated]")
    assert len(out) == 40
    # Cap smaller than marker → hard slice, no overshoot.
    tiny = truncate_tool_content("abcdefghij", 4)
    assert tiny == "abcd"
    assert len(tiny) == 4


def test_tool_result_to_content_includes_ok_and_error():
    tr = ToolResult(ok=False, payload={"x": 1}, error_reason="nope")
    raw = tool_result_to_content(tr, max_chars=8000)
    body = json.loads(raw)
    assert body["ok"] is False
    assert body["error_reason"] == "nope"
    assert body["x"] == 1


def test_tool_result_to_content_load_skill_ok_frames_playbook():
    """Successful load_skill wire content is PLAYBOOK ACTIVE plain text, not JSON."""
    body_md = "---\nname: plan-work\n---\n\n# plan-work\n\nSteps go here.\n"
    tr = ToolResult(
        ok=True,
        payload={
            "name": "plan-work",
            "description": "Break work into goals and tasks",
            "source": "bundled",
            "body": body_md,
        },
    )
    raw = tool_result_to_content(tr, max_chars=8000, tool_name="load_skill")
    assert raw.startswith("PLAYBOOK ACTIVE: plan-work")
    assert "source: bundled" in raw
    assert "## Playbook" in raw
    assert body_md.rstrip() in raw
    assert "tool_call implementing step 1" in raw
    # Must not be the old JSON envelope.
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)


def test_tool_result_to_content_load_skill_rest_honest_stop_follow_line():
    """rest framing still includes body but follow-line allows honest no-tool stop (K16)."""
    body_md = "# rest\n\nIdle honestly.\n"
    tr = ToolResult(
        ok=True,
        payload={
            "name": "rest",
            "description": "Honest idle",
            "source": "bundled",
            "body": body_md,
        },
    )
    raw = tool_result_to_content(tr, max_chars=8000, tool_name="load_skill")
    assert raw.startswith("PLAYBOOK ACTIVE: rest")
    assert "## Playbook" in raw
    assert body_md.rstrip() in raw
    assert "honest stop with no tools" in raw
    assert "must be a tool_call" not in raw


def test_tool_result_to_content_load_skill_error_stays_json():
    """Failed load_skill stays JSON (ok / error_reason)."""
    tr = ToolResult(
        ok=False,
        payload={"name": "nope"},
        error_reason="unknown_skill",
    )
    raw = tool_result_to_content(tr, max_chars=8000, tool_name="load_skill")
    body = json.loads(raw)
    assert body["ok"] is False
    assert body["error_reason"] == "unknown_skill"
    assert body["name"] == "nope"


def test_tool_result_to_content_tool_name_none_or_other_stays_json():
    """Default tool_name=None and non-load_skill tools keep JSON path."""
    tr = ToolResult(
        ok=True,
        payload={
            "name": "plan-work",
            "description": "x",
            "source": "bundled",
            "body": "# plan-work body",
        },
    )
    # No tool_name → JSON (direct unit callers).
    none_raw = tool_result_to_content(tr, max_chars=8000)
    none_body = json.loads(none_raw)
    assert none_body["ok"] is True
    assert none_body["body"] == "# plan-work body"

    # Other tool names → JSON even if payload looks like a skill.
    other_raw = tool_result_to_content(tr, max_chars=8000, tool_name="list_goals")
    other_body = json.loads(other_raw)
    assert other_body["ok"] is True
    assert other_body["name"] == "plan-work"


# ---------------------------------------------------------------------------
# Stage 5 L4 — social first-hop speak pin predicate (hop==0 pre-call)
# ---------------------------------------------------------------------------


def test_social_first_hop_tool_choice_predicate_table() -> None:
    """Pin speak only when social_wake and hop==0 (before chat_completion).

    Explicit matrix — must not pin hop==1 (second hop after first return).
    """
    speak_pin = {"type": "function", "function": {"name": "speak"}}

    assert social_first_hop_tool_choice(social_wake=True, hop=0) == speak_pin
    # After first completion state.hop is 1 — must NOT pin second hop.
    assert social_first_hop_tool_choice(social_wake=True, hop=1) is None
    assert social_first_hop_tool_choice(social_wake=True, hop=2) is None
    # Non-social wakes never pin (including hop 0).
    assert social_first_hop_tool_choice(social_wake=False, hop=0) is None
    assert social_first_hop_tool_choice(social_wake=False, hop=1) is None
    # Never returns the high-risk "required" string default.
    for sw in (True, False):
        for h in (0, 1, 2, 5):
            tc = social_first_hop_tool_choice(social_wake=sw, hop=h)
            assert tc != "required"
            assert tc is None or (
                isinstance(tc, dict)
                and tc.get("type") == "function"
                and tc.get("function", {}).get("name") == "speak"
            )


def test_social_first_hop_pin_passed_only_on_hop0_social(
    ctx, registry, moments
) -> None:
    """Integration: run_do_loop passes speak pin on first social call only."""
    from elyra.llm.client import ToolCall as LlmToolCall

    captured: list[Any] = []

    class _CaptureChoice:
        def __init__(self) -> None:
            self._n = 0

        def chat_completion(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(kwargs.get("tool_choice"))
            self._n += 1
            if self._n == 1:
                # Simulate model obeying speak pin
                return ChatCompletionResult(
                    content="",
                    reasoning_content="",
                    raw_json="{}",
                    tool_calls=[
                        LlmToolCall(
                            id="c1",
                            name="speak",
                            arguments={"text": "Hello."},
                            arguments_raw='{"text":"Hello."}',
                            arguments_parse_ok=True,
                        )
                    ],
                    finish_reason="tool_calls",
                )
            return ChatCompletionResult(
                content="",
                reasoning_content="",
                raw_json="{}",
                tool_calls=[],
                finish_reason="stop",
            )

    result = run_do_loop(
        client=_CaptureChoice(),  # type: ignore[arg-type]
        registry=registry,
        ctx=ctx,
        outer_prefix=[{"role": "system", "content": "test"}],
        settings=_settings(max_tool_hops=4),
        moments=moments,
        social_wake=True,
    )
    assert result.spoke is True
    assert len(captured) >= 2
    assert captured[0] == {"type": "function", "function": {"name": "speak"}}
    # Second completion (hop was 1 pre-call) must omit pin
    assert captured[1] is None


def test_no_speak_pin_on_non_social_wake(ctx, registry, moments) -> None:
    """Non-social: tool_choice stays None even on hop 0."""
    captured: list[Any] = []

    class _CaptureChoice:
        def chat_completion(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(kwargs.get("tool_choice"))
            return ChatCompletionResult(
                content="plan only",
                reasoning_content="",
                raw_json="{}",
                tool_calls=[],
                finish_reason="stop",
            )

    result = run_do_loop(
        client=_CaptureChoice(),  # type: ignore[arg-type]
        registry=registry,
        ctx=ctx,
        outer_prefix=[{"role": "system", "content": "test"}],
        settings=_settings(max_tool_hops=2),
        moments=moments,
        social_wake=False,
    )
    assert result.stop_reason == "no_tools"
    assert captured == [None]


# ---------------------------------------------------------------------------
# Flood-safe reasoning_content re-feed (PR7) — assistant_message_from_result
# ---------------------------------------------------------------------------

_CHANNEL_FLOOD = "\n".join(["<|channel>thought"] * 20)


def test_assistant_message_omits_empty_reasoning_content() -> None:
    """Empty / whitespace RC → omit key entirely (no reinfection fuel)."""
    msg = assistant_message_from_result(
        ChatCompletionResult(content="hi", reasoning_content="", raw_json="{}")
    )
    assert "reasoning_content" not in msg
    msg_ws = assistant_message_from_result(
        ChatCompletionResult(content="hi", reasoning_content="   \n", raw_json="{}")
    )
    assert "reasoning_content" not in msg_ws


def test_assistant_message_omits_nonempty_pure_flood_rc() -> None:
    """Non-empty pure channel flood must never appear on the chain assistant row.

    Pure floods are long non-empty marker strings — bare truthiness would re-feed
    them. Defense in depth even without prior sanitize (Path B).
    """
    assert _CHANNEL_FLOOD  # non-empty
    msg = assistant_message_from_result(
        ChatCompletionResult(
            content="",
            reasoning_content=_CHANNEL_FLOOD,
            raw_json="{}",
            tool_calls=[],
        )
    )
    assert "reasoning_content" not in msg
    assert "<|channel>" not in json.dumps(msg, ensure_ascii=False)


def test_assistant_message_refeeds_cleaned_prose_after_sanitize() -> None:
    """Prose + flood after sanitize → cleaned prose only on chain re-feed (Path A)."""
    prose = "Plan: list files then greet."
    raw = ChatCompletionResult(
        content="hello\n" + _CHANNEL_FLOOD,
        reasoning_content=prose + "\n" + _CHANNEL_FLOOD,
        raw_json="{}",
        tool_calls=[],
    )
    cleaned, report = sanitize_completion(raw)
    assert report.reasoning_flood is True
    assert cleaned.reasoning_content == prose
    assert "<|channel>" not in cleaned.reasoning_content

    msg = assistant_message_from_result(cleaned)
    assert msg["reasoning_content"] == prose
    assert "<|channel>" not in msg["reasoning_content"]
    assert msg["content"] == "hello"


def test_assistant_message_include_reasoning_false_omits_even_good_rc() -> None:
    msg = assistant_message_from_result(
        ChatCompletionResult(
            content="x",
            reasoning_content="private plan",
            raw_json="{}",
        ),
        include_reasoning=False,
    )
    assert "reasoning_content" not in msg
    assert msg["content"] == "x"


def test_assistant_message_refeeds_clean_nonflood_rc() -> None:
    msg = assistant_message_from_result(
        ChatCompletionResult(
            content="ok",
            reasoning_content="step 1 then step 2",
            raw_json="{}",
        )
    )
    assert msg["reasoning_content"] == "step 1 then step 2"


# ---------------------------------------------------------------------------
# Ingress channel hygiene (PR6) — sanitize before beat/chain
# ---------------------------------------------------------------------------


class _CapturingStubClient:
    """Wrap StubChatClient; record messages seen on each hop (for chain asserts)."""

    def __init__(self, responses: list[Any]) -> None:
        self._inner = StubChatClient.scripted(responses)
        self.seen_messages: list[list[dict[str, Any]]] = []

    def chat_completion(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        self.seen_messages.append([dict(m) for m in messages])
        return self._inner.chat_completion(messages, **kwargs)


def test_ingress_sanitize_flooded_completion_cleaned_beat_and_chain(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore, caplog: pytest.LogCaptureFixture
) -> None:
    """Prose + channel flood RC is stripped before model beat and chain re-feed."""
    mid = moments.open_moment(why_now="hygiene flood", moment_id="mhygiene")
    ctx.moment_id = mid
    prose = "Plan: list files then greet."
    flooded_rc = prose + "\n" + _CHANNEL_FLOOD
    # Hop 1: tool call with flooded reasoning; hop 2: no tools stop.
    hop1 = {
        "content": "hello\n" + _CHANNEL_FLOOD,
        "reasoning_content": flooded_rc,
        "tool_calls": [
            {
                "id": "c_list",
                "name": "list_dir",
                "arguments": {"path": "."},
            }
        ],
        "finish_reason": "tool_calls",
    }
    client = _CapturingStubClient([hop1, _text("done")])
    with caplog.at_level("WARNING", logger="elyra.loop.doloop"):
        result = _run(client, ctx, registry, moments=moments)
    assert result.stop_reason == "no_tools"
    assert result.hop_count == 2

    model_beats = [b for b in moments.list_beats(mid) if b.get("type") == "model"]
    assert len(model_beats) >= 1
    beat0 = model_beats[0]
    assert beat0["content"] == "hello"
    assert beat0["reasoning"] == prose
    assert "<|channel>" not in beat0["content"]
    assert "<|channel>" not in beat0["reasoning"]
    assert beat0.get("finish_reason") == "tool_calls"
    hyg = beat0.get("hygiene") or {}
    assert hyg.get("c_markers", 0) >= 5
    assert hyg.get("r_markers", 0) >= 5
    assert hyg.get("flood") is True

    # Second completion must re-feed cleaned assistant row (no markers).
    assert len(client.seen_messages) >= 2
    chain_msgs = client.seen_messages[1]
    assistant_rows = [m for m in chain_msgs if m.get("role") == "assistant"]
    assert assistant_rows, "expected cleaned assistant on multi-hop chain"
    asst = assistant_rows[0]
    assert asst.get("content") == "hello" or asst.get("content") is None or "hello" in (
        asst.get("content") or ""
    )
    rc = asst.get("reasoning_content") or ""
    assert "<|channel>" not in rc
    assert "Plan: list files" in rc
    assert any("channel hygiene" in r.message for r in caplog.records)


def test_ingress_sanitize_pure_flood_empty_rc(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Pure channel flood → empty reasoning on beat; omit/empty on chain re-feed."""
    mid = moments.open_moment(why_now="pure flood", moment_id="mpureflood")
    ctx.moment_id = mid
    hop1 = {
        "content": "",
        "reasoning_content": _CHANNEL_FLOOD,
        "tool_calls": [
            {
                "id": "c_list",
                "name": "list_dir",
                "arguments": {"path": "."},
            }
        ],
        "finish_reason": "length",
    }
    client = _CapturingStubClient([hop1, _text("done")])
    result = _run(client, ctx, registry, moments=moments)
    assert result.stop_reason == "no_tools"

    model_beats = [b for b in moments.list_beats(mid) if b.get("type") == "model"]
    beat0 = model_beats[0]
    assert beat0["reasoning"] == ""
    assert beat0["content"] == ""
    assert beat0.get("finish_reason") == "length"
    hyg = beat0.get("hygiene") or {}
    assert hyg.get("r_markers", 0) >= 5
    assert hyg.get("flood") is True

    # Chain assistant must not re-feed pure flood RC (empty → key omitted or "").
    chain_msgs = client.seen_messages[1]
    assistant_rows = [m for m in chain_msgs if m.get("role") == "assistant"]
    assert assistant_rows
    rc = assistant_rows[0].get("reasoning_content")
    assert not rc  # None / missing / ""
    assert "<|channel>" not in json.dumps(assistant_rows[0], ensure_ascii=False)


def test_ingress_sanitize_finish_reason_on_no_tools_beat(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """finish_reason from ChatCompletionResult is recorded on model beats."""
    mid = moments.open_moment(why_now="finish reason", moment_id="mfinish")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            ChatCompletionResult(
                content="orphan thoughts",
                reasoning_content="private",
                raw_json="{}",
                tool_calls=[],
                finish_reason="stop",
            )
        ]
    )
    result = _run(client, ctx, registry, moments=moments)
    assert result.stop_reason == "no_tools"
    beat = next(b for b in moments.list_beats(mid) if b.get("type") == "model")
    assert beat.get("finish_reason") == "stop"
    assert beat["content"] == "orphan thoughts"
    assert beat["reasoning"] == "private"
    assert "hygiene" not in beat  # no markers → no hygiene field


# ---------------------------------------------------------------------------
# 1. 2-hop list_dir + speak
# ---------------------------------------------------------------------------


def test_two_hop_list_dir_then_speak(
    ctx: ToolContext, registry: ToolRegistry, paths, moments: MomentStore
) -> None:
    mid = moments.open_moment(why_now="test two-hop", user_id="operator", moment_id="m2hop")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c_list"),
            _tc("speak", {"text": "Hi — sandbox has notes.txt"}, call_id="c_speak"),
            _text("done"),  # final no-tools stop (stub would otherwise hold last tool turn)
        ]
    )
    result = _run(client, ctx, registry, moments=moments, social_wake=True)
    assert result.stop_reason == "no_tools"
    # 2 tool hops + 1 no_tools hop
    assert result.hop_count == 3
    assert result.spoke is True
    glass = list_messages(paths=paths)
    assistant = [m for m in glass if m.get("role") == "assistant"]
    assert any("notes.txt" in (m.get("content") or "") or "Hi" in (m.get("content") or "") for m in assistant)
    tape = moments.list_beats(mid)
    types = [b["type"] for b in tape]
    assert types.count("model") == 3
    assert "tool" in types
    assert types[-1] == "stop"


# ---------------------------------------------------------------------------
# 2. invalid JSON args continue
# ---------------------------------------------------------------------------


def test_invalid_json_args_continue_loop(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    mid = moments.open_moment(why_now="bad json", moment_id="mbadjson")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("list_dir", "{not valid json", call_id="c_bad", parse_ok=False),
            _tc("speak", {"text": "recovered after bad args"}, call_id="c_ok"),
            _text("done"),
        ]
    )
    result = _run(client, ctx, registry, moments=moments)
    assert result.stop_reason == "no_tools"
    assert result.hop_count == 3
    assert result.spoke is True
    beats = moments.list_beats(mid)
    tool_beats = [b for b in beats if b.get("type") == "tool"]
    assert tool_beats[0]["ok"] is False
    assert tool_beats[0]["error_reason"] == "invalid_json_arguments"


# ---------------------------------------------------------------------------
# 3. tool ok=false continue
# ---------------------------------------------------------------------------


def test_tool_ok_false_continues(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    mid = moments.open_moment(why_now="unknown tool", moment_id="mfalse")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("not_a_real_tool_xyz", {"x": 1}, call_id="c_unk"),
            _tc("speak", {"text": "after failure"}, call_id="c_sp"),
            _text("done"),
        ]
    )
    result = _run(client, ctx, registry, moments=moments)
    assert result.stop_reason == "no_tools"
    assert result.hop_count == 3
    assert result.spoke is True
    tool_beats = [b for b in moments.list_beats(mid) if b.get("type") == "tool"]
    assert tool_beats[0]["ok"] is False
    assert tool_beats[0]["error_reason"] in ("unknown_tool", "invalid_name")


# ---------------------------------------------------------------------------
# 4. wait ends_moment; remaining batch tools skipped
# ---------------------------------------------------------------------------


def test_ends_moment_skips_remaining_batch_tools(
    ctx: ToolContext, registry: ToolRegistry, paths, moments: MomentStore
) -> None:
    mid = moments.open_moment(why_now="wait batch", moment_id="mwait")
    ctx.moment_id = mid
    # wait_user first → ends_moment; speak must NOT run (no glass).
    client = StubChatClient.scripted(
        [
            _batch(
                {
                    "id": "c_wait",
                    "name": "wait_user",
                    "arguments": {
                        "prompt": "Ready?",
                        "choices": ["yes", "no"],
                    },
                },
                {
                    "id": "c_speak_skip",
                    "name": "speak",
                    "arguments": {"text": "SHOULD_NOT_APPEAR_ON_GLASS"},
                },
            ),
            _text("should not be reached"),
        ]
    )
    result = _run(client, ctx, registry, moments=moments)
    assert result.stop_reason == "wait"
    assert result.hop_count == 1
    assert result.arm_wait is not None
    assert result.arm_wait.prompt == "Ready?"
    glass = list_messages(paths=paths)
    assert not any(
        "SHOULD_NOT_APPEAR_ON_GLASS" in (m.get("content") or "") for m in glass
    )
    tool_beats = [b for b in moments.list_beats(mid) if b.get("type") == "tool"]
    names = [b.get("name") for b in tool_beats]
    assert names == ["wait_user"]
    assert all(b.get("name") != "speak" for b in tool_beats)


# ---------------------------------------------------------------------------
# 5. no-speak via counts_as_speak + social nudge once
# ---------------------------------------------------------------------------


def test_social_no_speak_nudge_once_then_no_tools(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    mid = moments.open_moment(why_now="social silent", moment_id="mnudge")
    ctx.moment_id = mid
    # Two orphan content turns — nudge injects once between them.
    client = StubChatClient.scripted(
        [
            _text("thinking without tools"),
            _text("still no speak"),
            _text("should not run"),
        ]
    )
    result = _run(client, ctx, registry, moments=moments, social_wake=True)
    assert result.stop_reason == "no_tools"
    assert result.spoke is False
    assert result.hop_count == 2
    beats = moments.list_beats(mid)
    nudges = [
        b
        for b in beats
        if b.get("type") == "obs" and b.get("kind") == "no_speak_nudge"
    ]
    assert len(nudges) == 1
    assert NO_SPEAK_NUDGE in (nudges[0].get("content") or "")


def test_no_nudge_on_non_social_wake(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    mid = moments.open_moment(why_now="timer work", moment_id="mnosocial")
    ctx.moment_id = mid
    client = StubChatClient.scripted([_text("silent work ok")])
    result = _run(client, ctx, registry, moments=moments, social_wake=False)
    assert result.stop_reason == "no_tools"
    assert result.hop_count == 1
    obs = [b for b in moments.list_beats(mid) if b.get("type") == "obs"]
    assert not any(b.get("kind") == "no_speak_nudge" for b in obs)


def test_speak_counts_as_speak_skips_nudge(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    mid = moments.open_moment(why_now="spoke", moment_id="mspoke")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("speak", {"text": "hello operator"}, call_id="c1"),
            _text("done"),
        ]
    )
    result = _run(client, ctx, registry, moments=moments, social_wake=True)
    assert result.stop_reason == "no_tools"
    assert result.spoke is True
    assert result.hop_count == 2  # speak hop + final no_tools hop
    obs = [b for b in moments.list_beats(mid) if b.get("kind") == "no_speak_nudge"]
    assert obs == []


# ---------------------------------------------------------------------------
# 5b. Continuous in-moment work-continue HOST (PR5)
# ---------------------------------------------------------------------------


def test_speak_only_tools_ran_false_spoke_true(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore, paths
) -> None:
    """K15: speak alone → spoke=True, tools_ran=False (non-speak progress only)."""
    mid = moments.open_moment(why_now="speak only", moment_id="mspeakonly")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("speak", {"text": "hi"}, call_id="c1"),
            _text("done"),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=True,
        settings=_settings_continuous(enabled=True),
    )
    assert result.spoke is True
    assert result.tools_ran is False
    assert result.ledger_mutated is False
    assert result.work_continue_injects == 0
    assert result.model_beats >= 2
    # HOST work-continue must never hit SpeakTransport / glass.
    glass = list_messages(paths=paths)
    assert not any(
        WORK_CONTINUE_HOST in (m.get("content") or "") for m in glass
    )


def test_list_dir_tools_ran_true(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Successful non-speak tool (list_dir) sets tools_ran; speak stays False."""
    mid = moments.open_moment(why_now="list work", moment_id="mlistdir")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _text("done"),
        ]
    )
    # Continuous OFF so free-text stops immediately (no work-continue inject hop).
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=False,
        settings=default_settings(),
    )
    assert result.tools_ran is True
    assert result.spoke is False
    assert result.work_continue_injects == 0
    assert result.stop_reason == "no_tools"
    assert result.hop_count == 2


def test_list_dir_then_work_continue_once(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore, paths
) -> None:
    """Continuous ON + tools_ran → one work_continue HOST; second free-text stops."""
    mid = moments.open_moment(why_now="work continue", moment_id="mworkc")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _text("premature exit"),
            _text("still free text"),
            _text("should not run"),
        ]
    )
    result = run_do_loop(
        client=client,
        registry=registry,
        ctx=ctx,
        outer_prefix=_outer(),
        settings=_settings_continuous(enabled=True),
        moments=moments,
        social_wake=False,
        wake_kind="task_ready",
        continuous_enabled=True,
    )
    assert result.tools_ran is True
    assert result.work_continue_injects == 1
    assert result.stop_reason == "no_tools"
    assert result.hop_count == 3  # list_dir + free + free after nudge
    beats = moments.list_beats(mid)
    work_obs = [
        b
        for b in beats
        if b.get("type") == "obs" and b.get("kind") == "work_continue"
    ]
    assert len(work_obs) == 1
    content = work_obs[0].get("content") or ""
    assert content == work_continue_host_message()
    assert content.startswith("HOST:")
    assert _is_host_inject({"role": "user", "content": content})
    # Distinct from time-idle continue and no_speak_nudge
    assert work_obs[0].get("kind") == "work_continue"
    assert not any(b.get("kind") == "no_speak_nudge" for b in beats)
    glass = list_messages(paths=paths)
    assert not any(WORK_CONTINUE_HOST in (m.get("content") or "") for m in glass)


def test_flood_free_text_no_work_continue_hard_stop(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Flood free-text hop: no work-continue inject; last_stop_hop_was_flood True."""
    mid = moments.open_moment(why_now="flood stop", moment_id="mfloodwc")
    ctx.moment_id = mid
    flood = "\n".join(["<|channel>thought"] * 20)
    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            {
                "content": flood,
                "reasoning_content": flood,
                "tool_calls": [],
                "finish_reason": "stop",
            },
        ]
    )
    result = run_do_loop(
        client=client,
        registry=registry,
        ctx=ctx,
        outer_prefix=_outer(),
        settings=_settings_continuous(enabled=True),
        moments=moments,
        social_wake=False,
        wake_kind="timer",
        continuous_enabled=True,
    )
    assert result.tools_ran is True
    assert result.work_continue_injects == 0
    assert result.stop_reason == "no_tools"
    assert result.last_stop_hop_was_flood is True
    assert result.channel_flood_beats >= 1
    assert result.model_beats >= 2
    obs = [
        b
        for b in moments.list_beats(mid)
        if b.get("type") == "obs" and b.get("kind") == "work_continue"
    ]
    assert obs == []


def test_social_work_context_ignores_open_goals_alone(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Social hello with leftover open goals only → no work-continue HOST.

    work_context for social is tools_ran|ledger_mutated only — not open goals.
    Social no-speak may still fire when not spoke.
    """
    mid = moments.open_moment(why_now="hello leftover goals", moment_id="msocialgoals")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _text("thinking hi without tools"),
            _text("still silent after nudge"),
            _text("should not run"),
        ]
    )
    result = run_do_loop(
        client=client,
        registry=registry,
        ctx=ctx,
        outer_prefix=_outer(),
        settings=_settings_continuous(enabled=True),
        moments=moments,
        social_wake=True,
        wake_kind="user_message",
        has_open_goals_slice=True,
        continuous_enabled=True,
    )
    assert result.spoke is False
    assert result.tools_ran is False
    assert result.ledger_mutated is False
    assert result.work_continue_injects == 0
    assert result.stop_reason == "no_tools"
    beats = moments.list_beats(mid)
    assert any(b.get("kind") == "no_speak_nudge" for b in beats)
    assert not any(b.get("kind") == "work_continue" for b in beats)


def test_social_no_speak_wins_before_work_continue(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """K8 order: list_dir → free-text → no_speak → speak → free-text → work_continue → stop.

    Sequence:
    1. list_dir (tools_ran, not spoke)
    2. free-text → no_speak_nudge (social, not spoke)
    3. speak tool (spoke=True)
    4. free-text with work_context → work_continue once
    5. free-text again → no_tools stop
    """
    mid = moments.open_moment(why_now="social first", moment_id="msocfirst")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _text("no speak yet"),
            _tc("speak", {"text": "hello"}, call_id="c2"),
            _text("after speak free"),
            _text("after work nudge free"),
            _text("should not run"),
        ]
    )
    result = run_do_loop(
        client=client,
        registry=registry,
        ctx=ctx,
        outer_prefix=_outer(),
        settings=_settings_continuous(enabled=True),
        moments=moments,
        social_wake=True,
        wake_kind="user_message",
        continuous_enabled=True,
    )
    assert result.tools_ran is True
    assert result.spoke is True
    assert result.work_continue_injects == 1
    beats = moments.list_beats(mid)
    kinds = [b.get("kind") for b in beats if b.get("type") == "obs"]
    assert "no_speak_nudge" in kinds
    assert "work_continue" in kinds
    assert kinds.index("no_speak_nudge") < kinds.index("work_continue")


def test_social_no_work_continue_without_spoke_after_no_speak(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Strict K8: list_dir → free-text → no_speak → free-text (still no speak) → stop.

    tools_ran alone must not unlock work-continue on social without spoke.
    """
    mid = moments.open_moment(why_now="social need spoke", moment_id="msocneedspoke")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _text("silent free text"),
            _text("still no speak after nudge"),
            _text("should not run"),
        ]
    )
    result = run_do_loop(
        client=client,
        registry=registry,
        ctx=ctx,
        outer_prefix=_outer(),
        settings=_settings_continuous(enabled=True),
        moments=moments,
        social_wake=True,
        wake_kind="user_message",
        continuous_enabled=True,
    )
    assert result.tools_ran is True
    assert result.spoke is False
    assert result.work_continue_injects == 0
    assert result.stop_reason == "no_tools"
    beats = moments.list_beats(mid)
    assert any(b.get("kind") == "no_speak_nudge" for b in beats)
    assert not any(b.get("kind") == "work_continue" for b in beats)


def test_failed_non_speak_tool_tools_ran_false(
    ctx: ToolContext, registry: ToolRegistry
) -> None:
    """v1 K15: failed non-speak tool (ok=False) does not set tools_ran."""
    class _FailListDir:
        def openai_tools(self) -> list[dict[str, Any]]:
            return registry.openai_tools()

        def execute(
            self, name: str, args: dict[str, Any] | None, c: ToolContext
        ) -> ToolResult:
            if name == "list_dir":
                return ToolResult(
                    ok=False, payload={}, error_reason="sandbox_denied"
                )
            return registry.execute(name, args, c)

    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _text("done"),
        ]
    )
    result = run_do_loop(
        client=client,
        registry=_FailListDir(),  # type: ignore[arg-type]
        ctx=ctx,
        outer_prefix=_outer(),
        settings=default_settings(),
    )
    assert result.tools_ran is False
    assert result.spoke is False
    assert result.stop_reason == "no_tools"


def test_ledger_mutated_alone_work_continue_non_social(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """ledger_mutated alone (no ok non-speak tool) → work_continue on non-social."""
    mid = moments.open_moment(why_now="ledger only", moment_id="mledgeronly")
    ctx.moment_id = mid

    class _LedgerOnly:
        def openai_tools(self) -> list[dict[str, Any]]:
            return registry.openai_tools()

        def execute(
            self, name: str, args: dict[str, Any] | None, c: ToolContext
        ) -> ToolResult:
            if name == "list_dir":
                # Fail tool so tools_ran stays False, but mutate ledger.
                assert c.mark_task_changed is not None
                c.mark_task_changed()
                return ToolResult(ok=False, payload={}, error_reason="simulated")
            return registry.execute(name, args, c)

    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _text("exit early"),
            _text("after work continue"),
            _text("should not run"),
        ]
    )
    result = run_do_loop(
        client=client,
        registry=_LedgerOnly(),  # type: ignore[arg-type]
        ctx=ctx,
        outer_prefix=_outer(),
        settings=_settings_continuous(enabled=True),
        moments=moments,
        social_wake=False,
        wake_kind="timer",
        continuous_enabled=True,
    )
    assert result.ledger_mutated is True
    assert result.tools_ran is False
    assert result.work_continue_injects == 1
    assert result.stop_reason == "no_tools"


def test_nonsocial_workish_wake_kind_without_tools_work_continue(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Non-social task_ready wake with no tools → work_context from wake_kind → inject once."""
    mid = moments.open_moment(why_now="task ready free", moment_id="mtaskreadywc")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _text("premature free text"),
            _text("after work continue"),
            _text("should not run"),
        ]
    )
    result = run_do_loop(
        client=client,
        registry=registry,
        ctx=ctx,
        outer_prefix=_outer(),
        settings=_settings_continuous(enabled=True),
        moments=moments,
        social_wake=False,
        wake_kind="task_ready",
        has_open_goals_slice=False,
        continuous_enabled=True,
    )
    assert result.tools_ran is False
    assert result.ledger_mutated is False
    assert result.work_continue_injects == 1
    assert result.stop_reason == "no_tools"
    assert any(
        b.get("kind") == "work_continue" for b in moments.list_beats(mid)
    )


def test_mark_task_changed_sets_ledger_mutated(
    ctx: ToolContext, registry: ToolRegistry
) -> None:
    """_install_activity_hooks: mark_task_changed → ledger_mutated=True."""
    host_hits = {"n": 0}

    def host_task() -> None:
        host_hits["n"] += 1

    ctx.mark_task_changed = host_task

    class _LedgerReg:
        def openai_tools(self) -> list[dict[str, Any]]:
            return registry.openai_tools()

        def execute(
            self, name: str, args: dict[str, Any] | None, c: ToolContext
        ) -> ToolResult:
            if name == "list_dir":
                assert c.mark_task_changed is not None
                c.mark_task_changed()
                return ToolResult(ok=True, payload={"entries": []})
            return registry.execute(name, args, c)

    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _text("done"),
        ]
    )
    result = run_do_loop(
        client=client,
        registry=_LedgerReg(),  # type: ignore[arg-type]
        ctx=ctx,
        outer_prefix=_outer(),
        settings=_settings_continuous(enabled=False),
    )
    assert result.ledger_mutated is True
    assert result.tools_ran is True
    assert host_hits["n"] >= 1
    assert ctx.mark_task_changed is host_task


def test_work_continue_disabled_when_continuous_off(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Continuous OFF (default): no work-continue even with tools_ran + free-text."""
    mid = moments.open_moment(why_now="off", moment_id="mcontoff")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _text("exit"),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=False,
        wake_kind="timer",
        settings=default_settings(),
    )
    assert result.tools_ran is True
    assert result.work_continue_injects == 0
    assert result.stop_reason == "no_tools"


# ---------------------------------------------------------------------------
# 6. max_hops
# ---------------------------------------------------------------------------


def test_max_hops_backstop(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    mid = moments.open_moment(why_now="thrash", moment_id="mhops")
    ctx.moment_id = mid
    # Always list_dir — never ends moment.
    client = StubChatClient.scripted(
        [_tc("list_dir", {"path": "."}, call_id=f"c{i}") for i in range(10)]
    )
    settings = _settings(max_tool_hops=3)
    result = _run(client, ctx, registry, settings=settings, moments=moments)
    assert result.stop_reason == "max_hops"
    assert result.hop_count == 3


# ---------------------------------------------------------------------------
# 7–8. chain over budget drops oldest pairs; re-outer when still over
# ---------------------------------------------------------------------------


def test_enforce_in_turn_budget_drops_oldest_pairs() -> None:
    outer = [{"role": "system", "content": "sys"}]
    # Three large tool batches; budget allows roughly one.
    chain: list[dict[str, Any]] = []
    for i in range(3):
        chain.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "list_dir", "arguments": "{}"},
                    }
                ],
            }
        )
        # ~2000 tokens each if estimate is len//4
        chain.append(
            {
                "role": "tool",
                "tool_call_id": f"c{i}",
                "content": "x" * 8000,
            }
        )
    # budget ~ 2500 tokens → must drop oldest batches
    new_outer, new_chain, reouter = enforce_in_turn_budget(
        outer,
        chain,
        budget_tokens=2500,
        tool_result_max_chars=8000,
        rebuild_outer=None,
    )
    assert reouter is False
    batches = sum(1 for m in new_chain if m.get("role") == "assistant")
    assert batches < 3
    assert batches >= 1
    # Newest batch (c2) should remain
    tool_ids = [m.get("tool_call_id") for m in new_chain if m.get("role") == "tool"]
    assert "c2" in tool_ids


def test_enforce_in_turn_budget_reouter_when_still_over() -> None:
    outer = [{"role": "system", "content": "sys"}]
    chain: list[dict[str, Any]] = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "only",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "only",
            "content": "y" * 20_000,
        },
    ]
    rebuilds = {"n": 0}

    def rebuild() -> list[dict[str, Any]]:
        rebuilds["n"] += 1
        return [{"role": "system", "content": "rebuilt-outer"}]

    new_outer, new_chain, reouter = enforce_in_turn_budget(
        outer,
        chain,
        budget_tokens=100,  # tiny — single batch still over after truncate
        tool_result_max_chars=500,
        rebuild_outer=rebuild,
    )
    assert reouter is True
    assert rebuilds["n"] == 1
    assert new_outer[0]["content"] == "rebuilt-outer"
    # Tool content truncated
    tool_msgs = [m for m in new_chain if m.get("role") == "tool"]
    assert tool_msgs
    assert len(tool_msgs[0]["content"]) <= 500
    assert tool_msgs[0]["content"].endswith("…[truncated]")


class _FatPayloadRegistry:
    """Registry double: huge tool payloads to force in-turn re-outer."""

    def __init__(self, inner: ToolRegistry, blob_chars: int = 20_000) -> None:
        self._inner = inner
        self._blob = "Z" * blob_chars
        self.names_executed: list[str] = []

    def openai_tools(self) -> list[dict[str, Any]]:
        return self._inner.openai_tools()

    def execute(self, name: str, args: dict[str, Any] | None, ctx: ToolContext) -> ToolResult:
        self.names_executed.append(name)
        # Still exercise real tools for speak/wait; fat payload for list_dir.
        if name == "list_dir":
            return ToolResult(ok=True, payload={"entries": ["notes.txt"], "blob": self._blob})
        return self._inner.execute(name, args, ctx)


def test_run_do_loop_reouter_under_pressure(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """End-to-end: oversized tool payload forces mid-loop re-outer."""
    mid = moments.open_moment(why_now="budget", moment_id="mbudget")
    ctx.moment_id = mid
    rebuilds = {"n": 0}
    outers_seen: list[str] = []

    def rebuild() -> list[dict[str, Any]]:
        rebuilds["n"] += 1
        label = f"outer-{rebuilds['n']}"
        outers_seen.append(label)
        return [
            {"role": "system", "content": label},
            {"role": "user", "content": "work"},
        ]

    fat = _FatPayloadRegistry(registry, blob_chars=30_000)
    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _tc("list_dir", {"path": "."}, call_id="c2"),
            _text("done"),
        ]
    )
    # Tiny budget + large (post-truncate still big) payloads force re-outer
    # on hop 2 before the second model call (sole batch cannot be dropped).
    settings = _settings(
        max_tool_hops=10,
        in_turn_max_tokens=80,
        sliding_input_tokens=80,
        tool_result_max_chars=2000,
    )
    result = run_do_loop(
        client=client,
        registry=fat,  # type: ignore[arg-type]
        ctx=ctx,
        rebuild_outer=rebuild,
        settings=settings,
        moments=moments,
    )
    assert result.hop_count >= 2
    assert result.stop_reason == "no_tools"
    # Initial rebuild (no outer_prefix) + at least one mid-loop re-outer.
    assert result.reouter_count >= 1, result
    assert rebuilds["n"] >= 2, rebuilds
    assert "outer-2" in outers_seen or outers_seen[-1] != "outer-1"


def test_reouter_count_zero_without_caller_rebuild(
    ctx: ToolContext, registry: ToolRegistry
) -> None:
    """Compress-only pressure without rebuild_outer must not inflate reouter_count."""
    fat = _FatPayloadRegistry(registry, blob_chars=30_000)
    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _tc("list_dir", {"path": "."}, call_id="c2"),
            _text("done"),
        ]
    )
    settings = _settings(
        max_tool_hops=10,
        in_turn_max_tokens=80,
        sliding_input_tokens=80,
        tool_result_max_chars=2000,
    )
    result = run_do_loop(
        client=client,
        registry=fat,  # type: ignore[arg-type]
        ctx=ctx,
        outer_prefix=_outer(),
        settings=settings,
    )
    assert result.reouter_count == 0
    assert result.stop_reason == "no_tools"


# ---------------------------------------------------------------------------
# Disk prompts via assemble_outer_meal
# ---------------------------------------------------------------------------


def test_doloop_rebuild_outer_uses_disk_prompts(
    ctx: ToolContext, registry: ToolRegistry, paths, moments: MomentStore
) -> None:
    """rebuild_outer → assemble_outer_meal loads system/orient from disk prompts/."""
    mid = moments.open_moment(why_now="disk prompts", user_id="operator", moment_id="mdisk")
    ctx.moment_id = mid
    system_text = load_prompt("system", paths=paths)
    assert system_text.strip(), "expected prompts/system.md content"

    def rebuild() -> list[dict[str, Any]]:
        return assemble_outer_meal(
            paths=paths,
            glass_history=[],
            wake_content="Please list files and greet me",
            why_now="user_message:disk",
            settings=default_settings(),
        )

    meal = rebuild()
    assert meal[0]["role"] == "system"
    assert meal[0]["content"] == system_text
    assert meal[-1]["role"] == "user"
    orient = meal[-1]["content"]
    assert "disk" in orient.lower() or "user_message" in orient or "WHY" in orient or orient
    # Orient placeholders should be filled (no bare {{NOW}}).
    assert "{{NOW}}" not in orient
    assert "{{WHY_NOW}}" not in orient

    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _tc("speak", {"text": "hi from disk meal"}, call_id="c2"),
            _text("done"),
        ]
    )
    result = run_do_loop(
        client=client,
        registry=registry,
        ctx=ctx,
        rebuild_outer=rebuild,
        settings=default_settings(),
        moments=moments,
        social_wake=True,
    )
    assert result.stop_reason == "no_tools"
    assert result.spoke is True
    assert result.hop_count == 3


# ---------------------------------------------------------------------------
# Wire mark_spoke / mark_task_changed / ToolContext reuse (Issues 1–2, 5)
# ---------------------------------------------------------------------------


def test_mark_spoke_hook_called(
    ctx: ToolContext, registry: ToolRegistry
) -> None:
    flags = {"spoke": 0}

    def on_spoke() -> None:
        flags["spoke"] += 1

    ctx.mark_spoke = on_spoke
    client = StubChatClient.scripted(
        [
            _tc("speak", {"text": "hooked"}, call_id="c1"),
            _text("done"),
        ]
    )
    result = _run(client, ctx, registry)
    assert result.spoke is True
    assert flags["spoke"] >= 1
    # Host hook restored after loop (no nested wrapper left on ctx).
    assert ctx.mark_spoke is on_spoke


def test_host_mark_task_changed_updates_continue_idle(
    ctx: ToolContext, registry: ToolRegistry
) -> None:
    """Host-provided mark_task_changed must still stamp loop last_activity.

    Without always-wrap, a long tool that only calls the host hook leaves
    last_activity at moment start → spurious continue inject.
    """
    clock = {"t": datetime(2026, 1, 1, 12, 0, tzinfo=UTC)}

    def now() -> datetime:
        return clock["t"]

    host_hits = {"n": 0}

    def host_task_changed() -> None:
        host_hits["n"] += 1

    ctx.mark_task_changed = host_task_changed

    class _TaskProgressRegistry:
        def openai_tools(self) -> list[dict[str, Any]]:
            return registry.openai_tools()

        def execute(
            self, name: str, args: dict[str, Any] | None, c: ToolContext
        ) -> ToolResult:
            if name == "list_dir":
                # Simulate long work then task progress via host hook.
                clock["t"] = clock["t"] + timedelta(minutes=10)
                assert c.mark_task_changed is not None
                c.mark_task_changed()
                return ToolResult(ok=True, payload={"entries": ["notes.txt"]})
            return registry.execute(name, args, c)

    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _text("done"),
        ]
    )
    settings = _settings(continue_idle_minutes=5, continue_max_injects=3, max_tool_hops=10)
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    result = run_do_loop(
        client=client,
        registry=_TaskProgressRegistry(),  # type: ignore[arg-type]
        ctx=ctx,
        outer_prefix=_outer(),
        settings=settings,
        clock=now,
        started_at=t0,
    )
    assert host_hits["n"] >= 1
    assert result.continue_injects == 0, (
        "task change via host mark_task_changed should refresh idle clock; "
        f"got continue_injects={result.continue_injects}"
    )
    assert result.stop_reason == "no_tools"
    # Host hook restored.
    assert ctx.mark_task_changed is host_task_changed


def test_ctx_reuse_across_moments_rewires_fresh_hooks(
    ctx: ToolContext, registry: ToolRegistry
) -> None:
    """Second run_do_loop on same ctx must not keep moment-1 closures."""
    host_hits = {"n": 0}

    def host_task() -> None:
        host_hits["n"] += 1

    ctx.mark_task_changed = host_task

    class _MarkTaskReg:
        def openai_tools(self) -> list[dict[str, Any]]:
            return registry.openai_tools()

        def execute(
            self, name: str, args: dict[str, Any] | None, c: ToolContext
        ) -> ToolResult:
            if c.mark_task_changed is not None:
                c.mark_task_changed()
            return ToolResult(ok=True, payload={"ok": True, "name": name})

    # Moment 1
    r1 = run_do_loop(
        client=StubChatClient.scripted(
            [_tc("list_dir", {"path": "."}, call_id="a1"), _text("d1")]
        ),
        registry=_MarkTaskReg(),  # type: ignore[arg-type]
        ctx=ctx,
        outer_prefix=_outer(),
        settings=_settings(max_tool_hops=5),
    )
    assert r1.stop_reason == "no_tools"
    assert ctx.mark_task_changed is host_task
    hits_after_m1 = host_hits["n"]
    assert hits_after_m1 >= 1

    # Moment 2 — same ctx; host hook must still fire (fresh wrap each entry).
    r2 = run_do_loop(
        client=StubChatClient.scripted(
            [_tc("list_dir", {"path": "."}, call_id="b1"), _text("d2")]
        ),
        registry=_MarkTaskReg(),  # type: ignore[arg-type]
        ctx=ctx,
        outer_prefix=_outer(),
        settings=_settings(max_tool_hops=5),
    )
    assert r2.stop_reason == "no_tools"
    assert host_hits["n"] > hits_after_m1
    assert ctx.mark_task_changed is host_task


def test_host_mark_spoke_exception_does_not_abort_loop(
    ctx: ToolContext, registry: ToolRegistry
) -> None:
    """Host mark_spoke raising must not surface stop_reason=error (Issue 5)."""

    def boom() -> None:
        raise RuntimeError("host spoke hook exploded")

    ctx.mark_spoke = boom
    client = StubChatClient.scripted(
        [
            _tc("speak", {"text": "still delivered"}, call_id="c1"),
            _text("done"),
        ]
    )
    result = _run(client, ctx, registry)
    assert result.stop_reason == "no_tools"
    assert result.spoke is True
    assert result.error is None
    assert ctx.mark_spoke is boom


# ---------------------------------------------------------------------------
# Real model (@pytest.mark.llm)
# ---------------------------------------------------------------------------


def _model_available() -> bool:
    return not validate_model_paths(resolve_paths())


def _server_healthy(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def live_llama_server():
    """Reuse :8080 or start llama-server; skip when model/ missing."""
    if not _model_available():
        problems = validate_model_paths(resolve_paths())
        pytest.skip("model not available: " + "; ".join(problems))
    paths = resolve_paths()
    default_config = LlamaServerConfig()
    owned_proc: subprocess.Popen[bytes] | None = None
    port = default_config.port

    if _server_healthy(default_config.health_url):
        yield LlamaServerConfig(host="127.0.0.1", port=port)
        return

    port = _free_port()
    config = LlamaServerConfig(host="127.0.0.1", port=port)
    cmd = build_server_command(
        paths,
        config,
        context_tokens=8192,
        batch_size=512,
        ubatch_size=512,
    )
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
def test_real_model_tool_call_through_doloop(
    live_llama_server, tmp_path: Path
) -> None:
    """Real completions: model emits tool_calls; do-loop executes list_dir and/or speak.

    Pins tool_choice to list_dir for the first hop reliability (Gemma peg quirks),
    then allows free choice / no tools on subsequent hops.
    """
    home = tmp_path
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    sandbox = Sandbox(paths)
    (sandbox.root / "notes.txt").write_text("real-model note\n", encoding="utf-8")
    registry = ToolRegistry(paths, bundled_root=resolve_bundled_tools_root())
    speak = SpeakTransport(paths)
    timers = TimerService(paths, WakeQueue(paths))
    moments = MomentStore(paths)
    mid = moments.open_moment(why_now="llm multi-hop", user_id="operator")
    ctx = ToolContext(
        paths=paths,
        sandbox=sandbox,
        settings=default_settings(),
        moment_id=mid,
        user_id="operator",
        registry=registry,
        speak=speak,
        timers=timers,
        skills_used=[],
    )

    http = HttpChatClient(live_llama_server)
    # Narrow tool surface for the live model (list_dir + speak only).
    tools = [
        t
        for t in registry.openai_tools()
        if t.get("function", {}).get("name") in ("list_dir", "speak")
    ]
    assert len(tools) == 2

    hop_n = {"n": 0}

    class _FirstHopPinned:
        """Proxy: first completion forces list_dir; later hops free / no pin."""

        def chat_completion(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            hop_n["n"] += 1
            kw = dict(kwargs)
            kw["tools"] = tools
            if hop_n["n"] == 1:
                kw["tool_choice"] = {
                    "type": "function",
                    "function": {"name": "list_dir"},
                }
            else:
                # After tools returned, prefer speak if still going.
                if hop_n["n"] == 2:
                    kw["tool_choice"] = {
                        "type": "function",
                        "function": {"name": "speak"},
                    }
                else:
                    kw.pop("tool_choice", None)
            kw.setdefault("temperature", 0.1)
            kw.setdefault("reasoning", False)
            kw.setdefault("max_tokens", 256)
            return http.chat_completion(messages, **kw)

    def rebuild() -> list[dict[str, Any]]:
        return assemble_outer_meal(
            paths=paths,
            glass_history=[],
            wake_content="List the sandbox directory, then greet me via speak.",
            why_now="llm multi-hop",
            settings=default_settings(),
        )

    meal = rebuild()
    assert meal[0]["content"] == load_prompt("system", paths=paths)

    settings = _settings(max_tool_hops=6, generation_max_tokens=256)
    result = run_do_loop(
        client=_FirstHopPinned(),  # type: ignore[arg-type]
        registry=registry,
        ctx=ctx,
        rebuild_outer=rebuild,
        settings=settings,
        moments=moments,
        social_wake=True,
        tools=tools,
        max_tokens=256,
    )

    assert result.hop_count >= 1
    assert result.stop_reason in (
        "no_tools",
        "wait",
        "max_hops",
        "wall_clock",
        "time_continue_declined",
    )
    beats = moments.list_beats(mid)
    tool_beats = [b for b in beats if b.get("type") == "tool"]
    assert tool_beats, (
        f"expected at least one tool beat from real model; "
        f"stop={result.stop_reason} hops={result.hop_count} beats={beats!r}"
    )
    names = [b.get("name") for b in tool_beats]
    assert "list_dir" in names, f"expected list_dir executed; got {names}"
    # Prefer speak success when model followed path; not hard-required if model
    # stopped after list_dir with nudge, but hop should have progressed.
    if result.spoke:
        glass = list_messages(paths=paths)
        assert any(m.get("role") == "assistant" for m in glass)
