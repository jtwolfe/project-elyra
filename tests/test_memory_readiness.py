"""Hermetic tests for memory fabric readiness aggregate (P4 / KD-MR / KD-GATE).

Covers design §2.3 truth table for ``memory_ready`` and component-gate honesty.
``chat_ready`` is never folded into the aggregate.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.memory.readiness import (
    compute_memory_ready,
    edges_component_ready,
    format_memory_fabric_cli_line,
    need_edges,
    need_embed,
    need_index,
    need_store,
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    p = resolve_paths(tmp_path)
    p.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


# ── Pure formula / need_* ───────────────────────────────────────────────────


def test_need_store_false_when_memory_off():
    assert need_store(enabled=False, write_atoms=False) is False
    assert need_index(enabled=False, write_atoms=False) is False
    assert need_edges(
        enabled=False,
        write_atoms=False,
        backend="lance",
        durable_edges_enabled=True,
    ) is False
    assert need_embed(enabled=False, write_atoms=False, embed_enabled=True) is False


def test_need_edges_truth_table():
    # jsonl + durable false → not required
    assert need_edges(
        enabled=True,
        write_atoms=True,
        backend="jsonl",
        durable_edges_enabled=False,
    ) is False
    # jsonl + durable true → required
    assert need_edges(
        enabled=True,
        write_atoms=True,
        backend="jsonl",
        durable_edges_enabled=True,
    ) is True
    # lance + durable false → still required (read open)
    assert need_edges(
        enabled=True,
        write_atoms=True,
        backend="lance",
        durable_edges_enabled=False,
    ) is True
    # lance + durable true → required
    assert need_edges(
        enabled=True,
        write_atoms=True,
        backend="lance",
        durable_edges_enabled=True,
    ) is True


@pytest.mark.parametrize(
    "kwargs,expect_ready",
    [
        # both false → ready=true (nothing required)
        (
            dict(
                enabled=False,
                write_atoms=False,
                backend="jsonl",
                durable_edges_enabled=False,
                embed_enabled=False,
                store_open=False,
                store_ok=False,
                index_ready=False,
                edges_ready=False,
                embedder_ready=False,
            ),
            True,
        ),
        # jsonl, durable off, embed off → store + index
        (
            dict(
                enabled=True,
                write_atoms=True,
                backend="jsonl",
                durable_edges_enabled=False,
                embed_enabled=False,
                store_open=True,
                store_ok=True,
                index_ready=True,
                edges_ready=False,
                embedder_ready=False,
            ),
            True,
        ),
        # jsonl durable off but store down → not ready
        (
            dict(
                enabled=True,
                write_atoms=True,
                backend="jsonl",
                durable_edges_enabled=False,
                embed_enabled=False,
                store_open=False,
                store_ok=False,
                index_ready=False,
                edges_ready=False,
                embedder_ready=False,
            ),
            False,
        ),
        # jsonl + durable true: needs edges
        (
            dict(
                enabled=True,
                write_atoms=True,
                backend="jsonl",
                durable_edges_enabled=True,
                embed_enabled=False,
                store_open=True,
                store_ok=True,
                index_ready=True,
                edges_ready=True,
                embedder_ready=False,
            ),
            True,
        ),
        # jsonl + durable true, edges down → not ready
        (
            dict(
                enabled=True,
                write_atoms=True,
                backend="jsonl",
                durable_edges_enabled=True,
                embed_enabled=False,
                store_open=True,
                store_ok=True,
                index_ready=True,
                edges_ready=False,
                embedder_ready=False,
            ),
            False,
        ),
        # lance durable false: still needs edges
        (
            dict(
                enabled=True,
                write_atoms=True,
                backend="lance",
                durable_edges_enabled=False,
                embed_enabled=False,
                store_open=True,
                store_ok=True,
                index_ready=True,
                edges_ready=False,
                embedder_ready=False,
            ),
            False,
        ),
        # lance + embed: needs all
        (
            dict(
                enabled=True,
                write_atoms=True,
                backend="lance",
                durable_edges_enabled=True,
                embed_enabled=True,
                store_open=True,
                store_ok=True,
                index_ready=True,
                edges_ready=True,
                embedder_ready=True,
            ),
            True,
        ),
        # edges ready, embedder failed → memory_ready false (KD-GATE component test)
        (
            dict(
                enabled=True,
                write_atoms=True,
                backend="lance",
                durable_edges_enabled=True,
                embed_enabled=True,
                store_open=True,
                store_ok=True,
                index_ready=True,
                edges_ready=True,
                embedder_ready=False,
            ),
            False,
        ),
        # store open but health not ok → not ready (honesty: no false ready)
        (
            dict(
                enabled=True,
                write_atoms=True,
                backend="jsonl",
                durable_edges_enabled=False,
                embed_enabled=False,
                store_open=True,
                store_ok=False,
                index_ready=True,
                edges_ready=False,
                embedder_ready=False,
            ),
            False,
        ),
        # index missing while store required → not ready
        (
            dict(
                enabled=True,
                write_atoms=True,
                backend="jsonl",
                durable_edges_enabled=False,
                embed_enabled=False,
                store_open=True,
                store_ok=True,
                index_ready=False,
                edges_ready=False,
                embedder_ready=False,
            ),
            False,
        ),
    ],
)
def test_memory_ready_truth_table(kwargs, expect_ready):
    result = compute_memory_ready(**kwargs)
    assert result["memory_ready"] is expect_ready
    # atom_store_ready is always store_open AND store_ok
    assert result["atom_store_ready"] is (
        bool(kwargs["store_open"]) and bool(kwargs["store_ok"])
    )


def test_component_gates_edges_without_embedder():
    """edges_ready true, embedder failed → memory_ready false; edges still usable."""
    result = compute_memory_ready(
        enabled=True,
        write_atoms=True,
        backend="lance",
        durable_edges_enabled=True,
        embed_enabled=True,
        store_open=True,
        store_ok=True,
        index_ready=True,
        edges_ready=True,
        embedder_ready=False,
    )
    assert result["edges_ready"] is True
    assert result["embedder_ready"] is False
    assert result["memory_ready"] is False
    assert result["need_embed"] is True
    assert result["need_edges"] is True


def test_edges_component_ready_requires_health_ok():
    """Past bug: ready claimed when backing health.ok false / Unavailable."""

    class Unavailable:
        pass

    handle = object()
    assert (
        edges_component_ready(
            state="ready",
            handle=handle,
            health={"ok": True},
            unavailable_type=Unavailable,
        )
        is True
    )
    # health.ok false (parity) → not ready
    assert (
        edges_component_ready(
            state="ready",
            handle=handle,
            health={"ok": False, "error": "edge_count_parity_mismatch"},
            unavailable_type=Unavailable,
        )
        is False
    )
    # Unavailable handle → not ready even if state says ready
    assert (
        edges_component_ready(
            state="ready",
            handle=Unavailable(),
            health={"ok": True},
            unavailable_type=Unavailable,
        )
        is False
    )
    # opening / absent → not ready
    assert (
        edges_component_ready(
            state="opening",
            handle=None,
            health=None,
            unavailable_type=Unavailable,
        )
        is False
    )
    # missing health → not ready (no false ready when data absent)
    assert (
        edges_component_ready(
            state="ready",
            handle=handle,
            health=None,
            unavailable_type=Unavailable,
        )
        is False
    )


def test_format_memory_fabric_cli_line():
    assert format_memory_fabric_cli_line(
        {
            "enabled": False,
            "write_atoms": False,
            "memory_ready": True,
        }
    ) == "memory:      off"
    assert (
        format_memory_fabric_cli_line(
            {
                "enabled": True,
                "write_atoms": True,
                "memory_ready": True,
                "warming": False,
                "ok": True,
                "store_open": True,
                "edges_ready": True,
                "embedder_state": "warm",
            }
        )
        == "memory:      ready"
    )
    line = format_memory_fabric_cli_line(
        {
            "enabled": True,
            "write_atoms": True,
            "memory_ready": False,
            "warming": True,
            "ok": True,
            "store_open": True,
            "edges_ready": True,
            "embedder_state": "loading",
            "edges_open": {"state": "ready"},
        }
    )
    assert line.startswith("memory:      warming")
    assert "embedder=loading" in line
    deg = format_memory_fabric_cli_line(
        {
            "enabled": True,
            "write_atoms": True,
            "memory_ready": False,
            "warming": False,
            "ok": True,
            "store_open": True,
            "edges_ready": False,
            "embedder_state": "warm",
            "edges": {"state": "unavailable"},
        }
    )
    assert deg.startswith("memory:      degraded")
    assert "edges=unavailable" in deg


# ── Integration via PresenceWorker status block ─────────────────────────────


def _make_worker(paths, *, settings=None):
    """Minimal PresenceWorker for status-block tests (mirrors presence tests)."""
    import threading

    from elyra.llm.client import StubChatClient
    from elyra.loop.doloop import DoLoopResult
    from elyra.moment import MomentStore
    from elyra.presence.queue import WakeQueue
    from elyra.presence.timers import TimerService
    from elyra.presence.worker import PresenceWorker
    from elyra.settings import default_settings

    def _stub(**_k):
        return DoLoopResult(stop_reason="done", hop_count=0)

    stop = threading.Event()
    queue = WakeQueue(paths)
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=stop,
        poll_seconds=0.05,
        settings=settings or default_settings(),
        queue=queue,
        timers=TimerService(paths, queue),
        moments=MomentStore(paths),
        run_do_loop_fn=_stub,
    )
    return worker, stop


def test_memory_status_publishes_aggregate_and_components(paths):
    """Status block exposes memory_ready + component flags after core warm."""
    from elyra.memory.config import MemorySettings
    from elyra.settings import default_settings

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            durable_edges_enabled=True,
            embed_enabled=False,
            embed_preload=False,
        ),
    )
    worker, _stop = _make_worker(paths, settings=settings)
    # Before warm: store closed → memory_ready false (need_store).
    mem0 = worker.status_snapshot()["memory"]
    assert "memory_ready" in mem0
    assert mem0["memory_ready"] is False
    assert mem0["atom_store_ready"] is False
    assert mem0["edges_ready"] is False
    assert mem0["index_ready"] is False
    assert mem0["embedder_ready"] is False
    assert mem0["need_store"] is True
    assert mem0["need_edges"] is True
    assert mem0["need_embed"] is False
    assert "edges" in mem0 and isinstance(mem0["edges"], dict)
    assert "embedder" in mem0 and isinstance(mem0["embedder"], dict)
    assert "index" in mem0 and mem0["index"]["ready"] is False

    worker._warm_memory_core()  # noqa: SLF001
    mem = worker.status_snapshot()["memory"]
    assert mem["store_open"] is True
    assert mem["atom_store_ready"] is True
    assert mem["index_ready"] is True
    # jsonl + durable_edges: edges should open ready with health.ok
    assert mem["edges_open"]["state"] in ("ready", "unavailable")
    if mem["edges_open"]["state"] == "ready":
        assert mem["edges_ready"] is True
        assert mem["edges"]["ready"] is True
        # embed not required → aggregate ready when store+index+edges ok
        assert mem["memory_ready"] is True
        assert mem["embedder_ready"] is False  # absent, not required
        assert mem["need_embed"] is False
    else:
        assert mem["edges_ready"] is False
        assert mem["memory_ready"] is False


def test_memory_ready_false_while_embedder_loading(paths):
    """Warming + embed_enabled: memory_ready false until embedder warm."""
    from elyra.memory.config import MemorySettings
    from elyra.settings import default_settings

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=True,
            enabled=True,
            backend="jsonl",
            durable_edges_enabled=True,
            embed_enabled=True,
            embed_backend="mock",
            embed_preload=True,
        ),
    )
    worker, _stop = _make_worker(paths, settings=settings)
    worker._warm_memory_core()  # noqa: SLF001
    worker._embedder_state = "loading"  # noqa: SLF001
    worker._memory_warming = True  # noqa: SLF001
    mem = worker.status_snapshot()["memory"]
    assert mem["edges_ready"] is True or mem["edges_open"]["state"] != "ready"
    if mem["edges_ready"]:
        assert mem["memory_ready"] is False  # embedder still required
        assert mem["embedder_ready"] is False
        assert mem["warming"] is True
        assert mem["embedder"]["state"] == "loading"

    # Simulate terminal warm
    worker._embedder_state = "warm"  # noqa: SLF001
    worker._memory_warming = False  # noqa: SLF001
    mem2 = worker.status_snapshot()["memory"]
    if mem2["edges_ready"] and mem2["atom_store_ready"] and mem2["index_ready"]:
        assert mem2["memory_ready"] is True
        assert mem2["embedder_ready"] is True


def test_memory_ready_independent_of_chat_ready(paths):
    """memory_ready does not read chat_ready; both can diverge."""
    from elyra.memory.config import MemorySettings
    from elyra.settings import default_settings

    settings = replace(
        default_settings(),
        memory=MemorySettings(
            write_atoms=False,
            enabled=False,
            backend="jsonl",
        ),
    )
    worker, _stop = _make_worker(paths, settings=settings)
    mem = worker.status_snapshot()["memory"]
    # Memory off → aggregate ready (nothing required); chat is separate surface.
    assert mem["memory_ready"] is True
    assert mem["need_store"] is False
    assert "chat_ready" not in mem
