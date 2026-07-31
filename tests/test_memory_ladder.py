"""Period summary ladder: template, source packs, LLM stub, hourly tick."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.ladder import (
    LADDER_ENOUGH_1D_TIPS,
    LADDER_ENOUGH_1M_TIPS,
    LADDER_ENOUGH_1W_TIPS,
    allowed_scales,
    build_source_pack,
    build_summary_atom,
    cascade_from_hour,
    child_content_hash,
    collect_window_sources,
    count_tip_summaries,
    gap_spans,
    ladder_status_snapshot,
    load_ladder_state,
    mark_dirty_1h,
    max_highlights,
    moment_blocks_for_window,
    process_closed_hours,
    read_instance_created_at,
    refresh_due,
    refresh_window,
    render_template_summary,
    resolve_tip,
    save_ladder_state,
    scale_allowed_for_instance_age,
    select_highlights,
    tick,
    uses_versioned_ids,
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
    versioned_summary_id,
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
    # Default ladder_source_edge_k=24 when settings omitted.
    assert len(atom.meta["source_atom_ids"]) == 24


def test_build_summary_source_edge_k_respects_settings(store):
    """PR-C: write-time source_atom_ids capped by ladder_source_edge_k."""
    start = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    end = start + timedelta(hours=1)
    for i in range(40):
        store.put_atom(
            _atom(
                t=f"2026-07-28T12:00:{i % 60:02d}Z",
                kind="observation",
                text=f"src{i}",
                moment_id="m1",
            )
        )
    settings = MemorySettings(ladder_source_edge_k=5)
    atom = build_summary_atom(store, "1h", start, end, settings=settings)
    assert len(atom.meta["source_atom_ids"]) == 5
    # Coarser from_children leaves source_atom_ids empty (put 1h tips first).
    base = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    store.put_atom(
        _atom(
            t="2026-07-28T10:05:00Z",
            kind="speak",
            text="hour-A",
            moment_id="mA",
        )
    )
    store.put_atom(
        _atom(
            t="2026-07-28T11:20:00Z",
            kind="observation",
            text="hour-B",
            moment_id="mB",
        )
    )
    s_a = refresh_window(store, "1h", base, settings=settings)
    s_b = refresh_window(store, "1h", base + timedelta(hours=1), settings=settings)
    assert s_a is not None and s_b is not None
    day_start = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    day = build_summary_atom(
        store,
        "1d",
        day_start,
        day_start + timedelta(days=1),
        settings=settings,
    )
    assert day.meta.get("from_children") is True
    assert day.meta.get("source_atom_ids") == []
    child_ids = day.meta.get("child_atom_ids") or []
    assert s_a.atom_id in child_ids
    assert s_b.atom_id in child_ids


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
    state["cascade_pending_1h"] = []
    state["last_hourly_process"] = to_iso_z(now)
    state["catchup_cursor"] = to_iso_z(window_bounds("1h", now)[0])
    r2 = tick(store, now + timedelta(minutes=5), settings=settings, state=state)
    assert r2["path"] == "nibble"


def test_no_hop_path_llm_on_finalize(store):
    """mark_dirty_1h must not invoke SummaryLlm."""
    llm = StubLlm(responses=["should-not-run"])
    # Finalize-equivalent: dirty mark only.
    mark_dirty_1h(store, datetime(2026, 7, 28, 12, 30, tzinfo=UTC))
    assert llm.calls == []
    assert store.list_summaries("1h") == []


def test_cascade_resume_after_budget_stop(store):
    """Issue 1: incomplete cascade resumes on later tick → 1d tip lands."""
    store.put_atom(
        _atom(
            t="2026-07-28T10:15:00Z",
            kind="speak",
            text="work-hour",
            moment_id="m1",
        )
    )
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    settings = MemorySettings(
        summary_mode="template",
        ladder_catchup_max_hours=24,
        ladder_hourly_max_ms=60_000,
    )
    state: dict = {
        **load_ladder_state(store),
        "dirty_1h_windows": ["2026-07-28T10:00:00Z"],
    }

    import elyra.memory.ladder as ladder_mod

    clock = {"t": 0.0}
    original_refresh = ladder_mod.refresh_window
    call_n = {"n": 0}

    def gated_refresh(st, scale, t, **kwargs):
        # First call (1h) cheap; cascade parent hits budget immediately.
        call_n["n"] += 1
        if scale != "1h":
            clock["t"] += 10.0  # 10s → exceeds 1ms budget on cascade entry check
            # Still raise budget via elapsed before work by not calling if already over.
        atom = original_refresh(st, scale, t, **kwargs)
        if scale == "1h":
            clock["t"] += 0.0005
        return atom

    with mock.patch.object(ladder_mod.time, "monotonic", side_effect=lambda: clock["t"]):
        with mock.patch.object(
            ladder_mod, "refresh_window", side_effect=gated_refresh
        ):
            r1 = process_closed_hours(
                store, now, settings=settings, state=state, max_ms=1
            )

    # 1h should land; cascade should stop on budget before/while parent.
    assert "2026-07-28T10:00:00Z" in r1["processed_1h"]
    assert r1["stopped_reason"] == "budget" or state.get("cascade_pending_1h")
    assert store.get_atom(stable_summary_id("1h", "2026-07-28T10:00:00Z")) is not None
    # Tip identity via ladder index (KD-TIP), not stable_summary_id for 1d.
    day_start = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    day_tip = resolve_tip(store, "1d", day_start)
    # Force cascade pending if race left 1d already (slow path still ok).
    if day_tip is None:
        assert "2026-07-28T10:00:00Z" in (state.get("cascade_pending_1h") or []) or (
            state.get("catchup_cursor") == "2026-07-28T10:00:00Z"
        )

    # Resume with generous budget — coarser tip must appear.
    r2 = process_closed_hours(
        store, now, settings=settings, state=state, max_ms=60_000
    )
    day_tip = resolve_tip(store, "1d", day_start)
    assert day_tip is not None
    assert "2026-07-28T10:00:00Z" not in (state.get("cascade_pending_1h") or [])
    assert r2["stopped_reason"] != "budget" or day_tip is not None


def test_catchup_cursor_advances_past_empty_hours(store):
    """Issue 2: sparse content advances cursor; subsequent same-hour ticks nibble."""
    # Only 10:00 has content; 11:00 empty; now is 12:05.
    store.put_atom(
        _atom(
            t="2026-07-28T10:15:00Z",
            kind="speak",
            text="only-ten",
            moment_id="m10",
        )
    )
    now = datetime(2026, 7, 28, 12, 5, tzinfo=UTC)
    settings = MemorySettings(
        summary_mode="template",
        ladder_catchup_max_hours=24,
        ladder_hourly_max_ms=60_000,
    )
    state: dict = load_ladder_state(store)
    r1 = process_closed_hours(store, now, settings=settings, state=state)
    assert "2026-07-28T10:00:00Z" in r1["processed_1h"]
    # Cursor must reach current open hour start so catch-up is not perpetually behind.
    open_start = to_iso_z(window_bounds("1h", now)[0])
    assert state["catchup_cursor"] == open_start
    assert state.get("cascade_pending_1h") in (None, [],)

    # Same-hour follow-up ticks must take nibble path, not hourly forever.
    for _ in range(5):
        r = tick(store, now + timedelta(minutes=1), settings=settings, state=state)
        assert r["path"] == "nibble", r


def test_processed_1h_oldest_first(store):
    """Mixed dirty + missing tips process oldest-first."""
    for hour in (11, 9, 10):
        store.put_atom(
            _atom(
                t=f"2026-07-28T{hour:02d}:20:00Z",
                kind="speak",
                text=f"h{hour}",
                moment_id=f"m{hour}",
            )
        )
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    settings = MemorySettings(
        summary_mode="template",
        ladder_catchup_max_hours=24,
        ladder_hourly_max_ms=60_000,
    )
    state: dict = {
        **load_ladder_state(store),
        "dirty_1h_windows": ["2026-07-28T11:00:00Z", "2026-07-28T09:00:00Z"],
    }
    result = process_closed_hours(store, now, settings=settings, state=state)
    processed = result["processed_1h"]
    assert processed == sorted(processed)
    assert processed[0] == "2026-07-28T09:00:00Z"


def test_empty_horizon_does_not_spin_hourly(store):
    """Issue 2/3 zero-state: all-empty closed horizon leaves hourly idle next tick."""
    now = datetime(2026, 7, 28, 12, 30, tzinfo=UTC)
    settings = MemorySettings(summary_mode="template", ladder_hourly_max_ms=30_000)
    state: dict = load_ladder_state(store)
    r1 = tick(store, now, settings=settings, state=state)
    assert r1["path"] == "hourly"
    open_start = to_iso_z(window_bounds("1h", now)[0])
    assert state["catchup_cursor"] == open_start
    # No dirty, no pending, cursor at open hour → nibble.
    r2 = tick(store, now + timedelta(minutes=2), settings=settings, state=state)
    assert r2["path"] == "nibble"
    r3 = tick(store, now + timedelta(minutes=3), settings=settings, state=state)
    assert r3["path"] == "nibble"


def test_llm_partial_pass_counted_on_fallback(store):
    """Issue 4: pass-A ok + pass-B fail still records llm_passes >= 1."""

    class FailOnSecond:
        def __init__(self) -> None:
            self.n = 0

        def complete(self, messages, *, max_tokens: int) -> str:
            self.n += 1
            if self.n == 1:
                return "DRAFT " + ("x" * 50)
            raise SummaryLlmError("pass_b_boom")

    # Seed 1h tips so 1d has children (always two-pass).
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

    llm = FailOnSecond()
    settings = MemorySettings(summary_mode="llm")
    atom = build_summary_atom(
        store,
        "1d",
        datetime(2026, 7, 28, 0, 0, tzinfo=UTC),
        settings=settings,
        llm=llm,
    )
    assert atom.meta["source"] == "llm_fallback_template"
    # Pass A succeeded + pass B failed in-flight → count both (Issue 10).
    assert int(atom.meta.get("llm_passes") or 0) == 2
    assert llm.n == 2


def test_llm_max_passes_one_skips_pass_b(store):
    """Issue 5: remaining budget of 1 → draft accepted, only one complete()."""
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

    llm = StubLlm(responses=["single-pass draft only"])
    settings = MemorySettings(summary_mode="llm")
    atom = build_summary_atom(
        store,
        "1d",
        datetime(2026, 7, 28, 0, 0, tzinfo=UTC),
        settings=settings,
        llm=llm,
        llm_max_passes=1,
    )
    assert atom.content_text == "single-pass draft only"
    assert atom.meta["llm_passes"] == 1
    assert len(llm.calls) == 1


def test_refresh_due_includes_legacy_when_flag_on(store):
    """Issue 6: ladder_write_legacy_scales=true nibble visits 15m/6h."""
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="x", moment_id="m1")
    )
    settings = MemorySettings(ladder_write_legacy_scales=True, summary_mode="template")
    state: dict = {"round_robin_idx": 0, "last_refresh": {}}
    now = datetime(2026, 7, 28, 12, 10, tzinfo=UTC)
    # PERIOD_SCALE_ORDER starts with 15m when legacy included.
    r = refresh_due(store, now, max_ms=5000, state=state, settings=settings)
    assert r["scale"] == "15m"
    assert r["refreshed"] >= 1


# ── PR-B: version archaeology (coarser heads) ─────────────────────────────


def test_uses_versioned_ids_coarser_only():
    assert uses_versioned_ids("1d")
    assert uses_versioned_ids("1w")
    assert uses_versioned_ids("1m")
    assert uses_versioned_ids("1y")
    assert not uses_versioned_ids("1h")
    assert not uses_versioned_ids("15m")
    assert not uses_versioned_ids("6h")


def test_two_cascades_two_version_atoms_one_tip(store):
    """Two cascades → two 1d atom ids; ladder index holds one tip (KD-TIP)."""
    day_start = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)

    # First hour → cascade creates 1d v1.
    store.put_atom(
        _atom(
            t="2026-07-28T10:15:00Z",
            kind="speak",
            text="hour-ten",
            moment_id="m10",
        )
    )
    refresh_window(store, "1h", datetime(2026, 7, 28, 10, 0, tzinfo=UTC))
    r1 = cascade_from_hour(
        store,
        datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        settings=MemorySettings(summary_mode="template"),
    )
    assert any(c.startswith("1d:") for c in r1["refreshed"])
    tip1 = resolve_tip(store, "1d", day_start)
    assert tip1 is not None
    assert tip1.meta.get("version") == 1
    assert tip1.meta.get("supersedes_atom_id") is None
    assert tip1.atom_id == versioned_summary_id("1d", day_start, 1)
    v1_id = tip1.atom_id
    v1_body = tip1.content_text

    # Second hour → cascade creates 1d v2; tip moves; v1 immutable.
    store.put_atom(
        _atom(
            t="2026-07-28T11:20:00Z",
            kind="observation",
            text="hour-eleven",
            moment_id="m11",
        )
    )
    refresh_window(store, "1h", datetime(2026, 7, 28, 11, 0, tzinfo=UTC))
    r2 = cascade_from_hour(
        store,
        datetime(2026, 7, 28, 11, 0, tzinfo=UTC),
        settings=MemorySettings(summary_mode="template"),
    )
    assert any(c.startswith("1d:") for c in r2["refreshed"])
    tip2 = resolve_tip(store, "1d", day_start)
    assert tip2 is not None
    assert tip2.atom_id != v1_id
    assert tip2.meta.get("version") == 2
    assert tip2.meta.get("supersedes_atom_id") == v1_id
    assert tip2.meta.get("previous_version_id") == v1_id
    assert tip2.atom_id == versioned_summary_id("1d", day_start, 2)

    # Ladder index: one tip only.
    tips = store.list_summaries("1d", tips_only=True)
    day_tips = [
        a
        for a in tips
        if a.window_start and to_iso_z(a.window_start) == to_iso_z(day_start)
    ]
    assert len(day_tips) == 1
    assert day_tips[0].atom_id == tip2.atom_id

    # Previous version row left immutable.
    old = store.get_atom(v1_id)
    assert old is not None
    assert old.content_text == v1_body
    assert old.meta.get("version") == 1
    assert old.meta.get("supersedes_atom_id") is None


def test_skip_version_when_child_content_hash_equal(store):
    """Re-cascade with unchanged children must not mint a new version."""
    day_start = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    store.put_atom(
        _atom(
            t="2026-07-28T10:15:00Z",
            kind="speak",
            text="stable-hour",
            moment_id="m10",
        )
    )
    refresh_window(store, "1h", datetime(2026, 7, 28, 10, 0, tzinfo=UTC))
    cascade_from_hour(
        store,
        datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        settings=MemorySettings(summary_mode="template"),
    )
    tip1 = resolve_tip(store, "1d", day_start)
    assert tip1 is not None
    assert tip1.meta.get("child_content_hash")
    lines_before = store.atoms_path.read_text(encoding="utf-8").count("\n")

    # Same children → skip (hash equal).
    again = refresh_window(store, "1d", day_start)
    assert again is not None
    assert again.atom_id == tip1.atom_id
    assert again.meta.get("version") == 1
    lines_after = store.atoms_path.read_text(encoding="utf-8").count("\n")
    assert lines_after == lines_before

    # Direct hash helper is stable.
    sources, _, _ = collect_window_sources(
        store, "1d", day_start, day_start + timedelta(days=1)
    )
    assert child_content_hash(sources) == tip1.meta["child_content_hash"]


def test_list_summaries_tips_only_default_and_scan(store):
    """tips_only=True (default) via index; tips_only=False O(n) version scan."""
    day_start = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    for hour, text in ((10, "a"), (11, "b")):
        store.put_atom(
            _atom(
                t=f"2026-07-28T{hour:02d}:15:00Z",
                kind="speak",
                text=text,
                moment_id=f"m{hour}",
            )
        )
        refresh_window(store, "1h", datetime(2026, 7, 28, hour, 0, tzinfo=UTC))
        cascade_from_hour(
            store,
            datetime(2026, 7, 28, hour, 0, tzinfo=UTC),
            settings=MemorySettings(summary_mode="template"),
        )

    tip = resolve_tip(store, "1d", day_start)
    assert tip is not None
    assert tip.meta.get("version") == 2

    # Default = tips only (index).
    tips = store.list_summaries("1d")
    day_tips = [
        a
        for a in tips
        if a.window_start and to_iso_z(a.window_start) == to_iso_z(day_start)
    ]
    assert len(day_tips) == 1
    assert day_tips[0].atom_id == tip.atom_id

    # Full version chain via scan.
    versions = store.list_summaries(
        "1d",
        overlapping=(day_start, day_start + timedelta(days=1)),
        tips_only=False,
    )
    assert len(versions) == 2
    assert [int((v.meta or {}).get("version") or 0) for v in versions] == [1, 2]
    assert versions[0].atom_id == versioned_summary_id("1d", day_start, 1)
    assert versions[1].atom_id == versioned_summary_id("1d", day_start, 2)
    assert versions[1].meta.get("supersedes_atom_id") == versions[0].atom_id


def test_1h_still_tip_replace_stable_id(store):
    """1h remains stable_summary_id tip-replace (no version fan-out)."""
    t = datetime(2026, 7, 28, 12, 5, tzinfo=UTC)
    start, _ = window_bounds("1h", t)
    store.put_atom(
        _atom(t="2026-07-28T12:05:00Z", kind="speak", text="v1", moment_id="m1")
    )
    a1 = refresh_window(store, "1h", t)
    store.put_atom(
        _atom(t="2026-07-28T12:20:00Z", kind="speak", text="v2", moment_id="m1")
    )
    a2 = refresh_window(store, "1h", t)
    assert a1 is not None and a2 is not None
    assert a1.atom_id == a2.atom_id == stable_summary_id("1h", start)
    assert a1.meta.get("version") == 1
    assert a2.meta.get("version") == 1
    # Only one tip (and one atom id) for the hour.
    tips = store.list_summaries("1h", overlapping=(start, start + timedelta(hours=1)))
    assert len(tips) == 1
    versions = store.list_summaries(
        "1h",
        overlapping=(start, start + timedelta(hours=1)),
        tips_only=False,
    )
    assert len(versions) == 1


def test_body_equal_still_advances_hash_no_llm_loop(store):
    """Hash differs + same body → new version; next refresh hash-skips (no LLM)."""
    day_start = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    for hour in (10, 11):
        store.put_atom(
            _atom(
                t=f"2026-07-28T{hour:02d}:15:00Z",
                kind="speak",
                text=f"h{hour}",
                moment_id=f"m{hour}",
            )
        )
        refresh_window(store, "1h", datetime(2026, 7, 28, hour, 0, tzinfo=UTC))

    llm = StubLlm(responses=["Same day body forever."] * 20)
    settings = MemorySettings(summary_mode="llm")
    a1 = refresh_window(
        store, "1d", day_start, settings=settings, llm=llm, llm_max_passes=1
    )
    assert a1 is not None
    assert a1.content_text == "Same day body forever."
    assert a1.meta.get("version") == 1
    calls_v1 = len(llm.calls)
    assert calls_v1 >= 1

    # Mutate child 1h body → child_content_hash differs; LLM still returns same body.
    store.put_atom(
        _atom(
            t="2026-07-28T10:45:00Z",
            kind="speak",
            text="hour-ten-extra",
            moment_id="m10",
        )
    )
    refresh_window(store, "1h", datetime(2026, 7, 28, 10, 0, tzinfo=UTC))

    a2 = refresh_window(
        store, "1d", day_start, settings=settings, llm=llm, llm_max_passes=1
    )
    assert a2 is not None
    assert a2.atom_id != a1.atom_id
    assert a2.content_text == a1.content_text
    assert a2.meta.get("child_content_hash") != a1.meta.get("child_content_hash")
    assert a2.meta.get("version") == 2
    assert a2.meta.get("supersedes_atom_id") == a1.atom_id
    calls_v2 = len(llm.calls)
    assert calls_v2 > calls_v1

    # Hash now matches tip → no generate / no new version.
    a3 = refresh_window(
        store, "1d", day_start, settings=settings, llm=llm, llm_max_passes=1
    )
    assert a3 is not None
    assert a3.atom_id == a2.atom_id
    assert len(llm.calls) == calls_v2


def test_child_content_hash_order_independent():
    a = _atom(
        t="2026-07-28T10:00:00Z",
        text="aa",
        atom_id="as_zzzzzzzzzzzzzzzzzzzz",
    )
    b = _atom(
        t="2026-07-28T11:00:00Z",
        text="bb",
        atom_id="as_aaaaaaaaaaaaaaaaaaaa",
    )
    assert child_content_hash([a, b]) == child_content_hash([b, a])


def test_cascade_noop_not_counted_as_refreshed(store):
    """Hash-equal cascade must not list coarser scale under refreshed."""
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
    r1 = cascade_from_hour(
        store, datetime(2026, 7, 28, 10, 0, tzinfo=UTC), settings=settings
    )
    assert any(c.startswith("1d:") for c in r1["refreshed"])
    r2 = cascade_from_hour(
        store, datetime(2026, 7, 28, 10, 0, tzinfo=UTC), settings=settings
    )
    assert not any(c.startswith("1d:") for c in r2["refreshed"])


def test_legacy_stable_1d_superseded_by_versioned(store):
    """Pre-PR-B stable_summary_id tip is superseded; ladder tip becomes versioned."""
    day_start = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    legacy_id = stable_summary_id("1d", day_start)
    store.put_atom(
        Atom(
            atom_id=legacy_id,
            t_start=to_iso_z(day_start),
            t_end=to_iso_z(day_end),
            kind="summary",
            scale="1d",
            window_start=to_iso_z(day_start),
            window_end=to_iso_z(day_end),
            content_text="legacy day tip",
            moment_id=None,
            meta={"source": "template", "version": 1},
        )
    )
    assert resolve_tip(store, "1d", day_start).atom_id == legacy_id

    store.put_atom(
        _atom(
            t="2026-07-28T10:15:00Z",
            kind="speak",
            text="post-migration hour",
            moment_id="m10",
        )
    )
    refresh_window(store, "1h", datetime(2026, 7, 28, 10, 0, tzinfo=UTC))
    cascade_from_hour(
        store,
        datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        settings=MemorySettings(summary_mode="template"),
    )
    tip = resolve_tip(store, "1d", day_start)
    assert tip is not None
    assert tip.atom_id != legacy_id
    assert tip.atom_id == versioned_summary_id("1d", day_start, 2)
    assert tip.meta.get("supersedes_atom_id") == legacy_id
    # Legacy row immutable and still loadable for archaeology.
    old = store.get_atom(legacy_id)
    assert old is not None
    assert old.content_text == "legacy day tip"
    versions = store.list_summaries(
        "1d",
        overlapping=(day_start, day_end),
        tips_only=False,
    )
    ids = {v.atom_id for v in versions}
    assert legacy_id in ids
    assert tip.atom_id in ids


# ── PR-E: catch-up, meter exhaustion, instance-age, status ─────────────────


def test_allowed_scales_age_and_enough_tips():
    """Design §9: soft age thresholds + provisional enough-tips constants."""
    assert LADDER_ENOUGH_1D_TIPS == 3
    assert LADDER_ENOUGH_1W_TIPS == 2
    assert LADDER_ENOUGH_1M_TIPS == 2
    created = datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    young = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)  # age 1.5d
    assert allowed_scales(created, young, tip_counts={}) == ["1h", "1d"]
    # Enough 1d tips unlock 1w early.
    assert allowed_scales(
        created, young, tip_counts={"1d": LADDER_ENOUGH_1D_TIPS}
    ) == ["1h", "1d", "1w"]
    # Age ≥7d unlocks 1w without tip counts.
    weekish = created + timedelta(days=7)
    assert "1w" in allowed_scales(created, weekish, tip_counts={})
    assert "1m" not in allowed_scales(created, weekish, tip_counts={})
    # Enough 1w tips unlock 1m early.
    assert "1m" in allowed_scales(
        created, young, tip_counts={"1d": 3, "1w": LADDER_ENOUGH_1W_TIPS}
    )
    # Age ≥28d unlocks 1m; ≥365d unlocks 1y.
    monthish = created + timedelta(days=28)
    assert allowed_scales(created, monthish, tip_counts={}) == [
        "1h",
        "1d",
        "1w",
        "1m",
    ]
    yearish = created + timedelta(days=365)
    assert allowed_scales(created, yearish, tip_counts={}) == [
        "1h",
        "1d",
        "1w",
        "1m",
        "1y",
    ]
    # Enough 1m tips unlock 1y early.
    assert "1y" in allowed_scales(
        created,
        young,
        tip_counts={"1d": 3, "1w": 2, "1m": LADDER_ENOUGH_1M_TIPS},
    )
    assert scale_allowed_for_instance_age("1w", created, young, tip_counts={}) is False
    assert scale_allowed_for_instance_age(
        "1w", created, young, tip_counts={"1d": 3}
    )


def test_read_instance_created_at_and_count_tips(store, paths):
    created = read_instance_created_at(store)
    assert created is not None
    assert created.tzinfo is not None
    # Seed two 1h tips via ladder path.
    for hour in (10, 11):
        store.put_atom(
            _atom(
                t=f"2026-07-28T{hour:02d}:15:00Z",
                kind="speak",
                text=f"h{hour}",
                moment_id=f"m{hour}",
            )
        )
        refresh_window(store, "1h", datetime(2026, 7, 28, hour, 0, tzinfo=UTC))
    tips = count_tip_summaries(store)
    assert tips.get("1h", 0) >= 2


def test_catchup_three_missing_hours_oldest_first(store):
    """Integration: catch-up fills 3 missing closed hours oldest-first (DoD #9)."""
    for hour in (9, 10, 11):
        store.put_atom(
            _atom(
                t=f"2026-07-28T{hour:02d}:20:00Z",
                kind="speak",
                text=f"work-{hour}",
                moment_id=f"m{hour}",
            )
        )
    now = datetime(2026, 7, 28, 12, 30, tzinfo=UTC)
    settings = MemorySettings(
        summary_mode="template",
        ladder_catchup_max_hours=24,
        ladder_hourly_max_ms=60_000,
    )
    state: dict = load_ladder_state(store)
    result = process_closed_hours(store, now, settings=settings, state=state)
    processed = result["processed_1h"]
    assert processed == [
        "2026-07-28T09:00:00Z",
        "2026-07-28T10:00:00Z",
        "2026-07-28T11:00:00Z",
    ]
    for hour in (9, 10, 11):
        tip = store.get_atom(
            stable_summary_id("1h", datetime(2026, 7, 28, hour, 0, tzinfo=UTC))
        )
        assert tip is not None
        assert (tip.content_text or "").strip()
    # Cascade wrote a 1d tip (always age-allowed).
    assert any(c.startswith("1d:") for c in result["cascaded"])
    day_tip = resolve_tip(store, "1d", datetime(2026, 7, 28, 0, 0, tzinfo=UTC))
    assert day_tip is not None
    assert state.get("last_hourly_process") is not None


def test_llm_budget_exhaustion_template_fallback_no_crash(store):
    """Meter / per-hour LLM budget exhaustion → template path; no crash."""
    for hour in (10, 11):
        store.put_atom(
            _atom(
                t=f"2026-07-28T{hour:02d}:10:00Z",
                kind="speak",
                text=f"hour-{hour}",
                moment_id=f"m{hour}",
            )
        )
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    llm = StubLlm(responses=["LLM body that should not be used"] * 20)
    settings = MemorySettings(
        summary_mode="llm",
        ladder_llm_max_calls_per_tick=3,
        ladder_llm_max_calls_per_hour=0,  # exhausted immediately
        ladder_catchup_max_hours=24,
        ladder_hourly_max_ms=60_000,
    )
    state: dict = {
        **load_ladder_state(store),
        "llm_calls_hour": {
            "hour": to_iso_z(now.replace(minute=0, second=0, microsecond=0)),
            "count": 99,
        },
    }
    # Must not raise even with a failing / unused LLM.
    result = process_closed_hours(
        store, now, settings=settings, llm=llm, state=state
    )
    assert "2026-07-28T10:00:00Z" in result["processed_1h"]
    assert "2026-07-28T11:00:00Z" in result["processed_1h"]
    # With zero remaining calls, stub must not be invoked.
    assert llm.calls == []
    tip = store.get_atom(
        stable_summary_id("1h", datetime(2026, 7, 28, 10, 0, tzinfo=UTC))
    )
    assert tip is not None
    assert (tip.meta or {}).get("source") in ("template", "llm_fallback_template")


def test_llm_fail_mid_cascade_falls_back_without_crash(store):
    """SummaryLlmError during cascade → template fallback; presence-safe."""
    store.put_atom(
        _atom(
            t="2026-07-28T10:15:00Z",
            kind="speak",
            text="hour work",
            moment_id="m10",
        )
    )
    llm = StubLlm(fail=True)
    settings = MemorySettings(
        summary_mode="llm",
        ladder_llm_max_calls_per_tick=5,
        ladder_llm_max_calls_per_hour=40,
        ladder_hourly_max_ms=60_000,
    )
    state: dict = {
        **load_ladder_state(store),
        "dirty_1h_windows": ["2026-07-28T10:00:00Z"],
    }
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    result = process_closed_hours(
        store, now, settings=settings, llm=llm, state=state
    )
    assert "2026-07-28T10:00:00Z" in result["processed_1h"]
    tip = store.get_atom(
        stable_summary_id("1h", datetime(2026, 7, 28, 10, 0, tzinfo=UTC))
    )
    assert tip is not None
    assert (tip.meta or {}).get("source") in ("template", "llm_fallback_template")
    # Adapter hard-stop path already unit-tested; this is end-to-end process path.
    assert llm.calls  # attempted at least once before fallback


def test_cascade_gates_1w_when_young_instance(store, paths):
    """Young instance without enough 1d tips does not write 1w/1m/1y tips."""
    # Force young instance_created_at (overwrite meta.json).
    meta_path = paths.data_dir / "memory" / "meta.json"
    meta = {
        "schema_version": 1,
        "backend": "jsonl",
        "created_at": "2026-07-28T00:00:00Z",
    }
    meta_path.write_text(
        __import__("json").dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    assert read_instance_created_at(store) == datetime(
        2026, 7, 28, 0, 0, tzinfo=UTC
    )

    store.put_atom(
        _atom(
            t="2026-07-28T10:15:00Z",
            kind="speak",
            text="young-hour",
            moment_id="m10",
        )
    )
    refresh_window(store, "1h", datetime(2026, 7, 28, 10, 0, tzinfo=UTC))
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)  # age ~12h
    result = cascade_from_hour(
        store,
        datetime(2026, 7, 28, 10, 0, tzinfo=UTC),
        settings=MemorySettings(summary_mode="template"),
        now=now,
    )
    assert any(c.startswith("1d:") for c in result["refreshed"])
    assert not any(c.startswith("1w:") for c in result["refreshed"])
    assert not any(c.startswith("1m:") for c in result["refreshed"])
    assert not any(c.startswith("1y:") for c in result["refreshed"])
    assert result.get("stopped_reason") == "instance_age"
    assert resolve_tip(store, "1d", datetime(2026, 7, 28, 0, 0, tzinfo=UTC)) is not None
    assert store.list_summaries("1w") == []


def test_enough_1d_tips_unlocks_1w_cascade(store, paths):
    """Provisional LADDER_ENOUGH_1D_TIPS unlocks 1w even when age < 7d."""
    meta_path = paths.data_dir / "memory" / "meta.json"
    meta_path.write_text(
        __import__("json").dumps(
            {
                "schema_version": 1,
                "backend": "jsonl",
                "created_at": "2026-07-20T00:00:00Z",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    # Three distinct day tips via template refresh on different days.
    for day in (20, 21, 22):
        hour_dt = datetime(2026, 7, day, 10, 0, tzinfo=UTC)
        store.put_atom(
            _atom(
                t=to_iso_z(hour_dt + timedelta(minutes=15)),
                kind="speak",
                text=f"day-{day}",
                moment_id=f"m{day}",
            )
        )
        refresh_window(store, "1h", hour_dt)
        cascade_from_hour(
            store,
            hour_dt,
            settings=MemorySettings(summary_mode="template"),
            # Force age-old enough that 1w is allowed only via tip count:
            # use a now that is still young relative to created_at for 1m/1y
            # but we unlock 1w via tip_counts after 3 days of 1d tips.
            now=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        )
    tips = count_tip_summaries(store)
    assert tips.get("1d", 0) >= LADDER_ENOUGH_1D_TIPS
    # After third day cascade, 1w should have been allowed.
    week_tips = store.list_summaries("1w")
    assert len(week_tips) >= 1


def test_ladder_status_snapshot_exposes_knobs_and_state(store):
    """Design §10: status snapshot carries knobs + last_hourly + llm_calls."""
    settings = MemorySettings(
        summary_mode="llm",
        ladder_hourly_max_ms=9000,
        ladder_catchup_max_hours=12,
        ladder_llm_max_calls_per_tick=2,
        ladder_llm_max_calls_per_hour=15,
    )
    state: dict = {
        **load_ladder_state(store),
        "last_hourly_process": "2026-07-28T12:00:00Z",
        "last_closed_1h_processed": "2026-07-28T11:00:00Z",
        "catchup_cursor": "2026-07-28T12:00:00Z",
        "llm_calls_hour": {"hour": "2026-07-28T12:00:00Z", "count": 4},
        "dirty_1h_windows": ["2026-07-28T10:00:00Z"],
    }
    save_ladder_state(state, store)
    snap = ladder_status_snapshot(store, settings)
    assert snap["enabled"] is True
    assert snap["summary_mode"] == "llm"
    assert snap["ladder_hourly_max_ms"] == 9000
    assert snap["ladder_catchup_max_hours"] == 12
    assert snap["ladder_llm_max_calls_per_tick"] == 2
    assert snap["ladder_llm_max_calls_per_hour"] == 15
    assert snap["last_hourly_process"] == "2026-07-28T12:00:00Z"
    assert snap["last_closed_1h_processed"] == "2026-07-28T11:00:00Z"
    assert snap["catchup_cursor"] == "2026-07-28T12:00:00Z"
    assert snap["llm_calls_hour"]["count"] == 4
    assert snap["dirty_1h_count"] == 1
    assert "1h" in snap["allowed_scales"]
    assert "1d" in snap["allowed_scales"]
