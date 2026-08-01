"""Supervisor-owned grok_build job reaper (KD11).

Scope: daemon thread that polls job PIDs, finalizes result.json, shreds tokens,
enqueues completion wake kind=background with payload source=grok_build.
In scope: shared WakeQueue injection, startup GC, wall-timeout kill, usage_bridge.
Out of scope: tool handler spawn path, presence do-loop, inventing wake kinds.

**MANDATORY:** ``wake_queue`` must be the supervisor's shared WakeQueue instance
(same object as PresenceWorker). NEVER construct a private ``WakeQueue(paths)``
here — a second instance would append events.jsonl but never update the worker's
in-process heap, dropping completion wakes until full reload.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from elyra.config import ElyraPaths
from elyra.instrument.jobs import (
    ARTIFACTS_DIR_NAME,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_NEEDS_HUMAN,
    JOB_STATUS_RUNNING,
    RESULT_NAME,
    STDERR_NAME,
    STDOUT_NAME,
    TERMINAL_STATUSES,
    JobMeta,
    gc_interrupted,
    is_pid_alive,
    list_jobs,
    load_job,
    load_result,
    read_log,
    run_dir_for,
    shred_tokens,
    update_job_status,
    write_result,
)
from elyra.instrument.result import (
    harvest_artifacts,
    make_error_payload,
    make_success_payload,
    resolve_status_from_harvest,
)
from elyra.instrument.usage_bridge import record_instrument_usage
from elyra.llm.usage import UsageMeter
from elyra.presence.queue import WakeQueue

logger = logging.getLogger(__name__)

# Completion wake kind — closed KNOWN_KINDS only (never instrument_job).
COMPLETION_WAKE_KIND = "background"
COMPLETION_SOURCE = "grok_build"

DEFAULT_POLL_INTERVAL_S = 1.0
DEFAULT_JOIN_TIMEOUT_S = 5.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not isinstance(ts, str):
        return None
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _kill_pgid(pgid: int | None, pid: int | None) -> None:
    """Best-effort kill process group (wall timeout / shutdown)."""
    target = pgid if pgid is not None else pid
    if target is None or target <= 0:
        return
    try:
        os.killpg(int(target), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        if pid is not None:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                pass
    # Brief grace then SIGKILL.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if not is_pid_alive(pid if pid is not None else target):
            return
        time.sleep(0.05)
    try:
        os.killpg(int(target), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        if pid is not None:
            try:
                os.kill(int(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass


def build_completion_payload(
    meta: JobMeta,
    *,
    status: str,
    summary_path: str | None = None,
) -> dict[str, Any]:
    """Non-secret wake payload for kind=background (design normative)."""
    return {
        "source": COMPLETION_SOURCE,
        "job_id": meta.job_id,
        "run_id": meta.run_id or meta.job_id,
        "status": status,
        "mode": meta.mode,
        "summary_path": summary_path or "",
    }


def parse_headless_json(stdout: str) -> dict[str, Any] | None:
    """Best-effort parse of grok --output-format json stdout."""
    text = (stdout or "").strip()
    if not text:
        return None
    # Try whole stdout first.
    try:
        raw = json.loads(text)
        if isinstance(raw, dict):
            return raw
    except json.JSONDecodeError:
        pass
    # Last JSON object line (common when logs interleave).
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            raw = json.loads(line)
            if isinstance(raw, dict):
                return raw
        except json.JSONDecodeError:
            continue
    return None


def finalize_job(
    paths: ElyraPaths,
    job_id: str,
    *,
    meter: UsageMeter | None = None,
    status_override: str | None = None,
    error_reason: str | None = None,
    exit_code: int | None = None,
    timed_out: bool = False,
    known_values: list[str] | None = None,
) -> tuple[JobMeta, dict[str, Any]]:
    """Harvest logs, write result.json, shred tokens, update meta.

    Idempotent when result already exists and status is terminal.
    Returns (updated_meta, result_payload).
    """
    meta = load_job(paths, job_id)
    if meta is None:
        raise FileNotFoundError(f"job_not_found: {job_id}")

    run_dir = run_dir_for(paths, job_id)
    existing = load_result(paths, job_id)
    if existing is not None and meta.status in TERMINAL_STATUSES:
        # Still ensure tokens are gone.
        shred_tokens(run_dir)
        return meta, existing

    stdout = read_log(paths, job_id, STDOUT_NAME)
    stderr = read_log(paths, job_id, STDERR_NAME)
    headless = parse_headless_json(stdout)

    # Usage bridge (record known tokens only).
    usage_src: dict[str, Any] | None = headless
    if usage_src is None and headless is None:
        usage_src = None
    bridge = record_instrument_usage(meter, usage_src)

    artifacts_dir = run_dir / ARTIFACTS_DIR_NAME
    harvest = harvest_artifacts(
        mode=meta.mode,
        artifacts_dir=artifacts_dir if artifacts_dir.is_dir() else None,
        stdout_text=stdout,
        apply_copies=True,
    )

    code = exit_code if exit_code is not None else meta.exit_code
    if code is None and timed_out:
        code = -9

    if status_override is not None:
        status = status_override
    elif timed_out:
        status = JOB_STATUS_FAILED
        error_reason = error_reason or "timeout"
    elif error_reason == "interrupted":
        status = JOB_STATUS_INTERRUPTED
    else:
        status = resolve_status_from_harvest(harvest, exit_code=code)
        if status == JOB_STATUS_FAILED and not error_reason:
            error_reason = harvest.get("error_reason") or "nonzero_exit"
        if status == JOB_STATUS_NEEDS_HUMAN:
            error_reason = None

    # Auth-looking death (best-effort heuristics).
    combined = f"{stdout}\n{stderr}".lower()
    if status == JOB_STATUS_FAILED and error_reason in (None, "nonzero_exit"):
        if "auth" in combined and (
            "expired" in combined or "reauth" in combined or "unauthorized" in combined
        ):
            error_reason = (
                "auth_expired" if "expired" in combined else "auth_unavailable"
            )

    summary = ""
    if headless and isinstance(headless.get("text"), str):
        summary = headless["text"][:2000]
    elif stdout:
        summary = stdout[:2000]
    if harvest.get("needs_human") and not summary:
        summary = "needs_human"

    # Redact summary/logs lightly via known_values if provided.
    if known_values:
        from elyra.instrument.redact import redact_string

        summary = redact_string(summary, known_values)

    result_path = run_dir / RESULT_NAME
    if status in (JOB_STATUS_COMPLETED, JOB_STATUS_NEEDS_HUMAN):
        result = make_success_payload(
            mode=meta.mode,
            run_id=meta.run_id or meta.job_id,
            status=status,
            summary=summary,
            open_questions=list(harvest.get("open_questions") or []),
            artifacts=list(harvest.get("artifacts") or []),
            usage=bridge.payload_usage,
            exit_code=code if code is not None else 0,
            job_id=meta.job_id,
            log_path=str(result_path),
            extra={
                "usage_incomplete": bridge.usage_incomplete,
                "usage_recorded": bridge.usage_recorded,
            },
        )
        result["ok"] = True
    else:
        result = make_error_payload(
            error_reason or "failed",
            mode=meta.mode,
            summary=summary,
            run_id=meta.run_id or meta.job_id,
            job_id=meta.job_id,
            extra={
                "status": status,
                "exit_code": code,
                "usage": bridge.payload_usage,
                "usage_recorded": bridge.usage_recorded,
                "artifacts": list(harvest.get("artifacts") or []),
            },
        )
        result["status"] = status
        result["ok"] = False

    write_result(paths, job_id, result)
    updated = update_job_status(
        paths,
        job_id,
        status,
        error_reason=error_reason,
        exit_code=code,
        timed_out=timed_out or None,
    )
    shred_tokens(run_dir)
    return updated, result


class InstrumentReaper:
    """Daemon thread: poll running jobs → finalize → background wake.

    Construct only with an injected shared ``wake_queue``. Passing None or
    omitting it is a hard error — never falls back to ``WakeQueue(paths)``.
    """

    def __init__(
        self,
        *,
        paths: ElyraPaths,
        wake_queue: WakeQueue,
        stop_event: threading.Event | None = None,
        meter: UsageMeter | None = None,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        meter_factory: Callable[[], UsageMeter | None] | None = None,
    ) -> None:
        if wake_queue is None:
            raise TypeError(
                "wake_queue is required: pass the supervisor shared WakeQueue "
                "(never construct a private WakeQueue inside InstrumentReaper)"
            )
        if not isinstance(wake_queue, WakeQueue):
            raise TypeError(
                f"wake_queue must be a WakeQueue instance, got {type(wake_queue)!r}"
            )
        self.paths = paths
        self._wake_queue = wake_queue
        self._stop = stop_event if stop_event is not None else threading.Event()
        self._meter = meter
        self._meter_factory = meter_factory
        self._poll_interval_s = max(0.05, float(poll_interval_s))
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # job_ids we already enqueued a completion wake for (this process).
        self._woken: set[str] = set()

    @property
    def wake_queue(self) -> WakeQueue:
        """The injected shared queue (same object as PresenceWorker)."""
        return self._wake_queue

    @property
    def meter(self) -> UsageMeter | None:
        if self._meter is not None:
            return self._meter
        if self._meter_factory is not None:
            try:
                return self._meter_factory()
            except Exception:  # noqa: BLE001
                return None
        return None

    def start(self) -> None:
        """Start daemon reaper thread (idempotent). Runs startup GC first."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        # Startup GC before the loop so dead jobs from prior PE life are marked.
        try:
            marked = gc_interrupted(self.paths)
            for meta in marked:
                self._enqueue_completion(meta, status=JOB_STATUS_INTERRUPTED)
        except Exception:  # noqa: BLE001
            logger.exception("instrument reaper startup GC failed")
        self._thread = threading.Thread(
            target=self._run,
            name="elyra-instrument-reaper",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, join_timeout_s: float = DEFAULT_JOIN_TIMEOUT_S) -> None:
        """Signal stop and join the reaper thread (best-effort)."""
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=float(join_timeout_s))
        self._thread = None

    def poll_once(self) -> list[str]:
        """Single poll iteration (for tests / manual drive). Returns finalized ids."""
        return self._poll_running()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_running()
            except Exception:  # noqa: BLE001
                logger.exception("instrument reaper poll failed")
            if self._stop.wait(timeout=self._poll_interval_s):
                break

    def _poll_running(self) -> list[str]:
        finalized: list[str] = []
        for meta in list_jobs(self.paths, status=JOB_STATUS_RUNNING):
            if self._stop.is_set():
                break
            try:
                did = self._handle_running(meta)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "instrument reaper handle failed job_id=%s", meta.job_id
                )
                continue
            if did:
                finalized.append(meta.job_id)
        return finalized

    def _handle_running(self, meta: JobMeta) -> bool:
        """Return True if job was finalized this call."""
        # Wall timeout: kill process group then finalize as timeout.
        if self._is_overdue(meta):
            _kill_pgid(meta.pgid, meta.pid)
            updated, _result = finalize_job(
                self.paths,
                meta.job_id,
                meter=self.meter,
                status_override=JOB_STATUS_FAILED,
                error_reason="timeout",
                timed_out=True,
                exit_code=-9,
            )
            self._enqueue_completion(updated, status=updated.status)
            return True

        alive = is_pid_alive(meta.pid)
        if alive:
            return False

        # Dead pid (or never had one) — finalize.
        # pid None with running: treat as incomplete until GC stale, but if
        # result already written (sync finalize race), just mark + wake.
        existing = load_result(self.paths, meta.job_id)
        if existing is not None:
            status = str(existing.get("status") or JOB_STATUS_COMPLETED)
            if status not in TERMINAL_STATUSES:
                status = (
                    JOB_STATUS_COMPLETED
                    if existing.get("ok")
                    else JOB_STATUS_FAILED
                )
            updated = update_job_status(
                self.paths,
                meta.job_id,
                status,
                error_reason=existing.get("error_reason"),
                exit_code=existing.get("exit_code"),
            )
            shred_tokens(run_dir_for(self.paths, meta.job_id))
            self._enqueue_completion(updated, status=status)
            return True

        if meta.pid is None:
            # No process recorded yet — leave for GC stale path unless
            # stdout already has content (spawn race).
            stdout = read_log(self.paths, meta.job_id, STDOUT_NAME)
            if not stdout.strip():
                return False

        updated, _result = finalize_job(
            self.paths,
            meta.job_id,
            meter=self.meter,
            exit_code=meta.exit_code,
        )
        self._enqueue_completion(updated, status=updated.status)
        return True

    def _is_overdue(self, meta: JobMeta) -> bool:
        if meta.timeout_s is None or meta.timeout_s <= 0:
            return False
        started = _parse_iso(meta.started_at)
        if started is None:
            return False
        age = (datetime.now(UTC) - started.astimezone(UTC)).total_seconds()
        return age >= float(meta.timeout_s)

    def _enqueue_completion(self, meta: JobMeta, *, status: str) -> None:
        """Enqueue kind=background wake; idempotent per job_id in this process."""
        with self._lock:
            if meta.job_id in self._woken:
                return
            self._woken.add(meta.job_id)
        summary_path = str(run_dir_for(self.paths, meta.job_id) / RESULT_NAME)
        payload = build_completion_payload(
            meta, status=status, summary_path=summary_path
        )
        try:
            # CRITICAL: use injected shared queue only.
            item = self._wake_queue.enqueue(COMPLETION_WAKE_KIND, payload)
            logger.info(
                "instrument completion wake job_id=%s status=%s wake_id=%s",
                meta.job_id,
                status,
                item.id,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "failed to enqueue completion wake job_id=%s", meta.job_id
            )
            with self._lock:
                self._woken.discard(meta.job_id)


__all__ = [
    "COMPLETION_SOURCE",
    "COMPLETION_WAKE_KIND",
    "DEFAULT_JOIN_TIMEOUT_S",
    "DEFAULT_POLL_INTERVAL_S",
    "InstrumentReaper",
    "build_completion_payload",
    "finalize_job",
    "parse_headless_json",
]
