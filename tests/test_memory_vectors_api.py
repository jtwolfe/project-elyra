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
        assert body["tabs"]["graph"]["stub"] is False
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
        # PR-R5 honesty fields always present (never claim ready without data).
        assert "vectors_by_channel" in idx
        assert isinstance(idx["vectors_by_channel"], dict)
        assert "joint" in idx["vectors_by_channel"]
        assert "joint_repair_remaining" in idx
        assert idx["joint_repair_remaining"] == 0
        assert "ann_index_built" in idx
        assert idx["ann_index_built"] is False or idx["ann_index_built"] is True
        assert "last_optimize_notes" in idx
        assert isinstance(idx["last_optimize_notes"], list)
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
        # Soft omit: no_vector / no_index / encoder / no_hits depending on warm state.
        # Null index may still "search" empty → no_hits after encode_text fallback.
        assert body.get("omitted_reason") in (
            "no_vector",
            "no_index",
            "no_hits",
            "encoder",
            "encode_failed",
            None,
        )
        # PR-R5: default channel auto + resolve fields even on empty.
        assert body["query"]["channel"] == "auto"
        assert body["query"]["resolved_channel"]
        assert body["query"]["channel_reason"]
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
        h.worker._embedder_state = "warm"  # noqa: SLF001 — consumer gated path
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

        # Free-text query near a1 — default channel=auto (PR-R5).
        code, body = h.get(
            "/api/memory/vectors/neighbors?"
            + "q="
            + urllib.parse.quote("alpha neighbor seed about cats")
            + "&k=5"
        )
        assert code == 200, body
        assert body["ok"] is True
        assert body["count"] >= 1
        qblock = body["query"]
        assert qblock["channel"] == "auto"
        assert qblock["resolved_channel"] in ("joint", "text")
        assert qblock["channel_reason"]
        assert "auto_" in qblock["channel_reason"] or qblock["channel_reason"] == "explicit"
        ids = [n["atom_id"] for n in body["neighbors"]]
        assert a1.atom_id in ids or a2.atom_id in ids
        for n in body["neighbors"]:
            assert "score" in n
            assert n.get("score_kind") == "cosine"
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
        assert by_atom["query"]["channel"] == "auto"
        assert by_atom["query"]["resolved_channel"]
        for n in by_atom["neighbors"]:
            assert n["atom_id"] != a1.atom_id
        if by_atom["neighbors"]:
            assert by_atom["neighbors"][0]["atom_id"] == a2.atom_id

        # Explicit channel still works.
        code, joint_only = h.get(
            f"/api/memory/vectors/neighbors?atom_id={a1.atom_id}&k=5&channel=joint"
        )
        assert code == 200, joint_only
        assert joint_only["query"]["channel"] == "joint"
        assert joint_only["query"]["resolved_channel"] == "joint"
        assert joint_only["query"]["channel_reason"] == "explicit"

        # Missing atom → 404.
        code, missing = h.get(
            "/api/memory/vectors/neighbors?atom_id=a_deadbeefdeadbeefdeadbeefdeadbeef"
        )
        assert code == 404
        assert missing["ok"] is False
    finally:
        h.close()


def test_neighbors_auto_resolves_text_only_corpus(paths):
    """Text-only ready vectors: auto → text (or joint after copy); hits not empty."""
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
        a1 = promote_wake_observation(
            store,
            "m_text_only_1",
            content="text only corpus seed alpha",
            message_id="msg_to1",
            settings=h.worker.settings.memory,
        )
        a2 = promote_wake_observation(
            store,
            "m_text_only_2",
            content="text only corpus seed beta",
            message_id="msg_to2",
            settings=h.worker.settings.memory,
        )
        assert a1 and a2
        emb = MockEmbedder()
        h.worker._embedder = emb  # noqa: SLF001
        h.worker._embedder_state = "warm"  # noqa: SLF001
        # Disable eager repair so joint stays empty for this fixture.
        index = MemoryEmbeddingIndex(store, joint_repair_max_per_open=0)
        h.worker._embedding_index = index  # noqa: SLF001
        for atom in (a1, a2):
            text = atom.content_text or ""
            vec = tuple(l2_normalize(emb.encode_text(text)))
            ok = index.upsert(
                EmbeddingSet(
                    atom_id=atom.atom_id,
                    emb_text=vec,
                    emb_joint=None,
                    model_id=emb.model_id,
                    encoded_at="2026-01-01T00:00:00Z",
                )
            )
            assert ok is True

        health = index.health()
        assert health["vectors_by_channel"]["text"] >= 2
        assert health["vectors_by_channel"]["joint"] == 0

        code, body = h.get(
            f"/api/memory/vectors/neighbors?atom_id={a1.atom_id}&k=5&channel=auto"
        )
        assert code == 200, body
        assert body["ok"] is True
        assert body["query"]["channel"] == "auto"
        # Repair remaining may be >0 or auto_text when joint empty.
        assert body["query"]["resolved_channel"] == "text"
        assert "text" in body["query"]["channel_reason"]
        assert body["count"] >= 1
        assert body["omitted_reason"] is None
        ids = [n["atom_id"] for n in body["neighbors"]]
        assert a2.atom_id in ids

        # Explicit joint against text-only seed → no_vector (no encode_text invent).
        code, joint = h.get(
            f"/api/memory/vectors/neighbors?atom_id={a1.atom_id}&k=5&channel=joint"
        )
        assert code == 200, joint
        assert joint["query"]["resolved_channel"] == "joint"
        assert joint["query"]["channel_reason"] == "explicit"
        assert joint["query"]["source"] == "atom"
        assert joint["neighbors"] == []
        assert joint["omitted_reason"] == "no_vector"
        assert joint["count"] == 0
    finally:
        h.close()


