"""EmbedderGate + GatedEmbedder — serialize + lookup-over-bulk priority.

Scope: one holder at a time for shared-process Embedder forwards.
Lookup waiters block new bulk acquires between atoms (never mid-forward).
Critical section = model forward only (not cold load, not store I/O).

``GatedEmbedder`` is the only public encode handle for meal / graph / API
free-text: every ``encode_*`` acquires lookup priority. Bulk corpus drain
uses ``acquire(\"bulk\")`` + the raw embedder via ``encode_atom`` separately.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Literal

GateKind = Literal["bulk", "lookup"]


class EmbedderGateTimeout(TimeoutError):
    """Lookup (or bulk) gate acquire timed out — map to omit / encode_failed."""


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


class GatedEmbedder:
    """Only process-facing encode handle for meal / graph / API free-text.

    ``encode_text`` / image / audio / video / joint acquire the gate as
    **lookup** (priority over bulk between atoms). The bulk encode worker
    uses ``acquire(\"bulk\")`` + the raw embedder's ``encode_atom`` path
    separately — never through this proxy.

    ``health`` delegates without the gate (not a model forward).
    ``close`` is a no-op: only the open owner closes the inner embedder.
    """

    def __init__(
        self,
        inner: Any,
        gate: EmbedderGate,
        *,
        lookup_timeout_s: float | None = None,
    ) -> None:
        self._inner = inner
        self._gate = gate
        # None = wait until granted (bulk yields between atoms once waiting).
        self._lookup_timeout_s = lookup_timeout_s

    @property
    def inner(self) -> Any:
        """Raw embedder (loader / bulk / close owner only)."""
        return self._inner

    @property
    def gate(self) -> EmbedderGate:
        return self._gate

    @property
    def lookup_timeout_s(self) -> float | None:
        return self._lookup_timeout_s

    def with_lookup_timeout(self, timeout_s: float | None) -> GatedEmbedder:
        """Return a handle with the same inner/gate but a different timeout."""
        return GatedEmbedder(
            self._inner,
            self._gate,
            lookup_timeout_s=timeout_s,
        )

    # ── pass-through metadata used by warm checks / health blocks ────────

    @property
    def dim(self) -> Any:
        return getattr(self._inner, "dim", None)

    @property
    def model_id(self) -> Any:
        return getattr(self._inner, "model_id", None)

    @property
    def is_loaded(self) -> bool:
        if hasattr(self._inner, "is_loaded"):
            return bool(getattr(self._inner, "is_loaded"))
        return True

    @property
    def loaded(self) -> bool:
        if hasattr(self._inner, "loaded"):
            return bool(getattr(self._inner, "loaded"))
        return True

    def health(self) -> dict[str, Any]:
        """Delegate health — not a model forward; no gate."""
        h = self._inner.health()
        return h if isinstance(h, dict) else {"ok": False, "error": "bad_health"}

    def encode_text(self, text: str) -> list[float]:
        return self._forward("encode_text", text)

    def encode_image(self, path_or_bytes: bytes | str) -> list[float]:
        return self._forward("encode_image", path_or_bytes)

    def encode_audio(self, path_or_bytes: bytes | str) -> list[float]:
        return self._forward("encode_audio", path_or_bytes)

    def encode_video(self, path_or_bytes: bytes | str) -> list[float]:
        return self._forward("encode_video", path_or_bytes)

    def encode_joint(self, parts: Any) -> list[float]:
        return self._forward("encode_joint", parts)

    def encode_atom_inputs(self, *args: Any, **kwargs: Any) -> Any:
        """Lookup-priority wrap (rare for consumers; bulk uses raw)."""
        return self._forward("encode_atom_inputs", *args, **kwargs)

    def close(self) -> None:
        """No-op — only the open owner closes the inner embedder."""
        return None

    def _forward(self, method: str, *args: Any, **kwargs: Any) -> Any:
        timeout = self._lookup_timeout_s
        if not self._gate.acquire("lookup", timeout=timeout):
            raise EmbedderGateTimeout(
                "embedder gate lookup acquire timed out"
            )
        try:
            fn = getattr(self._inner, method)
            return fn(*args, **kwargs)
        finally:
            self._gate.release()


__all__ = [
    "EmbedderGate",
    "EmbedderGateTimeout",
    "GateKind",
    "GatedEmbedder",
]
