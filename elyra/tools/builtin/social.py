"""Social tool builtins — speak, wait_user, schedule_wake, create_group, update_group.

Scope: thin wrappers that map tool args → ToolResult / presence ports.
In scope: speak transport (KD3 resolve + KD20 group null user_id), WaitArm +
ends_moment with conversation_id, timer enqueue via TimerService, group
topology mutators (create_group / update_group → ConversationsStore).
Out of scope: do-loop ends_moment batch abort (PR11), phase machine, glass for wait.

ONLY speak (via transport) writes assistant glass rows — never bare content.
wait_user ends the moment (loop trusts ends_moment); later batch calls are
loop responsibility. schedule_wake records a durable timer only.
create_group / update_group never auto-add operator or ctx.user_id to members.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from elyra.presence.timers import TimerService, parse_utc
from elyra.speak import SpeakTransport
from elyra.tools.types import ToolContext, ToolResult, WaitArm

# Fallback when ctx.settings is unset (matches WaitSettings defaults).
_DEFAULT_WAIT_TIMEOUT_S = 300
_DEFAULT_FREE_TEXT_TIMEOUT_S = 300


def speak(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Address a conversation (or DM shorthand) via glass transport.

    Args (schema): ``text`` (required caption — non-empty even with media),
    optional ``conversation_id`` (prefer when known), optional ``user_id``
    (DM shorthand / arming stamp; defaults via KD3 resolver), optional
    ``attachment_ids`` / ``attachments``.

    Resolution (KD3): arg conversation_id → ctx.conversation_id → user_id arg
    DM shorthand → ctx.user_id DM (skipped when social_kind==group) →
    fail closed ``missing_conversation``.

    Success → ``ok=True``, ``counts_as_speak=True``, payload with transport_ok.
    Transport failure → ``ok=False``, reason in payload (and error_reason).
    Empty/whitespace text is always rejected, including when attachments are
    present (KD8 caption policy).
    """
    raw_text = args.get("text")
    if raw_text is None and "text" not in args:
        # Key absent — fail closed without writing glass.
        return _text_error("missing_text", args, ctx)
    if not isinstance(raw_text, str):
        # Key present but not a string (incl. explicit null) — invalid_text.
        return _text_error("invalid_text", args, ctx)
    # Caption required even with attachments (KD8) — reject before media ingest
    # so empty-text+path does not leave orphan store entries.
    if not raw_text.strip():
        return _text_error("empty_text", args, ctx)

    conversation_id, conv_err = _resolve_conversation_id(args, ctx)
    if conv_err is not None:
        return _text_error(conv_err, args, ctx, conversation_id=conversation_id)

    # Arming / media stamp (session speaker). Group glass rows force user_id=None
    # inside transport (KD20); peer stamp for DM comes from conversation_id.
    speaker_id = _resolve_speaker_user_id(args, ctx)
    deliver_user_id = _deliver_user_id_for(conversation_id, speaker_id)

    transport = _resolve_transport(ctx)
    moment_id = ctx.moment_id or None

    # Resolve optional outbound media before deliver so failures never write glass.
    att_list, att_err = _resolve_speak_attachments(
        args, ctx, user_id=speaker_id or "operator"
    )
    if att_err is not None:
        return _text_error(
            att_err, args, ctx, conversation_id=conversation_id
        )

    delivery = transport.deliver(
        raw_text,
        user_id=deliver_user_id,
        conversation_id=conversation_id,
        moment_id=moment_id if moment_id else None,
        attachments=att_list if att_list else None,
    )

    if delivery.ok:
        return ToolResult(
            ok=True,
            payload=delivery.as_payload(),
            counts_as_speak=True,
        )

    reason = delivery.reason or "transport_failed"
    return ToolResult(
        ok=False,
        payload=delivery.as_payload(),
        error_reason=reason,
        counts_as_speak=False,
    )


