"""Period summary ladder: template render, stable ids, refresh_due budget."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.ladder import (
    build_summary_atom,
    collect_window_sources,
    max_highlights,
    refresh_due,
    refresh_window,
    render_template_summary,
    select_highlights,
)
from elyra.memory.store import open_memory_store
from elyra.memory.types import (
    Atom,
    new_atom_id,
    stable_summary_id,
    to_iso_z,
    window_bounds,
)


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    s = open_memory_store(paths, MemorySettings(write_atoms=True, backend="jsonl"))
    yield s
    s.close()


def _atom(
    *,
    t: str,
    kind: str = "observation",
    text: str = "body",
    moment_id: str | None = "m1",
    atom_id: str | None = None,
    meta: dict | None = None,
    **kwargs,
) -> Atom:
    return Atom(
        atom_id=atom_id or new_atom_id(),
        t_start=t,
        kind=kind,
        content_text=text,
        content_ref="inline",
        moment_id=moment_id,
        meta=meta or {},
        **kwargs,
    )


def test_max_highlights_by_scale():
    assert max_highlights("15m") == 12
    assert max_highlights("1h") == 16
    assert max_highlights("6h") == 20
    assert max_highlights("1d") == 20


def test_window_bounds_used_by_ladder():
    """Ladder windows follow types.window_bounds UTC grids."""
    t = datetime(2026, 7, 28, 12, 17, tzinfo=UTC)
    start, end = window_bounds("15m", t)
    assert start == datetime(2026, 7, 28, 12, 15, tzinfo=UTC)
    assert end == datetime(2026, 7, 28, 12, 30, tzinfo=UTC)


def test_select_highlights_ranking():
    atoms = [
        _atom(t="2026-07-28T12:00:00Z", kind="tool", text="ok tool", meta={"ok": True}),
        _atom(t="2026-07-28T12:01:00Z", kind="model", text="think"),
        _atom(
            t="2026-07-28T12:02:00Z",
            kind="tool",
            text="boom",
            meta={"ok": False},
        ),
        _atom(t="2026-07-28T12:03:00Z", kind="ledger", text="goal updated"),
        _atom(t="2026-07-28T12:04:00Z", kind="observation", text="user said hi"),
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="hello there"),
    ]
    picks = select_highlights(atoms, scale="15m", limit=4)
    kinds = [p.kind for p in picks]
    assert kinds[0] == "speak"
    assert kinds[1] == "observation"
    assert kinds[2] == "ledger"
    assert kinds[3] == "tool" and picks[3].meta.get("ok") is False


def test_render_template_content():
    sources = [
        _atom(
            t="2026-07-28T12:05:00Z",
            kind="speak",
            text="hello operator",
            moment_id="mA",
            meta={"goal_id": "g_1", "why_now": "check inbox"},
        ),
        _atom(
            t="2026-07-28T12:06:00Z",
            kind="tool",
            text="ran search",
            moment_id="mA",
            meta={"ok": True, "goal_id": "g_1"},
        ),
        _atom(
            t="2026-07-28T12:10:00Z",
            kind="observation",
            text="follow-up",
            moment_id="mB",
            meta={"goal_ids": ["g_2"]},
        ),
    ]
    start = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    end = datetime(2026, 7, 28, 12, 15, tzinfo=UTC)
    body = render_template_summary(
        scale="15m",
        window_start=start,
        window_end=end,
        sources=sources,
    )
    assert body.startswith(
        "[summary 15m | 2026-07-28T12:00:00Z → 2026-07-28T12:15:00Z]"
    )
    assert "moments: 2 | atoms: 3 | speaks: 1 | tools: 1" in body
    assert "goals touched: g_1, g_2" in body
    assert "highlights:" in body
    assert "speak: hello operator" in body
    assert "open threads: check inbox" in body


def test_stable_id_replace(store):
    """Same (scale, window_start) always overwrites the same summary atom id."""
    t = datetime(2026, 7, 28, 12, 5, tzinfo=UTC)
    start, end = window_bounds("15m", t)
    store.put_atom(
        _atom(
            t="2026-07-28T12:05:00Z",
            kind="speak",
            text="first version content",
            moment_id="m1",
        )
    )
    a1 = refresh_window(store, "15m", t)
    assert a1 is not None
    sid = stable_summary_id("15m", start)
    assert a1.atom_id == sid
    assert a1.window_start == to_iso_z(start)
    assert a1.window_end == to_iso_z(end)
    assert "first version content" in a1.content_text

    store.put_atom(
        _atom(
            t="2026-07-28T12:08:00Z",
            kind="observation",
            text="second wave",
            moment_id="m1",
        )
    )
    a2 = refresh_window(store, "15m", t)
    assert a2 is not None
    assert a2.atom_id == sid
    assert a2.atom_id == a1.atom_id
    assert "second wave" in a2.content_text

    rows = store.list_summaries("15m", overlapping=(start, end))
    assert len(rows) == 1
    assert rows[0].atom_id == sid
    # Health: one logical summary (plus source atoms); id not duplicated.
    got = store.get_atom(sid)
    assert got is not None
    assert got.content_text == a2.content_text


def test_refresh_window_skips_empty(store):
    t = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    assert refresh_window(store, "15m", t) is None


def test_15m_to_1h_rollup_child_preference(store):
    """Coarser 1h summary prefers child 15m summaries over raw atoms."""
    base = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    # Two 15m windows inside the hour with distinct content.
    store.put_atom(
        _atom(
            t="2026-07-28T12:05:00Z",
            kind="speak",
            text="slot-A-speak",
            moment_id="m1",
        )
    )
    store.put_atom(
        _atom(
            t="2026-07-28T12:20:00Z",
            kind="observation",
            text="slot-B-obs",
            moment_id="m2",
        )
    )
    s_a = refresh_window(store, "15m", base)
    s_b = refresh_window(store, "15m", base + timedelta(minutes=20))
    assert s_a is not None and s_b is not None
    assert s_a.atom_id != s_b.atom_id

    sources, from_children, child = collect_window_sources(
        store, "1h", base, base + timedelta(hours=1)
    )
    assert from_children is True
    assert child == "15m"
    assert {s.atom_id for s in sources} == {s_a.atom_id, s_b.atom_id}

    hour = refresh_window(store, "1h", base)
    assert hour is not None
    assert hour.scale == "1h"
    assert hour.meta.get("from_children") is True
    assert hour.meta.get("child_scale") == "15m"
    child_ids = hour.meta.get("child_atom_ids") or []
    assert s_a.atom_id in child_ids
    assert s_b.atom_id in child_ids
    # Template mentions the child summary bodies or their highlights.
    assert "summary" in hour.content_text or "slot-" in hour.content_text


def test_1h_falls_back_to_raw_when_no_children(store):
    store.put_atom(
        _atom(
            t="2026-07-28T12:10:00Z",
            kind="speak",
            text="raw-only",
            moment_id="m1",
        )
    )
    sources, from_children, child = collect_window_sources(
        store,
        "1h",
        datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        datetime(2026, 7, 28, 13, 0, tzinfo=UTC),
    )
    assert from_children is False
    assert child == "15m"
    assert len(sources) == 1
    assert sources[0].content_text == "raw-only"


def test_build_summary_meta_child_cap(store):
    start = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    end = start + timedelta(minutes=15)
    for i in range(70):
        store.put_atom(
            _atom(
                t=f"2026-07-28T12:00:{i % 60:02d}Z",
                kind="observation",
                text=f"n{i}",
                moment_id="m1",
            )
        )
    atom = build_summary_atom(store, "15m", start, end)
    assert len(atom.meta["child_atom_ids"]) == 64
    assert atom.meta["source"] == "template"
    assert atom.kind == "summary"


def test_refresh_due_max_ms_zero_skips_work(store):
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="x", moment_id="m1")
    )
    state: dict = {"round_robin_idx": 0, "last_refresh": {}}
    result = refresh_due(
        store,
        datetime(2026, 7, 28, 12, 10, tzinfo=UTC),
        max_ms=0,
        scales=["15m", "1h"],
        state=state,
    )
    assert result["refreshed"] == 0
    assert result["skipped"] is True
    assert result["reason"] == "max_ms"
    assert result["scale"] == "15m"
    # Round-robin advanced even when skipping.
    assert state["round_robin_idx"] == 1
    assert store.list_summaries("15m") == []


def test_refresh_due_respects_max_ms_budget(store):
    """With a tiny budget, only as many windows as fit before elapsed >= max_ms."""
    # Populate several 15m windows.
    for minute in (0, 15, 30, 45):
        store.put_atom(
            _atom(
                t=f"2026-07-28T12:{minute:02d}:05Z",
                kind="speak",
                text=f"slot-{minute}",
                moment_id=f"m{minute}",
            )
        )

    clock = {"t": 100.0}
    state: dict = {"round_robin_idx": 0, "last_refresh": {}}

    def stepping_mono() -> float:
        return clock["t"]

    # Import the real function before patching the name used inside refresh_due.
    import elyra.memory.ladder as ladder_mod

    original = ladder_mod.refresh_window

    def counted_refresh(st, scale, t, **kwargs):
        atom = original(st, scale, t, **kwargs)
        clock["t"] += 0.020  # 20ms per refresh
        return atom

    with mock.patch.object(ladder_mod.time, "monotonic", side_effect=stepping_mono):
        with mock.patch.object(
            ladder_mod, "refresh_window", side_effect=counted_refresh
        ):
            result = refresh_due(
                store,
                datetime(2026, 7, 28, 12, 50, tzinfo=UTC),
                max_ms=25,
                scales=["15m"],
                state=state,
                n_windows=8,
            )

    # Budget 25ms; check before work. After 2 refreshes elapsed=40ms → stop.
    assert result["scale"] == "15m"
    assert result["refreshed"] == 2
    assert len(result["windows"]) == 2


def test_refresh_due_round_robin_scales(store):
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="x", moment_id="m1")
    )
    now = datetime(2026, 7, 28, 12, 10, tzinfo=UTC)
    state: dict = {"round_robin_idx": 0, "last_refresh": {}}
    r1 = refresh_due(
        store, now, max_ms=5000, scales=["15m", "1h", "6h"], state=state
    )
    assert r1["scale"] == "15m"
    assert r1["refreshed"] >= 1
    r2 = refresh_due(
        store, now, max_ms=5000, scales=["15m", "1h", "6h"], state=state
    )
    assert r2["scale"] == "1h"
    r3 = refresh_due(
        store, now, max_ms=5000, scales=["15m", "1h", "6h"], state=state
    )
    assert r3["scale"] == "6h"
    r4 = refresh_due(
        store, now, max_ms=5000, scales=["15m", "1h", "6h"], state=state
    )
    assert r4["scale"] == "15m"


def test_refresh_due_persists_state(store, paths):
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="x", moment_id="m1")
    )
    refresh_due(
        store,
        datetime(2026, 7, 28, 12, 10, tzinfo=UTC),
        max_ms=5000,
        scales=["15m"],
    )
    state_path = paths.data_dir / "memory" / "ladder" / "state.json"
    assert state_path.is_file()
    text = state_path.read_text(encoding="utf-8")
    assert "last_refresh" in text
    assert "15m" in text


def test_highlights_cap_15m():
    atoms = [
        _atom(
            t=f"2026-07-28T12:00:{i:02d}Z",
            kind="speak",
            text=f"s{i}",
        )
        for i in range(20)
    ]
    picks = select_highlights(atoms, scale="15m")
    assert len(picks) == 12
