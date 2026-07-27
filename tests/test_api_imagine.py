"""PR9 / KD10: POST /api/imagine stub (not_implemented)."""

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


def _stub_loop(**kwargs: Any) -> Any:
    def _fn(**kw: Any) -> DoLoopResult:
        ctx = kw.get("ctx")
        mid = getattr(ctx, "moment_id", "") if ctx is not None else ""
        return DoLoopResult(
            stop_reason="no_tools",
            hop_count=1,
            arm_wait=None,
            spoke=False,
            moment_id=mid,
            continue_injects=0,
            error=None,
        )

    return _fn


@pytest.fixture
def api_base(paths):
    stop = threading.Event()
    queue = WakeQueue(paths)
    timers = TimerService(paths, queue)
    moments = MomentStore(paths)
    worker = PresenceWorker(
        paths=paths,
        client=StubChatClient(),
        stop_event=stop,
        poll_seconds=0.05,
        settings=default_settings(),
        queue=queue,
        timers=timers,
        moments=moments,
        registry=_fake_registry(),
        run_do_loop_fn=_stub_loop(),
    )
    config = RuntimeConfig(api_host="127.0.0.1", api_port=0)
    state = RuntimeState()
    gate = ChatRequestGate()
    server, thread = start_api_server(
        config,
        paths=paths,
        gate=gate,
        state=state,
        worker=worker,
        tools=None,
        skills=None,
    )
    host, port = server.server_address[:2]
    base = f"http://{host}:{port}"
    yield base
    server.shutdown()
    thread.join(timeout=5)
    stop.set()


def test_imagine_stub_not_implemented(api_base):
    body = json.dumps({}).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base}/api/imagine",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as ei:
        urllib.request.urlopen(req, timeout=5)
    err = ei.value
    assert err.code == 501
    raw = err.read().decode("utf-8")
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert payload["reason"] == "not_implemented"
    assert "Imagine" in payload.get("hint", "") or "deferred" in payload.get(
        "hint", ""
    ).lower()
