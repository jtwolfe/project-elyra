"""Multi-hop do-loop: model ↔ tools with ToolResult contracts.

Scope: hop orchestration, in-turn budget, ends_moment batch abort, no-speak nudge.
In scope: ToolContext wiring hooks, beat appends, continue inject prechecks.
Out of scope: registry discovery, sandbox FS, presence phase machine, glass writes.

Trust: loop uses ToolResult.ends_moment / counts_as_speak only — never tool names.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Sequence

from elyra.llm.client import ChatClient, ChatCompletionResult, ToolCall as LlmToolCall
from elyra.loop.context import assemble_outer_meal, estimate_tokens
from elyra.loop.continue_policy import (
    continue_host_message,
    idle_minutes_since,
    should_inject_continue,
    should_stop_time_continue_declined,
    should_stop_wall_clock,
)
from elyra.loop.stop import (
    STOP_ERROR,
    resolve_host_precheck_stop,
    stop_for_no_tools,
    stop_from_tool_result,
)
from elyra.settings import LoopSettings, Settings, default_settings
from elyra.tools.registry import ToolRegistry
from elyra.tools.types import ToolContext, ToolResult, WaitArm

_LOG = logging.getLogger(__name__)

NO_SPEAK_NUDGE = (
    "HOST: no speak tool used — if the user needs a reply, call speak; otherwise stop."
)

_TRUNC_MARKER = "…[truncated]"
# Keep this many newest assistant+tool groups when re-outer compresses the chain.
_REOUTER_KEEP_GROUPS = 2


@dataclass(frozen=True)
class DoLoopResult:
    """Outcome of one moment's multi-hop do-loop."""

    stop_reason: str
    hop_count: int
    arm_wait: WaitArm | None = None
    spoke: bool = False
    moment_id: str = ""
    reouter_count: int = 0
    continue_injects: int = 0
    error: str | None = None


@dataclass
class _LoopState:
    """Mutable hop-local state (not public API)."""

    outer_prefix: list[dict[str, Any]]
    chain_messages: list[dict[str, Any]] = field(default_factory=list)
    hop: int = 0
    spoke: bool = False
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))
    continue_injects: int = 0
    no_speak_nudge_sent: bool = False
    arm_wait: WaitArm | None = None
    reouter_count: int = 0


def _loop_settings(settings: Settings | LoopSettings | None) -> LoopSettings:
    if settings is None:
        return default_settings().loop
    if isinstance(settings, LoopSettings):
        return settings
    return settings.loop


def _now_factory() -> datetime:
    return datetime.now(UTC)


