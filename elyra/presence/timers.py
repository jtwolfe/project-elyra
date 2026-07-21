"""Scheduled timers and durable wait snapshot.

Scope: schedule timer wakes, poll due → enqueue kind=timer; arm_wait /
check timeouts → enqueue wait_timeout; waits.json rehydrate.
In scope: pure store layer over data/wakes/{timers,waits}.json + WakeQueue.
Out of scope: worker phases, wait_user tool, full presence state machine.
"""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths
from elyra.presence.queue import WakeItem, WakeQueue

TIMERS_REL = Path("wakes") / "timers.json"
WAITS_REL = Path("wakes") / "waits.json"

STATUS_SCHEDULED = "scheduled"
STATUS_FIRED = "fired"
STATUS_CANCELLED = "cancelled"
STATUS_PENDING = "pending"
STATUS_ANSWERED = "answered"
STATUS_TIMED_OUT = "timed_out"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def parse_utc(ts: str) -> datetime:
    """Parse UTC ISO string (``Z`` or ``+00:00``) to aware datetime."""
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _as_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass
class PendingTimer:
    id: str
    wake_at: str
    reason: str
    goal_id: str | None = None
    task_id: str | None = None
    status: str = STATUS_SCHEDULED
    wake_id: str | None = None  # set when fired → enqueued

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingTimer:
        return cls(
            id=str(data["id"]),
            wake_at=str(data["wake_at"]),
            reason=str(data.get("reason") or ""),
            goal_id=data.get("goal_id"),
            task_id=data.get("task_id"),
            status=str(data.get("status") or STATUS_SCHEDULED),
            wake_id=data.get("wake_id"),
        )


@dataclass
class PendingWait:
    """Durable wait snapshot row (rehydrate on startup)."""

    id: str
    prompt: str
    choices: list[str]
    user_id: str
    moment_id: str
    expires_at: str
    timeout: float | None = None  # optional seconds used when arming
    status: str = STATUS_PENDING
    wake_id: str | None = None  # wait_timeout wake if timed out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PendingWait:
        choices = data.get("choices") or []
        if not isinstance(choices, list):
            choices = list(choices)
        timeout = data.get("timeout")
        return cls(
            id=str(data.get("id") or data.get("wait_id")),
            prompt=str(data.get("prompt") or ""),
            choices=[str(c) for c in choices],
            user_id=str(data.get("user_id") or "operator"),
            moment_id=str(data.get("moment_id") or ""),
            expires_at=str(
                data.get("expires_at") or data.get("deadline_utc") or ""
            ),
            timeout=float(timeout) if timeout is not None else None,
            status=str(data.get("status") or STATUS_PENDING),
            wake_id=data.get("wake_id"),
        )


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict) and isinstance(raw.get("items"), list):
        return [x for x in raw["items"] if isinstance(x, dict)]
    return []


