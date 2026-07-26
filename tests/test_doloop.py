"""Multi-hop do-loop tests (PR11).

Scripted StubChatClient covers contracts; hermetic fake HTTP / stubs only.
Optional live OpenAI-compat path is reserved via the registered ``llm`` marker
(not wired in this module).
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.llm.client import ChatCompletionResult, StubChatClient
from elyra.llm.reasoning_hygiene import sanitize_completion
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
from elyra.loop.skill_commit_policy import skill_commit_host_message
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
# 5c. Skill-commit HOST after load_skill (PR2)
# ---------------------------------------------------------------------------


def test_load_skill_work_free_text_skill_commit_then_tools(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """load_skill(plan-work) → free-text → skill_commit obs → tools; continuous OFF."""
    mid = moments.open_moment(why_now="skill commit", moment_id="mskillcommit")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("load_skill", {"name": "plan-work"}, call_id="c1"),
            _text("I have the skill, planning..."),
            _tc("list_dir", {"path": "."}, call_id="c2"),
            _text("done after tools"),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=False,
        settings=default_settings(),  # continuous OFF
    )
    assert result.skill_commit_injects == 1
    assert result.work_continue_injects == 0
    assert result.tools_ran is True
    assert result.stop_reason == "no_tools"
    beats = moments.list_beats(mid)
    commits = [
        b
        for b in beats
        if b.get("type") == "obs" and b.get("kind") == "skill_commit"
    ]
    assert len(commits) == 1
    content = commits[0].get("content") or ""
    assert content == skill_commit_host_message("plan-work")
    assert content.startswith("HOST:")
    assert commits[0].get("skill") == "plan-work"
    assert _is_host_inject({"role": "user", "content": content})
    # Order: tool load_skill, model free-text, obs skill_commit, tool list_dir
    commit_idx = next(
        i for i, b in enumerate(beats) if b.get("kind") == "skill_commit"
    )
    load_idx = next(
        i
        for i, b in enumerate(beats)
        if b.get("type") == "tool" and b.get("name") == "load_skill"
    )
    list_idx = next(
        i
        for i, b in enumerate(beats)
        if b.get("type") == "tool" and b.get("name") == "list_dir"
    )
    assert load_idx < commit_idx < list_idx


def test_flood_free_text_still_gets_skill_commit(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Flood free-text after load_skill still injects skill_commit (unlike work_continue).

    Continuous ON so a pure work_continue path would also be candidate; flood
    hard-stops work_continue on the flood hop, but skill_commit still fires.
    """
    mid = moments.open_moment(why_now="flood skill", moment_id="mfloodsc")
    ctx.moment_id = mid
    flood = "\n".join(["<|channel>thought"] * 20)
    client = StubChatClient.scripted(
        [
            _tc("load_skill", {"name": "plan-work"}, call_id="c1"),
            {
                "content": flood,
                "reasoning_content": flood,
                "tool_calls": [],
                "finish_reason": "length",
            },
            # Second free-text is also flood → no work_continue either.
            {
                "content": flood,
                "reasoning_content": flood,
                "tool_calls": [],
                "finish_reason": "length",
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
    assert result.skill_commit_injects == 1
    assert result.work_continue_injects == 0  # flood hard-stops work_continue
    assert result.channel_flood_beats >= 1
    assert result.last_stop_hop_was_flood is True
    beats = moments.list_beats(mid)
    assert any(b.get("kind") == "skill_commit" for b in beats)
    assert not any(b.get("kind") == "work_continue" for b in beats)


def test_social_plan_work_free_text_skill_commit_not_no_speak(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Social + load_skill(plan-work) + free-text → skill_commit, not no_speak on that hop."""
    mid = moments.open_moment(why_now="social plan", moment_id="msocplan")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("load_skill", {"name": "plan-work"}, call_id="c1"),
            _text("planning without tools"),
            _tc("list_dir", {"path": "."}, call_id="c2"),
            _text("after tools free"),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=True,
        settings=default_settings(),
    )
    assert result.skill_commit_injects == 1
    beats = moments.list_beats(mid)
    kinds = [b.get("kind") for b in beats if b.get("type") == "obs"]
    assert "skill_commit" in kinds
    # On the hop after load_skill, no_speak must not fire first.
    commit_idx = kinds.index("skill_commit")
    if "no_speak_nudge" in kinds:
        assert kinds.index("no_speak_nudge") > commit_idx
    # After commit spent + tools (list_dir clears pending), free-text may no_speak.
    # Sequence ends with free-text after list_dir — social !spoke → no_speak then stop.
    # Either path is fine as long as first free-text after load was skill_commit.


def test_load_skill_and_list_goals_same_batch_no_skill_commit(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Same-batch load_skill + non-load tool clears pending; free-text does not skill_commit."""
    mid = moments.open_moment(why_now="same batch", moment_id="msamebatch")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _batch(
                {"id": "c1", "name": "load_skill", "arguments": {"name": "plan-work"}},
                {"id": "c2", "name": "list_dir", "arguments": {"path": "."}},
            ),
            _text("free after same-batch tools"),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=False,
        settings=default_settings(),
    )
    assert result.skill_commit_injects == 0
    assert result.tools_ran is True
    assert result.stop_reason == "no_tools"
    assert not any(
        b.get("kind") == "skill_commit" for b in moments.list_beats(mid)
    )


def test_load_rest_alone_no_skill_commit(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """rest is never commit-eligible; free-text does not skill_commit."""
    mid = moments.open_moment(why_now="rest idle", moment_id="mrestalone")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("load_skill", {"name": "rest"}, call_id="c1"),
            _text("honest idle free text"),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=False,
        settings=default_settings(),
    )
    assert result.skill_commit_injects == 0
    assert result.stop_reason == "no_tools"
    assert not any(
        b.get("kind") == "skill_commit" for b in moments.list_beats(mid)
    )


def test_plan_work_then_rest_clears_pending_no_skill_commit(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """load plan-work then rest (replace-not-sticky) → free-text does not skill_commit."""
    mid = moments.open_moment(why_now="rest supersede", moment_id="mrestsuper")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("load_skill", {"name": "plan-work"}, call_id="c1"),
            _tc("load_skill", {"name": "rest"}, call_id="c2"),
            _text("idle after rest"),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=False,
        settings=default_settings(),
    )
    assert result.skill_commit_injects == 0
    assert not any(
        b.get("kind") == "skill_commit" for b in moments.list_beats(mid)
    )


def test_failed_rest_load_does_not_clear_prior_work_pending(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Failed rest load does not clear prior work pending → free-text skill_commits."""
    mid = moments.open_moment(why_now="failed rest", moment_id="mfailrest")
    ctx.moment_id = mid

    class _FailRestAfterPlan:
        def openai_tools(self) -> list[dict[str, Any]]:
            return registry.openai_tools()

        def execute(
            self, name: str, args: dict[str, Any] | None, c: ToolContext
        ) -> ToolResult:
            if name == "load_skill" and (args or {}).get("name") == "rest":
                return ToolResult(
                    ok=False,
                    payload={"name": "rest"},
                    error_reason="simulated_fail",
                )
            return registry.execute(name, args, c)

    client = StubChatClient.scripted(
        [
            _tc("load_skill", {"name": "plan-work"}, call_id="c1"),
            _tc("load_skill", {"name": "rest"}, call_id="c2"),
            _text("should get skill_commit for plan-work"),
            _text("after commit"),
        ]
    )
    result = run_do_loop(
        client=client,
        registry=_FailRestAfterPlan(),  # type: ignore[arg-type]
        ctx=ctx,
        outer_prefix=_outer(),
        settings=default_settings(),
        moments=moments,
        social_wake=False,
    )
    assert result.skill_commit_injects == 1
    commits = [
        b
        for b in moments.list_beats(mid)
        if b.get("kind") == "skill_commit"
    ]
    assert len(commits) == 1
    assert commits[0].get("skill") == "plan-work"


def test_non_load_clear_does_not_set_skill_commit_sent(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Clearing pending via non-load tool does not spend skill_commit budget.

    After clear without inject, a later re-load can still arm and inject once.
    After inject spends budget, second skill load free-text gets no second HOST.
    """
    mid = moments.open_moment(why_now="budget", moment_id="mbudgetsc")
    ctx.moment_id = mid
    # Path A: load + list_dir same batch clears pending without inject;
    # then re-load eligible alone → free-text should still skill_commit once.
    client = StubChatClient.scripted(
        [
            _batch(
                {"id": "c1", "name": "load_skill", "arguments": {"name": "plan-work"}},
                {"id": "c2", "name": "list_dir", "arguments": {"path": "."}},
            ),
            _tc("load_skill", {"name": "do-work"}, call_id="c3"),
            _text("commit for do-work"),
            _tc("load_skill", {"name": "create-tool"}, call_id="c4"),
            _text("second free-text after inject spent — no second HOST"),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=False,
        settings=default_settings(),
    )
    assert result.skill_commit_injects == 1
    commits = [
        b
        for b in moments.list_beats(mid)
        if b.get("kind") == "skill_commit"
    ]
    assert len(commits) == 1
    assert commits[0].get("skill") == "do-work"


def test_skill_commit_does_not_touch_speak_transport(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore, paths
) -> None:
    """skill_commit HOST is chain-only; never SpeakTransport / glass."""
    mid = moments.open_moment(why_now="glass check", moment_id="mglasssc")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("load_skill", {"name": "plan-work"}, call_id="c1"),
            _text("free"),
            _text("after"),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=False,
        settings=default_settings(),
    )
    assert result.skill_commit_injects == 1
    host_line = skill_commit_host_message("plan-work")
    glass = list_messages(paths=paths)
    assert not any(host_line in (m.get("content") or "") for m in glass)
    assert not any(
        "execute its next checklist step" in (m.get("content") or "")
        for m in glass
    )


def test_skill_commit_once_per_moment_budget(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """At most one skill_commit HOST per moment even with two free-text hops."""
    mid = moments.open_moment(why_now="once", moment_id="moncesc")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("load_skill", {"name": "plan-work"}, call_id="c1"),
            _text("first free"),
            _text("second free after commit"),
            _text("should not run third"),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=False,
        settings=default_settings(),
    )
    assert result.skill_commit_injects == 1
    assert result.hop_count == 3  # load + free + free after inject
    commits = [
        b
        for b in moments.list_beats(mid)
        if b.get("kind") == "skill_commit"
    ]
    assert len(commits) == 1


# ---------------------------------------------------------------------------
# 5d. Optional post-load tool_choice=required (PR4; default OFF)
# ---------------------------------------------------------------------------


def test_post_load_tool_choice_flag_off_stays_none(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Default flag OFF → tool_choice is None after load_skill arm."""
    mid = moments.open_moment(why_now="tc off", moment_id="mtcoff")
    ctx.moment_id = mid
    captured: list[Any] = []

    class _CaptureChoice:
        def __init__(self) -> None:
            self._n = 0
            self._inner = StubChatClient.scripted(
                [
                    _tc("load_skill", {"name": "plan-work"}, call_id="c1"),
                    _tc("list_dir", {"path": "."}, call_id="c2"),
                    _text("done"),
                ]
            )

        def chat_completion(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(kwargs.get("tool_choice"))
            return self._inner.chat_completion(messages, **kwargs)

    result = run_do_loop(
        client=_CaptureChoice(),  # type: ignore[arg-type]
        registry=registry,
        ctx=ctx,
        outer_prefix=_outer(),
        settings=default_settings(),  # flag default False
        moments=moments,
        social_wake=False,
    )
    assert result.tools_ran is True
    assert len(captured) >= 2
    # hop0: no pending yet → None; hop1: pending armed but flag OFF → None
    assert captured[0] is None
    assert captured[1] is None


def test_post_load_tool_choice_flag_on_required_after_load(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Flag ON + eligible pending → tool_choice == \"required\" on next hop only."""
    mid = moments.open_moment(why_now="tc on", moment_id="mtcon")
    ctx.moment_id = mid
    captured: list[Any] = []

    class _CaptureChoice:
        def __init__(self) -> None:
            self._inner = StubChatClient.scripted(
                [
                    _tc("load_skill", {"name": "plan-work"}, call_id="c1"),
                    # Hop after arm: model obeys required with a real tool.
                    _tc("list_dir", {"path": "."}, call_id="c2"),
                    _text("done"),
                ]
            )

        def chat_completion(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(kwargs.get("tool_choice"))
            return self._inner.chat_completion(messages, **kwargs)

    settings = _settings(post_load_skill_tool_choice_required=True)
    result = run_do_loop(
        client=_CaptureChoice(),  # type: ignore[arg-type]
        registry=registry,
        ctx=ctx,
        outer_prefix=_outer(),
        settings=settings,
        moments=moments,
        social_wake=False,
    )
    assert result.tools_ran is True
    assert len(captured) >= 3
    # hop0 pre-load: no pending → None
    assert captured[0] is None
    # hop1 after load_skill arm: required
    assert captured[1] == "required"
    # hop2 after non-load clear: pending cleared → None
    assert captured[2] is None


def test_post_load_tool_choice_cleared_after_commit_spent(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Flag ON: free-text spends skill_commit → later hop tool_choice is None."""
    mid = moments.open_moment(why_now="tc spent", moment_id="mtcsp")
    ctx.moment_id = mid
    captured: list[Any] = []

    class _CaptureChoice:
        def __init__(self) -> None:
            self._inner = StubChatClient.scripted(
                [
                    _tc("load_skill", {"name": "plan-work"}, call_id="c1"),
                    # Free-text while armed → skill_commit injects, clears pending
                    _text("planning in prose..."),
                    # After commit spent: no more required
                    _text("still free-text"),
                ]
            )

        def chat_completion(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(kwargs.get("tool_choice"))
            return self._inner.chat_completion(messages, **kwargs)

    settings = _settings(post_load_skill_tool_choice_required=True)
    result = run_do_loop(
        client=_CaptureChoice(),  # type: ignore[arg-type]
        registry=registry,
        ctx=ctx,
        outer_prefix=_outer(),
        settings=settings,
        moments=moments,
        social_wake=False,
    )
    assert result.skill_commit_injects == 1
    assert len(captured) >= 3
    assert captured[0] is None
    assert captured[1] == "required"  # armed on free-text hop
    assert captured[2] is None  # after commit spent / pending cleared


def test_post_load_tool_choice_rest_load_never_required(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """rest is not commit-eligible; flag ON still yields None after load rest."""
    mid = moments.open_moment(why_now="tc rest", moment_id="mtcrest")
    ctx.moment_id = mid
    captured: list[Any] = []

    class _CaptureChoice:
        def __init__(self) -> None:
            self._inner = StubChatClient.scripted(
                [
                    _tc("load_skill", {"name": "rest"}, call_id="c1"),
                    _text("idle"),
                ]
            )

        def chat_completion(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(kwargs.get("tool_choice"))
            return self._inner.chat_completion(messages, **kwargs)

    settings = _settings(post_load_skill_tool_choice_required=True)
    result = run_do_loop(
        client=_CaptureChoice(),  # type: ignore[arg-type]
        registry=registry,
        ctx=ctx,
        outer_prefix=_outer(),
        settings=settings,
        moments=moments,
        social_wake=False,
    )
    assert result.skill_commit_injects == 0
    assert len(captured) >= 2
    assert captured[0] is None
    assert captured[1] is None  # rest never arms


def test_social_hop0_speak_pin_wins_over_post_load_flag(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Social hop==0 speak pin is never overridden by post-load required flag.

    pending_skill_commit cannot arm before hop 0 in real runs; this locks the
    wire order (speak pin first) independent of arm state / flag.
    """
    from elyra.llm.client import ToolCall as LlmToolCall

    captured: list[Any] = []

    class _CaptureChoice:
        def __init__(self) -> None:
            self._n = 0

        def chat_completion(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            captured.append(kwargs.get("tool_choice"))
            self._n += 1
            if self._n == 1:
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

    settings = _settings(post_load_skill_tool_choice_required=True)
    result = run_do_loop(
        client=_CaptureChoice(),  # type: ignore[arg-type]
        registry=registry,
        ctx=ctx,
        outer_prefix=[{"role": "system", "content": "test"}],
        settings=settings,
        moments=moments,
        social_wake=True,
    )
    assert result.spoke is True
    assert len(captured) >= 1
    # Hop-0 social: speak function pin, not "required"
    assert captured[0] == {"type": "function", "function": {"name": "speak"}}
    assert captured[0] != "required"


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
# Tool thrash (Phase B): post-batch HOST + K15 work_continue suppress
# ---------------------------------------------------------------------------


def test_identical_fail_tools_inject_thrash_host_once(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore, paths
) -> None:
    """Three identical failing read_file → one thrash HOST; fourth does not re-inject."""
    mid = moments.open_moment(why_now="thrash read", moment_id="mthrash1")
    ctx.moment_id = mid
    missing = {"path": "tools/drafts/search_web/TOOL.md"}
    client = StubChatClient.scripted(
        [
            _tc("read_file", missing, call_id="c1"),
            _tc("read_file", missing, call_id="c2"),
            _tc("read_file", missing, call_id="c3"),
            # After thrash HOST, free-text → stop (continuous OFF).
            _text("ok I'll stop"),
            # Would be hop if thrash re-injected; should not run.
            _text("should not run"),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=False,
        settings=default_settings(),
    )
    assert result.thrash_host_injects == 1
    assert result.stop_reason == "no_tools"
    beats = moments.list_beats(mid)
    thrash_obs = [
        b for b in beats if b.get("type") == "obs" and b.get("kind") == "tool_thrash"
    ]
    assert len(thrash_obs) == 1
    content = thrash_obs[0].get("content") or ""
    assert content.startswith("HOST:")
    assert "tool thrash" in content
    assert "read_file" in content
    assert "call tools to continue" not in content
    assert thrash_obs[0].get("streak") == 3
    assert _is_host_inject({"role": "user", "content": content})
    # Thrash HOST never on glass / SpeakTransport
    glass = list_messages(paths=paths)
    assert not any("tool thrash" in (m.get("content") or "") for m in glass)
    # attempt# present on tool results (streak after each call)
    tool_beats = [b for b in beats if b.get("type") == "tool" and b.get("name") == "read_file"]
    assert len(tool_beats) == 3
    bodies = [json.loads(b["content"]) for b in tool_beats if b.get("content")]
    assert [b.get("attempt") for b in bodies] == [1, 2, 3]
    assert all(b.get("ok") is False for b in bodies)


def test_thrash_host_budget_one_per_moment(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Fourth identical fail after thrash HOST does not second-inject."""
    mid = moments.open_moment(why_now="thrash budget", moment_id="mthrash2")
    ctx.moment_id = mid
    missing = {"path": "nope.md"}
    client = StubChatClient.scripted(
        [
            _tc("read_file", missing, call_id="c1"),
            _tc("read_file", missing, call_id="c2"),
            _tc("read_file", missing, call_id="c3"),  # injects thrash HOST
            _tc("read_file", missing, call_id="c4"),  # streak 4 but budget spent
            _text("done"),
        ]
    )
    result = _run(client, ctx, registry, moments=moments, social_wake=False)
    assert result.thrash_host_injects == 1
    thrash_obs = [
        b
        for b in moments.list_beats(mid)
        if b.get("type") == "obs" and b.get("kind") == "tool_thrash"
    ]
    assert len(thrash_obs) == 1


def test_work_continue_suppressed_after_thrash_host(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore, paths
) -> None:
    """Continuous ON + thrash HOST → free-text does not get work_continue (K15)."""
    mid = moments.open_moment(why_now="thrash k15", moment_id="mthrashk15")
    ctx.moment_id = mid
    missing = {"path": "tools/drafts/x/TOOL.md"}
    client = StubChatClient.scripted(
        [
            _tc("read_file", missing, call_id="c1"),
            _tc("read_file", missing, call_id="c2"),
            _tc("read_file", missing, call_id="c3"),  # thrash HOST
            _text("premature free text after thrash"),
            _text("should not get work_continue"),
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
    assert result.thrash_host_injects == 1
    assert result.work_continue_injects == 0
    assert result.stop_reason == "no_tools"
    beats = moments.list_beats(mid)
    assert any(b.get("kind") == "tool_thrash" for b in beats)
    assert not any(b.get("kind") == "work_continue" for b in beats)
    glass = list_messages(paths=paths)
    assert not any(WORK_CONTINUE_HOST in (m.get("content") or "") for m in glass)
    assert not any("tool thrash" in (m.get("content") or "") for m in glass)


def test_free_text_order_unchanged_without_thrash(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Without thrash, skill_commit → no_speak → work_continue order still holds.

    Regression lock: thrash must not reorder free-text injects.
    """
    mid = moments.open_moment(why_now="order lock", moment_id="morderlock")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("list_dir", {"path": "."}, call_id="c1"),
            _text("no speak yet"),
            _tc("speak", {"text": "hello"}, call_id="c2"),
            _text("after speak free"),
            _text("after work nudge free"),
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
    assert result.thrash_host_injects == 0
    assert result.work_continue_injects == 1
    kinds = [b.get("kind") for b in moments.list_beats(mid) if b.get("type") == "obs"]
    assert "no_speak_nudge" in kinds
    assert "work_continue" in kinds
    assert kinds.index("no_speak_nudge") < kinds.index("work_continue")
    assert "tool_thrash" not in kinds
    assert "thrash_lesson" not in kinds
    assert "lesson_pin" not in kinds


# ---------------------------------------------------------------------------
# Phase C — thrash lessons (request, capture, pin, synthesize)
# ---------------------------------------------------------------------------


def test_thrash_lesson_request_and_free_text_capture(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore, paths
) -> None:
    """After thrash HOST: lesson request; free-text stores lesson + pin; no glass."""
    mid = moments.open_moment(why_now="lesson capture", moment_id="mlesson1")
    ctx.moment_id = mid
    missing = {"path": "tools/drafts/search_web/TOOL.md"}
    lesson_text = (
        "FAILURE: wrong path for TOOL.md\n"
        "TRIED: read_file three times\n"
        "WHY: draft never written\n"
        "NEXT: write package files then install_tool_draft"
    )
    client = StubChatClient.scripted(
        [
            _tc("read_file", missing, call_id="c1"),
            _tc("read_file", missing, call_id="c2"),
            _tc("read_file", missing, call_id="c3"),  # thrash + lesson request
            _text(lesson_text),
        ]
    )
    result = _run(
        client,
        ctx,
        registry,
        moments=moments,
        social_wake=False,
        settings=default_settings(),
    )
    assert result.thrash_host_injects == 1
    assert result.stop_reason == "no_tools"  # honest stop after lesson OK
    beats = moments.list_beats(mid)
    kinds = [b.get("kind") for b in beats if b.get("type") == "obs"]
    assert "tool_thrash" in kinds
    assert "thrash_lesson" in kinds
    assert "lesson_pin" in kinds
    assert kinds.index("tool_thrash") < kinds.index("thrash_lesson")
    assert kinds.index("thrash_lesson") < kinds.index("lesson_pin")
    pin_obs = [b for b in beats if b.get("kind") == "lesson_pin"]
    assert len(pin_obs) == 1
    pin_content = pin_obs[0].get("content") or ""
    assert pin_content.startswith("HOST:")
    assert "moment lesson pin" in pin_content
    assert pin_obs[0].get("synthesized") is False
    # Lesson HOST never SpeakTransport / glass
    glass = list_messages(paths=paths)
    assert not any("thrash lesson" in (m.get("content") or "") for m in glass)
    assert not any("moment lesson pin" in (m.get("content") or "") for m in glass)


def test_flood_free_text_does_not_capture_lesson(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Channel-flood free-text after lesson request does not capture or pin."""
    mid = moments.open_moment(why_now="lesson flood", moment_id="mlessonflood")
    ctx.moment_id = mid
    missing = {"path": "nope.md"}
    flood = "\n".join(["<|channel>thought"] * 20)
    client = StubChatClient.scripted(
        [
            _tc("read_file", missing, call_id="c1"),
            _tc("read_file", missing, call_id="c2"),
            _tc("read_file", missing, call_id="c3"),
            {
                "content": flood,
                "reasoning_content": flood,
                "tool_calls": [],
                "finish_reason": "stop",
            },
        ]
    )
    result = _run(client, ctx, registry, moments=moments, social_wake=False)
    assert result.thrash_host_injects == 1
    assert result.last_stop_hop_was_flood is True
    beats = moments.list_beats(mid)
    assert any(b.get("kind") == "thrash_lesson" for b in beats)
    assert not any(b.get("kind") == "lesson_pin" for b in beats)


def test_lesson_capture_does_not_force_stop_before_skill_commit(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Lesson alone does not auto-stop: free-text order still runs (skill_commit)."""
    mid = moments.open_moment(why_now="lesson no autostop", moment_id="mlessonsc")
    ctx.moment_id = mid
    missing = {"path": "missing.md"}
    client = StubChatClient.scripted(
        [
            # Arm skill-commit via load_skill, then thrash path, then free-text.
            _tc("load_skill", {"name": "create-tool"}, call_id="ls1"),
            _tc("read_file", missing, call_id="c1"),
            _tc("read_file", missing, call_id="c2"),
            _tc("read_file", missing, call_id="c3"),  # thrash + lesson request
            # Free-text: captures lesson then skill_commit still injects → continue
            _text("FAILURE: thrash on missing path. NEXT: draft real files."),
            # After skill_commit HOST, free-text again → stop
            _text("done for real"),
        ]
    )
    result = _run(client, ctx, registry, moments=moments, social_wake=False)
    assert result.thrash_host_injects == 1
    assert result.skill_commit_injects == 1
    assert result.stop_reason == "no_tools"
    beats = moments.list_beats(mid)
    kinds = [b.get("kind") for b in beats if b.get("type") == "obs"]
    assert "lesson_pin" in kinds
    assert "skill_commit" in kinds
    # Capture/pin before skill_commit inject (order: lesson then free-text lattice)
    assert kinds.index("lesson_pin") < kinds.index("skill_commit")


def test_host_synthesized_lesson_after_fail_streak(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore, paths
) -> None:
    """No free-text after request: K more identical fails → HOST-synthesized pin."""
    mid = moments.open_moment(why_now="lesson synth", moment_id="mlessonsynth")
    ctx.moment_id = mid
    missing = {"path": "tools/drafts/x/TOOL.md"}
    client = StubChatClient.scripted(
        [
            _tc("read_file", missing, call_id="c1"),
            _tc("read_file", missing, call_id="c2"),
            _tc("read_file", missing, call_id="c3"),  # thrash + lesson request
            # 3 additional fails after request → synthesize
            _tc("read_file", missing, call_id="c4"),
            _tc("read_file", missing, call_id="c5"),
            _tc("read_file", missing, call_id="c6"),
            _text("stop now"),
        ]
    )
    result = _run(client, ctx, registry, moments=moments, social_wake=False)
    assert result.thrash_host_injects == 1
    assert result.stop_reason == "no_tools"
    beats = moments.list_beats(mid)
    pin_obs = [b for b in beats if b.get("kind") == "lesson_pin"]
    assert len(pin_obs) == 1
    assert pin_obs[0].get("synthesized") is True
    content = pin_obs[0].get("content") or ""
    assert "HOST-synthesized" in content
    assert content.startswith("HOST:")
    # Only one thrash HOST (budget)
    thrash_obs = [b for b in beats if b.get("kind") == "tool_thrash"]
    assert len(thrash_obs) == 1
    glass = list_messages(paths=paths)
    assert not any("HOST-synthesized" in (m.get("content") or "") for m in glass)


def test_diversified_fails_after_lesson_request_do_not_synthesize(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """Three *different* fail fingerprints after request must not HOST-synthesize.

    Synth counter is identical-fingerprint only (design C2); diversified recovery
    is not thrash continuation.
    """
    mid = moments.open_moment(why_now="lesson diversify", moment_id="mlessondiv")
    ctx.moment_id = mid
    client = StubChatClient.scripted(
        [
            _tc("read_file", {"path": "a.md"}, call_id="c1"),
            _tc("read_file", {"path": "a.md"}, call_id="c2"),
            _tc("read_file", {"path": "a.md"}, call_id="c3"),  # thrash + lesson req
            # Different paths → different fingerprints; no identical streak of 3
            _tc("read_file", {"path": "b.md"}, call_id="c4"),
            _tc("read_file", {"path": "c.md"}, call_id="c5"),
            _tc("read_file", {"path": "d.md"}, call_id="c6"),
            _text("changed approach"),
        ]
    )
    result = _run(client, ctx, registry, moments=moments, social_wake=False)
    assert result.thrash_host_injects == 1
    beats = moments.list_beats(mid)
    assert any(b.get("kind") == "thrash_lesson" for b in beats)
    # Free-text captures model lesson — but no HOST-synthesized pin before that
    pin_obs = [b for b in beats if b.get("kind") == "lesson_pin"]
    assert len(pin_obs) == 1
    assert pin_obs[0].get("synthesized") is False
    assert "HOST-synthesized" not in (pin_obs[0].get("content") or "")


def test_lesson_pin_survives_in_turn_reouter(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """lesson_pin HOST inject survives compress/re-outer (kept + sticky ensure)."""
    from elyra.loop.doloop import (
        _LoopState,
        _compress_chain_for_reouter,
        _ensure_lesson_pin_in_chain,
        enforce_in_turn_budget,
    )
    from elyra.loop.tool_thrash_policy import lesson_pin_host_message

    pin = lesson_pin_host_message("wrong path; write draft files next")
    # Build a chain with old batches + pin; compress drops old batches, keeps injects.
    chain = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "old1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "old1", "content": "x" * 200},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "old2",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "old2", "content": "y" * 200},
        {"role": "user", "content": pin},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "new1",
                    "type": "function",
                    "function": {"name": "list_dir", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "new1", "content": "z" * 50},
    ]
    compressed = _compress_chain_for_reouter(list(chain))
    pin_msgs = [
        m for m in compressed if m.get("role") == "user" and m.get("content") == pin
    ]
    assert len(pin_msgs) == 1

    # Sticky re-append when pin missing after compress (belt-and-suspenders).
    state = _LoopState(outer_prefix=[{"role": "system", "content": "sys"}])
    state.lesson_pin_message = pin
    state.chain_messages = [m for m in compressed if m.get("content") != pin]
    assert not any(m.get("content") == pin for m in state.chain_messages)
    _ensure_lesson_pin_in_chain(state)
    assert any(m.get("content") == pin for m in state.chain_messages)

    # Budget pressure: compress keeps HOST inject spans — pin present without ensure.
    outer = [{"role": "system", "content": "S" * 20}]
    fat_chain = list(chain)
    new_outer, new_chain, did = enforce_in_turn_budget(
        outer,
        fat_chain,
        budget_tokens=5,
        tool_result_max_chars=20,
        rebuild_outer=lambda: [{"role": "system", "content": "rebuilt"}],
    )
    assert did is True
    assert new_outer[0]["content"] == "rebuilt"
    pin_after_budget = [
        m for m in new_chain if m.get("role") == "user" and m.get("content") == pin
    ]
    assert len(pin_after_budget) == 1, (
        "compress/re-outer must keep lesson pin HOST inject without sticky re-append"
    )


def test_lesson_pin_survives_run_do_loop_reouter(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore
) -> None:
    """End-to-end: HOST-synth pin still present in model messages after re-outer.

    Tool-path synth keeps the loop running so fat payloads can force re-outer
    without free-text auto-stop after capture.
    """
    mid = moments.open_moment(why_now="lesson pin e2e", moment_id="mlessonpin")
    ctx.moment_id = mid
    missing = {"path": "tools/drafts/pin/TOOL.md"}
    messages_seen: list[list[dict[str, Any]]] = []
    script = [
        _tc("read_file", missing, call_id="c1"),
        _tc("read_file", missing, call_id="c2"),
        _tc("read_file", missing, call_id="c3"),  # thrash + lesson request
        _tc("read_file", missing, call_id="c4"),
        _tc("read_file", missing, call_id="c5"),
        _tc("read_file", missing, call_id="c6"),  # K fails → HOST-synth pin
        # Fat tools force re-outer; pin must survive into later completions
        _tc("list_dir", {"path": "."}, call_id="fat1"),
        _tc("list_dir", {"path": "."}, call_id="fat2"),
        _text("done after reouter"),
    ]
    idx = {"i": 0}

    def recording_client(
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatCompletionResult:
        messages_seen.append([dict(m) for m in messages])
        i = idx["i"]
        idx["i"] += 1
        resp = script[min(i, len(script) - 1)]
        if isinstance(resp, ChatCompletionResult):
            return resp
        return StubChatClient.scripted([resp]).chat_completion(messages, **kwargs)

    client = StubChatClient(responses=recording_client)
    fat = _FatPayloadRegistry(registry, blob_chars=30_000)
    rebuilds = {"n": 0}

    def rebuild() -> list[dict[str, Any]]:
        rebuilds["n"] += 1
        return [
            {"role": "system", "content": f"outer-{rebuilds['n']}"},
            {"role": "user", "content": "work"},
        ]

    settings = _settings(
        max_tool_hops=20,
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
        social_wake=False,
    )
    assert result.thrash_host_injects == 1
    assert result.reouter_count >= 1, result
    beats = moments.list_beats(mid)
    pin_obs = [b for b in beats if b.get("kind") == "lesson_pin"]
    assert len(pin_obs) == 1
    assert pin_obs[0].get("synthesized") is True
    pin_content = pin_obs[0].get("content") or ""
    assert "moment lesson pin" in pin_content
    assert "HOST-synthesized" in pin_content
    # Pin is injected at end of hop 6 (index 5). Completions from hop 7+
    # (messages_seen[6:]) must still include the pin after re-outer.
    post_pin_msgs = messages_seen[6:]
    assert post_pin_msgs, (
        f"expected completions after synth pin; saw {len(messages_seen)} total"
    )
    saw_pin = any(
        any(
            m.get("role") == "user" and (m.get("content") or "") == pin_content
            for m in msgs
        )
        for msgs in post_pin_msgs
    )
    assert saw_pin, "lesson pin HOST must appear in chain after re-outer"


# ---------------------------------------------------------------------------
# Phase C3: optional skip-identical (default OFF)
# ---------------------------------------------------------------------------


def test_skip_identical_default_off_still_executes(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore, monkeypatch
) -> None:
    """Default OFF: 6+ identical fails still call registry; no skip obs."""
    # Ensure product default (and do not enable via monkeypatch).
    import elyra.loop.doloop as doloop_mod

    assert doloop_mod.SKIP_IDENTICAL_ENABLED is False

    mid = moments.open_moment(why_now="skip off", moment_id="mskipoff")
    ctx.moment_id = mid
    missing = {"path": "tools/drafts/missing/TOOL.md"}
    # 6 identical fails — would skip if enabled after streak 5.
    client = StubChatClient.scripted(
        [
            _tc("read_file", missing, call_id=f"c{i}")
            for i in range(1, 7)
        ]
        + [_text("stop")]
    )
    execute_calls: list[str] = []
    real_execute = registry.execute

    def counting_execute(name: str, args: dict[str, Any] | None, c: ToolContext) -> ToolResult:
        execute_calls.append(name)
        return real_execute(name, args, c)

    monkeypatch.setattr(registry, "execute", counting_execute)
    result = _run(client, ctx, registry, moments=moments, social_wake=False)
    assert result.thrash_skips == 0
    assert execute_calls.count("read_file") == 6
    beats = moments.list_beats(mid)
    assert not any(
        b.get("type") == "obs" and b.get("kind") == "tool_skip_identical" for b in beats
    )
    tool_beats = [b for b in beats if b.get("type") == "tool" and b.get("name") == "read_file"]
    assert len(tool_beats) == 6
    # Real fails — not synthetic skip
    assert all(b.get("error_reason") != "skipped_identical" for b in tool_beats)


def test_skip_identical_enabled_skips_and_is_visible(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore, monkeypatch
) -> None:
    """Enabled: after 5 identical fails, 6th is synthetic skip — never silent."""
    import elyra.loop.doloop as doloop_mod

    monkeypatch.setattr(doloop_mod, "SKIP_IDENTICAL_ENABLED", True)

    mid = moments.open_moment(why_now="skip on", moment_id="mskipon")
    ctx.moment_id = mid
    missing = {"path": "tools/drafts/search_web/TOOL.md"}
    client = StubChatClient.scripted(
        [
            _tc("read_file", missing, call_id=f"c{i}")
            for i in range(1, 7)
        ]
        + [_text("I'll change approach")]
    )
    execute_calls: list[str] = []
    real_execute = registry.execute

    def counting_execute(name: str, args: dict[str, Any] | None, c: ToolContext) -> ToolResult:
        execute_calls.append(name)
        return real_execute(name, args, c)

    monkeypatch.setattr(registry, "execute", counting_execute)
    result = _run(client, ctx, registry, moments=moments, social_wake=False)

    assert result.thrash_skips == 1
    # First 5 executed; 6th skipped
    assert execute_calls.count("read_file") == 5
    beats = moments.list_beats(mid)
    skip_obs = [
        b
        for b in beats
        if b.get("type") == "obs" and b.get("kind") == "tool_skip_identical"
    ]
    assert len(skip_obs) == 1
    assert skip_obs[0].get("prior_error_reason") in ("not_found", "path_not_found") or (
        skip_obs[0].get("prior_error_reason") is not None
    )
    tool_beats = [b for b in beats if b.get("type") == "tool" and b.get("name") == "read_file"]
    assert len(tool_beats) == 6
    skip_tools = [b for b in tool_beats if b.get("error_reason") == "skipped_identical"]
    assert len(skip_tools) == 1
    assert skip_tools[0].get("ends_moment") is False
    # Model-visible payload fields (never silent)
    body = json.loads(skip_tools[0]["content"])
    assert body["ok"] is False
    assert body["error_reason"] == "skipped_identical"
    assert body["blocked_duplicate"] is True
    assert body.get("prior_error_reason")
    assert body.get("attempt") == 6
    assert body.get("args_echo") == missing
    assert body.get("next_actions")
    assert body.get("do_not")
    assert "HOST skipped re-exec" in (body.get("host_note") or "")
    # Stop still free-text; skip never ends moment
    assert result.stop_reason == "no_tools"


def test_skip_identical_never_ends_moment(
    ctx: ToolContext, registry: ToolRegistry, moments: MomentStore, monkeypatch
) -> None:
    """Skip synthetic results must not set ends_moment / stop the moment alone."""
    import elyra.loop.doloop as doloop_mod

    monkeypatch.setattr(doloop_mod, "SKIP_IDENTICAL_ENABLED", True)

    mid = moments.open_moment(why_now="skip no end", moment_id="mskipend")
    ctx.moment_id = mid
    missing = {"path": "nope.md"}
    # 5 real fails + 2 skips + free text stop
    client = StubChatClient.scripted(
        [
            _tc("read_file", missing, call_id=f"c{i}")
            for i in range(1, 8)
        ]
        + [_text("done")]
    )
    result = _run(client, ctx, registry, moments=moments, social_wake=False)
    assert result.thrash_skips == 2
    assert result.stop_reason == "no_tools"
    beats = moments.list_beats(mid)
    for b in beats:
        if b.get("error_reason") == "skipped_identical":
            assert b.get("ends_moment") is False
    stop_beats = [b for b in beats if b.get("type") == "stop"]
    assert len(stop_beats) == 1
    assert stop_beats[0].get("stop_reason") == "no_tools"
    assert stop_beats[0].get("thrash_skips") == 2


# ---------------------------------------------------------------------------
# Usage hard-stop → STOP_POLICY (Phase 0 PR 5b)
# ---------------------------------------------------------------------------


def test_usage_hard_stop_yields_policy_not_error(ctx, registry, moments):
    """UsageHardStopError from the gated client maps to stop_reason=policy.

    Dedicated except before broad Exception so continuous does not treat this
    as STOP_ERROR, and policy ∉ moment_continue allowlist.
    """
    from elyra.llm.usage import TokenUsage, UsageHardStopError, UsageMeter
    from elyra.llm.client import UsageGatedChatClient
    from elyra.settings import UsageSettings

    mid = moments.open_moment(why_now="hard-stop test", moment_id="m-hardstop")
    ctx.moment_id = mid

    # Meter already at ceiling → gate raises before any model call.
    usage = UsageSettings(
        enabled=True,
        weekly_allowed_tokens=10,
        day_allowed_tokens=10,
        hour_allowed_tokens=10,
    )
    # Persist under tmp via paths from ctx
    meter = UsageMeter.load(ctx.paths.data_dir, usage)
    meter.record(TokenUsage(total_tokens=10))
    assert meter.can_call() is False

    inner_calls = {"n": 0}

    def _should_not_run(*_a: Any, **_k: Any) -> ChatCompletionResult:
        inner_calls["n"] += 1
        return ChatCompletionResult(content="nope", reasoning_content="", raw_json="{}")

    client = UsageGatedChatClient(StubChatClient(responses=_should_not_run), meter)
    result = _run(client, ctx, registry, moments=moments, social_wake=False)

    assert result.stop_reason == "policy"
    assert result.stop_reason != "error"
    assert result.error is not None
    assert "usage_hard_stop" in result.error
    assert inner_calls["n"] == 0

    beats = moments.list_beats(mid)
    stop_beats = [b for b in beats if b.get("type") == "stop"]
    assert len(stop_beats) == 1
    assert stop_beats[0].get("stop_reason") == "policy"
    assert "usage_hard_stop" in (stop_beats[0].get("error") or "")


def test_usage_hard_stop_direct_raise_is_policy(ctx, registry, moments):
    """Bare UsageHardStopError (no gate wrapper) still maps to policy not error."""
    from elyra.llm.usage import UsageHardStopError

    mid = moments.open_moment(why_now="direct hard-stop", moment_id="m-direct-hs")
    ctx.moment_id = mid

    class _HardStopClient:
        def chat_completion(self, *args: Any, **kwargs: Any) -> ChatCompletionResult:
            raise UsageHardStopError("week budget exhausted", level="week")

    result = _run(_HardStopClient(), ctx, registry, moments=moments)  # type: ignore[arg-type]
    assert result.stop_reason == "policy"
    assert result.error == "usage_hard_stop:week:week budget exhausted"
    stop_beats = [b for b in moments.list_beats(mid) if b.get("type") == "stop"]
    assert stop_beats[0]["stop_reason"] == "policy"


def test_generic_exception_still_yields_error(ctx, registry, moments):
    """Non-usage exceptions still surface as stop_reason=error (regression)."""

    mid = moments.open_moment(why_now="generic boom", moment_id="m-boom")
    ctx.moment_id = mid

    class _BoomClient:
        def chat_completion(self, *args: Any, **kwargs: Any) -> ChatCompletionResult:
            raise RuntimeError("network down")

    result = _run(_BoomClient(), ctx, registry, moments=moments)  # type: ignore[arg-type]
    assert result.stop_reason == "error"
    assert result.error is not None
    assert "RuntimeError" in result.error
