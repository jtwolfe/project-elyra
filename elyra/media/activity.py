"""Process-local media activity chips for glass trail (PR10 observability).

Kinds: ``upload``, ``stt``, ``tts`` — short labels only; no paths/PII.
Merged into ``/api/status`` as ``media_activity`` (and optionally folded into
``recent_activity`` by the API handler).
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Literal

MediaActivityKind = Literal["upload", "stt", "tts"]

_MAX_EVENTS = 12
_lock = threading.Lock()
_events: Deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)

_LABELS: dict[str, str] = {
    "upload": "upload",
    "stt": "stt",
    "tts": "tts",
}


def note_media_activity(
    kind: MediaActivityKind | str,
    *,
    label: str | None = None,
    ok: bool = True,
    detail: str | None = None,
) -> dict[str, Any]:
    """Record a short media trail event; return the event dict."""
    kind_s = str(kind or "upload").strip().lower()
    if kind_s not in _LABELS:
        kind_s = "upload"
    lab = (label or _LABELS[kind_s]).strip() or _LABELS[kind_s]
    if not ok and "✗" not in lab:
        lab = f"{lab}✗"
    # Keep labels short for chips.
    lab = lab[:28]
    ev: dict[str, Any] = {
        "id": f"media-{kind_s}-{uuid.uuid4().hex[:10]}",
        "kind": kind_s,
        "label": lab,
        "short": kind_s,
        "ok": ok,
        "ts": time.time(),
    }
    if detail:
        ev["detail"] = str(detail)[:48]
    with _lock:
        _events.append(ev)
    return ev


def recent_media_activity(*, limit: int = 8) -> list[dict[str, Any]]:
    """Newest-last copy of recent media events (oldest → newest)."""
    n = max(0, int(limit))
    with _lock:
        items = list(_events)
    if n and len(items) > n:
        return items[-n:]
    return items


def clear_media_activity_for_tests() -> None:
    with _lock:
        _events.clear()
