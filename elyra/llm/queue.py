"""Serialize access to a chat backend — one HTTP operation at a time.

Scope: single-flight gate so UI and worker never race the inference endpoint.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class ChatGateShutdown(RuntimeError):
    """Raised when the gate is shutting down."""


class ChatRequestGate:
    """One chat HTTP operation at a time (provider-neutral single-flight)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._busy = False
        self._label: str | None = None
        self._stop = False

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    @property
    def current_label(self) -> str | None:
        with self._lock:
            return self._label

    def submit(self, label: str, fn: Callable[[], T]) -> T:
        with self._lock:
            if self._stop:
                raise ChatGateShutdown("chat gate is shut down")
            self._busy = True
            self._label = label
        try:
            return fn()
        finally:
            with self._lock:
                self._busy = False
                self._label = None

    def shutdown(self) -> None:
        with self._lock:
            self._stop = True
