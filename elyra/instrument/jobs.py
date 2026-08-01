"""Durable grok_build job meta under data/runtime/grok_build/<id>/.

Scope: ensure_grok_build_runtime, create/load/update job status, run_dir layout,
token shred, startup interrupted GC for dead/stale running jobs.
In scope: meta.json + result.json I/O, pid liveness probe, retention constants.
Out of scope: subprocess spawn, wake enqueue, usage metering, presence worker.

Retention: LOG_RETENTION_DAYS, MAX_RUNS, STALE_TOKEN_SHRED_MINUTES live here
(single owner of mkdir + GC policy — design KD11 / runtime layout).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from elyra.config import ElyraPaths
from elyra.instrument.result import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_INTERRUPTED,
    STATUS_NEEDS_HUMAN,
    STATUS_RUNNING,
)

logger = logging.getLogger(__name__)

# Runtime layout (relative to data_dir).
GROK_BUILD_RUNTIME_REL = Path("runtime") / "grok_build"

# Retention / GC (design: live next to ensure_grok_build_runtime).
LOG_RETENTION_DAYS: int = 14
MAX_RUNS: int = 50
STALE_TOKEN_SHRED_MINUTES: int = 30

META_NAME = "meta.json"
RESULT_NAME = "result.json"
STDOUT_NAME = "stdout.log"
STDERR_NAME = "stderr.log"
ARTIFACTS_DIR_NAME = "artifacts"
ACCESS_CACHE_NAME = ".access_cache"
GROK_HOME_NAME = "grok_home"

# Status strings shared with result.py (re-export for callers).
JOB_STATUS_RUNNING = STATUS_RUNNING
JOB_STATUS_COMPLETED = STATUS_COMPLETED
JOB_STATUS_FAILED = STATUS_FAILED
JOB_STATUS_NEEDS_HUMAN = STATUS_NEEDS_HUMAN
JOB_STATUS_INTERRUPTED = STATUS_INTERRUPTED

TERMINAL_STATUSES = frozenset(
    {
        JOB_STATUS_COMPLETED,
        JOB_STATUS_FAILED,
        JOB_STATUS_NEEDS_HUMAN,
        JOB_STATUS_INTERRUPTED,
    }
)

_meta_lock = threading.RLock()


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


def ensure_grok_build_runtime(paths: ElyraPaths) -> Path:
    """Create ``data/runtime/grok_build`` at mode 0700; return absolute path.

    Single owner of this mkdir — callers must not scatter ad-hoc creates.
    """
    root = Path(paths.data_dir) / GROK_BUILD_RUNTIME_REL
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root.resolve()


def run_dir_for(paths: ElyraPaths, job_id: str) -> Path:
    """Absolute path of ``data/runtime/grok_build/<job_id>/`` (may not exist)."""
    if not job_id or not isinstance(job_id, str):
        raise ValueError("job_id must be a non-empty string")
    # Refuse path traversal in job_id.
    if Path(job_id).name != job_id or ".." in job_id or "/" in job_id or "\\" in job_id:
        raise ValueError(f"invalid job_id: {job_id!r}")
    return ensure_grok_build_runtime(paths) / job_id


def is_pid_alive(pid: int | None) -> bool:
    """True if ``pid`` is a positive int and the process exists (signal 0)."""
    if pid is None:
        return False
    try:
        p = int(pid)
    except (TypeError, ValueError):
        return False
    if p <= 0:
        return False
    try:
        os.kill(p, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but we cannot signal it — treat as alive.
        return True
    except OSError:
        return False
    return True


def shred_path(path: Path | str) -> bool:
    """Best-effort overwrite + unlink a secret file. Returns True if gone."""
    p = Path(path)
    try:
        if not p.is_file():
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    return not p.exists()
            return True
        size = p.stat().st_size
        # Overwrite with zeros (best-effort; not cryptographic wipe).
        with open(p, "r+b", buffering=0) as fh:
            fh.write(b"\x00" * max(size, 1))
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        p.unlink(missing_ok=True)
        return not p.exists()
    except OSError as exc:
        logger.debug("shred_path failed path=%s err=%s", p, exc)
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
        return not p.exists()


def shred_tokens(run_dir: Path | str) -> list[str]:
    """Shred known token cache files under a run_dir. Returns shredded paths."""
    root = Path(run_dir)
    shredded: list[str] = []
    candidates = [
        root / ACCESS_CACHE_NAME,
        root / GROK_HOME_NAME / "auth.json",
        root / GROK_HOME_NAME / ".access_cache",
    ]
    # Also shred any .access_cache* siblings under run_dir.
    try:
        if root.is_dir():
            for child in root.iterdir():
                if child.name.startswith(".access") or child.name.endswith(
                    "_token"
                ):
                    candidates.append(child)
    except OSError:
        pass
    seen: set[str] = set()
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        if c.exists() or c.is_symlink():
            if shred_path(c):
                shredded.append(key)
    return shredded


@dataclass
class JobMeta:
    """Durable job record (no secrets)."""

    job_id: str
    mode: str
    status: str = JOB_STATUS_RUNNING
    run_id: str | None = None
    pid: int | None = None
    pgid: int | None = None
    async_job: bool = True
    base_branch: str | None = None
    cwd: str | None = None
    timeout_s: float | None = None
    started_at: str | None = None
    ended_at: str | None = None
    exit_code: int | None = None
    error_reason: str | None = None
    timed_out: bool = False
    argv: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Keep wire name ``async`` for meta.json (Python attr is async_job).
        d["async"] = d.pop("async_job")
        # Drop empty extra to keep meta lean.
        if not d.get("extra"):
            d.pop("extra", None)
        if not d.get("argv"):
            d.pop("argv", None)
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> JobMeta:
        raw = dict(data)
        job_id = str(raw.get("job_id") or raw.get("run_id") or "")
        if not job_id:
            raise ValueError("job meta missing job_id")
        mode = str(raw.get("mode") or "prompt")
        status = str(raw.get("status") or JOB_STATUS_RUNNING)
        pid = raw.get("pid")
        pgid = raw.get("pgid")
        try:
            pid_i = int(pid) if pid is not None else None
        except (TypeError, ValueError):
            pid_i = None
        try:
            pgid_i = int(pgid) if pgid is not None else None
        except (TypeError, ValueError):
            pgid_i = None
        timeout = raw.get("timeout_s")
        try:
            timeout_f = float(timeout) if timeout is not None else None
        except (TypeError, ValueError):
            timeout_f = None
        exit_code = raw.get("exit_code")
        try:
            exit_i = int(exit_code) if exit_code is not None else None
        except (TypeError, ValueError):
            exit_i = None
        async_job = bool(raw.get("async", raw.get("async_job", True)))
        argv_raw = raw.get("argv") or []
        argv = [str(a) for a in argv_raw] if isinstance(argv_raw, list) else []
        extra = raw.get("extra") if isinstance(raw.get("extra"), dict) else {}
        # Preserve unknown top-level keys in extra for forward compat.
        known = {
            "job_id",
            "run_id",
            "mode",
            "status",
            "pid",
            "pgid",
            "async",
            "async_job",
            "base_branch",
            "cwd",
            "timeout_s",
            "started_at",
            "ended_at",
            "exit_code",
            "error_reason",
            "timed_out",
            "argv",
            "extra",
        }
        for k, v in raw.items():
            if k not in known and k not in extra:
                extra[k] = v
        return cls(
            job_id=job_id,
            mode=mode,
            status=status,
            run_id=str(raw["run_id"]) if raw.get("run_id") else job_id,
            pid=pid_i,
            pgid=pgid_i,
            async_job=async_job,
            base_branch=str(raw["base_branch"]) if raw.get("base_branch") else None,
            cwd=str(raw["cwd"]) if raw.get("cwd") else None,
            timeout_s=timeout_f,
            started_at=str(raw["started_at"]) if raw.get("started_at") else None,
            ended_at=str(raw["ended_at"]) if raw.get("ended_at") else None,
            exit_code=exit_i,
            error_reason=str(raw["error_reason"]) if raw.get("error_reason") else None,
            timed_out=bool(raw.get("timed_out", False)),
            argv=argv,
            extra=dict(extra or {}),
        )


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(
            json.dumps(dict(data), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def create_job(
    paths: ElyraPaths,
    *,
    mode: str,
    job_id: str | None = None,
    pid: int | None = None,
    pgid: int | None = None,
    async_job: bool = True,
    base_branch: str | None = None,
    cwd: str | None = None,
    timeout_s: float | None = None,
    argv: list[str] | None = None,
    extra: Mapping[str, Any] | None = None,
    status: str = JOB_STATUS_RUNNING,
) -> JobMeta:
    """Create run_dir (0700) + meta.json; return JobMeta.

    ``job_id`` defaults to a new uuid4 hex. ``run_id`` equals ``job_id`` in v1.
    """
    jid = (job_id or uuid.uuid4().hex).strip()
    if not jid:
        raise ValueError("job_id must be non-empty")
    run_dir = run_dir_for(paths, jid)
    if run_dir.exists() and (run_dir / META_NAME).is_file():
        raise FileExistsError(f"job already exists: {jid}")
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(run_dir, 0o700)
    except OSError:
        pass
    (run_dir / ARTIFACTS_DIR_NAME).mkdir(mode=0o700, parents=True, exist_ok=True)
    meta = JobMeta(
        job_id=jid,
        run_id=jid,
        mode=str(mode),
        status=status,
        pid=pid,
        pgid=pgid,
        async_job=bool(async_job),
        base_branch=base_branch,
        cwd=str(cwd) if cwd is not None else None,
        timeout_s=float(timeout_s) if timeout_s is not None else None,
        started_at=_now_iso(),
        argv=list(argv or []),
        extra=dict(extra or {}),
    )
    with _meta_lock:
        _atomic_write_json(run_dir / META_NAME, meta.to_dict())
    return meta


def load_job(paths: ElyraPaths, job_id: str) -> JobMeta | None:
    """Load job meta; None if missing / corrupt."""
    try:
        run_dir = run_dir_for(paths, job_id)
    except ValueError:
        return None
    meta_path = run_dir / META_NAME
    if not meta_path.is_file():
        return None
    try:
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("load_job corrupt job_id=%s err=%s", job_id, exc)
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return JobMeta.from_dict(raw)
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("load_job parse failed job_id=%s err=%s", job_id, exc)
        return None


def update_job(
    paths: ElyraPaths,
    job_id: str,
    **fields: Any,
) -> JobMeta:
    """Update meta fields (status, pid, exit_code, …); raises FileNotFoundError."""
    with _meta_lock:
        meta = load_job(paths, job_id)
        if meta is None:
            raise FileNotFoundError(f"job_not_found: {job_id}")
        for key, value in fields.items():
            if key == "async":
                meta.async_job = bool(value)
            elif key == "extra" and isinstance(value, Mapping):
                meta.extra.update(dict(value))
            elif hasattr(meta, key):
                setattr(meta, key, value)
            else:
                meta.extra[key] = value
        # Terminal statuses get ended_at if not set.
        if meta.status in TERMINAL_STATUSES and not meta.ended_at:
            meta.ended_at = _now_iso()
        _atomic_write_json(run_dir_for(paths, job_id) / META_NAME, meta.to_dict())
        return meta


def update_job_status(
    paths: ElyraPaths,
    job_id: str,
    status: str,
    *,
    error_reason: str | None = None,
    exit_code: int | None = None,
    timed_out: bool | None = None,
) -> JobMeta:
    """Convenience: set status (+ optional error/exit) and persist."""
    fields: dict[str, Any] = {"status": status}
    if error_reason is not None:
        fields["error_reason"] = error_reason
    if exit_code is not None:
        fields["exit_code"] = exit_code
    if timed_out is not None:
        fields["timed_out"] = timed_out
    if status in TERMINAL_STATUSES:
        fields["ended_at"] = _now_iso()
    return update_job(paths, job_id, **fields)


def list_jobs(
    paths: ElyraPaths,
    *,
    status: str | None = None,
) -> list[JobMeta]:
    """List jobs under the runtime root (best-effort; skips corrupt dirs)."""
    root = ensure_grok_build_runtime(paths)
    out: list[JobMeta] = []
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return []
    for child in children:
        if not child.is_dir():
            continue
        meta = load_job(paths, child.name)
        if meta is None:
            continue
        if status is not None and meta.status != status:
            continue
        out.append(meta)
    return out


def write_result(paths: ElyraPaths, job_id: str, result: Mapping[str, Any]) -> Path:
    """Write redacted result.json under the job run_dir; return path."""
    run_dir = run_dir_for(paths, job_id)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"job_not_found: {job_id}")
    path = run_dir / RESULT_NAME
    with _meta_lock:
        _atomic_write_json(path, result)
    return path


def load_result(paths: ElyraPaths, job_id: str) -> dict[str, Any] | None:
    """Load result.json if present."""
    try:
        path = run_dir_for(paths, job_id) / RESULT_NAME
    except ValueError:
        return None
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def read_log(paths: ElyraPaths, job_id: str, name: str = STDOUT_NAME) -> str:
    """Read a log file from the run_dir (empty string if missing)."""
    try:
        path = run_dir_for(paths, job_id) / name
    except ValueError:
        return ""
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return ""


def gc_interrupted(
    paths: ElyraPaths,
    *,
    stale_minutes: int | None = None,
    now: datetime | None = None,
) -> list[JobMeta]:
    """Mark incomplete/dead running jobs as interrupted and shred tokens.

    Rules (design startup GC):
    - ``status=running`` with dead (or missing) pid → interrupted
    - incomplete runs older than ``stale_minutes`` (default 30) → interrupted
    - always shred leftover token files for those jobs

    Returns the list of jobs marked interrupted.
    """
    threshold_m = (
        STALE_TOKEN_SHRED_MINUTES if stale_minutes is None else int(stale_minutes)
    )
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    marked: list[JobMeta] = []
    for meta in list_jobs(paths):
        if meta.status in TERMINAL_STATUSES:
            # Still shred stale token files on terminal incomplete? only if cache left.
            run_dir = run_dir_for(paths, meta.job_id)
            if (run_dir / ACCESS_CACHE_NAME).exists():
                shred_tokens(run_dir)
            continue
        # Non-terminal (running or unknown).
        dead = not is_pid_alive(meta.pid)
        stale = False
        started = _parse_iso(meta.started_at)
        if started is not None:
            age = clock - started.astimezone(UTC)
            if age >= timedelta(minutes=threshold_m):
                stale = True
        # Also honor wall timeout if set and exceeded (dead or not — reaper
        # kills; GC treats overdue dead as interrupted).
        overdue = False
        if started is not None and meta.timeout_s is not None and meta.timeout_s > 0:
            if clock - started.astimezone(UTC) >= timedelta(seconds=float(meta.timeout_s)):
                overdue = True
        if not (dead or stale or overdue):
            continue
        try:
            updated = update_job_status(
                paths,
                meta.job_id,
                JOB_STATUS_INTERRUPTED,
                error_reason=meta.error_reason or "interrupted",
                exit_code=meta.exit_code,
            )
        except FileNotFoundError:
            continue
        shred_tokens(run_dir_for(paths, meta.job_id))
        # Write a minimal result if none yet (durable poll source of truth).
        if load_result(paths, meta.job_id) is None:
            try:
                write_result(
                    paths,
                    meta.job_id,
                    {
                        "ok": False,
                        "error_reason": "interrupted",
                        "mode": updated.mode,
                        "run_id": updated.run_id or updated.job_id,
                        "job_id": updated.job_id,
                        "status": JOB_STATUS_INTERRUPTED,
                        "summary": "job interrupted (dead pid or stale incomplete)",
                    },
                )
            except FileNotFoundError:
                pass
        marked.append(updated)
        logger.info(
            "gc_interrupted job_id=%s dead=%s stale=%s overdue=%s",
            meta.job_id,
            dead,
            stale,
            overdue,
        )
    return marked


def prune_old_runs(
    paths: ElyraPaths,
    *,
    max_runs: int | None = None,
    retention_days: int | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Best-effort prune excess/old run dirs (terminal only). Returns removed ids."""
    cap = MAX_RUNS if max_runs is None else int(max_runs)
    days = LOG_RETENTION_DAYS if retention_days is None else int(retention_days)
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=UTC)
    jobs = list_jobs(paths)
    # Never prune non-terminal.
    terminal = [j for j in jobs if j.status in TERMINAL_STATUSES]
    removed: list[str] = []

    def _age_key(j: JobMeta) -> float:
        dt = _parse_iso(j.ended_at) or _parse_iso(j.started_at)
        if dt is None:
            return 0.0
        return dt.timestamp()

    # By retention age.
    for j in terminal:
        dt = _parse_iso(j.ended_at) or _parse_iso(j.started_at)
        if dt is None:
            continue
        if clock - dt.astimezone(UTC) >= timedelta(days=days):
            if _remove_run_dir(paths, j.job_id):
                removed.append(j.job_id)

    # By max count (oldest first).
    remaining = [j for j in list_jobs(paths) if j.status in TERMINAL_STATUSES]
    remaining.sort(key=_age_key)
    while len(remaining) > cap:
        victim = remaining.pop(0)
        if victim.job_id in removed:
            continue
        if _remove_run_dir(paths, victim.job_id):
            removed.append(victim.job_id)
    return removed


