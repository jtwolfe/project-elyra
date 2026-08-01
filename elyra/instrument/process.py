"""Subprocess broker for host ``grok`` CLI (KD3 / KD9).

Scope: run/spawn only (shell=False), wall timeout, env merge (GROK_HOME),
process-group kill on hang, truncated stdout/stderr capture.
Out of scope: usage metering, skill logic, artifact harvest, OAuth refresh,
jobs/reaper.

PR review checklist: refuse PRs that stuff usage/skills/harvest into this module.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

# Cap captured streams so a runaway grok cannot blow memory (result layer trims further).
DEFAULT_CAPTURE_MAX_CHARS: int = 512_000

# Non-interactive backstop hints (auth hang mitigation; timeout still owns kill).
_DEFAULT_CHILD_ENV: dict[str, str] = {
    "CI": "1",
    "GROK_NO_BROWSER": "1",
    "GIT_TERMINAL_PROMPT": "0",
}


@dataclass(frozen=True)
class ProcessResult:
    """Outcome of a synchronous grok process run."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_ms: int = 0
    pid: int | None = None
    pgid: int | None = None


def truncate_capture(text: str, max_chars: int = DEFAULT_CAPTURE_MAX_CHARS) -> str:
    """Truncate captured stream; keep head and tail when oversized."""
    if text is None:
        return ""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    # Head-heavy: keep ~2/3 head + 1/3 tail with a marker.
    head_n = max_chars * 2 // 3
    tail_n = max_chars - head_n - 32
    if tail_n < 0:
        return text[:max_chars]
    marker = f"\n…[truncated {len(text) - head_n - tail_n} chars]…\n"
    return text[:head_n] + marker + text[-tail_n:]


def build_child_env(
    *,
    grok_home: Path | str,
    base: Mapping[str, str] | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build env for grok child: GROK_HOME + non-interactive defaults.

    Does **not** inject XAI_API_KEY or OAuth tokens — auth is via live
    auth_provider under the seeded home.
    """
    env: dict[str, str] = dict(base if base is not None else os.environ)
    env["GROK_HOME"] = str(Path(grok_home).resolve())
    for k, v in _DEFAULT_CHILD_ENV.items():
        env.setdefault(k, v)
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


def _kill_process_group(pid: int) -> None:
    """Kill the whole process group (auth hang backstop)."""
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        pass
    # Brief grace then SIGKILL the group.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, OSError):
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def run_grok(
    argv: Sequence[str],
    *,
    grok_home: Path | str,
    cwd: Path | str | None = None,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
    extra_env: Mapping[str, str] | None = None,
    capture_max_chars: int = DEFAULT_CAPTURE_MAX_CHARS,
    stdin_data: str | None = None,
) -> ProcessResult:
    """Run ``argv`` (typically grok -p …) with wall timeout and process-group kill.

    ``shell=False``. On timeout, kills the process **group** (not only the pid)
    so hung interactive auth children die with the broker.
    """
    if not argv:
        raise ValueError("argv must be non-empty")
    if timeout_s is None or float(timeout_s) <= 0:
        raise ValueError("timeout_s must be positive")

    child_env = build_child_env(
        grok_home=grok_home,
        base=env,
        extra=extra_env,
    )
    workdir = str(cwd) if cwd is not None else None
    t0 = time.monotonic()
    # start_new_session=True → child is session/process-group leader (pgid == pid).
    proc = subprocess.Popen(
        list(argv),
        cwd=workdir,
        env=child_env,
        stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        start_new_session=True,
    )
    pid = proc.pid
    try:
        pgid: int | None = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        pgid = pid

    timed_out = False
    try:
        stdout, stderr = proc.communicate(
            input=stdin_data,
            timeout=float(timeout_s),
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(pid)
        try:
            stdout, stderr = proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        except Exception:  # noqa: BLE001
            stdout, stderr = "", ""
    except Exception:  # noqa: BLE001
        _kill_process_group(pid)
        raise

    duration_ms = int((time.monotonic() - t0) * 1000)
    code = proc.returncode
    if code is None:
        code = -1
    if timed_out and code == 0:
        # Normalize: timeout should not look like success.
        code = -9

    return ProcessResult(
        exit_code=int(code),
        stdout=truncate_capture(stdout or "", capture_max_chars),
        stderr=truncate_capture(stderr or "", capture_max_chars),
        timed_out=timed_out,
        duration_ms=duration_ms,
        pid=pid,
        pgid=pgid,
    )


# Alias used by design text ("process.spawn" for async records pid — PR3).
# Blocking wait path; prefer run_grok for clarity.
spawn_and_wait = run_grok


@dataclass(frozen=True)
class SpawnedProcess:
    """Handle for a non-blocking grok child (async jobs / reaper ownership)."""

    pid: int
    pgid: int | None
    stdout_path: Path
    stderr_path: Path


def spawn_grok(
    argv: Sequence[str],
    *,
    grok_home: Path | str,
    cwd: Path | str | None = None,
    stdout_path: Path | str,
    stderr_path: Path | str,
    env: Mapping[str, str] | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> SpawnedProcess:
    """Spawn ``argv`` non-blocking; redirect stdout/stderr to files.

    ``shell=False``, new session (process-group leader). Caller records pid/pgid
    in job meta; reaper waits/finalizes. Does not inject OAuth tokens.
    """
    if not argv:
        raise ValueError("argv must be non-empty")

    child_env = build_child_env(
        grok_home=grok_home,
        base=env,
        extra=extra_env,
    )
    workdir = str(cwd) if cwd is not None else None
    out_p = Path(stdout_path)
    err_p = Path(stderr_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    err_p.parent.mkdir(parents=True, exist_ok=True)

    # Open files owned by child; parent closes after Popen.
    out_fh = open(out_p, "w", encoding="utf-8")  # noqa: SIM115
    err_fh = open(err_p, "w", encoding="utf-8")  # noqa: SIM115
    try:
        proc = subprocess.Popen(
            list(argv),
            cwd=workdir,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=out_fh,
            stderr=err_fh,
            text=True,
            shell=False,
            start_new_session=True,
        )
    finally:
        # Child has its own FDs; close parent copies.
        try:
            out_fh.close()
        except OSError:
            pass
        try:
            err_fh.close()
        except OSError:
            pass

    pid = proc.pid
    try:
        pgid: int | None = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        pgid = pid

    return SpawnedProcess(
        pid=pid,
        pgid=pgid,
        stdout_path=out_p,
        stderr_path=err_p,
    )


__all__ = [
    "DEFAULT_CAPTURE_MAX_CHARS",
    "ProcessResult",
    "SpawnedProcess",
    "build_child_env",
    "run_grok",
    "spawn_and_wait",
    "spawn_grok",
    "truncate_capture",
]
