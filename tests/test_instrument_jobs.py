"""Unit tests: elyra.instrument.jobs — CRUD, runtime helper, GC, shred."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.instrument.jobs import (
    ACCESS_CACHE_NAME,
    ARTIFACTS_DIR_NAME,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_RUNNING,
    LOG_RETENTION_DAYS,
    MAX_RUNS,
    META_NAME,
    RESULT_NAME,
    STALE_TOKEN_SHRED_MINUTES,
    create_job,
    ensure_grok_build_runtime,
    gc_interrupted,
    is_pid_alive,
    list_jobs,
    load_job,
    load_result,
    reap_instrument_pid,
    run_dir_for,
    shred_tokens,
    update_job,
    update_job_status,
    write_result,
)


def _paths(tmp_path: Path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return paths


def test_ensure_grok_build_runtime_mkdir_0700(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    root = ensure_grok_build_runtime(paths)
    assert root.is_dir()
    assert root == (paths.data_dir / "runtime" / "grok_build").resolve()
    mode = root.stat().st_mode & 0o777
    # Platform may mask bits; require owner rwx and no group/other write.
    assert mode & 0o700 == 0o700


def test_create_load_update_job(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    meta = create_job(
        paths,
        mode="design",
        job_id="jobabc",
        pid=None,
        timeout_s=90 * 60,
        base_branch="working",
        cwd=str(tmp_path),
    )
    assert meta.job_id == "jobabc"
    assert meta.run_id == "jobabc"
    assert meta.status == JOB_STATUS_RUNNING
    assert meta.mode == "design"
    assert meta.base_branch == "working"
    assert meta.async_job is True

    run_dir = run_dir_for(paths, "jobabc")
    assert (run_dir / META_NAME).is_file()
    assert (run_dir / ARTIFACTS_DIR_NAME).is_dir()
    raw = json.loads((run_dir / META_NAME).read_text(encoding="utf-8"))
    assert raw["async"] is True
    assert raw["mode"] == "design"

    loaded = load_job(paths, "jobabc")
    assert loaded is not None
    assert loaded.job_id == "jobabc"
    assert loaded.timeout_s == 90 * 60

    updated = update_job(paths, "jobabc", pid=12345, pgid=12345)
    assert updated.pid == 12345
    assert load_job(paths, "jobabc").pid == 12345

    done = update_job_status(
        paths, "jobabc", JOB_STATUS_COMPLETED, exit_code=0
    )
    assert done.status == JOB_STATUS_COMPLETED
    assert done.exit_code == 0
    assert done.ended_at is not None


def test_create_job_duplicate_raises(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    create_job(paths, mode="prompt", job_id="dup1")
    with pytest.raises(FileExistsError):
        create_job(paths, mode="prompt", job_id="dup1")


def test_load_job_missing_and_invalid_id(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    assert load_job(paths, "nope") is None
    with pytest.raises(ValueError):
        run_dir_for(paths, "../escape")
    with pytest.raises(ValueError):
        run_dir_for(paths, "a/b")


def test_write_load_result(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    create_job(paths, mode="review", job_id="r1")
    path = write_result(
        paths,
        "r1",
        {"ok": True, "status": "completed", "summary": "hi"},
    )
    assert path.name == RESULT_NAME
    result = load_result(paths, "r1")
    assert result is not None
    assert result["ok"] is True
    assert result["summary"] == "hi"


def test_list_jobs_filter_status(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    create_job(paths, mode="design", job_id="a")
    create_job(paths, mode="implement", job_id="b")
    update_job_status(paths, "b", JOB_STATUS_COMPLETED, exit_code=0)
    running = list_jobs(paths, status=JOB_STATUS_RUNNING)
    assert {j.job_id for j in running} == {"a"}
    all_jobs = list_jobs(paths)
    assert {j.job_id for j in all_jobs} == {"a", "b"}


def test_shred_tokens_removes_access_cache(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    meta = create_job(paths, mode="design", job_id="tok1")
    run_dir = run_dir_for(paths, meta.job_id)
    cache = run_dir / ACCESS_CACHE_NAME
    cache.write_text("super-secret-token-value", encoding="utf-8")
    assert cache.is_file()
    shredded = shred_tokens(run_dir)
    assert any(ACCESS_CACHE_NAME in p for p in shredded)
    assert not cache.exists()


def test_gc_interrupted_dead_pid(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    # Use a pid that is almost certainly dead.
    dead_pid = 2_000_000_001
    while is_pid_alive(dead_pid):
        dead_pid += 1
    meta = create_job(
        paths, mode="design", job_id="dead1", pid=dead_pid, pgid=dead_pid
    )
    run_dir = run_dir_for(paths, meta.job_id)
    cache = run_dir / ACCESS_CACHE_NAME
    cache.write_text("token", encoding="utf-8")

    marked = gc_interrupted(paths)
    assert any(j.job_id == "dead1" for j in marked)
    reloaded = load_job(paths, "dead1")
    assert reloaded is not None
    assert reloaded.status == JOB_STATUS_INTERRUPTED
    assert not cache.exists()
    result = load_result(paths, "dead1")
    assert result is not None
    assert result["error_reason"] == "interrupted"


def test_gc_interrupted_stale_incomplete(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    # Live pid (this process) but started long ago → stale.
    meta = create_job(
        paths,
        mode="implement",
        job_id="stale1",
        pid=os.getpid(),
        pgid=os.getpid(),
    )
    old = (datetime.now(UTC) - timedelta(minutes=STALE_TOKEN_SHRED_MINUTES + 5)).isoformat()
    if old.endswith("+00:00"):
        old = old[:-6] + "Z"
    update_job(paths, "stale1", started_at=old)

    marked = gc_interrupted(paths, stale_minutes=STALE_TOKEN_SHRED_MINUTES)
    assert any(j.job_id == "stale1" for j in marked)
    assert load_job(paths, "stale1").status == JOB_STATUS_INTERRUPTED


def test_gc_skips_live_fresh_job(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    create_job(
        paths,
        mode="design",
        job_id="live1",
        pid=os.getpid(),
        timeout_s=3600,
    )
    marked = gc_interrupted(paths, stale_minutes=60)
    assert not any(j.job_id == "live1" for j in marked)
    assert load_job(paths, "live1").status == JOB_STATUS_RUNNING


def test_retention_constants() -> None:
    assert LOG_RETENTION_DAYS == 14
    assert MAX_RUNS == 50
    assert STALE_TOKEN_SHRED_MINUTES == 30


def test_is_pid_alive_self() -> None:
    assert is_pid_alive(os.getpid()) is True
    assert is_pid_alive(None) is False
    assert is_pid_alive(-1) is False
    assert is_pid_alive(0) is False


def test_is_pid_alive_missing_pid() -> None:
    dead_pid = 2_000_000_777
    while is_pid_alive(dead_pid):
        dead_pid += 1
    assert is_pid_alive(dead_pid) is False


def test_is_pid_alive_zombie_false(tmp_path: Path) -> None:
    """Synthetic zombie: child exits, parent has not waitpid'd → state Z → dead."""
    # Use os.fork for a true unreaped zombie under this process.
    if not hasattr(os, "fork"):
        pytest.skip("os.fork required for zombie synthesis")
    child_pid = os.fork()
    if child_pid == 0:
        # Child: exit immediately.
        os._exit(0)
    # Parent: do NOT wait yet — child becomes zombie.
    # Brief spin until /proc shows Z or gone.
    deadline = time.time() + 2.0
    saw_zombie = False
    while time.time() < deadline:
        try:
            with open(f"/proc/{child_pid}/stat", encoding="utf-8") as fh:
                raw = fh.read()
            state = raw.split(")", 1)[1].split()[0]
            if state == "Z":
                saw_zombie = True
                break
        except (FileNotFoundError, OSError, IndexError):
            break
        time.sleep(0.01)
    try:
        if saw_zombie:
            assert is_pid_alive(child_pid) is False
        # Reap and assert exit code + /proc gone.
        code = reap_instrument_pid(child_pid)
        assert code == 0
        # After reaping, /proc should be gone.
        deadline = time.time() + 1.0
        while time.time() < deadline and Path(f"/proc/{child_pid}").exists():
            time.sleep(0.01)
        assert not Path(f"/proc/{child_pid}").exists()
        assert is_pid_alive(child_pid) is False
    finally:
        # Safety reap if test failed mid-way.
        try:
            os.waitpid(child_pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            pass


def test_reap_instrument_pid_invalid() -> None:
    assert reap_instrument_pid(None) is None
    assert reap_instrument_pid(-1) is None
    assert reap_instrument_pid(0) is None
    # Not our child / never existed → ECHILD or no-op → None
    assert reap_instrument_pid(2_000_000_888) is None
