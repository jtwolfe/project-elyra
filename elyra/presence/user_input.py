"""User input routing state machine.

Scope: pure decide where chat/wait text goes given phase + pending wait.
In scope: idle / in_moment / waiting matrix; wait_api vs free-text;
group wait match (KD12) via conversation membership + session binding.
Out of scope: wake enqueue, interject buffer storage, HTTP, do-loop.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

PHASE_IDLE = "idle"
PHASE_IN_MOMENT = "in_moment"
PHASE_WAITING = "waiting"

ROUTE_INTERJECT = "interject"
ROUTE_WAIT_REPLY = "wait_reply"
ROUTE_USER_MESSAGE = "user_message"

KNOWN_PHASES = frozenset({PHASE_IDLE, PHASE_IN_MOMENT, PHASE_WAITING})


class _ConversationsLookup(Protocol):
    def get(self, conversation_id: str) -> dict[str, Any] | None: ...


def _pending_status(pending_wait: Mapping[str, Any]) -> str:
    return str(pending_wait.get("status") or "pending")


def wait_matches(
    session_user: str,
    wait: Mapping[str, Any] | None,
    conversations: _ConversationsLookup | None = None,
    *,
    session_conversation_id: str | None = None,
) -> bool:
    """True when ``session_user`` may answer the pending wait (KD12).

    - **DM / null conversation:** exact ``wait.user_id == session_user``.
    - **Group:** ``session_user ∈ members(wait.conversation_id)`` **and**
      ``session_conversation_id == wait.conversation_id``. A member viewing
      Private Chat (``dm:<self>``) does **not** match until they PUT session
      to that group (T9).
    - Unknown group or missing store → fail closed (False).
    """
    if not wait:
        return False
    if _pending_status(wait) != "pending":
        return False

    cid_raw = wait.get("conversation_id")
    cid: str | None = None
    if isinstance(cid_raw, str) and cid_raw.strip():
        cid = cid_raw.strip()

    if cid is None or cid.startswith("dm:"):
        return str(wait.get("user_id") or "") == str(session_user)

    if cid.startswith("group:"):
        members: list[str] = []
        if conversations is not None:
            rec = conversations.get(cid)
            if isinstance(rec, dict):
                raw_members = rec.get("members") or []
                if isinstance(raw_members, list):
                    members = [str(m) for m in raw_members]
        if str(session_user) not in members:
            return False
        # Mandatory after KD18: session must be bound to this group.
        sess_cid = (
            session_conversation_id.strip()
            if isinstance(session_conversation_id, str)
            and session_conversation_id.strip()
            else None
        )
        if sess_cid != cid:
            return False
        return True

    # Unknown conversation shape — safe default exact user match.
    return str(wait.get("user_id") or "") == str(session_user)


def pending_wait_matches_user(
    pending_wait: Mapping[str, Any] | None,
    user_id: str,
    *,
    session_conversation_id: str | None = None,
    conversations: _ConversationsLookup | None = None,
) -> bool:
    """True when a durable pending wait is answerable by ``user_id``.

    Thin wrapper over :func:`wait_matches` (KD12). Without group context,
    behaves as legacy exact ``wait.user_id == user_id``.
    """
    return wait_matches(
        user_id,
        pending_wait,
        conversations,
        session_conversation_id=session_conversation_id,
    )


def wait_id_of(pending_wait: Mapping[str, Any] | None) -> str | None:
    """Extract wait id from a pending_wait snapshot dict."""
    if not pending_wait:
        return None
    raw = pending_wait.get("id") or pending_wait.get("wait_id")
    if raw is None or raw == "":
        return None
    return str(raw)


def resolve_user_input(
    content: str,
    user_id: str,
    choice: str | None = None,
    *,
    from_wait_api: bool = False,
    phase: str,
    pending_wait: Mapping[str, Any] | None = None,
    has_attachments: bool = False,
    session_conversation_id: str | None = None,
    conversations: _ConversationsLookup | None = None,
) -> dict[str, Any]:
    """Pure routing decision for operator chat / wait reply.

    Returns a dict (no side effects):

    - ``routed``: ``interject`` | ``wait_reply`` | ``user_message``
    - ``ok``: False only when input is unusable for the chosen route
    - ``reason``: optional machine-readable failure
    - ``cancel_stale_wait``: idle path should cancel pending wait for user
    - ``answer_wait_id``: wait id when ``routed == wait_reply``

    Matrix (design Stretch 1):

    - ``in_moment`` → interject buffer
    - pending wait matching user/session and (``from_wait_api`` or
      ``phase == waiting``) → wait_reply
    - else (idle) → user_message; cancel any stale pending wait for user

    Empty/whitespace ``content`` is allowed when ``has_attachments`` is True
    (media-only user send / interject; R1b). Wait reply still needs text or choice.
    """
    text = content.strip() if isinstance(content, str) else ""
    choice_s = choice.strip() if isinstance(choice, str) else (
        str(choice) if choice is not None else ""
    )

    if phase == PHASE_IN_MOMENT:
        if not text and not has_attachments:
            # Interject needs free-text and/or attachments (choice alone is wait UX).
            return {
                "routed": ROUTE_INTERJECT,
                "ok": False,
                "reason": "empty_content",
                "cancel_stale_wait": False,
                "answer_wait_id": None,
            }
        return {
            "routed": ROUTE_INTERJECT,
            "ok": True,
            "cancel_stale_wait": False,
            "answer_wait_id": None,
        }

    matches = wait_matches(
        user_id,
        pending_wait,
        conversations,
        session_conversation_id=session_conversation_id,
    )
    wid = wait_id_of(pending_wait) if matches else None

    if matches and (from_wait_api or phase == PHASE_WAITING):
        # Wait answer still requires text or choice (attachments alone are not a reply).
        if not text and not choice_s:
            return {
                "routed": ROUTE_WAIT_REPLY,
                "ok": False,
                "reason": "empty_content",
                "cancel_stale_wait": False,
                "answer_wait_id": wid,
            }
        return {
            "routed": ROUTE_WAIT_REPLY,
            "ok": True,
            "cancel_stale_wait": False,
            "answer_wait_id": wid,
        }

    # idle (or waiting without a matching wait for this user — defensive)
    if not text and not has_attachments:
        return {
            "routed": ROUTE_USER_MESSAGE,
            "ok": False,
            "reason": "empty_content",
            "cancel_stale_wait": False,
            "answer_wait_id": None,
        }
    return {
        "routed": ROUTE_USER_MESSAGE,
        "ok": True,
        "cancel_stale_wait": matches,
        "answer_wait_id": None,
    }
