"""User input routing state machine.

Scope: pure decide where chat/wait text goes given phase + pending wait.
In scope: idle / in_moment / waiting matrix; wait_api vs free-text.
Out of scope: wake enqueue, interject buffer storage, HTTP, do-loop.
"""

from __future__ import annotations

from typing import Any, Mapping

PHASE_IDLE = "idle"
PHASE_IN_MOMENT = "in_moment"
PHASE_WAITING = "waiting"

ROUTE_INTERJECT = "interject"
ROUTE_WAIT_REPLY = "wait_reply"
ROUTE_USER_MESSAGE = "user_message"

KNOWN_PHASES = frozenset({PHASE_IDLE, PHASE_IN_MOMENT, PHASE_WAITING})


def _pending_status(pending_wait: Mapping[str, Any]) -> str:
    return str(pending_wait.get("status") or "pending")


def pending_wait_matches_user(
    pending_wait: Mapping[str, Any] | None,
    user_id: str,
) -> bool:
    """True when a durable pending wait is owned by ``user_id``."""
    if not pending_wait:
        return False
    if _pending_status(pending_wait) != "pending":
        return False
    return str(pending_wait.get("user_id") or "") == str(user_id)


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
    - pending wait for user and (``from_wait_api`` or ``phase == waiting``)
      → wait_reply
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

    matches = pending_wait_matches_user(pending_wait, user_id)
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