def _message_tokens(msg: Mapping[str, Any]) -> int:
    """Token estimate including tool_calls JSON when present."""
    content = msg.get("content")
    n = estimate_tokens(content if isinstance(content, str) else (str(content) if content else ""))
    tcs = msg.get("tool_calls")
    if tcs:
        try:
            n += estimate_tokens(json.dumps(tcs, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            n += estimate_tokens(str(tcs))
    return n


def _messages_tokens(messages: Sequence[Mapping[str, Any]]) -> int:
    return sum(_message_tokens(m) for m in messages)


def serialize_tool_result(tr: ToolResult) -> dict[str, Any]:
    """Model-visible JSON body for a tool message (ok + payload + error)."""
    body: dict[str, Any] = {"ok": tr.ok}
    if tr.error_reason is not None:
        body["error_reason"] = tr.error_reason
    if isinstance(tr.payload, dict):
        for k, v in tr.payload.items():
            if k not in body:
                body[k] = v
    return body


def truncate_tool_content(content: str, max_chars: int) -> str:
    """Cap tool message string; append marker when truncated.

    When ``max_chars`` is smaller than the marker, returns a hard slice of
    ``content`` (no marker) so the result never exceeds the cap.
    """
    if max_chars <= 0 or len(content) <= max_chars:
        return content
    if max_chars <= len(_TRUNC_MARKER):
        return content[:max_chars]
    keep = max_chars - len(_TRUNC_MARKER)
    return content[:keep] + _TRUNC_MARKER


def tool_result_to_content(tr: ToolResult, max_chars: int) -> str:
    """Serialize + truncate a ToolResult for the wire tool message."""
    try:
        raw = json.dumps(serialize_tool_result(tr), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = json.dumps(
            {"ok": tr.ok, "error_reason": tr.error_reason or "serialize_failed"},
            ensure_ascii=False,
        )
    return truncate_tool_content(raw, max_chars)


def assistant_message_from_result(result: ChatCompletionResult) -> dict[str, Any]:
    """OpenAI-style assistant row carrying tool_calls (and optional content)."""
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": result.content if result.content else None,
    }
    if result.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": tc.arguments_raw
                    if tc.arguments_raw
                    else json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in result.tool_calls
        ]
    if result.reasoning_content:
        msg["reasoning_content"] = result.reasoning_content
    return msg


def _is_tool_batch_start(msg: Mapping[str, Any]) -> bool:
    return msg.get("role") == "assistant" and bool(msg.get("tool_calls"))


def _is_host_inject(msg: Mapping[str, Any]) -> bool:
    if msg.get("role") != "user":
        return False
    content = msg.get("content") or ""
    if not isinstance(content, str):
        return False
    return content.startswith("HOST:")


def _group_chain_spans(
    chain: Sequence[Mapping[str, Any]],
) -> list[tuple[str, int, int]]:
    """Return (kind, start, end) spans: 'batch' | 'inject' | 'other'."""
    spans: list[tuple[str, int, int]] = []
    i = 0
    n = len(chain)
    while i < n:
        msg = chain[i]
        if _is_tool_batch_start(msg):
            start = i
            i += 1
            while i < n and chain[i].get("role") == "tool":
                i += 1
            spans.append(("batch", start, i))
        elif _is_host_inject(msg):
            spans.append(("inject", i, i + 1))
            i += 1
        else:
            spans.append(("other", i, i + 1))
            i += 1
    return spans


def _drop_oldest_batch(chain: list[dict[str, Any]]) -> bool:
    """Drop the oldest complete assistant+tool batch if more than one exists.

    Never drops the sole remaining batch or the most recent HOST inject.
    Returns True if a batch was dropped.
    """
    spans = _group_chain_spans(chain)
    batch_spans = [(s, e) for kind, s, e in spans if kind == "batch"]
    if len(batch_spans) <= 1:
        return False
    # Drop oldest batch (first in list).
    start, end = batch_spans[0]
    del chain[start:end]
    return True


def _truncate_chain_tool_contents(chain: list[dict[str, Any]], max_chars: int) -> None:
    for msg in chain:
        if msg.get("role") != "tool":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = truncate_tool_content(content, max_chars)


def _compress_chain_for_reouter(chain: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep last N tool batches + trailing HOST injects."""
    spans = _group_chain_spans(chain)
    batches = [(s, e) for kind, s, e in spans if kind == "batch"]
    keep_starts: set[int] = set()
    for s, e in batches[-_REOUTER_KEEP_GROUPS:]:
        keep_starts.add(s)
    # Always keep injects (especially recent HOST lines).
    out: list[dict[str, Any]] = []
    for kind, s, e in spans:
        if kind == "batch" and s not in keep_starts:
            continue
        out.extend(chain[s:e])
    return out


def enforce_in_turn_budget(
    outer_prefix: list[dict[str, Any]],
    chain_messages: list[dict[str, Any]],
    *,
    budget_tokens: int,
    tool_result_max_chars: int,
    rebuild_outer: Callable[[], list[dict[str, Any]]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Trim chain (and maybe re-outer) so outer+chain fit budget.

    Returns ``(outer_prefix, chain_messages, did_reouter)``.
    """
    _truncate_chain_tool_contents(chain_messages, tool_result_max_chars)
    did_reouter = False

    def over() -> bool:
        return _messages_tokens(outer_prefix) + _messages_tokens(chain_messages) > budget_tokens

    if not over():
        return outer_prefix, chain_messages, False

    # Drop oldest complete assistant+tool pairs while over budget.
    while over() and _drop_oldest_batch(chain_messages):
        pass

    if not over():
        return outer_prefix, chain_messages, False

    # Still over → re-outer: compress chain, rebuild outer meal.
    chain_messages[:] = _compress_chain_for_reouter(chain_messages)
    _truncate_chain_tool_contents(chain_messages, tool_result_max_chars)
    if rebuild_outer is not None:
        outer_prefix = list(rebuild_outer())
        did_reouter = True
    # Final pass: drop more batches if still over after re-outer.
    while over() and _drop_oldest_batch(chain_messages):
        pass
    return outer_prefix, chain_messages, did_reouter


def _append_beat(
    moments: Any | None,
    moment_id: str,
    beat: dict[str, Any],
) -> None:
    if moments is None or not moment_id:
        return
    try:
        moments.append_beat(moment_id, beat)
    except Exception:  # noqa: BLE001 — beat persistence must not kill the loop
        _LOG.exception("append_beat failed moment_id=%s type=%s", moment_id, beat.get("type"))


def _obs_user_message(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _wire_ctx_defaults(
    ctx: ToolContext,
    *,
    registry: ToolRegistry,
    mark_spoke: Callable[[], None],
    mark_task_changed: Callable[[], None],
) -> None:
    """Fill missing ToolContext ports used by builtins (non-destructive)."""
    if ctx.registry is None:
        ctx.registry = registry
    if ctx.mark_spoke is None:
        ctx.mark_spoke = mark_spoke
    if ctx.mark_task_changed is None:
        ctx.mark_task_changed = mark_task_changed


def _execute_one(
    tc: LlmToolCall,
    *,
    registry: ToolRegistry,
    ctx: ToolContext,
) -> ToolResult:
    """Dispatch one LLM tool call → ToolResult (invalid JSON → error, continue)."""
    if not tc.arguments_parse_ok:
        return ToolResult(
            ok=False,
            payload={},
            error_reason="invalid_json_arguments",
        )
    return registry.execute(tc.name, tc.arguments, ctx)


def _drain_interjections(
    chain: list[dict[str, Any]],
    drain: Callable[[], Sequence[Any]] | None,
    moments: Any | None,
    moment_id: str,
) -> None:
    if drain is None:
        return
    try:
        items = drain()
    except Exception:  # noqa: BLE001
        _LOG.exception("drain_interjections failed")
        return
    for item in items or ():
        if isinstance(item, str):
            text = item
        elif isinstance(item, Mapping):
            text = str(item.get("content") or item.get("text") or "")
        else:
            continue
        if not text:
            continue
        chain.append(_obs_user_message(text))
        _append_beat(
            moments,
            moment_id,
            {"type": "obs", "kind": "interjection", "content": text},
        )


def run_do_loop(
    *,
    client: ChatClient,
    registry: ToolRegistry,
    ctx: ToolContext,
    outer_prefix: Sequence[Mapping[str, Any]] | None = None,
    rebuild_outer: Callable[[], list[dict[str, Any]]] | None = None,
    settings: Settings | LoopSettings | None = None,
    moments: Any | None = None,
    social_wake: bool = False,
    clock: Callable[[], datetime] | None = None,
    started_at: datetime | None = None,
    drain_interjections: Callable[[], Sequence[Any]] | None = None,
    max_tokens: int | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> DoLoopResult:
    """Run the multi-hop model↔tools loop until a stop reason fires.

    Parameters
    ----------
    client:
        Chat client (real HTTP or StubChatClient scripted sequences).
    registry:
        Tool catalog; ``openai_tools()`` + ``execute``.
    ctx:
        Host context for handlers (paths, sandbox, speak, timers, …).
    outer_prefix:
        Pre-built system+history+orient meal. If omitted, ``rebuild_outer`` is
        required and called once at start.
    rebuild_outer:
        Callable that rebuilds the outer meal (used at start if needed and on
        re-outer under in-turn budget pressure).
    social_wake:
        When True, inject a one-shot no-speak nudge before ``no_tools`` stop if
        no successful ``counts_as_speak`` occurred.
    moments:
        Optional MomentStore-like object with ``append_beat(moment_id, beat)``.
    """
    loop = _loop_settings(settings)
    now = clock or _now_factory
    t0 = started_at if started_at is not None else now()
    moment_id = ctx.moment_id or ""

    if outer_prefix is not None:
        initial_outer = [dict(m) for m in outer_prefix]
    elif rebuild_outer is not None:
        initial_outer = list(rebuild_outer())
    else:
        # Minimal outer so unit tests can pass a one-shot meal without assembler.
        initial_outer = [{"role": "system", "content": "elyra"}]

    state = _LoopState(outer_prefix=initial_outer, last_activity=t0)

    def mark_spoke() -> None:
        state.spoke = True
        state.last_activity = now()

    def mark_task_changed() -> None:
        state.last_activity = now()

    _wire_ctx_defaults(
        ctx,
        registry=registry,
        mark_spoke=mark_spoke,
        mark_task_changed=mark_task_changed,
    )

    openai_tools = tools if tools is not None else registry.openai_tools()
    gen_max = (
        max_tokens
        if max_tokens is not None
        else loop.generation_max_tokens
    )
    budget = min(loop.sliding_input_tokens, loop.in_turn_max_tokens)
    tool_cap = loop.tool_result_max_chars
    max_hops = loop.max_tool_hops

    def _rebuild() -> list[dict[str, Any]]:
        if rebuild_outer is not None:
            return list(rebuild_outer())
        return list(state.outer_prefix)

    try:
        return _run_loop_body(
            state=state,
            client=client,
            registry=registry,
            ctx=ctx,
            moments=moments,
            moment_id=moment_id,
            social_wake=social_wake,
            now=now,
            t0=t0,
            openai_tools=openai_tools,
            gen_max=gen_max,
            budget=budget,
            tool_cap=tool_cap,
            max_hops=max_hops,
            loop=loop,
            rebuild=_rebuild,
            drain_interjections=drain_interjections,
        )
    except Exception as exc:  # noqa: BLE001 — surface as stop error
        _LOG.exception("do-loop uncaught error")
        _append_beat(
            moments,
            moment_id,
            {"type": "stop", "stop_reason": STOP_ERROR, "error": str(exc)},
        )
        return DoLoopResult(
            stop_reason=STOP_ERROR,
            hop_count=state.hop,
            arm_wait=state.arm_wait,
            spoke=state.spoke,
            moment_id=moment_id,
            reouter_count=state.reouter_count,
            continue_injects=state.continue_injects,
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_loop_body(
    *,
    state: _LoopState,
    client: ChatClient,
    registry: ToolRegistry,
    ctx: ToolContext,
    moments: Any | None,
    moment_id: str,
    social_wake: bool,
    now: Callable[[], datetime],
    t0: datetime,
    openai_tools: list[dict[str, Any]],
    gen_max: int,
    budget: int,
    tool_cap: int,
    max_hops: int,
    loop: LoopSettings,
    rebuild: Callable[[], list[dict[str, Any]]],
    drain_interjections: Callable[[], Sequence[Any]] | None,
) -> DoLoopResult:
    while True:
        t = now()
        wall = should_stop_wall_clock(t0, t, settings=loop)
        declined = should_stop_time_continue_declined(
            state.last_activity,
            state.continue_injects,
            t,
            settings=loop,
        )
        pre = resolve_host_precheck_stop(
            wall_clock_exceeded=wall,
            hop=state.hop,
            max_tool_hops=max_hops,
            time_continue_declined=declined,
        )
        if pre is not None:
            return _finish(state, pre, moments, moment_id)

        # Continue inject (only when not already declined by precheck).
        if should_inject_continue(
            state.last_activity,
            state.continue_injects,
            t,
            settings=loop,
        ):
            idle = int(idle_minutes_since(state.last_activity, t))
            host_line = continue_host_message(max(idle, loop.continue_idle_minutes))
            state.chain_messages.append(_obs_user_message(host_line))
            state.continue_injects += 1
            state.last_activity = t
            _append_beat(
                moments,
                moment_id,
                {"type": "obs", "kind": "continue", "content": host_line},
            )

        state.outer_prefix, state.chain_messages, did_re = enforce_in_turn_budget(
            state.outer_prefix,
            state.chain_messages,
            budget_tokens=budget,
            tool_result_max_chars=tool_cap,
            rebuild_outer=rebuild,
        )
        if did_re:
            state.reouter_count += 1

        messages = list(state.outer_prefix) + list(state.chain_messages)
        result = client.chat_completion(
            messages,
            max_tokens=gen_max,
            tools=openai_tools,
        )
        state.hop += 1
        _append_beat(
            moments,
            moment_id,
            {
                "type": "model",
                "content": result.content or "",
                "reasoning": result.reasoning_content or "",
                "tool_calls": [
                    {"id": tc.id, "name": tc.name} for tc in result.tool_calls
                ],
                "hop": state.hop,
            },
        )

        if result.tool_calls:
            stop = _handle_tool_batch(
                state=state,
                result=result,
                registry=registry,
                ctx=ctx,
                moments=moments,
                moment_id=moment_id,
                tool_cap=tool_cap,
                now=now,
                drain_interjections=drain_interjections,
            )
            if stop is not None:
                return _finish(state, stop, moments, moment_id, arm=state.arm_wait)
            continue

        # Orphan content → model beat only (already recorded); never glass.
        if social_wake and not state.spoke and not state.no_speak_nudge_sent:
            state.no_speak_nudge_sent = True
            state.chain_messages.append(_obs_user_message(NO_SPEAK_NUDGE))
            _append_beat(
                moments,
                moment_id,
                {
                    "type": "obs",
                    "kind": "no_speak_nudge",
                    "content": NO_SPEAK_NUDGE,
                },
            )
            continue

        return _finish(state, stop_for_no_tools(), moments, moment_id)


def _handle_tool_batch(
    *,
    state: _LoopState,
    result: ChatCompletionResult,
    registry: ToolRegistry,
    ctx: ToolContext,
    moments: Any | None,
    moment_id: str,
    tool_cap: int,
    now: Callable[[], datetime],
    drain_interjections: Callable[[], Sequence[Any]] | None,
) -> str | None:
    """Execute tool_calls serially. Returns stop_reason or None to continue."""
    state.chain_messages.append(assistant_message_from_result(result))

    for tc in result.tool_calls:
        tr = _execute_one(tc, registry=registry, ctx=ctx)
        content = tool_result_to_content(tr, tool_cap)
        state.chain_messages.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": content,
            }
        )
        _append_beat(
            moments,
            moment_id,
            {
                "type": "tool",
                "name": tc.name,
                "tool_call_id": tc.id,
                "ok": tr.ok,
                "error_reason": tr.error_reason,
                "ends_moment": tr.ends_moment,
                "content": content[:500],
            },
        )

        if tr.counts_as_speak and tr.ok:
            # Loop tracks speak; host mark_spoke updates activity / glass hooks.
            state.spoke = True
            state.last_activity = now()
            if ctx.mark_spoke is not None:
                ctx.mark_spoke()

        if tr.ends_moment:
            # Remaining tool_calls in this batch are NOT executed.
            reason = stop_from_tool_result(
                ends_moment=True,
                stop_reason=tr.stop_reason,
            )
            if tr.arm_wait is not None:
                state.arm_wait = tr.arm_wait
            return reason or "policy"

    # Safe point: full batch complete without ends_moment.
    _drain_interjections(state.chain_messages, drain_interjections, moments, moment_id)
    return None


def _finish(
    state: _LoopState,
    stop_reason: str,
    moments: Any | None,
    moment_id: str,
    *,
    arm: WaitArm | None = None,
) -> DoLoopResult:
    _append_beat(
        moments,
        moment_id,
        {
            "type": "stop",
            "stop_reason": stop_reason,
            "hop_count": state.hop,
            "spoke": state.spoke,
        },
    )
    return DoLoopResult(
        stop_reason=stop_reason,
        hop_count=state.hop,
        arm_wait=arm if arm is not None else state.arm_wait,
        spoke=state.spoke,
        moment_id=moment_id,
        reouter_count=state.reouter_count,
        continue_injects=state.continue_injects,
    )


# Re-export meal helper for callers that build outer + run in one place.
__all__ = [
    "NO_SPEAK_NUDGE",
    "DoLoopResult",
    "assistant_message_from_result",
    "enforce_in_turn_budget",
    "run_do_loop",
    "serialize_tool_result",
    "tool_result_to_content",
    "truncate_tool_content",
    "assemble_outer_meal",
]
