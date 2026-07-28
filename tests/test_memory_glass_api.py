"""PR9 glass Memory APIs: context meal inspector + atoms list (read-only)."""

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
from elyra.memory.inspect import meal_package_to_inspect
from elyra.memory.meal import compose_meal
from elyra.memory.promote import promote_wake_observation
from elyra.memory.store import open_memory_store
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


def test_memory_overview_defaults(paths):
    h = _ApiHarness(paths)
    try:
        code, body = h.get("/api/memory")
        assert code == 200
        assert "memory" in body
        assert "tabs" in body
        assert body["tabs"]["vectors"]["stub"] is False
        assert body["tabs"]["vectors"]["phase"] == "2"
        assert body["tabs"]["graph"]["stub"] is True
        assert body["tabs"]["context"] is True
        assert body["tabs"]["atoms"] is True
        mem = body["memory"]
        assert "enabled" in mem
        assert "write_atoms" in mem
        assert "backend" in mem
    finally:
        h.close()


def test_memory_context_fail_closed_without_snapshot(paths):
    """Store flags off / no open → meal None, ok false (fail closed)."""
    h = _ApiHarness(
        paths,
        memory=MemorySettings(enabled=False, write_atoms=False, backend="jsonl"),
    )
    try:
        code, body = h.get("/api/memory/context")
        assert code == 200
        assert body["ok"] is False
        assert body["meal"] is None
        assert body.get("error")
    finally:
        h.close()


def test_memory_context_returns_last_meal_snapshot(paths):
    h = _ApiHarness(
        paths,
        memory=MemorySettings(enabled=True, write_atoms=True, backend="jsonl"),
    )
    try:
        store = open_memory_store(
            paths, MemorySettings(write_atoms=True, enabled=True, backend="jsonl")
        )
        try:
            mid = "moment_glass_test_01"
            promote_wake_observation(
                store,
                mid,
                content="hello from glass memory test",
                message_id="msg_glass_1",
            )
            package = compose_meal(
                store,
                open_moment_id=mid,
                budget_tokens=8000,
                system_text="SYS",
                orient_text="ORIENT body",
            )
            payload = meal_package_to_inspect(
                package,
                system_text="SYS",
                orient_text="ORIENT body",
                budget_tokens=8000,
                source="rebuild_outer",
            )
            h.worker._last_meal_snapshot = payload  # noqa: SLF001
        finally:
            store.close()

        code, body = h.get("/api/memory/context")
        assert code == 200, body
        assert body["ok"] is True
        meal = body["meal"]
        assert meal is not None
        assert meal["open_moment_id"] == "moment_glass_test_01"
        assert "items" in meal
        assert "channels" in meal
        assert meal["fixed"]["system"]["token_estimate"] >= 0
        # Snippets are truncated; meal payload has no credential material.
        assert "snippet" in meal["fixed"]["system"]
        for item in meal["items"]:
            assert "snippet" in item
            assert len(item["snippet"]) <= 250
    finally:
        h.close()


def test_memory_atoms_list_and_detail(paths):
    h = _ApiHarness(
        paths,
        memory=MemorySettings(enabled=True, write_atoms=True, backend="jsonl"),
    )
    try:
        # Seed via worker store open (same data dir).
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        assert store is not None
        mid = "moment_atoms_list_01"
        atom = promote_wake_observation(
            store,
            mid,
            content="atom list seed text for glass",
            message_id="msg_atoms_1",
        )
        assert atom is not None

        code, body = h.get("/api/memory/atoms?limit=20")
        assert code == 200, body
        assert body["ok"] is True
        assert body["count"] >= 1
        ids = {a["atom_id"] for a in body["atoms"]}
        assert atom.atom_id in ids
        row = next(a for a in body["atoms"] if a["atom_id"] == atom.atom_id)
        assert row["kind"] == "observation"
        assert row["moment_id"] == mid
        assert "text" in row
        assert len(row["text"]) <= 250

        code, detail = h.get(f"/api/memory/atoms/{atom.atom_id}")
        assert code == 200, detail
        assert detail["ok"] is True
        assert detail["atom"]["atom_id"] == atom.atom_id
        assert "atom list seed" in (detail["atom"].get("content_text") or "")

        code, missing = h.get("/api/memory/atoms/a_deadbeefdeadbeefdeadbeefdeadbeef")
        assert code == 404
        assert missing["ok"] is False
    finally:
        h.close()


def test_memory_atoms_kind_filter(paths):
    h = _ApiHarness(
        paths,
        memory=MemorySettings(enabled=True, write_atoms=True, backend="jsonl"),
    )
    try:
        store = h.worker._ensure_memory_store()  # noqa: SLF001
        promote_wake_observation(
            store,
            "m_kind_filter",
            content="kind filter seed",
            message_id="msg_kind_1",
        )
        code, body = h.get("/api/memory/atoms?kind=observation&limit=10")
        assert code == 200
        assert body["ok"] is True
        for a in body["atoms"]:
            assert a["kind"] == "observation"

        code, bad = h.get("/api/memory/atoms?kind=not_a_kind")
        assert code == 400
        assert bad["ok"] is False
    finally:
        h.close()


def test_memory_atoms_fail_closed_when_flags_off(paths):
    h = _ApiHarness(
        paths,
        memory=MemorySettings(enabled=False, write_atoms=False, backend="jsonl"),
    )
    try:
        code, body = h.get("/api/memory/atoms")
        assert code == 200
        assert body["ok"] is False
        assert body["atoms"] == []
        assert body.get("error")
    finally:
        h.close()


def test_status_includes_memory_has_last_meal(paths):
    h = _ApiHarness(
        paths,
        memory=MemorySettings(enabled=True, write_atoms=True, backend="jsonl"),
    )
    try:
        code, body = h.get("/api/status")
        assert code == 200
        assert "memory" in body
        assert "has_last_meal" in body["memory"]
        assert body["memory"]["has_last_meal"] is False
    finally:
        h.close()
