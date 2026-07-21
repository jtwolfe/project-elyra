"""Start Elyra: llama-server, API + Web UI, presence worker.

Scope: single-command process supervision.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from elyra.config import ElyraPaths, resolve_paths
from elyra.llm.client import GatedChatClient, HttpChatClient, StubChatClient
from elyra.llm.config import LlamaServerConfig
from elyra.llm.constants import CONTEXT_WINDOW_TOKENS
from elyra.llm.queue import LlamaServerGate
from elyra.llm.server import build_server_command, validate_model_paths
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.state import RuntimeState, set_runtime_state

_LOG = logging.getLogger(__name__)


class ElyraSupervisor:
    def __init__(
        self,
        *,
        paths: ElyraPaths | None = None,
        config: RuntimeConfig | None = None,
        use_stub_llm: bool = False,
    ) -> None:
        self.paths = paths or resolve_paths()
        self.config = config or RuntimeConfig()
        self.state = RuntimeState()
        self._use_stub = use_stub_llm
        self._llama_proc: subprocess.Popen[bytes] | None = None
        self._api_server: Any = None
        self._api_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._worker: PresenceWorker | None = None
        self._gate = LlamaServerGate()
        self._stop = threading.Event()

    def start(self) -> None:
        set_runtime_state(self.state)
        self.paths.ensure_data_dirs()

        if self.config.start_llama_server and not self._use_stub:
            self._start_llama_server()
        elif self._use_stub:
            self.state.set_llama(pid=None, ready=False, error="stub_llm")
        else:
            self.state.set_llama(pid=None, ready=False, error="llama disabled")

        client: HttpChatClient | StubChatClient | GatedChatClient
        if self._use_stub or not self.state.llama_ready:
            if not self._use_stub and not self.state.llama_ready:
                _LOG.warning("llama not ready — using stub chat client")
            client = StubChatClient()
        else:
            client = GatedChatClient(
                HttpChatClient(self.config.llama),
                self._gate,
            )

        self._worker = PresenceWorker(
            paths=self.paths,
            client=client,
            stop_event=self._stop,
        )
        self._worker_thread = threading.Thread(
            target=self._worker.run,
            name="elyra-presence",
            daemon=True,
        )
        self._worker_thread.start()

        self._api_server, self._api_thread = start_api_server(
            self.config,
            paths=self.paths,
            gate=self._gate,
            state=self.state,
            worker=self._worker,
        )

    def run_forever(self) -> None:
        self.start()

        def _handle(signum: int, _frame: object) -> None:
            print(f"\nshutting down (signal {signum})…", file=sys.stderr)
            self._stop.set()

        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)

        try:
            while not self._stop.wait(timeout=0.5):
                if self._llama_proc is not None and self._llama_proc.poll() is not None:
                    code = self._llama_proc.returncode
                    print(f"llama-server exited with code {code}", file=sys.stderr)
                    self.state.set_llama(pid=None, ready=False, error=f"exited {code}")
                    self._llama_proc = None
        finally:
            self.shutdown()

    def _start_llama_server(self) -> None:
        problems = validate_model_paths(self.paths)
        if problems:
            for p in problems:
                _LOG.error("%s", p)
            self.state.set_llama(
                pid=None,
                ready=False,
                error="; ".join(problems),
            )
            return

        llama_cfg = self.config.llama
        # No global CLI reasoning budget (match elyra2 supervisor default).
        if llama_cfg.reasoning_budget is None:
            from dataclasses import replace

            llama_cfg = replace(llama_cfg, reasoning_budget=-1)

        ctx = self.config.context_tokens or CONTEXT_WINDOW_TOKENS
        cmd = build_server_command(
            self.paths,
            llama_cfg,
            context_tokens=ctx,
        )
        _LOG.info("starting llama-server: %s", " ".join(cmd[:8]) + " …")
        self._llama_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.state.set_llama(pid=self._llama_proc.pid, ready=False)
        ready = self._wait_for_llama(llama_cfg)
        err = None if ready else "health check timed out"
        self.state.set_llama(pid=self._llama_proc.pid, ready=ready, error=err)
        if ready:
            print(f"llama-server ready on {llama_cfg.host}:{llama_cfg.port}")
        else:
            print("llama-server failed health check", file=sys.stderr)

    def _wait_for_llama(self, llama_cfg: LlamaServerConfig) -> bool:
        deadline = time.monotonic() + self.config.llama_health_timeout
        url = llama_cfg.health_url
        while time.monotonic() < deadline:
            if self._llama_proc is not None and self._llama_proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    if 200 <= resp.status < 300:
                        return True
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            time.sleep(0.5)
        return False

    def shutdown(self) -> None:
        self._stop.set()
        self._gate.shutdown()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=5)
        if self._llama_proc is not None:
            self._llama_proc.terminate()
            try:
                self._llama_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._llama_proc.kill()
            self._llama_proc = None
            self.state.set_llama(pid=None, ready=False)
        if self._api_server is not None:
            self._api_server.shutdown()
        if self._api_thread is not None:
            self._api_thread.join(timeout=5)


def run_supervisor(
    *,
    paths: ElyraPaths | None = None,
    config: RuntimeConfig | None = None,
    use_stub_llm: bool = False,
) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    ElyraSupervisor(
        paths=paths, config=config, use_stub_llm=use_stub_llm
    ).run_forever()
