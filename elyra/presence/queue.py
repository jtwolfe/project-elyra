"""Event-sourced wake queue (Scheme A).

Scope: WakeItem, append-only events.jsonl, fold, claim, recover_claimed,
in-process priority heap, task_ready dedupe helper.
In scope: ops enqueue|claimed|done|cancelled; band priority; crash recovery.
Out of scope: worker phases, moments open/close, interjections, runtime/web.
"""

from __future__ import annotations

import heapq
import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths

EVENTS_REL = Path("wakes") / "events.jsonl"

# Band ordinal: lower = sooner. FIFO within band via created_at then id.
KIND_PRIORITY: dict[str, int] = {
    "user_message": 0,
    "wait_reply": 0,
    "wait_timeout": 1,
    "timer": 2,
    "task_ready": 3,
    "background": 4,
}

KNOWN_KINDS = frozenset(KIND_PRIORITY)
TERMINAL_OPS = frozenset({"done", "cancelled"})
# On crash recovery: re-enqueue durable work; cancel social/wait kinds.
RE_ENQUEUE_ON_RECOVER = frozenset({"timer", "task_ready"})
REASON_INTERRUPTED = "interrupted_redelivery"
REASON_REPLACED = "replaced"


def priority_for_kind(kind: str) -> int:
    """Return band priority for ``kind``; raises ValueError if unknown."""
    if kind not in KIND_PRIORITY:
        raise ValueError(f"unknown wake kind: {kind!r}")
    return KIND_PRIORITY[kind]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class WakeItem:
    id: str
    kind: str
    priority: int
    created_at: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WakeItem:
        return cls(
            id=str(data["id"]),
            kind=str(data["kind"]),
            priority=int(data["priority"]),
            created_at=str(data["created_at"]),
            payload=dict(data.get("payload") or {}),
        )


@dataclass
class _FoldedWake:
    """Latest lifecycle state for one wake_id after fold."""

    item: WakeItem | None = None
    op: str | None = None  # enqueue | claimed | done | cancelled
    moment_id: str | None = None
    reason: str | None = None


def fold_events(events: list[dict[str, Any]]) -> dict[str, _FoldedWake]:
    """Fold event list: latest lifecycle op per wake_id; item from last enqueue."""
    state: dict[str, _FoldedWake] = {}
    for ev in events:
        wake_id = str(ev["wake_id"])
        op = str(ev["op"])
        slot = state.setdefault(wake_id, _FoldedWake())
        if op == "enqueue":
            item_raw = ev.get("item")
            if not isinstance(item_raw, dict):
                continue
            slot.item = WakeItem.from_dict(item_raw)
            # Enqueue resets lifecycle to pending (non-terminal).
            slot.op = "enqueue"
            slot.moment_id = None
            slot.reason = None
        elif op == "claimed":
            slot.op = "claimed"
            slot.moment_id = ev.get("moment_id")
            if slot.moment_id is not None:
                slot.moment_id = str(slot.moment_id)
            slot.reason = None
        elif op == "done":
            slot.op = "done"
            slot.reason = None
        elif op == "cancelled":
            slot.op = "cancelled"
            slot.reason = ev.get("reason")
            if slot.reason is not None:
                slot.reason = str(slot.reason)
        else:
            # Unknown ops ignored for forward compatibility.
            continue
    return state


def _heap_key(item: WakeItem) -> tuple[int, str, str]:
    """Priority key: band, then created_at (FIFO), then id for stability."""
    return (item.priority, item.created_at, item.id)


