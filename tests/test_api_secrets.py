"""PR5: Glass secrets API — write-only, never echo values."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from elyra.config import resolve_paths
from elyra.identity import IdentityStore
from elyra.llm.client import StubChatClient
from elyra.llm.queue import ChatRequestGate
from elyra.loop.doloop import DoLoopResult
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import TimerService
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState
from elyra.settings import default_settings
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
    def __init__(self, paths) -> None:
        self.paths = paths
        stop = threading.Event()
        queue = WakeQueue(paths)
        timers = TimerService(paths, queue)
        moments = MomentStore(paths)
        from elyra.goals import GoalsStore

        goals = GoalsStore(paths)
        self.worker = PresenceWorker(
            paths=paths,
            client=StubChatClient(),
            stop_event=stop,
            poll_seconds=0.05,
            settings=default_settings(),
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
            tools=None,
            skills=None,
        )
        host, port = self.server.server_address[:2]
        self.base = f"http://{host}:{port}"

    def close(self) -> None:
        self._stop.set()
        self.server.shutdown()

    def _req(
        self,
        method: str,
        path: str,
        body: dict | None = None,
    ) -> tuple[int, dict[str, Any]]:
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            try:
                parsed = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                parsed = {"error": raw}
            return exc.code, parsed

    def get(self, path: str) -> tuple[int, dict]:
        return self._req("GET", path)

    def put(self, path: str, body: dict) -> tuple[int, dict]:
        return self._req("PUT", path, body)

    def delete(self, path: str) -> tuple[int, dict]:
        return self._req("DELETE", path)


@pytest.fixture
def harness(paths):
    h = _ApiHarness(paths)
    try:
        yield h
    finally:
        h.close()


def test_put_secret_write_only_never_echoes(harness: _ApiHarness) -> None:
    secret = "glass-secret-value-never-echo"
    code, body = harness.put(
        "/api/secrets",
        {"name": "gh_token", "value": secret, "grants": ["gh_api"]},
    )
    assert code == 200
    assert body.get("ok") is True
    raw = json.dumps(body)
    assert secret not in raw
    assert "value" not in (body.get("secret") or {})
    assert body["secret"]["name"] == "gh_token"
    assert body["secret"]["grants"] == ["gh_api"]


def test_get_secrets_no_values(harness: _ApiHarness) -> None:
    secret = "list-must-not-show-this"
    harness.put("/api/secrets", {"name": "gh_token", "value": secret})
    code, body = harness.get("/api/secrets")
    assert code == 200
    assert body.get("ok") is True
    secrets = body.get("secrets") or []
    assert any(s.get("name") == "gh_token" for s in secrets)
    raw = json.dumps(body)
    assert secret not in raw
    for s in secrets:
        assert "value" not in s


def test_delete_secret(harness: _ApiHarness) -> None:
    harness.put("/api/secrets", {"name": "tmp_tok", "value": "v1"})
    code, body = harness.delete("/api/secrets/tmp_tok")
    assert code == 200
    assert body.get("deleted") is True
    code, body = harness.get("/api/secrets")
    names = [s["name"] for s in body.get("secrets") or []]
    assert "tmp_tok" not in names
    code, body = harness.delete("/api/secrets/tmp_tok")
    assert code == 404


def test_put_grants(harness: _ApiHarness) -> None:
    harness.put("/api/secrets", {"name": "gh_token", "value": "tok"})
    code, body = harness.put(
        "/api/secrets/gh_token/grants",
        {"grants": ["gh_pr_create", "gh_api"]},
    )
    assert code == 200
    assert body["secret"]["grants"] == ["gh_pr_create", "gh_api"]
    assert "value" not in body["secret"]


def test_reserved_name_rejected(harness: _ApiHarness) -> None:
    code, body = harness.put(
        "/api/secrets",
        {"name": "xai_api_key", "value": "nope"},
    )
    assert code == 400
    assert body.get("error") == "reserved_secret_name"


def test_put_requires_name_and_value(harness: _ApiHarness) -> None:
    code, body = harness.put("/api/secrets", {"value": "x"})
    assert code == 400
    code, body = harness.put("/api/secrets", {"name": "a", "value": "  "})
    assert code == 400
