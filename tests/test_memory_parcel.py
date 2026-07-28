"""Parcels for oversized content (Phase 2 PR5 / KD21 / KD23).

Hermetic: jsonl store only. Covers pure split helpers + promote call site.
"""

from __future__ import annotations

import json

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.parcel import (
    make_parent_and_parcels,
    parcel_threshold,
    reconstruct_text,
    should_split_into_parcels,
    split_oversized_text,
)
from elyra.memory.promote import promote_beat, promote_wake_observation
from elyra.memory.store import open_memory_store


@pytest.fixture
def paths(tmp_path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


@pytest.fixture
def store(paths):
    return open_memory_store(paths, MemorySettings(write_atoms=True, backend="jsonl"))


def _long_body(n: int = 12_000, *, sep: str = "\n\n") -> str:
    """Build a body longer than default atom_max/parcel_threshold (8000)."""
    # Paragraph-ish units so natural split can fire.
    unit = "word " * 40  # ~200 chars
    parts = [f"paragraph-{i}: {unit}" for i in range(max(1, n // 200 + 1))]
    text = sep.join(parts)
    # Ensure we exceed n.
    if len(text) < n:
        text = text + ("x" * (n - len(text)))
    return text


# ── Pure split helpers ─────────────────────────────────────────────────────


def test_should_split_default_off():
    """KD23: parcels_enabled defaults false → never split."""
    settings = MemorySettings()  # all Phase 2 flags off
    assert settings.parcels_enabled is False
    assert settings.semantic_enabled is False
    body = _long_body(10_000)
    assert should_split_into_parcels(body, settings) is False


def test_should_split_when_enabled_and_over_threshold():
    settings = MemorySettings(parcels_enabled=True, parcel_threshold_chars=100)
    assert should_split_into_parcels("a" * 101, settings) is True
    assert should_split_into_parcels("a" * 100, settings) is False
    assert should_split_into_parcels("short", settings) is False


def test_split_oversized_text_preserves_full_join():
    text = ("alpha paragraph\n\n" * 50) + ("beta line\n" * 50) + ("hard" * 500)
    chunks = split_oversized_text(text, max_chars=200)
    assert len(chunks) >= 2
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(chunks) == text


def test_split_oversized_text_fits_returns_single():
    assert split_oversized_text("hello", 100) == ["hello"]
    assert split_oversized_text("hello", 0) == ["hello"]


def test_split_prefers_paragraph_then_line():
    # First paragraph ends well before max; second overflows alone after join.
    p1 = "first block\n\n"
    p2 = "second block that is long " + ("y" * 80)
    text = p1 + p2
    chunks = split_oversized_text(text, max_chars=40)
    assert "".join(chunks) == text
    # First cut should land on paragraph boundary when possible.
    assert chunks[0].endswith("\n\n") or "\n" in chunks[0]


def test_make_parent_and_parcels_shapes():
    text = _long_body(500, sep="\n\n")
    parent, children = make_parent_and_parcels(
        text=text,
        max_chars=120,
        kind="model",
        t_start="2026-07-28T10:00:00Z",
        moment_id="m1",
        source_beat_type="model",
        base_meta={"idempotency_key": "k1"},
    )
    assert parent.kind == "model"
    assert parent.moment_id == "m1"
    assert parent.meta.get("has_parcels") is True
    assert parent.meta.get("parcel_count") == len(children)
    assert parent.meta.get("first_parcel_id") == children[0].atom_id
    assert "truncated" not in parent.meta
    assert all(c.kind == "parcel" for c in children)
    assert all(c.parent_atom_id == parent.atom_id for c in children)
    for i, c in enumerate(children):
        assert c.meta["parcel_index"] == i
        assert c.meta["parcel_count"] == len(children)
        assert c.prev_atom_id is None  # linking is promote's job
    assert reconstruct_text(parent, children) == text


# ── Golden: flags off → Phase 1 single truncated atom ──────────────────────


def test_golden_parcels_off_long_body_single_truncated_atom(store):
    """KD23 / PR5 golden: semantic off, parcels off → one truncated atom."""
    settings = MemorySettings(
        write_atoms=True,
        backend="jsonl",
        semantic_enabled=False,
        embed_enabled=False,
        parcels_enabled=False,
        atom_max_chars=8000,
    )
    body = _long_body(12_000)
    assert len(body) > settings.atom_max_chars

    atom = promote_wake_observation(
        store,
        "m_golden",
        content=body,
        message_id="msg_long",
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "observation"
    assert len(atom.content_text) == settings.atom_max_chars
    assert atom.meta.get("truncated") is True
    assert atom.meta.get("has_parcels") is not True

    rows = store.list_by_moment("m_golden")
    assert len(rows) == 1
    assert store.health()["atom_count"] == 1
    assert not any(r.kind == "parcel" for r in rows)


def test_golden_promote_beat_model_parcels_off_truncates(store):
    settings = MemorySettings(
        write_atoms=True,
        parcels_enabled=False,
        atom_max_chars=500,
        model_promote_min_chars=10,
    )
    body = "Z" * 1200
    atom = promote_beat(
        store,
        "m_model",
        {
            "type": "model",
            "content": body,
            "ts": "2026-07-28T11:00:00Z",
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "model"
    assert atom.content_text == body[:500]
    assert atom.meta.get("truncated") is True
    assert len(store.list_by_moment("m_model")) == 1


# ── parcels_enabled=true: parent + children, full text, chain rules ────────


def test_oversized_wake_splits_parent_and_parcels(store):
    settings = MemorySettings(
        write_atoms=True,
        backend="jsonl",
        parcels_enabled=True,
        parcel_threshold_chars=400,
        atom_max_chars=400,
    )
    body = _long_body(1500, sep="\n\n").strip()
    assert len(body) > settings.parcel_threshold_chars

    parent = promote_wake_observation(
        store,
        "m_parcels",
        content=body,
        message_id="msg_p",
        settings=settings,
    )
    assert parent is not None
    assert parent.kind == "observation"
    assert parent.meta.get("has_parcels") is True
    assert parent.meta.get("truncated") is not True
    assert len(parent.content_text) <= settings.parcel_threshold_chars

    rows = store.list_by_moment("m_parcels")
    parcels = [r for r in rows if r.kind == "parcel"]
    experience = [r for r in rows if r.kind != "parcel"]

    assert len(experience) == 1
    assert experience[0].atom_id == parent.atom_id
    assert len(parcels) >= 1
    assert store.health()["atom_count"] == 1 + len(parcels)

    for p in parcels:
        assert p.parent_atom_id == parent.atom_id
        assert p.meta.get("parcel_count") == len(parcels)

    # Full text preserved across parent + parcels (no silent middle loss).
    # Wake path strips content; compare to stripped body.
    assert reconstruct_text(parent, parcels) == body

    # Experience sequential weave: moment_tail is parent, not a parcel.
    tail = store.moment_tail("m_parcels")
    assert tail is not None
    assert tail.atom_id == parent.atom_id
    assert tail.kind == "observation"

    # Parcels form their own prev/next chain (not mixed into parent neighbours).
    ordered = sorted(parcels, key=lambda a: int(a.meta["parcel_index"]))
    if len(ordered) >= 2:
        assert ordered[0].prev_atom_id is None
        assert ordered[0].next_atom_id == ordered[1].atom_id
        for i in range(1, len(ordered)):
            assert ordered[i].prev_atom_id == ordered[i - 1].atom_id
            if i + 1 < len(ordered):
                assert ordered[i].next_atom_id == ordered[i + 1].atom_id
            else:
                assert ordered[i].next_atom_id is None

    # Parent may optionally point at first parcel in meta.
    assert parent.meta.get("first_parcel_id") == ordered[0].atom_id


def test_oversized_speak_splits_when_parcels_enabled(store):
    settings = MemorySettings(
        write_atoms=True,
        parcels_enabled=True,
        parcel_threshold_chars=300,
        atom_max_chars=300,
    )
    body = _long_body(1000)
    content = json.dumps(
        {"ok": True, "transport_ok": True, "text": body, "user_id": "op"}
    )
    parent = promote_beat(
        store,
        "m_speak",
        {
            "type": "tool",
            "name": "speak",
            "ok": True,
            "content": content,
            "ts": "2026-07-28T12:00:00Z",
        },
        settings=settings,
    )
    assert parent is not None
    assert parent.kind == "speak"
    parcels = [r for r in store.list_by_moment("m_speak") if r.kind == "parcel"]
    assert len(parcels) >= 1
    assert reconstruct_text(parent, parcels) == body
    assert store.moment_tail("m_speak").atom_id == parent.atom_id


def test_parcels_excluded_from_global_tail_chain(store):
    settings = MemorySettings(
        write_atoms=True,
        parcels_enabled=True,
        parcel_threshold_chars=200,
        atom_max_chars=200,
        link_across_moments=True,
    )
    body = _long_body(900)
    parent = promote_wake_observation(
        store, "m_a", content=body, message_id="a", settings=settings
    )
    assert parent is not None
    # Global tail must be the experience parent, never a parcel.
    gtail = store.global_tail()
    assert gtail is not None
    assert gtail.kind != "parcel"
    assert gtail.atom_id == parent.atom_id


def test_semantic_on_marks_parent_and_parcels_pending(store):
    """Encode queue picks each via write hooks; promote sets pending when semantic on."""
    settings = MemorySettings(
        write_atoms=True,
        parcels_enabled=True,
        semantic_enabled=True,
        parcel_threshold_chars=250,
        atom_max_chars=250,
    )
    enqueued: list[str] = []

    def hook(atom):
        enqueued.append(atom.atom_id)

    store.set_write_hook(hook)
    body = _long_body(800)
    parent = promote_wake_observation(
        store, "m_sem", content=body, message_id="s1", settings=settings
    )
    assert parent is not None
    assert parent.embedding_status == "pending"

    rows = store.list_by_moment("m_sem")
    parcels = [r for r in rows if r.kind == "parcel"]
    assert parcels
    assert all(p.embedding_status == "pending" for p in parcels)
    # Parent + every parcel put fired the hook.
    assert parent.atom_id in enqueued
    for p in parcels:
        assert p.atom_id in enqueued
    assert len(enqueued) == 1 + len(parcels)


def test_tool_ok_preview_does_not_parcel(store):
    """Tool OK density preview stays short; parcels not applied on OK tools."""
    settings = MemorySettings(
        write_atoms=True,
        parcels_enabled=True,
        parcel_threshold_chars=100,
        atom_max_chars=8000,
        tool_ok_preview_chars=240,
    )
    body = "T" * 5000
    atom = promote_beat(
        store,
        "m_tool",
        {
            "type": "tool",
            "name": "run_shell",
            "ok": True,
            "content": body,
            "ts": "2026-07-28T13:00:00Z",
        },
        settings=settings,
    )
    assert atom is not None
    assert atom.kind == "tool"
    assert atom.meta.get("preview") is True
    assert len(atom.content_text) <= 240
    assert len(store.list_by_moment("m_tool")) == 1


def test_parcel_threshold_helper():
    s = MemorySettings(parcel_threshold_chars=1234)
    assert parcel_threshold(s) == 1234
    s2 = MemorySettings(parcel_threshold_chars=0, atom_max_chars=500)
    assert parcel_threshold(s2) == 500