class WakeQueue:
    """Append-only wake event store + in-process pending heap.

    Mutations are serialized with an RLock (API and single worker share it later).
    """

    def __init__(self, paths: ElyraPaths) -> None:
        self._paths = paths
        self._lock = threading.RLock()
        self._items: dict[str, WakeItem] = {}
        self._ops: dict[str, str] = {}  # wake_id -> latest op
        self._moments: dict[str, str | None] = {}
        self._reasons: dict[str, str | None] = {}
        self._pending_ids: set[str] = set()
        self._heap: list[tuple[int, str, str]] = []  # (priority, created_at, id)
        self._load()

    @property
    def events_path(self) -> Path:
        return self._paths.data_dir / EVENTS_REL

    def _ensure_parent(self) -> None:
        self.events_path.parent.mkdir(parents=True, exist_ok=True)

    def _read_events(self) -> list[dict[str, Any]]:
        path = self.events_path
        if not path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def _append_event(self, event: dict[str, Any]) -> None:
        self._ensure_parent()
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _apply_fold(self, folded: dict[str, _FoldedWake]) -> None:
        self._items.clear()
        self._ops.clear()
        self._moments.clear()
        self._reasons.clear()
        self._pending_ids.clear()
        self._heap.clear()
        for wake_id, slot in folded.items():
            if slot.item is None:
                continue
            self._items[wake_id] = slot.item
            if slot.op is None:
                continue
            self._ops[wake_id] = slot.op
            self._moments[wake_id] = slot.moment_id
            self._reasons[wake_id] = slot.reason
            if slot.op == "enqueue":
                self._pending_ids.add(wake_id)
                heapq.heappush(self._heap, _heap_key(slot.item))

    def _load(self) -> None:
        with self._lock:
            self._apply_fold(fold_events(self._read_events()))

    def reload(self) -> None:
        """Re-fold events from disk (e.g. after external append — rare)."""
        self._load()

    def _pop_pending(self) -> WakeItem | None:
        """Pop best pending item from heap, skipping stale entries."""
        while self._heap:
            _pri, _ts, wake_id = heapq.heappop(self._heap)
            if wake_id not in self._pending_ids:
                continue
            item = self._items.get(wake_id)
            if item is None:
                self._pending_ids.discard(wake_id)
                continue
            if self._ops.get(wake_id) != "enqueue":
                self._pending_ids.discard(wake_id)
                continue
            self._pending_ids.discard(wake_id)
            return item
        return None

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        wake_id: str | None = None,
        priority: int | None = None,
        created_at: str | None = None,
    ) -> WakeItem:
        """Append enqueue event and add to pending heap. Returns the WakeItem."""
        band = priority_for_kind(kind) if priority is None else int(priority)
        if priority is not None and kind not in KNOWN_KINDS:
            # Allow explicit priority only for known kinds (keeps fold predictable).
            raise ValueError(f"unknown wake kind: {kind!r}")
        item = WakeItem(
            id=wake_id or str(uuid.uuid4()),
            kind=kind,
            priority=band if priority is None else int(priority),
            created_at=created_at or _now_iso(),
            payload=dict(payload or {}),
        )
        event = {
            "ts": _now_iso(),
            "wake_id": item.id,
            "op": "enqueue",
            "item": item.to_dict(),
        }
        with self._lock:
            self._append_event(event)
            self._items[item.id] = item
            self._ops[item.id] = "enqueue"
            self._moments[item.id] = None
            self._reasons[item.id] = None
            self._pending_ids.add(item.id)
            heapq.heappush(self._heap, _heap_key(item))
        return item

    def claim(self, moment_id: str) -> WakeItem | None:
        """Claim highest-priority pending wake for ``moment_id``.

        Appends ``claimed`` event. Returns None if nothing pending.
        """
        if not moment_id:
            raise ValueError("moment_id is required")
        with self._lock:
            item = self._pop_pending()
            if item is None:
                return None
            event = {
                "ts": _now_iso(),
                "wake_id": item.id,
                "op": "claimed",
                "moment_id": moment_id,
            }
            self._append_event(event)
            self._ops[item.id] = "claimed"
            self._moments[item.id] = moment_id
            self._reasons[item.id] = None
            return item

    def mark_done(self, wake_id: str) -> None:
        """Append done (terminal). No-op if already terminal."""
        with self._lock:
            op = self._ops.get(wake_id)
            if op is None:
                raise KeyError(f"unknown wake_id: {wake_id}")
            if op in TERMINAL_OPS:
                return
            self._append_event({"ts": _now_iso(), "wake_id": wake_id, "op": "done"})
            self._ops[wake_id] = "done"
            self._reasons[wake_id] = None
            self._pending_ids.discard(wake_id)

    def cancel(self, wake_id: str, reason: str) -> None:
        """Append cancelled (terminal). No-op if already terminal."""
        with self._lock:
            op = self._ops.get(wake_id)
            if op is None:
                raise KeyError(f"unknown wake_id: {wake_id}")
            if op in TERMINAL_OPS:
                return
            self._append_event(
                {
                    "ts": _now_iso(),
                    "wake_id": wake_id,
                    "op": "cancelled",
                    "reason": reason,
                }
            )
            self._ops[wake_id] = "cancelled"
            self._reasons[wake_id] = reason
            self._pending_ids.discard(wake_id)

    def recover_claimed(self) -> list[WakeItem]:
        """Handle claimed-without-terminal after crash/restart.

        - user_message / wait_* / background: cancel with interrupted_redelivery
        - timer / task_ready: cancel then re-enqueue clone with a new id

        Returns newly enqueued items (timer/task_ready clones only).
        """
        reenqueued: list[WakeItem] = []
        with self._lock:
            claimed_ids = [
                wid for wid, op in list(self._ops.items()) if op == "claimed"
            ]
            for wake_id in claimed_ids:
                item = self._items.get(wake_id)
                if item is None:
                    self.cancel(wake_id, REASON_INTERRUPTED)
                    continue
                self.cancel(wake_id, REASON_INTERRUPTED)
                if item.kind in RE_ENQUEUE_ON_RECOVER:
                    clone = self.enqueue(item.kind, dict(item.payload))
                    reenqueued.append(clone)
        return reenqueued

    def pending(self) -> list[WakeItem]:
        """Return pending wakes sorted by priority (band, created_at, id)."""
        with self._lock:
            items = [
                self._items[wid]
                for wid in self._pending_ids
                if wid in self._items and self._ops.get(wid) == "enqueue"
            ]
            items.sort(key=_heap_key)
            return list(items)

    def peek(self) -> WakeItem | None:
        """Return next pending wake without claiming, or None."""
        items = self.pending()
        return items[0] if items else None

    def get(self, wake_id: str) -> WakeItem | None:
        with self._lock:
            return self._items.get(wake_id)

    def status(self, wake_id: str) -> str | None:
        """Latest lifecycle op for ``wake_id``, or None if unknown."""
        with self._lock:
            return self._ops.get(wake_id)

    def claimed(self) -> list[WakeItem]:
        """Return wakes currently in claimed (non-terminal) state."""
        with self._lock:
            out: list[WakeItem] = []
            for wid, op in self._ops.items():
                if op == "claimed" and wid in self._items:
                    out.append(self._items[wid])
            return out

    def pending_task_ready(self, task_id: str) -> list[WakeItem]:
        """Pending (not claimed/terminal) task_ready wakes for ``task_id``."""
        with self._lock:
            out: list[WakeItem] = []
            for wid in self._pending_ids:
                item = self._items.get(wid)
                if item is None or item.kind != "task_ready":
                    continue
                if self._ops.get(wid) != "enqueue":
                    continue
                if item.payload.get("task_id") == task_id:
                    out.append(item)
            return out

    def enqueue_task_ready(
        self,
        task_id: str,
        *,
        goal_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> WakeItem:
        """Enqueue task_ready after cancelling any pending same task_id (dedupe).

        Old pending (enqueue-only) task_ready for the same task_id are cancelled
        with reason=replaced, then a fresh wake is enqueued.
        """
        if not task_id:
            raise ValueError("task_id is required")
        body = dict(payload or {})
        body["task_id"] = task_id
        if goal_id is not None:
            body["goal_id"] = goal_id
        with self._lock:
            for old in self.pending_task_ready(task_id):
                self.cancel(old.id, REASON_REPLACED)
            return self.enqueue("task_ready", body)
