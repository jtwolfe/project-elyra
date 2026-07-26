"""Multi-hop do-loop: model ↔ tools with ToolResult contracts.

Scope: hop orchestration, in-turn budget, ends_moment batch abort, no-speak
nudge, budgeted in-moment work-continue HOST (continuous policy), post-load
skill-commit HOST (skill_commit_policy), post-batch tool thrash HOST
(tool_thrash_policy), thrash lesson request/capture/HOST-synthesized pin
(Phase C), optional skip-identical re-exec (default OFF),
optional post-load tool_choice pin (default OFF).
In scope: ToolContext wiring hooks, beat appends, continue inject prechecks,
          completion-ingress channel hygiene (sanitize before beat/chain),
          tools_ran / ledger_mutated / flood / thrash counters on DoLoopResult.
Out of scope: registry discovery, sandbox FS, presence phase machine, glass
writes, outer moment_continue enqueue (PR6).

Trust: loop uses ToolResult.ends_moment / counts_as_speak only — never tool names.

Channel hygiene at completion ingress is **boundary defense** (tape + chain fuel),
not a claim that generation is cured — floods remain stochastic at the model.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Sequence

from elyra.llm.client import ChatClient, ChatCompletionResult, ToolCall as LlmToolCall
from elyra.llm.reasoning_hygiene import is_channel_flood, sanitize_completion
from elyra.llm.usage import UsageHardStopError
from elyra.loop.context import assemble_outer_meal, estimate_tokens
from elyra.loop.continue_policy import (
    continue_host_message,
    idle_minutes_since,
    should_inject_continue,
    should_stop_time_continue_declined,
    should_stop_wall_clock,
)
from elyra.loop.continuous_policy import (
    in_moment_work_context,
    should_in_moment_work_nudge,
    work_continue_host_message,
)
from elyra.loop.skill_commit_policy import (
    format_playbook_active,
    is_commit_eligible_skill,
    post_load_skill_tool_choice,
    should_allow_no_speak,
    should_skill_commit_nudge,
    skill_commit_host_message,
)
from elyra.loop.tool_thrash_policy import (
    LESSON_SYNTH_FAIL_STREAK,
    MAX_LESSON_PINS,
    SKIP_IDENTICAL_ENABLED,
    THRASH_TRIED_CAP,
    compact_lesson,
    lesson_pin_host_message,
    should_inject_thrash_host,
    should_skip_identical,
    synthesize_lesson,
    thrash_detail,
    thrash_host_message,
    thrash_lesson_request_message,
    tool_fingerprint,
    update_thrash_streak,
)
from elyra.loop.stop import (
    STOP_ERROR,
    STOP_POLICY,
    resolve_host_precheck_stop,
    stop_for_no_tools,
    stop_from_tool_result,
)
from elyra.settings import ContinuousSettings, LoopSettings, Settings, default_settings
from elyra.tools.registry import ToolRegistry
from elyra.tools.types import ToolContext, ToolResult, WaitArm

_LOG = logging.getLogger(__name__)

NO_SPEAK_NUDGE = (
    "HOST: no speak tool used — if the user needs a reply, call speak; otherwise stop."
)

# OpenAI-style function pin used only on social first completion (Stage 5 L4).
_SPEAK_TOOL_CHOICE: dict[str, Any] = {
    "type": "function",
    "function": {"name": "speak"},
}

_TRUNC_MARKER = "…[truncated]"
# Keep this many newest assistant+tool groups when re-outer compresses the chain.
_REOUTER_KEEP_GROUPS = 2


def social_first_hop_tool_choice(
    *,
    social_wake: bool,
    hop: int,
) -> dict[str, Any] | None:
    """Speak pin for the **first** social completion only.

    Predicate (normative for Stage 5 L4):

    - ``social_wake`` is True (presence user_message / wait_reply / …)
    - ``hop == 0`` **before** ``chat_completion`` is called

    Do **not** use ``hop == 1`` at call time: after the first return
    ``state.hop`` is already 1, so that would pin the **second** hop.
    Non-social wakes and later hops return ``None`` (omit ``tool_choice``;
    never default to ``required``).
    """
    if social_wake and hop == 0:
        return dict(_SPEAK_TOOL_CHOICE)
    return None


@dataclass(frozen=True)
class DoLoopResult:
    """Outcome of one moment's multi-hop do-loop."""

    stop_reason: str
    hop_count: int
    arm_wait: WaitArm | None = None
    spoke: bool = False
    moment_id: str = ""
    reouter_count: int = 0
    continue_injects: int = 0  # time-idle HOST injects (continue_policy)
    work_continue_injects: int = 0  # continuous work-continue HOST injects
    skill_commit_injects: int = 0  # post-load_skill commit HOST injects
    thrash_host_injects: int = 0  # post-batch tool thrash HOST injects
    thrash_skips: int = 0  # skip-identical synthetic results this moment
    # K15: ≥1 successful non-speak tool (ok and not counts_as_speak); speak alone False
    tools_ran: bool = False
    ledger_mutated: bool = False  # mark_task_changed fired this moment
    model_beats: int = 0  # type=model beats appended
    channel_flood_beats: int = 0  # model beats with hygiene.any_flood
    last_stop_hop_was_flood: bool = False  # free-text stop hop was channel flood
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
    work_continue_injects: int = 0
    pending_skill_commit: str | None = None
    skill_commit_sent: bool = False
    skill_commit_injects: int = 0
    tools_ran: bool = False
    ledger_mutated: bool = False
    model_beats: int = 0
    channel_flood_beats: int = 0
    last_stop_hop_was_flood: bool = False
    arm_wait: WaitArm | None = None
    reouter_count: int = 0
    # Tool thrash (Phase B) — moment-scoped; survive in-turn re-outer.
    thrash_last_fp: str | None = None
    thrash_streak: int = 0
    thrash_last_ok: bool | None = None
    thrash_last_error: str | None = None
    thrash_last_tool: str | None = None
    thrash_host_sent: int = 0
    thrash_tried: list[str] = field(default_factory=list)
    # Thrash lessons (Phase C) — moment-scoped; reset on new _LoopState only.
    lessons: list[str] = field(default_factory=list)
    lesson_request_sent: bool = False
    lesson_captured: bool = False
    lesson_pin_message: str | None = None
    # Identical-fail streak after lesson request (same fingerprint; synthesize after K).
    lesson_fails_since_request: int = 0
    lesson_synth_fp: str | None = None  # fp being counted for HOST-synthesize
    thrash_skip_count: int = 0  # skip-identical budget used this moment


