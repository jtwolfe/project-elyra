"""Presence pre-claim gate: model_available + rebindable worker.client (PR 5b).

Hard-stop (override OFF) and !credential_ok leave wakes pending; timers still
fire; override ON and credential repair allow claim again.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.llm.client import FailingChatClient, StubChatClient
from elyra.llm.credits import CreditsSnapshot
from elyra.llm.usage import TokenUsage, UsageMeter
from elyra.loop.doloop import DoLoopResult
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import STATUS_PENDING, TimerService
from elyra.presence.user_input import PHASE_IDLE
from elyra.presence.worker import PresenceWorker
from elyra.settings import UsageSettings, default_settings


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


# ---------------------------------------------------------------------------
# Fixtures / stubs
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


def _fake_registry() -> MagicMock:
    reg = MagicMock()
    reg.openai_tools.return_value = []
    reg.execute.return_value = MagicMock(ok=True, payload={}, ends_moment=False)
    return reg


def _stub_loop(
    *,
    stop_reason: str = "no_tools",
    hop_count: int = 1,
    on_call: Any = None,
) -> Any:
    calls: list[dict[str, Any]] = []

    def _fn(**kwargs: Any) -> DoLoopResult:
        calls.append(kwargs)
        if on_call is not None:
            on_call(kwargs)
        ctx = kwargs.get("ctx")
        mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
        return DoLoopResult(
            stop_reason=stop_reason,
            hop_count=hop_count,
            moment_id=mid,
            tools_ran=False,
            model_beats=1,
        )

    _fn.calls = calls  # type: ignore[attr-defined]
    return _fn


def _make_worker(
    paths,
    *,
    run_do_loop_fn=None,
    model_available=None,
    client=None,
    poll_seconds: float = 0.05,
    stop_event: threading.Event | None = None,
) -> tuple[PresenceWorker, threading.Event]:
    stop = stop_event or threading.Event()
    queue = WakeQueue(paths)
    timers = TimerService(paths, queue)
    moments = MomentStore(paths)
    worker = PresenceWorker(
        paths=paths,
        client=client if client is not None else StubChatClient(),
        stop_event=stop,
        poll_seconds=poll_seconds,
        settings=default_settings(),
        queue=queue,
        timers=timers,
        moments=moments,
        registry=_fake_registry(),
        run_do_loop_fn=run_do_loop_fn or _stub_loop(),
        model_available=model_available,
    )
    return worker, stop


def _start(worker: PresenceWorker) -> threading.Thread:
    t = threading.Thread(target=worker.run, name="test-presence", daemon=True)
    t.start()
    # Let startup recover finish.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if getattr(worker, "_started", False):
            break
        time.sleep(0.01)
    return t


def _stop_join(
    worker: PresenceWorker, stop: threading.Event, t: threading.Thread
) -> None:
    stop.set()
    t.join(timeout=2.0)


def _wait_until(pred, timeout: float = 2.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return bool(pred())


# ---------------------------------------------------------------------------
# Pre-claim: hard-stop / !credential_ok leave wake pending
# ---------------------------------------------------------------------------


def test_hard_stop_preclaim_leaves_wake_pending(paths):
    """Hard-stopped meter → model_available false → no claim, wake stays pending."""
    usage = UsageSettings(
        enabled=True,
        weekly_allowed_tokens=10,
        day_allowed_tokens=10,
        hour_allowed_tokens=10,
    )
    meter = UsageMeter.load(paths.data_dir, usage)
    meter.record(TokenUsage(total_tokens=10))
    assert meter.can_call() is False

    stub = _stub_loop()
    worker, stop = _make_worker(
        paths,
        run_do_loop_fn=stub,
        model_available=lambda: meter.can_call(),
    )
    t = _start(worker)
    try:
        wake_id = worker.enqueue_user_message("blocked by budget", user_id="operator")
        # Give the worker several poll cycles.
        assert not _wait_until(lambda: len(stub.calls) >= 1, timeout=0.6)
        assert len(stub.calls) == 0
        assert worker.phase == PHASE_IDLE
        assert worker.busy is False
        # Queue ops: enqueue | claimed | done | cancelled ("enqueue" = still pending).
        assert worker._queue.status(wake_id) == "enqueue"  # noqa: SLF001
        assert len(worker._queue.pending()) == 1  # noqa: SLF001
        # No open moments created for the skipped claim.
        assert MomentStore(paths).list_open_moments() == []
    finally:
        _stop_join(worker, stop, t)


def test_credential_ok_false_preclaim_leaves_wake_pending(paths):
    """!credential_ok (FailingChatClient path) → no claim, no noise error moment."""
    available = {"ok": False}
    stub = _stub_loop()
    worker, stop = _make_worker(
        paths,
        run_do_loop_fn=stub,
        client=FailingChatClient("missing_auth_json"),
        model_available=lambda: available["ok"],
    )
    t = _start(worker)
    try:
        wake_id = worker.enqueue_user_message("no creds yet", user_id="operator")
        assert not _wait_until(lambda: len(stub.calls) >= 1, timeout=0.6)
        assert len(stub.calls) == 0
        assert worker.phase == PHASE_IDLE
        assert worker._queue.status(wake_id) == "enqueue"  # noqa: SLF001
        # No moment tape for noise errors.
        assert MomentStore(paths).list_open_moments() == []
        closed = [
            m
            for m in MomentStore(paths).list_moments()
            if m.get("ended_at") is not None
        ]
        # No moments opened at all for this wake.
        assert closed == [] or all(
            m.get("wake_id") != wake_id for m in closed
        )
    finally:
        _stop_join(worker, stop, t)


def test_after_available_true_claim_proceeds(paths):
    """After model_available flips true (repair/override), pending wake is claimed."""
    available = {"ok": False}
    stub = _stub_loop(hop_count=2)
    worker, stop = _make_worker(
        paths,
        run_do_loop_fn=stub,
        model_available=lambda: available["ok"],
    )
    t = _start(worker)
    try:
        wake_id = worker.enqueue_user_message("wait for repair", user_id="operator")
        assert not _wait_until(lambda: len(stub.calls) >= 1, timeout=0.4)
        assert worker._queue.status(wake_id) == "enqueue"  # noqa: SLF001

        available["ok"] = True
        assert _wait_until(lambda: len(stub.calls) >= 1, timeout=2.0)
        assert _wait_until(lambda: worker.phase == PHASE_IDLE and not worker.busy)
        assert worker._queue.status(wake_id) == "done"  # noqa: SLF001
        assert len(stub.calls) == 1
    finally:
        _stop_join(worker, stop, t)


def test_override_on_allows_claim_when_over_budget(paths):
    """Hard-stop override ON → can_call true → claim proceeds while over budget."""
    usage = UsageSettings(
        enabled=True,
        weekly_allowed_tokens=10,
        day_allowed_tokens=10,
        hour_allowed_tokens=10,
    )
    meter = UsageMeter.load(paths.data_dir, usage)
    meter.record(TokenUsage(total_tokens=10))
    assert meter.can_call() is False

    meter.set_hard_stop_override(True)
    assert meter.can_call() is True

    stub = _stub_loop()
    worker, stop = _make_worker(
        paths,
        run_do_loop_fn=stub,
        model_available=lambda: meter.can_call(),
    )
    t = _start(worker)
    try:
        wake_id = worker.enqueue_user_message("override on", user_id="operator")
        assert _wait_until(lambda: len(stub.calls) >= 1, timeout=2.0)
        assert _wait_until(lambda: worker._queue.status(wake_id) == "done")  # noqa: SLF001
    finally:
        _stop_join(worker, stop, t)


def test_account_hard_preclaim_leaves_wake_pending(paths):
    """Injected SuperGrok A≥A_hard → can_call false → wake stays pending."""
    clock = _FixedClock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    usage = UsageSettings(
        enabled=True,
        weekly_allowed_tokens=1_000_000,
        account_hard_stop_percent=95.0,
    )
    meter = UsageMeter.load(paths.data_dir, usage, clock=clock)
    meter.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=96.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
            status="ok",
            ok=True,
        )
    )
    assert meter.can_call() is False
    assert meter.snapshot().hard_stop == "account"

    stub = _stub_loop()
    worker, stop = _make_worker(
        paths,
        run_do_loop_fn=stub,
        model_available=lambda: meter.can_call(),
    )
    t = _start(worker)
    try:
        wake_id = worker.enqueue_user_message("account hard", user_id="operator")
        assert not _wait_until(lambda: len(stub.calls) >= 1, timeout=0.6)
        assert len(stub.calls) == 0
        assert worker.phase == PHASE_IDLE
        assert worker._queue.status(wake_id) == "enqueue"  # noqa: SLF001
    finally:
        _stop_join(worker, stop, t)


def test_account_hard_override_allows_claim(paths):
    """Account hard + override ON → claim proceeds (override kept)."""
    clock = _FixedClock(datetime(2026, 7, 24, 14, 0, tzinfo=UTC))
    usage = UsageSettings(
        enabled=True,
        weekly_allowed_tokens=1_000_000,
        account_hard_stop_percent=95.0,
    )
    meter = UsageMeter.load(paths.data_dir, usage, clock=clock)
    meter.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=99.0,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at="2026-07-24T14:00:00Z",
            status="ok",
            ok=True,
        )
    )
    assert meter.can_call() is False
    meter.set_hard_stop_override(True)
    assert meter.can_call() is True
    assert meter.snapshot().hard_stop == "account"

    stub = _stub_loop()
    worker, stop = _make_worker(
        paths,
        run_do_loop_fn=stub,
        model_available=lambda: meter.can_call(),
    )
    t = _start(worker)
    try:
        wake_id = worker.enqueue_user_message("account override", user_id="operator")
        assert _wait_until(lambda: len(stub.calls) >= 1, timeout=2.0)
        assert _wait_until(lambda: worker._queue.status(wake_id) == "done")  # noqa: SLF001
    finally:
        _stop_join(worker, stop, t)


def test_yellow_band_still_allows_claim(paths):
    """Soft yellow pace band never blocks presence pre-claim."""
    B, k = 7000, 4.0
    H = 168.0
    t_hours = 24.0
    week_start = datetime(2026, 7, 20, 0, 0, tzinfo=UTC)
    clock = _FixedClock(week_start + timedelta(hours=t_hours))
    usage = UsageSettings(
        enabled=True,
        weekly_allowed_tokens=B,
        burst_hours=k,
    )
    meter = UsageMeter.load(paths.data_dir, usage, clock=clock)
    S = int(round(1.2 * B * t_hours / H))
    meter.record(TokenUsage(total_tokens=S))
    snap = meter.snapshot()
    assert snap.hard_stop is None
    assert snap.pace_band == "yellow"
    assert meter.can_call() is True

    stub = _stub_loop()
    worker, stop = _make_worker(
        paths,
        run_do_loop_fn=stub,
        model_available=lambda: meter.can_call(),
    )
    t = _start(worker)
    try:
        wake_id = worker.enqueue_user_message("yellow soft", user_id="operator")
        assert _wait_until(lambda: len(stub.calls) >= 1, timeout=2.0)
        assert _wait_until(lambda: worker._queue.status(wake_id) == "done")  # noqa: SLF001
    finally:
        _stop_join(worker, stop, t)


def test_timers_still_fire_when_model_unavailable(paths):
    """Timers rehydrate/fire even when model_available is false (wakes enqueue)."""
    available = {"ok": False}
    stub = _stub_loop()
    worker, stop = _make_worker(
        paths,
        run_do_loop_fn=stub,
        model_available=lambda: available["ok"],
        poll_seconds=0.05,
    )
    t = _start(worker)
    try:
        # Arm a short wait that will fire wait_timeout into the wake queue.
        wait = worker._timers.arm_wait(  # noqa: SLF001
            timeout=0.15,
            prompt="ping?",
            choices=["ok"],
            user_id="operator",
            moment_id="m-timer-test",
        )
        assert wait is not None
        # Wait for timeout to enqueue wait_timeout wake.
        assert _wait_until(
            lambda: len(worker._queue.pending()) >= 1,  # noqa: SLF001
            timeout=2.0,
        )
        pending = worker._queue.pending()  # noqa: SLF001
        kinds = {w.kind for w in pending}
        assert "wait_timeout" in kinds or len(pending) >= 1
        # Still no claim / no do-loop while unavailable.
        assert len(stub.calls) == 0
        # Wakes stay on the queue (op=enqueue; never claimed/cancelled by skip).
        assert all(
            worker._queue.status(w.id) == "enqueue" for w in pending  # noqa: SLF001
        )
    finally:
        _stop_join(worker, stop, t)


def test_worker_client_rebindable(paths):
    """ProviderRuntime rebuild path: worker.client is a rebindable public attr."""
    client_a = StubChatClient()
    client_b = FailingChatClient("missing_auth_json")
    worker, stop = _make_worker(paths, client=client_a)
    assert worker.client is client_a
    # Live rebind (no restart) — same contract as ProviderRuntime._bind_worker.
    worker.client = client_b
    assert worker.client is client_b
    assert isinstance(worker.client, FailingChatClient)


def test_default_model_available_allows_claim(paths):
    """No model_available hook → default True; claim proceeds (compat)."""
    stub = _stub_loop()
    worker, stop = _make_worker(paths, run_do_loop_fn=stub, model_available=None)
    t = _start(worker)
    try:
        wake_id = worker.enqueue_user_message("default open", user_id="operator")
        assert _wait_until(lambda: len(stub.calls) >= 1, timeout=2.0)
        assert _wait_until(lambda: worker._queue.status(wake_id) == "done")  # noqa: SLF001
    finally:
        _stop_join(worker, stop, t)


def test_model_available_exception_treated_as_unavailable(paths):
    """Hook raising must not crash the worker; treat as unavailable."""

    def _boom() -> bool:
        raise RuntimeError("gate broken")

    stub = _stub_loop()
    worker, stop = _make_worker(
        paths, run_do_loop_fn=stub, model_available=_boom
    )
    t = _start(worker)
    try:
        wake_id = worker.enqueue_user_message("gate boom", user_id="operator")
        assert not _wait_until(lambda: len(stub.calls) >= 1, timeout=0.5)
        assert worker._queue.status(wake_id) == "enqueue"  # noqa: SLF001
        # Worker still alive and idle.
        assert worker.phase == PHASE_IDLE
        assert worker.busy is False
    finally:
        _stop_join(worker, stop, t)
