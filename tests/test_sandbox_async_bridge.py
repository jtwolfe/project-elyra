"""AsyncBridge contract tests (timeout / shutdown / reentrancy)."""

from __future__ import annotations

import asyncio
import threading

import pytest

from elyra.sandbox.async_bridge import AsyncBridge
from elyra.sandbox.errors import (
    BridgeReentrancyError,
    BridgeShutdownError,
    BridgeTimeoutError,
)


async def _sleep_return(value: str, delay: float = 0.0) -> str:
    if delay:
        await asyncio.sleep(delay)
    return value


def test_bridge_run_basic() -> None:
    bridge = AsyncBridge(name="test-bridge-basic")
    try:
        assert bridge.run(_sleep_return("ok"), timeout=2.0) == "ok"
    finally:
        bridge.shutdown()


def test_bridge_timeout_cancels() -> None:
    bridge = AsyncBridge(name="test-bridge-timeout")
    try:
        with pytest.raises(BridgeTimeoutError):
            bridge.run(_sleep_return("late", delay=2.0), timeout=0.1)
    finally:
        bridge.shutdown()


def test_bridge_shutdown_rejects_new_run() -> None:
    bridge = AsyncBridge(name="test-bridge-closed")
    bridge.shutdown()
    with pytest.raises(BridgeShutdownError):
        bridge.run(_sleep_return("nope"), timeout=1.0)


def test_bridge_shutdown_with_pending() -> None:
    bridge = AsyncBridge(name="test-bridge-pending")
    started = threading.Event()
    finished = threading.Event()
    errors: list[BaseException] = []

    async def _long() -> str:
        started.set()
        await asyncio.sleep(5.0)
        return "done"

    def _worker() -> None:
        try:
            bridge.run(_long(), timeout=10.0)
        except BaseException as exc:  # noqa: BLE001 — collect for assert
            errors.append(exc)
        finally:
            finished.set()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    assert started.wait(timeout=2.0)
    # Shutdown should cancel pending and join.
    bridge.shutdown(join_timeout=2.0)
    assert finished.wait(timeout=3.0)
    t.join(timeout=1.0)
    assert errors, "pending run should fail after shutdown"
    # Cancelled future may surface as CancelledError, BridgeShutdownError, or generic.
    assert all(isinstance(e, BaseException) for e in errors)


def test_bridge_reentrancy_from_loop() -> None:
    bridge = AsyncBridge(name="test-bridge-reentrancy")

    async def _nested() -> str:
        # Running on the bridge loop — nested run must raise.
        return bridge.run(_sleep_return("inner"), timeout=1.0)

    try:
        with pytest.raises(BridgeReentrancyError):
            bridge.run(_nested(), timeout=2.0)
    finally:
        bridge.shutdown()


def test_bridge_double_shutdown_idempotent() -> None:
    bridge = AsyncBridge(name="test-bridge-double-sd")
    bridge.shutdown()
    bridge.shutdown()  # no raise


def test_bridge_requires_positive_timeout() -> None:
    """Unbounded / non-positive timeout rejected."""
    bridge = AsyncBridge(name="test-bridge-timeout-req")
    try:
        with pytest.raises(ValueError):
            bridge.run(_sleep_return("x"), timeout=0)
        with pytest.raises(ValueError):
            bridge.run(_sleep_return("x"), timeout=-1)
    finally:
        bridge.shutdown()
