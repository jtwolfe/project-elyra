"""Minimal presence worker: drain user messages and run a simple do-loop.

Scope: single-thread worker; one chat completion per user message for now.
In scope: wake on messages, call LLM, store assistant reply, moment ids.
Out of scope: full tool registry, multi-hop tools (next slices).
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from elyra.config import ElyraPaths
from elyra.llm.client import ChatClient
from elyra.llm.constants import GENERATION_MAX_TOKENS
from elyra.messages import append_message, list_messages

_LOG = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Elyra, a communal digital teammate.
Follow self ≠ user: you are Elyra; the human is a separate person.
Be clear and useful. Prefer honesty over fluff.
When you cannot use tools yet, say what you would do next.
"""


@dataclass
class WakeItem:
    kind: str  # user_message
    message_id: str
    user_id: str
    content: str


class PresenceWorker:
    """Single worker: queue → moment (simple chat for bootstrap)."""

    def __init__(
        self,
        *,
        paths: ElyraPaths,
        client: ChatClient,
        stop_event: threading.Event,
        poll_seconds: float = 0.1,
    ) -> None:
        self.paths = paths
        self.client = client
        self._stop = stop_event
        self._poll = poll_seconds
        self._queue: queue.Queue[WakeItem] = queue.Queue()
        self._busy = False
        self._last_error: str | None = None
        self._active_moment: str | None = None

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def active_moment_id(self) -> str | None:
        return self._active_moment

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    def enqueue_user_message(
        self, content: str, *, user_id: str = "operator", message_id: str | None = None
    ) -> str:
        mid = message_id or str(uuid.uuid4())
        self._queue.put(
            WakeItem(
                kind="user_message",
                message_id=mid,
                user_id=user_id,
                content=content,
            )
        )
        return mid

    def run(self) -> None:
        _LOG.info("presence worker started")
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=self._poll)
            except queue.Empty:
                continue
            try:
                self._run_moment(item)
            except Exception as exc:  # noqa: BLE001 — keep worker alive
                _LOG.exception("moment failed: %s", exc)
                self._last_error = f"{type(exc).__name__}: {exc}"
            finally:
                self._busy = False
                self._active_moment = None
        _LOG.info("presence worker stopped")

    def _run_moment(self, item: WakeItem) -> None:
        self._busy = True
        moment_id = str(uuid.uuid4())
        self._active_moment = moment_id
        self._last_error = None

        history = list_messages(limit=40, paths=self.paths)
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for row in history:
            role = row.get("role")
            if role not in ("user", "assistant"):
                continue
            content = row.get("content") or ""
            if not content:
                continue
            messages.append({"role": role, "content": content})

        # Ensure the triggering message is last (already appended by API).
        if not messages or messages[-1].get("content") != item.content:
            messages.append({"role": "user", "content": item.content})

        result = self.client.chat_completion(
            messages,
            max_tokens=min(8192, GENERATION_MAX_TOKENS),
            reasoning=True,
        )
        append_message(
            "assistant",
            result.content or "(empty response)",
            user_id=item.user_id,
            reasoning=result.reasoning_content,
            moment_id=moment_id,
            paths=self.paths,
        )
