"""Start Elyra: optional llama-server, API + Web UI, presence worker.

Provider-aware client stack (Phase 0):
- provider=xai → skip llama; UsageGatedChatClient(HttpChatClient.for_xai) or
  FailingChatClient when !credential_ok (meter still loaded).
- provider=local → optional llama + gated local HTTP client.
- --stub-llm → StubChatClient only (not implied by --no-llama).
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
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths, resolve_paths
from elyra.llm.auth import resolve_bearer
from elyra.llm.client import (
    ChatClient,
    FailingChatClient,
    GatedChatClient,
    HttpChatClient,
    StubChatClient,
    UsageGatedChatClient,
)
from elyra.llm.config import LlamaServerConfig, XaiClientConfig
from elyra.llm.constants import CONTEXT_WINDOW_TOKENS
from elyra.llm.models import CURATED_XAI_MODELS, models_for_picker
from elyra.llm.provider_prefs import provider_prefs_path
from elyra.llm.queue import LlamaServerGate
from elyra.llm.server import build_server_command, validate_model_paths
from elyra.llm.usage import UsageMeter
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.provider_runtime import ProviderRuntime
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
        self.provider_runtime: ProviderRuntime | None = None

    def start(self) -> None:
        set_runtime_state(self.state)
        self.paths.ensure_data_dirs()

        cfg = self.config
        provider_name = cfg.provider_name
        data_dir = self.paths.data_dir

        # Always load meter (even when !credential_ok) so repair keeps windows.
        meter = UsageMeter.load(data_dir, cfg.usage)

        grok_auth_path: Path | None = None
        if cfg.grok_auth_path:
            grok_auth_path = Path(cfg.grok_auth_path).expanduser()

        xai_config = XaiClientConfig(
            base_url=cfg.base_url,
            read_timeout=cfg.request_timeout_s,
        )

        http_client: HttpChatClient | None = None
        chat_client: ChatClient
        credential_ok = False
        credential_detail: str | None = None
        credential_expires_at: str | None = None
        credential_email: str | None = None
        api_key_configured = False
        models_available: list[str] = []

        if self._use_stub:
            # Stub path: never force llama; --stub-llm is the only stub trigger.
            if cfg.start_llama_server:
                self._start_llama_server()
            else:
                self.state.set_llama(pid=None, ready=False, error="stub_llm")
            chat_client = StubChatClient()
            credential_ok = True
            credential_detail = None
            if provider_name == "local":
                models_available = ["local"]
            else:
                models_available = models_for_picker(
                    None, fallback=CURATED_XAI_MODELS, current=cfg.model
                )
        elif provider_name == "xai":
            # Product default: no llama-server for xai.
            self.state.set_llama(pid=None, ready=False, error="provider_xai")
            resolution = resolve_bearer(
                source=cfg.credential_source,
                data_dir=data_dir,
                grok_auth_path=grok_auth_path,
            )
            api_key_configured = resolution.api_key_configured
            credential_expires_at = resolution.expires_at
            credential_email = resolution.email
            if resolution.ok and resolution.token:
                http_client = HttpChatClient.for_xai(
                    xai_config,
                    model=cfg.model,
                    bearer_token=resolution.token,
                )
                if cfg.usage.enabled:
                    chat_client = UsageGatedChatClient(http_client, meter)
                else:
                    chat_client = http_client
                credential_ok = True
                credential_detail = None
                models_available = models_for_picker(
                    None, fallback=CURATED_XAI_MODELS, current=cfg.model
                )
            else:
                detail = resolution.detail or "credential_unavailable"
                _LOG.warning(
                    "xai credentials not ok (source=%s detail=%s) — FailingChatClient",
                    cfg.credential_source,
                    detail,
                )
                chat_client = FailingChatClient(detail)
                credential_ok = False
                credential_detail = detail
                models_available = models_for_picker(
                    None, fallback=CURATED_XAI_MODELS, current=cfg.model
                )
        else:
            # provider=local
            if cfg.start_llama_server:
                self._start_llama_server()
            else:
                self.state.set_llama(pid=None, ready=False, error="llama disabled")

            if self.state.llama_ready:
                http_client = HttpChatClient.for_local(cfg.llama)
                gated: ChatClient = GatedChatClient(http_client, self._gate)
                if cfg.usage.enabled:
                    chat_client = UsageGatedChatClient(gated, meter)
                else:
                    chat_client = gated
                credential_ok = True
            else:
                if not self.state.llama_ready:
                    _LOG.warning("llama not ready — using stub chat client")
                chat_client = StubChatClient()
                credential_ok = True
                credential_detail = self.state.llama_error
            models_available = ["local"]

        self.state.set_provider(
            provider_name=provider_name,
            model=cfg.model,
            model_label=cfg.model_label,
            base_url=cfg.base_url,
            credential_source=cfg.credential_source,
            credential_ok=credential_ok,
            credential_detail=credential_detail,
            credential_expires_at=credential_expires_at,
            credential_email=credential_email,
            api_key_configured=api_key_configured,
        )

        pr = ProviderRuntime(
            meter=meter,
            http_client=http_client,
            chat_client=chat_client,
            worker=None,
            usage_settings=cfg.usage,
            xai_config=xai_config if provider_name == "xai" else None,
            llama_config=cfg.llama if provider_name == "local" else None,
            gate=self._gate,
            prefs_path=provider_prefs_path(data_dir),
            data_dir=data_dir,
            provider_name=provider_name,
            model=cfg.model,
            model_label=cfg.model_label,
            credential_source=cfg.credential_source,
            credential_ok=credential_ok,
            credential_detail=credential_detail,
            credential_expires_at=credential_expires_at,
            credential_email=credential_email,
            api_key_configured=api_key_configured,
            models_available=models_available,
            base_url=cfg.base_url,
            grok_auth_path=grok_auth_path,
            request_timeout_s=cfg.request_timeout_s,
            state=self.state,
        )
        self.provider_runtime = pr

        self._worker = PresenceWorker(
            paths=self.paths,
            client=chat_client,
            stop_event=self._stop,
            model_available=pr.can_open_model_moment,
        )
        pr.worker = self._worker

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
            provider=pr,
        )

        # Best-effort remote models when credentials already ok (no network on fail).
        if credential_ok and provider_name == "xai" and not self._use_stub:
            try:
                pr.refresh_models()
            except Exception:  # noqa: BLE001
                _LOG.debug("initial refresh_models failed", exc_info=True)

    def run_forever(self) -> None:
        self.start()
        self.serve_until_stopped()

    def serve_until_stopped(self) -> None:
        """Block until SIGINT/SIGTERM after ``start()`` has been called."""

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
