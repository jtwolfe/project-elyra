"""Flag fallback tests: write_atoms / enabled can be disabled.

Default product: write_atoms=true, enabled=true. When write_atoms is false,
promote is a no-op (MomentStore only). Store open is skipped when both flags
are off. Promote I/O failures never change DoLoopResult.
"""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.llm.client import ChatCompletionResult, StubChatClient
from elyra.loop.doloop import DoLoopResult, _record_beat, run_do_loop
from elyra.memory.config import MemorySettings
from elyra.memory.promote import promote_beat, promote_wake_observation
from elyra.memory.store import open_memory_store
from elyra.moment import MomentStore
from elyra.presence.queue import WakeItem
from elyra.presence.worker import PresenceWorker, _media_ids_from_wake
from elyra.settings import default_settings
from elyra.tools import ToolContext, ToolRegistry
from elyra.tools.policy import resolve_bundled_tools_root


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


def test_defaults_write_atoms_and_enabled_on():
    s = default_settings()
    assert s.memory.write_atoms is True
    assert s.memory.enabled is True


def test_promote_noop_when_write_atoms_false(paths):
    store = open_memory_store(paths, MemorySettings(write_atoms=False))
    atom = promote_beat(
        store,
        "m1",
        {
            "type": "model",
            "content": "x" * 80,
            "ts": "2026-07-28T10:00:00Z",
        },
        settings=MemorySettings(write_atoms=False),
    )
    assert atom is None
    assert store.list_by_moment("m1") == []

    wake = promote_wake_observation(
        store,
        "m1",
        content="hello there",
        message_id="mid-1",
        settings=MemorySettings(write_atoms=False),
    )
    assert wake is None
    assert store.list_by_moment("m1") == []


def test_record_beat_legacy_only_when_write_atoms_false(paths):
    moments = MomentStore(paths)
    mid = moments.open_moment(why_now="test", user_id="operator")
    store = open_memory_store(paths, MemorySettings(write_atoms=True))
    # write_atoms false on settings → no promote even if store is open.
    _record_beat(
        moments,
        mid,
        {
            "type": "model",
            "content": "memorable free text " * 5,
            "ts": "2026-07-28T10:00:00Z",
        },
        memory_store=store,
        memory_settings=MemorySettings(write_atoms=False),
    )
    tape = moments.list_beats(mid)
    assert any(b.get("type") == "model" for b in tape)
    assert store.list_by_moment(mid) == []


def test_record_beat_promotes_when_write_atoms_true(paths):
    moments = MomentStore(paths)
    mid = moments.open_moment(why_now="test", user_id="operator")
    settings = MemorySettings(write_atoms=True)
    store = open_memory_store(paths, settings)
    _record_beat(
        moments,
        mid,
        {
            "type": "model",
            "content": "memorable free text long enough to promote " * 2,
            "ts": "2026-07-28T10:00:00Z",
        },
        memory_store=store,
        memory_settings=settings,
    )
    tape = moments.list_beats(mid)
    assert any(b.get("type") == "model" for b in tape)
    atoms = store.list_by_moment(mid)
    assert len(atoms) == 1
    assert atoms[0].kind == "model"


def test_worker_does_not_open_store_when_flags_off(paths):
    client = StubChatClient()
    stop = __import__("threading").Event()
    settings = replace(
        default_settings(),
        memory=MemorySettings(write_atoms=False, enabled=False, backend="jsonl"),
    )
    worker = PresenceWorker(
        paths=paths,
        client=client,
        stop_event=stop,
        settings=settings,
    )
    assert worker._ensure_memory_store() is None
    assert worker._memory is None
    assert worker._memory_open_attempted is False


def test_worker_opens_store_when_write_atoms(paths):
    client = StubChatClient()
    stop = __import__("threading").Event()
    settings = replace(
        default_settings(),
        memory=MemorySettings(write_atoms=True, backend="jsonl"),
    )
    worker = PresenceWorker(
        paths=paths,
        client=client,
        stop_event=stop,
        settings=settings,
    )
    store = worker._ensure_memory_store()
    assert store is not None
    assert worker._memory is store
    # Second call reuses.
    assert worker._ensure_memory_store() is store


def test_worker_ladder_skipped_when_flags_off(paths):
    client = StubChatClient()
    stop = __import__("threading").Event()
    settings = replace(
        default_settings(),
        memory=MemorySettings(write_atoms=False, enabled=False, backend="jsonl"),
    )
    worker = PresenceWorker(
        paths=paths,
        client=client,
        stop_event=stop,
        settings=settings,
    )
    # Must not raise; no store open.
    worker._idle_memory_ladder()
    worker._finalize_memory_ladder_15m()
    assert worker._memory is None


def test_media_ids_from_wake_payload():
    wake = WakeItem(
        id="w1",
        kind="user_message",
        priority=10,
        created_at="2026-07-28T10:00:00Z",
        payload={
            "content": "hi",
            "message_id": "m1",
            "media_ids": ["a1", "a2"],
        },
    )
    assert _media_ids_from_wake(wake) == ("a1", "a2")


def test_promote_io_failure_does_not_change_doloop_result(paths):
    """Broken store put_atom must not alter stop_reason / hop_count."""
    registry = ToolRegistry(paths, bundled_root=resolve_bundled_tools_root())
    ctx = ToolContext(
        paths=paths,
        sandbox=None,
        settings=default_settings(),
        moment_id="moment-mem-1",
        user_id="operator",
        registry=registry,
    )
    client = StubChatClient(
        responses=[
            ChatCompletionResult(
                content="all done, nothing to tool",
                reasoning_content="",
                raw_json="{}",
                tool_calls=[],
                finish_reason="stop",
            )
        ]
    )
    # Store that raises on every put.
    bad_store = MagicMock()
    bad_store.list_by_moment.return_value = []
    bad_store.moment_tail.return_value = None
    bad_store.global_tail.return_value = None
    bad_store.put_atom.side_effect = OSError("disk full")

    mem = MemorySettings(write_atoms=True)
    result = run_do_loop(
        client=client,
        registry=registry,
        ctx=ctx,
        settings=default_settings(),
        outer_prefix=[{"role": "system", "content": "test"}],
        social_wake=False,
        memory_store=bad_store,
        memory_settings=mem,
    )
    assert isinstance(result, DoLoopResult)
    # Must complete as a normal free-text stop, not error from promote I/O.
    assert result.stop_reason == "no_tools"
    assert result.error is None
    assert result.hop_count == 1
    assert result.moment_id == "moment-mem-1"


def test_control_obs_never_atoms_via_record_beat(paths):
    moments = MomentStore(paths)
    mid = moments.open_moment(why_now="test", user_id="operator")
    settings = MemorySettings(write_atoms=True)
    store = open_memory_store(paths, settings)
    for kind in (
        "continue",
        "no_speak_nudge",
        "work_continue",
        "tool_thrash",
        "thrash_lesson",
        "skill_commit",
    ):
        _record_beat(
            moments,
            mid,
            {"type": "obs", "kind": kind, "content": f"host {kind}"},
            memory_store=store,
            memory_settings=settings,
        )
    assert store.list_by_moment(mid) == []
    # Tape still has the beats.
    kinds = [b.get("kind") for b in moments.list_beats(mid) if b.get("type") == "obs"]
    assert "continue" in kinds
    assert "tool_thrash" in kinds