def _loop_settings(settings: Settings | LoopSettings | None) -> LoopSettings:
    if settings is None:
        return default_settings().loop
    if isinstance(settings, LoopSettings):
        return settings
    return settings.loop


def _continuous_settings(
    settings: Settings | LoopSettings | ContinuousSettings | None,
) -> ContinuousSettings:
    if settings is None:
        return default_settings().continuous
    if isinstance(settings, ContinuousSettings):
        return settings
    if isinstance(settings, Settings):
        return settings.continuous
    # LoopSettings-only callers: continuous defaults (enabled OFF).
    return default_settings().continuous


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


def tool_result_to_content(
    tr: ToolResult,
    max_chars: int,
    *,
    tool_name: str | None = None,
) -> str:
    """Serialize + truncate a ToolResult for the wire tool message.

    Successful ``load_skill`` results are framed as plain-text playbooks
    (``PLAYBOOK ACTIVE: …``) when ``tool_name == "load_skill"`` and the payload
    has a body. Errors and all other tools stay JSON. Default ``tool_name=None``
    preserves the JSON path for direct unit callers and non-load tools.
    """
    if (
        tool_name == "load_skill"
        and tr.ok
        and isinstance(tr.payload, dict)
        and isinstance(tr.payload.get("body"), str)
        and tr.payload["body"]
    ):
        name = tr.payload.get("name")
        if not isinstance(name, str) or not name:
            name = "unknown"
        source = tr.payload.get("source")
        description = tr.payload.get("description")
        framed = format_playbook_active(
            name,
            tr.payload["body"],
            source=source if isinstance(source, str) else None,
            description=description if isinstance(description, str) else None,
        )
        return truncate_tool_content(framed, max_chars)

    try:
        raw = json.dumps(serialize_tool_result(tr), ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        raw = json.dumps(
            {"ok": tr.ok, "error_reason": tr.error_reason or "serialize_failed"},
            ensure_ascii=False,
        )
    return truncate_tool_content(raw, max_chars)


def assistant_message_from_result(
    result: ChatCompletionResult,
    *,
    include_reasoning: bool = True,
) -> dict[str, Any]:
    """OpenAI-style assistant row carrying tool_calls (and optional content).

    Prefer a **post-sanitize** ``result`` (ingress already strips channel markers).
    ``reasoning_content`` is re-fed only when non-empty **and** not a channel
    flood — defense in depth so pure floods never re-enter the multi-hop chain
    even if sanitize is skipped or residual flood text remains.
    """
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
    if include_reasoning:
        rc = (result.reasoning_content or "").strip()
        # Flood-aware omit: pure floods are long non-empty marker strings —
        # bare truthiness would re-feed them and reinfect later hops.
        if rc and not is_channel_flood(rc):
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

    # Still over → re-outer: compress chain; rebuild outer only when caller
    # supplied rebuild_outer (compress-only does not count as re-outer).
    chain_messages[:] = _compress_chain_for_reouter(chain_messages)
    _truncate_chain_tool_contents(chain_messages, tool_result_max_chars)
    if rebuild_outer is not None:
        outer_prefix = list(rebuild_outer())
        did_reouter = True
    # Final pass: drop more batches if still over after re-outer/compress.
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


def _install_activity_hooks(
    ctx: ToolContext,
    *,
    registry: ToolRegistry,
    state: _LoopState,
    now: Callable[[], datetime],
) -> tuple[Callable[[], None] | None, Callable[[], None] | None]:
    """Always wrap mark_spoke / mark_task_changed for this moment.

    Each ``run_do_loop`` entry installs fresh wrappers that:
    1. Update live ``state.spoke`` / ``state.last_activity``
    2. Call any pre-existing host hooks (best-effort; host exceptions logged)

    Returns the previous host callables so the caller can restore them on exit
    (avoids nesting wrappers and stale closures when the same ToolContext is
    reused across moments — presence PR12 pattern).
    """
    if ctx.registry is None:
        ctx.registry = registry

    host_spoke = ctx.mark_spoke
    host_task = ctx.mark_task_changed

    def mark_spoke() -> None:
        state.spoke = True
        state.last_activity = now()
        if host_spoke is not None:
            try:
                host_spoke()
            except Exception:  # noqa: BLE001 — host hooks must not kill the loop
                _LOG.exception("host mark_spoke failed")

    def mark_task_changed() -> None:
        state.last_activity = now()
        state.ledger_mutated = True  # continuous outer / in-moment progress (K15)
        if host_task is not None:
            try:
                host_task()
            except Exception:  # noqa: BLE001
                _LOG.exception("host mark_task_changed failed")

    ctx.mark_spoke = mark_spoke
    ctx.mark_task_changed = mark_task_changed
    return host_spoke, host_task


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
    wake_kind: str = "",
    has_open_goals_slice: bool = False,
    continuous_enabled: bool | None = None,
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
        When True, inject a one-shot no-speak nudge on free-text hops if no
        successful ``counts_as_speak`` occurred — via ``should_allow_no_speak``
        (deferred while a work/unknown skill is pending commit).
    wake_kind:
        Wake kind string for continuous in-moment ``work_context`` (non-social
        kinds ``task_ready`` / ``moment_continue`` / ``timer`` count as workish).
    has_open_goals_slice:
        Whether orient showed a non-empty open goals slice. Used only for
        **non-social** work_context; social wakes ignore leftover goals alone.
    continuous_enabled:
        Override continuous toggle for this moment. When None, uses
        ``settings.continuous.enabled`` (default OFF).
    moments:
        Optional MomentStore-like object with ``append_beat(moment_id, beat)``.

    Free-text inject order (K8 extended)::

        skill_commit → no_speak (via should_allow_no_speak) → work_continue → stop

    Skill-commit fires even on channel flood free-text and is independent of
    ``continuous_enabled``. Social no-speak no longer always wins first over
    post-skill commit.
    """
    loop = _loop_settings(settings)
    cont = _continuous_settings(settings)
    now = clock or _now_factory
    t0 = started_at if started_at is not None else now()
    moment_id = ctx.moment_id or ""
    cont_on = (
        bool(continuous_enabled)
        if continuous_enabled is not None
        else bool(cont.enabled)
    )

    if outer_prefix is not None:
        initial_outer = [dict(m) for m in outer_prefix]
    elif rebuild_outer is not None:
        initial_outer = list(rebuild_outer())
    else:
        # Minimal outer so unit tests can pass a one-shot meal without assembler.
        initial_outer = [{"role": "system", "content": "elyra"}]

    state = _LoopState(outer_prefix=initial_outer, last_activity=t0)

    # Always wrap (Issues 1–2): update live state + chain host hooks; restore on exit.
    host_spoke, host_task = _install_activity_hooks(
        ctx, registry=registry, state=state, now=now
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

    # Only pass caller-supplied rebuild_outer so reouter_count is accurate
    # (compress-only under pressure does not count as re-outer — Issue 6).
    try:
        return _run_loop_body(
            state=state,
            client=client,
            registry=registry,
            ctx=ctx,
            moments=moments,
            moment_id=moment_id,
            social_wake=social_wake,
            wake_kind=wake_kind or "",
            has_open_goals_slice=bool(has_open_goals_slice),
            continuous_enabled=cont_on,
            work_nudge_max=int(cont.in_moment_work_nudge_max),
            now=now,
            t0=t0,
            openai_tools=openai_tools,
            gen_max=gen_max,
            budget=budget,
            tool_cap=tool_cap,
            max_hops=max_hops,
            loop=loop,
            rebuild_outer=rebuild_outer,
            drain_interjections=drain_interjections,
        )
    except UsageHardStopError as exc:
        # Dedicated catch BEFORE broad Exception so hard-stop is policy, not error.
        # Continuous will not auto-chain (policy ∉ MOMENT_CONTINUE_STOP_ALLOWLIST).
        err_detail = f"usage_hard_stop:{exc.level}:{exc.reason}"
        _LOG.warning("do-loop usage hard stop: %s", err_detail)
        _append_beat(
            moments,
            moment_id,
            {
                "type": "stop",
                "stop_reason": STOP_POLICY,
                "error": err_detail,
            },
        )
        return DoLoopResult(
            stop_reason=STOP_POLICY,
            hop_count=state.hop,
            arm_wait=state.arm_wait,
            spoke=state.spoke,
            moment_id=moment_id,
            reouter_count=state.reouter_count,
            continue_injects=state.continue_injects,
            work_continue_injects=state.work_continue_injects,
            skill_commit_injects=state.skill_commit_injects,
            thrash_host_injects=state.thrash_host_sent,
            thrash_skips=state.thrash_skip_count,
            tools_ran=state.tools_ran,
            ledger_mutated=state.ledger_mutated,
            model_beats=state.model_beats,
            channel_flood_beats=state.channel_flood_beats,
            last_stop_hop_was_flood=state.last_stop_hop_was_flood,
            error=err_detail,
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
            work_continue_injects=state.work_continue_injects,
            skill_commit_injects=state.skill_commit_injects,
            thrash_host_injects=state.thrash_host_sent,
            thrash_skips=state.thrash_skip_count,
            tools_ran=state.tools_ran,
            ledger_mutated=state.ledger_mutated,
            model_beats=state.model_beats,
            channel_flood_beats=state.channel_flood_beats,
            last_stop_hop_was_flood=state.last_stop_hop_was_flood,
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        # Restore host hooks so reuse does not nest wrappers / stale state.
        ctx.mark_spoke = host_spoke
        ctx.mark_task_changed = host_task


def _run_loop_body(
    *,
    state: _LoopState,
    client: ChatClient,
    registry: ToolRegistry,
    ctx: ToolContext,
    moments: Any | None,
    moment_id: str,
    social_wake: bool,
    wake_kind: str,
    has_open_goals_slice: bool,
    continuous_enabled: bool,
    work_nudge_max: int,
    now: Callable[[], datetime],
    t0: datetime,
    openai_tools: list[dict[str, Any]],
    gen_max: int,
    budget: int,
    tool_cap: int,
    max_hops: int,
    loop: LoopSettings,
    rebuild_outer: Callable[[], list[dict[str, Any]]] | None,
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
            rebuild_outer=rebuild_outer,
        )
        if did_re:
            state.reouter_count += 1
        # Belt-and-suspenders: lesson pin is HOST inject (compress keeps injects);
        # re-materialize if missing after re-outer/compress.
        _ensure_lesson_pin_in_chain(state)

        messages = list(state.outer_prefix) + list(state.chain_messages)
        # Stage 5 L4: pin speak only on social first completion (hop==0 pre-call).
        # Optional K12 post-load required pin only when speak pin does not apply —
        # hop-0 social speak always wins. Flag defaults OFF.
        tool_choice = social_first_hop_tool_choice(
            social_wake=social_wake,
            hop=state.hop,
        )
        if tool_choice is None:
            tool_choice = post_load_skill_tool_choice(
                pending_skill_name=state.pending_skill_commit,
                enabled=loop.post_load_skill_tool_choice_required,
            )
        result = client.chat_completion(
            messages,
            max_tokens=gen_max,
            tools=openai_tools,
            tool_choice=tool_choice,
        )
        # Ingress sanitize: strip channel-marker floods before beat tape + chain
        # re-feed. Boundary defense only — does not cure generation floods.
        result, hygiene = sanitize_completion(result)
        if hygiene.any_markers:
            _LOG.warning(
                "channel hygiene hop=%s content_markers=%s reasoning_markers=%s "
                "content_flood=%s reasoning_flood=%s",
                state.hop + 1,
                hygiene.original_content_markers,
                hygiene.original_reasoning_markers,
                hygiene.content_flood,
                hygiene.reasoning_flood,
            )
        state.hop += 1
        state.model_beats += 1
        hop_was_flood = bool(hygiene.any_flood)
        if hop_was_flood:
            state.channel_flood_beats += 1
        model_beat: dict[str, Any] = {
            "type": "model",
            "content": result.content or "",
            "reasoning": result.reasoning_content or "",
            "tool_calls": [
                {"id": tc.id, "name": tc.name} for tc in result.tool_calls
            ],
            "hop": state.hop,
        }
        if result.finish_reason is not None:
            model_beat["finish_reason"] = result.finish_reason
        if hygiene.any_markers:
            model_beat["hygiene"] = {
                "c_markers": hygiene.original_content_markers,
                "r_markers": hygiene.original_reasoning_markers,
                "flood": hygiene.any_flood,
            }
        _append_beat(moments, moment_id, model_beat)

        if result.tool_calls:
            # Tool path is not a free-text stop; clear last free-text flood flag.
            state.last_stop_hop_was_flood = False
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

        # Free-text hop (no tool_calls) — candidate no_tools stop site.
        # Flood flag from this completion (not only cumulative counters).
        state.last_stop_hop_was_flood = hop_was_flood

        # Orphan content → model beat only (already recorded); never glass.
        # Phase C lesson capture BEFORE frozen free-text inject order.
        # Does not force stop or tools; fall through to skill_commit → …
        _maybe_capture_free_text_lesson(
            state=state,
            content=result.content or "",
            hop_was_flood=hop_was_flood,
            moments=moments,
            moment_id=moment_id,
        )

        # Free-text inject order (K8 extended): skill_commit → no_speak →
        # work_continue → stop. Skill-commit fires even on flood free-text and
        # is independent of continuous_enabled.

        # 1. Post-load skill-commit HOST (once per moment when pending).
        commit = should_skill_commit_nudge(
            pending_skill_name=state.pending_skill_commit,
            skill_commit_sent=state.skill_commit_sent,
            free_text_no_tools=True,
        )
        if commit.inject:
            skill_name = state.pending_skill_commit or ""
            host_line = skill_commit_host_message(skill_name)
            state.chain_messages.append(_obs_user_message(host_line))
            state.skill_commit_sent = True
            state.pending_skill_commit = None
            state.skill_commit_injects += 1
            _append_beat(
                moments,
                moment_id,
                {
                    "type": "obs",
                    "kind": "skill_commit",
                    "content": host_line,
                    "skill": skill_name,
                },
            )
            continue

        # 2. Social no-speak via pure predicate (work pending defers; K7/K8).
        if should_allow_no_speak(
            social_wake=social_wake,
            spoke=state.spoke,
            no_speak_nudge_sent=state.no_speak_nudge_sent,
            pending_skill_name=state.pending_skill_commit,
            skill_commit_sent=state.skill_commit_sent,
        ):
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

        # 3. Budgeted in-moment work-continue HOST (continuous policy; K7/flood).
        # Flood free-text → hard stop (no inject).
        # K8 is owned structurally above (no-speak continue) AND in pure policy
        # (social work-continue requires spoke; after no-speak spent without
        # speak → need_spoke stop). no_speak_still_needed is always False here
        # because the structural branch already continued when it was True —
        # passed for API completeness / belt-and-suspenders if that branch moves.
        work_ctx = in_moment_work_context(
            social_wake=social_wake,
            tools_ran=state.tools_ran,
            ledger_mutated=state.ledger_mutated,
            wake_kind=wake_kind,
            has_open_goals_slice=has_open_goals_slice,
        )
        no_speak_still_needed = should_allow_no_speak(
            social_wake=social_wake,
            spoke=state.spoke,
            no_speak_nudge_sent=state.no_speak_nudge_sent,
            pending_skill_name=state.pending_skill_commit,
            skill_commit_sent=state.skill_commit_sent,
        )
        # Second inject blocked by work_nudge_sent >= max (budget), not by
        # folding last-HOST into work_context (keeps reason diagnostics clean).
        nudge = should_in_moment_work_nudge(
            continuous_enabled=continuous_enabled,
            social_wake=social_wake,
            spoke=state.spoke,
            no_speak_nudge_pending_or_needed=no_speak_still_needed,
            work_nudge_sent=state.work_continue_injects,
            max_nudges=work_nudge_max,
            work_context=work_ctx,
            last_hop_was_flood=hop_was_flood,
            thrash_host_sent=state.thrash_host_sent,
        )
        if nudge.inject:
            host_line = work_continue_host_message()
            state.chain_messages.append(_obs_user_message(host_line))
            state.work_continue_injects += 1
            _append_beat(
                moments,
                moment_id,
                {
                    "type": "obs",
                    "kind": "work_continue",
                    "content": host_line,
                },
            )
            continue

        return _finish(state, stop_for_no_tools(), moments, moment_id)


def _skill_name_from_load(
    tc: LlmToolCall,
    tr: ToolResult,
) -> str | None:
    """Prefer catalog meta name from ok payload; fall back to parsed args."""
    if isinstance(tr.payload, dict):
        payload_name = tr.payload.get("name")
        if isinstance(payload_name, str) and payload_name.strip():
            return payload_name.strip()
    if tc.arguments_parse_ok and isinstance(tc.arguments, dict):
        arg_name = tc.arguments.get("name")
        if isinstance(arg_name, str) and arg_name.strip():
            return arg_name.strip()
    return None


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
        args: Mapping[str, Any] = (
            tc.arguments
            if tc.arguments_parse_ok and isinstance(tc.arguments, dict)
            else {}
        )
        # Phase C3: optional skip-identical BEFORE registry execute (default OFF).
        fp = tool_fingerprint(tc.name, args)
        same_fp = state.thrash_last_fp is not None and fp == state.thrash_last_fp
        skipped = False
        prior_error: str | None = None
        if same_fp:
            skip_dec = should_skip_identical(
                enabled=SKIP_IDENTICAL_ENABLED,
                streak=state.thrash_streak,
                last_ok=state.thrash_last_ok,
                skip_count=state.thrash_skip_count,
            )
            if skip_dec.skip:
                prior_error = state.thrash_last_error
                # Synthetic model-visible result — never silent, never ends_moment.
                tr = ToolResult(
                    ok=False,
                    error_reason="skipped_identical",
                    ends_moment=False,
                    payload={
                        "blocked_duplicate": True,
                        "prior_error_reason": prior_error,
                        "attempt": state.thrash_streak + 1,
                        "args_echo": dict(args),
                        "next_actions": [
                            "change tool or arguments",
                            "or free-text stop / thrash lesson",
                        ],
                        "do_not": ["repeat this exact call"],
                        "host_note": (
                            "HOST skipped re-exec of identical failing call "
                            "— visible by design"
                        ),
                    },
                )
                state.thrash_skip_count += 1
                skipped = True
            else:
                tr = _execute_one(tc, registry=registry, ctx=ctx)
        else:
            tr = _execute_one(tc, registry=registry, ctx=ctx)

        # Thrash fingerprint streak (Phase B) — pure update then apply to state.
        # Synthetic skips still update streak so thrash HOST / further skips work.
        upd = update_thrash_streak(
            prev_fp=state.thrash_last_fp,
            prev_streak=state.thrash_streak,
            tool_name=tc.name,
            args=args,
            ok=tr.ok,
            error_reason=tr.error_reason,
        )
        state.thrash_last_fp = upd.fingerprint
        state.thrash_streak = upd.streak
        state.thrash_last_ok = upd.ok
        state.thrash_last_error = upd.error_reason
        state.thrash_last_tool = upd.tool_name
        # Compact tried fingerprints (cap 8).
        if upd.fingerprint not in state.thrash_tried:
            state.thrash_tried.append(upd.fingerprint)
            if len(state.thrash_tried) > THRASH_TRIED_CAP:
                state.thrash_tried = state.thrash_tried[-THRASH_TRIED_CAP:]

        # Phase C: identical-fail streak after lesson request (HOST-synthesize).
        # Only count continuing same fingerprint; diversified fails / ok reset.
        if state.lesson_request_sent and not state.lesson_captured:
            if not upd.ok and (
                state.lesson_synth_fp is not None
                and upd.fingerprint == state.lesson_synth_fp
            ):
                state.lesson_fails_since_request += 1
            elif not upd.ok:
                # New fail fingerprint — start a fresh identical streak.
                state.lesson_synth_fp = upd.fingerprint
                state.lesson_fails_since_request = 1
            else:
                state.lesson_fails_since_request = 0
                state.lesson_synth_fp = None

        # Enrich attempt# into payload via replace (ToolResult is frozen).
        payload = dict(tr.payload) if isinstance(tr.payload, dict) else {}
        payload["attempt"] = upd.streak
        tr = replace(tr, payload=payload)

        content = tool_result_to_content(tr, tool_cap, tool_name=tc.name)
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
        if skipped:
            _append_beat(
                moments,
                moment_id,
                {
                    "type": "obs",
                    "kind": "tool_skip_identical",
                    "name": tc.name,
                    "fingerprint": upd.fingerprint,
                    "streak": upd.streak,
                    "skip_count": state.thrash_skip_count,
                    "prior_error_reason": prior_error,
                    "content": content[:500],
                },
            )
            _LOG.info(
                "skip-identical moment_id=%s tool=%s streak=%s skip_count=%s fp=%s",
                moment_id,
                tc.name,
                upd.streak,
                state.thrash_skip_count,
                upd.fingerprint,
            )

        # Arm / clear skill-commit pending (K17 replace-not-sticky; OQ1 same-batch).
        # Failed tools do not arm or clear. Clears do NOT set skill_commit_sent.
        if tr.ok and tc.name == "load_skill":
            name = _skill_name_from_load(tc, tr)
            if name and is_commit_eligible_skill(name):
                state.pending_skill_commit = name
            else:
                # rest / empty / not eligible: supersede prior arm.
                state.pending_skill_commit = None
        elif tr.ok and tc.name != "load_skill":
            # Same batch OR later: model already committed to a non-load tool.
            state.pending_skill_commit = None

        if tr.ok and not tr.counts_as_speak:
            # K15: tools_ran = successful non-speak only (not speak-tool name).
            state.tools_ran = True

        if tr.counts_as_speak and tr.ok:
            # Wired wrapper updates state.last_activity/spoke then host hook.
            if ctx.mark_spoke is not None:
                ctx.mark_spoke()
            else:
                state.spoke = True
                state.last_activity = now()

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

    # Post-batch thrash HOST (tool path — NOT free-text order). Last-fp only (v1).
    thrash_dec = should_inject_thrash_host(
        streak=state.thrash_streak,
        last_ok=state.thrash_last_ok,
        thrash_host_sent=state.thrash_host_sent,
        tool_name=state.thrash_last_tool,
    )
    if thrash_dec.inject and state.thrash_last_tool:
        detail = thrash_detail(
            last_ok=state.thrash_last_ok,
            last_error=state.thrash_last_error,
        )
        host_line = thrash_host_message(
            tool_name=state.thrash_last_tool,
            streak=state.thrash_streak,
            detail=detail,
        )
        state.chain_messages.append(_obs_user_message(host_line))
        state.thrash_host_sent += 1
        _LOG.info(
            "thrash HOST inject moment_id=%s tool=%s streak=%s fp=%s",
            moment_id,
            state.thrash_last_tool,
            state.thrash_streak,
            state.thrash_last_fp,
        )
        _append_beat(
            moments,
            moment_id,
            {
                "type": "obs",
                "kind": "tool_thrash",
                "content": host_line,
                "fingerprint": state.thrash_last_fp,
                "streak": state.thrash_streak,
                "thrash_kind": thrash_dec.kind,
            },
        )

    # Phase C: arm thrash lesson request once after thrash HOST is in play
    # (including later batches when thrash budget already spent).
    if state.thrash_host_sent > 0 and not state.lesson_request_sent:
        lesson_req = thrash_lesson_request_message()
        state.chain_messages.append(_obs_user_message(lesson_req))
        state.lesson_request_sent = True
        state.lesson_fails_since_request = 0
        # Count further identical fails of the thrashing fingerprint only.
        state.lesson_synth_fp = state.thrash_last_fp
        _LOG.info("thrash lesson request moment_id=%s", moment_id)
        _append_beat(
            moments,
            moment_id,
            {
                "type": "obs",
                "kind": "thrash_lesson",
                "content": lesson_req,
            },
        )

    # Phase C: HOST-synthesized lesson after K additional *identical* fails.
    if (
        state.lesson_request_sent
        and not state.lesson_captured
        and state.lesson_fails_since_request >= LESSON_SYNTH_FAIL_STREAK
    ):
        _store_and_pin_lesson(
            state=state,
            lesson=synthesize_lesson(
                tried=state.thrash_tried,
                last_error=state.thrash_last_error,
                tool_name=state.thrash_last_tool or "tool",
            ),
            moments=moments,
            moment_id=moment_id,
            synthesized=True,
        )

    return None


def _ensure_lesson_pin_in_chain(state: _LoopState) -> None:
    """Re-append lesson pin HOST if missing after in-turn re-outer/compress."""
    pin = state.lesson_pin_message
    if not pin:
        return
    for msg in state.chain_messages:
        if msg.get("role") == "user" and msg.get("content") == pin:
            return
    state.chain_messages.append(_obs_user_message(pin))


def _store_and_pin_lesson(
    *,
    state: _LoopState,
    lesson: str,
    moments: Any | None,
    moment_id: str,
    synthesized: bool = False,
) -> None:
    """Store compact lesson, set pin HOST, mark captured. No auto-stop."""
    body = (lesson or "").strip()
    if not body:
        return
    state.lessons = (state.lessons + [body])[-MAX_LESSON_PINS:]
    state.lesson_captured = True
    pin = lesson_pin_host_message(body)
    state.lesson_pin_message = pin
    # Materialize as HOST so compress keeps inject span (OQ3).
    if not any(
        m.get("role") == "user" and m.get("content") == pin for m in state.chain_messages
    ):
        state.chain_messages.append(_obs_user_message(pin))
    _LOG.info(
        "lesson pin moment_id=%s synthesized=%s lessons=%s",
        moment_id,
        synthesized,
        len(state.lessons),
    )
    _append_beat(
        moments,
        moment_id,
        {
            "type": "obs",
            "kind": "lesson_pin",
            "content": pin,
            "synthesized": synthesized,
        },
    )


def _maybe_capture_free_text_lesson(
    *,
    state: _LoopState,
    content: str,
    hop_was_flood: bool,
    moments: Any | None,
    moment_id: str,
) -> None:
    """Capture non-flood free-text as lesson after request; do not force stop."""
    if not state.lesson_request_sent or state.lesson_captured:
        return
    if hop_was_flood:
        return
    text = (content or "").strip()
    if not text:
        return
    lesson = compact_lesson(text)
    if not lesson:
        return
    _store_and_pin_lesson(
        state=state,
        lesson=lesson,
        moments=moments,
        moment_id=moment_id,
        synthesized=False,
    )


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
            "tools_ran": state.tools_ran,
            "ledger_mutated": state.ledger_mutated,
            "work_continue_injects": state.work_continue_injects,
            "skill_commit_injects": state.skill_commit_injects,
            "thrash_host_injects": state.thrash_host_sent,
            "thrash_skips": state.thrash_skip_count,
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
        work_continue_injects=state.work_continue_injects,
        skill_commit_injects=state.skill_commit_injects,
        thrash_host_injects=state.thrash_host_sent,
        thrash_skips=state.thrash_skip_count,
        tools_ran=state.tools_ran,
        ledger_mutated=state.ledger_mutated,
        model_beats=state.model_beats,
        channel_flood_beats=state.channel_flood_beats,
        last_stop_hop_was_flood=state.last_stop_hop_was_flood,
    )


# Re-export meal helper for callers that build outer + run in one place.
__all__ = [
    "NO_SPEAK_NUDGE",
    "DoLoopResult",
    "assistant_message_from_result",
    "enforce_in_turn_budget",
    "run_do_loop",
    "serialize_tool_result",
    "social_first_hop_tool_choice",
    "tool_result_to_content",
    "truncate_tool_content",
    "assemble_outer_meal",
]
