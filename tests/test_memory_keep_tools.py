"""Tests for memory_keep_update host builtin + package discovery (#104)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.memory.config import MemorySettings
from elyra.memory.keep_tray import load_directed_keep_tray
from elyra.memory.traverse import ERROR_INVALID_ARGS, ERROR_KEEP_DISABLED, TraversalRegistry
from elyra.settings import default_settings
from elyra.tools.builtin.memory_keep import (
    ERROR_KEEP_UNAVAILABLE,
    memory_keep_update,
)
from elyra.tools.policy import resolve_bundled_tools_root
from elyra.tools.registry import ToolRegistry
from elyra.tools.types import ToolContext


@pytest.fixture
def paths(tmp_path: Path):
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return p


def _keep_settings(**kwargs: Any) -> MemorySettings:
    base = dict(
        directed_keep_enabled=True,
        directed_traversal_enabled=False,
        write_atoms=True,
        backend="jsonl",
    )
    base.update(kwargs)
    return MemorySettings(**base)


def _ctx(
    paths,
    *,
    settings: MemorySettings | None = None,
    extras: dict[str, Any] | None = None,
    inject_ports: bool = True,
    moment_id: str = "m1",
) -> ToolContext:
    mem = settings or _keep_settings()
    full = replace(default_settings(), memory=mem)
    bag: dict[str, Any] = dict(extras or {})
    if inject_ports:
        reg = TraversalRegistry(settings=mem, paths=paths)
        bag.setdefault("traversal", reg)
    return ToolContext(
        paths=paths,
        settings=full,
        moment_id=moment_id,
        user_id="operator",
        extras=bag,
    )


def test_bundled_memory_keep_update_discoverable(paths):
    reg = ToolRegistry(paths, bundled_root=resolve_bundled_tools_root())
    pkg = reg.get("memory_keep_update")
    assert pkg is not None
    assert pkg.source == "bundled"
    assert pkg.meta.kind == "read"
    assert pkg.meta.name == "memory_keep_update"
    assert pkg.runner.entry == "elyra.tools.builtin.memory_keep:memory_keep_update"
    props = (pkg.meta.parameters or {}).get("properties") or {}
    assert "mode" in props
    assert "atom_ids" in props
    assert "remove_ids" in props


def test_missing_extras_keep_unavailable(paths):
    ctx = _ctx(paths, inject_ports=False)
    r = memory_keep_update({"mode": "merge", "atom_ids": ["a1"]}, ctx)
    assert r.ok is False
    assert r.error_reason == ERROR_KEEP_UNAVAILABLE


def test_keep_disabled_fail_closed_no_mutate(paths):
    mem = _keep_settings(directed_keep_enabled=False, directed_traversal_enabled=False)
    now = "2026-07-28T12:00:00Z"
    reg = TraversalRegistry(settings=mem, paths=paths, now_fn=lambda: now)
    # Pre-seed tray via force ensure (flag does not block load)
    tray = reg.ensure_tray()
    tray.merge_confirm(["seed"], now=now)
    before = list(tray.atom_ids())

    full = replace(default_settings(), memory=mem)
    ctx = ToolContext(
        paths=paths,
        settings=full,
        moment_id="m1",
        extras={"traversal": reg},
    )
    r = memory_keep_update({"mode": "replace", "atom_ids": ["x"]}, ctx)
    assert r.ok is False
    assert r.error_reason == ERROR_KEEP_DISABLED
    assert reg.ensure_tray().atom_ids() == before
    assert reg.last_confirmed_keep is None


def test_merge_noop_invalid_args(paths):
    ctx = _ctx(paths)
    r = memory_keep_update({"mode": "merge"}, ctx)
    assert r.ok is False
    assert r.error_reason == ERROR_INVALID_ARGS
    reg: TraversalRegistry = ctx.extras["traversal"]
    # Fail before durable mutate — tray still unloaded or empty
    assert reg.last_confirmed_keep is None


def test_bad_mode_and_types(paths):
    ctx = _ctx(paths)
    r = memory_keep_update({"mode": "upsert", "atom_ids": ["a"]}, ctx)
    assert r.ok is False
    assert r.error_reason == ERROR_INVALID_ARGS

    # Non-list / non-str container rejected; bare str is accepted as single id.
    r2 = memory_keep_update({"mode": "merge", "atom_ids": {"a": 1}}, ctx)
    assert r2.ok is False
    assert r2.error_reason == ERROR_INVALID_ARGS

    r3 = memory_keep_update({"mode": "merge", "atom_ids": [1]}, ctx)
    assert r3.ok is False
    assert r3.error_reason == ERROR_INVALID_ARGS


def test_success_merge_replace_clear_payload(paths):
    ctx = _ctx(paths)
    reg: TraversalRegistry = ctx.extras["traversal"]

    r = memory_keep_update(
        {
            "mode": "merge",
            "atom_ids": ["a1", "a2"],
            "note": "pins for goal",
        },
        ctx,
    )
    assert r.ok is True
    assert r.payload["ok"] is True
    assert r.payload["mode"] == "merge"
    assert set(r.payload["atom_ids"]) == {"a1", "a2"}
    assert r.payload["entry_count"] == 2
    assert r.payload["walk_summary_nl"] == "pins for goal"
    assert r.payload["meal_timing"] == "next_compose"
    assert reg.last_confirmed_keep is not None
    assert set(reg.last_confirmed_keep.keep_ids) == {"a1", "a2"}

    r2 = memory_keep_update(
        {"mode": "merge", "remove_ids": ["a1"]},
        ctx,
    )
    assert r2.ok is True
    assert r2.payload["atom_ids"] == ["a2"]
    assert r2.payload["removed"] == ["a1"]

    r3 = memory_keep_update(
        {"mode": "replace", "atom_ids": ["b1", "b2"], "note": "fresh"},
        ctx,
    )
    assert r3.ok is True
    assert set(r3.payload["atom_ids"]) == {"b1", "b2"}
    assert r3.payload["walk_summary_nl"] == "fresh"

    r4 = memory_keep_update({"mode": "replace", "atom_ids": []}, ctx)
    assert r4.ok is True
    assert r4.payload["atom_ids"] == []
    assert r4.payload["entry_count"] == 0
    assert r4.payload["walk_summary_nl"] is None
    assert reg.last_confirmed_keep is None
    assert reg.get_meal_keep_ids() == ([], None)
    disk = load_directed_keep_tray(paths)
    assert disk.atom_ids() == []


def test_default_mode_is_merge(paths):
    ctx = _ctx(paths)
    r = memory_keep_update({"atom_ids": ["only"]}, ctx)
    assert r.ok is True
    assert r.payload["mode"] == "merge"
    assert r.payload["atom_ids"] == ["only"]
