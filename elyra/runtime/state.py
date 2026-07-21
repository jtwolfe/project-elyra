"""Process-level runtime status for the Web UI."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RuntimeState:
    started_at: float = field(default_factory=time.time)
    llama_pid: int | None = None
    llama_ready: bool = False
    llama_error: str | None = None

    def set_llama(
        self,
        *,
        pid: int | None,
        ready: bool,
        error: str | None = None,
    ) -> None:
        self.llama_pid = pid
        self.llama_ready = ready
        self.llama_error = error

    def snapshot(self) -> dict[str, Any]:
        return {
            "uptime_s": round(time.time() - self.started_at, 1),
            "llama_pid": self.llama_pid,
            "llama_ready": self.llama_ready,
            "llama_error": self.llama_error,
        }


_state: RuntimeState | None = None
_lock = threading.Lock()


def set_runtime_state(state: RuntimeState) -> None:
    global _state
    with _lock:
        _state = state


def get_runtime_state() -> RuntimeState | None:
    with _lock:
        return _state
