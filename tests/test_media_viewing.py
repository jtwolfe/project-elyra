"""Moment viewing set helpers (PR2 — KD-V4 / KD-V12 / FIFO cap)."""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.media import MediaStore
from elyra.media.viewing import (
    MAX_VIEWING_SET,
    VIEWING_CARRIER_ID,
    ViewingEntry,
    add_viewing,
    clear_viewing,
    drop_viewing,
    inject_viewing_carrier,
    list_viewing,
    list_viewing_att_ids,
    viewing_att_dicts,
)

FIXTURE_PNG = Path(__file__).parent / "fixtures" / "media" / "1x1.png"


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    return MediaStore(paths)


def test_add_viewing_fifo_order_and_cap():
    entries: dict[str, ViewingEntry] = {}
    for i in range(MAX_VIEWING_SET + 2):
        add_viewing(entries, f"att_{i:02d}", kind="image", filename=f"{i}.png")
    ids = list_viewing_att_ids(entries)
    assert len(ids) == MAX_VIEWING_SET
    # Oldest two dropped.
    assert "att_00" not in ids
    assert "att_01" not in ids
    assert ids[0] == "att_02"
    assert ids[-1] == f"att_{MAX_VIEWING_SET + 1:02d}"


def test_re_view_does_not_reorder():
    entries: dict[str, ViewingEntry] = {}
    add_viewing(entries, "att_a", kind="image")
    add_viewing(entries, "att_b", kind="image")
    add_viewing(entries, "att_c", kind="image")
    entry, created = add_viewing(entries, "att_a", kind="image", filename="new.png")
    assert created is False
    assert entry.filename == "new.png"
    assert list_viewing_att_ids(entries) == ["att_a", "att_b", "att_c"]


def test_drop_and_clear():
    entries: dict[str, ViewingEntry] = {}
    add_viewing(entries, "att_x")
    add_viewing(entries, "att_y")
    assert drop_viewing(entries, "att_x") is True
    assert drop_viewing(entries, "att_x") is False
    assert list_viewing_att_ids(entries) == ["att_y"]
    n = clear_viewing(entries)
    assert n == 1
    assert list_viewing(entries) == []


def test_add_viewing_rejects_empty_att_id():
    entries: dict[str, ViewingEntry] = {}
    with pytest.raises(ValueError):
        add_viewing(entries, "  ")


def test_inject_viewing_carrier_empty_status_quo():
    meal = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "orient"},
    ]
    out, glass, cid = inject_viewing_carrier(
        meal, glass_by_id={}, viewing_att_ids=None
    )
    assert cid is None
    assert glass == {}
    assert out == meal


def test_inject_viewing_carrier_before_orient(store):
    att = store.put_bytes(
        FIXTURE_PNG.read_bytes(), filename="v.png", origin="user_upload"
    )
    meal = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "history", "id": "h1"},
        {"role": "user", "content": "orient body"},  # no id → orient-like
    ]
    out, glass, cid = inject_viewing_carrier(
        meal,
        glass_by_id={},
        viewing_att_ids=[att.id],
        media_store=store,
    )
    assert cid == VIEWING_CARRIER_ID
    assert out[-1]["content"] == "orient body"
    assert out[-2]["id"] == VIEWING_CARRIER_ID
    assert VIEWING_CARRIER_ID in glass
    atts = glass[VIEWING_CARRIER_ID]["attachments"]
    assert atts and atts[0]["id"] == att.id
    assert atts[0].get("kind") == "image"


def test_viewing_att_dicts_resolves_media_store(store):
    att = store.put_bytes(
        FIXTURE_PNG.read_bytes(), filename="x.png", origin="user_upload"
    )
    rows = viewing_att_dicts([att.id], store)
    assert len(rows) == 1
    assert rows[0]["id"] == att.id
    assert rows[0]["filename"] == "x.png"
    # Dedup
    assert len(viewing_att_dicts([att.id, att.id], store)) == 1


def test_worker_mark_viewing_and_finalize_clear(paths, store):
    """KD-V12: mark_viewing dirties set; finalize clear empties set + dirty."""
    import threading

    from elyra.llm.client import StubChatClient
    from elyra.loop.doloop import DoLoopResult
    from elyra.moment import MomentStore
    from elyra.presence import TimerService, WakeQueue
    from elyra.presence.worker import PresenceWorker
    from elyra.settings import default_settings
    from elyra.tools import ToolRegistry

    def _stub_loop(**_kwargs):
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            arm_wait=None,
            spoke=False,
            moment_id=_kwargs.get("ctx").moment_id if _kwargs.get("ctx") else "",
            reouter_count=0,
        )

    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=threading.Event(),
        poll_seconds=0.05,
        settings=default_settings(),
        queue=WakeQueue(paths),
        timers=TimerService(paths, WakeQueue(paths)),
        moments=MomentStore(paths),
        registry=ToolRegistry(),
        run_do_loop_fn=_stub_loop,
    )
    att = store.put_bytes(
        FIXTURE_PNG.read_bytes(), filename="f.png", origin="user_upload"
    )
    ids = worker.mark_viewing(
        att.id, kind="image", mime="image/png", filename="f.png"
    )
    assert att.id in ids
    assert worker._is_viewing_dirty() is True
    assert worker._snapshot_viewing_att_ids() == [att.id]

    # Finalize path clears set (caller holds lock in production; use unlocked).
    with worker._lock:
        worker._clear_moment_viewing_unlocked()
    assert worker._snapshot_viewing_att_ids() == []
    assert worker._is_viewing_dirty() is False
