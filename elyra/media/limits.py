"""Process-local STT/TTS rate limits (PR10).

In-process sliding windows (no redis): default **10 STT/min**, **20 TTS/min**.
Exceed → callers return HTTP **429** ``{ok:false, reason:"rate_limited"}``.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

# Product defaults (design security table).
STT_MAX_PER_MINUTE = 10
TTS_MAX_PER_MINUTE = 20
_WINDOW_S = 60.0


@dataclass
class SlidingWindowLimiter:
    """Allow at most ``max_events`` events inside a rolling ``window_s``."""

    max_events: int
    window_s: float = _WINDOW_S
    _times: Deque[float] = field(default_factory=deque, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def allow(self, *, now: float | None = None) -> bool:
        """Return True and record an event if under the limit; else False."""
        t = time.monotonic() if now is None else now
        with self._lock:
            cutoff = t - self.window_s
            while self._times and self._times[0] <= cutoff:
                self._times.popleft()
            if len(self._times) >= self.max_events:
                return False
            self._times.append(t)
            return True

    def reset(self) -> None:
        with self._lock:
            self._times.clear()

    def remaining(self, *, now: float | None = None) -> int:
        t = time.monotonic() if now is None else now
        with self._lock:
            cutoff = t - self.window_s
            while self._times and self._times[0] <= cutoff:
                self._times.popleft()
            return max(0, self.max_events - len(self._times))


# Process-wide limiters (shared across ThreadingHTTPServer handlers).
_stt_limiter = SlidingWindowLimiter(STT_MAX_PER_MINUTE, _WINDOW_S)
_tts_limiter = SlidingWindowLimiter(TTS_MAX_PER_MINUTE, _WINDOW_S)


def allow_stt(*, now: float | None = None) -> bool:
    """True if this STT request is under the process-local rate limit."""
    return _stt_limiter.allow(now=now)


def allow_tts(*, now: float | None = None) -> bool:
    """True if this TTS request is under the process-local rate limit."""
    return _tts_limiter.allow(now=now)


def stt_remaining(*, now: float | None = None) -> int:
    return _stt_limiter.remaining(now=now)


def tts_remaining(*, now: float | None = None) -> int:
    return _tts_limiter.remaining(now=now)


def reset_rate_limits_for_tests() -> None:
    """Clear both limiters (tests only)."""
    _stt_limiter.reset()
    _tts_limiter.reset()


RATE_LIMITED_PAYLOAD: dict[str, object] = {
    "ok": False,
    "error": "rate limited",
    "reason": "rate_limited",
}
