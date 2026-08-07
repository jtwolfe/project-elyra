"""Presence worker: claim wakes, open moments, run do-loop.

Scope: single-thread orchestration; phase machine; public enqueue/interject API.
In scope: claim → open → run_do_loop → close; wait arm → waiting; startup recover;
full reset port (reset_runtime_state under lock while idle).
Out of scope: HTTP/web, tool internals, glass UI panels.

Public API: enqueue_wake, enqueue_user_message, interject, resolve_user_input,
busy, active_moment_id, pending_wait, status_snapshot, continuous_status, timers,
reset_runtime_state, set_continuous_enabled, set_dev_speed, set_semantic_wait,
set_meal_budget.
Must not import runtime.web.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import replace
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
from elyra.runtime.meal_budget import (
    MealBudgetState,
    clamp_fraction,
    effective_meal_budget_tokens,
    load_meal_budget_runtime,
    meal_budget_status_block,
    save_meal_budget_runtime,
)
from elyra.runtime.semantic_wait import (
    SEMANTIC_WAIT_APPLIES_TO,
    SemanticWaitState,
    load_semantic_wait_runtime,
    save_semantic_wait_runtime,
    semantic_wait_status_block,
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
    clear_client_sessions,
    clear_conversations,
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


# Hard cap for wait_reply why_now user content dual-write (OQ7 / BUG-meal-03 S2).
_WHY_NOW_SNIPPET_MAX_CHARS = 160


def _snippet(text: Any, *, max_chars: int = _WHY_NOW_SNIPPET_MAX_CHARS) -> str:
    """Collapse whitespace and hard-cap a user content snippet for why_now.

    Empty / non-string / whitespace-only → empty string. Cap is inclusive of
    the trailing ellipsis when truncated (same shape as orient_slice._truncate).
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = " ".join(text.split()).strip()
    if not text:
        return ""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    if max_chars <= 1:
        return text[:max_chars]
    return text[: max_chars - 1].rstrip() + "…"


def _why_now(wake: WakeItem) -> str:
    kind = wake.kind
    payload = wake.payload or {}
    if kind == "user_message":
        uid = payload.get("user_id") or "user"
        return f"user message from {uid}"
    if kind == "wait_reply":
        # Dual-write: wait_id + capped user content snippet (complements BIAS_TALK;
        # does not replace skill bias). Full dialogue remains glass-tail SoT.
        wid = payload.get("wait_id") or "?"
        snippet = _snippet(payload.get("content"), max_chars=_WHY_NOW_SNIPPET_MAX_CHARS)
        if snippet:
            return f"wait reply (wait_id={wid}): {snippet}"
        return f"wait reply (wait_id={wid})"
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


def _conversation_id_from_wake(wake: WakeItem) -> str | None:
    """Soft conversation_id from wake.payload only (never invent from session)."""
    cid = (wake.payload or {}).get("conversation_id")
    if not isinstance(cid, str):
        return None
    stripped = cid.strip()
    return stripped or None