def _remove_run_dir(paths: ElyraPaths, job_id: str) -> bool:
    import shutil

    try:
        run_dir = run_dir_for(paths, job_id)
    except ValueError:
        return False
    shred_tokens(run_dir)
    try:
        if run_dir.is_dir():
            shutil.rmtree(run_dir, ignore_errors=True)
        return not run_dir.exists()
    except OSError:
        return False


__all__ = [
    "ACCESS_CACHE_NAME",
    "ARTIFACTS_DIR_NAME",
    "GROK_BUILD_RUNTIME_REL",
    "GROK_HOME_NAME",
    "JOB_STATUS_COMPLETED",
    "JOB_STATUS_FAILED",
    "JOB_STATUS_INTERRUPTED",
    "JOB_STATUS_NEEDS_HUMAN",
    "JOB_STATUS_RUNNING",
    "LOG_RETENTION_DAYS",
    "MAX_RUNS",
    "META_NAME",
    "RESULT_NAME",
    "STALE_TOKEN_SHRED_MINUTES",
    "STDERR_NAME",
    "STDOUT_NAME",
    "TERMINAL_STATUSES",
    "JobMeta",
    "create_job",
    "ensure_grok_build_runtime",
    "gc_interrupted",
    "is_pid_alive",
    "list_jobs",
    "load_job",
    "load_result",
    "prune_old_runs",
    "read_log",
    "run_dir_for",
    "shred_path",
    "shred_tokens",
    "update_job",
    "update_job_status",
    "write_result",
]
