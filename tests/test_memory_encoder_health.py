"""Unit tests for encode worker health block (PR4) — run via pytest path if needed."""
from __future__ import annotations

from types import SimpleNamespace

from elyra.memory.embed.gate import EmbedderGate
from elyra.memory.embed.mock import MockEmbedder
from elyra.memory.embed.queue import EncodePriority, EncodeQueue
from elyra.memory.inspect import encode_worker_health_block, encoder_health_block


def test_encode_worker_health_defaults_without_presence():
    block = encode_worker_health_block(None)
    assert block["owner"] == "none"
    assert block["alive"] is False
    assert block["embedder_state"] == "absent"
    assert block["last_drain_at"] is None
    assert block["gate_lookup_waits"] == 0


def test_encoder_health_includes_priority_and_worker():
    q = EncodeQueue(maxsize=16)
    q.enqueue("x" * 32, priority=EncodePriority.ATOM_CREATE)
    q.enqueue("y" * 32, priority=EncodePriority.CATCHUP)
    gate = EmbedderGate()
    gate.gate_lookup_waits = 1
    gate.gate_lookup_wait_ms_last = 9
    presence = SimpleNamespace(
        settings=SimpleNamespace(
            memory=SimpleNamespace(
                encode_worker_enabled=True,
                embed_enabled=True,
                semantic_enabled=True,
                embed_backend="mock",
                embed_device="auto",
                embed_model_id="",
                encode_queue_max=16,
            )
        ),
        _encode_owner="worker",
        _encode_worker=None,
        _encode_worker_restarts=0,
        _encode_worker_restart_throttled=False,
        _gap_drain_active=False,
        _encode_last_drain_at="2026-01-01T00:00:00Z",
        _encode_last_drain_stats={"ok": 1, "failed": 0, "processed": 1, "ms": 5},
        _encode_drain_ok_total=1,
        _encode_drain_failed_total=0,
        _embedder_gate=gate,
        _embedder_state="warm",
    )
    cfg = presence.settings.memory
    block = encoder_health_block(
        settings=cfg,
        embedder=MockEmbedder(),
        queue=q,
        presence=presence,
    )
    assert block["queue_depth"] == 2
    assert block["queue_depth_by_priority"]["atom_create"] == 1
    assert block["queue_depth_by_priority"]["catchup"] == 1
    ew = block["encode_worker"]
    assert ew["owner"] == "worker"
    assert ew["alive"] is False
    assert ew["last_drain_at"] == "2026-01-01T00:00:00Z"
    assert ew["last_drain_stats"]["ms"] == 5
    assert ew["gate_lookup_waits"] == 1
    assert ew["gate_lookup_wait_ms_last"] == 9
    assert ew["embedder_state"] == "warm"
    assert block["ok"] is True
    # No secret-like keys.
    assert "content_text" not in block
    assert "api_key" not in str(block).lower()


def test_encode_worker_health_ignores_monotonic_last_drain():
    presence = SimpleNamespace(
        settings=None,
        _encode_owner="idle",
        _encode_worker=None,
        _encode_worker_restarts=0,
        _encode_worker_restart_throttled=False,
        _gap_drain_active=False,
        _encode_last_drain_at=12345.67,  # monotonic leftover
        _encode_last_drain_stats=None,
        _encode_drain_ok_total=0,
        _encode_drain_failed_total=0,
        _embedder_gate=None,
        _embedder_state="absent",
    )
    block = encode_worker_health_block(presence)
    assert block["last_drain_at"] is None