def wait_user(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """End the moment and arm a durable user wait (multi-choice + free text).

    Args (schema):
      - ``prompt`` (required str) — question / wait prompt shown to the user
      - ``choices`` (optional list[str]) — multi-choice options; empty = free text
      - ``timeout_seconds`` (optional int) — default from settings.wait
        (300 multi-choice / 300 free-text when omitted; prefer longer for
        open-ended questions)
      - ``conversation_id`` (optional) — social address (KD3 same as speak)
      - ``user_id`` (optional) — arming / notify stamp; defaults to ctx.user_id

    Success → ``ok=True``, ``ends_moment=True``, ``stop_reason="wait"``,
    ``arm_wait=WaitArm(...)``, ``counts_as_speak=False``.

    When ``ctx.timers`` (or extras) is present, also persists the wait via
    ``TimerService.arm_wait`` so presence can fire ``wait_timeout`` on expiry.
    Loop still owns batch abort after ends_moment (PR11).
    """
    prompt = args.get("prompt")
    if prompt is None and "prompt" not in args:
        return ToolResult(
            ok=False,
            payload={"reason": "missing_prompt"},
            error_reason="missing_prompt",
            counts_as_speak=False,
        )
    if not isinstance(prompt, str):
        return ToolResult(
            ok=False,
            payload={"reason": "invalid_prompt"},
            error_reason="invalid_prompt",
            counts_as_speak=False,
        )
    prompt = prompt.strip()
    if not prompt:
        return ToolResult(
            ok=False,
            payload={"reason": "empty_prompt"},
            error_reason="empty_prompt",
            counts_as_speak=False,
        )

    choices, choices_err = _parse_choices(args.get("choices"))
    if choices_err is not None:
        return ToolResult(
            ok=False,
            payload={"reason": choices_err},
            error_reason=choices_err,
            counts_as_speak=False,
        )

    free_text = not choices
    timeout_seconds, timeout_err = _parse_timeout_seconds(
        args, ctx, free_text=free_text
    )
    if timeout_err is not None:
        return ToolResult(
            ok=False,
            payload={"reason": timeout_err},
            error_reason=timeout_err,
            counts_as_speak=False,
        )

    conversation_id, conv_err = _resolve_conversation_id(args, ctx)
    if conv_err is not None:
        return ToolResult(
            ok=False,
            payload={
                "reason": conv_err,
                "user_id": _resolve_speaker_user_id(args, ctx),
            },
            error_reason=conv_err,
            counts_as_speak=False,
        )

    # Arming stamp = session speaker (not room for group waits — KD12).
    user_id = _resolve_speaker_user_id(args, ctx) or "operator"
    wait_id = str(uuid.uuid4())
    arm = WaitArm(
        wait_id=wait_id,
        timeout_seconds=timeout_seconds,
        prompt=prompt,
        choices=choices,
        user_id=user_id,
        conversation_id=conversation_id,
    )

    # Durable snapshot for presence timers when host injected TimerService.
    timers = _resolve_timers(ctx)
    if timers is not None:
        try:
            timers.arm_wait(
                wait_id=arm.wait_id,
                prompt=arm.prompt,
                choices=list(arm.choices),
                user_id=arm.user_id,
                moment_id=ctx.moment_id or "",
                timeout=float(arm.timeout_seconds),
                conversation_id=arm.conversation_id,
            )
        except (ValueError, TypeError, OSError) as exc:
            return ToolResult(
                ok=False,
                payload={
                    "reason": f"arm_wait_failed:{type(exc).__name__}",
                    "detail": str(exc),
                },
                error_reason=f"arm_wait_failed:{type(exc).__name__}",
                counts_as_speak=False,
            )

    payload: dict[str, Any] = {
        "wait_id": arm.wait_id,
        "timeout_seconds": arm.timeout_seconds,
        "prompt": arm.prompt,
        "choices": list(arm.choices),
        "user_id": arm.user_id,
        "moment_id": ctx.moment_id or "",
        "armed": timers is not None,
    }
    if arm.conversation_id is not None:
        payload["conversation_id"] = arm.conversation_id
    return ToolResult(
        ok=True,
        payload=payload,
        ends_moment=True,
        stop_reason="wait",
        arm_wait=arm,
        counts_as_speak=False,
    )


def create_group(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Create a multi-party group conversation (explicit members only).

    Args (schema): ``name`` (required), ``members`` (required non-empty list),
    optional ``description``, optional ``conversation_id`` (tests/seeds).

    Does **not** auto-insert ``ctx.user_id`` or operator into members.
    Success → ``ok=True``, payload with conversation record + actor_user_id.
    Failures use stable ``error_reason`` on result and payload (KD-T7).
    """
    from elyra.conversations import ConversationsStore, validate_conversation_id

    raw_name = args.get("name")
    if raw_name is None and "name" not in args:
        return _tool_err("missing_name")
    if not isinstance(raw_name, str):
        return _tool_err("invalid_name", detail="name must be a string")
    if not raw_name.strip():
        return _tool_err("missing_name")

    clean, m_err, m_detail = _clean_tool_members(args.get("members"))
    if m_err:
        return _tool_err(m_err, detail=m_detail)

    # description: omit vs null vs str — fail closed on wrong type (KD-T8)
    desc_kw: str | None
    if "description" not in args:
        desc_kw = None  # store default
    else:
        description = args.get("description")
        if description is not None and not isinstance(description, str):
            return _tool_err(
                "invalid_description",
                detail="description must be str or null",
            )
        desc_kw = description  # str or None; store strips empty str → null

    cid_kw: str | None = None
    if "conversation_id" in args and args.get("conversation_id") not in (None, ""):
        raw_cid = args.get("conversation_id")
        if not isinstance(raw_cid, str):
            return _tool_err("invalid_conversation_id")
        try:
            cid_kw = validate_conversation_id(raw_cid.strip())
        except ValueError as exc:
            return _tool_err("invalid_conversation_id", detail=str(exc))
        if not cid_kw.startswith("group:"):
            return _tool_err(
                "invalid_conversation_id", detail="must be group:…"
            )

    try:
        rec = ConversationsStore(ctx.paths).create_group(
            name=raw_name,
            members=clean,  # type: ignore[arg-type]
            description=desc_kw,
            conversation_id=cid_kw,
        )
    except (ValueError, KeyError, OSError) as exc:
        return _map_store_error(exc)

    payload: dict[str, Any] = {
        "conversation": rec,
        "actor_user_id": _actor_user_id(ctx),
    }
    labels = _optional_member_labels(ctx, rec.get("members") or [])
    if labels:
        payload["member_labels"] = labels
    return ToolResult(ok=True, payload=payload)


def update_group(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Partial group update — args conversation_id only; members full replace.

    Args (schema): ``conversation_id`` (required ``group:…``), optional
    ``name`` / ``description`` / ``members`` (at least one field required).
    Never defaults conversation_id from ``ctx.conversation_id`` (KD-T4).
    """
    from elyra.conversations import ConversationsStore, validate_conversation_id

    raw_cid = args.get("conversation_id")
    if not isinstance(raw_cid, str) or not raw_cid.strip():
        return _tool_err("missing_conversation_id")
    try:
        cid = validate_conversation_id(raw_cid.strip())
    except ValueError as exc:
        return _tool_err("invalid_conversation_id", detail=str(exc))
    if not cid.startswith("group:"):
        return _tool_err("not_a_group", detail=cid)

    # Existence: prefer get() for clear conversation_not_found before update
    store = ConversationsStore(ctx.paths)
    existing = store.get(cid)
    if existing is None:
        return _tool_err("conversation_not_found", detail=cid)
    if existing.get("type") != "group":
        return _tool_err("not_a_group", detail=cid)

    kwargs: dict[str, Any] = {}
    if "name" in args:
        name = args.get("name")
        if name is None:
            return _tool_err("invalid_name", detail="group name cannot be null")
        if not isinstance(name, str) or not name.strip():
            return _tool_err("invalid_name")
        kwargs["name"] = name
    if "description" in args:
        description = args.get("description")
        if description is not None and not isinstance(description, str):
            return _tool_err("invalid_description")
        kwargs["description"] = description  # None clears; str strips in store
    if "members" in args:
        clean, m_err, m_detail = _clean_tool_members(args.get("members"))
        if m_err:
            return _tool_err(m_err, detail=m_detail)
        kwargs["members"] = clean

    if not kwargs:
        return _tool_err("no_fields_to_update")

    try:
        rec = store.update(cid, **kwargs)
    except (ValueError, KeyError, OSError) as exc:
        return _map_store_error(exc)

    payload: dict[str, Any] = {
        "conversation": rec,
        "actor_user_id": _actor_user_id(ctx),
    }
    labels = _optional_member_labels(ctx, rec.get("members") or [])
    if labels:
        payload["member_labels"] = labels
    return ToolResult(ok=True, payload=payload)


def schedule_wake(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    """Record a durable timer that will enqueue a ``timer`` wake when due.

    Args (schema):
      - ``wake_at`` (optional ISO UTC) — absolute fire time
      - ``delay_seconds`` (optional number) — relative from now (alternative to wake_at)
      - ``reason`` (optional str)
      - ``goal_id`` / ``task_id`` (optional str)

    Exactly one of ``wake_at`` or ``delay_seconds`` must be provided.
    Requires ``ctx.timers`` (or extras timers) — does not invent a WakeQueue.
    Does **not** end the moment (``ends_moment=False``).
    """
    timers = _resolve_timers(ctx)
    if timers is None:
        return ToolResult(
            ok=False,
            payload={"reason": "timers_unavailable"},
            error_reason="timers_unavailable",
        )

    wake_at, when_err = _resolve_wake_at(args)
    if when_err is not None:
        return ToolResult(
            ok=False,
            payload={"reason": when_err},
            error_reason=when_err,
        )

    reason = args.get("reason")
    if reason is None:
        reason_s = ""
    elif isinstance(reason, str):
        reason_s = reason
    else:
        return ToolResult(
            ok=False,
            payload={"reason": "invalid_reason"},
            error_reason="invalid_reason",
        )

    goal_id = _optional_str_id(args, "goal_id")
    if goal_id is False:
        return ToolResult(
            ok=False,
            payload={"reason": "invalid_goal_id"},
            error_reason="invalid_goal_id",
        )
    task_id = _optional_str_id(args, "task_id")
    if task_id is False:
        return ToolResult(
            ok=False,
            payload={"reason": "invalid_task_id"},
            error_reason="invalid_task_id",
        )

    try:
        timer = timers.schedule_timer(
            wake_at,
            reason=reason_s,
            goal_id=goal_id if isinstance(goal_id, str) else None,
            task_id=task_id if isinstance(task_id, str) else None,
        )
    except (ValueError, TypeError, OSError) as exc:
        return ToolResult(
            ok=False,
            payload={
                "reason": f"schedule_failed:{type(exc).__name__}",
                "detail": str(exc),
            },
            error_reason=f"schedule_failed:{type(exc).__name__}",
        )

    return ToolResult(
        ok=True,
        payload={
            "timer_id": timer.id,
            "wake_at": timer.wake_at,
            "reason": timer.reason,
            "goal_id": timer.goal_id,
            "task_id": timer.task_id,
            "status": timer.status,
        },
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_error(
    reason: str,
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    conversation_id: str | None = None,
) -> ToolResult:
    """Fail closed without writing glass.

    Payload user_id uses conversation-aware rules when conversation_id is
    known (group → null); otherwise diagnostic speaker/operator stamp.
    """
    from elyra.speak import normalize_speak_user_id

    speaker = _resolve_speaker_user_id(args, ctx)
    uid = normalize_speak_user_id(speaker, conversation_id=conversation_id)
    payload: dict[str, Any] = {
        "transport_ok": False,
        "reason": reason,
        "user_id": uid,
    }
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    return ToolResult(
        ok=False,
        payload=payload,
        error_reason=reason,
        counts_as_speak=False,
    )


def _social_kind(ctx: ToolContext) -> str:
    """Host social_kind from extras (stamped at enqueue). Absent → \"none\"."""
    raw = ctx.extras.get("social_kind") if isinstance(ctx.extras, dict) else None
    if raw in ("group", "dm", "none"):
        return str(raw)
    return "none"


def _resolve_conversation_id(
    args: dict[str, Any], ctx: ToolContext
) -> tuple[str | None, str | None]:
    """KD3 speak/wait address resolution.

    Order:
      1. Explicit non-blank ``conversation_id`` arg (validate format / type)
      2. Else ``ctx.conversation_id`` when non-blank
      3. Else non-blank ``user_id`` **arg** → DM shorthand (skipped when
         social_kind == group — fail closed, no silent group→DM demotion)
      4. Else ``ctx.user_id`` → ``dm:<ctx.user_id>`` when social_kind != group
      5. Else ``missing_conversation``

    Returns ``(conversation_id | None, error_reason | None)``.
    """
    from elyra.conversations import (
        ConversationsStore,
        dm_id_for_user,
        validate_conversation_id,
    )
    from elyra.identity.layout import validate_user_id

    kind = _social_kind(ctx)

    # 1. Explicit arg
    raw_cid = args.get("conversation_id")
    if isinstance(raw_cid, str) and raw_cid.strip():
        try:
            cid = validate_conversation_id(raw_cid.strip())
        except ValueError:
            return None, "invalid_conversation_id"
        # Soft-ensure DM exists; group must already exist (fail closed).
        if cid.startswith("dm:"):
            try:
                ConversationsStore(ctx.paths).ensure_dm(cid[3:])
            except (ValueError, OSError):
                pass
        elif cid.startswith("group:"):
            rec = ConversationsStore(ctx.paths).get(cid)
            if rec is None:
                return None, "conversation_not_found"
        return cid, None
    if raw_cid is not None and raw_cid != "":
        # Present but not a usable string (null / int / blank already handled).
        if not isinstance(raw_cid, str):
            return None, "invalid_conversation_id"

    # 2. ctx.conversation_id (wake-stamped; includes groups)
    if isinstance(ctx.conversation_id, str) and ctx.conversation_id.strip():
        try:
            cid = validate_conversation_id(ctx.conversation_id.strip())
        except ValueError:
            return None, "invalid_conversation_id"
        if cid.startswith("dm:"):
            try:
                ConversationsStore(ctx.paths).ensure_dm(cid[3:])
            except (ValueError, OSError):
                pass
        return cid, None

    # 3. Explicit user_id arg → intentional DM shorthand
    raw_uid = args.get("user_id")
    if isinstance(raw_uid, str) and raw_uid.strip():
        if kind == "group":
            # Model forgot the room — never demote group wake to dm:speaker.
            return None, "missing_conversation"
        try:
            peer = validate_user_id(raw_uid.strip())
        except ValueError:
            return None, "invalid_user_id"
        try:
            ConversationsStore(ctx.paths).ensure_dm(peer)
        except (ValueError, OSError):
            pass
        return dm_id_for_user(peer), None

    # 4. ctx.user_id DM fallback — skipped for social_kind=group (T8)
    if kind != "group" and ctx.user_id is not None and str(ctx.user_id).strip():
        try:
            peer = validate_user_id(str(ctx.user_id).strip())
        except ValueError:
            return None, "invalid_user_id"
        try:
            ConversationsStore(ctx.paths).ensure_dm(peer)
        except (ValueError, OSError):
            pass
        return dm_id_for_user(peer), None

    # 5. Fail closed
    return None, "missing_conversation"


def _resolve_speaker_user_id(args: dict[str, Any], ctx: ToolContext) -> str | None:
    """Arming / media stamp: args.user_id → ctx.user_id → None (not operator).

    Operator default is applied only at deliver normalize when conversation is
    null (legacy) or as wait arming last resort.
    """
    raw = args.get("user_id")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if ctx.user_id is not None and str(ctx.user_id).strip():
        return str(ctx.user_id).strip()
    return None


def _deliver_user_id_for(
    conversation_id: str | None, speaker_id: str | None
) -> str | None:
    """User_id passed into SpeakTransport.deliver (pre-normalize intent).

    Group: always None (KD20 — never stamp member or operator).
    DM: peer from ``dm:<peer>`` suffix (assistant peer stamp / legacy filters).
    Null conversation should not reach here after KD3 success.
    """
    if isinstance(conversation_id, str) and conversation_id.startswith("group:"):
        return None
    if isinstance(conversation_id, str) and conversation_id.startswith("dm:"):
        peer = conversation_id[3:].strip()
        return peer if peer else speaker_id
    return speaker_id


def _resolve_speak_attachments(
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    user_id: str,
) -> tuple[list[Any] | None, str | None]:
    """Parse attachment_ids + path attachments → media Attachment list.

    Returns ``(list_or_None, error_reason)``. Empty / absent media → (None, None).
    """
    has_ids = "attachment_ids" in args and args.get("attachment_ids") is not None
    has_paths = "attachments" in args and args.get("attachments") is not None
    if not has_ids and not has_paths:
        return None, None

    raw_ids = args.get("attachment_ids") if has_ids else None
    raw_paths = args.get("attachments") if has_paths else None

    if has_ids and not isinstance(raw_ids, list):
        return None, "invalid_attachment_ids"
    if has_paths and not isinstance(raw_paths, list):
        return None, "invalid_attachments"

    # Nothing to attach after null/empty lists.
    ids: list[str] = []
    if isinstance(raw_ids, list):
        for item in raw_ids:
            if not isinstance(item, str) or not item.strip():
                return None, "invalid_attachment_ids"
            ids.append(item.strip())
    path_specs: list[dict[str, Any]] = []
    if isinstance(raw_paths, list):
        for item in raw_paths:
            if not isinstance(item, dict):
                return None, "invalid_attachments"
            path_specs.append(item)

    if not ids and not path_specs:
        return None, None

    from elyra.media.ingest import IngestError, prepare_speak_attachments

    try:
        atts = prepare_speak_attachments(
            attachment_ids=ids or None,
            path_specs=path_specs or None,
            paths=ctx.paths,
            sandbox=ctx.sandbox,
            uploader_user_id=user_id,
        )
    except IngestError as exc:
        return None, exc.reason
    return atts, None


def _resolve_transport(ctx: ToolContext) -> SpeakTransport:
    """Prefer injected ctx.speak; else construct from paths (or extras)."""
    if ctx.speak is not None:
        return ctx.speak
    extra = ctx.extras.get("speak")
    if isinstance(extra, SpeakTransport):
        return extra
    return SpeakTransport(ctx.paths)


def _resolve_timers(ctx: ToolContext) -> TimerService | None:
    """Prefer injected ctx.timers; else extras['timers'] when TimerService."""
    if ctx.timers is not None:
        return ctx.timers
    extra = ctx.extras.get("timers")
    if isinstance(extra, TimerService):
        return extra
    return None


def _parse_choices(raw: Any) -> tuple[list[str], str | None]:
    """Return (choices, error_reason). Absent / null → empty list."""
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return [], "invalid_choices"
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            return [], "invalid_choices"
        text = item.strip()
        if text:
            out.append(text)
    return out, None


def _parse_timeout_seconds(
    args: dict[str, Any],
    ctx: ToolContext,
    *,
    free_text: bool = False,
) -> tuple[int, str | None]:
    """Resolve timeout_seconds from args or settings.

    Free-text waits (no choices) use ``free_text_timeout_seconds`` when the
    model omits an explicit value so open-ended replies get a longer floor.
    """
    if "timeout_seconds" not in args or args.get("timeout_seconds") is None:
        return _default_timeout_seconds(ctx, free_text=free_text), None
    raw = args["timeout_seconds"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0, "invalid_timeout"
    if isinstance(raw, float) and not raw.is_integer():
        return 0, "invalid_timeout"
    value = int(raw)
    if value < 0:
        return 0, "invalid_timeout"
    return value, None


def _default_timeout_seconds(ctx: ToolContext, *, free_text: bool = False) -> int:
    if ctx.settings is not None:
        try:
            wait = ctx.settings.wait
            if free_text:
                return int(wait.free_text_timeout_seconds)
            return int(wait.default_timeout_seconds)
        except (AttributeError, TypeError, ValueError):
            pass
    return _DEFAULT_FREE_TEXT_TIMEOUT_S if free_text else _DEFAULT_WAIT_TIMEOUT_S


def _resolve_wake_at(args: dict[str, Any]) -> tuple[str | None, str | None]:
    """Resolve absolute wake_at ISO from wake_at or delay_seconds.

    Returns (wake_at_iso, error_reason). Exactly one of the two args required.
    """
    has_wake = "wake_at" in args and args.get("wake_at") is not None
    has_delay = "delay_seconds" in args and args.get("delay_seconds") is not None

    if has_wake and has_delay:
        return None, "ambiguous_when"
    if not has_wake and not has_delay:
        return None, "missing_when"

    if has_wake:
        raw = args["wake_at"]
        if not isinstance(raw, str) or not raw.strip():
            return None, "invalid_wake_at"
        # Validate parseable ISO (TimerService also validates).
        try:
            parse_utc(raw.strip())
        except (ValueError, TypeError):
            return None, "invalid_wake_at"
        return raw.strip(), None

    delay = args["delay_seconds"]
    if isinstance(delay, bool) or not isinstance(delay, (int, float)):
        return None, "invalid_delay_seconds"
    if float(delay) < 0:
        return None, "invalid_delay_seconds"
    wake_dt = datetime.now(UTC) + timedelta(seconds=float(delay))
    wake_at = wake_dt.isoformat().replace("+00:00", "Z")
    return wake_at, None


def _optional_str_id(args: dict[str, Any], key: str) -> str | None | bool:
    """Return optional non-blank str id, None if absent, False if invalid type."""
    if key not in args or args.get(key) is None:
        return None
    raw = args[key]
    if not isinstance(raw, str):
        return False
    text = raw.strip()
    if not text:
        return None
    return text


# ---------------------------------------------------------------------------
# Group topology helpers (create_group / update_group)
# ---------------------------------------------------------------------------


def _tool_err(reason: str, *, detail: str | None = None) -> ToolResult:
    """Fail closed with stable error_reason on result and payload (KD-T7)."""
    payload: dict[str, Any] = {"error_reason": reason}
    if detail:
        payload["detail"] = detail
    return ToolResult(ok=False, payload=payload, error_reason=reason)


def _map_store_error(exc: BaseException) -> ToolResult:
    """Map ConversationsStore exceptions to stable error_reason strings."""
    if isinstance(exc, KeyError):
        return _tool_err("conversation_not_found", detail=str(exc))
    if isinstance(exc, OSError):
        return _tool_err("store_error", detail=str(exc))
    msg = str(exc)
    low = msg.lower()
    if "already exists" in low:
        return _tool_err("conversation_exists", detail=msg)
    if "no update fields" in low:
        return _tool_err("no_fields_to_update", detail=msg)
    if "group name cannot be null" in low or "name must be" in low:
        return _tool_err("invalid_name", detail=msg)
    if "members must be" in low:
        # Empty list → missing_members; other member shape → invalid_members
        if "non-empty" in low:
            return _tool_err("missing_members", detail=msg)
        return _tool_err("invalid_members", detail=msg)
    if "description must be" in low:
        return _tool_err("invalid_description", detail=msg)
    if "must be group" in low or "invalid conversation_id" in low:
        return _tool_err("invalid_conversation_id", detail=msg)
    if "dm members must be" in low:
        return _tool_err("not_a_group", detail=msg)
    return _tool_err("invalid_args", detail=msg)


def _clean_tool_members(
    members: Any,
) -> tuple[list[str] | None, str | None, str | None]:
    """Strip + dedupe + validate members (REST parity without HTTP side effects).

    Returns ``(clean_ids, error_reason, detail)``.
    """
    from elyra.identity.layout import validate_user_id

    if members is None:
        return None, "missing_members", None
    if not isinstance(members, list):
        return None, "invalid_members", "members must be a list"
    if not members:
        return None, "missing_members", None
    clean: list[str] = []
    seen: set[str] = set()
    for m in members:
        if not isinstance(m, str):
            return None, "invalid_members", "members must be user_id strings"
        try:
            uid = validate_user_id(m.strip())  # strip before validate (REST)
        except ValueError as exc:
            return None, "invalid_user_id", f"{m!r}: {exc}"
        if uid not in seen:
            seen.add(uid)
            clean.append(uid)
    if not clean:
        return None, "missing_members", None
    return clean, None, None


def _actor_user_id(ctx: ToolContext) -> str | None:
    """Snapshot ctx.user_id for result provenance only (not a member claim)."""
    if ctx.user_id and str(ctx.user_id).strip():
        return str(ctx.user_id).strip()
    return None


def _optional_member_labels(
    ctx: ToolContext, members: list[Any]
) -> dict[str, str] | None:
    """Soft display labels from UsersStore when available (extras['users'])."""
    users = ctx.extras.get("users") if isinstance(ctx.extras, dict) else None
    if users is None or not hasattr(users, "display_label"):
        return None
    labels: dict[str, str] = {}
    for m in members:
        if not isinstance(m, str) or not m.strip():
            continue
        try:
            labels[m] = str(users.display_label(m))
        except (TypeError, ValueError, AttributeError, KeyError):
            continue
    return labels or None
