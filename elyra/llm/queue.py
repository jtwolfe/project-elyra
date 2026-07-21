"""Serialize access to llama-server — one HTTP operation at a time.

Scope: gate for chat/embed so UI and worker never race the server.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class LlamaQueueShutdown(RuntimeError):
    """Raised when the gate is shutting down."""


class LlamaServerGate:
    """One llama-server HTTP operation at a time."""

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
                raise LlamaQueueShutdown("llama gate is shut down")
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
