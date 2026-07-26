"""Dedicated asyncio loop thread for sync callers of async sandbox SDK.

Scope: single-loop bridge with timeout, reentrancy guard, orderly shutdown.
In scope: AsyncBridge.run / shutdown; BridgeReentrancyError on nested use.
Out of scope: sandbox lifecycle, tool invoke.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from elyra.sandbox.errors import (
    BridgeReentrancyError,
    BridgeShutdownError,
    BridgeTimeoutError,
)

_LOG = logging.getLogger(__name__)

T = TypeVar("T")

# Design: bridge cancel/join ≤5s on shutdown.
DEFAULT_SHUTDOWN_JOIN_SECONDS = 5.0


class AsyncBridge:
    """Run coroutines on a dedicated background event-loop thread.

    Contract:
    - Never call ``run`` from a coroutine already on this loop (reentrancy).
    - Every ``run`` waits with a timeout; on timeout cancel the future.
    - ``shutdown`` sets closed, cancels pending, stops loop, joins ≤5s.
    """

    def __init__(self, *, name: str = "elyra-sandbox-bridge") -> None:
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closed = False
        self._lock = threading.Lock()
        self._pending: set[concurrent.futures.Future[Any]] = set()
        self._start()

    def _start(self) -> None:
        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            self._ready.set()
            try:
                loop.run_forever()
            finally:
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(
                            asyncio.gather(*pending, return_exceptions=True)
                        )
                finally:
                    loop.close()
                    if self._loop is loop:
                        self._loop = None

        self._thread = threading.Thread(
            target=_runner,
            name=self._name,
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("AsyncBridge event loop failed to start")

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop

    def run(self, coro: Coroutine[Any, Any, T], *, timeout: float) -> T:
        """Schedule ``coro`` on the bridge loop and wait with a required timeout.

        ``timeout`` must be a positive finite float — unbounded waits are rejected
        so callers never hang (sync bridge contract).
        """
        if timeout is None or not isinstance(timeout, (int, float)) or timeout <= 0:
            coro.close()
            raise ValueError(
                f"AsyncBridge.run requires timeout > 0, got {timeout!r}"
            )

        with self._lock:
            if self._closed or self._loop is None:
                # Close coro to avoid "coroutine was never awaited".
                coro.close()
                raise BridgeShutdownError("AsyncBridge is shut down")
            loop = self._loop

        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None and running is loop:
            coro.close()
            raise BridgeReentrancyError(
                "cannot call AsyncBridge.run from a coroutine on the bridge loop"
            )

        future: concurrent.futures.Future[T] = asyncio.run_coroutine_threadsafe(
            coro, loop
        )
        with self._lock:
            if self._closed:
                future.cancel()
                raise BridgeShutdownError("AsyncBridge is shut down")
            self._pending.add(future)

        def _discard(_f: concurrent.futures.Future[Any]) -> None:
            with self._lock:
                self._pending.discard(future)

        future.add_done_callback(_discard)

        try:
            return future.result(timeout=float(timeout))
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise BridgeTimeoutError(
                f"AsyncBridge.run timed out after {timeout}s"
            ) from exc
        except concurrent.futures.CancelledError as exc:
            raise BridgeShutdownError("AsyncBridge future cancelled") from exc

    def shutdown(self, *, join_timeout: float = DEFAULT_SHUTDOWN_JOIN_SECONDS) -> None:
        """Cancel pending work, stop the loop, join the thread."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = list(self._pending)
            self._pending.clear()
            loop = self._loop

        for fut in pending:
            fut.cancel()

        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=join_timeout)
            if thread.is_alive():
                _LOG.warning(
                    "AsyncBridge thread %s did not join within %.1fs",
                    self._name,
                    join_timeout,
                )
        self._thread = None