def _conversation_id_from_wait_obj(wait_obj: Any) -> str | None:
    """Read soft conversation_id from a PendingWait / wait-like object."""
    if wait_obj is None:
        return None
    raw = getattr(wait_obj, "conversation_id", None)
    if raw is None and hasattr(wait_obj, "to_dict"):
        try:
            raw = (wait_obj.to_dict() or {}).get("conversation_id")
        except Exception:  # noqa: BLE001 — soft
            raw = None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _stamp_social_payload(
    payload: dict[str, Any],
    *,
    conversation_id: str | None = None,
    social_kind: str | None = None,
) -> dict[str, Any]:
    """Mutate/return payload with conversation_id + social_kind when social."""
    cid = conversation_id
    if isinstance(cid, str):
        cid = cid.strip() or None
    kind = social_kind if social_kind in ("group", "dm") else None
    if kind is None and cid is not None:
        from elyra.conversations import social_kind_for

        kind = social_kind_for(cid)
    if cid is not None:
        payload["conversation_id"] = cid
    if kind is not None:
        payload["social_kind"] = kind
    return payload


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
        # Meal budget fraction of model window (default 0.5 → 250k @ 500k).
        # Missing runtime JSON → product default; does not mutate Settings.
        self._meal_budget: MealBudgetState = load_meal_budget_runtime(
            paths.data_dir
        )
        # Semantic wait-for-select (default ON): keep slow encodes for meal pack.
        # Missing runtime JSON seeds from settings.memory (elyra.toml).
        self._semantic_wait: SemanticWaitState = load_semantic_wait_runtime(
            paths.data_dir,
            defaults=self.settings.memory,
        )

        # Stretch 2 memory store (lazy). Defaults write_atoms=true / enabled=true
        # → never opened; meal still legacy until PR6.
        self._memory: Any | None = None
        self._memory_open_attempted = False
        self._memory_open_failed = False
        # Durable EdgeStore (sibling to atoms; lazy until P1 eager warm).
        # Open state machine (KD-ES-LOCK / KD-ES-RETRY):
        #   absent | opening | ready | unavailable
        # Sticky soft-fail path (real bug): fail_soft returns UnavailableEdgeStore
        # which is not None → old ensure never retried. We null that handle on
        # transient retry and only permanent-fail ImportError / integrity.
        self._edge_store: Any | None = None
        self._edge_store_state: str = "absent"
        self._edge_store_open_lock = threading.Lock()
        self._edge_store_open_attempts: int = 0
        self._edge_store_open_failed = False  # permanent fail (no more retries)
        self._edge_store_error: str | None = None
        self._edge_store_next_retry_at: float = 0.0  # time.monotonic()
        # Legacy alias used by older tests / status; mirrors open_attempts > 0.
        self._edge_store_open_attempted = False
        # Dev force edge backfill last result (process-RAM; glass progress line).
        self._edge_backfill_last: dict[str, Any] | None = None
        # Phase 2 encode queue + embedder + EmbeddingIndex (lazy; store open).
        self._encode_queue: Any | None = None
        self._embedder: Any | None = None
        self._embedder_open_failed = False
        self._embedder_open_lock = threading.Lock()
        # absent | loading | warm | failed — consumers non-blocking while loading
        self._embedder_state: str = "absent"
        # EmbedderGate: create-once at init (no lazy TOCTOU across threads).
        from elyra.memory.embed.gate import EmbedderGate

        self._embedder_gate: Any = EmbedderGate()
        self._embedding_index: Any | None = None
        self._embed_catchup_marked: int = 0  # process-life OQ4 none→pending count
        # Warm-on-start (KD-WARM-UX / KD-EP): core fabric blocks claim; embedder
        # loads on a sole side-thread; encode worker starts only on terminal.
        self._memory_warming: bool = False
        self._embedder_loader_thread: threading.Thread | None = None
        self._embedder_loader_terminal: str | None = None  # warm|failed|absent
        self._embedder_loader_applied: bool = False
        # Continuous encode worker ownership (KD-E1 / KD-E7).
        # encode_owner ∈ {none, idle, worker}
        self._encode_owner: str = "none"
        self._encode_worker: Any | None = None  # EncodeWorker
        self._encode_wake = threading.Event()
        # Generation bumped on stop/shutdown so late zombie ticks no-op.
        self._encode_epoch: int = 0
        self._encode_shutting_down: bool = False
        self._encode_worker_restarts: int = 0
        self._encode_worker_restart_times: list[float] = []
        self._encode_worker_next_restart_at: float = 0.0
        self._encode_worker_backoff_s: float = 0.5
        self._encode_worker_restart_throttled: bool = False
        self._gap_drain_active: bool = False
        self._encode_drain_ok_total: int = 0
        self._encode_drain_failed_total: int = 0
        self._encode_last_drain_at: float | None = None
        self._encode_last_drain_stats: dict[str, Any] | None = None
        # Deferred speak-recalls queue (polish1 KD-P0-defer / PR1b).
        # Process-local; drained on idle tick under semantic wait ceiling.
        from elyra.memory.config import EDGE_RECALLS_DEFERRED_QUEUE_DEPTH_DEFAULT

        self._deferred_recalls_jobs: deque[tuple[str, str]] = deque()
        self._deferred_recalls_depth = int(
            EDGE_RECALLS_DEFERRED_QUEUE_DEPTH_DEFAULT
        )
        self._recalls_deferred_queued: int = 0
        self._recalls_deferred_ok: int = 0
        self._recalls_deferred_dropped: int = 0
        self._recalls_skipped: dict[str, int] = {}
        # Last labeled meal package inspect payload (glass Memory Context tab).
        self._last_meal_snapshot: dict[str, Any] | None = None
        # Raw meal atom ids for created_with (edges design PR3 / KD-E17).
        # Filled at rebuild_outer from package items — never glass inspect DTO.
        self._last_meal_atom_ids: list[str] = []
        # Phase 2a directed traversal (PR-A2): process-local session registry.
        # Flags default off — registry is inert until directed_traversal_enabled.
        from elyra.memory.traverse import TraversalRegistry

        self._traversal: TraversalRegistry = TraversalRegistry(
            settings=self.settings.memory,
            paths=paths,
        )

        # Moment viewing set (KD-V4/V12/V13): process-local; clear on finalize.
        # Insertion-ordered dict att_id → ViewingEntry; dirty forces re-outer.
        from elyra.media.viewing import ViewingEntry

        self._moment_viewing: dict[str, ViewingEntry] = {}
        self._viewing_dirty: bool = False

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

    def set_semantic_wait(
        self,
        *,
        enabled: bool | None = None,
        max_ms: int | None = None,
    ) -> dict[str, Any]:
        """Toggle / set meal semantic wait ceiling; persist runtime JSON.

        When wait is on, ``compose_meal`` → ``select_semantic`` uses a long
        ceiling and keeps finished slow encodes (CPU dogfood). Does not invent
        wakes. When both args are None, returns current state.
        """
        with self._lock:
            snappy = int(self.settings.memory.semantic_select_max_ms)
            if self._continuous.resetting:
                return {
                    "ok": False,
                    "error": "resetting",
                    "semantic_wait": semantic_wait_status_block(
                        self._semantic_wait,
                        snappy_max_ms=snappy,
                        applies_to=SEMANTIC_WAIT_APPLIES_TO,
                    ),
                }
            prev_en = bool(self._semantic_wait.enabled)
            prev_max = int(self._semantic_wait.max_ms)
            if enabled is not None:
                self._semantic_wait.enabled = bool(enabled)
            if max_ms is not None:
                from elyra.memory.config import clamp_semantic_wait_max_ms

                self._semantic_wait.max_ms = clamp_semantic_wait_max_ms(max_ms)
            try:
                save_semantic_wait_runtime(
                    self.paths.data_dir,
                    enabled=bool(self._semantic_wait.enabled),
                    max_ms=int(self._semantic_wait.max_ms),
                    defaults=self.settings.memory,
                )
            except OSError as exc:
                _LOG.warning("persist semantic_wait.json failed: %s", exc)
            block = semantic_wait_status_block(
                self._semantic_wait,
                snappy_max_ms=snappy,
                applies_to=SEMANTIC_WAIT_APPLIES_TO,
            )
            return {
                "ok": True,
                "changed": (
                    prev_en != bool(self._semantic_wait.enabled)
                    or prev_max != int(self._semantic_wait.max_ms)
                ),
                "semantic_wait": block,
            }

    def set_meal_budget(
        self,
        *,
        fraction: float | None = None,
    ) -> dict[str, Any]:
        """Set meal budget fraction of model window; persist runtime JSON.

        Product paths apply the derived token budget to both sliding and
        in-turn caps (policy A). Does not invent wakes or mutate Settings.
        When fraction is None, returns current state.

        Fail-clean durable path: clamp first, ``save_meal_budget_runtime``
        **before** mutating live state; on ``OSError`` leave memory unchanged
        and return ``ok: False`` / ``error: persist_failed``.
        """
        with self._lock:
            window = int(self.settings.loop.model_context_window_tokens)
            prev_block = meal_budget_status_block(
                self._meal_budget, model_window=window
            )
            if self._continuous.resetting:
                return {
                    "ok": False,
                    "error": "resetting",
                    "meal_budget": prev_block,
                }
            prev = float(self._meal_budget.fraction)
            if fraction is None:
                return {
                    "ok": True,
                    "changed": False,
                    "meal_budget": prev_block,
                }
            try:
                ceiling = float(self._meal_budget.max_fraction)
                new_frac = clamp_fraction(fraction, max_fraction=ceiling)
            except (TypeError, ValueError) as exc:
                return {
                    "ok": False,
                    "error": "invalid_fraction",
                    "detail": str(exc),
                    "meal_budget": prev_block,
                }
            if abs(new_frac - prev) <= 1e-12:
                return {
                    "ok": True,
                    "changed": False,
                    "meal_budget": prev_block,
                }
            try:
                save_meal_budget_runtime(
                    self.paths.data_dir,
                    fraction=new_frac,
                    max_fraction=ceiling,
                )
            except OSError as exc:
                _LOG.warning("persist meal_budget.json failed: %s", exc)
                return {
                    "ok": False,
                    "error": "persist_failed",
                    "detail": str(exc),
                    "meal_budget": prev_block,
                }
            self._meal_budget.fraction = new_frac
            block = meal_budget_status_block(
                self._meal_budget, model_window=window
            )
            return {
                "ok": True,
                "changed": True,
                "meal_budget": block,
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
        conversation_id: str | None = None,
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
        ``conversation_id`` is stamped on the row when provided (PR3b / KD16).
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
                conversation_id=conversation_id,
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
    def queue(self) -> WakeQueue:
        """Shared wake queue (same instance supervisor injects into reaper)."""
        return self._queue

    @property
    def timers(self) -> TimerService:
        """Durable timer/wait store (Glass schedule inspect, tools)."""
        return self._timers

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
        conversation_id: str | None = None,
        social_kind: str | None = None,
    ) -> str:
        """Enqueue a ``user_message`` wake; returns wake id.

        When ``conversation_id`` / ``social_kind`` provided, stamps both on
        the wake payload (social). Pure-work callers omit both.
        """
        mid = message_id or str(uuid.uuid4())
        payload: dict[str, Any] = {
            "content": content,
            "user_id": user_id,
            "message_id": mid,
        }
        _stamp_social_payload(
            payload, conversation_id=conversation_id, social_kind=social_kind
        )
        with self._lock:
            self._raise_if_resetting_unlocked()
            item = self._queue.enqueue("user_message", payload)
            return item.id

    def interject(
        self,
        content: str,
        *,
        user_id: str = "operator",
        message_id: str | None = None,
        conversation_id: str | None = None,
        social_kind: str | None = None,
    ) -> dict[str, Any]:
        """Buffer an interjection when ``phase == in_moment``.

        On buffer full: still an interject decision — return
        ``routed=interject``, ``ok=false``, ``reason=interjection_buffer_full``,
        and enqueue a durable ``user_message`` wake (do not drop). Clients should
        key glass notices off ``ok`` + ``reason`` (and optional ``wake_id``);
        ``routed`` stays ``interject`` so UI that branches on route does not
        treat overflow as a fresh idle chat.

        Overflow and remainder wakes retain ``conversation_id`` + ``social_kind``
        (§3.6). When not in a moment: enqueue as ``user_message`` instead.
        """
        text = content if isinstance(content, str) else str(content)
        cid = (
            conversation_id.strip()
            if isinstance(conversation_id, str) and conversation_id.strip()
            else None
        )
        kind = social_kind if social_kind in ("group", "dm") else None
        if kind is None and cid is not None:
            from elyra.conversations import social_kind_for

            kind = social_kind_for(cid)
        with self._lock:
            if self._continuous.resetting:
                return {
                    "ok": False,
                    "error": "resetting",
                    "reason": "resetting",
                }
            if self._phase != PHASE_IN_MOMENT:
                payload: dict[str, Any] = {
                    "content": text,
                    "user_id": user_id,
                    "message_id": message_id or str(uuid.uuid4()),
                }
                _stamp_social_payload(
                    payload, conversation_id=cid, social_kind=kind
                )
                wake_id = self._queue.enqueue("user_message", payload).id
                return {
                    "ok": True,
                    "routed": ROUTE_USER_MESSAGE,
                    "wake_id": wake_id,
                    "reason": "not_in_moment",
                }
            item = InterjectItem(
                content=text,
                user_id=user_id,
                message_id=message_id,
                conversation_id=cid,
                social_kind=kind,
            )
            ok, reason = self._interject.try_add(item)
            if ok:
                return {"ok": True, "routed": ROUTE_INTERJECT}
            # Overflow → durable wake (do not drop); keep routed=interject.
            # Retain conversation_id + social_kind from the social enqueue.
            overflow_payload: dict[str, Any] = {
                "content": text,
                "user_id": user_id,
                "message_id": message_id or str(uuid.uuid4()),
                "from_interject_overflow": True,
            }
            _stamp_social_payload(
                overflow_payload, conversation_id=cid, social_kind=kind
            )
            wake_id = self._queue.enqueue("user_message", overflow_payload).id
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
        conversation_id: str | None = None,
        social_kind: str | None = None,
    ) -> dict[str, Any]:
        """Route user input via the phase/wait state machine; apply side effects.

        ``conversation_id`` / ``social_kind`` thread through all social routes
        (interject, wait_reply, user_message) — §3.6 propagation matrix.
        """
        cid = (
            conversation_id.strip()
            if isinstance(conversation_id, str) and conversation_id.strip()
            else None
        )
        kind = social_kind if social_kind in ("group", "dm") else None
        if kind is None and cid is not None:
            from elyra.conversations import social_kind_for

            kind = social_kind_for(cid)
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
                    content,
                    user_id=user_id,
                    message_id=message_id,
                    conversation_id=cid,
                    social_kind=kind,
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
                    conversation_id=cid,
                    social_kind=kind,
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
            payload: dict[str, Any] = {
                "content": text,
                "user_id": user_id,
                "message_id": mid,
            }
            _stamp_social_payload(
                payload, conversation_id=cid, social_kind=kind
            )
            item = self._queue.enqueue("user_message", payload)
            out = dict(decision)
            out["wake_id"] = item.id
            out["message_id"] = mid
            return out

    def continuous_status(self) -> dict[str, Any]:
        """Lightweight continuous block for Glass (same keys as status continuous).

        Does **not** build meal/context/memory/viewing. Safe for ``/api/schedule``.
        """
        with self._lock:
            pending_continues = len(self._queue.pending_of_kind("moment_continue"))
            return continuous_status_block(
                self._continuous,
                self.settings.continuous,
                pending_moment_continues=pending_continues,
            )

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
            model_window = int(loop.model_context_window_tokens)
            # Policy A: effective fraction×window applies to product meal size
            # (not min(frozen sliding, in_turn) which left budgets stuck at 50k).
            meal_budget = effective_meal_budget_tokens(
                self.settings, self._meal_budget
            )
            meal_budget_block = meal_budget_status_block(
                self._meal_budget, model_window=model_window
            )
            try:
                from elyra.loop import context_meter

                context_block = context_meter.status_block(
                    meal_budget_tokens=meal_budget,
                    model_window_tokens=model_window,
                )
            except Exception:
                context_block = {
                    "meal_used_tokens": 0,
                    "meal_budget_tokens": meal_budget,
                    "model_window_tokens": model_window,
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
                "semantic_wait": semantic_wait_status_block(
                    self._semantic_wait,
                    snappy_max_ms=int(
                        self.settings.memory.semantic_select_max_ms
                    ),
                    applies_to=SEMANTIC_WAIT_APPLIES_TO,
                ),
                "meal_budget": meal_budget_block,
                "context": context_block,
                "memory": self._memory_status_block(),
                # Moment viewing set (KD-V4 / PR6 observability).
                # att_ids only — no paths/URLs/filenames.
                "viewing": self._viewing_status_block_unlocked(),
            }

    # ------------------------------------------------------------------
    # Worker thread
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Blocking poll loop (run on the presence thread).

        Warm-on-start (KD-WARM-UX R2): after recover, eagerly open store + index
        + EdgeStore on this thread (may block first claim). Embedder cold load
        runs on a **sole side-thread** when ``embed_preload``; presence sets
        ``_started`` and enters the claim loop **without** joining Nemotron.
        Encode worker starts only after loader terminal (warm|failed), or
        immediately when preload is off (encode tick remains sole loader).
        """
        _LOG.info("presence worker started")
        try:
            self._startup_recover()
            self._memory_warming = True
            self._warm_memory_core()
            # Sole start loader: NOT on presence thread; do NOT join here.
            if self._should_preload_embedder():
                self._start_embedder_loader_thread()
            else:
                # Preload off / embed disabled: encode worker may cold-load on
                # first tick (existing path). No start-path loader to await.
                self._start_encode_worker_if_needed()
                self._memory_warming = False
            self._started = True
            while not self._stop.is_set():
                wake: WakeItem | None = None
                moment_id: str | None = None
                try:
                    # Apply loader terminal once (start encode worker, clear warming).
                    self._maybe_apply_embedder_loader_terminal()
                    # Busy-safe death recovery: monitor every loop iteration
                    # (not idle-only) — KD-E16.
                    self._maybe_restart_encode_worker()
                    claimed = self._claim_and_open()
                    if claimed is None:
                        # Still fire due timers/waits while idle.
                        with self._lock:
                            self._fire_due_unlocked()
                        # Ladder refresh OUTSIDE lock (PR5 normative placement).
                        self._idle_memory_ladder()
                        # Corpus encode: idle only when owner=idle (rollback).
                        # When owner=worker, no-op; gap drain covers dead-worker.
                        self._idle_memory_encode()
                        self._gap_drain_if_needed()
                        # KD-R11: joint-copy repair continue (open/idle only).
                        self._idle_memory_joint_repair()
                        # ANN optimize OUTSIDE lock — never mid-hop (KD4).
                        self._idle_memory_optimize()
                        # Phase 2a: abandon idle active TraversalSession (TTL).
                        self._idle_traversal_ttl()
                        # PR1b: drain deferred speak-recalls (ANN under wait).
                        self._idle_deferred_recalls()
                        self._stop.wait(timeout=self._poll)
                        continue
                    wake, moment_id = claimed
                    result, skills_used = self._run_moment(wake, moment_id)
                    self._finalize_moment(
                        wake, moment_id, result, skills_used=skills_used
                    )
                    # Gap drain after finalize (busy path) when worker is dead.
                    self._gap_drain_if_needed()
                except Exception as exc:  # noqa: BLE001 — keep worker alive
                    _LOG.exception("presence worker iteration failed: %s", exc)
                    self._fail_in_flight(wake, moment_id, exc)
                    self._stop.wait(timeout=self._poll)
        finally:
            # Join start loader before encode/embedder teardown (KD-WARM-UX).
            try:
                self._join_embedder_loader_thread(timeout_s=30.0)
            except Exception:  # noqa: BLE001
                _LOG.exception("join embedder loader thread failed")
            # Encode teardown before browser close (join worker → close embedder).
            self._shutdown_encode()
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
            # Embedder preload is owned by run() sole start loader thread
            # (KD-WARM-UX / KD-EP) — never sync open_encoder on store open.
            return self._memory
        except Exception:  # noqa: BLE001 — store down must not kill presence
            self._memory_open_failed = True
            self._memory = None
            _LOG.exception("memory store open failed; atoms disabled this run")
            return None

    def _warm_memory_core(self) -> None:
        """Eager open atom store + embedding index + EdgeStore (KD-WARM-UX).

        Runs on the presence thread after recover and **before** ``_started``.
        May block first claim until store + edges are decided. Does **not**
        load the embedder (side-thread sole loader owns that).

        Optional (W5 / KD-TRAY): best-effort ``ensure_tray`` once so the sticky
        directed-keep tray is loaded before first meal. Soft-fail only — tray
        is never a ``memory_ready`` gate.
        """
        _LOG.info("memory.fabric_warming start")
        try:
            self._ensure_memory_store()
            # Index opens with store; re-ensure is cheap if already set.
            if self._memory is not None:
                self._ensure_embedding_index()
            # EdgeStore single-flight SM (P2); eager open for durable fabric.
            self._ensure_edge_store()
            # W5 / KD-TRAY: optional tray warm — soft fail, non-gating.
            self._warm_directed_keep_tray()
            _LOG.info(
                "memory.fabric_core_ready store_open=%s index_open=%s "
                "edges_state=%s tray_loaded=%s",
                self._memory is not None,
                self._embedding_index is not None,
                getattr(self, "_edge_store_state", "absent"),
                getattr(self._traversal, "directed_keep_tray", None) is not None,
            )
        except Exception:  # noqa: BLE001 — never kill presence on warm
            _LOG.exception("memory.fabric_core warm failed")

    def _warm_directed_keep_tray(self) -> None:
        """Best-effort load of sticky directed-keep tray (W5 / KD-TRAY).

        Soft-fail only: missing file, IO error, or registry issues must not
        block warm core or affect ``memory_ready``. Lazy first-meal path still
        calls ``ensure_tray`` when this warm is skipped or fails.
        """
        try:
            trav = getattr(self, "_traversal", None)
            if trav is None:
                return
            trav.ensure_tray()
        except Exception:  # noqa: BLE001 — tray never gates fabric
            _LOG.warning(
                "directed_keep_tray warm failed (soft); lazy load on first use",
                exc_info=True,
            )

    def _should_preload_embedder(self) -> bool:
        """True when start-path sole loader should cold-open the embedder."""
        mem_cfg = self.settings.memory
        if not (mem_cfg.write_atoms or mem_cfg.enabled):
            return False
        if not bool(getattr(mem_cfg, "embed_enabled", False)):
            return False
        return bool(getattr(mem_cfg, "embed_preload", False))

    def _start_embedder_loader_thread(self) -> None:
        """Kick sole start-path embedder loader. Do **not** join before claim.

        Uses existing ``_ensure_embedder(role=loader)`` open lock / state machine.
        Encode worker is **not** started until terminal warm|failed is applied
        on the presence thread (``_maybe_apply_embedder_loader_terminal``).
        """
        if self._embedder_loader_thread is not None:
            return

        def _target() -> None:
            terminal = "failed"
            try:
                emb = self._ensure_embedder(role="loader")
                if emb is not None and self._embedder_state == "warm":
                    terminal = "warm"
                elif self._embedder_state == "failed":
                    terminal = "failed"
                else:
                    # Aborted mid-load (shutdown) or no handle — treat as failed
                    # for encode-worker start gating (soft-skip drain).
                    terminal = "failed"
            except Exception:  # noqa: BLE001
                terminal = "failed"
                _LOG.exception("memory.embed.loader_thread failed")
            self._embedder_loader_terminal = terminal
            _LOG.info(
                "memory.embed.loader_thread_terminal state=%s", terminal
            )

        t = threading.Thread(
            target=_target,
            name="elyra-embedder-loader",
            daemon=True,
        )
        self._embedder_loader_thread = t
        _LOG.info("memory.embed.loader_thread_start")
        t.start()

    def _maybe_apply_embedder_loader_terminal(self) -> None:
        """Presence-thread join point: start encode worker once after loader.

        Idempotent. Called each claim-loop iteration. When the start-path
        loader is not used (preload off), this is a no-op.
        """
        if self._embedder_loader_applied:
            return
        # Only applies when start-path loader was kicked.
        if self._embedder_loader_thread is None:
            return
        term = self._embedder_loader_terminal
        if term not in ("warm", "failed", "absent"):
            return
        self._embedder_loader_applied = True
        if term in ("warm", "failed"):
            self._start_encode_worker_if_needed()
        # Clear warming when embedder is no longer mid-load.
        if self._embedder_state != "loading":
            self._memory_warming = False
        _LOG.info(
            "memory.fabric_ready embedder_terminal=%s embedder_state=%s",
            term,
            self._embedder_state,
        )

    def _join_embedder_loader_thread(self, *, timeout_s: float = 30.0) -> None:
        """Best-effort join of start-path loader on shutdown."""
        t = getattr(self, "_embedder_loader_thread", None)
        if t is None:
            return
        if not t.is_alive():
            return
        t.join(timeout=float(timeout_s))
        if t.is_alive():
            _LOG.warning(
                "memory.embed.loader_thread join timed out after %.1fs",
                float(timeout_s),
            )

    def _encode_worker_start_allowed(self) -> bool:
        """When start-path loader owns cold open, defer encode worker until terminal.

        Tests / non-run paths that call ``_start_encode_worker_if_needed``
        without kicking the loader thread are unrestricted (loader thread is
        None → allowed). Soft-safe when warm-path fields are missing.
        """
        t = getattr(self, "_embedder_loader_thread", None)
        if t is None:
            return True
        if bool(getattr(self, "_embedder_loader_applied", False)):
            return True
        term = getattr(self, "_embedder_loader_terminal", None)
        return term in ("warm", "failed", "absent")

    def _install_encode_hooks(self, store: Any, mem_cfg: Any) -> None:
        """Install store write hook + EncodeQueue (KD16). Best-effort."""
        try:
            from elyra.memory.embed.queue import EncodePriority, EncodeQueue

            maxsize = int(getattr(mem_cfg, "encode_queue_max", 1024) or 1024)
            queue = EncodeQueue(maxsize=maxsize)
            self._encode_queue = queue
            wake = self._encode_wake

            def _on_written(atom: Any) -> None:
                # Hook must never raise to put_atom (store already guards).
                try:
                    cfg = self.settings.memory
                    if not cfg.semantic_enabled:
                        return
                    # Only enqueue when drain can run; otherwise backlog
                    # overflows mark atoms skipped (semantic+embed-off must
                    # leave pending until embed is enabled — KD intent).
                    # Pending scan fills the queue once embed turns on.
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
                    enqueued = queue.enqueue(
                        atom.atom_id,
                        store=store,
                        priority=EncodePriority.ATOM_CREATE,
                    )
                    if enqueued:
                        wake.set()
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

    def _get_embedder_gate(self) -> Any:
        """Process EmbedderGate (create-once in ``__init__``)."""
        return self._embedder_gate

    def _gated_embedder_handle(self, emb: Any) -> Any:
        """Wrap raw warm embedder as GatedEmbedder for meal/graph/API."""
        from elyra.memory.embed.gate import GatedEmbedder

        return GatedEmbedder(emb, self._embedder_gate)

    def _ensure_embedder(self, *, role: str = "consumer") -> Any | None:
        """Process-shared embedder access (KD-E13 / KD-E18 / KD-E5).

        consumer (default): non-blocking — return warm **GatedEmbedder** or None.
          While loading / absent / failed → None (callers omit encoder).
          Never waits on cold load; never calls open_encoder.
          Gated handle is the only public encode path for meal/graph/API.
        loader: may perform open_encoder **outside** the open lock; only
          encode-worker tick / embed_preload use role=\"loader\".
          Returns the **raw** embedder (bulk uses gate around encode_atom).
        Publish after open only if still ``state==loading`` and not shutting
        down — else close the orphan handle (no resurrection after close).
        """
        mem_cfg = self.settings.memory
        if not mem_cfg.embed_enabled:
            return None

        with self._embedder_open_lock:
            if self._embedder is not None and self._embedder_state == "warm":
                if role == "loader":
                    return self._embedder
                return self._gated_embedder_handle(self._embedder)
            if self._embedder_open_failed or self._embedder_state == "failed":
                return None
            if role != "loader":
                # consumer: never wait, never open
                return None
            if self._encode_shutting_down:
                return None
            if self._embedder_state == "loading":
                # Another loader in flight — do not double-open.
                return None
            self._embedder_state = "loading"

        # Cold load outside open lock and outside encode gate (~18s possible).
        try:
            from elyra.memory.embed.runtime import open_encoder

            emb = open_encoder(mem_cfg)
            orphan = None
            with self._embedder_open_lock:
                # Loader publish guard: only assign if still loading and not
                # shut down / closed mid-load (prevents warm resurrection).
                if (
                    self._encode_shutting_down
                    or self._embedder_state != "loading"
                ):
                    orphan = emb
                    if self._embedder_state == "loading":
                        self._embedder_state = "absent"
                else:
                    self._embedder = emb
                    self._embedder_state = "warm"
                    self._embedder_open_failed = False
                    _LOG.info(
                        "memory.embed.embedder_warm role=loader backend=%s",
                        getattr(mem_cfg, "embed_backend", "?"),
                    )
                    # Loader returns raw for bulk encode_atom path.
                    return self._embedder
            # Close orphan outside lock.
            if orphan is not None:
                try:
                    close = getattr(orphan, "close", None)
                    if callable(close):
                        close()
                except Exception:  # noqa: BLE001
                    _LOG.debug(
                        "orphan embedder close after aborted load failed",
                        exc_info=True,
                    )
                _LOG.info(
                    "memory.embed.embedder_load_aborted "
                    "(shutdown or state changed mid-load)"
                )
            return None
        except Exception:  # noqa: BLE001
            with self._embedder_open_lock:
                # Only sticky-fail if we still own the loading slot.
                if self._embedder_state == "loading":
                    self._embedder = None
                    self._embedder_state = "failed"
                    self._embedder_open_failed = True
            _LOG.exception(
                "embedder open failed; encode drain soft-disabled this run"
            )
            return None

    def _close_embedder(self) -> None:
        """Close embedder once under open lock. Best-effort; never raises.

        Forces state away from ``loading``/``warm`` so a concurrent loader
        publish is rejected (orphan closed by loader guard).

        Safe when attributes are missing (partial ``object.__new__`` fixtures
        and early teardown paths).
        """
        emb = None
        lock = getattr(self, "_embedder_open_lock", None)
        if lock is not None:
            with lock:
                emb = getattr(self, "_embedder", None)
                self._embedder = None
                self._embedder_state = "absent"
                # Keep open_failed sticky for this process life if it was set.
        else:
            emb = getattr(self, "_embedder", None)
            self._embedder = None
            if hasattr(self, "_embedder_state"):
                self._embedder_state = "absent"
        if emb is not None:
            close = getattr(emb, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001
                    _LOG.exception("embedder close failed")

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
        settings = getattr(self, "settings", None)
        if settings is None:
            return False
        mem_cfg = settings.memory
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
        """Lightweight memory health for ``/api/status`` (optional PR6).

        Includes a compact ``ladder`` sub-block (knobs + last_hourly_process /
        llm_calls) for dogfood observability (#92 design §10).

        Warm-on-start component fields + aggregate (KD-GATE / KD-MR / P4):
        ``edges_ready``, ``embedder_ready``, ``atom_store_ready``, ``index_ready``,
        aggregate ``memory_ready``, ``warming`` / ``memory_warming``. Nested
        ``edges`` / ``embedder`` / ``index`` objects for Glass honesty.
        ``chat_ready`` is never folded into ``memory_ready``.
        """
        from elyra.memory.edges import UnavailableEdgeStore
        from elyra.memory.readiness import (
            compute_memory_ready,
            edges_component_ready,
        )

        mem_cfg = self.settings.memory
        with self._lock:
            deferred_pending = len(self._deferred_recalls_jobs)
            deferred_metrics = {
                "queued": int(self._recalls_deferred_queued),
                "ok": int(self._recalls_deferred_ok),
                "dropped": int(self._recalls_deferred_dropped),
                "pending": deferred_pending,
                "skipped": dict(self._recalls_skipped),
                "queue_depth_max": int(self._deferred_recalls_depth),
            }
        emb_state = str(getattr(self, "_embedder_state", None) or "absent")
        if emb_state not in ("absent", "loading", "warm", "failed"):
            emb_state = "absent"
        emb_error: str | None = None
        if emb_state == "failed":
            emb_error = "embedder_failed"
        try:
            edges_open = self.edge_store_open_status()
        except Exception:  # noqa: BLE001 — status must never raise
            edges_open = {
                "state": str(getattr(self, "_edge_store_state", "absent") or "absent"),
                "attempts": int(getattr(self, "_edge_store_open_attempts", 0) or 0),
                "error": getattr(self, "_edge_store_error", None),
                "permanent_failed": bool(
                    getattr(self, "_edge_store_open_failed", False)
                ),
                "next_retry_at_monotonic": None,
            }

        # Edge health peek (no open side-effects beyond existing handle).
        edge_handle = getattr(self, "_edge_store", None)
        edge_state = str(edges_open.get("state") or "absent")
        edge_health: dict[str, Any] = {}
        edge_backend: str | None = None
        edge_count = 0
        disk_edge_count: int | None = None
        edge_count_parity: bool | None = None
        edge_error: str | None = edges_open.get("error")
        if (
            edge_handle is not None
            and not isinstance(edge_handle, UnavailableEdgeStore)
            and hasattr(edge_handle, "health")
        ):
            try:
                eh = edge_handle.health()
                if isinstance(eh, Mapping):
                    edge_health = dict(eh)
                    if eh.get("backend") is not None:
                        edge_backend = str(eh.get("backend"))
                    try:
                        edge_count = int(eh.get("edge_count") or 0)
                    except (TypeError, ValueError):
                        edge_count = 0
                    if eh.get("disk_edge_count") is not None:
                        try:
                            disk_edge_count = int(eh["disk_edge_count"])
                        except (TypeError, ValueError):
                            disk_edge_count = None
                    if "edge_count_parity" in eh:
                        edge_count_parity = bool(eh.get("edge_count_parity"))
                    if eh.get("error") is not None and edge_error is None:
                        edge_error = str(eh.get("error"))
            except Exception:  # noqa: BLE001
                edge_health = {}
        elif isinstance(edge_handle, UnavailableEdgeStore):
            if edge_error is None:
                edge_error = str(
                    getattr(edge_handle, "reason", "") or "edge_store_unavailable"
                )

        edges_ready = edges_component_ready(
            state=edge_state,
            handle=edge_handle,
            health=edge_health if edge_health else None,
            unavailable_type=UnavailableEdgeStore,
        )
        # health.ok when handle real; unavailable → ok false
        edges_ok = bool(edge_health.get("ok")) if edge_health else False
        if isinstance(edge_handle, UnavailableEdgeStore):
            edges_ok = False
        edges_open_flag = edge_state == "ready" and edge_handle is not None and (
            not isinstance(edge_handle, UnavailableEdgeStore)
        )
        edges_warming = edge_state in ("opening", "absent") and bool(
            getattr(self, "_memory_warming", False)
        )

        store_open = self._memory is not None
        store_ok = False
        store_error: str | None = None
        atom_count: Any = None
        line_count: Any = None
        health_backend: Any = None
        if self._memory is None:
            if self._memory_open_failed:
                store_error = "open_failed"
            elif not (mem_cfg.write_atoms or mem_cfg.enabled):
                store_error = "disabled"
        else:
            try:
                health = self._memory.health()
                if isinstance(health, Mapping):
                    store_ok = bool(health.get("ok"))
                    atom_count = health.get("atom_count")
                    line_count = health.get("line_count")
                    health_backend = health.get("backend")
                    if health.get("error") is not None:
                        store_error = str(health.get("error"))
                else:
                    store_ok = True
            except Exception as exc:  # noqa: BLE001
                store_ok = False
                store_error = str(exc) or type(exc).__name__

        index_handle = getattr(self, "_embedding_index", None)
        index_ready = index_handle is not None
        # Honesty: never claim index ready without a store handle (backing data).
        if not store_open:
            index_ready = False

        embedder_ready = emb_state == "warm"
        warming = bool(getattr(self, "_memory_warming", False))
        # Also surface warming while required edges are mid-open.
        if edge_state == "opening":
            warming = True
        if emb_state == "loading" and bool(getattr(mem_cfg, "embed_enabled", False)):
            warming = True

        agg = compute_memory_ready(
            enabled=bool(mem_cfg.enabled),
            write_atoms=bool(mem_cfg.write_atoms),
            backend=str(mem_cfg.backend),
            durable_edges_enabled=bool(
                getattr(mem_cfg, "durable_edges_enabled", False)
            ),
            embed_enabled=bool(getattr(mem_cfg, "embed_enabled", False)),
            store_open=store_open,
            store_ok=store_ok,
            index_ready=index_ready,
            edges_ready=edges_ready,
            embedder_ready=embedder_ready,
        )

        block: dict[str, Any] = {
            "enabled": bool(mem_cfg.enabled),
            "write_atoms": bool(mem_cfg.write_atoms),
            "backend": str(mem_cfg.backend),
            "store_open": store_open,
            "ok": store_ok,
            "has_last_meal": self._last_meal_snapshot is not None,
            "recalls_deferred": deferred_metrics,
            # Aggregate + component gates (P4 / KD-MR / KD-GATE).
            "memory_ready": bool(agg["memory_ready"]),
            "warming": warming,
            "memory_warming": warming,  # alias (P1 field name)
            "atom_store_ready": bool(agg["atom_store_ready"]),
            "index_ready": bool(agg["index_ready"]),
            "edges_ready": bool(agg["edges_ready"]),
            "embedder_ready": bool(agg["embedder_ready"]),
            "need_store": bool(agg["need_store"]),
            "need_index": bool(agg["need_index"]),
            "need_edges": bool(agg["need_edges"]),
            "need_embed": bool(agg["need_embed"]),
            # Backward-compat flat fields (P1).
            "embedder_state": emb_state,
            "edges_open": edges_open,
            # Nested component objects (design §2.4).
            "edges": {
                "ready": bool(edges_ready),
                "open": bool(edges_open_flag),
                "ok": bool(edges_ok),
                "warming": bool(edges_warming),
                "backend": edge_backend,
                "edge_count": edge_count,
                "disk_edge_count": disk_edge_count,
                "edge_count_parity": edge_count_parity,
                "error": edge_error,
                "state": edge_state,
                "attempts": int(edges_open.get("attempts") or 0),
                "next_retry_at": edges_open.get("next_retry_at_monotonic"),
            },
            "embedder": {
                "ready": bool(embedder_ready),
                "state": emb_state,
                "preload": bool(getattr(mem_cfg, "embed_preload", False)),
                "error": emb_error,
            },
            "index": {"ready": bool(index_ready)},
        }
        if atom_count is not None:
            block["atom_count"] = atom_count
        if line_count is not None:
            block["line_count"] = line_count
        if health_backend is not None:
            block["backend"] = str(health_backend) if health_backend else block["backend"]
        if store_error is not None:
            block["error"] = store_error

        # Ladder knobs always visible (even when store not yet open).
        try:
            from elyra.memory.ladder import ladder_status_snapshot

            block["ladder"] = ladder_status_snapshot(self._memory, mem_cfg)
        except Exception:  # noqa: BLE001 — status must never raise
            block["ladder"] = {
                "enabled": bool(getattr(mem_cfg, "ladder_enabled", True)),
                "summary_mode": str(
                    getattr(mem_cfg, "summary_mode", "template") or "template"
                ),
            }
        return block


    def last_meal_snapshot(self) -> dict[str, Any] | None:
        """Return a copy of the last composed meal inspect payload, if any."""
        with self._lock:
            snap = self._last_meal_snapshot
            return dict(snap) if isinstance(snap, dict) else None

    # ------------------------------------------------------------------
    # Phase 2a GraphView + TraversalSession (PR-A2)
    # ------------------------------------------------------------------

    @property
    def traversal(self) -> Any:
        """Process-local TraversalRegistry (tools inject via extras later)."""
        return self._traversal

    # Edge open SM: max transient retries + backoff (KD-ES-RETRY).
    _EDGE_OPEN_MAX_RETRIES = 3
    _EDGE_OPEN_BACKOFF_S = (5.0, 30.0, 120.0)

    @staticmethod
    def _edge_open_reason_class(reason: str | None) -> str:
        """Classify Unavailable / error reason: permanent | transient | integrity."""
        r = (reason or "").strip()
        if not r:
            return "transient"
        if r == "edge_backend_unavailable" or r.startswith(
            "edge_backend_unavailable"
        ):
            # ImportError / missing lancedb — will not heal without install.
            return "permanent"
        if r.startswith("edge_load_parity_failure") or r.startswith(
            "edge_count_parity_mismatch"
        ):
            # Integrity: need compact/quarantine/repair, not blind re-open loop.
            return "integrity"
        return "transient"

    def edge_store_open_status(self) -> dict[str, Any]:
        """Snapshot of EdgeStore open SM for Graph/status honesty (KD-ES-*)."""
        with self._edge_store_open_lock:
            return {
                "state": str(self._edge_store_state),
                "attempts": int(self._edge_store_open_attempts),
                "error": self._edge_store_error,
                "permanent_failed": bool(self._edge_store_open_failed),
                "next_retry_at_monotonic": (
                    float(self._edge_store_next_retry_at)
                    if self._edge_store_next_retry_at
                    else None
                ),
            }

    def _ensure_edge_store(self) -> Any | None:
        """Open EdgeStore under single-flight lock with retry state machine.

        Open is independent of ``durable_edges_enabled`` so glass/tests can
        read edges written when the flag was on.

        State machine (KD-ES-LOCK / KD-ES-RETRY)::

            absent → opening → ready | unavailable

        **Sticky soft-fail was the real path:** ``open_edge_store(fail_soft=True)``
        returns ``UnavailableEdgeStore`` without raising; the old ensure stored
        that handle and returned it forever because ``_edge_store is not None``.
        On **transient** retry we **null the Unavailable handle** and re-open.
        Permanent (ImportError class) and integrity (RAM=0/disk>0) do not
        auto-retry in a tight loop.

        Returns real store, ``UnavailableEdgeStore``, or ``None`` (disabled /
        atom store missing). GraphView treats Unavailable/None as no durable
        fabric — never as honest empty.
        """
        mem_cfg = self.settings.memory
        if not (mem_cfg.write_atoms or mem_cfg.enabled):
            return None

        from elyra.memory.edges import UnavailableEdgeStore, open_edge_store

        with self._edge_store_open_lock:
            # Ready: real backend handle only.
            if self._edge_store_state == "ready" and self._edge_store is not None:
                if not isinstance(self._edge_store, UnavailableEdgeStore):
                    return self._edge_store
                # Defensive: ready must never hold Unavailable.
                self._edge_store = None
                self._edge_store_state = "absent"

            if self._edge_store_open_failed:
                # Permanent fail — return sticky Unavailable if we have one.
                return self._edge_store

            now = time.monotonic()
            if self._edge_store_state == "unavailable":
                reason = self._edge_store_error
                rclass = self._edge_open_reason_class(reason)
                if rclass in ("permanent", "integrity"):
                    return self._edge_store
                if self._edge_store_open_attempts >= self._EDGE_OPEN_MAX_RETRIES:
                    self._edge_store_open_failed = True
                    return self._edge_store
                if now < float(self._edge_store_next_retry_at or 0.0):
                    return self._edge_store
                # Transient retry: **null the Unavailable handle** (critical).
                # Soft-fail handle retention was the sticky process-life death.
                if isinstance(self._edge_store, UnavailableEdgeStore):
                    self._edge_store = None
                self._edge_store_state = "absent"
                self._edge_store_error = None

            if self._edge_store is not None and not isinstance(
                self._edge_store, UnavailableEdgeStore
            ):
                self._edge_store_state = "ready"
                return self._edge_store

            # Single-flight open: lock held through construct/materialize.
            self._edge_store_state = "opening"
            self._edge_store_open_attempts += 1
            self._edge_store_open_attempted = True
            attempt = self._edge_store_open_attempts

            # Atom store root first (shared data/memory layout).
            if self._ensure_memory_store() is None:
                self._edge_store_state = "unavailable"
                self._edge_store_error = "atom_store_unavailable"
                self._edge_store_open_failed = True
                self._edge_store = UnavailableEdgeStore("atom_store_unavailable")
                return self._edge_store

            try:
                store = open_edge_store(self.paths, mem_cfg, fail_soft=True)
            except Exception as exc:  # noqa: BLE001 — edges optional
                reason = f"edge_backend_open_failed:{type(exc).__name__}"
                self._edge_store = UnavailableEdgeStore(reason)
                self._edge_store_state = "unavailable"
                self._edge_store_error = reason
                self._schedule_edge_open_retry(attempt, reason)
                _LOG.exception(
                    "edge store open raised; durable expand disabled "
                    "attempts=%d reason=%s",
                    attempt,
                    reason,
                )
                return self._edge_store

            if isinstance(store, UnavailableEdgeStore):
                reason = str(getattr(store, "reason", "") or "edge_backend_unavailable")
                self._edge_store = store
                self._edge_store_state = "unavailable"
                self._edge_store_error = reason
                self._schedule_edge_open_retry(attempt, reason)
                _LOG.warning(
                    "edge store open soft-fail attempts=%d reason=%s class=%s",
                    attempt,
                    reason,
                    self._edge_open_reason_class(reason),
                )
                return self._edge_store

            # Real store opened. RAM=0/disk>0 already fails inside Lance open
            # (Unavailable). Partial parity mismatch (0 < RAM < disk) keeps the
            # handle so Graph can surface counts, but health.ok=false (KD-ES-PARITY).
            try:
                eh = store.health() if hasattr(store, "health") else {}
            except Exception:  # noqa: BLE001
                eh = {}
            if isinstance(eh, Mapping) and eh.get("ok") is False:
                err = str(eh.get("error") or "edge_health_not_ok")
                if err.startswith("edge_load_parity_failure") or (
                    int(eh.get("disk_edge_count") or 0) > 0
                    and int(eh.get("edge_count") or 0) == 0
                ):
                    try:
                        close = getattr(store, "close", None)
                        if callable(close):
                            close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._edge_store = UnavailableEdgeStore(err)
                    self._edge_store_state = "unavailable"
                    self._edge_store_error = err
                    self._edge_store_open_failed = True
                    _LOG.warning(
                        "edge store load parity failure; unavailable reason=%s",
                        err,
                    )
                    return self._edge_store
                # Partial mismatch: retain handle; Graph honesty uses ok=false.
                _LOG.warning(
                    "edge store open with health.ok=false reason=%s "
                    "ram=%s disk=%s (retained for inspect)",
                    err,
                    eh.get("edge_count"),
                    eh.get("disk_edge_count"),
                )
                self._edge_store_error = err

            self._edge_store = store
            self._edge_store_state = "ready"
            self._edge_store_open_failed = False
            _LOG.info(
                "memory.edges.open_ready backend=%s attempts=%d health_ok=%s",
                getattr(mem_cfg, "backend", "?"),
                attempt,
                (eh.get("ok") if isinstance(eh, Mapping) else None),
            )
            return self._edge_store

    def _schedule_edge_open_retry(self, attempt: int, reason: str) -> None:
        """Set next_retry_at or permanent-fail based on reason class."""
        rclass = self._edge_open_reason_class(reason)
        if rclass in ("permanent", "integrity"):
            # Integrity: no auto-retry loop without compact/quarantine.
            # Permanent: ImportError will not heal.
            self._edge_store_open_failed = True
            self._edge_store_next_retry_at = 0.0
            return
        if attempt >= self._EDGE_OPEN_MAX_RETRIES:
            self._edge_store_open_failed = True
            self._edge_store_next_retry_at = 0.0
            return
        idx = min(attempt - 1, len(self._EDGE_OPEN_BACKOFF_S) - 1)
        delay = float(self._EDGE_OPEN_BACKOFF_S[max(0, idx)])
        self._edge_store_next_retry_at = time.monotonic() + delay

    def _memory_settings_with_wait(self) -> Any:
        """MemorySettings snapshot with runtime semantic_wait overlaid.

        Single path for meal rebuild_outer, graph_view, and any long-path
        consumer that must honor glass/API ``set_semantic_wait`` without
        using bare ``self.settings.memory`` as the ANN ceiling source.
        """
        mem_cfg = self.settings.memory
        with self._lock:
            sw = self._semantic_wait
            return replace(
                mem_cfg,
                semantic_wait_for_select=bool(sw.enabled),
                semantic_wait_max_ms=int(sw.max_ms),
            )

    def enqueue_deferred_recalls(
        self,
        src_atom_id: str = "",
        spoken_text: str = "",
        **kwargs: Any,
    ) -> bool:
        """Enqueue speak-time recalls for idle drain (KD-P0-defer).

        Product path from promote: never runs ANN. Drop-new when queue depth
        exceeds default 32 (metric ``recalls_deferred_dropped``). Gates match
        "would have called recalls" (durable_edges + semantic). Returns True
        when queued. Accepts positional or keyword ``src_atom_id`` /
        ``spoken_text`` (promote uses keywords).
        """
        aid = str(kwargs.get("src_atom_id", src_atom_id) or "").strip()
        text = str(kwargs.get("spoken_text", spoken_text) or "").strip()
        if not aid or not text:
            return False
        from elyra.memory.config import is_durable_edges_enabled

        mem = self.settings.memory
        if not is_durable_edges_enabled(mem):
            self._bump_recalls_skipped("flag_off")
            return False
        if not bool(getattr(mem, "semantic_enabled", False)):
            self._bump_recalls_skipped("flag_off")
            return False
        with self._lock:
            if len(self._deferred_recalls_jobs) >= self._deferred_recalls_depth:
                self._recalls_deferred_dropped += 1
                _LOG.debug(
                    "recalls deferred dropped (queue full depth=%s) src=%s",
                    self._deferred_recalls_depth,
                    aid,
                )
                return False
            self._deferred_recalls_jobs.append((aid, text))
            self._recalls_deferred_queued += 1
            return True

    def _bump_recalls_skipped(self, reason: str) -> None:
        key = str(reason or "unknown")
        with self._lock:
            self._recalls_skipped[key] = int(self._recalls_skipped.get(key, 0) or 0) + 1

    def _idle_deferred_recalls(self) -> None:
        """Drain one deferred speak-recalls job under semantic wait ceiling.

        Idle-only. Soft-skips cold/pressure/flag-off inside write_speak_recalls.
        Uses :func:`semantic_ann_deadline_ms` (wait helper authority).
        """
        with self._lock:
            if not self._deferred_recalls_jobs:
                return
            src_atom_id, spoken_text = self._deferred_recalls_jobs.popleft()
        try:
            from elyra.memory.config import semantic_ann_deadline_ms
            from elyra.memory.edges import write_speak_recalls

            mem_cfg = self._memory_settings_with_wait()
            max_ms = semantic_ann_deadline_ms(mem_cfg, "recalls")
            skip_metrics: dict[str, int] = {}
            written = write_speak_recalls(
                src_atom_id=src_atom_id,
                spoken_text=spoken_text,
                settings=mem_cfg,
                edge_store=self._ensure_edge_store(),
                index=self._ensure_embedding_index(),
                embedder=self._ensure_embedder(role="consumer"),
                encode_queue=self._encode_queue,
                store=self._ensure_memory_store(),
                max_ms=max_ms,
                skip_metrics=skip_metrics,
            )
            if written:
                with self._lock:
                    self._recalls_deferred_ok += 1
            else:
                reason = next(iter(skip_metrics), "empty")
                self._bump_recalls_skipped(str(reason))
        except Exception:  # noqa: BLE001 — never kill worker on recalls
            _LOG.exception(
                "deferred speak recalls failed src=%s", src_atom_id
            )
            self._bump_recalls_skipped("error")

    def graph_view(self) -> Any | None:
        """Build a GraphView from the open store + warm embedder if available.

        Never cold-loads torch. Returns None when the memory store is down.
        Structural walks work without index/embedder; semantic hops require
        a non-null index and already-warm embedder (GraphView policy).
        Injects EdgeStore when open so durable kinds union into neighbors.
        Injects MediaStore for multimodal ``seed_from_query`` (PR5).
        Settings include runtime semantic_wait overlay so traverse start/step
        ANN ceilings track glass wait (polish1 KD-P0 wiring).
        """
        store = self._ensure_memory_store()
        if store is None:
            return None
        try:
            from elyra.memory.graph import GraphView

            mem_cfg = self._memory_settings_with_wait()
            self._traversal.bind_settings(mem_cfg)
            index = self._ensure_embedding_index()
            # Consumer gated handle only (KD-E5); never cold-load for graph.
            embedder = self._ensure_embedder(role="consumer")
            edge_store = self._ensure_edge_store()
            media_store = None
            try:
                from elyra.media.store import MediaStore

                media_store = MediaStore(self.paths)
            except Exception:  # noqa: BLE001 — media optional for graph
                _LOG.debug("MediaStore open failed for graph_view", exc_info=True)
            return GraphView(
                store,
                index=index,
                embedder=embedder,
                settings=mem_cfg,
                edge_store=edge_store,
                media_store=media_store,
            )
        except Exception:  # noqa: BLE001 — fail closed for tools/glass
            _LOG.exception("graph_view factory failed")
            return None

    def _idle_traversal_ttl(self) -> None:
        """Abandon active traversal session when idle past traverse_session_ttl_s."""
        try:
            # Prefer wait overlay so glass max_ms is not clobbered by bare
            # settings if any future ANN path reuses the bound snapshot.
            self._traversal.bind_settings(self._memory_settings_with_wait())
            dropped = self._traversal.sweep_idle()
            if dropped is not None:
                _LOG.info(
                    "traversal idle TTL: timed_out session_id=%s",
                    dropped.session_id,
                )
        except Exception:  # noqa: BLE001
            _LOG.exception("traversal idle TTL sweep failed")

    def _close_traversal_for_moment(self, moment_id: str | None) -> None:
        """Moment end hygiene: abandon active only (KD-P-glass §5.1).

        Retains process-life glass ``last_session`` and meal directed_keep tray
        (B5 / KD-A16). Clear glass via operator ``clear_confirmed_keep(clear_glass=True)``
        or registry ``reset()``. No-op when traversal was never installed
        (partial worker fixtures).
        """
        trav = getattr(self, "_traversal", None)
        if trav is None:
            return
        try:
            trav.on_moment_close(moment_id)
        except Exception:  # noqa: BLE001
            _LOG.exception(
                "traversal moment-close cleanup failed moment_id=%s", moment_id
            )

    def _last_confirmed_keep_for_meal(
        self, moment_id: str | None = None
    ) -> tuple[list[str], str | None]:
        """Keep-set for next compose_meal from registry tray (KD-TRAY-SOT).

        ``moment_id`` is ignored for the meal path (B5b fix — no open-moment
        equality filter). Delegates only to ``TraversalRegistry.get_meal_keep_ids``;
        no worker-side tray cache.
        """
        del moment_id  # B5b: meal reads instance tray, not snap.moment filter
        try:
            return self._traversal.get_meal_keep_ids()
        except Exception:  # noqa: BLE001
            _LOG.exception("read meal keep ids from registry tray failed")
            return [], None

    def _record_last_meal_snapshot(
        self,
        package: Any,
        *,
        system_text: str = "",
        orient_text: str = "",
        budget_tokens: int | None = None,
        source: str = "rebuild_outer",
    ) -> None:
        """Best-effort: stash inspect payload for glass Memory Context tab.

        Also captures **raw** uncapped meal atom ids for ``created_with``
        (``_last_meal_atom_ids``). That list is independent of the glass DTO
        (which caps multi-atom ``atom_ids`` at 24) — KD-E17.
        """
        # Raw ids first (promote edges); inspect DTO is UI-only and may fail.
        try:
            from elyra.memory.promote import atom_ids_from_meal_items

            items = getattr(package, "items", None) or ()
            raw_ids = atom_ids_from_meal_items(items)
            with self._lock:
                self._last_meal_atom_ids = list(raw_ids)
        except Exception:  # noqa: BLE001
            _LOG.exception("record last meal atom ids failed")
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
                # Viewing observability (PR6): count / dirty / att_ids only.
                payload.update(self._viewing_status_block_unlocked())
                self._last_meal_snapshot = payload
        except Exception:  # noqa: BLE001 — never break rebuild for glass
            _LOG.exception("record last meal snapshot failed")

    def _promote_context_snapshot(self) -> Any:
        """Build PromoteContext from raw meal ids + EdgeStore (fail-soft).

        Returns a memory.promote.PromoteContext. Edge store open is independent
        of durable_edges_enabled; write path gates on the flag.
        """
        from elyra.memory.promote import PromoteContext

        with self._lock:
            ids = list(self._last_meal_atom_ids)
        edge_store = None
        try:
            edge_store = self._ensure_edge_store()
        except Exception:  # noqa: BLE001
            _LOG.exception("promote_context edge_store open failed")
            edge_store = None
        return PromoteContext(context_atom_ids=ids, edge_store=edge_store)

    def _idle_memory_ladder(self) -> None:
        """Budgeted ladder tick outside the state lock (idle only; never hop)."""
        if not self._memory_ladder_active():
            return
        store = self._memory
        if store is None:
            return
        try:
            from elyra.memory.ladder import tick
            from elyra.memory.ladder_llm import ChatClientSummaryLlm

            mem_cfg = self.settings.memory
            llm = None
            mode = str(getattr(mem_cfg, "summary_mode", "template") or "template").lower()
            if mode == "llm" and self.client is not None:
                llm = ChatClientSummaryLlm(self.client)
            identity_names: dict[str, str] | None = None
            try:
                # Soft display names for source packs (best-effort).
                id_store = getattr(self, "_identity", None) or getattr(
                    self, "identity", None
                )
                if id_store is not None and hasattr(id_store, "display_name"):
                    identity_names = {
                        "self": str(id_store.display_name() or "Elyra"),
                    }
            except Exception:  # noqa: BLE001
                identity_names = None
            tick(
                store,
                settings=mem_cfg,
                llm=llm,
                identity_names=identity_names,
            )
        except Exception:  # noqa: BLE001
            _LOG.exception("memory ladder tick failed")
        self._maybe_compact_memory_store()

    def rebuild_episodic_summaries(
        self,
        *,
        max_hours: int | None = None,
        max_ms: int | None = None,
        max_llm_calls: int | None = None,
    ) -> dict[str, Any]:
        """Operator force-rebuild of recent 1h tips + coarser cascade (Glass).

        Outside the state lock. Uses ChatClient ladder adapter when
        ``summary_mode=llm``. Returns rebuild status dict for the API.
        """
        store = self._ensure_memory_store()
        if store is None:
            return {"ok": False, "error": "store_unavailable"}
        if not self._memory_ladder_active():
            return {"ok": False, "error": "ladder_disabled"}
        try:
            from elyra.memory.ladder import rebuild_episodic_summaries
            from elyra.memory.ladder_llm import ChatClientSummaryLlm

            mem_cfg = self.settings.memory
            llm = None
            mode = str(getattr(mem_cfg, "summary_mode", "template") or "template").lower()
            if mode == "llm" and self.client is not None:
                llm = ChatClientSummaryLlm(self.client)
            identity_names: dict[str, str] | None = None
            try:
                id_store = getattr(self, "_identity", None) or getattr(
                    self, "identity", None
                )
                if id_store is not None and hasattr(id_store, "display_name"):
                    identity_names = {
                        "self": str(id_store.display_name() or "Elyra"),
                    }
            except Exception:  # noqa: BLE001
                identity_names = None
            result = rebuild_episodic_summaries(
                store,
                settings=mem_cfg,
                llm=llm,
                identity_names=identity_names,
                max_hours=max_hours,
                max_ms=float(max_ms) if max_ms is not None else None,
                max_llm_calls=max_llm_calls,
            )
            return result if isinstance(result, dict) else {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("rebuild_episodic_summaries failed")
            return {"ok": False, "error": str(exc) or type(exc).__name__}

    def backfill_durable_edges(
        self,
        *,
        max_atoms: int | None = None,
        max_ms: int | None = None,
        kinds: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Operator force structural edge backfill (Graph glass; polish1 PR4).

        Synchronous like ladder rebuild. Process-RAM last result for glass.
        """
        from elyra.memory.edges import backfill_durable_edges as _backfill

        store = self._ensure_memory_store()
        edge_store = self._ensure_edge_store()
        mem_cfg = self.settings.memory
        try:
            result = _backfill(
                store,
                edge_store,
                settings=mem_cfg,
                max_atoms=max_atoms,
                max_ms=max_ms,
                kinds=kinds,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("backfill_durable_edges failed")
            result = {
                "ok": False,
                "error": str(exc) or type(exc).__name__,
                "scanned": 0,
                "written": 0,
                "skipped": 0,
            }
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        # Process-life last result for Graph progress line.
        self._edge_backfill_last = dict(result)
        return result

    def last_edge_backfill_result(self) -> dict[str, Any] | None:
        """Return a copy of the last force-backfill result, if any."""
        snap = self._edge_backfill_last
        return dict(snap) if isinstance(snap, dict) else None

    def _desired_encode_owner(self) -> str:
        """Compute desired encode_owner from flags (none | idle | worker)."""
        mem_cfg = self.settings.memory
        if not mem_cfg.semantic_enabled or not mem_cfg.embed_enabled:
            return "none"
        if bool(getattr(mem_cfg, "encode_worker_enabled", True)):
            return "worker"
        return "idle"

    def _start_encode_worker_if_needed(self) -> None:
        """Start continuous EncodeWorker when flags allow. Soft-fail.

        When the start-path sole embedder loader is in flight (KD-EP), defers
        until ``_maybe_apply_embedder_loader_terminal`` so open_encoder is not
        dual-owned with the encode-worker first tick.
        """
        try:
            if self._encode_shutting_down:
                return
            if not self._encode_worker_start_allowed():
                return
            desired = self._desired_encode_owner()
            self._encode_owner = desired
            if desired != "worker":
                return
            w = self._encode_worker
            # Zombie or live handle: do not start a second thread.
            if w is not None and getattr(w, "is_alive", lambda: False)():
                return
            from elyra.memory.embed.worker import EncodeWorker

            mem_cfg = self.settings.memory
            poll_s = float(getattr(mem_cfg, "encode_worker_poll_s", 0.35) or 0.35)
            # Bind epoch so late ticks after stop no-op even if join timed out.
            epoch = int(self._encode_epoch)

            def _poll_bound() -> dict[str, Any] | None:
                return self._encode_poll_once(epoch=epoch)

            self._encode_worker = EncodeWorker(
                poll_once=_poll_bound,
                poll_s=poll_s,
                wake_event=self._encode_wake,
                generation=epoch,
            )
            # Owner set BEFORE first drain tick (KD-E7).
            self._encode_owner = "worker"
            self._encode_worker.start()
            _LOG.info(
                "memory.embed.encode_owner=worker poll_s=%.3f epoch=%d",
                poll_s,
                epoch,
            )
        except Exception:  # noqa: BLE001 — never kill presence
            _LOG.exception("start encode worker failed")

    def _stop_encode_worker(self, *, join_timeout_s: float = 2.0) -> bool:
        """Stop and join encode worker if running. Soft-fail.

        Bumps ``_encode_epoch`` so in-flight / zombie ticks no-op. Does **not**
        drop the worker handle while the thread is still alive (join timeout
        zombie) — ``is_alive()`` stays True so gap/restart cannot dual-drain.
        Returns True if thread is dead (or was absent).
        """
        # Invalidate any in-flight poll_once bound to the prior epoch.
        # getattr: partial workers (tests) may lack encode fields.
        self._encode_epoch = int(getattr(self, "_encode_epoch", 0)) + 1
        w = getattr(self, "_encode_worker", None)
        if w is None:
            return True
        dead = True
        try:
            stop = getattr(w, "stop", None)
            if callable(stop):
                dead = bool(stop(join_timeout_s=join_timeout_s))
            else:
                dead = not getattr(w, "is_alive", lambda: False)()
        except Exception:  # noqa: BLE001
            _LOG.exception("stop encode worker failed")
            dead = not getattr(w, "is_alive", lambda: False)()
        if dead or not getattr(w, "is_alive", lambda: False)():
            self._encode_worker = None
            return True
        # Zombie retained on self._encode_worker; is_alive True.
        _LOG.warning(
            "memory.embed.encode_worker_zombie retained epoch=%d",
            self._encode_epoch,
        )
        return False

    def _shutdown_encode(self) -> None:
        """Teardown encode path in run() finally: stop worker → close embedder.

        Order (KD-E13): signal + join encode worker, close embedder, owner=none.
        Longer join on shutdown; epoch + shutting_down block zombie publish.
        Soft-safe when encode subsystem was never initialized.
        """
        self._encode_shutting_down = True
        try:
            # Prefer a longer join on process teardown (cold load can be ~18s).
            self._stop_encode_worker(join_timeout_s=20.0)
        except Exception:  # noqa: BLE001
            _LOG.exception("shutdown encode worker failed")
        try:
            self._close_embedder()
        except Exception:  # noqa: BLE001
            _LOG.exception("shutdown embedder close failed")
        if hasattr(self, "_encode_owner"):
            self._encode_owner = "none"

    def _maybe_restart_encode_worker(self) -> None:
        """Restart dead encode worker with backoff (every presence loop).

        Never permanently switches to idle while encode_worker_enabled remains
        true (KD-E16). After max_restarts in the thrash window, applies backoff
        and **returns without restarting** this iteration (gap drain covers
        progress). Soft-fail; never raises.
        """
        try:
            if self._encode_shutting_down:
                return
            # Start-path sole loader still owns cold open — do not thrash-count
            # "restarts" every poll while embedder is loading (KD-EP).
            if not self._encode_worker_start_allowed():
                return
            desired = self._desired_encode_owner()
            if desired != "worker":
                # Flag flip: continuous off → stop worker, idle drain may run.
                if self._encode_owner == "worker" and desired == "idle":
                    self._stop_encode_worker()
                    self._encode_owner = "idle"
                elif desired == "none":
                    if self._encode_owner == "worker":
                        self._stop_encode_worker()
                    self._encode_owner = "none"
                else:
                    self._encode_owner = desired
                return

            self._encode_owner = "worker"
            w = self._encode_worker
            # Live or zombie thread: do not start a second worker.
            if w is not None and getattr(w, "is_alive", lambda: False)():
                # Healthy / still running — clear throttle flag only.
                self._encode_worker_restart_throttled = False
                return

            # Drop dead handle if present (thread exited after join timeout).
            if w is not None and not getattr(w, "is_alive", lambda: False)():
                self._encode_worker = None

            now = time.monotonic()
            if now < float(self._encode_worker_next_restart_at or 0.0):
                return

            mem_cfg = self.settings.memory
            window_s = float(
                getattr(mem_cfg, "encode_worker_restart_window_s", 60.0) or 60.0
            )
            max_restarts = int(
                getattr(mem_cfg, "encode_worker_max_restarts", 3) or 3
            )
            backoff_cap = float(
                getattr(mem_cfg, "encode_worker_restart_backoff_max_s", 30.0)
                or 30.0
            )

            # Prune restart times outside the thrash window.
            cutoff = now - max(1.0, window_s)
            self._encode_worker_restart_times = [
                t for t in self._encode_worker_restart_times if t >= cutoff
            ]
            in_window = len(self._encode_worker_restart_times)
            if in_window >= max(1, max_restarts):
                # Thrash budget exhausted: delay; do NOT restart this iteration.
                self._encode_worker_restart_throttled = True
                backoff = min(
                    backoff_cap,
                    max(float(self._encode_worker_backoff_s), 1.0) * 2.0,
                )
                self._encode_worker_backoff_s = backoff
                self._encode_worker_next_restart_at = now + backoff
                _LOG.error(
                    "memory.embed.encode_worker_restart_throttled "
                    "restarts_in_window=%d backoff_s=%.1f next_in=%.1f",
                    in_window,
                    backoff,
                    backoff,
                )
                return

            # Stop any stale dead handle, start fresh under a new epoch.
            self._stop_encode_worker()
            if self._encode_worker is not None and getattr(
                self._encode_worker, "is_alive", lambda: False
            )():
                # Zombie still running — wait for it; do not dual-start.
                return
            self._start_encode_worker_if_needed()
            self._encode_worker_restarts += 1
            self._encode_worker_restart_times.append(now)
            # Exponential backoff for the *next* death-restart (keep after
            # successful start so rapid die-loops are delayed).
            self._encode_worker_backoff_s = min(
                backoff_cap,
                max(0.5, float(self._encode_worker_backoff_s)) * 2.0
                if self._encode_worker_restarts > 1
                else 0.5,
            )
            self._encode_worker_next_restart_at = now + float(
                self._encode_worker_backoff_s
            )
            alive = (
                self._encode_worker is not None
                and getattr(self._encode_worker, "is_alive", lambda: False)()
            )
            if alive:
                _LOG.info(
                    "memory.embed.encode_worker_restart n=%d backoff_s=%.1f "
                    "next_restart_in=%.1f",
                    self._encode_worker_restarts,
                    self._encode_worker_backoff_s,
                    self._encode_worker_backoff_s,
                )
                # Do NOT clear next_restart_at — respects throttle / rapid-death
                # delay. Clears naturally when now >= next_restart_at.
        except Exception:  # noqa: BLE001
            _LOG.exception("maybe restart encode worker failed")

    def _gap_drain_if_needed(self) -> None:
        """One budgeted drain while owner=worker but thread not alive.

        Bridge during restart backoff (finalize_moment + idle path only).
        Never runs while a live or zombie worker thread still holds the handle
        (``is_alive``). Soft-fail; never raises.
        """
        try:
            if self._encode_owner != "worker":
                return
            if self._encode_shutting_down:
                return
            w = self._encode_worker
            if w is not None and getattr(w, "is_alive", lambda: False)():
                return
            if self._gap_drain_active:
                return
            self._gap_drain_active = True
            try:
                # Gap uses current epoch (intentional presence-thread drain).
                stats = self._encode_poll_once(epoch=None)
                if stats and int(stats.get("ok") or 0) > 0:
                    _LOG.info(
                        "memory.embed.gap_drain ok=%s remaining=%s "
                        "reason=worker_gap",
                        stats.get("ok"),
                        stats.get("remaining"),
                    )
            finally:
                self._gap_drain_active = False
        except Exception:  # noqa: BLE001
            self._gap_drain_active = False
            _LOG.exception("gap drain failed")

    def _encode_poll_once(
        self, *, epoch: int | None = None
    ) -> dict[str, Any] | None:
        """Shared catch-up + scan + budgeted drain (worker / idle / gap).

        When ``epoch`` is set (worker-bound tick), no-op if the epoch is stale
        (stop/shutdown bumped generation) or shutting down. Gap/idle pass
        ``epoch=None`` for intentional presence-thread drains.

        Never raises. Loader role may cold-open embedder on this call path.
        """
        # Stale worker generation → soft no-op (zombie after stop).
        if epoch is not None and int(epoch) != int(self._encode_epoch):
            return {
                "ok": 0,
                "failed": 0,
                "skipped": 0,
                "remaining": 0,
                "processed": 0,
                "reason": "stale_epoch",
            }
        if epoch is not None and self._encode_shutting_down:
            return {
                "ok": 0,
                "failed": 0,
                "skipped": 0,
                "remaining": 0,
                "processed": 0,
                "reason": "shutting_down",
            }
        mem_cfg = self.settings.memory
        if not mem_cfg.semantic_enabled or not mem_cfg.embed_enabled:
            return None
        # Worker tick only drains when owner=worker; idle path uses owner=idle.
        # Gap drain also runs with owner=worker.
        owner = self._encode_owner
        if owner not in ("worker", "idle"):
            return None
        store = self._memory
        if store is None:
            store = self._ensure_memory_store()
        if store is None:
            return None
        queue = self._encode_queue
        if queue is None:
            self._install_encode_hooks(store, mem_cfg)
            queue = self._encode_queue
        if queue is None:
            return None
        try:
            from elyra.memory.embed.queue import (
                catchup_none_atoms_for_encode,
                scan_pending_into_queue,
            )

            max_items = max(1, int(mem_cfg.encode_max_items_per_tick or 4))
            # OQ4: historical none → pending under process-life budget.
            # Continuous mode owns the single catch-up counter (KD-E7).
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
            # Re-check epoch after store I/O (stop may have raced).
            if epoch is not None and int(epoch) != int(self._encode_epoch):
                return {
                    "ok": 0,
                    "processed": 0,
                    "remaining": queue.qsize(),
                    "reason": "stale_epoch",
                }
            scan_pending_into_queue(
                store,
                queue,
                limit=max_items * 4,
            )
            # Loader role: may cold-load on worker/gap/idle drain thread only.
            embedder = self._ensure_embedder(role="loader")
            if embedder is None:
                # Still loading / failed — soft skip this tick.
                return {
                    "ok": 0,
                    "failed": 0,
                    "skipped": 0,
                    "remaining": queue.qsize(),
                    "processed": 0,
                    "reason": "embedder_unavailable",
                }
            # Epoch again after possible long cold load.
            if epoch is not None and int(epoch) != int(self._encode_epoch):
                return {
                    "ok": 0,
                    "processed": 0,
                    "remaining": queue.qsize(),
                    "reason": "stale_epoch",
                }
            media_store = None
            try:
                from elyra.media.store import MediaStore

                media_store = MediaStore(self.paths)
            except Exception:  # noqa: BLE001 — media optional for encode
                _LOG.debug("MediaStore open for encode failed", exc_info=True)
            index = self._ensure_embedding_index()
            gate = self._get_embedder_gate()
            edge_store = self._ensure_edge_store()
            drain_t0 = time.monotonic()
            stats = queue.drain(
                store,
                embedder,
                index=index,
                max_ms=int(mem_cfg.encode_max_ms_per_tick or 100),
                max_items=max_items,
                max_attempts=int(mem_cfg.encode_max_attempts or 3),
                media_store=media_store,
                settings=mem_cfg,
                gate=gate,
                edge_store=edge_store,
            )
            drain_ms = int((time.monotonic() - drain_t0) * 1000.0)
            # Wall-clock ISO for Vectors/inspect health (not monotonic).
            from elyra.memory.types import utc_now_iso

            self._encode_last_drain_at = utc_now_iso()
            if isinstance(stats, dict):
                stats = dict(stats)
                stats.setdefault("ms", drain_ms)
                self._encode_last_drain_stats = stats
            else:
                self._encode_last_drain_stats = None
            self._encode_drain_ok_total += int((stats or {}).get("ok") or 0)
            self._encode_drain_failed_total += int(
                (stats or {}).get("failed") or 0
            )
            return stats if isinstance(stats, dict) else None
        except Exception:  # noqa: BLE001 — never kill presence / worker
            _LOG.exception("memory encode poll_once failed")
            return None

    def _idle_memory_encode(self) -> None:
        """Legacy idle-only corpus drain when encode_owner=idle (rollback).

        When encode_owner=worker (continuous mode, including restart gaps),
        this is a no-op — the EncodeWorker (or gap drain) owns bulk drain.
        Outside the state lock; never runs mid-hop. Soft-fail.
        """
        try:
            # Keep owner in sync with flags (tests may not call start).
            desired = self._desired_encode_owner()
            if self._encode_owner == "none" and desired == "idle":
                self._encode_owner = "idle"
            if self._encode_owner == "worker":
                return
            if desired != "idle":
                # Continuous intended but worker not started yet (unit tests
                # that never call _start_encode_worker_if_needed): still no
                # idle drain when flag is on — use _encode_poll_once / worker.
                if desired == "worker":
                    # For unit tests that only call _idle_memory_encode with
                    # encode_worker_enabled default true: fall through only
                    # when owner was never set to worker. Prefer explicit
                    # owner=idle via encode_worker_enabled=false for idle path.
                    return
                return
            self._encode_owner = "idle"
            self._encode_poll_once()
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
            err = "store_unavailable"
            return {
                "ok": False,
                "error": err,
                "optimized": False,
                "notes": [err],
                "note": err,
            }
        index = self._ensure_embedding_index()
        if index is None:
            err = "index_unavailable"
            return {
                "ok": False,
                "error": err,
                "optimized": False,
                "notes": [err],
                "note": err,
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
            # KD-R3 rebuild honesty: always expose notes[] (and legacy note join).
            notes = out.get("notes")
            if not isinstance(notes, list):
                legacy = out.get("note")
                notes = [str(legacy)] if legacy else []
                out["notes"] = notes
            if "note" not in out or not out.get("note"):
                out["note"] = "; ".join(str(n) for n in notes) if notes else ""
            _LOG.info("memory vector index rebuild: %s", out)
            return out
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("memory vector index rebuild failed")
            return {
                "ok": False,
                "error": str(exc) or type(exc).__name__,
                "optimized": False,
                "notes": [str(exc) or type(exc).__name__],
                "note": str(exc) or type(exc).__name__,
            }

    def _idle_memory_joint_repair(self) -> None:
        """Idle-only joint-copy repair continue (KD-R11). Never mid-hop / meal.

        Fills emb_joint = copy(sole modality) for ready rows missing joint.
        Caps via ``joint_repair_max_per_tick``. No encoder.
        """
        mem_cfg = self.settings.memory
        if not (mem_cfg.semantic_enabled or self._embedding_index is not None):
            return
        store = self._memory
        if store is None and (
            mem_cfg.write_atoms or mem_cfg.enabled or mem_cfg.semantic_enabled
        ):
            store = self._ensure_memory_store()
        if store is None:
            return
        index = self._ensure_embedding_index()
        if index is None:
            return
        try:
            health = index.health() if hasattr(index, "health") else {}
            remaining = 0
            if isinstance(health, dict):
                remaining = int(health.get("joint_repair_remaining") or 0)
            if remaining <= 0:
                # Also check store directly when index health lacks the field.
                store_fn = getattr(store, "joint_repair_remaining", None)
                if callable(store_fn):
                    remaining = int(store_fn() or 0)
            if remaining <= 0:
                return
            # 0 is a valid disable; only None/missing falls back to default 64.
            raw_tick = getattr(mem_cfg, "joint_repair_max_per_tick", None)
            limit = 64 if raw_tick is None else max(0, int(raw_tick))
            if limit <= 0:
                return
            repair_fn = getattr(index, "repair_joint_copies", None)
            if not callable(repair_fn):
                repair_fn = getattr(store, "repair_joint_copies", None)
            if callable(repair_fn):
                result = repair_fn(limit=limit)
                _LOG.debug("memory joint repair: %s", result)
        except Exception:  # noqa: BLE001 — never kill presence
            _LOG.exception("memory idle joint repair failed")

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
        """Mark current 1h dirty after moment close (no LLM on hop path).

        Retains the historical method name for call-site compatibility; body
        only dirty-marks so idle ``tick`` can process the hour later.
        """
        if not self._memory_ladder_active():
            return
        store = self._memory
        if store is None:
            return
        try:
            from elyra.memory.ladder import mark_dirty_1h

            mark_dirty_1h(store, datetime.now(UTC))
        except Exception:  # noqa: BLE001
            _LOG.exception("memory ladder dirty-mark finalize failed")
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

            # Consumer-only embedder (no cold load) + edge/index for recalls.
            promote_wake_observation(
                store,
                moment_id,
                content=content_s,
                message_id=message_id_s,
                media_ids=media_ids,
                why_now=why,
                settings=mem_cfg,
                promote_context=self._promote_context_snapshot(),
                edge_store=self._ensure_edge_store(),
                embedder=self._ensure_embedder(role="consumer"),
                index=self._ensure_embedding_index(),
                encode_queue=self._encode_queue,
                enqueue_speak_recalls=self.enqueue_deferred_recalls,
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
            # Soft conversation_id from wake.payload only — never invent from
            # any client session (continuous/timer stay null; PR3b / KD4 solo).
            conv_id = _conversation_id_from_wake(wake)
            why = _why_now(wake)
            try:
                self._moments.open_moment(
                    why_now=why,
                    user_id=user_id,
                    wake_id=wake.id,
                    moment_id=moment_id,
                    conversation_id=conv_id,
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

        # Snapshot continuous + meal budget for this moment (no lock held during
        # do-loop). Mid-moment glass PATCH to meal_budget applies **next moment**
        # so outer meal size and in-turn cap stay Policy A-aligned for the hop.
        with self._lock:
            cont_on = bool(self._continuous.enabled)
            meal_state_for_moment = MealBudgetState(
                fraction=float(self._meal_budget.fraction)
            )
        has_open = self._has_open_work()
        # Policy A: one moment-scoped effective budget for sliding + in-turn.
        meal_tokens = effective_meal_budget_tokens(
            self.settings, meal_state_for_moment
        )

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
            # Meal token budget is moment-scoped (meal_tokens) — not re-read
            # from runtime mid-moment (avoids outer/in-turn desync).
            glass_list_limit = int(
                getattr(self.settings.memory, "glass_tail_list_limit", 80) or 80
            )
            glass = list_messages(limit=glass_list_limit, paths=self.paths)
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

            budget = meal_tokens

            use_memory_meal = self._memory_meal_active()
            if use_memory_meal:
                try:
                    from elyra.loop.context import fill_orient, format_now
                    from elyra.memory.meal import (
                        SOCIAL_WAKE_KINDS,
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
                    # Overlay runtime wait toggle so first outer + re-outer both
                    # honor glass/API wait-for-select (CPU dogfood).
                    mem_cfg = self._memory_settings_with_wait()
                    # Semantic select: index + gated consumer embedder only
                    # (KD12 — no cold load; KD-E5 — lookup priority via gate).
                    meal_index = None
                    meal_embedder = None
                    if mem_cfg.semantic_enabled:
                        meal_index = self._ensure_embedding_index()
                        meal_embedder = self._ensure_embedder(role="consumer")
                    # PR-A3 / KD-A16: directed_keep from last_confirmed_keep on
                    # next natural compose only (no soft re-outer on finish).
                    dk_ids, dk_summary = self._last_confirmed_keep_for_meal(
                        moment_id
                    )
                    social = wake.kind in SOCIAL_WAKE_KINDS
                    package = compose_meal(
                        self._memory,
                        open_moment_id=moment_id,
                        budget_tokens=budget,
                        system_text=system_text,
                        orient_text=orient_body,
                        settings=mem_cfg,
                        index=meal_index,
                        embedder=meal_embedder,
                        directed_keep_ids=dk_ids or None,
                        directed_keep_summary=dk_summary,
                        glass_rows=glass,
                        social_wake=social,
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
                        directed_keep_ids=dk_ids or None,
                        directed_keep_summary=dk_summary,
                        glass_rows=glass,
                        social_wake=social,
                    )
                    viewing_ids = self._snapshot_viewing_att_ids()
                    viewing_entries = self._snapshot_viewing_entries()
                    expanded = expand_memory_meal_for_provider(
                        meal,
                        glass_by_id=glass_by_id,
                        wake_message_id=wake_message_id_s,
                        viewing_att_ids=viewing_ids or None,
                        viewing_entries=viewing_entries or None,
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
                sliding_input_tokens=budget,
                retain_ids=True,
            )
            viewing_ids = self._snapshot_viewing_att_ids()
            viewing_entries = self._snapshot_viewing_entries()
            expanded = expand_meal_for_provider(
                meal,
                glass_by_id=glass_by_id,
                wake_message_id=wake_message_id_s,
                viewing_att_ids=viewing_ids or None,
                viewing_entries=viewing_entries or None,
                media_store=media_store,
                provider=provider_name,
            )
            return strip_meal_wire_fields(expanded)

        registry = self._ensure_registry()
        with self._lock:
            hop_delay = effective_hop_delay_seconds(self._dev_speed)
        # Policy A: both sliding and in-turn use the same moment-scoped tokens.
        settings_for_loop = replace(
            self.settings,
            loop=replace(
                self.settings.loop,
                sliding_input_tokens=meal_tokens,
                in_turn_max_tokens=meal_tokens,
            ),
        )
        mem = self._ensure_memory_store()
        result = self._run_do_loop(
            client=self.client,
            registry=registry,
            ctx=ctx,
            rebuild_outer=rebuild_outer,
            settings=settings_for_loop,
            moments=self._moments,
            social_wake=social,
            wake_kind=wake.kind,
            has_open_goals_slice=has_open,
            continuous_enabled=cont_on,
            hop_delay_seconds=hop_delay,
            drain_interjections=self._drain_interjections,
            memory_store=mem,
            memory_settings=self.settings.memory,
            promote_context_fn=self._promote_context_snapshot,
            edge_store=self._ensure_edge_store(),
            embedder=self._ensure_embedder(role="consumer"),
            index=self._ensure_embedding_index(),
            encode_queue=self._encode_queue,
            enqueue_speak_recalls=self.enqueue_deferred_recalls,
            viewing_dirty_fn=self._is_viewing_dirty,
            clear_viewing_dirty_fn=self._clear_viewing_dirty,
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

            # Phase 2a / KD-P-glass: abandon active only; retain last_session + meal tray.
            self._close_traversal_for_moment(moment_id)

            # KD-V12: viewing set is moment-local — no cross-moment media-part leak.
            self._clear_moment_viewing_unlocked()

        # After close (outside long critical sections): dirty-mark current 1h
        # for the just-ended moment (no LLM on hop path — KD20).
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
        # Option A: only from DoLoopResult (model tool-batch audit) — never host I/O.
        ledger_audited = bool(result.ledger_audited) if result else False
        model_beats = int(result.model_beats) if result else 0
        flood_beats = int(result.channel_flood_beats) if result else 0
        last_flood = bool(result.last_stop_hop_was_flood) if result else False

        pending_task_ready = len(self._queue.pending_of_kind("task_ready"))
        pending_continues = len(self._queue.pending_of_kind("moment_continue"))
        has_pending_wait = bool(
            self._timers.list_waits(status=STATUS_PENDING)
        )
        # Re-read ledger at finalize so mid-moment closes are respected (K18).
        # This host re-read does NOT set ledger_audited (KD21).
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
            ledger_audited=ledger_audited,
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
                    "ledger_audited=%s pending_task_ready=%d",
                    decision.reason,
                    moment_id,
                    wake.kind,
                    tools_ran,
                    ledger_mutated,
                    ledger_audited,
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
            self._close_traversal_for_moment(moment_id)
            # KD-V12: error path also drops moment-local viewing set.
            self._clear_moment_viewing_unlocked()

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
            payload: dict[str, Any] = {
                "content": item.content,
                "user_id": item.user_id,
                "message_id": item.message_id or str(uuid.uuid4()),
                "from_interject_remainder": True,
            }
            _stamp_social_payload(
                payload,
                conversation_id=item.conversation_id,
                social_kind=item.social_kind,
            )
            self._queue.enqueue("user_message", payload)

    def _drain_interjections(self) -> Sequence[Any]:
        """Do-loop safe-point drain → list of content strings / dicts."""
        with self._lock:
            items = self._interject.drain()
        out: list[dict[str, Any]] = []
        for it in items:
            row: dict[str, Any] = {
                "content": it.content,
                "user_id": it.user_id,
                "message_id": it.message_id,
            }
            if it.conversation_id:
                row["conversation_id"] = it.conversation_id
            if it.social_kind:
                row["social_kind"] = it.social_kind
            out.append(row)
        return out

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

    # ------------------------------------------------------------------
    # Moment viewing set (KD-V4 / KD-V12 / KD-V13)
    # ------------------------------------------------------------------

    def _snapshot_viewing_att_ids(self) -> list[str]:
        """FIFO att_ids for expand; lock-held snapshot, expand uses copy."""
        with self._lock:
            return list(self._moment_viewing.keys())

    def _snapshot_viewing_entries(self) -> dict[str, ViewingEntry]:
        """Shallow copy of viewing entries (duration_s etc.) for expand."""
        with self._lock:
            return dict(self._moment_viewing)

    def _viewing_status_block_unlocked(self) -> dict[str, Any]:
        """Operator viewing fields. Caller holds ``self._lock`` (or single-thread)."""
        from elyra.media.viewing import viewing_observability

        return viewing_observability(
            self._moment_viewing,
            dirty=bool(self._viewing_dirty),
        )

    def _is_viewing_dirty(self) -> bool:
        with self._lock:
            return bool(self._viewing_dirty)

    def _clear_viewing_dirty(self) -> None:
        with self._lock:
            self._viewing_dirty = False

    def _set_viewing_dirty(self) -> None:
        """Tool / host port: mark viewing dirty (KD-V13 force re-outer)."""
        with self._lock:
            self._viewing_dirty = True

    def _clear_moment_viewing_unlocked(self) -> None:
        """Clear viewing set + dirty. Caller holds ``self._lock``."""
        self._moment_viewing.clear()
        self._viewing_dirty = False

    def mark_viewing(
        self,
        att_id: str,
        *,
        kind: str = "file",
        mime: str = "application/octet-stream",
        filename: str = "file",
        byte_size: int = 0,
        duration_s: float | None = None,
    ) -> list[str]:
        """Add att_id to the moment viewing set and set dirty (test / tool port).

        Returns the FIFO att_id list after the op. Always dirties on success
        (including re-view — no reorder).
        """
        from elyra.media.viewing import add_viewing

        with self._lock:
            add_viewing(
                self._moment_viewing,
                att_id,
                kind=kind,
                mime=mime,
                filename=filename,
                byte_size=byte_size,
                duration_s=duration_s,
            )
            self._viewing_dirty = True
            return list(self._moment_viewing.keys())

    def drop_from_viewing(self, att_id: str) -> bool:
        """Remove one att_id under lock. Dirties only when something was removed."""
        from elyra.media.viewing import drop_viewing

        with self._lock:
            removed = drop_viewing(self._moment_viewing, att_id)
            if removed:
                self._viewing_dirty = True
            return removed

    def clear_moment_viewing(self) -> int:
        """Empty viewing set under lock. Dirties only when set was non-empty."""
        from elyra.media.viewing import clear_viewing

        with self._lock:
            n = clear_viewing(self._moment_viewing)
            if n > 0:
                self._viewing_dirty = True
            return n

    def _build_tool_context(self, wake: WakeItem, moment_id: str) -> ToolContext:
        user_id = _user_id_from_wake(wake)  # may be None — do not force "operator"
        payload = wake.payload or {}
        # conversation_id + social_kind from wake.payload only — never invent
        # from any client session (pure work / continuous stay unscoped).
        conv_id = _conversation_id_from_wake(wake)
        raw_kind = payload.get("social_kind")
        if raw_kind in ("group", "dm"):
            extras_kind = str(raw_kind)
        else:
            extras_kind = "none"
        return ToolContext(
            paths=self.paths,
            sandbox=self._ensure_sandbox(),
            settings=self.settings,
            moment_id=moment_id,
            user_id=user_id,
            conversation_id=conv_id,
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
                "social_kind": extras_kind,  # KD3 step-4 gate (group/dm/none)
                "identity": self._identity,
                "users": self._users,
                # install_skill / growth tools reload the held catalog
                "skills": self._ensure_skills(),
                # Phase 2a directed traversal: thin tools resolve these ports.
                # graph_view is a factory (fresh view per call; warm embedder only).
                "graph_view": self.graph_view,
                "traversal": self._traversal,
                # Wait overlay so tools rebind registry with runtime semantic_wait
                # (not bare settings.memory product defaults).
                "memory_settings_with_wait": self._memory_settings_with_wait,
                # Moment viewing set + promote ports (view_media tool).
                "moment_viewing": self._moment_viewing,
                "viewing_dirty_ref": lambda: self._viewing_dirty,
                "set_viewing_dirty": self._set_viewing_dirty,
                "mark_viewing": self.mark_viewing,
                "drop_viewing": self.drop_from_viewing,
                "clear_viewing": self.clear_moment_viewing,
                "memory_store": self._ensure_memory_store(),
                # Durable edge promote context (created_with / in_moment).
                "promote_context_fn": self._promote_context_snapshot,
                "edge_store": self._ensure_edge_store(),
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
        # KD9: conversations are social state — clear with messages.
        _step("conversations", lambda: clear_conversations(self.paths))
        # KD21: per-client session registry (dogfood concurrent principals).
        _step("client_sessions", lambda: clear_client_sessions(self.paths))
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
            _step("conversations", lambda: clear_conversations(self.paths))
            _step("client_sessions", lambda: clear_client_sessions(self.paths))
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
            self._last_meal_atom_ids = []
            self._clear_moment_viewing_unlocked()
            try:
                self._traversal.reset()
            except Exception:  # noqa: BLE001
                _LOG.exception("traversal reset failed")
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
        conversation_id: str | None = None,
        social_kind: str | None = None,
    ) -> dict[str, Any]:
        """Mark wait answered and enqueue wait_reply (phase stays waiting).

        Prefer wait-record ``conversation_id`` when present (PR3c stores it on
        WaitArm / PendingWait). Read the wait object via ``mark_wait_answered``
        return value (or ``get_wait``, which includes answered rows) — never
        ``list_waits()`` pending-only after mark, which cannot see the row.
        When the wait address wins, re-derive ``social_kind`` from that id so a
        group wait is not stamped ``social_kind=dm`` from a Private Chat session.
        """
        text = content.strip() if isinstance(content, str) else ""
        choice_s = (
            choice.strip()
            if isinstance(choice, str)
            else (str(choice) if choice is not None else "")
        )
        wait_obj: Any = None
        if wait_id:
            try:
                # mark_wait_answered returns the wait (incl. after status→answered).
                wait_obj = self._timers.mark_wait_answered(wait_id)
            except KeyError:
                _LOG.warning("wait_reply for unknown wait_id=%s", wait_id)
                # Still try get_wait (covers races / already-terminal rows).
                try:
                    wait_obj = self._timers.get_wait(wait_id)
                except Exception:  # noqa: BLE001
                    wait_obj = None

        wait_cid = _conversation_id_from_wait_obj(wait_obj)

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
        # Prefer wait record address; when wait wins, kind must match wait cid.
        if wait_cid is not None:
            from elyra.conversations import social_kind_for

            cid = wait_cid
            kind = social_kind_for(wait_cid)
        else:
            cid = conversation_id
            kind = social_kind
        _stamp_social_payload(payload, conversation_id=cid, social_kind=kind)
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
