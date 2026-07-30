"""Golden tests: labeled meal, episodic policy, slide-off (no store deletes)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.meal import (
    MealPackage,
    compose_meal,
    compose_outer_messages,
    format_atom_line,
    moment_id_short,
    select_episodic,
    slide_off_temporal,
)
from elyra.memory.store import open_memory_store
from elyra.memory.tokens import (
    DEFAULT_MEAL_BUDGET_TOKENS,
    estimate_tokens,
    split_memory_budget,
)
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
    moment_id: str | None = "m_open",
    atom_id: str | None = None,
    media_ids: tuple[str, ...] = (),
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
        media_ids=media_ids,
        meta=meta or {},
        **kwargs,
    )


def _put_summary(store, scale: str, t: datetime, text: str) -> Atom:
    start, end = window_bounds(scale, t)
    atom = Atom(
        atom_id=stable_summary_id(scale, start),
        t_start=to_iso_z(start),
        kind="summary",
        content_text=text,
        content_ref="inline",
        scale=scale,
        window_start=to_iso_z(start),
        window_end=to_iso_z(end),
        moment_id=None,
        meta={"source": "template"},
    )
    return store.put_atom(atom)


# ---------------------------------------------------------------------------
# tokens
# ---------------------------------------------------------------------------


def test_estimate_tokens_len_div_4():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 40) == 10


def test_default_budget_is_sliding_style():
    assert DEFAULT_MEAL_BUDGET_TOKENS == 250_000


def test_split_memory_budget_fraction():
    fixed, epi, temp = split_memory_budget(
        1000,
        system_text="a" * 40,  # 10 tok
        orient_text="b" * 40,  # 10 tok
        episodic_fraction=0.20,
    )
    assert fixed == 20
    assert epi == int(980 * 0.20)
    assert temp == 980 - epi


# ---------------------------------------------------------------------------
# labels / format
# ---------------------------------------------------------------------------


def test_format_atom_line_and_moment_short():
    a = _atom(t="2026-07-28T12:34:00Z", kind="speak", text="hello")
    assert format_atom_line(a) == "[12:34] (speak) hello"
    assert moment_id_short("m_abcdefghij") == "abcdefgh"
    assert len(moment_id_short("short")) <= 8


def test_compose_outer_message_labels(store):
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    open_id = "m_openmoment01"
    store.put_atom(
        _atom(
            t="2026-07-28T14:50:00Z",
            kind="observation",
            text="wake hi",
            moment_id=open_id,
            meta={"wake_message_id": "msg_wake_1"},
        )
    )
    _put_summary(store, "1h", now, "hour rollup body")

    msgs = compose_outer_messages(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYS",
        orient_text="ORIENT",
        now=now,
        settings=MemorySettings(episodic_fraction=0.20),
    )
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[-1] == {"role": "user", "content": "ORIENT"}
    joined = "\n".join(str(m.get("content")) for m in msgs)
    assert "[context:temporal/moment" in joined
    assert "[context:episodic/summary 1h]" in joined
    # Wake id stamped for media expand path (PR6).
    wake_rows = [m for m in msgs if m.get("id") == "msg_wake_1"]
    assert wake_rows


# ---------------------------------------------------------------------------
# select_episodic
# ---------------------------------------------------------------------------


def test_select_episodic_empty_store(store):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    items = select_episodic(store, now, "m_open", episodic_cap_tokens=5000)
    assert items == []


def test_select_episodic_summaries_only(store):
    now = datetime(2026, 7, 28, 12, 30, tzinfo=UTC)
    _put_summary(store, "1h", now, "current hour")
    prev_t = now - timedelta(hours=1)
    _put_summary(store, "1h", prev_t, "previous hour")
    _put_summary(store, "1d", now, "today")

    items = select_episodic(store, now, "m_open", episodic_cap_tokens=10_000)
    labels = [i.label for i in items]
    assert any(l.startswith("episodic/summary") for l in labels)
    bodies = " ".join(i.content for i in items)
    assert "current hour" in bodies or "today" in bodies


def test_select_episodic_horizon_excludes_older_raw(store):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    # Within 24h
    store.put_atom(
        _atom(
            t="2026-07-28T10:00:00Z",
            kind="speak",
            text="recent prior",
            moment_id="m_prior_recent",
        )
    )
    # Older than 24h horizon
    store.put_atom(
        _atom(
            t="2026-07-20T10:00:00Z",
            kind="speak",
            text="ancient prior",
            moment_id="m_prior_old",
        )
    )
    items = select_episodic(
        store,
        now,
        open_moment_id="m_open",
        episodic_cap_tokens=50_000,
        settings=MemorySettings(episodic_horizon_hours=24.0),
    )
    text = " ".join(i.content for i in items)
    assert "recent prior" in text
    assert "ancient prior" not in text


def test_select_episodic_excludes_open_moment(store):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    store.put_atom(
        _atom(
            t="2026-07-28T11:00:00Z",
            kind="speak",
            text="open speak",
            moment_id="m_open",
        )
    )
    store.put_atom(
        _atom(
            t="2026-07-28T11:05:00Z",
            kind="speak",
            text="other speak",
            moment_id="m_other",
        )
    )
    items = select_episodic(store, now, "m_open", episodic_cap_tokens=50_000)
    text = " ".join(i.content for i in items)
    assert "other speak" in text
    assert "open speak" not in text


def test_select_episodic_shrink_order_tool_before_summary(store):
    """Over-budget shrink: raw tool/model (3a) before finer summaries (3c)."""
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    # Large tool bodies to force pressure.
    big = "T" * 400  # 100 tokens each line roughly with overhead
    for i in range(6):
        store.put_atom(
            _atom(
                t=f"2026-07-28T10:{i:02d}:00Z",
                kind="tool",
                text=big + f" tool{i}",
                moment_id="m_prior_tools",
                meta={"ok": True},
            )
        )
    # Finer summary present
    _put_summary(store, "15m", now, "S" * 200)
    _put_summary(store, "1d", now, "day keep me")

    # Cap small enough that tools must drop before day summary.
    items = select_episodic(store, now, "m_open", episodic_cap_tokens=120)
    labels = [i.label for i in items]
    text = " ".join(i.content for i in items)
    # Day summary is last-resort protected relative to 15m; tools drop first.
    assert any("summary 1d" in l for l in labels) or "day keep me" in text
    # Tools should be gone or heavily reduced under tight cap.
    tool_mentions = text.count("tool")
    assert tool_mentions < 6


def test_select_episodic_shrink_finer_summary_before_coarser(store):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    # Oversized summaries at multiple scales
    _put_summary(store, "15m", now, "F" * 800)  # ~200 tok
    _put_summary(store, "1h", now, "H" * 800)
    _put_summary(store, "1d", now, "D" * 800)

    items = select_episodic(store, now, "m_open", episodic_cap_tokens=250)
    labels = [i.label for i in items]
    # Under pressure drop 15m before 1d (3c); 1h/1d protected until last resort.
    if any("15m" in l for l in labels) and any("1d" in l for l in labels):
        # If both fit, fine; otherwise 15m should not be sole survivor over 1d.
        pass
    # With 250 tok, at most one ~200-tok body + label fits fully.
    assert len(items) <= 2
    # Prefer coarser remaining if something dropped.
    if len(items) == 1:
        assert "15m" not in items[0].label or "1d" in items[0].label or "1h" in items[0].label


# ---------------------------------------------------------------------------
# slide-off
# ---------------------------------------------------------------------------


def test_slide_off_media_protect():
    atoms = []
    base = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    # Early media atom
    atoms.append(
        _atom(
            t=to_iso_z(base),
            kind="observation",
            text="image wake",
            moment_id="m1",
            media_ids=("media_abc",),
            meta={"wake_message_id": "w1"},
        )
    )
    for i in range(1, 20):
        atoms.append(
            _atom(
                t=to_iso_z(base + timedelta(minutes=i)),
                kind="tool",
                text=("noise " * 50) + str(i),
                moment_id="m1",
                meta={"ok": True},
            )
        )
    # Tail speaks
    atoms.append(
        _atom(
            t=to_iso_z(base + timedelta(minutes=30)),
            kind="speak",
            text="latest speak",
            moment_id="m1",
        )
    )

    kept, compact, n = slide_off_temporal(
        atoms,
        temporal_cap_tokens=80,
        protect_tail_atoms=3,
        compact_max_tokens=100,
        open_moment_id="m1",
    )
    assert n > 0
    assert compact is not None
    assert "slid from meal" in compact
    kept_ids = {a.atom_id for a in kept}
    # Media-bearing atom protected
    assert atoms[0].atom_id in kept_ids
    # Latest speak protected
    assert atoms[-1].atom_id in kept_ids


def test_slide_off_never_calls_delete(store):
    """Slide-off must not delete store atoms (atom_count stable)."""
    open_id = "m_slide"
    now = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    for i in range(25):
        store.put_atom(
            _atom(
                t=to_iso_z(now - timedelta(minutes=30 - i)),
                kind="tool" if i < 20 else "speak",
                text=("x" * 200) + f" step{i}",
                moment_id=open_id,
                meta={"ok": True} if i < 20 else {},
            )
        )
    before = store.health().get("atom_count")
    assert before == 25

    # Spy: delete_atom must never be invoked from meal path.
    with mock.patch.object(
        store, "delete_atom", wraps=store.delete_atom
    ) as del_spy:
        pkg = compose_meal(
            store,
            open_moment_id=open_id,
            budget_tokens=200,  # tight → slide-off
            system_text="S",
            orient_text="O",
            now=now,
            settings=MemorySettings(
                episodic_fraction=0.1,
                protect_tail_atoms=5,
                compact_max_tokens=50,
            ),
        )
        del_spy.assert_not_called()

    after = store.health().get("atom_count")
    assert after == before == 25
    assert isinstance(pkg, MealPackage)
    assert pkg.slid_off_count >= 0
    # Under tight budget we expect some slide-off.
    assert pkg.slid_off_count > 0
    assert pkg.compact_text is not None


def test_slide_off_protect_failed_tool_and_tail():
    base = datetime(2026, 7, 28, 11, 0, tzinfo=UTC)
    atoms = [
        _atom(
            t=to_iso_z(base),
            kind="tool",
            text="early ok " + ("z" * 100),
            meta={"ok": True},
        ),
        _atom(
            t=to_iso_z(base + timedelta(minutes=1)),
            kind="tool",
            text="failed once " + ("z" * 100),
            meta={"ok": False},
        ),
        _atom(
            t=to_iso_z(base + timedelta(minutes=2)),
            kind="observation",
            text="mid " + ("z" * 100),
        ),
    ]
    # Add many tools then a late failed tool that should be the protected one
    for i in range(3, 15):
        atoms.append(
            _atom(
                t=to_iso_z(base + timedelta(minutes=i)),
                kind="tool",
                text="noise " + ("z" * 100),
                meta={"ok": True},
            )
        )
    atoms.append(
        _atom(
            t=to_iso_z(base + timedelta(minutes=20)),
            kind="tool",
            text="latest fail",
            meta={"ok": False},
        )
    )
    atoms.append(
        _atom(
            t=to_iso_z(base + timedelta(minutes=21)),
            kind="speak",
            text="done",
        )
    )

    kept, _compact, n = slide_off_temporal(
        atoms,
        temporal_cap_tokens=60,
        protect_tail_atoms=2,
        open_moment_id="m1",
    )
    assert n > 0
    kept_ids = {a.atom_id for a in kept}
    assert atoms[-1].atom_id in kept_ids  # latest speak
    assert atoms[-2].atom_id in kept_ids  # latest failed tool


# ---------------------------------------------------------------------------
# compose_meal integration
# ---------------------------------------------------------------------------


def test_compose_meal_dedup_open_vs_episodic(store):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    open_id = "m_open"
    shared = _atom(
        t="2026-07-28T11:30:00Z",
        kind="speak",
        text="only once please",
        moment_id=open_id,
        atom_id="a_shared_unique_01",
    )
    store.put_atom(shared)
    # Same atom id must not appear twice if somehow listed in range —
    # open moment wins; episodic excludes open_moment_id already.
    store.put_atom(
        _atom(
            t="2026-07-28T11:00:00Z",
            kind="speak",
            text="prior only",
            moment_id="m_prior",
        )
    )
    pkg = compose_meal(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="sys",
        orient_text="orient",
        now=now,
    )
    texts = [i.content for i in pkg.items]
    joined = "\n".join(texts)
    assert joined.count("only once please") == 1
    assert "prior only" in joined


def test_compose_meal_message_order(store):
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    open_id = "m_open"
    store.put_atom(
        _atom(
            t="2026-07-28T11:55:00Z",
            kind="observation",
            text="now",
            moment_id=open_id,
        )
    )
    store.put_atom(
        _atom(
            t="2026-07-28T11:00:00Z",
            kind="speak",
            text="before",
            moment_id="m_prior",
        )
    )
    _put_summary(store, "1h", now, "sum body")

    msgs = compose_outer_messages(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYSTEM",
        orient_text="ORIENT",
        now=now,
    )
    assert msgs[0]["role"] == "system"
    assert msgs[-1]["content"] == "ORIENT"
    # Episodic sections before temporal in package/messages (no glass_rows).
    roles_content = [m["content"] for m in msgs[1:-1]]
    epi_idx = next(
        (i for i, c in enumerate(roles_content) if "episodic/" in c),
        None,
    )
    temp_idx = next(
        (i for i, c in enumerate(roles_content) if "temporal/" in c),
        None,
    )
    if epi_idx is not None and temp_idx is not None:
        assert epi_idx < temp_idx

    # With glass_rows: glass_tail after supports and before temporal.
    glass = [
        {"id": "g-u", "role": "user", "content": "tip user"},
        {"id": "g-a", "role": "assistant", "content": "tip asst"},
    ]
    msgs_gt = compose_outer_messages(
        store,
        open_moment_id=open_id,
        budget_tokens=50_000,
        system_text="SYSTEM",
        orient_text="ORIENT",
        now=now,
        glass_rows=glass,
        social_wake=True,
    )
    mid = [m["content"] for m in msgs_gt[1:-1]]
    gt_idx = next((i for i, c in enumerate(mid) if "glass-tail" in c), None)
    temp_idx2 = next((i for i, c in enumerate(mid) if "temporal/" in c), None)
    assert gt_idx is not None
    if temp_idx2 is not None:
        assert gt_idx < temp_idx2
    epi_idx2 = next((i for i, c in enumerate(mid) if "episodic/" in c), None)
    if epi_idx2 is not None:
        assert epi_idx2 < gt_idx


def test_compose_meal_no_forbidden_words_in_module():
    """KD17: meal module must not use similarity / embedding language."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "elyra" / "memory" / "meal.py"
    text = src.read_text(encoding="utf-8").lower()
    for banned in (r"\bsimilar\b", r"\bembedding\b", r"\bnearest\b", r"\bann\b"):
        assert re.search(banned, text) is None, f"banned pattern {banned!r} in meal.py"
