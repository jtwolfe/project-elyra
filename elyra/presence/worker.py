"""Presence worker: claim wakes, open moments, run do-loop.

Scope: single-thread orchestration; phase machine; public enqueue/interject API.
In scope: claim → open → run_do_loop → close; wait arm → waiting; startup recover;
full reset port (reset_runtime_state under lock while idle).
Out of scope: HTTP/web, tool internals, glass UI panels.

Public API: enqueue_wake, enqueue_user_message, interject, resolve_user_input,
busy, active_moment_id, pending_wait, status_snapshot, reset_runtime_state,
set_continuous_enabled.
Must not import runtime.web.
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Any, Callable, Sequence

from elyra.config import ElyraPaths
from elyra.goals import GoalsStore
from elyra.identity import IdentityStore
from elyra.llm.client import ChatClient
from elyra.loop.context import assemble_outer_meal
from elyra.loop.continuous_policy import (
    SOCIAL_WAKE_KINDS,
    ContinuousRuntimeState,
    continuous_status_block,
    load_continuous_runtime,
    save_continuous_enabled,
    should_enqueue_moment_continue,
)
from elyra.loop.doloop import DoLoopResult, run_do_loop
from elyra.loop.orient_slice import (
    format_goals_slice,
    format_skill_bias,
    format_skill_catalog,
)
from elyra.messages import list_messages
from elyra.moment import MomentStore
from elyra.moment.types import STOP_REASONS
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
    clear_messages,
    clear_moments,
    clear_sandbox,
    clear_tool_drafts,
    clear_wakes_disk,
    ensure_preserved_dirs,
    normalize_reset_flags,
)
from elyra.sandbox import Sandbox
from elyra.settings import Settings, load_settings
from elyra.skills import SkillCatalog
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
    ) -> None:
        self.paths = paths
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

        self._identity = IdentityStore(paths)
        self._users = UsersStore(paths)

        # Continuous work runtime: defaults + JSON override; finalize enqueues
        # moment_continue when gates pass (progress-gated; never task_ready).
        self._continuous: ContinuousRuntimeState = load_continuous_runtime(
            paths.data_dir,
            defaults=self.settings.continuous,
        )

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

    def reset_runtime_state(
        self, flags: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Full reset of ephemeral runtime product (K10/K11). Worker-owned.

        Precondition: not busy and phase != in_moment. Else
        ``{"ok": False, "error": "worker_busy", "phase": ...}``.

        Under ``self._lock``: set ``resetting=True``, clear queue/timer memory +
        disk helpers, zero continuous streak fields (preserve enabled), assert
        empty pending/claimed. Concurrent ops see ``error=resetting``.

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
                return self._reset_runtime_state_unlocked(norm)
            finally:
                self._continuous.resetting = False

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
            return {
                "phase": self._phase,
                "active_moment_id": self._active_moment_id,
                "hop_count": self._hop_count,
                "last_tool": self._last_tool,
                "continue_injects": self._continue_injects,
                "queue_depth_by_band": self._queue_depth_by_band_unlocked(),
                "pending_wait": self._pending_wait_unlocked(),
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
            }

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Blocking poll loop (run on the presence thread)."""
        _LOG.info("presence worker started")
        try:
            self._startup_recover()
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

    def _claim_and_open(self) -> tuple[WakeItem, str] | None:
        """Under lock: fire due work, claim one wake, open moment, set phase.

        If claim succeeds but ``open_moment`` fails, the wake is cancelled so it
        is not left stuck in ``claimed``. Skips claim while full reset runs.
        """
        with self._lock:
            if self._continuous.resetting:
                return None
            self._fire_due_unlocked()
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
        user_id = _user_id_from_wake(wake) or "operator"

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
            glass = list_messages(limit=80, paths=self.paths)
            self_digest = self._identity.self_digest()
            try:
                user_digest = self._users.profile(user_id)
            except ValueError:
                user_digest = ""
            loop = self.settings.loop
            catalog = self._ensure_skills().catalog()
            goals_list = self._ensure_goals().list_goals()
            protect_goal_ids: set[str] = set()
            protect_task_ids: set[str] = set()
            if payload.get("goal_id"):
                protect_goal_ids.add(str(payload["goal_id"]))
            if payload.get("task_id"):
                protect_task_ids.add(str(payload["task_id"]))
            return assemble_outer_meal(
                glass_history=glass,
                settings=self.settings,
                paths=self.paths,
                self_digest=self_digest,
                user_digest=user_digest,
                why_now=why,
                goals=format_goals_slice(
                    goals_list,
                    max_tokens=loop.orient_goals_max_tokens,
                    protect_goal_ids=protect_goal_ids or None,
                    protect_task_ids=protect_task_ids or None,
                ),
                skill_catalog=format_skill_catalog(
                    catalog,
                    max_tokens=loop.orient_skill_catalog_max_tokens,
                ),
                skill_bias=format_skill_bias(wake.kind, payload),
                wake_content=wake_content_s,
                wake_message_id=wake_message_id_s,
            )

        registry = self._ensure_registry()
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
            drain_interjections=self._drain_interjections,
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
        if self._sandbox is None:
            self._sandbox = Sandbox(self.paths)
        return self._sandbox

    def _ensure_speak(self) -> SpeakTransport:
        if self._speak is None:
            self._speak = SpeakTransport(self.paths)
        return self._speak

    def _ensure_goals(self) -> GoalsStore:
        if self._goals is None:
            self._goals = GoalsStore(
                self.paths,
                on_task_ready=self._on_task_ready,
            )
        return self._goals

    def _ensure_skills(self) -> SkillCatalog:
        if self._skills is None:
            self._skills = SkillCatalog(self.paths)
        return self._skills

    def _on_task_ready(self, task_id: str, goal_id: str) -> None:
        try:
            with self._lock:
                self._queue.enqueue_task_ready(task_id, goal_id=goal_id)
        except Exception:  # noqa: BLE001 — best-effort notify
            _LOG.exception(
                "task_ready enqueue failed task_id=%s goal_id=%s",
                task_id,
                goal_id,
            )

    def _build_tool_context(self, wake: WakeItem, moment_id: str) -> ToolContext:
        user_id = _user_id_from_wake(wake)
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
            # Same SkillCatalog instance as rebuild_outer so install_skill
            # can reload() and the next outer meal sees new skills.
            extras={"wake": wake, "skills": self._ensure_skills()},
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
    # Full reset (disk helpers + memory; caller sets resetting flag)
    # ------------------------------------------------------------------

    def _raise_if_resetting_unlocked(self) -> None:
        if self._continuous.resetting:
            raise RuntimeError("resetting")

    def _reset_runtime_state_unlocked(
        self, flags: dict[str, bool]
    ) -> dict[str, Any]:
        """Execute clear steps; caller holds lock and ``resetting=True``."""
        cleared: list[str] = []
        errors: list[dict[str, str]] = []

        def _step(name: str, fn: Callable[[], Any]) -> None:
            try:
                fn()
                cleared.append(name)
            except Exception as exc:  # noqa: BLE001 — partial reset shape
                _LOG.exception("reset step %s failed: %s", name, exc)
                errors.append({"step": name, "detail": f"{type(exc).__name__}: {exc}"})

        # a/b: wakes disk + queue/timer memory (order: disk then memory reload)
        def _wakes() -> None:
            clear_wakes_disk(self.paths)
            self._timers.clear_all()
            self._queue.reset_empty()

        _step("wakes", _wakes)

        # c: open moments → interrupted then wipe tapes/index
        def _moments() -> None:
            try:
                self._moments.recover_open_moments()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("recover_open_moments during reset: %s", exc)
            clear_moments(self.paths)

        _step("moments", _moments)

        _step("messages", lambda: clear_messages(self.paths))
        _step("goals", lambda: clear_goals(self.paths))

        if flags.get("clear_sandbox", True):
            _step("sandbox", lambda: clear_sandbox(self.paths))
        if flags.get("clear_drafts", True):
            _step("drafts", lambda: clear_tool_drafts(self.paths))
        if flags.get("clear_local_tools", False):
            _step("local_tools", lambda: clear_local_tools(self.paths))

        # h: continuous streak zero; preserve enabled (and continuous.json)
        def _continuous_zero() -> None:
            cont = self._continuous
            cont.streak = 0
            cont.last_enqueue_at = None
            cont.last_continue_wake_id = None
            cont.last_source_moment_id = None
            cont.last_skip_reason = None

        _step("continuous_streak", _continuous_zero)

        # Ensure dirs still present (sandbox empty, skills/local untouched).
        try:
            ensure_preserved_dirs(self.paths)
        except OSError as exc:
            _LOG.warning("ensure_preserved_dirs after reset: %s", exc)

        # Worker soft state
        self._interject.clear()
        self._active_moment_id = None
        self._busy = False
        self._worker_error = None
        self._hop_count = 0
        self._last_tool = None
        self._continue_injects = 0
        self._phase = PHASE_IDLE

        # i: assert queue empty
        pending = self._queue.pending()
        claimed = self._queue.claimed()
        if pending or claimed:
            detail = f"pending={len(pending)} claimed={len(claimed)}"
            errors.append({"step": "queue_assert", "detail": detail})
            _LOG.error("reset queue not empty after clear: %s", detail)

        # j: no pending waits
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
