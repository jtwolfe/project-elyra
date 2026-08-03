"""EmbedderGate — exclusive serialize + lookup-over-bulk priority.

Scope: one holder at a time for shared-process Embedder forwards.
Lookup waiters block new bulk acquires between atoms (never mid-forward).
Critical section = model forward only (not cold load, not store I/O).
Out of scope: GatedEmbedder meal/graph/API proxy (PR3).
"""

from __future__ import annotations

import threading
import time
from typing import Literal

GateKind = Literal["bulk", "lookup"]


class EmbedderGate:
    """Exclusive access to the shared process Embedder.

    - ``acquire(kind=\"bulk\"|\"lookup\", timeout=None) -> bool``
    - ``release()``
    - ``lookup_waiting`` — bulk checks between atoms and yields when True
    """

    def __init__(self) -> None:
        self._cond = threading.Condition(threading.Lock())
        self._holder: GateKind | None = None
        self._lookup_waiters: int = 0
        # Observability (process-local; PR4 may surface these).
        self.gate_lookup_waits: int = 0
        self.gate_lookup_wait_ms_last: int = 0
        self.gate_bulk_yields: int = 0

    @property
    def lookup_waiting(self) -> bool:
        with self._cond:
            return self._lookup_waiters > 0

    @property
    def holder(self) -> str | None:
        with self._cond:
            return self._holder

    def acquire(
        self,
        kind: GateKind | str = "bulk",
        timeout: float | None = None,
    ) -> bool:
        """Acquire exclusive forward access. Return True if granted.

        Lookup priority: while any lookup waiter is present, bulk must not
        become the holder. Once a forward is in progress it runs to completion
        (no mid-forward preemption).
        """
        k: GateKind = "lookup" if str(kind) == "lookup" else "bulk"
        deadline: float | None = None
        if timeout is not None:
            deadline = time.monotonic() + max(0.0, float(timeout))

        with self._cond:
            if k == "lookup":
                self._lookup_waiters += 1
                self.gate_lookup_waits += 1
            t0 = time.monotonic()
            try:
                while True:
                    free = self._holder is None
                    if k == "lookup":
                        if free:
                            self._holder = "lookup"
                            self.gate_lookup_wait_ms_last = int(
                                (time.monotonic() - t0) * 1000.0
                            )
                            return True
                    else:
                        # Bulk: free and no lookup waiters (including those
                        # still blocked waiting for the current holder).
                        if free and self._lookup_waiters == 0:
                            self._holder = "bulk"
                            return True
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            if k == "bulk":
                                self.gate_bulk_yields += 1
                            return False
                        self._cond.wait(timeout=remaining)
                    else:
                        self._cond.wait()
            finally:
                if k == "lookup":
                    # Drop waiter slot whether granted or timed out.
                    # Holder is tracked via _holder; bulk is blocked by either
                    # waiters > 0 or holder is set.
                    self._lookup_waiters = max(0, self._lookup_waiters - 1)

    def release(self) -> None:
        """Release the gate. Safe no-op if not held (best-effort)."""
        with self._cond:
            self._holder = None
            self._cond.notify_all()


__all__ = ["EmbedderGate", "GateKind"]
