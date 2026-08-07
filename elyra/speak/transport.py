"""Glass delivery transport for the speak product act.

Scope: write user-visible assistant rows via messages.append_message only.
In scope: deliver(text, user_id, conversation_id?, moment_id, attachments?) →
SpeakDelivery with transport status; optional outbound media (PR8 / KD8).
Out of scope: wait_user, schedule_wake, do-loop mark_spoke, beat tape.

Ownership: this module is the sole production path for assistant glass rows.
Bare one-shot content writes (former loop/worker) have been removed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from elyra.config import ElyraPaths, resolve_paths
from elyra.messages import Message, append_message


@dataclass(frozen=True)
class SpeakDelivery:
    """Outcome of attempting to deliver speech to glass/chat.

    ``ok`` mirrors transport success. Callers map this onto ToolResult and
    set ``counts_as_speak`` only when ``ok`` is True.

    ``user_id`` is Optional (KD20): group delivers use ``None`` (JSON null);
    DM/legacy use a non-null peer stamp. Never coerce group success to
    ``\"operator\"``.
    """

    ok: bool
    text: str
    user_id: str | None
    message_id: str | None = None
    moment_id: str | None = None
    reason: str | None = None
    conversation_id: str | None = None
    # Outbound media inventory written on the assistant row (PR8).
    attachments: tuple[dict[str, Any], ...] = ()

    def as_payload(self) -> dict[str, Any]:
        """Model-visible transport status (always includes transport_ok).

        ``user_id`` is always present — JSON ``null`` when group (T15).
        ``conversation_id`` included when set.
        """
        payload: dict[str, Any] = {
            "transport_ok": self.ok,
            "text": self.text,
            "user_id": self.user_id,  # may be None → JSON null
        }
        if self.message_id is not None:
            payload["message_id"] = self.message_id
        if self.moment_id is not None:
            payload["moment_id"] = self.moment_id
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.conversation_id is not None:
            payload["conversation_id"] = self.conversation_id
        if self.attachments:
            payload["attachments"] = list(self.attachments)
            payload["attachment_ids"] = [
                a.get("id") for a in self.attachments if isinstance(a, dict) and a.get("id")
            ]
        return payload


class SpeakTransport:
    """Deliver speak acts to the chat glass (messages.jsonl).

    Construct with paths (or default resolve_paths). Injectable for tests.
    """

    def __init__(
        self,
        paths: ElyraPaths | None = None,
        *,
        append: Any | None = None,
    ) -> None:
        """Parameters
        ----------
        paths:
            Elyra home paths; data_dir holds messages.jsonl.
        append:
            Optional callable with the same signature as messages.append_message
            (tests inject a failing double). Defaults to real append_message.
        """
        self._paths = paths or resolve_paths()
        self._append = append if append is not None else append_message

    @property
    def paths(self) -> ElyraPaths:
        return self._paths

    def deliver(
        self,
        text: str,
        *,
        user_id: str | None = "operator",
        conversation_id: str | None = None,
        moment_id: str | None = None,
        reasoning: str = "",
        attachments: list[Any] | None = None,
    ) -> SpeakDelivery:
        """Append an assistant glass row for ``text`` (caption required).

        Optional ``attachments`` is a list of Attachment objects or plain dicts
        already registered in the media store (unbound). After a successful
        append, each id is bound to the new message id.

        ``conversation_id`` stamps the social address on the glass row (C12).
        Group conversations force ``user_id=None`` (KD20) regardless of the
        incoming user_id stamp.

        Returns SpeakDelivery(ok=False, reason=…) without writing when input is
        invalid. Empty/whitespace ``text`` is always rejected — even when
        attachments are present (KD8 caption policy). On I/O or append failure,
        returns ok=False with reason — never raises for expected transport faults.
        """
        cid = _normalize_conversation_id(conversation_id)
        # Conversation-aware normalize BEFORE append and before SpeakDelivery
        # (KD20). Group → None; DM blank → peer; null conversation → operator.
        uid = _normalize_user_id(user_id, conversation_id=cid)
        att_dicts, att_err = _normalize_delivery_attachments(attachments)
        if att_err is not None:
            return SpeakDelivery(
                ok=False,
                text=text if isinstance(text, str) else "",
                user_id=uid,
                moment_id=moment_id,
                conversation_id=cid,
                reason=att_err,
            )
        if not isinstance(text, str):
            return SpeakDelivery(
                ok=False,
                text="",
                user_id=uid,
                moment_id=moment_id,
                conversation_id=cid,
                reason="invalid_text",
            )
        # Preserve intentional internal whitespace; reject empty / whitespace-only.
        # Caption required even with attachments (design R1 / KD8 / KD19).
        if not text.strip():
            return SpeakDelivery(
                ok=False,
                text=text,
                user_id=uid,
                moment_id=moment_id,
                conversation_id=cid,
                reason="empty_text",
                attachments=tuple(att_dicts),
            )

        try:
            msg = self._append(
                "assistant",
                text,
                user_id=uid,
                reasoning=reasoning or "",
                moment_id=moment_id,
                conversation_id=cid,
                attachments=att_dicts if att_dicts else None,
                paths=self._paths,
            )
            # Bad doubles / adapters may return non-Message without raising —
            # keep attribute access inside the fault boundary (never raise).
            if not isinstance(msg, Message):
                return SpeakDelivery(
                    ok=False,
                    text=text,
                    user_id=uid,
                    moment_id=moment_id,
                    conversation_id=cid,
                    reason="append_failed:invalid_return",
                    attachments=tuple(att_dicts),
                )
            if att_dicts:
                bind_err = _bind_attachments(att_dicts, msg.id, paths=self._paths)
                if bind_err is not None:
                    return SpeakDelivery(
                        ok=False,
                        text=text,
                        user_id=uid,
                        message_id=msg.id,
                        moment_id=moment_id or msg.moment_id,
                        conversation_id=cid,
                        reason=bind_err,
                        attachments=tuple(att_dicts),
                    )
            # Best-effort activity stamp for conversation list ordering.
            if cid is not None:
                _touch_conversation_activity(cid, paths=self._paths)
            return SpeakDelivery(
                ok=True,
                text=text,
                user_id=uid,
                message_id=msg.id,
                moment_id=moment_id or msg.moment_id,
                conversation_id=cid,
                reason=None,
                attachments=tuple(att_dicts),
            )
        except Exception as exc:  # noqa: BLE001 — transport failure → structured result
            return SpeakDelivery(
                ok=False,
                text=text,
                user_id=uid,
                moment_id=moment_id,
                conversation_id=cid,
                reason=f"append_failed:{type(exc).__name__}",
                attachments=tuple(att_dicts),
            )


def _normalize_conversation_id(conversation_id: str | None) -> str | None:
    if not isinstance(conversation_id, str):
        return None
    stripped = conversation_id.strip()
    return stripped or None


def _normalize_user_id(
    user_id: str | None,
    *,
    conversation_id: str | None = None,
) -> str | None:
    """Conversation-aware user_id for assistant glass rows (KD20).

    | conversation_id | incoming user_id | stored |
    | group:…         | any              | None   |
    | dm:…            | blank/None       | peer from suffix (else operator) |
    | dm:…            | non-blank        | that string (peer stamp) |
    | null            | blank/None       | \"operator\" (legacy / solo) |
    | null            | non-blank        | that string |

    Failure paths that know a group conversation also leave user_id=None —
    never re-normalize group failures to operator after a group-aware path.
    """
    cid = conversation_id
    if isinstance(cid, str) and cid.startswith("group:"):
        return None

    stripped: str | None
    if user_id is None:
        stripped = None
    elif not isinstance(user_id, str):
        stripped = None
    else:
        s = user_id.strip()
        stripped = s if s else None

    if isinstance(cid, str) and cid.startswith("dm:"):
        if stripped is not None:
            return stripped
        peer = cid[3:].strip()
        return peer if peer else "operator"

    # null / missing conversation_id — legacy solo / tool path
    if stripped is not None:
        return stripped
    return "operator"


def _touch_conversation_activity(
    conversation_id: str, *, paths: ElyraPaths
) -> None:
    """Best-effort ConversationsStore.touch_activity (never raises to callers)."""
    try:
        from elyra.conversations import ConversationsStore

        ConversationsStore(paths).touch_activity(conversation_id)
    except Exception:  # noqa: BLE001 — soft; glass row already written
        pass


def _normalize_delivery_attachments(
    attachments: list[Any] | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Coerce Attachment/dict list → plain dicts; error reason or None."""
    if attachments is None:
        return [], None
    if not isinstance(attachments, list):
        return [], "invalid_attachments"
    out: list[dict[str, Any]] = []
    for item in attachments:
        if hasattr(item, "to_dict"):
            d = item.to_dict()  # type: ignore[union-attr]
            if not isinstance(d, dict) or not d.get("id"):
                return [], "invalid_attachments"
            out.append(d)
        elif isinstance(item, dict):
            if not item.get("id"):
                return [], "invalid_attachments"
            out.append(dict(item))
        else:
            return [], "invalid_attachments"
    return out, None


def _bind_attachments(
    att_dicts: list[dict[str, Any]],
    message_id: str,
    *,
    paths: ElyraPaths,
) -> str | None:
    """Bind each attachment id to message_id. Return error reason or None.

    Best-effort: bind failures after append are reported as transport failure
    (message may already exist with attachments inventory — meta bind is
    host-truth for re-use; glass row still has the snapshot dicts).
    """
    try:
        from elyra.media import MediaStore

        store = MediaStore(paths)
        for d in att_dicts:
            aid = d.get("id")
            if not isinstance(aid, str) or not aid:
                return "invalid_attachments"
            store.bind_message(aid, message_id)
    except FileNotFoundError:
        return "attachment_not_found"
    except ValueError as exc:
        # already bound to different message, invalid id, etc.
        msg = str(exc)
        if "already bound" in msg:
            return "attachment_bound"
        return "invalid_attachments"
    except OSError as exc:
        return f"bind_failed:{type(exc).__name__}"
    return None