def test_neighbors_atom_id_no_encode_text_on_missing_channel(paths):
    """Text-only seed + joint peer: channel=joint must not invent atom_text hits.

    Repro from PR-R5 review Issue 1: encode_text soft path returned near-zero
    cosine noise against joint corpus. Design: missing channel → no_vector.
    """
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
        seed = promote_wake_observation(
            store,
            "m_mix_seed",
            content="mixed corpus text-only seed about cats",
            message_id="msg_mix_seed",
            settings=h.worker.settings.memory,
        )
        peer = promote_wake_observation(
            store,
            "m_mix_peer",
            content="mixed corpus joint-only peer about dogs",
            message_id="msg_mix_peer",
            settings=h.worker.settings.memory,
        )
        assert seed and peer
        emb = MockEmbedder()
        h.worker._embedder = emb  # noqa: SLF001 — warm encoder must not change outcome
        h.worker._embedder_state = "warm"  # noqa: SLF001
        index = MemoryEmbeddingIndex(store, joint_repair_max_per_open=0)
        h.worker._embedding_index = index  # noqa: SLF001

        seed_text = tuple(l2_normalize(emb.encode_text(seed.content_text or "")))
        peer_joint = tuple(
            l2_normalize(emb.encode_text(f"joint|{peer.content_text or ''}"))
        )
        assert index.upsert(
            EmbeddingSet(
                atom_id=seed.atom_id,
                emb_text=seed_text,
                emb_joint=None,
                model_id=emb.model_id,
                encoded_at="2026-01-01T00:00:00Z",
            )
        )
        assert index.upsert(
            EmbeddingSet(
                atom_id=peer.atom_id,
                emb_text=None,
                emb_joint=peer_joint,
                model_id=emb.model_id,
                encoded_at="2026-01-01T00:00:00Z",
            )
        )

        code, body = h.get(
            f"/api/memory/vectors/neighbors?atom_id={seed.atom_id}&k=5&channel=joint"
        )
        assert code == 200, body
        assert body["ok"] is True
        assert body["query"]["channel"] == "joint"
        assert body["query"]["resolved_channel"] == "joint"
        assert body["query"]["channel_reason"] == "explicit"
        assert body["query"]["source"] == "atom"
        assert body["query"]["source"] != "atom_text"
        assert body["neighbors"] == []
        assert body["count"] == 0
        assert body["omitted_reason"] == "no_vector"

        # Free-text q= still encodes and may hit joint peer (design-normative).
        code, free = h.get(
            "/api/memory/vectors/neighbors?"
            + "q="
            + urllib.parse.quote("joint|" + (peer.content_text or ""))
            + "&k=5&channel=joint"
        )
        assert code == 200, free
        assert free["query"]["source"] == "text"
        assert free["omitted_reason"] in (None, "no_hits") or free["count"] >= 0
    finally:
        h.close()


def test_neighbors_query_vector_no_cross_channel_fallback():
    """query_vector_for_atom must not soft-fallback to another channel (PR-R5)."""
    from elyra.memory.inspect import query_vector_for_atom

    emb = MockEmbedder()
    text_v = tuple(l2_normalize(emb.encode_text("only text")))
    es = EmbeddingSet(
        atom_id="a_only_text",
        emb_text=text_v,
        emb_joint=None,
        model_id=emb.model_id,
        encoded_at="2026-01-01T00:00:00Z",
    )

    class _Idx:
        def get(self, atom_id: str):
            return es if atom_id == "a_only_text" else None

    vec, reason = query_vector_for_atom(
        "a_only_text", index=_Idx(), store=None, channel="joint"
    )
    assert vec is None
    assert reason == "no_vector"

    vec_t, reason_t = query_vector_for_atom(
        "a_only_text", index=_Idx(), store=None, channel="text"
    )
    assert vec_t is not None
    assert reason_t is None
    assert len(vec_t) == len(text_v)


def test_vectors_glass_static_wiring():
    """Glass Vectors tab wires channel select + honesty helpers (not identifier-only)."""
    web = Path(__file__).resolve().parents[1] / "elyra" / "runtime" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "app.js").read_text(encoding="utf-8")

    assert 'id="memory-neighbor-channel"' in html
    assert 'value="auto"' in html
    assert 'value="joint"' in html
    assert 'value="text"' in html
    # Wiring: JS reads select and sends channel param (snippet, not bare id).
    assert "memoryNeighborChannel" in js
    assert 'params.set("channel"' in js or "params.set('channel'" in js
    assert "formatAnnHonesty" in js
    assert "formatVectorsByChannel" in js
    assert "renderNeighborsMeta" in js
    assert "joint_repair_remaining" in js
    assert "cosine" in js
    assert "score_kind" in js
    # Honesty copy present in glass.
    assert "full scan still used" in html or "full scan still used" in js


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
