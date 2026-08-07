"""Mid-moment interjection buffer.

Scope: bounded buffer for operator notes while phase == in_moment;
drain, clear, overflow signal.
In scope: message/char caps; try_add returns full reason.
Out of scope: wake enqueue, phase machine, HTTP, do-loop drain policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Design Stretch 1: first limit hit wins.
INTERJECT_MAX_MESSAGES = 8
INTERJECT_MAX_CHARS = 16_000

REASON_BUFFER_FULL = "interjection_buffer_full"


@dataclass
class InterjectItem:
    content: str
    user_id: str
    message_id: str | None = None
    # Social address stamps (PR3b / §3.6): overflow enqueue must retain both.
    conversation_id: str | None = None
    social_kind: str | None = None


@dataclass
class InterjectBuffer:
    """Bounded mid-moment interjection buffer (not wake-queue items)."""

    max_messages: int = INTERJECT_MAX_MESSAGES
    max_chars: int = INTERJECT_MAX_CHARS
    items: list[InterjectItem] = field(default_factory=list)
    chars: int = 0

    def try_add(self, item: InterjectItem) -> tuple[bool, str | None]:
        """Attempt to buffer ``item``.

        Returns ``(True, None)`` on success, or
        ``(False, "interjection_buffer_full")`` when a cap is hit.
        """
        n = len(item.content)
        if len(self.items) >= self.max_messages:
            return False, REASON_BUFFER_FULL
        if self.chars + n > self.max_chars:
            return False, REASON_BUFFER_FULL
        self.items.append(item)
        self.chars += n
        return True, None

    def drain(self) -> list[InterjectItem]:
        """Remove and return all buffered items."""
        out = list(self.items)
        self.items.clear()
        self.chars = 0
        return out

    def clear(self) -> None:
        self.items.clear()
        self.chars = 0

    @property
    def depth(self) -> int:
        return len(self.items)
