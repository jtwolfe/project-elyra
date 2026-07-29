"""Phase 2 PR7 glass Vectors APIs: health, status list, neighbors (read-only)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.goals import GoalsStore
from elyra.identity import IdentityStore
from elyra.llm.client import StubChatClient
from elyra.llm.queue import ChatRequestGate
from elyra.loop.doloop import DoLoopResult
from elyra.memory.config import MemorySettings
from elyra.memory.embed.mock import MockEmbedder
from elyra.memory.embed.types import EmbeddingSet, l2_normalize
from elyra.memory.index import MemoryEmbeddingIndex
from elyra.memory.promote import promote_wake_observation
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import TimerService
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState
from elyra.settings import default_settings
from elyra.skills.catalog import SkillCatalog
from elyra.tools.registry import ToolRegistry
from elyra.users import UsersStore


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


def _fake_registry() -> MagicMock:
    reg = MagicMock()
    reg.openai_tools.return_value = []
    reg.execute.return_value = MagicMock(ok=True, payload={}, ends_moment=False)
    return reg


def _stub_loop(**kwargs: Any) -> DoLoopResult:
    ctx = kwargs.get("ctx")
    mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
    return DoLoopResult(
        stop_reason="no_tools",
        hop_count=1,
        moment_id=mid,
        spoke=False,
    )


class _ApiHarness:
    def __init__(self, paths, *, memory: MemorySettings | None = None) -> None:
        self.paths = paths
        stop = threading.Event()
        queue = WakeQueue(paths)
        timers = TimerService(paths, queue)
        moments = MomentStore(paths)
        goals = GoalsStore(paths)
        settings = default_settings()
        if memory is not None:
            settings = replace(settings, memory=memory)
        self.worker = PresenceWorker(
            paths=paths,
            client=StubChatClient(),
            stop_event=stop,
            poll_seconds=0.05,
            settings=settings,
            queue=queue,
            timers=timers,
            moments=moments,
            registry=_fake_registry(),
            goals=goals,
            run_do_loop_fn=_stub_loop,
        )
        self._stop = stop
        config = RuntimeConfig(api_host="127.0.0.1", api_port=0)
        self.state = RuntimeState()
        self.gate = ChatRequestGate()
        tools: ToolRegistry | None
        skills: SkillCatalog | None
        try:
            tools = ToolRegistry(paths)
        except Exception:  # noqa: BLE001
            tools = None
        try:
            skills = SkillCatalog(paths)
        except Exception:  # noqa: BLE001
            skills = None
        self.server, self._api_thread = start_api_server(
            config,
            paths=paths,
            gate=self.gate,
            state=self.state,
            worker=self.worker,
            goals=goals,
            moments=moments,
            identity=IdentityStore(paths),
            users=UsersStore(paths),
            tools=tools,
            skills=skills,
        )
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def close(self) -> None:
        self._stop.set()
        try:
            self.server.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.server.server_close()
        except Exception:  # noqa: BLE001
            pass

    def get(self, path: str) -> tuple[int, Any]:
        req = urllib.request.Request(self.base + path, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body

    def post(self, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
        data = json.dumps(payload if payload is not None else {}).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "Content-Length": str(len(data))},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body


def test_memory_overview_vectors_live(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/memory")
        assert code == 200
        assert body["tabs"]["vectors"]["stub"] is False
        assert body["tabs"]["vectors"]["phase"] == "2"
        assert body["tabs"]["graph"]["stub"] is True
        assert body["tabs"]["graph"]["phase"] == "2a"
    finally:
        h.close()


def test_vectors_health_defaults(paths):
    h = _ApiHarness(
        paths,
        memory=MemorySettings(enabled=True, write_atoms=True, backend="jsonl"),
    )
    try:
        code, body = h.get("/api/memory/vectors")
        assert code == 200, body
        assert "encoder" in body
        assert "index" in body
        enc = body["encoder"]
        assert "embed_enabled" in enc
        assert "queue_depth" in enc
        assert "semantic_enabled" in enc
        idx = body["index"]
        assert "vectors_ready" in idx
        assert "index_stale" in idx
        assert body["tabs"]["vectors"]["stub"] is False
        # No raw vectors dumped.
        blob = json.dumps(body)
        assert "emb_joint" not in blob
    finally:
        h.close()


def test_vectors_atoms_status_filter(paths):
    h = _ApiHarness(
        paths,
        memory=MemorySettings(
            enabled=True,
            write_atoms=True,
            backend="jsonl",
            semantic_enabled=True,
            embed_enabled=False,
        ),
    )
    try:
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        assert store is not None
        atom = promote_wake_observation(
            store,
            "m_vec_status",
            content="pending seed for vectors glass",
            message_id="msg_vec_1",
            settings=h.worker.settings.memory,
        )
        assert atom is not None
        assert atom.embedding_status == "pending"

        code, body = h.get("/api/memory/vectors/atoms?status=pending&limit=20")
        assert code == 200, body
        assert body["ok"] is True
        assert body["count"] >= 1
        ids = {a["atom_id"] for a in body["atoms"]}
        assert atom.atom_id in ids
        row = next(a for a in body["atoms"] if a["atom_id"] == atom.atom_id)
        assert row["embedding_status"] == "pending"
        assert "text" in row
        assert len(row["text"]) <= 250

        code, ready = h.get("/api/memory/vectors/atoms?status=ready")
        assert code == 200
        assert ready["ok"] is True
        assert atom.atom_id not in {a["atom_id"] for a in ready["atoms"]}

        code, bad = h.get("/api/memory/vectors/atoms?status=not_a_status")
        assert code == 400
        assert bad["ok"] is False
    finally:
        h.close()


def test_vectors_atoms_fail_closed_when_flags_off(paths):
    h = _ApiHarness(
        paths,
        memory=MemorySettings(enabled=False, write_atoms=False, backend="jsonl"),
    )
    try:
        code, body = h.get("/api/memory/vectors/atoms")
        assert code == 200
        assert body["ok"] is False
        assert body["atoms"] == []
        assert body.get("error")
    finally:
        h.close()


def test_neighbors_requires_seed(paths):
    h = _ApiHarness(
        paths,
        memory=MemorySettings(enabled=True, write_atoms=True, backend="jsonl"),
    )
    try:
        code, body = h.get("/api/memory/vectors/neighbors")
        assert code == 400
        assert body["ok"] is False
        assert body["neighbors"] == []
    finally:
        h.close()


def test_vectors_rebuild_endpoint(paths):
    """POST /api/memory/vectors/rebuild rebuilds ANN index (not re-encode)."""
    h = _ApiHarness(
        paths,
        memory=MemorySettings(
            enabled=True,
            write_atoms=True,
            backend="jsonl",
            semantic_enabled=True,
            embed_enabled=True,
            embed_backend="mock",
        ),
    )
    try:
        # Index may be Null on JSONL — rebuild still returns a structured body.
        code, body = h.post("/api/memory/vectors/rebuild", {})
        assert code == 200, body
        assert "ok" in body
        assert "memory" in body
        # KD-R3 rebuild honesty: notes[] always present for glass.
        assert isinstance(body.get("notes"), list), body
        assert isinstance(body.get("note"), str), body
        # optimized may be False on Null index; must not 500.
        assert body.get("error") in (None, "index_unavailable", "store_unavailable") or (
            body.get("ok") is True or body.get("optimized") is False
        )
        # JSONL → NullEmbeddingIndex: skip path should explain (not silent empty notes).
        if body.get("optimized") is False and not body.get("error"):
            joined = " ".join(str(n) for n in body["notes"]).lower()
            assert "null" in joined or body["notes"], body
        # Bad max_ms
        code, bad = h.post("/api/memory/vectors/rebuild", {"max_ms": "nope"})
        assert code == 400
        assert bad["ok"] is False
    finally:
        h.close()


def test_neighbors_soft_empty_without_index_vectors(paths):
    """JSONL Null index: neighbor search ok with empty list / omitted_reason."""
    h = _ApiHarness(
        paths,
        memory=MemorySettings(
            enabled=True,
            write_atoms=True,
            backend="jsonl",
            semantic_enabled=True,
            embed_enabled=True,
            embed_backend="mock",
        ),
    )
    try:
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        atom = promote_wake_observation(
            store,
            "m_nei_empty",
            content="no vectors on jsonl null index",
            message_id="msg_nei_e",
            settings=h.worker.settings.memory,
        )
        assert atom is not None
        code, body = h.get(
            f"/api/memory/vectors/neighbors?atom_id={atom.atom_id}&k=5"
        )
        assert code == 200, body
        assert body["ok"] is True
        assert body["neighbors"] == []
        # Soft omit: no_vector / no_index / encoder depending on warm state.
        assert body.get("omitted_reason") in (
            "no_vector",
            "no_index",
            "encoder",
            "encode_failed",
            None,
        )
    finally:
        h.close()


def test_neighbors_with_memory_index_and_mock(paths):
    """Inject MemoryEmbeddingIndex + MockEmbedder — free-text and atom_id hits."""
    h = _ApiHarness(
        paths,
        memory=MemorySettings(
            enabled=True,
            write_atoms=True,
            backend="jsonl",
            semantic_enabled=True,
            embed_enabled=True,
            embed_backend="mock",
        ),
    )
    try:
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        assert store is not None
        a1 = promote_wake_observation(
            store,
            "m_nei_1",
            content="alpha neighbor seed about cats",
            message_id="msg_n1",
            settings=h.worker.settings.memory,
        )
        a2 = promote_wake_observation(
            store,
            "m_nei_2",
            content="beta neighbor seed about dogs",
            message_id="msg_n2",
            settings=h.worker.settings.memory,
        )
        assert a1 and a2

        emb = MockEmbedder()
        h.worker._embedder = emb  # noqa: SLF001
        index = MemoryEmbeddingIndex(store)
        h.worker._embedding_index = index  # noqa: SLF001

        # Upsert joint vectors for both atoms (mock-derived, deterministic).
        for atom in (a1, a2):
            text = atom.content_text or ""
            vec = tuple(emb.encode_text(text))
            joint = tuple(emb.encode_text(f"joint|{text}"))
            ok = index.upsert(
                EmbeddingSet(
                    atom_id=atom.atom_id,
                    emb_text=tuple(l2_normalize(vec)),
                    emb_joint=tuple(l2_normalize(joint)),
                    model_id=emb.model_id,
                    encoded_at="2026-01-01T00:00:00Z",
                )
            )
            assert ok is True
            reloaded = store.get_atom(atom.atom_id)
            assert reloaded is not None
            assert reloaded.embedding_status == "ready"

        # Free-text query near a1.
        code, body = h.get(
            "/api/memory/vectors/neighbors?"
            + "q="
            + urllib.parse.quote("alpha neighbor seed about cats")
            + "&k=5"
        )
        assert code == 200, body
        assert body["ok"] is True
        assert body["count"] >= 1
        ids = [n["atom_id"] for n in body["neighbors"]]
        assert a1.atom_id in ids or a2.atom_id in ids
        for n in body["neighbors"]:
            assert "score" in n
            assert "snippet" in n
            assert "atom_id" in n
            assert len(n["snippet"]) <= 250
        # No raw float vectors in payload.
        assert "emb_joint" not in json.dumps(body)

        # Seed by atom_id excludes self.
        code, by_atom = h.get(
            f"/api/memory/vectors/neighbors?atom_id={a1.atom_id}&k=5"
        )
        assert code == 200, by_atom
        assert by_atom["ok"] is True
        for n in by_atom["neighbors"]:
            assert n["atom_id"] != a1.atom_id
        if by_atom["neighbors"]:
            assert by_atom["neighbors"][0]["atom_id"] == a2.atom_id

        # Missing atom → 404.
        code, missing = h.get(
            "/api/memory/vectors/neighbors?atom_id=a_deadbeefdeadbeefdeadbeefdeadbeef"
        )
        assert code == 404
        assert missing["ok"] is False
    finally:
        h.close()


def test_vectors_status_list_includes_ready_after_index(paths):
    h = _ApiHarness(
        paths,
        memory=MemorySettings(
            enabled=True,
            write_atoms=True,
            backend="jsonl",
            semantic_enabled=True,
            embed_enabled=True,
            embed_backend="mock",
        ),
    )
    try:
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        atom = promote_wake_observation(
            store,
            "m_ready_list",
            content="ready list seed",
            message_id="msg_ready_1",
            settings=h.worker.settings.memory,
        )
        assert atom is not None
        emb = MockEmbedder()
        index = MemoryEmbeddingIndex(store)
        h.worker._embedding_index = index  # noqa: SLF001
        vec = tuple(emb.encode_text(atom.content_text or ""))
        index.upsert(
            EmbeddingSet(
                atom_id=atom.atom_id,
                emb_joint=tuple(l2_normalize(vec)),
                model_id=emb.model_id,
                encoded_at="2026-01-01T00:00:00Z",
            )
        )
        code, body = h.get("/api/memory/vectors/atoms?status=ready&limit=20")
        assert code == 200, body
        assert body["ok"] is True
        ids = {a["atom_id"] for a in body["atoms"]}
        assert atom.atom_id in ids
        row = next(a for a in body["atoms"] if a["atom_id"] == atom.atom_id)
        assert row["embedding_status"] == "ready"
        assert "joint" in (row.get("channels") or []) or row.get("channels") is not None
    finally:
        h.close()