def _write_json_list(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


class TimerService:
    """Schedule timers and arm waits; poll due work into the wake queue."""

    def __init__(self, paths: ElyraPaths, queue: WakeQueue) -> None:
        self._paths = paths
        self._queue = queue
        self._lock = threading.RLock()
        self._timers: dict[str, PendingTimer] = {}
        self._waits: dict[str, PendingWait] = {}
        self._load()

    @property
    def timers_path(self) -> Path:
        return self._paths.data_dir / TIMERS_REL

    @property
    def waits_path(self) -> Path:
        return self._paths.data_dir / WAITS_REL

    def _load(self) -> None:
        with self._lock:
            self._timers = {
                t.id: t
                for t in (
                    PendingTimer.from_dict(r)
                    for r in _read_json_list(self.timers_path)
                )
            }
            self._waits = {
                w.id: w
                for w in (
                    PendingWait.from_dict(r)
                    for r in _read_json_list(self.waits_path)
                )
            }

    def _persist_timers(self) -> None:
        rows = [t.to_dict() for t in self._timers.values()]
        rows.sort(key=lambda r: (r.get("wake_at") or "", r.get("id") or ""))
        _write_json_list(self.timers_path, rows)

    def _persist_waits(self) -> None:
        rows = [w.to_dict() for w in self._waits.values()]
        rows.sort(key=lambda r: (r.get("expires_at") or "", r.get("id") or ""))
        _write_json_list(self.waits_path, rows)

    # --- timers -----------------------------------------------------------

    def schedule_timer(
        self,
        wake_at: str | datetime,
        reason: str = "",
        *,
        goal_id: str | None = None,
        task_id: str | None = None,
        timer_id: str | None = None,
    ) -> PendingTimer:
        """Record a scheduled timer (not yet a wake). Use schedule_due to fire."""
        if isinstance(wake_at, datetime):
            wake_at_s = _as_iso(wake_at)
        else:
            # Validate parseable ISO.
            parse_utc(str(wake_at))
            wake_at_s = str(wake_at)
        timer = PendingTimer(
            id=timer_id or str(uuid.uuid4()),
            wake_at=wake_at_s,
            reason=reason,
            goal_id=goal_id,
            task_id=task_id,
            status=STATUS_SCHEDULED,
        )
        with self._lock:
            self._timers[timer.id] = timer
            self._persist_timers()
        return timer

    def cancel_timer(self, timer_id: str, *, reason: str = "cancelled") -> None:
        del reason  # reserved for future event log
        with self._lock:
            timer = self._timers.get(timer_id)
            if timer is None:
                raise KeyError(f"unknown timer_id: {timer_id}")
            if timer.status != STATUS_SCHEDULED:
                return
            timer.status = STATUS_CANCELLED
            self._persist_timers()

    def list_timers(self, *, status: str | None = STATUS_SCHEDULED) -> list[PendingTimer]:
        with self._lock:
            rows = list(self._timers.values())
        if status is not None:
            rows = [t for t in rows if t.status == status]
        rows.sort(key=lambda t: (t.wake_at, t.id))
        return rows

    def schedule_due(
        self,
        now: datetime | str | None = None,
    ) -> list[WakeItem]:
        """Enqueue wake kind=timer for every scheduled timer with wake_at <= now.

        Marks those timers fired and records the wake_id. Idempotent per timer.
        """
        if now is None:
            now_dt = datetime.now(UTC)
        elif isinstance(now, str):
            now_dt = parse_utc(now)
        else:
            now_dt = now if now.tzinfo else now.replace(tzinfo=UTC)
            now_dt = now_dt.astimezone(UTC)

        fired: list[WakeItem] = []
        with self._lock:
            due = [
                t
                for t in self._timers.values()
                if t.status == STATUS_SCHEDULED and parse_utc(t.wake_at) <= now_dt
            ]
            due.sort(key=lambda t: (t.wake_at, t.id))
            for timer in due:
                payload: dict[str, Any] = {
                    "wake_at": timer.wake_at,
                    "reason": timer.reason,
                    "timer_id": timer.id,
                }
                if timer.goal_id is not None:
                    payload["goal_id"] = timer.goal_id
                if timer.task_id is not None:
                    payload["task_id"] = timer.task_id
                item = self._queue.enqueue("timer", payload)
                timer.status = STATUS_FIRED
                timer.wake_id = item.id
                fired.append(item)
            if due:
                self._persist_timers()
        return fired

    # --- waits ------------------------------------------------------------

    def arm_wait(
        self,
        *,
        prompt: str,
        user_id: str,
        moment_id: str,
        expires_at: str | datetime | None = None,
        timeout: float | None = None,
        choices: list[str] | None = None,
        wait_id: str | None = None,
    ) -> PendingWait:
        """Persist a pending wait. Caller may later check_timeouts / answer / cancel."""
        if expires_at is None:
            if timeout is None:
                raise ValueError("expires_at or timeout is required")
            exp_dt = datetime.now(UTC) + timedelta(seconds=float(timeout))
            expires_s = _as_iso(exp_dt)
        elif isinstance(expires_at, datetime):
            expires_s = _as_iso(expires_at)
        else:
            parse_utc(str(expires_at))
            expires_s = str(expires_at)

        wait = PendingWait(
            id=wait_id or str(uuid.uuid4()),
            prompt=prompt,
            choices=list(choices or []),
            user_id=user_id,
            moment_id=moment_id,
            expires_at=expires_s,
            timeout=float(timeout) if timeout is not None else None,
            status=STATUS_PENDING,
        )
        with self._lock:
            self._waits[wait.id] = wait
            self._persist_waits()
        return wait

    def get_wait(self, wait_id: str) -> PendingWait | None:
        with self._lock:
            return self._waits.get(wait_id)

    def list_waits(self, *, status: str | None = STATUS_PENDING) -> list[PendingWait]:
        with self._lock:
            rows = list(self._waits.values())
        if status is not None:
            rows = [w for w in rows if w.status == status]
        rows.sort(key=lambda w: (w.expires_at, w.id))
        return rows

    def mark_wait_answered(self, wait_id: str) -> PendingWait:
        with self._lock:
            wait = self._waits.get(wait_id)
            if wait is None:
                raise KeyError(f"unknown wait_id: {wait_id}")
            if wait.status != STATUS_PENDING:
                return wait
            wait.status = STATUS_ANSWERED
            self._persist_waits()
            return wait

    def cancel_wait(self, wait_id: str) -> PendingWait:
        with self._lock:
            wait = self._waits.get(wait_id)
            if wait is None:
                raise KeyError(f"unknown wait_id: {wait_id}")
            if wait.status != STATUS_PENDING:
                return wait
            wait.status = STATUS_CANCELLED
            self._persist_waits()
            return wait

    def check_timeouts(
        self,
        now: datetime | str | None = None,
    ) -> list[WakeItem]:
        """Enqueue wait_timeout for pending waits with expires_at <= now."""
        if now is None:
            now_dt = datetime.now(UTC)
        elif isinstance(now, str):
            now_dt = parse_utc(now)
        else:
            now_dt = now if now.tzinfo else now.replace(tzinfo=UTC)
            now_dt = now_dt.astimezone(UTC)

        fired: list[WakeItem] = []
        with self._lock:
            due = [
                w
                for w in self._waits.values()
                if w.status == STATUS_PENDING
                and w.expires_at
                and parse_utc(w.expires_at) <= now_dt
            ]
            due.sort(key=lambda w: (w.expires_at, w.id))
            for wait in due:
                # Elapsed seconds if we can parse both ends.
                elapsed_s: float | None = None
                if wait.timeout is not None:
                    elapsed_s = float(wait.timeout)
                payload: dict[str, Any] = {
                    "wait_id": wait.id,
                    "moment_id": wait.moment_id,
                    "choices_offered": list(wait.choices),
                    "prompt": wait.prompt,
                    "user_id": wait.user_id,
                }
                if elapsed_s is not None:
                    payload["wait_elapsed_s"] = elapsed_s
                item = self._queue.enqueue("wait_timeout", payload)
                wait.status = STATUS_TIMED_OUT
                wait.wake_id = item.id
                fired.append(item)
            if due:
                self._persist_waits()
        return fired

    def rehydrate_waits(
        self,
        now: datetime | str | None = None,
    ) -> list[WakeItem]:
        """Reload waits from disk and fire any already-expired pending waits.

        On startup: pending with deadline past → enqueue wait_timeout;
        still-future deadlines remain pending for later check_timeouts.
        """
        self._load()
        return self.check_timeouts(now=now)
