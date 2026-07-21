"""Glass delivery transport for the speak product act.

Scope: write user-visible assistant rows via messages.append_message only.
In scope: deliver(text, user_id, moment_id) → SpeakDelivery with transport status.
Out of scope: wait_user, schedule_wake, do-loop mark_spoke, beat tape.

Ownership: this module is the sole path for assistant glass rows in the product
tool path. Loop/worker bare content writes are removed in a later cutover PR.
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
    """

    ok: bool
    text: str
    user_id: str
    message_id: str | None = None
    moment_id: str | None = None
    reason: str | None = None

    def as_payload(self) -> dict[str, Any]:
        """Model-visible transport status (always includes transport_ok)."""
        payload: dict[str, Any] = {
            "transport_ok": self.ok,
            "text": self.text,
            "user_id": self.user_id,
        }
        if self.message_id is not None:
            payload["message_id"] = self.message_id
        if self.moment_id is not None:
            payload["moment_id"] = self.moment_id
        if self.reason is not None:
            payload["reason"] = self.reason
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
        moment_id: str | None = None,
        reasoning: str = "",
    ) -> SpeakDelivery:
        """Append an assistant glass row for ``text``.

        Returns SpeakDelivery(ok=False, reason=…) without writing when input is
        invalid. On I/O or append failure, returns ok=False with reason — never
        raises for expected transport faults.
        """
        uid = _normalize_user_id(user_id)
        if not isinstance(text, str):
            return SpeakDelivery(
                ok=False,
                text="",
                user_id=uid,
                moment_id=moment_id,
                reason="invalid_text",
            )
        # Preserve intentional internal whitespace; reject empty / whitespace-only.
        if not text.strip():
            return SpeakDelivery(
                ok=False,
                text=text,
                user_id=uid,
                moment_id=moment_id,
                reason="empty_text",
            )

        try:
            msg = self._append(
                "assistant",
                text,
                user_id=uid,
                reasoning=reasoning or "",
                moment_id=moment_id,
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
                    reason="append_failed:invalid_return",
                )
            return SpeakDelivery(
                ok=True,
                text=text,
                user_id=uid,
                message_id=msg.id,
                moment_id=moment_id or msg.moment_id,
                reason=None,
            )
        except Exception as exc:  # noqa: BLE001 — transport failure → structured result
            return SpeakDelivery(
                ok=False,
                text=text,
                user_id=uid,
                moment_id=moment_id,
                reason=f"append_failed:{type(exc).__name__}",
            )


def _normalize_user_id(user_id: str | None) -> str:
    """Default blank/None to operator; strip whitespace."""
    if user_id is None:
        return "operator"
    if not isinstance(user_id, str):
        return "operator"
    stripped = user_id.strip()
    return stripped if stripped else "operator"
