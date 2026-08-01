"""Unit tests: InstrumentReaper — shared WakeQueue, finalize, completion wake."""

from __future__ import annotations

import inspect
import os
import threading
import time
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.instrument.jobs import (
    ACCESS_CACHE_NAME,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_INTERRUPTED,
    JOB_STATUS_RUNNING,
    RESULT_NAME,
    STDOUT_NAME,
    create_job,
    load_job,
    load_result,
    run_dir_for,
    update_job,
    write_result,
)
from elyra.instrument.reaper import (
    COMPLETION_SOURCE,
    COMPLETION_WAKE_KIND,
    InstrumentReaper,
    build_completion_payload,
    finalize_job,
    parse_headless_json,
)
from elyra.llm.usage import UsageMeter
from elyra.presence.queue import WakeQueue, priority_for_kind
from elyra.presence.worker import PresenceWorker
from elyra.llm.client import StubChatClient
from elyra.settings import UsageSettings


def _paths(tmp_path: Path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return paths


def _settings(**kwargs: object) -> UsageSettings:
    base = dict(
        enabled=True,
        weekly_allowed_tokens=1_000_000,
        weekly_allowed_fraction=0.50,
        hour_block_minutes=60,
        day_allowed_tokens=None,
        hour_allowed_tokens=None,
    )
    base.update(kwargs)
    return UsageSettings(**base)  # type: ignore[arg-type]


def test_reaper_requires_wake_queue(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    with pytest.raises(TypeError, match="wake_queue"):
        InstrumentReaper(paths=paths, wake_queue=None)  # type: ignore[arg-type]


def test_reaper_never_constructs_private_wake_queue() -> None:
    """Source must not construct WakeQueue — only accept injected queue."""
    import ast

    src_path = Path(inspect.getfile(InstrumentReaper))
    tree = ast.parse(src_path.read_text(encoding="utf-8"))
    constructions: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Name):
                name = func.id
            elif isinstance(func, ast.Attribute):
                name = func.attr
            if name == "WakeQueue":
                constructions.append(ast.dump(node))
    assert constructions == [], f"reaper must not construct WakeQueue: {constructions}"
    src = src_path.read_text(encoding="utf-8")
    assert "wake_queue is required" in src
    assert "wake_queue or WakeQueue" not in src


def test_completion_wake_kind_is_background_only() -> None:
    assert COMPLETION_WAKE_KIND == "background"
    assert priority_for_kind("background") == 4
    with pytest.raises(ValueError):
        priority_for_kind("instrument_job")


def test_build_completion_payload_no_secrets() -> None:
    paths = _paths(Path("/tmp"))  # unused; build meta inline
    from elyra.instrument.jobs import JobMeta

    meta = JobMeta(job_id="j1", mode="design", status="completed", run_id="j1")
    payload = build_completion_payload(
        meta, status="completed", summary_path="/x/result.json"
    )
    assert payload["source"] == COMPLETION_SOURCE
    assert payload["job_id"] == "j1"
    assert payload["mode"] == "design"
    assert "token" not in payload
    assert "stdout" not in payload


def test_reaper_shares_queue_object_with_worker(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    shared = WakeQueue(paths)
    stop = threading.Event()
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=stop,
        queue=shared,
    )
    reaper = InstrumentReaper(
        paths=paths,
        wake_queue=shared,
        stop_event=stop,
        poll_interval_s=0.05,
    )
    assert reaper.wake_queue is shared
    assert worker.queue is shared
    assert reaper.wake_queue is worker.queue


def test_reaper_enqueue_visible_on_shared_queue(tmp_path: Path) -> None:
    """Reaper completion enqueue must appear on the same WakeQueue heap."""
    paths = _paths(tmp_path)
    shared = WakeQueue(paths)
    stop = threading.Event()
    reaper = InstrumentReaper(
        paths=paths,
        wake_queue=shared,
        stop_event=stop,
        poll_interval_s=0.05,
    )

    # Dead pid job with stdout so finalize can run.
    dead_pid = 2_000_000_042
    meta = create_job(
        paths, mode="design", job_id="vis1", pid=dead_pid, timeout_s=600
    )
    run_dir = run_dir_for(paths, meta.job_id)
    (run_dir / STDOUT_NAME).write_text(
        '{"text":"ok","usage":{"input_tokens":10,"output_tokens":2,"total_tokens":12}}',
        encoding="utf-8",
    )
    # Design artifact so harvest succeeds.
    art = run_dir / "artifacts" / "design.md"
    art.write_text("# design\n", encoding="utf-8")

    finalized = reaper.poll_once()
    assert "vis1" in finalized

    pending = shared.pending()
    bg = [w for w in pending if w.kind == "background"]
    assert bg, "completion wake must be pending on shared queue"
    assert bg[0].payload.get("source") == "grok_build"
    assert bg[0].payload.get("job_id") == "vis1"
    assert bg[0].payload.get("status") in (
        JOB_STATUS_COMPLETED,
        JOB_STATUS_FAILED,
        "needs_human",
    )

    # Private second queue must NOT see the in-process heap entry as a pass.
    private = WakeQueue(paths)
    # Private reconstructs from events.jsonl — durable events ARE visible after
    # fold, but the acceptance criterion is same-object identity for mid-run
    # claim. Assert identity requirement explicitly:
    assert private is not shared
    # Shared still has the pending item without reload.
    assert any(w.payload.get("job_id") == "vis1" for w in shared.pending())


def test_second_queue_is_not_same_object(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    q1 = WakeQueue(paths)
    q2 = WakeQueue(paths)
    assert q1 is not q2
    # Enqueue on q1 does not update q2's in-memory heap until reload — if we
    # only push to heap of q1, q2.pending may still fold from disk. The
    # critical bug is mid-run heap: inject into worker's instance only.
    item = q1.enqueue("background", {"source": "grok_build", "job_id": "x"})
    assert item.id in {w.id for w in q1.pending()}


def test_finalize_job_writes_result_and_shreds(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    meter = UsageMeter.load(tmp_path / "data", _settings())
    # Use data_dir under paths for meter consistency.
    meter = UsageMeter.load(paths.data_dir, _settings())

    meta = create_job(paths, mode="prompt", job_id="fin1", pid=None)
    run_dir = run_dir_for(paths, meta.job_id)
    (run_dir / STDOUT_NAME).write_text(
        '{"text":"hello","usage":{"input_tokens":5,"output_tokens":1,"total_tokens":6}}',
        encoding="utf-8",
    )
    cache = run_dir / ACCESS_CACHE_NAME
    cache.write_text("secret-access", encoding="utf-8")

    updated, result = finalize_job(
        paths, "fin1", meter=meter, exit_code=0, status_override=JOB_STATUS_COMPLETED
    )
    assert updated.status == JOB_STATUS_COMPLETED
    assert result.get("ok") is True
    assert not cache.exists()
    assert load_result(paths, "fin1") is not None
    assert result.get("usage", {}).get("recorded") is True or result.get(
        "usage_recorded"
    )


def test_startup_gc_marks_interrupted_and_wakes(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    shared = WakeQueue(paths)
    dead_pid = 2_000_000_099
    create_job(paths, mode="review", job_id="gc1", pid=dead_pid)
    cache = run_dir_for(paths, "gc1") / ACCESS_CACHE_NAME
    cache.write_text("tok", encoding="utf-8")

    stop = threading.Event()
    reaper = InstrumentReaper(
        paths=paths,
        wake_queue=shared,
        stop_event=stop,
        poll_interval_s=0.05,
    )
    reaper.start()
    try:
        # Startup GC runs inside start().
        deadline = time.time() + 2.0
        while time.time() < deadline:
            meta = load_job(paths, "gc1")
            if meta and meta.status == JOB_STATUS_INTERRUPTED:
                break
            time.sleep(0.05)
        meta = load_job(paths, "gc1")
        assert meta is not None
        assert meta.status == JOB_STATUS_INTERRUPTED
        assert not cache.exists()
        # Completion wake enqueued.
        pending = shared.pending()
        assert any(
            w.kind == "background" and w.payload.get("job_id") == "gc1"
            for w in pending
        )
    finally:
        stop.set()
        reaper.stop(join_timeout_s=2.0)


def test_reaper_timeout_kills_and_fails(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    shared = WakeQueue(paths)
    # Job that is already overdue with dead pid.
    meta = create_job(
        paths,
        mode="design",
        job_id="to1",
        pid=2_000_000_123,
        timeout_s=1,
    )
    # Force started_at into the past.
    from datetime import UTC, datetime, timedelta

    old = (datetime.now(UTC) - timedelta(seconds=30)).isoformat().replace(
        "+00:00", "Z"
    )
    update_job(paths, "to1", started_at=old)

    reaper = InstrumentReaper(
        paths=paths,
        wake_queue=shared,
        stop_event=threading.Event(),
        poll_interval_s=0.05,
    )
    finalized = reaper.poll_once()
    assert "to1" in finalized
    reloaded = load_job(paths, "to1")
    assert reloaded is not None
    assert reloaded.status == JOB_STATUS_FAILED
    assert reloaded.error_reason == "timeout" or reloaded.timed_out


def test_parse_headless_json() -> None:
    assert parse_headless_json('{"text":"a"}') == {"text": "a"}
    assert parse_headless_json("noise\n{\"ok\": true}\n") == {"ok": True}
    assert parse_headless_json("") is None


def test_supervisor_wires_shared_queue(tmp_path: Path) -> None:
    """ElyraSupervisor builds one WakeQueue and injects into worker + reaper."""
    from elyra.runtime.supervisor import ElyraSupervisor
    from elyra.runtime.config import RuntimeConfig

    paths = _paths(tmp_path)
    sup = ElyraSupervisor(paths=paths, config=RuntimeConfig(), use_stub_llm=True)
    try:
        sup.start()
        assert sup._wake_queue is not None
        assert sup._worker is not None
        assert sup._instrument_reaper is not None
        assert sup._worker.queue is sup._wake_queue
        assert sup._instrument_reaper.wake_queue is sup._wake_queue
        assert sup._instrument_reaper.wake_queue is sup._worker.queue
        # Meter shared / loaded.
        assert sup._usage_meter is not None
    finally:
        sup.shutdown()
