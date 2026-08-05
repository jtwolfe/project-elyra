"""Phase 2a PR-A5 glass Graph APIs: overview, session, neighbors, debug POST."""

from __future__ import annotations

import json
import threading
import urllib.error
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
from elyra.memory.promote import promote_wake_observation
from elyra.memory.types import Atom, new_atom_id
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
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(data)),
            },
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


def _enabled_memory(**kwargs: Any) -> MemorySettings:
    base = dict(
        enabled=True,
        write_atoms=True,
        backend="jsonl",
        directed_traversal_enabled=True,
        directed_keep_enabled=False,  # follows via helper when traversal on
        traverse_max_steps=4,
        traverse_max_nodes=24,
        traverse_max_depth=2,
        traverse_max_seeds=6,
        traverse_keep_max=8,
        traverse_expand_max_ms=80,
        traverse_keep_adjacent=False,
    )
    base.update(kwargs)
    return MemorySettings(**base)


def _link_chain(store, texts: list[str], *, moment_id: str = "m_graph") -> list[Atom]:
    atoms: list[Atom] = []
    ids = [new_atom_id() for _ in texts]
    for i, text in enumerate(texts):
        a = Atom(
            atom_id=ids[i],
            t_start=f"2026-07-28T10:0{i}:00Z",
            kind="observation",
            content_text=text,
            content_ref="inline",
            moment_id=moment_id,
            prev_atom_id=ids[i - 1] if i > 0 else None,
            next_atom_id=ids[i + 1] if i + 1 < len(ids) else None,
        )
        atoms.append(store.put_atom(a))
    return atoms


# ── Overview + tabs ──────────────────────────────────────────────────────────


