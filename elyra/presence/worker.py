"""Presence worker: claim wakes, open moments, run do-loop.

Scope: single-thread orchestration; phase machine; public enqueue/interject API.
In scope: claim → open → run_do_loop → close; wait arm → waiting; startup recover;
full reset port (reset_runtime_state under lock while idle).
Out of scope: HTTP/web, tool internals, glass UI panels.

Public API: enqueue_wake, enqueue_user_message, interject, resolve_user_input,
busy, active_moment_id, pending_wait, status_snapshot, reset_runtime_state,
set_continuous_enabled, set_dev_speed.
Must not import runtime.web.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Sequence

from elyra.config import ElyraPaths
from elyra.goals import GoalsStore
from elyra.identity import IdentityStore
from elyra.identity.orient_user import resolve_orient_user
from elyra.llm.client import ChatClient
from elyra.loop.context import assemble_outer_meal
from elyra.loop.orient_slice import (
    format_goals_slice,
    format_skill_bias,
    format_skill_catalog,
)
from elyra.loop.continuous_policy import (
    SOCIAL_WAKE_KINDS,
    ContinuousRuntimeState,
    continuous_status_block,
    load_continuous_runtime,
    save_continuous_enabled,
    should_enqueue_moment_continue,
)
from elyra.loop.doloop import DoLoopResult, run_do_loop
from elyra.messages import Message, append_message, list_messages
from elyra.moment import MomentStore
from elyra.moment.types import STOP_REASONS
from elyra.runtime.dev_speed import (
    DevSpeedState,
    dev_speed_status_block,
    effective_hop_delay_seconds,
    load_dev_speed_runtime,
    save_dev_speed_runtime,
)
from elyra.presence.interject import (
    REASON_BUFFER_FULL,
    InterjectBuffer,
    InterjectItem,
)
from elyra.presence.queue import (
    KIND_PRIORITY,
    REASON_CONTINUOUS_DISABLED,
    WakeItem,
    WakeQueue,
)
from elyra.presence.timers import STATUS_PENDING, TimerService
from elyra.presence.user_input import (
    PHASE_IDLE,
    PHASE_IN_MOMENT,
    PHASE_WAITING,
    ROUTE_INTERJECT,
    ROUTE_USER_MESSAGE,
    ROUTE_WAIT_REPLY,
    resolve_user_input as decide_user_input,
)
from elyra.runtime.reset import (
    clear_goals,
    clear_local_tools,
    clear_media,
    clear_messages,
    clear_moments,
    clear_sandbox,
    clear_tool_drafts,
    clear_wakes_disk,
    ensure_preserved_dirs,
    normalize_reset_flags,
)
from elyra.sandbox import Sandbox
from elyra.skills import SkillCatalog
from elyra.settings import Settings, load_settings
from elyra.speak import SpeakTransport
from elyra.tools import ToolContext, ToolRegistry
from elyra.tools.policy import resolve_bundled_tools_root
from elyra.tools.types import WaitArm
from elyra.users import UsersStore

_LOG = logging.getLogger(__name__)

# Injectable do-loop port (tests inject stubs; production uses run_do_loop).
# SOCIAL_WAKE_KINDS: imported from continuous_policy (single source of truth).
RunDoLoopFn = Callable[..., DoLoopResult]

# Goal / task statuses that count as open work for outer moment_continue (K18).
_OPEN_GOAL_STATUSES = frozenset({"open", "review"})
_OPEN_TASK_STATUSES = frozenset({"ready", "in_progress", "blocked"})


def _media_ids_from_wake(
    wake: WakeItem,
    *,
    paths: ElyraPaths | None = None,
) -> tuple[str, ...]:
    """Resolve Stretch-1 media content ids for a social wake (best-effort).

    Prefers payload ``media_ids`` / attachment ids; falls back to glass row
    attachments for ``message_id`` when present.
    """
    payload = wake.payload or {}
    raw = payload.get("media_ids")
    if raw is None:
        raw = payload.get("attachment_ids")
    ids: list[str] = []
    if isinstance(raw, str) and raw.strip():
        ids.append(raw.strip())
    elif isinstance(raw, (list, tuple)):
        for x in raw:
            if x:
                ids.append(str(x))
    # Attachment dicts may be embedded on the wake payload.
    atts = payload.get("attachments")
    if isinstance(atts, (list, tuple)):
        for att in atts:
            if isinstance(att, Mapping):
                aid = att.get("id") or att.get("media_id")
                if aid:
                    ids.append(str(aid))
            elif isinstance(att, str) and att.strip():
                ids.append(att.strip())
    # Glass row lookup when only message_id is known.
    if not ids and payload.get("message_id") and paths is not None:
        try:
            from elyra.messages import get_message

            row = get_message(str(payload["message_id"]), paths=paths)
            if isinstance(row, dict):
                glass_atts = row.get("attachments") or []
                if isinstance(glass_atts, (list, tuple)):
                    for att in glass_atts:
                        if isinstance(att, dict):
                            aid = att.get("id")
                            if aid:
                                ids.append(str(aid))
        except Exception:  # noqa: BLE001
            _LOG.exception("wake media_ids glass lookup failed")
    # De-dupe preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for mid in ids:
        if mid not in seen:
            seen.add(mid)
            out.append(mid)
    return tuple(out)


def _why_now(wake: WakeItem) -> str:
    kind = wake.kind
    payload = wake.payload or {}
    if kind == "user_message":
        uid = payload.get("user_id") or "user"
        return f"user message from {uid}"
    if kind == "wait_reply":
        return f"wait reply (wait_id={payload.get('wait_id') or '?'})"
    if kind == "wait_timeout":
        return f"wait timeout (wait_id={payload.get('wait_id') or '?'})"
    if kind == "timer":
        reason = payload.get("reason") or ""
        return f"timer due: {reason}" if reason else "timer due"
    if kind == "task_ready":
        return f"task ready: {payload.get('task_id') or '?'}"
    if kind == "moment_continue":
        src = payload.get("source_moment_id") or "?"
        return f"continue work (from moment {src})"
    if kind == "background":
        return "background wake"
    return f"wake kind={kind}"


def _user_id_from_wake(wake: WakeItem) -> str | None:
    uid = (wake.payload or {}).get("user_id")
    if uid is None or uid == "":
        return None
    return str(uid)


def compact_activity_event(beat: dict[str, Any]) -> dict[str, Any] | None:
    """Map a moment beat → small glass trail event (or None if uninteresting)."""
    if not isinstance(beat, dict):
        return None
    btype = beat.get("type")
    ts = beat.get("ts")

    if btype == "model":
        raw_calls = beat.get("tool_calls") or []
        names: list[str] = []
        if isinstance(raw_calls, list):
            for call in raw_calls:
                if isinstance(call, dict):
                    n = call.get("name")
                    if isinstance(n, str) and n.strip():
                        names.append(n.strip())
        hop = beat.get("hop")
        if names:
            detail = ", ".join(names[:3])
            if len(names) > 3:
                detail += f" +{len(names) - 3}"
            return {
                "id": f"model-{hop}-{detail}",
                "kind": "model_tools",
                "label": detail,
                "short": "model",
                "tools": names,
                "hop": hop,
                "ts": ts,
            }
        content = beat.get("content") or ""
        has_speech = isinstance(content, str) and bool(content.strip())
        return {
            "id": f"model-{hop}-{'speak' if has_speech else 'think'}",
            "kind": "model",
            "label": "speak" if has_speech else "think",
            "short": "model",
            "hop": hop,
            "ts": ts,
        }

    if btype == "tool":
        name = beat.get("name") or beat.get("tool") or beat.get("tool_name") or "tool"
        if not isinstance(name, str):
            name = "tool"
        ok = beat.get("ok")
        err = beat.get("error_reason")
        label = name if ok is not False else f"{name}✗"
        return {
            "id": f"tool-{name}-{ok}-{err or ''}",
            "kind": "tool_err" if ok is False else "tool",
            "label": label,
            "short": name,
            "name": name,
            "ok": ok,
            "error_reason": err if isinstance(err, str) else None,
            "ts": ts,
        }

    if btype == "obs":
        kind = beat.get("kind") or "obs"
        kind_s = str(kind)
        name = beat.get("name")
        label = f"{kind_s}:{name}" if isinstance(name, str) and name else kind_s
        return {
            "id": f"obs-{label}",
            "kind": "obs",
            "label": label[:28],
            "short": kind_s[:14],
            "ts": ts,
        }

    if btype == "stop":
        reason = beat.get("stop_reason") or "stop"
        return {
            "id": f"stop-{reason}",
            "kind": "stop",
            "label": str(reason),
            "short": "stop",
            "ts": ts,
        }

    return None


def _activity_headline(
    *,
    phase: str,
    recent: list[dict[str, Any]],
    pending_wait: dict[str, Any] | None,
    hop_count: int,
    last_tool: str | None,
) -> dict[str, Any]:
    """Human-facing headline for the chat activity pill."""
    hop_bit = f"hop {hop_count}" if hop_count else ""

    if phase == PHASE_WAITING:
        prompt = ""
        if isinstance(pending_wait, dict):
            raw = pending_wait.get("prompt") or ""
            if isinstance(raw, str) and raw.strip():
                prompt = raw.strip()
                if len(prompt) > 48:
                    prompt = prompt[:45] + "…"
        return {
            "label": "waiting for you",
            "detail": prompt,
            "state": "waiting",
            "hop": hop_count,
            "last_tool": last_tool,
        }

    if phase != PHASE_IN_MOMENT:
        return {
            "label": "working…",
            "detail": hop_bit,
            "state": "busy",
            "hop": hop_count,
            "last_tool": last_tool,
        }

    if not recent:
        return {
            "label": "starting…",
            "detail": hop_bit,
            "state": "in_moment",
            "hop": hop_count,
            "last_tool": last_tool,
        }

    last = recent[-1]
    kind = last.get("kind")
    if kind == "model_tools":
        tools = last.get("tools") or []
        first = tools[0] if tools else last.get("label") or "tools"
        more = f" +{len(tools) - 1}" if len(tools) > 1 else ""
        return {
            "label": f"calling {first}{more}",
            "detail": hop_bit,
            "state": "tool_call",
            "hop": hop_count,
            "last_tool": first if isinstance(first, str) else last_tool,
        }
    if kind == "tool":
        name = last.get("name") or last.get("label") or "tool"
        return {
            "label": f"ran {name}",
            "detail": " · ".join(x for x in (hop_bit, "thinking…") if x),
            "state": "after_tool",
            "hop": hop_count,
            "last_tool": name if isinstance(name, str) else last_tool,
        }
    if kind == "tool_err":
        name = last.get("name") or last.get("label") or "tool"
        err = last.get("error_reason") or "error"
        return {
            "label": f"{name} failed",
            "detail": str(err)[:40],
            "state": "tool_error",
            "hop": hop_count,
            "last_tool": name if isinstance(name, str) else last_tool,
        }
    if kind == "model":
        lab = last.get("label") or "model"
        if lab == "speak":
            return {
                "label": "speaking…",
                "detail": hop_bit,
                "state": "speak",
                "hop": hop_count,
                "last_tool": last_tool,
            }
        return {
            "label": "thinking…",
            "detail": hop_bit,
            "state": "model",
            "hop": hop_count,
            "last_tool": last_tool,
        }
    if kind == "stop":
        return {
            "label": f"stop: {last.get('label') or 'done'}",
            "detail": hop_bit,
            "state": "stop",
            "hop": hop_count,
            "last_tool": last_tool,
        }

    return {
        "label": str(last.get("label") or "in moment…"),
        "detail": hop_bit,
        "state": "in_moment",
        "hop": hop_count,
        "last_tool": last_tool,
    }


class PresenceWorker:
    """Single worker thread: wake queue → open moment → do-loop → close.

    Shared mutable state (phase, interject buffer, hop stats) is guarded by
    one ``threading.RLock``. The lock is released during the long do-loop so
    API threads can still interject into the buffer.
    """

    def __init__(
        self,
        *,
        paths: ElyraPaths,
        client: ChatClient,
        stop_event: threading.Event,
        poll_seconds: float = 0.1,
        settings: Settings | None = None,
        queue: WakeQueue | None = None,
        timers: TimerService | None = None,
        moments: MomentStore | None = None,
        registry: ToolRegistry | None = None,
        sandbox: Sandbox | None = None,
        speak: SpeakTransport | None = None,
        goals: GoalsStore | None = None,
        skills: SkillCatalog | None = None,
        run_do_loop_fn: RunDoLoopFn | None = None,
        model_available: Callable[[], bool] | None = None,
    ) -> None:
        self.paths = paths
        # Rebindable: ProviderRuntime.rebuild_chat_stack sets worker.client.
        self.client = client
        self._stop = stop_event
        self._poll = poll_seconds
        self.settings = settings or load_settings(paths.home)

        self._lock = threading.RLock()
        self._queue = queue or WakeQueue(paths)
        self._timers = timers or TimerService(paths, self._queue)
        self._moments = moments or MomentStore(paths)
        self._registry = registry
        self._sandbox = sandbox
        self._speak = speak
        self._goals = goals
        self._skills = skills
        self._run_do_loop: RunDoLoopFn = run_do_loop_fn or run_do_loop
        # Pre-claim gate: safe to open a model-using moment (creds + budget).
        # Default True preserves prior behaviour for unit tests without a meter.
        self._model_available: Callable[[], bool] = (
            model_available if model_available is not None else (lambda: True)
        )
        # Rate-limit "skip claim" log to once/minute (usage vs credential).
        self._model_unavailable_log_at: float = 0.0

        self._identity = IdentityStore(paths)
        self._users = UsersStore(paths)

        # Continuous work runtime: defaults + JSON override; finalize enqueues
        # moment_continue when gates pass (progress-gated; never task_ready).
        self._continuous: ContinuousRuntimeState = load_continuous_runtime(
            paths.data_dir,
            defaults=self.settings.continuous,
        )
        # Dev-speed pacing (default ON): inter-hop pause for followable glass.
        self._dev_speed: DevSpeedState = load_dev_speed_runtime(paths.data_dir)

        # Stretch 2 memory store (lazy). Defaults write_atoms=true / enabled=true
        # → never opened; meal still legacy until PR6.
        self._memory: Any | None = None
        self._memory_open_attempted = False
        self._memory_open_failed = False
        # Phase 2 encode queue + embedder + EmbeddingIndex (lazy; store open).
        self._encode_queue: Any | None = None
        self._embedder: Any | None = None
        self._embedder_open_failed = False
        self._embedding_index: Any | None = None
        self._embed_catchup_marked: int = 0  # process-life OQ4 none→pending count
        # Last labeled meal package inspect payload (glass Memory Context tab).
        self._last_meal_snapshot: dict[str, Any] | None = None

        self._phase: str = PHASE_IDLE
        self._busy = False
        self._active_moment_id: str | None = None
        self._worker_error: str | None = None
        self._hop_count = 0
        self._last_tool: str | None = None
        self._continue_injects = 0
        self._interject = InterjectBuffer()
        self._started = False

    # ------------------------------------------------------------------
    # Public API (lock-guarded)
    # ------------------------------------------------------------------

    def set_continuous_enabled(self, enabled: bool) -> dict[str, Any]:
        """Toggle continuous work; persist JSON; cancel pending continues on OFF.

        PR7 wires ``PATCH /api/continuous`` to this method. Does not invent wakes
        on ON. On OFF: cancel only pending ``moment_continue`` (not task_ready /
        timers / user), reset streak, clear last_skip_reason.
        """
        want = bool(enabled)
        with self._lock:
            if self._continuous.resetting:
                return {
                    "ok": False,
                    "error": "resetting",
                    "enabled": bool(self._continuous.enabled),
                }
            prev = bool(self._continuous.enabled)
            self._continuous.enabled = want
            try:
                save_continuous_enabled(self.paths.data_dir, want)
            except OSError as exc:
                _LOG.warning("persist continuous.json failed: %s", exc)
            cancelled: list[str] = []
            if not want:
                cancelled = self._queue.cancel_all_pending_of_kind(
                    "moment_continue", REASON_CONTINUOUS_DISABLED
                )
                self._continuous.streak = 0
                self._continuous.last_skip_reason = None
            pending_continues = len(
                self._queue.pending_of_kind("moment_continue")
            )
            block = continuous_status_block(
                self._continuous,
                self.settings.continuous,
                pending_moment_continues=pending_continues,
            )
            return {
                "ok": True,
                "enabled": want,
                "changed": prev != want,
                "cancelled_moment_continues": cancelled,
                "continuous": block,
            }

    def set_dev_speed(
        self,
        *,
        enabled: bool | None = None,
        delay_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Toggle / set inter-hop delay; persist ``data/runtime/dev_speed.json``.

        Does not invent wakes. When both args are None, returns current state.
        """
        with self._lock:
            if self._continuous.resetting:
                return {
                    "ok": False,
                    "error": "resetting",
                    "dev_speed": dev_speed_status_block(self._dev_speed),
                }
            prev_en = bool(self._dev_speed.enabled)
            prev_delay = float(self._dev_speed.delay_seconds)
            if enabled is not None:
                self._dev_speed.enabled = bool(enabled)
            if delay_seconds is not None:
                from elyra.runtime.dev_speed import clamp_delay_seconds

                self._dev_speed.delay_seconds = clamp_delay_seconds(delay_seconds)
            try:
                save_dev_speed_runtime(
                    self.paths.data_dir,
                    enabled=bool(self._dev_speed.enabled),
                    delay_seconds=float(self._dev_speed.delay_seconds),
                )
            except OSError as exc:
                _LOG.warning("persist dev_speed.json failed: %s", exc)
            block = dev_speed_status_block(self._dev_speed)
            return {
                "ok": True,
                "changed": (
                    prev_en != bool(self._dev_speed.enabled)
                    or abs(prev_delay - float(self._dev_speed.delay_seconds)) > 1e-9
                ),
                "dev_speed": block,
            }

    def reset_runtime_state(
        self, flags: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Full reset of ephemeral runtime product (K10/K11). Worker-owned.

        Precondition: not busy and phase != in_moment. Else
        ``{"ok": False, "error": "worker_busy", "phase": ...}``.

        Protocol (concurrent-safe 503 + path-store integrity)::

            acquire lock → reject busy/already-resetting → resetting=True → release
            disk path clears (flag visible; claim/enqueue/API writers refuse)
            acquire lock → timer/queue memory wipe + final messages/goals re-clear
                         → soft state + asserts → release
            finally: resetting=False under lock

        Concurrent API observes ``error=resetting`` (HTTP 503) without blocking
        on the full disk clear. Final messages/goals re-clear under lock closes
        TOCTOU windows where a writer raced mid-clear.

        Never clears ``skills/local``, identity, users, continuous.json enabled,
        model paths, or settings. Optional flags via ``normalize_reset_flags``.
        """
        norm = normalize_reset_flags(flags)
        with self._lock:
            if self._continuous.resetting:
                return {"ok": False, "error": "resetting"}
            if self._busy or self._phase == PHASE_IN_MOMENT:
                return {
                    "ok": False,
                    "error": "worker_busy",
                    "phase": self._phase,
                    "worker_busy": self._busy,
                }
            self._continuous.resetting = True
        try:
            return self._run_full_reset(norm)
        finally:
            with self._lock:
                self._continuous.resetting = False

    @property
    def is_resetting(self) -> bool:
        """True while a full reset is in progress (lock-free for writers to poll)."""
        with self._lock:
            return bool(self._continuous.resetting)

    def append_message_if_allowed(
        self,
        role: str,
        content: str,
        *,
        user_id: str | None = "operator",
        reasoning: str = "",
        moment_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        meta: dict[str, Any] | None = None,
        bind_attachment_ids: Sequence[str] | None = None,
    ) -> tuple[Message | None, dict[str, Any] | None]:
        """Append a chat message only when not resetting.

        Holds ``self._lock`` for the check + append so reset's final re-clear
        cannot interleave mid-write without also holding the lock.
        Attachments/meta are persisted on the same lock as content (KD1).
        When ``bind_attachment_ids`` is set, each id is validated (exists,
        unbound or already bound to the new message id after append) and
        ``bound_message_id`` is set under the same lock (PR3 / KD23).
        Returns ``(message, None)`` on success or ``(None, error_dict)``.
        """
        with self._lock:
            if self._continuous.resetting:
                return None, {
                    "ok": False,
                    "error": "resetting",
                    "reason": "resetting",
                }
            resolved_atts = attachments
            bind_ids = list(bind_attachment_ids) if bind_attachment_ids else []
            if bind_ids:
                from elyra.media import MediaStore

                store = MediaStore(self.paths)
                metas: list[dict[str, Any]] = []
                for aid in bind_ids:
                    att = store.get(aid)
                    if att is None:
                        return None, {
                            "ok": False,
                            "error": "attachment_not_found",
                            "reason": "attachment_not_found",
                            "attachment_id": aid,
                        }
                    if (
                        att.bound_message_id is not None
                        and att.bound_message_id != ""
                    ):
                        # Only allow re-bind to same message (idempotent); new
                        # message cannot steal another row's attachment.
                        return None, {
                            "ok": False,
                            "error": "attachment_bound",
                            "reason": "attachment_bound",
                            "attachment_id": aid,
                            "bound_message_id": att.bound_message_id,
                        }
                    metas.append(att.to_dict())
                if resolved_atts is None:
                    resolved_atts = metas
            msg = append_message(
                role,
                content,
                user_id=user_id,
                reasoning=reasoning,
                moment_id=moment_id,
                attachments=resolved_atts,
                meta=meta,
                paths=self.paths,
            )
            if bind_ids:
                from elyra.media import MediaStore

                store = MediaStore(self.paths)
                for aid in bind_ids:
                    store.bind_message(aid, msg.id)
            return msg, None

    def create_goal_if_allowed(
        self,
        title: str,
        *,
        acceptance: str | None = None,
        status: str = "open",
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Create a goal only when not resetting (path-store gate)."""
        with self._lock:
            if self._continuous.resetting:
                return None, {
                    "ok": False,
                    "error": "resetting",
                    "reason": "resetting",
                }
            goal = self._ensure_goals().create_goal(
                title, acceptance=acceptance, status=status
            )
            return goal, None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    @property
    def active_moment_id(self) -> str | None:
        with self._lock:
            return self._active_moment_id

    @property
    def pending_wait(self) -> dict[str, Any] | None:
        """First durable pending wait snapshot, or None."""
        with self._lock:
            return self._pending_wait_unlocked()

    @property
    def last_error(self) -> str | None:
        """Alias for status ``worker_error`` (scaffold API compat)."""
        with self._lock:
            return self._worker_error

    @property
    def pending(self) -> int:
        """Pending wake count (scaffold API compat)."""
        with self._lock:
            return len(self._queue.pending())

    @property
    def phase(self) -> str:
        with self._lock:
            return self._phase

    def enqueue_wake(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        wake_id: str | None = None,
    ) -> str:
        """Enqueue a wake by kind; returns wake id.

        Raises ``RuntimeError`` with message ``resetting`` while a full reset
        is in progress (API maps to 503).
        """
        with self._lock:
            self._raise_if_resetting_unlocked()
            item = self._queue.enqueue(kind, payload, wake_id=wake_id)
            return item.id

    def enqueue_user_message(
        self,
        content: str,
        *,
        user_id: str = "operator",
        message_id: str | None = None,
    ) -> str:
        """Enqueue a ``user_message`` wake; returns wake id."""
        mid = message_id or str(uuid.uuid4())
        with self._lock:
            self._raise_if_resetting_unlocked()
            item = self._queue.enqueue(
                "user_message",
                {
                    "content": content,
                    "user_id": user_id,
                    "message_id": mid,
                },
            )
            return item.id

    def interject(
        self,
        content: str,
        *,
        user_id: str = "operator",
        message_id: str | None = None,
    ) -> dict[str, Any]:
        """Buffer an interjection when ``phase == in_moment``.

        On buffer full: still an interject decision — return
        ``routed=interject``, ``ok=false``, ``reason=interjection_buffer_full``,
        and enqueue a durable ``user_message`` wake (do not drop). Clients should
        key glass notices off ``ok`` + ``reason`` (and optional ``wake_id``);
        ``routed`` stays ``interject`` so UI that branches on route does not
        treat overflow as a fresh idle chat.

        When not in a moment: enqueue as ``user_message`` instead.
        """
        text = content if isinstance(content, str) else str(content)
        with self._lock:
            if self._continuous.resetting:
                return {
                    "ok": False,
                    "error": "resetting",
                    "reason": "resetting",
                }
            if self._phase != PHASE_IN_MOMENT:
                wake_id = self._queue.enqueue(
                    "user_message",
                    {
                        "content": text,
                        "user_id": user_id,
                        "message_id": message_id or str(uuid.uuid4()),
                    },
                ).id
                return {
                    "ok": True,
                    "routed": ROUTE_USER_MESSAGE,
                    "wake_id": wake_id,
                    "reason": "not_in_moment",
                }
            item = InterjectItem(
                content=text, user_id=user_id, message_id=message_id
            )
            ok, reason = self._interject.try_add(item)
            if ok:
                return {"ok": True, "routed": ROUTE_INTERJECT}
            # Overflow → durable wake (do not drop); keep routed=interject.
            wake_id = self._queue.enqueue(
                "user_message",
                {
                    "content": text,
                    "user_id": user_id,
                    "message_id": message_id or str(uuid.uuid4()),
                    "from_interject_overflow": True,
                },
            ).id
            return {
                "ok": False,
                "routed": ROUTE_INTERJECT,
                "reason": reason or REASON_BUFFER_FULL,
                "wake_id": wake_id,
            }

    def resolve_user_input(
        self,
        content: str,
        user_id: str = "operator",
        choice: str | None = None,
        *,
        from_wait_api: bool = False,
        message_id: str | None = None,
        has_attachments: bool = False,
    ) -> dict[str, Any]:
        """Route user input via the phase/wait state machine; apply side effects."""
        with self._lock:
            if self._continuous.resetting:
                return {
                    "ok": False,
                    "error": "resetting",
                    "reason": "resetting",
                }
            pending = self._pending_wait_unlocked()
            decision = decide_user_input(
                content,
                user_id,
                choice,
                from_wait_api=from_wait_api,
                phase=self._phase,
                pending_wait=pending,
                has_attachments=has_attachments,
            )
            if not decision.get("ok"):
                return dict(decision)

            routed = decision["routed"]
            if routed == ROUTE_INTERJECT:
                result = self.interject(
                    content, user_id=user_id, message_id=message_id
                )
                out = dict(decision)
                out.update(result)
                return out

            if routed == ROUTE_WAIT_REPLY:
                return self._apply_wait_reply_unlocked(
                    content=content,
                    user_id=user_id,
                    choice=choice,
                    wait_id=decision.get("answer_wait_id"),
                    message_id=message_id,
                )

            # user_message
            if decision.get("cancel_stale_wait") and pending is not None:
                wid = pending.get("id") or pending.get("wait_id")
                if wid:
                    try:
                        self._timers.cancel_wait(str(wid))
                    except KeyError:
                        pass
                if self._phase == PHASE_WAITING and not self._timers.list_waits(
                    status=STATUS_PENDING
                ):
                    self._phase = PHASE_IDLE

            mid = message_id or str(uuid.uuid4())
            text = content if isinstance(content, str) else str(content)
            item = self._queue.enqueue(
                "user_message",
                {
                    "content": text,
                    "user_id": user_id,
                    "message_id": mid,
                },
            )
            out = dict(decision)
            out["wake_id"] = item.id
            out["message_id"] = mid
            return out

    def status_snapshot(self) -> dict[str, Any]:
        """Snapshot for ``/api/status`` (phase, hops, queue depths, wait)."""
        with self._lock:
            pending_continues = len(self._queue.pending_of_kind("moment_continue"))
            pending_wait = self._pending_wait_unlocked()
            # Live tape summary while a moment is open (beats append mid-loop).
            recent: list[dict[str, Any]] = []
            live_hop = self._hop_count
            live_tool = self._last_tool
            mid = self._active_moment_id
            if mid and self._phase in (PHASE_IN_MOMENT, PHASE_WAITING):
                recent, live_hop, live_tool = self._live_activity_from_tape(
                    mid, hop_fallback=live_hop, tool_fallback=live_tool
                )
            activity = _activity_headline(
                phase=self._phase,
                recent=recent,
                pending_wait=pending_wait,
                hop_count=live_hop,
                last_tool=live_tool,
            )
            loop = self.settings.loop
            meal_budget = min(
                int(loop.sliding_input_tokens),
                int(loop.in_turn_max_tokens),
            )
            try:
                from elyra.loop import context_meter

                context_block = context_meter.status_block(
                    meal_budget_tokens=meal_budget,
                    model_window_tokens=int(loop.model_context_window_tokens),
                )
            except Exception:
                context_block = {
                    "meal_used_tokens": 0,
                    "meal_budget_tokens": meal_budget,
                    "model_window_tokens": int(
                        getattr(loop, "model_context_window_tokens", 500_000)
                    ),
                    "meal_used_fraction": 0.0,
                    "window_used_fraction": 0.0,
                    "hop": None,
                    "moment_id": None,
                }
            return {
                "phase": self._phase,
                "active_moment_id": self._active_moment_id,
                "hop_count": live_hop,
                "last_tool": live_tool,
                "activity": activity,
                "recent_activity": recent,
                "continue_injects": self._continue_injects,
                "queue_depth_by_band": self._queue_depth_by_band_unlocked(),
                "pending_wait": pending_wait,
                "worker_error": self._worker_error,
                "worker_busy": self._busy,
                "worker_pending": len(self._queue.pending()),
                "interject_depth": self._interject.depth,
                "resetting": bool(self._continuous.resetting),
                "continuous": continuous_status_block(
                    self._continuous,
                    self.settings.continuous,
                    pending_moment_continues=pending_continues,
                ),
                "dev_speed": dev_speed_status_block(self._dev_speed),
                "context": context_block,
                "memory": self._memory_status_block(),
            }

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Blocking poll loop (run on the presence thread)."""
        _LOG.info("presence worker started")
        try:
            self._startup_recover()
            self._ensure_memory_store()
            self._started = True
            while not self._stop.is_set():
                wake: WakeItem | None = None
                moment_id: str | None = None
                try:
                    claimed = self._claim_and_open()
                    if claimed is None:
                        # Still fire due timers/waits while idle.
                        with self._lock:
                            self._fire_due_unlocked()
                        # Ladder refresh OUTSIDE lock (PR5 normative placement).
                        self._idle_memory_ladder()
                        # Corpus encode drain OUTSIDE lock (KD2 / KD16).
                        self._idle_memory_encode()
                        # ANN optimize OUTSIDE lock — never mid-hop (KD4).
                        self._idle_memory_optimize()
                        self._stop.wait(timeout=self._poll)
                        continue
                    wake, moment_id = claimed
                    result, skills_used = self._run_moment(wake, moment_id)
                    self._finalize_moment(
                        wake, moment_id, result, skills_used=skills_used
                    )
                except Exception as exc:  # noqa: BLE001 — keep worker alive
                    _LOG.exception("presence worker iteration failed: %s", exc)
                    self._fail_in_flight(wake, moment_id, exc)
                    self._stop.wait(timeout=self._poll)
        finally:
            # Close Playwright on the owner thread (sync API is not cross-thread).
            # Supervisor close_all is a safety net only after join.
            try:
                from elyra.tools.browser_sessions import get_browser_session_manager

                get_browser_session_manager().close_all(force=True)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("browser close_all on worker stop failed: %s", exc)
            _LOG.info("presence worker stopped")

    def _startup_recover(self) -> None:
        """Interrupt open moments, redeliver claimed wakes, rehydrate timers/waits."""
        with self._lock:
            closed = self._moments.recover_open_moments()
            if closed:
                _LOG.warning(
                    "recovered %d open moment(s) as interrupted: %s",
                    len(closed),
                    closed,
                )
            reenqueued = self._queue.recover_claimed()
            if reenqueued:
                _LOG.warning(
                    "recover_claimed re-enqueued %d wake(s)",
                    len(reenqueued),
                )
            fired = self._timers.rehydrate()
            if fired:
                _LOG.info(
                    "rehydrate enqueued %d due timer/wait wake(s)", len(fired)
                )
            if self._timers.list_waits(status=STATUS_PENDING):
                self._phase = PHASE_WAITING
            else:
                self._phase = PHASE_IDLE

    def _fire_due_unlocked(self) -> None:
        """Poll timers/waits into the wake queue (caller holds lock)."""
        try:
            self._timers.schedule_due()
            self._timers.check_timeouts()
        except Exception:  # noqa: BLE001
            _LOG.exception("timer/wait poll failed")

    def _ensure_memory_store(self) -> Any | None:
        """Open MemoryStore once when write_atoms or enabled; never raise.

        Defaults both false → returns None without opening (legacy path).
        On open failure: log once, leave ``self._memory`` None for the life of
        the worker (promote/ladder become no-ops).
        """
        mem_cfg = self.settings.memory
        if not (mem_cfg.write_atoms or mem_cfg.enabled):
            return None
        if self._memory is not None:
            return self._memory
        if self._memory_open_failed:
            return None
        if self._memory_open_attempted and self._memory is None:
            return None
        self._memory_open_attempted = True
        try:
            from elyra.memory.store import open_memory_store

            self._memory = open_memory_store(self.paths, mem_cfg)
            self._install_encode_hooks(self._memory, mem_cfg)
            self._ensure_embedding_index()
            return self._memory
        except Exception:  # noqa: BLE001 — store down must not kill presence
            self._memory_open_failed = True
            self._memory = None
            _LOG.exception("memory store open failed; atoms disabled this run")
            return None

    def _install_encode_hooks(self, store: Any, mem_cfg: Any) -> None:
        """Install store write hook + EncodeQueue (KD16). Best-effort."""
        try:
            from elyra.memory.embed.queue import EncodeQueue

            maxsize = int(getattr(mem_cfg, "encode_queue_max", 1024) or 1024)
            queue = EncodeQueue(maxsize=maxsize)
            self._encode_queue = queue

            def _on_written(atom: Any) -> None:
                # Hook must never raise to put_atom (store already guards).
                try:
                    cfg = self.settings.memory
                    if not cfg.semantic_enabled:
                        return
                    # Only enqueue when drain can run; otherwise backlog
                    # overflows mark atoms skipped (semantic+embed-off must
                    # leave pending until embed is enabled — KD intent).
                    # Idle pending scan fills the queue once embed turns on.
                    if not cfg.embed_enabled:
                        return
                    if getattr(atom, "embedding_status", None) != "pending":
                        return
                    # KD16 re-put: already encode-ok with same content → no-op.
                    meta = getattr(atom, "meta", None) or {}
                    if meta.get("embed_encode_ok"):
                        try:
                            from elyra.memory.embed.encode import (
                                content_fingerprint,
                            )

                            if meta.get("embed_content_fp") == content_fingerprint(
                                atom
                            ):
                                return
                        except Exception:  # noqa: BLE001
                            pass
                    queue.enqueue(atom.atom_id, store=store)
                except Exception:  # noqa: BLE001
                    _LOG.exception(
                        "encode write hook failed atom_id=%s",
                        getattr(atom, "atom_id", "?"),
                    )

            set_hook = getattr(store, "set_write_hook", None)
            if callable(set_hook):
                set_hook(_on_written)
        except Exception:  # noqa: BLE001
            _LOG.exception("install encode hooks failed")

    def _ensure_embedder(self) -> Any | None:
        """Open embedder once when embed_enabled; never raise."""
        mem_cfg = self.settings.memory
        if not mem_cfg.embed_enabled:
            return None
        if self._embedder is not None:
            return self._embedder
        if self._embedder_open_failed:
            return None
        try:
            from elyra.memory.embed.runtime import open_encoder

            self._embedder = open_encoder(mem_cfg)
            return self._embedder
        except Exception:  # noqa: BLE001
            self._embedder_open_failed = True
            self._embedder = None
            _LOG.exception("embedder open failed; encode drain disabled this run")
            return None

    def _ensure_embedding_index(self) -> Any | None:
        """Open EmbeddingIndex once for the memory store (KD4 freshness).

        Best-effort; Null index for JSONL. Lance path seeds buffer / full mode
        on open. Never raises into the presence loop.
        """
        if self._embedding_index is not None:
            return self._embedding_index
        store = self._memory
        if store is None:
            return None
        try:
            from elyra.memory.index import open_embedding_index

            self._embedding_index = open_embedding_index(
                store, settings=self.settings.memory
            )
            return self._embedding_index
        except Exception:  # noqa: BLE001
            _LOG.exception("embedding index open failed")
            self._embedding_index = None
            return None

    def _memory_ladder_active(self) -> bool:
        """True when ladder should run on idle / finalize (PR5 placement)."""
        mem_cfg = self.settings.memory
        if not mem_cfg.ladder_enabled:
            return False
        if not (mem_cfg.write_atoms or mem_cfg.enabled):
            return False
        return self._ensure_memory_store() is not None

    def _memory_meal_active(self) -> bool:
        """True when rebuild_outer should use labeled memory meal (PR6).

        Requires ``memory.enabled`` and a healthy open store. Flag off or
        store down → legacy assemble_outer_meal + expand.
        """
        if not self.settings.memory.enabled:
            return False
        store = self._ensure_memory_store()
        if store is None:
            return False
        try:
            health = store.health()
        except Exception:  # noqa: BLE001
            return False
        if not isinstance(health, Mapping):
            return False
        return bool(health.get("ok"))

    def _memory_status_block(self) -> dict[str, Any]:
        """Lightweight memory health for ``/api/status`` (optional PR6)."""
        mem_cfg = self.settings.memory
        block: dict[str, Any] = {
            "enabled": bool(mem_cfg.enabled),
            "write_atoms": bool(mem_cfg.write_atoms),
            "backend": str(mem_cfg.backend),
            "store_open": self._memory is not None,
            "ok": False,
            "has_last_meal": self._last_meal_snapshot is not None,
        }
        if self._memory is None:
            if self._memory_open_failed:
                block["error"] = "open_failed"
            elif not (mem_cfg.write_atoms or mem_cfg.enabled):
                block["error"] = "disabled"
            return block
        try:
            health = self._memory.health()
            if isinstance(health, Mapping):
                block["ok"] = bool(health.get("ok"))
                for key in ("atom_count", "line_count", "backend", "error"):
                    if key in health:
                        block[key] = health[key]
            else:
                block["ok"] = True
        except Exception as exc:  # noqa: BLE001
            block["ok"] = False
            block["error"] = str(exc) or type(exc).__name__
        return block

    def last_meal_snapshot(self) -> dict[str, Any] | None:
        """Return a copy of the last composed meal inspect payload, if any."""
        with self._lock:
            snap = self._last_meal_snapshot
            return dict(snap) if isinstance(snap, dict) else None

    def _record_last_meal_snapshot(
        self,
        package: Any,
        *,
        system_text: str = "",
        orient_text: str = "",
        budget_tokens: int | None = None,
        source: str = "rebuild_outer",
    ) -> None:
        """Best-effort: stash inspect payload for glass Memory Context tab."""
        try:
            from elyra.memory.inspect import meal_package_to_inspect
            from elyra.memory.types import utc_now_iso

            payload = meal_package_to_inspect(
                package,
                system_text=system_text,
                orient_text=orient_text,
                budget_tokens=budget_tokens,
                source=source,
                recorded_at=utc_now_iso(),
            )
            with self._lock:
                self._last_meal_snapshot = payload
        except Exception:  # noqa: BLE001 — never break rebuild for glass
            _LOG.exception("record last meal snapshot failed")

    def _idle_memory_ladder(self) -> None:
        """Budgeted period-summary refresh outside the state lock (idle only)."""
        if not self._memory_ladder_active():
            return
        store = self._memory
        if store is None:
            return
        try:
            from elyra.memory.ladder import refresh_due

            max_ms = int(self.settings.memory.ladder_max_ms_per_tick)
            refresh_due(store, max_ms=max_ms)
        except Exception:  # noqa: BLE001
            _LOG.exception("memory ladder refresh_due failed")
        self._maybe_compact_memory_store()

    def _idle_memory_encode(self) -> None:
        """Idle-only corpus encode: pending scan + queue drain (KD2 / KD16).

        Outside the state lock; never runs in-moment / hop path. Drain only
        when ``embed_enabled``; hooks still enqueue when ``semantic_enabled``.
        PR2 never marks ``ready`` without an index (leave pending).
        """
        mem_cfg = self.settings.memory
        if not mem_cfg.semantic_enabled:
            return
        if not mem_cfg.embed_enabled:
            return
        store = self._memory
        if store is None:
            store = self._ensure_memory_store()
        if store is None:
            return
        queue = self._encode_queue
        if queue is None:
            # Store opened before hooks existed or install failed — re-install.
            self._install_encode_hooks(store, mem_cfg)
            queue = self._encode_queue
        if queue is None:
            return
        try:
            from elyra.memory.embed.queue import (
                catchup_none_atoms_for_encode,
                scan_pending_into_queue,
            )

            max_items = max(1, int(mem_cfg.encode_max_items_per_tick or 4))
            # OQ4: historical atoms written before semantic_on stay ``none``;
            # flip a budgeted batch to pending so drain can fill vectors.
            # Process-lifetime budget: stop once catchup_max marked this run.
            catchup_budget = int(getattr(mem_cfg, "embed_catchup_max", 500) or 0)
            already = int(getattr(self, "_embed_catchup_marked", 0) or 0)
            if catchup_budget > 0 and already < catchup_budget:
                per_tick = int(
                    getattr(mem_cfg, "embed_catchup_per_tick", 32) or 32
                )
                room = min(per_tick, catchup_budget - already)
                n = catchup_none_atoms_for_encode(
                    store,
                    limit=room,
                    horizon_hours=float(
                        getattr(mem_cfg, "embed_catchup_horizon_hours", 168.0)
                        or 168.0
                    ),
                )
                self._embed_catchup_marked = already + int(n or 0)
            scan_pending_into_queue(
                store,
                queue,
                limit=max_items * 4,
            )
            embedder = self._ensure_embedder()
            if embedder is None:
                return
            media_store = None
            try:
                from elyra.media.store import MediaStore

                media_store = MediaStore(self.paths)
            except Exception:  # noqa: BLE001 — media optional for encode
                _LOG.debug("MediaStore open for encode failed", exc_info=True)
            index = self._ensure_embedding_index()
            queue.drain(
                store,
                embedder,
                index=index,
                max_ms=int(mem_cfg.encode_max_ms_per_tick or 100),
                max_items=max_items,
                max_attempts=int(mem_cfg.encode_max_attempts or 3),
                media_store=media_store,
                settings=mem_cfg,  # threads embed_media_max_bytes/seconds
            )
        except Exception:  # noqa: BLE001 — never kill presence
            _LOG.exception("memory idle encode drain failed")

    def rebuild_vector_index(self, *, max_ms: int | None = None) -> dict[str, Any]:
        """Operator-triggered ANN index rebuild (glass Vectors button).

        ANN here means **approximate nearest-neighbor** vector index (Lance),
        not re-loading Nemotron. Rebuilds the search index over **already
        stored** vectors; does not re-encode atoms. Best-effort; never raises.
        """
        mem_cfg = self.settings.memory
        store = self._memory
        if store is None and (mem_cfg.write_atoms or mem_cfg.enabled or mem_cfg.semantic_enabled):
            store = self._ensure_memory_store()
        if store is None:
            return {
                "ok": False,
                "error": "store_unavailable",
                "optimized": False,
            }
        index = self._ensure_embedding_index()
        if index is None:
            return {
                "ok": False,
                "error": "index_unavailable",
                "optimized": False,
            }
        try:
            seed_fn = getattr(index, "seed_buffer", None)
            if callable(seed_fn):
                try:
                    seed_fn(max_ms=int(mem_cfg.ann_optimize_max_ms or 200))
                except Exception:  # noqa: BLE001
                    _LOG.debug("index seed_buffer on rebuild failed", exc_info=True)
            # Operator button: allow longer than idle soft budget (default 5s).
            budget = max_ms
            if budget is None:
                idle = int(getattr(mem_cfg, "ann_optimize_max_ms", 200) or 200)
                budget = max(idle, 5000)
            result = index.optimize(max_ms=int(budget))
            if not isinstance(result, dict):
                result = {"ok": True, "optimized": bool(result), "result": result}
            health = index.health() if hasattr(index, "health") else {}
            out = dict(result)
            out["health"] = health if isinstance(health, dict) else {}
            out.setdefault("ok", True)
            _LOG.info("memory vector index rebuild: %s", out)
            return out
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("memory vector index rebuild failed")
            return {
                "ok": False,
                "error": str(exc) or type(exc).__name__,
                "optimized": False,
            }

    def _idle_memory_optimize(self) -> None:
        """Idle-only ANN optimize / buffer seed (KD4). Never mid-hop.

        Runs outside the state lock after encode drain. Soft ``ann_optimize_max_ms``
        only; meal hard budget is PR6.
        """
        mem_cfg = self.settings.memory
        # Index may exist for Lance even when semantic is off (vectors on disk);
        # only schedule work when semantic path is active or index already open.
        if not (mem_cfg.semantic_enabled or self._embedding_index is not None):
            return
        store = self._memory
        if store is None and (mem_cfg.write_atoms or mem_cfg.enabled):
            store = self._ensure_memory_store()
        if store is None:
            return
        index = self._ensure_embedding_index()
        if index is None:
            return
        try:
            # Continue budgeted seed if open left it incomplete.
            seed_fn = getattr(index, "seed_buffer", None)
            if callable(seed_fn):
                try:
                    h0 = index.health() if hasattr(index, "health") else {}
                    if isinstance(h0, dict) and h0.get("seed_incomplete"):
                        seed_fn(max_ms=int(mem_cfg.ann_optimize_max_ms or 200))
                except Exception:  # noqa: BLE001
                    _LOG.debug("index seed_buffer failed", exc_info=True)

            health = index.health() if hasattr(index, "health") else {}
            if not isinstance(health, dict):
                return
            if not health.get("index_stale"):
                return
            max_ms = int(getattr(mem_cfg, "ann_optimize_max_ms", 200) or 200)
            result = index.optimize(max_ms=max_ms)
            _LOG.debug("memory index optimize: %s", result)
        except Exception:  # noqa: BLE001 — never kill presence
            _LOG.exception("memory idle index optimize failed")

    def _finalize_memory_ladder_15m(self) -> None:
        """Refresh the 15m window containing now after moment close (budgeted)."""
        if not self._memory_ladder_active():
            return
        store = self._memory
        if store is None:
            return
        try:
            from elyra.memory.ladder import refresh_window

            refresh_window(store, "15m", datetime.now(UTC))
        except Exception:  # noqa: BLE001
            _LOG.exception("memory ladder 15m finalize refresh failed")
        self._maybe_compact_memory_store()

    def _maybe_compact_memory_store(self) -> None:
        """Idle-only JSONL rewrite when dirty lines / size exceed thresholds."""
        store = self._memory
        if store is None:
            return
        maybe = getattr(store, "maybe_compact", None)
        if not callable(maybe):
            return
        try:
            if maybe():
                _LOG.info("memory store compacted (jsonl latest-wins rewrite)")
        except Exception:  # noqa: BLE001
            _LOG.exception("memory store maybe_compact failed")

    def _promote_social_wake_unlocked(
        self,
        wake: WakeItem,
        moment_id: str,
        why: str,
    ) -> None:
        """Promote social wake observation after open_moment (caller holds lock).

        Best-effort: never raises; no-op when write_atoms false or store down.
        Non-social wakes must not call this (R6 / BUG-wake-01 density).
        """
        mem_cfg = self.settings.memory
        if not mem_cfg.write_atoms:
            return
        store = self._ensure_memory_store()
        if store is None:
            return
        payload = wake.payload or {}
        content = payload.get("content")
        content_s = str(content) if content is not None else None
        message_id = payload.get("message_id")
        message_id_s = str(message_id) if message_id is not None else None
        media_ids = _media_ids_from_wake(wake, paths=self.paths)
        try:
            from elyra.memory.promote import promote_wake_observation

            promote_wake_observation(
                store,
                moment_id,
                content=content_s,
                message_id=message_id_s,
                media_ids=media_ids,
                why_now=why,
                settings=mem_cfg,
            )
        except Exception:  # noqa: BLE001 — never abort claim/open
            _LOG.exception(
                "memory promote_wake_observation failed moment_id=%s",
                moment_id,
            )

    def _claim_and_open(self) -> tuple[WakeItem, str] | None:
        """Under lock: fire due work, claim one wake, open moment, set phase.

        If claim succeeds but ``open_moment`` fails, the wake is cancelled so it
        is not left stuck in ``claimed``. Skips claim while full reset runs.

        Pre-claim gate: when ``model_available()`` is false (usage hard-stop with
        override OFF, or ``!credential_ok`` / FailingChatClient), do **not**
        claim — wakes stay pending (never cancelled). Timers still fire via
        ``_fire_due_unlocked`` so due work lands on the queue.
        """
        with self._lock:
            if self._continuous.resetting:
                return None
            self._fire_due_unlocked()
            # Pre-claim model gate (usage hard-stop / missing credentials).
            try:
                available = bool(self._model_available())
            except Exception:  # noqa: BLE001 — never block worker on gate errors
                available = False
                now = time.monotonic()
                if now - self._model_unavailable_log_at >= 60.0:
                    self._model_unavailable_log_at = now
                    _LOG.exception(
                        "model_available hook failed; treating as unavailable"
                    )
            if not available:
                pending = self._queue.pending()
                if pending:
                    now = time.monotonic()
                    if now - self._model_unavailable_log_at >= 60.0:
                        self._model_unavailable_log_at = now
                        _LOG.warning(
                            "model_available=false; skipping claim "
                            "(%d pending wake(s) left untouched)",
                            len(pending),
                        )
                return None
            moment_id = str(uuid.uuid4())
            wake = self._queue.claim(moment_id)
            if wake is None:
                return None
            user_id = _user_id_from_wake(wake)
            why = _why_now(wake)
            try:
                self._moments.open_moment(
                    why_now=why,
                    user_id=user_id,
                    wake_id=wake.id,
                    moment_id=moment_id,
                )
            except Exception:
                # Do not leave a claimed-without-terminal wake.
                try:
                    self._queue.cancel(wake.id, "open_moment_failed")
                except KeyError:
                    pass
                raise
            # User-band claim resets continuous streak (design C runtime state).
            if wake.kind in SOCIAL_WAKE_KINDS:
                self._continuous.streak = 0
                # Promote social wake observation while still under lock is fine
                # (best-effort; failures never abort open). Call after open_moment.
                self._promote_social_wake_unlocked(wake, moment_id, why)

            self._phase = PHASE_IN_MOMENT
            self._busy = True
            self._active_moment_id = moment_id
            self._worker_error = None
            self._hop_count = 0
            self._last_tool = None
            self._continue_injects = 0
            self._interject.clear()
            return wake, moment_id

    def _run_moment(
        self, wake: WakeItem, moment_id: str
    ) -> tuple[DoLoopResult, list[str]]:
        """Run do-loop outside the state lock (interjects still lock-protected).

        Returns ``(result, skills_used)`` so close can persist skills loaded
        during the moment even if the loop returns without a live ctx ref.
        """
        ctx = self._build_tool_context(wake, moment_id)
        social = wake.kind in SOCIAL_WAKE_KINDS
        payload = wake.payload or {}
        wake_content = payload.get("content")
        wake_content_s = (
            str(wake_content) if wake_content is not None else None
        )
        wake_message_id = payload.get("message_id")
        wake_message_id_s = (
            str(wake_message_id) if wake_message_id is not None else None
        )
        why = _why_now(wake)

        # Snapshot continuous + open-work for in-moment work_context (no lock
        # held during do-loop; toggle mid-moment takes effect next moment).
        with self._lock:
            cont_on = bool(self._continuous.enabled)
        has_open = self._has_open_work()

        def rebuild_outer() -> list[dict[str, Any]]:
            # Re-read glass + goals from disk every rebuild (ledger edits mid-moment
            # appear). Catalog is the held SkillCatalog snapshot — growth tools
            # reload it via ctx.extras["skills"] (install_skill); do not cache
            # formatted strings at moment open.
            # USER inject: work-origin policy (K13/K19) — social speaker, else
            # linked goal/task created_in_context (PR4), else empty — never
            # blind "operator" fallback.
            # Multimodal (KD20/KD25): every rebuild re-runs assemble/compose
            # → expand → strip_meal_wire_fields. Never stash expanded parts
            # across hops; never expand after ids are stripped.
            # Memory path (PR6): when enabled + store healthy use labeled meal
            # (no full sliding glass) + expand_memory_meal_for_provider.
            glass = list_messages(limit=80, paths=self.paths)
            self_digest = self._identity.self_digest()
            _orient_uid, user_digest = resolve_orient_user(
                wake,
                users=self._users,
                goals=self._ensure_goals(),
            )
            loop = self.settings.loop
            catalog = self._ensure_skills().catalog()
            goals_list = self._ensure_goals().list_goals()
            protect_goal_ids: set[str] = set()
            protect_task_ids: set[str] = set()
            if payload.get("goal_id"):
                protect_goal_ids.add(str(payload["goal_id"]))
            if payload.get("task_id"):
                protect_task_ids.add(str(payload["task_id"]))
            goals_slice = format_goals_slice(
                goals_list,
                max_tokens=loop.orient_goals_max_tokens,
                protect_goal_ids=protect_goal_ids or None,
                protect_task_ids=protect_task_ids or None,
            )
            skill_catalog_s = format_skill_catalog(
                catalog,
                max_tokens=loop.orient_skill_catalog_max_tokens,
            )
            skill_bias_s = format_skill_bias(wake.kind, payload, goals_list)

            from elyra.media import MediaStore
            from elyra.media.prompt import (
                expand_meal_for_provider,
                index_glass,
                strip_meal_wire_fields,
            )

            media_store = MediaStore(self.paths)
            glass_by_id = index_glass(glass)
            provider_name = self.settings.provider.name

            use_memory_meal = self._memory_meal_active()
            if use_memory_meal:
                try:
                    from elyra.loop.context import fill_orient, format_now
                    from elyra.memory.meal import (
                        compose_meal,
                        compose_outer_messages,
                        expand_memory_meal_for_provider,
                    )
                    from elyra.prompts.loader import load_prompt

                    system_text = load_prompt("system", paths=self.paths)
                    orient_template = load_prompt("orient", paths=self.paths)
                    orient_body = fill_orient(
                        orient_template,
                        now=format_now(),
                        self_digest=self_digest,
                        user_digest=user_digest,
                        why_now=why,
                        goals=goals_slice,
                        skill_catalog=skill_catalog_s,
                        skill_bias=skill_bias_s,
                    )
                    budget = int(loop.sliding_input_tokens)
                    mem_cfg = self.settings.memory
                    # Semantic select: pass index (cheap open) + warm embedder
                    # only (KD12 — no cold model load inside rebuild_outer).
                    meal_index = None
                    meal_embedder = None
                    if mem_cfg.semantic_enabled:
                        meal_index = self._ensure_embedding_index()
                        meal_embedder = self._embedder
                    package = compose_meal(
                        self._memory,
                        open_moment_id=moment_id,
                        budget_tokens=budget,
                        system_text=system_text,
                        orient_text=orient_body,
                        settings=mem_cfg,
                        index=meal_index,
                        embedder=meal_embedder,
                    )
                    self._record_last_meal_snapshot(
                        package,
                        system_text=system_text,
                        orient_text=orient_body,
                        budget_tokens=budget,
                        source="rebuild_outer",
                    )
                    meal = compose_outer_messages(
                        self._memory,
                        open_moment_id=moment_id,
                        budget_tokens=budget,
                        system_text=system_text,
                        orient_text=orient_body,
                        settings=mem_cfg,
                        package=package,
                        index=meal_index,
                        embedder=meal_embedder,
                    )
                    expanded = expand_memory_meal_for_provider(
                        meal,
                        glass_by_id=glass_by_id,
                        wake_message_id=wake_message_id_s,
                        media_store=media_store,
                        provider=provider_name,
                    )
                    return strip_meal_wire_fields(expanded)
                except Exception:  # noqa: BLE001 — fall back to legacy meal
                    _LOG.exception(
                        "memory meal rebuild failed; falling back to glass meal"
                    )

            meal = assemble_outer_meal(
                glass_history=glass,
                settings=self.settings,
                paths=self.paths,
                self_digest=self_digest,
                user_digest=user_digest,
                why_now=why,
                goals=goals_slice,
                skill_catalog=skill_catalog_s,
                skill_bias=skill_bias_s,
                wake_content=wake_content_s,
                wake_message_id=wake_message_id_s,
                retain_ids=True,
            )
            expanded = expand_meal_for_provider(
                meal,
                glass_by_id=glass_by_id,
                wake_message_id=wake_message_id_s,
                media_store=media_store,
                provider=provider_name,
            )
            return strip_meal_wire_fields(expanded)

        registry = self._ensure_registry()
        with self._lock:
            hop_delay = effective_hop_delay_seconds(self._dev_speed)
        mem = self._ensure_memory_store()
        result = self._run_do_loop(
            client=self.client,
            registry=registry,
            ctx=ctx,
            rebuild_outer=rebuild_outer,
            settings=self.settings,
            moments=self._moments,
            social_wake=social,
            wake_kind=wake.kind,
            has_open_goals_slice=has_open,
            continuous_enabled=cont_on,
            hop_delay_seconds=hop_delay,
            drain_interjections=self._drain_interjections,
            memory_store=mem,
            memory_settings=self.settings.memory,
        )
        return result, list(ctx.skills_used)

    def _finalize_moment(
        self,
        wake: WakeItem,
        moment_id: str,
        result: DoLoopResult,
        *,
        skills_used: list[str] | None = None,
    ) -> None:
        """Close moment, mark wake done, phase → waiting|idle; maybe continue."""
        with self._lock:
            stop = result.stop_reason if result else "error"
            if stop not in STOP_REASONS:
                stop = "error"
            hop = int(result.hop_count) if result else 0
            skills = list(skills_used) if skills_used is not None else []
            try:
                self._moments.close_moment(
                    moment_id,
                    stop,
                    hop_count=hop,
                    skills_used=skills,
                )
            except (KeyError, ValueError) as exc:
                _LOG.warning("close_moment failed: %s", exc)

            # Browser sessions bound to this moment (Playwright) — best-effort.
            self._close_browser_sessions_for_moment(moment_id)

            try:
                self._queue.mark_done(wake.id)
            except KeyError:
                _LOG.warning("mark_done unknown wake_id=%s", wake.id)

            self._hop_count = hop
            self._continue_injects = (
                int(result.continue_injects) if result else 0
            )
            self._last_tool = self._last_tool_from_tape(moment_id)

            # Leftover interjects become wakes after the moment closes.
            self._flush_interjects_as_wakes_unlocked()

            arm = result.arm_wait if result else None
            if stop == "wait" or arm is not None:
                self._ensure_wait_armed_unlocked(arm, moment_id)
            # Phase waiting only when a durable pending wait exists — never
            # label waiting with pending_wait is None (stub/misbehaving loop).
            self._phase = self._phase_from_pending_waits_unlocked()
            if stop == "wait" and self._phase != PHASE_WAITING:
                _LOG.warning(
                    "wait stop without durable pending wait "
                    "(arm_wait=%s); phase=idle",
                    arm.wait_id if arm is not None else None,
                )

            # Streak: consecutive moment_continue moments completed (not
            # task_ready). Only while continuous still ON — mid-flight toggle
            # OFF resets streak and must not leave residual +1 in status.
            if wake.kind == "moment_continue" and self._continuous.enabled:
                self._continuous.streak = int(self._continuous.streak) + 1

            # Continuous outer re-entry (K4/K15/K16): never enqueue_task_ready.
            self._maybe_enqueue_moment_continue_unlocked(
                wake, moment_id, result
            )

            self._busy = False
            self._active_moment_id = None
            if result and result.error:
                self._worker_error = result.error

        # After close (outside long critical sections): refresh 15m window for
        # the just-ended moment when atom writes or meal are active.
        self._finalize_memory_ladder_15m()

    def _maybe_enqueue_moment_continue_unlocked(
        self,
        wake: WakeItem,
        moment_id: str,
        result: DoLoopResult | None,
    ) -> None:
        """Progress-gated outer re-wake after close (caller holds lock).

        Prefer *pending* ``task_ready`` only — never call ``enqueue_task_ready``
        / re-arm ledger ready tasks (K4/K16). Speak-only moments never continue
        (K15: tools_ran is non-speak only).
        """
        cont = self._continuous
        cfg = self.settings.continuous
        stop = (result.stop_reason if result else "error") or "error"
        if stop not in STOP_REASONS:
            stop = "error"

        tools_ran = bool(result.tools_ran) if result else False
        ledger_mutated = bool(result.ledger_mutated) if result else False
        model_beats = int(result.model_beats) if result else 0
        flood_beats = int(result.channel_flood_beats) if result else 0
        last_flood = bool(result.last_stop_hop_was_flood) if result else False

        pending_task_ready = len(self._queue.pending_of_kind("task_ready"))
        pending_continues = len(self._queue.pending_of_kind("moment_continue"))
        has_pending_wait = bool(
            self._timers.list_waits(status=STATUS_PENDING)
        )
        # Re-read ledger at finalize so mid-moment closes are respected (K18).
        has_open = self._has_open_work()

        seconds_since: float | None = None
        if cont.last_enqueue_at is not None:
            last = cont.last_enqueue_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=UTC)
            seconds_since = (
                datetime.now(UTC) - last.astimezone(UTC)
            ).total_seconds()

        decision = should_enqueue_moment_continue(
            continuous_enabled=bool(cont.enabled),
            stop_reason=stop,
            wake_kind=wake.kind,
            tools_ran=tools_ran,
            ledger_mutated=ledger_mutated,
            has_pending_wait=has_pending_wait,
            pending_task_ready_count=pending_task_ready,
            has_open_work=has_open,
            pending_moment_continues=pending_continues,
            streak=int(cont.streak),
            max_streak=int(cfg.max_continue_streak),
            seconds_since_last_enqueue=seconds_since,
            cooldown_seconds=int(cfg.cooldown_seconds),
            model_beats=model_beats,
            flood_beats=flood_beats,
            last_stop_hop_was_flood=last_flood,
            require_progress=bool(cfg.require_progress),
            skip_pure_social=bool(cfg.skip_pure_social),
            max_pending_continues=int(cfg.max_pending_continues),
        )

        now = datetime.now(UTC)
        if decision.start_cooldown:
            # Successful enqueue *and* flood thrash skip tick cooldown (gate 11).
            cont.last_enqueue_at = now

        if not decision.enqueue:
            # Do not clobber status with "disabled" on every OFF moment.
            if decision.reason != "disabled":
                cont.last_skip_reason = decision.reason
                _LOG.info(
                    "moment_continue skip reason=%s moment_id=%s "
                    "wake_kind=%s tools_ran=%s ledger_mutated=%s "
                    "pending_task_ready=%d",
                    decision.reason,
                    moment_id,
                    wake.kind,
                    tools_ran,
                    ledger_mutated,
                    pending_task_ready,
                )
            return

        cont.last_skip_reason = None

        # K4/K16: enqueue moment_continue only — never enqueue_task_ready.
        payload = {
            "source_moment_id": moment_id,
            "source_wake_kind": wake.kind,
            "source_stop_reason": stop,
            "streak": int(cont.streak),
        }
        item = self._queue.enqueue("moment_continue", payload)
        cont.last_continue_wake_id = item.id
        cont.last_source_moment_id = moment_id
        _LOG.info(
            "moment_continue enqueued wake_id=%s source_moment=%s "
            "streak=%s stop=%s",
            item.id,
            moment_id,
            cont.streak,
            stop,
        )

    def _has_open_work(self) -> bool:
        """True if any goal open|review or task ready|in_progress|blocked."""
        try:
            goals = self._ensure_goals().list_goals()
        except (OSError, ValueError, TypeError) as exc:
            _LOG.warning("has_open_work list_goals failed: %s", exc)
            return False
        for g in goals:
            if not isinstance(g, dict):
                continue
            if g.get("status") in _OPEN_GOAL_STATUSES:
                return True
            tasks = g.get("tasks") or []
            if not isinstance(tasks, list):
                continue
            for t in tasks:
                if isinstance(t, dict) and t.get("status") in _OPEN_TASK_STATUSES:
                    return True
        return False

    def _fail_in_flight(
        self,
        wake: WakeItem | None,
        moment_id: str | None,
        exc: BaseException,
    ) -> None:
        """Close open moment + terminalize claimed wake after iteration failure.

        Preserves design invariant: open moment iff phase == in_moment.
        """
        with self._lock:
            self._worker_error = f"{type(exc).__name__}: {exc}"
            if moment_id is not None:
                try:
                    meta = self._moments.get_moment(moment_id)
                    if meta is not None and meta.get("ended_at") is None:
                        self._moments.close_moment(
                            moment_id,
                            "error",
                            hop_count=self._hop_count,
                        )
                except (KeyError, ValueError) as close_exc:
                    _LOG.warning(
                        "fail_in_flight close_moment failed: %s", close_exc
                    )
                # Dual path: error finalize must not orphan Chromium (IK18).
                self._close_browser_sessions_for_moment(moment_id)
            if wake is not None:
                try:
                    op = self._queue.status(wake.id)
                    if op is not None and op not in ("done", "cancelled"):
                        self._queue.mark_done(wake.id)
                except KeyError:
                    _LOG.warning(
                        "fail_in_flight mark_done unknown wake_id=%s", wake.id
                    )
            self._flush_interjects_as_wakes_unlocked()
            self._phase = self._phase_from_pending_waits_unlocked()
            self._busy = False
            self._active_moment_id = None

    @staticmethod
    def _close_browser_sessions_for_moment(moment_id: str) -> None:
        """Best-effort close of Playwright sessions bound to ``moment_id``.

        Never raises into the worker path (optional browser dep / teardown noise).
        """
        if not moment_id:
            return
        try:
            from elyra.tools.browser_sessions import get_browser_session_manager

            get_browser_session_manager().close_for_moment(moment_id)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "browser close_for_moment failed moment_id=%s: %s",
                moment_id,
                exc,
            )

    def _phase_from_pending_waits_unlocked(self) -> str:
        """``waiting`` iff durable pending wait exists; else ``idle``."""
        if self._timers.list_waits(status=STATUS_PENDING):
            return PHASE_WAITING
        return PHASE_IDLE

    def _ensure_wait_armed_unlocked(
        self, arm: WaitArm | None, moment_id: str
    ) -> None:
        """Ensure durable wait exists when stop_reason is wait.

        ``wait_user`` usually arms via ``ctx.timers`` mid-loop; this is a
        backstop when only ``DoLoopResult.arm_wait`` is set (e.g. stub loop).
        """
        if arm is None:
            return
        existing = self._timers.get_wait(arm.wait_id)
        if existing is not None:
            return
        try:
            self._timers.arm_wait(
                wait_id=arm.wait_id,
                prompt=arm.prompt,
                choices=list(arm.choices),
                user_id=arm.user_id,
                moment_id=moment_id,
                timeout=float(arm.timeout_seconds),
            )
        except (ValueError, TypeError, OSError) as exc:
            _LOG.exception("backstop arm_wait failed: %s", exc)

    def _last_tool_from_tape(self, moment_id: str) -> str | None:
        try:
            beats = self._moments.list_beats(moment_id)
        except (KeyError, ValueError, OSError):
            return None
        last: str | None = None
        for beat in beats:
            if beat.get("type") != "tool":
                continue
            name = beat.get("name") or beat.get("tool") or beat.get("tool_name")
            if isinstance(name, str) and name:
                last = name
        return last

    def _live_activity_from_tape(
        self,
        moment_id: str,
        *,
        hop_fallback: int = 0,
        tool_fallback: str | None = None,
    ) -> tuple[list[dict[str, Any]], int, str | None]:
        """Last 3 glass events + live hop/tool from the open moment tape."""
        try:
            beats = self._moments.list_beats(moment_id)
        except (KeyError, ValueError, OSError):
            return [], hop_fallback, tool_fallback
        events: list[dict[str, Any]] = []
        hop = hop_fallback
        last_tool = tool_fallback
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            if beat.get("type") == "model":
                try:
                    hop = max(hop, int(beat.get("hop") or 0))
                except (TypeError, ValueError):
                    pass
            if beat.get("type") == "tool":
                name = beat.get("name") or beat.get("tool") or beat.get("tool_name")
                if isinstance(name, str) and name:
                    last_tool = name
            event = compact_activity_event(beat)
            if event is not None:
                events.append(event)
        return events[-3:], hop, last_tool

    def _flush_interjects_as_wakes_unlocked(self) -> None:
        for item in self._interject.drain():
            self._queue.enqueue(
                "user_message",
                {
                    "content": item.content,
                    "user_id": item.user_id,
                    "message_id": item.message_id or str(uuid.uuid4()),
                    "from_interject_remainder": True,
                },
            )

    def _drain_interjections(self) -> Sequence[Any]:
        """Do-loop safe-point drain → list of content strings / dicts."""
        with self._lock:
            items = self._interject.drain()
        return [
            {"content": it.content, "user_id": it.user_id, "message_id": it.message_id}
            for it in items
        ]

    # ------------------------------------------------------------------
    # Tool context / ports
    # ------------------------------------------------------------------

    def _ensure_registry(self) -> ToolRegistry:
        if self._registry is None:
            self._registry = ToolRegistry(
                self.paths,
                bundled_root=resolve_bundled_tools_root(),
            )
        return self._registry

    def _ensure_sandbox(self) -> Sandbox:
        """Product FS jail rooted at sandboxes/sandbox0 (H2c cutover)."""
        if self._sandbox is None:
            self._sandbox = Sandbox(self.paths)
            # Ensure seed + RW dirs exist before first FS tool call.
            self._sandbox.ensure_root()
        return self._sandbox

    def _ensure_speak(self) -> SpeakTransport:
        if self._speak is None:
            self._speak = SpeakTransport(self.paths)
        return self._speak

    def _ensure_skills(self) -> SkillCatalog:
        if self._skills is None:
            self._skills = SkillCatalog(self.paths)
        return self._skills

    def _ensure_goals(self) -> GoalsStore:
        if self._goals is None:
            self._goals = GoalsStore(
                self.paths,
                on_task_ready=self._on_task_ready,
            )
        return self._goals

    def _on_task_ready(self, task_id: str, goal_id: str) -> None:
        try:
            with self._lock:
                if self._continuous.resetting:
                    _LOG.info(
                        "skip task_ready enqueue while resetting task_id=%s",
                        task_id,
                    )
                    return
                self._queue.enqueue_task_ready(task_id, goal_id=goal_id)
        except Exception:  # noqa: BLE001 — best-effort notify
            _LOG.exception(
                "task_ready enqueue failed task_id=%s goal_id=%s",
                task_id,
                goal_id,
            )

    def _build_tool_context(self, wake: WakeItem, moment_id: str) -> ToolContext:
        user_id = _user_id_from_wake(wake)  # may be None — do not force "operator"
        return ToolContext(
            paths=self.paths,
            sandbox=self._ensure_sandbox(),
            settings=self.settings,
            moment_id=moment_id,
            user_id=user_id,
            registry=self._ensure_registry(),
            speak=self._ensure_speak(),
            timers=self._timers,
            goals=self._ensure_goals(),
            skills_used=[],
            enqueue_wake=self._tool_enqueue_wake,
            cancel_wait=self._tool_cancel_wait,
            extras={
                "wake": wake,
                "wake_kind": wake.kind,  # for identity promote gates
                "identity": self._identity,
                "users": self._users,
                # install_skill / growth tools reload the held catalog
                "skills": self._ensure_skills(),
            },
        )

    def _tool_enqueue_wake(
        self,
        kind: str | None = None,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        """Port for tools: ``enqueue_wake(kind=..., payload=...)`` or positional."""
        # Support update_task style: enqueue_wake(kind="task_ready", payload={...})
        # and keyword-only variants.
        if kind is None:
            kind = kwargs.pop("kind", None)
        if not kind:
            raise TypeError("enqueue_wake requires kind")
        if payload is None:
            payload = kwargs.pop("payload", None)
        # Remaining kwargs fold into payload when payload absent.
        if payload is None and kwargs:
            payload = dict(kwargs)
            wake_id = payload.pop("wake_id", None)
        else:
            wake_id = kwargs.get("wake_id")
            if payload is not None and kwargs:
                # Merge non-wake_id extras.
                payload = dict(payload)
                for k, v in kwargs.items():
                    if k != "wake_id" and k not in payload:
                        payload[k] = v
        with self._lock:
            self._raise_if_resetting_unlocked()
            if kind == "task_ready" and payload and payload.get("task_id"):
                item = self._queue.enqueue_task_ready(
                    str(payload["task_id"]),
                    goal_id=payload.get("goal_id"),
                    payload=payload,
                )
                return item.id
            item = self._queue.enqueue(
                str(kind), payload, wake_id=wake_id
            )
            return item.id

    def _tool_cancel_wait(self, wait_id: str) -> None:
        with self._lock:
            try:
                self._timers.cancel_wait(wait_id)
            except KeyError:
                return
            if (
                self._phase == PHASE_WAITING
                and not self._busy
                and not self._timers.list_waits(status=STATUS_PENDING)
            ):
                self._phase = PHASE_IDLE

    # ------------------------------------------------------------------
    # Full reset (disk helpers + memory; resetting flag set by caller)
    # ------------------------------------------------------------------

    def _raise_if_resetting_unlocked(self) -> None:
        if self._continuous.resetting:
            raise RuntimeError("resetting")

    def _run_full_reset(self, flags: dict[str, bool]) -> dict[str, Any]:
        """Two-phase reset: path clears without worker lock; memory under lock.

        Caller has set ``resetting=True`` and released the lock. Concurrent
        writers must observe the flag via ``is_resetting`` / enqueue guards.
        """
        cleared: list[str] = []
        errors: list[dict[str, str]] = []

        def _step(name: str, fn: Callable[[], Any]) -> None:
            try:
                fn()
                if name not in cleared:
                    cleared.append(name)
            except Exception as exc:  # noqa: BLE001 — partial reset shape
                _LOG.exception("reset step %s failed: %s", name, exc)
                errors.append(
                    {"step": name, "detail": f"{type(exc).__name__}: {exc}"}
                )

        # --- Phase 1: disk path clears (lock free; flag blocks writers) ---
        _step("wakes", lambda: clear_wakes_disk(self.paths))

        def _moments() -> None:
            try:
                self._moments.recover_open_moments()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("recover_open_moments during reset: %s", exc)
            clear_moments(self.paths)

        _step("moments", _moments)
        _step("messages", lambda: clear_messages(self.paths))
        # KD13: full reset clears data/media with messages (no orphan blobs).
        _step("media", lambda: clear_media(self.paths))
        _step("goals", lambda: clear_goals(self.paths))

        if flags.get("clear_sandbox", True):
            _step("sandbox", lambda: clear_sandbox(self.paths))
        if flags.get("clear_drafts", True):
            _step("drafts", lambda: clear_tool_drafts(self.paths))
        if flags.get("clear_local_tools", False):
            _step("local_tools", lambda: clear_local_tools(self.paths))

        try:
            ensure_preserved_dirs(self.paths)
        except OSError as exc:
            _LOG.warning("ensure_preserved_dirs after reset: %s", exc)

        # --- Phase 2: memory + final path re-clear under worker lock ---
        with self._lock:
            def _memory() -> None:
                # Disk wakes already truncated; wipe maps and events fold.
                self._timers.clear_all()
                self._queue.reset_empty()

            _step("wakes_memory", _memory)

            # Final re-clear closes TOCTOU: concurrent append/create that raced
            # phase-1 clears (or bypassed API gates) cannot survive ok:true.
            _step("messages", lambda: clear_messages(self.paths))
            _step("media", lambda: clear_media(self.paths))
            _step("goals", lambda: clear_goals(self.paths))

            def _continuous_zero() -> None:
                cont = self._continuous
                cont.streak = 0
                cont.last_enqueue_at = None
                cont.last_continue_wake_id = None
                cont.last_source_moment_id = None
                cont.last_skip_reason = None

            _step("continuous_streak", _continuous_zero)

            self._interject.clear()
            self._active_moment_id = None
            self._busy = False
            self._worker_error = None
            self._hop_count = 0
            self._last_tool = None
            self._continue_injects = 0
            self._last_meal_snapshot = None
            self._phase = PHASE_IDLE

            pending = self._queue.pending()
            claimed = self._queue.claimed()
            if pending or claimed:
                detail = f"pending={len(pending)} claimed={len(claimed)}"
                errors.append({"step": "queue_assert", "detail": detail})
                _LOG.error("reset queue not empty after clear: %s", detail)

            waits = self._timers.list_waits(status=STATUS_PENDING)
            if waits:
                errors.append(
                    {
                        "step": "waits_assert",
                        "detail": f"pending_waits={len(waits)}",
                    }
                )

            if errors:
                return {
                    "ok": False,
                    "error": "partial_reset",
                    "cleared": cleared,
                    "errors": errors,
                    "phase": self._phase,
                }
            return {
                "ok": True,
                "cleared": cleared,
                "phase": self._phase,
                "continuous": continuous_status_block(
                    self._continuous,
                    self.settings.continuous,
                    pending_moment_continues=0,
                ),
            }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _pending_wait_unlocked(self) -> dict[str, Any] | None:
        waits = self._timers.list_waits(status=STATUS_PENDING)
        if not waits:
            return None
        return waits[0].to_dict()

    def _queue_depth_by_band_unlocked(self) -> dict[str, int]:
        depths: dict[str, int] = {k: 0 for k in KIND_PRIORITY}
        for item in self._queue.pending():
            depths[item.kind] = depths.get(item.kind, 0) + 1
        return depths

    def _apply_wait_reply_unlocked(
        self,
        *,
        content: str,
        user_id: str,
        choice: str | None,
        wait_id: str | None,
        message_id: str | None,
    ) -> dict[str, Any]:
        """Mark wait answered and enqueue wait_reply (phase stays waiting)."""
        text = content.strip() if isinstance(content, str) else ""
        choice_s = (
            choice.strip()
            if isinstance(choice, str)
            else (str(choice) if choice is not None else "")
        )
        if wait_id:
            try:
                self._timers.mark_wait_answered(wait_id)
            except KeyError:
                _LOG.warning("wait_reply for unknown wait_id=%s", wait_id)

        mid = message_id or str(uuid.uuid4())
        payload: dict[str, Any] = {
            "user_id": user_id,
            "content": text or choice_s,
            "message_id": mid,
        }
        if wait_id:
            payload["wait_id"] = wait_id
        if choice_s:
            payload["choice"] = choice_s
        item = self._queue.enqueue("wait_reply", payload)
        # Phase stays waiting until claim → in_moment (design).
        return {
            "ok": True,
            "routed": ROUTE_WAIT_REPLY,
            "wake_id": item.id,
            "wait_id": wait_id,
            "message_id": mid,
            "cancel_stale_wait": False,
            "answer_wait_id": wait_id,
        }
