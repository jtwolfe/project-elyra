"""Memory pure types: validation, summary id stability, window bounds."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from elyra.memory.types import (
    ATOM_KINDS,
    PERIOD_SCALES,
    SCHEMA_VERSION,
    Atom,
    atom_from_dict,
    atom_to_dict,
    new_atom_id,
    stable_summary_id,
    to_iso_z,
    validate_atom,
    window_bounds,
)


def test_new_atom_id_prefix_and_unique():
    a = new_atom_id()
    b = new_atom_id()
    assert a.startswith("a_")
    assert b.startswith("a_")
    assert a != b
    assert len(a) == 2 + 32  # a_ + uuid hex


def test_atom_kinds_and_scales_vocab():
    assert "observation" in ATOM_KINDS
    assert "speak" in ATOM_KINDS
    assert "summary" in ATOM_KINDS
    assert PERIOD_SCALES == frozenset({"15m", "1h", "6h", "1d", "1w", "1m"})


def test_validate_atom_happy_path():
    atom = Atom(
        atom_id=new_atom_id(),
        t_start="2026-07-28T12:00:00Z",
        kind="speak",
        content_text="hello",
        content_ref="inline",
        moment_id="m1",
    )
    assert validate_atom(atom) is atom
    assert atom.schema_version == SCHEMA_VERSION
    assert atom.embedding_status == "none"
    assert atom.qualia is None


def test_validate_atom_rejects_bad_kind():
    atom = Atom(
        atom_id="a_x",
        t_start="2026-07-28T12:00:00Z",
        kind="not_a_kind",
        content_text="x",
    )
    with pytest.raises(ValueError, match="invalid kind"):
        validate_atom(atom)


def test_validate_atom_rejects_non_v1_schema():
    atom = Atom(
        atom_id="a_x",
        t_start="2026-07-28T12:00:00Z",
        kind="speak",
        content_text="x",
        schema_version=2,
    )
    with pytest.raises(ValueError, match="schema_version"):
        validate_atom(atom)


def test_stable_summary_id_z_and_offset_equivalent():
    """Helper is sole normative source; Z and +00:00 yield the same id."""
    a = stable_summary_id("15m", "2026-07-28T12:00:00Z")
    b = stable_summary_id("15m", "2026-07-28T12:00:00+00:00")
    c = stable_summary_id("15m", datetime(2026, 7, 28, 12, 0, tzinfo=UTC))
    assert a == b == c



def test_validate_summary_requires_windows():
    atom = Atom(
        atom_id="as_x",
        t_start="2026-07-28T12:00:00Z",
        kind="summary",
        scale="15m",
        content_text="s",
    )
    with pytest.raises(ValueError, match="window_start"):
        validate_atom(atom)

    ok = Atom(
        atom_id="as_y",
        t_start="2026-07-28T12:00:00Z",
        kind="summary",
        scale="15m",
        window_start="2026-07-28T12:00:00Z",
        window_end="2026-07-28T12:15:00Z",
        content_text="s",
    )
    validate_atom(ok)


def test_stable_summary_id_deterministic():
    start = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    a = stable_summary_id("15m", start)
    b = stable_summary_id("15m", "2026-07-28T12:00:00Z")
    c = stable_summary_id("1h", start)
    assert a.startswith("as_")
    assert a == b
    assert a != c
    assert len(a) == 3 + 20  # as_ + 20 hex


def test_window_bounds_15m():
    t = datetime(2026, 7, 28, 12, 17, 30, tzinfo=UTC)
    start, end = window_bounds("15m", t)
    assert start == datetime(2026, 7, 28, 12, 15, tzinfo=UTC)
    assert end == datetime(2026, 7, 28, 12, 30, tzinfo=UTC)


def test_window_bounds_1h():
    t = datetime(2026, 7, 28, 12, 45, tzinfo=UTC)
    start, end = window_bounds("1h", t)
    assert start == datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    assert end == datetime(2026, 7, 28, 13, 0, tzinfo=UTC)


def test_window_bounds_6h():
    t = datetime(2026, 7, 28, 14, 0, tzinfo=UTC)
    start, end = window_bounds("6h", t)
    assert start == datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    assert end == datetime(2026, 7, 28, 18, 0, tzinfo=UTC)


def test_window_bounds_1d():
    t = datetime(2026, 7, 28, 23, 59, tzinfo=UTC)
    start, end = window_bounds("1d", t)
    assert start == datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 7, 29, 0, 0, tzinfo=UTC)


def test_window_bounds_1w_monday():
    # 2026-07-28 is a Tuesday → week starts Monday 2026-07-27
    t = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    start, end = window_bounds("1w", t)
    assert start == datetime(2026, 7, 27, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 3, 0, 0, tzinfo=UTC)


def test_window_bounds_1m():
    t = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    start, end = window_bounds("1m", t)
    assert start == datetime(2026, 7, 1, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 8, 1, 0, 0, tzinfo=UTC)


def test_window_bounds_invalid_scale():
    with pytest.raises(ValueError, match="invalid period scale"):
        window_bounds("2h", datetime.now(UTC))


def test_atom_roundtrip_dict():
    atom = Atom(
        atom_id=new_atom_id(),
        t_start="2026-07-28T12:00:00Z",
        kind="tool",
        content_text="ok",
        media_ids=("att_1", "att_2"),
        meta={"tool_name": "run_cmd", "ok": True},
        moment_id="m1",
        prev_atom_id="a_prev",
    )
    row = atom_to_dict(atom)
    assert row["media_ids"] == ["att_1", "att_2"]
    restored = atom_from_dict(row)
    assert restored.atom_id == atom.atom_id
    assert restored.media_ids == ("att_1", "att_2")
    assert restored.meta["tool_name"] == "run_cmd"
    assert restored.prev_atom_id == "a_prev"


def test_content_ref_is_locator_not_prose():
    """KD18: content_text is render; content_ref is locator only."""
    atom = Atom(
        atom_id=new_atom_id(),
        t_start=to_iso_z(datetime(2026, 1, 1, tzinfo=UTC)),
        kind="observation",
        content_text="the body callers render",
        content_ref="inline",
    )
    assert atom.content_text == "the body callers render"
    assert atom.content_ref == "inline"
