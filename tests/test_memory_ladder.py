"""Period summary ladder: template, source packs, LLM stub, hourly tick."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.ladder import (
    build_source_pack,
    build_summary_atom,
    cascade_from_hour,
    collect_window_sources,
    gap_spans,
    load_ladder_state,
    mark_dirty_1h,
    max_highlights,
    moment_blocks_for_window,
    process_closed_hours,
    refresh_due,
    refresh_window,
    render_template_summary,
    select_highlights,
    tick,
)
from elyra.memory.ladder_llm import (
    ChatClientSummaryLlm,
    SummaryLlmError,
)
from elyra.memory.store import open_memory_store
from elyra.memory.types import (
    Atom,
    new_atom_id,
    stable_summary_id,
    to_iso_z,
    window_bounds,
)
from elyra.llm.usage import UsageHardStopError


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


class StubLlm:
    """Deterministic SummaryLlm for tests."""

    def __init__(self, responses: list[str] | None = None, *, fail: bool = False):
        self.responses = list(responses or ["DRAFT narrative body."])
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: list[dict[str, Any]], *, max_tokens: int) -> str:
        self.calls.append({"messages": messages, "max_tokens": max_tokens})
        if self.fail:
            raise SummaryLlmError("stub_fail")
        if not self.responses:
            return "empty-fallback"
        return self.responses.pop(0)


def test_max_highlights_by_scale():
    assert max_highlights("15m") == 12
    assert max_highlights("1h") == 16
    assert max_highlights("6h") == 20
    assert max_highlights("1d") == 20
    assert max_highlights("1y") == 20


def test_window_bounds_used_by_ladder():
    """Ladder windows follow types.window_bounds UTC grids."""
    t = datetime(2026, 7, 28, 12, 17, tzinfo=UTC)
    start, end = window_bounds("1h", t)
    assert start == datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    assert end == datetime(2026, 7, 28, 13, 0, tzinfo=UTC)


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
    picks = select_highlights(atoms, scale="1h", limit=4)
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
    end = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
    body = render_template_summary(
        scale="1h",
        window_start=start,
        window_end=end,
        sources=sources,
    )
    assert body.startswith(
        "[summary 1h | 2026-07-28T12:00:00Z → 2026-07-28T13:00:00Z]"
    )
    assert "moments: 2 | atoms: 3 | speaks: 1 | tools: 1" in body
    assert "goals touched: g_1, g_2" in body
    assert "highlights:" in body
    assert "speak: hello operator" in body
    assert "open threads: check inbox" in body


def test_stable_id_replace(store):
    """Same (scale, window_start) always overwrites the same summary atom id."""
    t = datetime(2026, 7, 28, 12, 5, tzinfo=UTC)
    start, end = window_bounds("1h", t)
    store.put_atom(
        _atom(
            t="2026-07-28T12:05:00Z",
            kind="speak",
            text="first version content",
            moment_id="m1",
        )
    )
    a1 = refresh_window(store, "1h", t)
    assert a1 is not None
    sid = stable_summary_id("1h", start)
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
    a2 = refresh_window(store, "1h", t)
    assert a2 is not None
    assert a2.atom_id == sid
    assert a2.atom_id == a1.atom_id
    assert "second wave" in a2.content_text

    rows = store.list_summaries("1h", overlapping=(start, end))
    assert len(rows) == 1
    assert rows[0].atom_id == sid
    got = store.get_atom(sid)
    assert got is not None
    assert got.content_text == a2.content_text


def test_refresh_window_skips_empty(store):
    t = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    assert refresh_window(store, "1h", t) is None


def test_write_path_rejects_legacy_15m_by_default(store):
    """ladder_write_legacy_scales=false rejects new 15m/6h writes."""
    t = datetime(2026, 7, 28, 12, 5, tzinfo=UTC)
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="x", moment_id="m1")
    )
    settings = MemorySettings(ladder_write_legacy_scales=False)
    assert refresh_window(store, "15m", t, settings=settings) is None
    assert refresh_window(store, "6h", t, settings=settings) is None
    with pytest.raises(ValueError, match="not writable"):
        build_summary_atom(store, "15m", t, settings=settings)


def test_write_path_allows_legacy_when_flag_on(store):
    t = datetime(2026, 7, 28, 12, 5, tzinfo=UTC)
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="legacy-ok", moment_id="m1")
    )
    settings = MemorySettings(ladder_write_legacy_scales=True)
    atom = refresh_window(store, "15m", t, settings=settings)
    assert atom is not None
    assert atom.scale == "15m"


def test_1d_prefers_child_1h_summaries(store):
    """Coarser 1d summary prefers child 1h tips over raw atoms."""
    base = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    store.put_atom(
        _atom(
            t="2026-07-28T10:05:00Z",
            kind="speak",
            text="hour-A-speak",
            moment_id="m1",
        )
    )
    store.put_atom(
        _atom(
            t="2026-07-28T11:20:00Z",
            kind="observation",
            text="hour-B-obs",
            moment_id="m2",
        )
    )
    s_a = refresh_window(store, "1h", base)
    s_b = refresh_window(store, "1h", base + timedelta(hours=1))
    assert s_a is not None and s_b is not None
    assert s_a.atom_id != s_b.atom_id

    day_start = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    sources, from_children, child = collect_window_sources(
        store, "1d", day_start, day_start + timedelta(days=1)
    )
    assert from_children is True
    assert child == "1h"
    assert {s.atom_id for s in sources} == {s_a.atom_id, s_b.atom_id}

    day = refresh_window(store, "1d", day_start)
    assert day is not None
    assert day.scale == "1d"
    assert day.meta.get("from_children") is True
    assert day.meta.get("child_scale") == "1h"
    child_ids = day.meta.get("child_atom_ids") or []
    assert s_a.atom_id in child_ids
    assert s_b.atom_id in child_ids


def test_1h_uses_raw_no_child_scale(store):
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
    assert child is None
    assert len(sources) == 1
    assert sources[0].content_text == "raw-only"


def test_build_summary_meta_child_cap_and_honesty(store):
    start = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    for i in range(100):
        store.put_atom(
            _atom(
                t=f"2026-07-28T12:00:{i % 60:02d}Z",
                kind="observation",
                text=f"n{i}",
                moment_id="m1",
            )
        )
    atom = build_summary_atom(store, "1h", start, end)
    assert len(atom.meta["child_atom_ids"]) == 96
    assert atom.meta["source"] == "template"
    assert atom.meta["summary_mode_requested"] == "template"
    assert atom.meta["version"] == 1
    assert "is_tip" not in atom.meta  # KD-TIP: no live is_tip
    assert atom.meta.get("generated_at")
    assert atom.kind == "summary"


def test_gap_spans_mid_window():
    w0 = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    w1 = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
    # Moments only in first 10 min and last 10 min → mid gap.
    intervals = [
        (datetime(2026, 7, 28, 12, 0, tzinfo=UTC), datetime(2026, 7, 28, 12, 10, tzinfo=UTC)),
        (datetime(2026, 7, 28, 12, 50, tzinfo=UTC), datetime(2026, 7, 28, 13, 0, tzinfo=UTC)),
    ]
    gaps = gap_spans(w0, w1, intervals)
    assert len(gaps) == 1
    assert gaps[0][0] == datetime(2026, 7, 28, 12, 10, tzinfo=UTC)
    assert gaps[0][1] == datetime(2026, 7, 28, 12, 50, tzinfo=UTC)


def test_gap_spans_empty_collection():
    w0 = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    w1 = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
    gaps = gap_spans(w0, w1, [])
    assert gaps == [(w0, w1)]
    # Tiny window under threshold → no gap emitted.
    tiny_end = w0 + timedelta(minutes=2)
    assert gap_spans(w0, tiny_end, []) == []


def test_build_source_pack_includes_mid_window_gap(store):
    """Empty mid-window range appears in pack text (KD6)."""
    w0 = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    w1 = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
    store.put_atom(
        _atom(
            t="2026-07-28T12:05:00Z",
            kind="speak",
            text="early",
            moment_id="m_early",
            meta={"why_now": "start"},
        )
    )
    store.put_atom(
        _atom(
            t="2026-07-28T12:55:00Z",
            kind="observation",
            text="late",
            moment_id="m_late",
        )
    )
    sources, _, _ = collect_window_sources(store, "1h", w0, w1)
    pack = build_source_pack(
        "1h",
        w0,
        w1,
        sources,
        identity_names={"self": "Elyra", "user": "Jim"},
        from_children=False,
        store=store,
    )
    assert "[gaps]" in pack
    assert "no moments from" in pack
    assert "self=Elyra" in pack
    assert "user=Jim" in pack
    assert "m_early" in pack
    assert "m_late" in pack


def test_moment_blocks_for_window(store):
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="a", moment_id="m1")
    )
    store.put_atom(
        _atom(t="2026-07-28T12:06:00Z", kind="tool", text="b", moment_id="m1")
    )
    store.put_atom(
        _atom(t="2026-07-28T12:20:00Z", kind="observation", text="c", moment_id="m2")
    )
    blocks = moment_blocks_for_window(
        store,
        datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        datetime(2026, 7, 28, 13, 0, tzinfo=UTC),
    )
    assert len(blocks) == 2
    assert blocks[0]["moment_id"] == "m1"
    assert blocks[0]["n_atoms"] == 2


def test_template_path_still_works(store):
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="hello", moment_id="m1")
    )
    settings = MemorySettings(summary_mode="template")
    atom = refresh_window(
        store,
        "1h",
        datetime(2026, 7, 28, 12, 5, tzinfo=UTC),
        settings=settings,
    )
    assert atom is not None
    assert atom.meta["source"] == "template"
    assert "[summary 1h" in atom.content_text


def test_stub_llm_one_pass_1h(store):
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="hi", moment_id="m1")
    )
    llm = StubLlm(responses=["Hour narrative in one pass."])
    settings = MemorySettings(summary_mode="llm")
    atom = build_summary_atom(
        store,
        "1h",
        datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        settings=settings,
        llm=llm,
    )
    assert atom.content_text == "Hour narrative in one pass."
    assert atom.meta["source"] == "llm"
    assert atom.meta["llm_passes"] == 1
    assert len(llm.calls) == 1


def test_stub_llm_two_pass_1d(store):
    # Seed two 1h tips so 1d has children.
    for hour in (10, 11):
        store.put_atom(
            _atom(
                t=f"2026-07-28T{hour:02d}:05:00Z",
                kind="speak",
                text=f"h{hour}",
                moment_id=f"m{hour}",
            )
        )
        refresh_window(store, "1h", datetime(2026, 7, 28, hour, 0, tzinfo=UTC))

    llm = StubLlm(
        responses=[
            "DRAFT " + ("x" * 100),
            "Final day narrative compressed.",
        ]
    )
    settings = MemorySettings(summary_mode="llm")
    atom = build_summary_atom(
        store,
        "1d",
        datetime(2026, 7, 28, 0, 0, tzinfo=UTC),
        settings=settings,
        llm=llm,
    )
    assert atom.content_text == "Final day narrative compressed."
    assert atom.meta["source"] == "llm"
    assert atom.meta["llm_passes"] == 2
    assert len(llm.calls) == 2


def test_stub_llm_failure_falls_back_to_template(store):
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="hi", moment_id="m1")
    )
    llm = StubLlm(fail=True)
    settings = MemorySettings(summary_mode="llm")
    atom = build_summary_atom(
        store,
        "1h",
        datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        settings=settings,
        llm=llm,
    )
    assert atom.meta["source"] == "llm_fallback_template"
    assert atom.meta.get("llm_error")
    assert "[summary 1h" in atom.content_text


def test_adapter_hard_stop_template_fallback(store):
    class HardStopClient:
        def chat_completion(self, messages, **kwargs):
            assert kwargs.get("reasoning") is False
            raise UsageHardStopError("weekly cap", level="hard")

    adapter = ChatClientSummaryLlm(HardStopClient())
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="hi", moment_id="m1")
    )
    settings = MemorySettings(summary_mode="llm")
    atom = build_summary_atom(
        store,
        "1h",
        datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
        settings=settings,
        llm=adapter,
    )
    assert atom.meta["source"] == "llm_fallback_template"
    assert "usage_hard_stop" in (atom.meta.get("llm_error") or "")


def test_adapter_success_passes_reasoning_false():
    class OkClient:
        def __init__(self):
            self.kwargs = None

        def chat_completion(self, messages, **kwargs):
            self.kwargs = kwargs

            class R:
                content = "  ok body  "

            return R()

    client = OkClient()
    adapter = ChatClientSummaryLlm(client)
    text = adapter.complete([{"role": "user", "content": "x"}], max_tokens=50)
    assert text == "ok body"
    assert client.kwargs["reasoning"] is False
    assert client.kwargs["max_tokens"] == 50


def test_refresh_due_max_ms_zero_skips_work(store):
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="x", moment_id="m1")
    )
    state: dict = {"round_robin_idx": 0, "last_refresh": {}}
    result = refresh_due(
        store,
        datetime(2026, 7, 28, 12, 10, tzinfo=UTC),
        max_ms=0,
        scales=["1h", "1d"],
        state=state,
    )
    assert result["refreshed"] == 0
    assert result["skipped"] is True
    assert result["reason"] == "max_ms"
    assert result["scale"] == "1h"
    assert state["round_robin_idx"] == 1
    assert store.list_summaries("1h") == []


def test_refresh_due_respects_max_ms_budget(store):
    """With a tiny budget, only as many windows as fit before elapsed >= max_ms."""
    for hour in (8, 9, 10, 11):
        store.put_atom(
            _atom(
                t=f"2026-07-28T{hour:02d}:05:00Z",
                kind="speak",
                text=f"slot-{hour}",
                moment_id=f"m{hour}",
            )
        )

    clock = {"t": 100.0}
    state: dict = {"round_robin_idx": 0, "last_refresh": {}}

    def stepping_mono() -> float:
        return clock["t"]

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
                datetime(2026, 7, 28, 12, 0, tzinfo=UTC),
                max_ms=25,
                scales=["1h"],
                state=state,
                n_windows=8,
            )

    assert result["scale"] == "1h"
    assert result["refreshed"] == 2
    assert len(result["windows"]) == 2


def test_refresh_due_round_robin_write_scales(store):
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="x", moment_id="m1")
    )
    now = datetime(2026, 7, 28, 12, 10, tzinfo=UTC)
    state: dict = {"round_robin_idx": 0, "last_refresh": {}}
    r1 = refresh_due(
        store, now, max_ms=5000, scales=["1h", "1d", "1w"], state=state
    )
    assert r1["scale"] == "1h"
    assert r1["refreshed"] >= 1
    r2 = refresh_due(
        store, now, max_ms=5000, scales=["1h", "1d", "1w"], state=state
    )
    assert r2["scale"] == "1d"
    r3 = refresh_due(
        store, now, max_ms=5000, scales=["1h", "1d", "1w"], state=state
    )
    assert r3["scale"] == "1w"
    r4 = refresh_due(
        store, now, max_ms=5000, scales=["1h", "1d", "1w"], state=state
    )
    assert r4["scale"] == "1h"


def test_refresh_due_persists_state(store, paths):
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="x", moment_id="m1")
    )
    refresh_due(
        store,
        datetime(2026, 7, 28, 12, 10, tzinfo=UTC),
        max_ms=5000,
        scales=["1h"],
    )
    state_path = paths.data_dir / "memory" / "ladder" / "state.json"
    assert state_path.is_file()
    text = state_path.read_text(encoding="utf-8")
    assert "last_refresh" in text
    assert "1h" in text
    assert "schema_version" in text


def test_highlights_cap_1h():
    atoms = [
        _atom(
            t=f"2026-07-28T12:00:{i:02d}Z",
            kind="speak",
            text=f"s{i}",
        )
        for i in range(20)
    ]
    picks = select_highlights(atoms, scale="1h")
    assert len(picks) == 16


def test_refresh_window_skips_unchanged_put(store):
    """Unchanged summary body must not append another JSONL line (bloat)."""
    t = datetime(2026, 7, 28, 4, 20, tzinfo=UTC)
    store.put_atom(
        _atom(t=to_iso_z(t), kind="speak", text="hello world highlight")
    )
    a1 = refresh_window(store, "1h", t)
    assert a1 is not None
    lines_before = store.atoms_path.read_text(encoding="utf-8").count("\n")
    a2 = refresh_window(store, "1h", t)
    assert a2 is not None
    assert a2.atom_id == a1.atom_id
    assert a2.content_text == a1.content_text
    lines_after = store.atoms_path.read_text(encoding="utf-8").count("\n")
    assert lines_after == lines_before


def test_mark_dirty_1h_no_llm(store, paths):
    """Finalize path only dirty-marks; does not create summary atoms."""
    state: dict = load_ladder_state(store)
    result = mark_dirty_1h(
        store,
        datetime(2026, 7, 28, 12, 30, tzinfo=UTC),
        state=state,
    )
    assert result["dirty_1h"] == "2026-07-28T12:00:00Z"
    assert "2026-07-28T12:00:00Z" in state["dirty_1h_windows"]
    assert store.list_summaries("1h") == []


def test_hourly_process_closed_hours_and_cascade(store):
    # Two closed hours with content.
    for hour in (10, 11):
        store.put_atom(
            _atom(
                t=f"2026-07-28T{hour:02d}:15:00Z",
                kind="speak",
                text=f"work-{hour}",
                moment_id=f"m{hour}",
            )
        )
    now = datetime(2026, 7, 28, 12, 5, tzinfo=UTC)
    settings = MemorySettings(
        summary_mode="template",
        ladder_catchup_max_hours=24,
        ladder_hourly_max_ms=60_000,
    )
    state: dict = load_ladder_state(store)
    # Seed dirty so process runs even if tips missing.
    state["dirty_1h_windows"] = [
        "2026-07-28T10:00:00Z",
        "2026-07-28T11:00:00Z",
    ]
    result = process_closed_hours(
        store, now, settings=settings, state=state
    )
    assert "2026-07-28T10:00:00Z" in result["processed_1h"]
    assert "2026-07-28T11:00:00Z" in result["processed_1h"]
    # Cascade should have refreshed 1d (and possibly coarser if children exist).
    assert any(c.startswith("1d:") for c in result["cascaded"])
    tips_1h = store.list_summaries("1h")
    assert len(tips_1h) >= 2


def test_hourly_budget_stops_mid_cascade(store):
    store.put_atom(
        _atom(
            t="2026-07-28T10:15:00Z",
            kind="speak",
            text="work",
            moment_id="m1",
        )
    )
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    settings = MemorySettings(
        summary_mode="template",
        ladder_hourly_max_ms=1,  # tiny wall budget
        ladder_catchup_max_hours=24,
    )
    state: dict = {
        **load_ladder_state(store),
        "dirty_1h_windows": ["2026-07-28T10:00:00Z", "2026-07-28T11:00:00Z"],
    }

    import elyra.memory.ladder as ladder_mod

    clock = {"t": 0.0}

    def mono() -> float:
        return clock["t"]

    original_refresh = ladder_mod.refresh_window

    def slow_refresh(*args, **kwargs):
        clock["t"] += 0.050  # 50ms per refresh
        return original_refresh(*args, **kwargs)

    with mock.patch.object(ladder_mod.time, "monotonic", side_effect=mono):
        with mock.patch.object(
            ladder_mod, "refresh_window", side_effect=slow_refresh
        ):
            result = process_closed_hours(
                store, now, settings=settings, state=state, max_ms=1
            )

    # With 1ms budget and 50ms per refresh, at most one window starts.
    assert result["stopped_reason"] == "budget" or len(result["processed_1h"]) <= 1


def test_cascade_from_hour_uses_write_parent_map(store):
    store.put_atom(
        _atom(
            t="2026-07-28T10:15:00Z",
            kind="speak",
            text="hour work",
            moment_id="m1",
        )
    )
    refresh_window(store, "1h", datetime(2026, 7, 28, 10, 0, tzinfo=UTC))
    settings = MemorySettings(summary_mode="template")
    result = cascade_from_hour(
        store,
        datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        settings=settings,
    )
    # Parent of 1h is 1d (never 6h).
    assert any(c.startswith("1d:") for c in result["refreshed"])
    assert not any("6h:" in c for c in result["refreshed"])
    assert not any("15m:" in c for c in result["refreshed"])


def test_tick_hourly_vs_nibble(store):
    store.put_atom(
        _atom(
            t="2026-07-28T10:15:00Z",
            kind="speak",
            text="x",
            moment_id="m1",
        )
    )
    settings = MemorySettings(summary_mode="template", ladder_hourly_max_ms=30_000)
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    state: dict = load_ladder_state(store)
    state["dirty_1h_windows"] = ["2026-07-28T10:00:00Z"]
    r1 = tick(store, now, settings=settings, state=state)
    assert r1["path"] == "hourly"

    # After hourly, with last_hourly_process recent and no dirty → nibble.
    state["dirty_1h_windows"] = []
    state["last_hourly_process"] = to_iso_z(now)
    state["catchup_cursor"] = to_iso_z(now)
    r2 = tick(store, now + timedelta(minutes=5), settings=settings, state=state)
    assert r2["path"] == "nibble"


def test_no_hop_path_llm_on_finalize(store):
    """mark_dirty_1h must not invoke SummaryLlm."""
    llm = StubLlm(responses=["should-not-run"])
    # Finalize-equivalent: dirty mark only.
    mark_dirty_1h(store, datetime(2026, 7, 28, 12, 30, tzinfo=UTC))
    assert llm.calls == []
    assert store.list_summaries("1h") == []
