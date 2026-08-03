"""EncodeWorker — continuous background corpus encode drain.

Presence-owned daemon thread (pattern after instrument.reaper). Owns bulk
drain while ``encode_owner=worker``. Soft-fail forever; never raises into
the presence do-loop.

Stop / join: ``_stop`` is set so the poll loop exits ASAP after the current
tick; the thread handle is **not** cleared until the thread is actually dead
(join success or later observation). Callers must treat ``is_alive()`` as the
source of truth so gap drain / restart cannot race a zombie tick.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

_LOG = logging.getLogger(__name__)

DEFAULT_POLL_S = 0.35
DEFAULT_JOIN_TIMEOUT_S = 2.0

# tick body: returns stats dict or None; must never raise to caller if careful,
# but worker still isolates exceptions.
EncodePollOnce = Callable[[], dict[str, Any] | None]


class EncodeWorker:
    """Daemon encode worker: Event wake + poll timeout → budgeted drain tick.

    Construct with a ``poll_once`` callable that performs one catch-up + scan
    + drain under budgets (PresenceWorker._encode_poll_once). The worker never
    takes PresenceWorker._lock and never touches browser.
    """

    def __init__(
        self,
        *,
        poll_once: EncodePollOnce,
        poll_s: float = DEFAULT_POLL_S,
        wake_event: threading.Event | None = None,
        stop_event: threading.Event | None = None,
        name: str = "elyra-encode-worker",
        generation: int = 0,
    ) -> None:
        self._poll_once = poll_once
        self._poll_s = max(0.05, float(poll_s))
        self._wake = wake_event if wake_event is not None else threading.Event()
        self._stop = stop_event if stop_event is not None else threading.Event()
        self._name = name
        self._generation = int(generation)
        self._thread: threading.Thread | None = None
        self._ticks: int = 0
        self._last_stats: dict[str, Any] | None = None
        self._last_tick_at: float | None = None
        self._last_error: str | None = None
        self._join_timed_out: bool = False

    @property
    def poll_s(self) -> float:
        return self._poll_s

    @property
    def wake_event(self) -> threading.Event:
        return self._wake

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def stop_requested(self) -> bool:
        return self._stop.is_set()

    @property
    def join_timed_out(self) -> bool:
        return self._join_timed_out

    def is_alive(self) -> bool:
        """True while the daemon thread still exists and has not exited.

        After a timed-out ``stop()``, this remains True until the thread dies
        so callers do not treat a zombie as dead (no dual drain / restart).
        """
        t = self._thread
        return t is not None and t.is_alive()

    def ticks(self) -> int:
        return self._ticks

    def last_stats(self) -> dict[str, Any] | None:
        return dict(self._last_stats) if self._last_stats else None

    def wake(self) -> None:
        """Signal the worker to run a tick soon (best-effort)."""
        self._wake.set()

    def start(self) -> None:
        """Start daemon thread (idempotent if already alive)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._join_timed_out = False
        self._thread = threading.Thread(
            target=self._run,
            name=self._name,
            daemon=True,
        )
        self._thread.start()
        _LOG.info(
            "memory.embed.encode_worker_start name=%s poll_s=%.3f gen=%d",
            self._name,
            self._poll_s,
            self._generation,
        )

    def stop(self, *, join_timeout_s: float = DEFAULT_JOIN_TIMEOUT_S) -> bool:
        """Signal stop, wake, and join.

        Returns True if the thread is dead after join (or was already dead).
        Returns False if the thread is still alive after the join timeout —
        the handle is **kept** so ``is_alive()`` stays True until exit.
        """
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is None:
            return True
        if t.is_alive():
            t.join(timeout=float(join_timeout_s))
        if t.is_alive():
            self._join_timed_out = True
            _LOG.warning(
                "memory.embed.encode_worker_join_timeout name=%s gen=%d "
                "timeout_s=%.1f (thread still alive; handle retained)",
                self._name,
                self._generation,
                float(join_timeout_s),
            )
            return False
        # Thread dead — clear handle only now.
        self._thread = None
        self._join_timed_out = False
        _LOG.info(
            "memory.embed.encode_worker_stop name=%s gen=%d",
            self._name,
            self._generation,
        )
        return True

    def tick_once(self) -> dict[str, Any] | None:
        """Run one poll body synchronously (tests / gap drain). Never raises."""
        if self._stop.is_set():
            return {"ok": 0, "reason": "stopped"}
        try:
            stats = self._poll_once()
            self._ticks += 1
            self._last_tick_at = time.monotonic()
            if isinstance(stats, dict):
                self._last_stats = dict(stats)
            return stats if isinstance(stats, dict) else None
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc) or type(exc).__name__
            _LOG.exception("encode worker tick_once failed")
            return None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if self._stop.is_set():
                    break
                stats = self._poll_once()
                self._ticks += 1
                self._last_tick_at = time.monotonic()
                if isinstance(stats, dict):
                    self._last_stats = dict(stats)
                    ok = int(stats.get("ok") or 0)
                    if ok > 0:
                        _LOG.info(
                            "memory.embed.encode_worker_tick ok=%s failed=%s "
                            "remaining=%s processed=%s gen=%d",
                            ok,
                            stats.get("failed"),
                            stats.get("remaining"),
                            stats.get("processed"),
                            self._generation,
                        )
                    else:
                        _LOG.debug(
                            "memory.embed.encode_worker_tick ok=0 remaining=%s "
                            "gen=%d",
                            stats.get("remaining"),
                            self._generation,
                        )
            except Exception:  # noqa: BLE001 — isolate; keep thread alive
                _LOG.exception("encode worker poll failed")
                self._last_error = "poll_failed"
            if self._stop.is_set():
                break
            # Wait for enqueue wake or poll timeout.
            self._wake.wait(timeout=self._poll_s)
            self._wake.clear()


__all__ = [
    "DEFAULT_JOIN_TIMEOUT_S",
    "DEFAULT_POLL_S",
    "EncodePollOnce",
    "EncodeWorker",
]