def test_memory_overview_graph_live(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/memory")
        assert code == 200
        assert body["tabs"]["graph"]["stub"] is False
        assert body["tabs"]["graph"]["phase"] == "2a"
        assert body["tabs"]["vectors"]["stub"] is False
    finally:
        h.close()


def test_graph_overview_flags_off_honesty(paths):
    h = _ApiHarness(
        paths,
        memory=MemorySettings(
            enabled=True,
            write_atoms=True,
            backend="jsonl",
            directed_traversal_enabled=False,
        ),
    )
    try:
        code, body = h.get("/api/memory/graph")
        assert code == 200, body
        assert body["tabs"]["graph"]["stub"] is False
        assert body["tabs"]["graph_free_browse"]["stub"] is False
        assert "GET /api/memory/graph/neighbors" in body["tabs"]["graph_free_browse"]["api"]
        assert body["traversal"]["directed_traversal_enabled"] is False
        assert body["has_active"] is False
        assert body["has_last_session"] is False
        assert isinstance(body["edge_kind_legend"], list)
        assert len(body["edge_kind_legend"]) >= 4
        kinds = {e["kind"] for e in body["edge_kind_legend"]}
        assert "sequential" in kinds
        assert "semantic_hop" in kinds
        assert body["honesty"]["flag_off"] is True
        assert body["honesty"]["note"]
        # Empty EdgeStore honesty for free-browse (#61 / PR8).
        assert body["honesty"]["edge_store_empty"] is True
        assert body["honesty"]["projected_edges_only"] is True
        assert body["edge_count"] == 0
        assert "EdgeStore empty" in body["honesty"]["note"]
        # No multi-hop wall-clock field.
        blob = json.dumps(body)
        assert "wall_ms" not in blob
        assert "wall_clock" not in blob
    finally:
        h.close()


def test_graph_session_empty_when_no_walk(paths):
    h = _ApiHarness(paths, memory=_enabled_memory())
    try:
        code, body = h.get("/api/memory/graph/session")
        assert code == 200, body
        assert body["ok"] is True
        assert body["which"] == "none"
        assert body["session"] is None
        assert body["honesty"]["no_session"] is True
    finally:
        h.close()


# ── Neighbors ────────────────────────────────────────────────────────────────


def test_graph_neighbors_requires_atom_id(paths):
    h = _ApiHarness(paths, memory=_enabled_memory())
    try:
        code, body = h.get("/api/memory/graph/neighbors")
        assert code == 400
        assert body["ok"] is False
        assert body["neighbors"] == []
    finally:
        h.close()


def test_graph_neighbors_structural_chain(paths):
    h = _ApiHarness(paths, memory=_enabled_memory())
    try:
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        assert store is not None
        atoms = _link_chain(
            store,
            ["alpha node body", "beta middle node", "gamma end node"],
        )
        mid = atoms[1].atom_id
        code, body = h.get(f"/api/memory/graph/neighbors?atom_id={mid}&k=8")
        assert code == 200, body
        assert body["ok"] is True
        assert body["count"] >= 1
        dsts = {n["atom_id"] for n in body["neighbors"]}
        assert atoms[0].atom_id in dsts or atoms[2].atom_id in dsts
        for n in body["neighbors"]:
            assert "edge_kind" in n
            assert "weight" in n
            assert "snippet" in n
            assert "src_atom_id" in n  # free-browse canvas needs src for links
            # No raw embedding dump.
            assert "emb_joint" not in n
        # Free-browse honesty: empty EdgeStore still returns projected edges.
        code, overview = h.get("/api/memory/graph")
        assert code == 200
        assert overview["honesty"]["projected_edges_only"] is True
        assert overview["edge_count"] == 0
        # Neighbor expand still works with projected sequential links only.
        projected_kinds = {n["edge_kind"] for n in body["neighbors"]}
        assert "sequential" in projected_kinds or "same_moment" in projected_kinds
        # Missing atom → 404
        code, missing = h.get("/api/memory/graph/neighbors?atom_id=a_does_not_exist")
        assert code == 404
        assert missing["ok"] is False
    finally:
        h.close()


def test_graph_neighbors_soft_empty_unknown_reasons(paths):
    """Promote atom without links → empty neighbors with honest omit reason."""
    h = _ApiHarness(paths, memory=_enabled_memory())
    try:
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        atom = promote_wake_observation(
            store,
            "m_iso",
            content="isolated observation no links",
            message_id="msg_iso",
            settings=h.worker.settings.memory,
        )
        assert atom is not None
        code, body = h.get(
            f"/api/memory/graph/neighbors?atom_id={atom.atom_id}&allow_semantic=0"
        )
        assert code == 200, body
        assert body["ok"] is True
        # May be empty (no structural neighbors) — honesty required.
        if not body["neighbors"]:
            assert body.get("omitted_reason")
    finally:
        h.close()


# ── POST debug: flags-off fail-closed + budget parity ────────────────────────


def test_graph_traverse_post_flags_off_fail_closed(paths):
    """When directed_traversal_enabled is false, POST fails closed (tools parity)."""
    h = _ApiHarness(
        paths,
        memory=MemorySettings(
            enabled=True,
            write_atoms=True,
            backend="jsonl",
            directed_traversal_enabled=False,
        ),
    )
    try:
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        atoms = _link_chain(store, ["seed a", "seed b", "seed c"])
        code, body = h.post(
            "/api/memory/graph/traverse",
            {
                "action": "start",
                "goal": "should not start",
                "seed_atom_ids": [atoms[0].atom_id],
            },
        )
        assert code == 200, body
        assert body["ok"] is False
        assert body["error_reason"] == "traverse_disabled"
        assert body.get("status") == "disabled"
        # Sticky state untouched — still no session.
        code, sess = h.get("/api/memory/graph/session")
        assert code == 200
        assert sess["which"] == "none"
        assert sess["session"] is None
    finally:
        h.close()


def test_graph_traverse_start_step_finish_session_view(paths):
    """Flags on: debug POST uses same registry; glass sees considered vs kept."""
    h = _ApiHarness(paths, memory=_enabled_memory())
    try:
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        atoms = _link_chain(
            store,
            [
                "memory about apples and orchards",
                "memory about cider press",
                "memory about autumn harvest",
                "memory about barrels",
            ],
        )
        # Start with explicit seeds.
        code, start = h.post(
            "/api/memory/graph/traverse",
            {
                "action": "start",
                "goal": "apples",
                "seed_atom_ids": [atoms[1].atom_id, atoms[0].atom_id],
            },
        )
        assert code == 200, start
        assert start["ok"] is True
        assert start["status"] == "active"
        sid = start["session_id"]
        assert sid

        # Active session view has considered (not meal-thin).
        code, active = h.get("/api/memory/graph/session")
        assert code == 200, active
        assert active["which"] == "active"
        assert active["session"] is not None
        assert active["session"]["session_id"] == sid
        assert active["session"]["considered_count"] >= 1
        assert "budgets" in active["session"]
        budgets = active["session"]["budgets"]
        assert "steps_spent" in budgets
        assert "nodes_spent" in budgets
        assert "depth_spent" in budgets
        assert "expand_ms_budget" in budgets
        assert "expand_ms_spent_last" in budgets
        assert active["session"].get("idle_age_s") is not None
        # No wall-clock countdown on glass session.
        assert "wall_ms_remaining" not in active["session"]
        assert "wall_clock_ms" not in active["session"]

        # Step expand middle.
        code, step = h.post(
            "/api/memory/graph/traverse",
            {
                "action": "step",
                "session_id": sid,
                "expand_ids": [atoms[1].atom_id],
            },
        )
        assert code == 200, step
        assert step["ok"] is True

        # Finish with keep set.
        keep = [atoms[1].atom_id]
        code, fin = h.post(
            "/api/memory/graph/traverse",
            {
                "action": "finish",
                "session_id": sid,
                "keep_ids": keep,
                "summary_hint": "glass debug finish",
            },
        )
        assert code == 200, fin
        assert fin["ok"] is True
        assert fin["status"] == "confirmed"
        assert atoms[1].atom_id in fin.get("keep_ids", fin.get("keep_set", []))

        # After finish: default session view is last_session with considered vs kept.
        code, last = h.get("/api/memory/graph/session")
        assert code == 200, last
        assert last["which"] == "last"
        assert last["has_active"] is False
        assert last["has_last_session"] is True
        sess = last["session"]
        assert sess is not None
        assert sess["status"] == "confirmed"
        assert sess["walk_summary_nl"]
        assert sess["considered_count"] >= 1
        assert isinstance(sess["considered"], list)
        assert any(c.get("kept") for c in sess["considered"]) or atoms[1].atom_id in sess[
            "keep_ids"
        ]
        # meal_keep_ids present as thin side field (KD-A19 separation).
        assert last["meal_keep_count"] >= 1
        assert atoms[1].atom_id in last["meal_keep_ids"]

        # which=meal never collapses glass to keep-only session body.
        code, meal = h.get("/api/memory/graph/session?which=meal")
        assert code == 200, meal
        assert meal["which"] == "none"
        assert meal["session"] is None
        assert meal["meal_keep_count"] >= 1
    finally:
        h.close()


def test_graph_traverse_budget_enforcement_parity(paths):
    """POST respects traverse_max_steps — same as tools (no bypass)."""
    h = _ApiHarness(
        paths,
        memory=_enabled_memory(
            traverse_max_steps=1,
            traverse_max_expand_per_step=2,
            traverse_max_nodes=24,
        ),
    )
    try:
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        atoms = _link_chain(
            store,
            [f"budget atom {i} text" for i in range(5)],
        )
        code, start = h.post(
            "/api/memory/graph/traverse",
            {
                "action": "start",
                "goal": "budget test",
                "seed_atom_ids": [atoms[2].atom_id],
            },
        )
        assert code == 200, start
        assert start["ok"] is True
        sid = start["session_id"]
        assert start["budget"]["steps_remaining"] == 1 or start["budget"][
            "max_steps"
        ] == 1 or start.get("budget", {}).get("steps_remaining") in (0, 1)

        # First step consumes the only step budget.
        code, step1 = h.post(
            "/api/memory/graph/traverse",
            {
                "action": "step",
                "session_id": sid,
                "expand_ids": [atoms[2].atom_id],
            },
        )
        assert code == 200, step1
        assert step1["ok"] is True
        rem = step1.get("budget", {}).get("steps_remaining")
        assert rem == 0

        count_after = step1.get("considered_count")
        # Second step must not expand further under exhausted steps.
        code, step2 = h.post(
            "/api/memory/graph/traverse",
            {
                "action": "step",
                "session_id": sid,
                "expand_ids": [atoms[2].atom_id, atoms[1].atom_id],
            },
        )
        assert code == 200, step2
        assert step2["ok"] is True
        assert step2.get("considered_count") == count_after
        assert step2.get("newly_expanded") in ([], None) or step2.get(
            "newly_expanded"
        ) == []
    finally:
        h.close()


def test_graph_traverse_bad_action(paths):
    h = _ApiHarness(paths, memory=_enabled_memory())
    try:
        code, body = h.post(
            "/api/memory/graph/traverse",
            {"action": "explode"},
        )
        assert code == 400
        assert body["ok"] is False
    finally:
        h.close()


def test_graph_which_query_validation(paths):
    h = _ApiHarness(paths, memory=_enabled_memory())
    try:
        code, body = h.get("/api/memory/graph/session?which=nope")
        assert code == 400
        assert body["ok"] is False
    finally:
        h.close()


# ── PR2: full legend + durable neighbors honesty ────────────────────────────


def test_graph_overview_legend_covers_all_edge_kinds(paths):
    """Legend includes summary_* and durable kinds (not stub partial list)."""
    from elyra.memory.weights import EDGE_KINDS

    h = _ApiHarness(paths, memory=_enabled_memory())
    try:
        code, body = h.get("/api/memory/graph")
        assert code == 200, body
        legend = body["edge_kind_legend"]
        kinds = {e["kind"] for e in legend}
        # All EDGE_KINDS present.
        assert EDGE_KINDS <= kinds
        for required in (
            "sequential",
            "summary_child",
            "summary_source",
            "supersedes",
            "created_with",
            "recalls",
            "in_moment",
            "has_channel",
            "semantic_hop",
        ):
            assert required in kinds
        # Durable flag honesty on overview.
        assert "edge_count" in body
        assert "edges_by_kind" in body
        assert "edge_store" in body
        assert body["edge_store"]["durable_edges_enabled"] is False
        assert body["traversal"].get("durable_edges_enabled") is False
    finally:
        h.close()


def test_graph_neighbors_durable_created_with_real_dsts(paths):
    """Neighbors API returns durable edges with real atom dsts only."""
    from elyra.memory.edges import DurableEdge, new_edge_id
    from elyra.memory.graph import moment_hub_id
    from elyra.memory.weights import EDGE_CREATED_WITH, EDGE_IN_MOMENT

    h = _ApiHarness(
        paths,
        memory=MemorySettings(
            enabled=True,
            write_atoms=True,
            backend="jsonl",
            directed_traversal_enabled=True,
            durable_edges_enabled=True,
        ),
    )
    try:
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        estore = h.worker._ensure_edge_store()  # noqa: SLF001
        assert store is not None and estore is not None

        mid = "m_api_durable"
        a = store.put_atom(
            Atom(
                atom_id=new_atom_id(),
                t_start="2026-07-28T10:00:00Z",
                kind="observation",
                content_text="seed atom",
                content_ref="inline",
                moment_id=mid,
            )
        )
        b = store.put_atom(
            Atom(
                atom_id=new_atom_id(),
                t_start="2026-07-28T09:00:00Z",
                kind="observation",
                content_text="context atom",
                content_ref="inline",
                moment_id=mid,
            )
        )
        c = store.put_atom(
            Atom(
                atom_id=new_atom_id(),
                t_start="2026-07-28T10:01:00Z",
                kind="observation",
                content_text="peer atom",
                content_ref="inline",
                moment_id=mid,
            )
        )
        now = "2026-07-28T10:02:00Z"
        estore.put_edge(
            DurableEdge(
                edge_id=new_edge_id(),
                src_atom_id=a.atom_id,
                dst_atom_id=b.atom_id,
                edge_kind=EDGE_CREATED_WITH,
                created_at=now,
                updated_at=now,
                reason="test",
                meta={"retarget_from": None},
            )
        )
        hub = moment_hub_id(mid)
        for atom in (a, b, c):
            estore.put_edge(
                DurableEdge(
                    edge_id=new_edge_id(),
                    src_atom_id=atom.atom_id,
                    dst_atom_id=hub,
                    edge_kind=EDGE_IN_MOMENT,
                    created_at=now,
                    updated_at=now,
                    reason="membership",
                )
            )

        code, body = h.get(
            f"/api/memory/graph/neighbors?atom_id={a.atom_id}&k=20&allow_semantic=0"
        )
        assert code == 200, body
        assert body["ok"] is True
        dsts = {n["atom_id"] for n in body["neighbors"]}
        kinds = {n["edge_kind"] for n in body["neighbors"]}
        assert b.atom_id in dsts
        assert c.atom_id in dsts  # co-member via in_moment rewrite
        assert hub not in dsts
        assert all(not str(d).startswith("moment:") for d in dsts)
        assert EDGE_CREATED_WITH in kinds or EDGE_IN_MOMENT in kinds

        # Overview reflects durable edge counts when store has rows.
        code, overview = h.get("/api/memory/graph")
        assert code == 200
        assert overview["edge_count"] >= 4
        assert overview["traversal"]["durable_edges_enabled"] is True
        assert overview["honesty"]["edge_store_empty"] is False
        assert overview["honesty"]["projected_edges_only"] is False
        assert "created_with" in overview["edges_by_kind"] or overview[
            "edge_count"
        ] > 0
    finally:
        h.close()
