"""Start Elyra: API + Web UI, presence worker (no local inference process).

Provider-aware client stack:
- provider=xai → UsageGatedChatClient(HttpChatClient.for_xai) or
  FailingChatClient when !credential_ok (meter still loaded).
- provider=local → FailingChatClient("local_not_implemented") unless --stub-llm.
- --stub-llm → StubChatClient only (never starts an inference process).

Sandbox (H2c): host tree ensure (sync) + SandboxLifecycleManager register +
**async warm** ensure so chat starts without multi-minute MSB hang.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from elyra.config import ElyraPaths, resolve_paths
from elyra.llm.auth import SOURCE_XAI_OAUTH, resolve_bearer
from elyra.llm.client import (
    ChatClient,
    FailingChatClient,
    HttpChatClient,
    StubChatClient,
    UsageGatedChatClient,
)
from elyra.llm.config import XaiClientConfig
from elyra.llm.models import CURATED_XAI_MODELS, models_for_picker
from elyra.llm.provider_prefs import provider_prefs_path
from elyra.llm.queue import ChatRequestGate
from elyra.llm.usage import UsageMeter
from elyra.presence.queue import WakeQueue
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.credits_poller import CreditsPoller
from elyra.runtime.provider_runtime import ProviderRuntime
from elyra.runtime.state import RuntimeState, set_runtime_state
from elyra.sandbox.lifecycle import SandboxLifecycleManager
from elyra.sandbox.paths import PRIMARY_NAME, ensure_host_tree, isolation_enabled
from elyra.sandbox.registry import clear_sandbox_lifecycle, set_sandbox_lifecycle
from elyra.sandbox.status import sandbox_status_block

_LOG = logging.getLogger(__name__)

# Log install hint at most once per process when isolation on + client unusable.
_INSTALL_HINT_LOGGED = False
_INSTALL_HINT = (
    "sandbox isolation on but microsandbox client unusable — "
    "guest tools will fail closed. Install: pip install -e '.[sandbox]' "
    "then ./scripts/setup-microsandbox.sh --doctor-only"
)


class ElyraSupervisor:
    def __init__(
        self,
        *,
        paths: ElyraPaths | None = None,
        config: RuntimeConfig | None = None,
        use_stub_llm: bool = False,
        sandbox_lifecycle: SandboxLifecycleManager | None = None,
    ) -> None:
        self.paths = paths or resolve_paths()
        self.config = config or RuntimeConfig()
        self.state = RuntimeState()
        self._use_stub = use_stub_llm
        self._api_server: Any = None
        self._api_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None
        self._worker: PresenceWorker | None = None
        self._gate = ChatRequestGate()
        self._stop = threading.Event()
        self.provider_runtime: ProviderRuntime | None = None
        # Sandbox lifecycle (H2c) — injectable for hermetic tests.
        self._sandbox: SandboxLifecycleManager | None = sandbox_lifecycle
        self._sandbox_warm_thread: threading.Thread | None = None
        self._sandbox_warm_lock = threading.Lock()
        self._sandbox_warm_reason: str | None = "warming"
        self._sandbox_warm_done: bool = False
        self._sandbox_stop = threading.Event()
        self._credits_poller: CreditsPoller | None = None
        # Shared wake queue (worker + instrument reaper). One instance only.
        self._wake_queue: WakeQueue | None = None
        self._usage_meter: UsageMeter | None = None
        self._instrument_reaper: Any = None

    def sandbox_status(self) -> dict[str, Any]:
        """Operator status block (also used by GET /api/status)."""
        with self._sandbox_warm_lock:
            warm_reason = self._sandbox_warm_reason
            warm_done = self._sandbox_warm_done
        return sandbox_status_block(
            self.paths,
            warm_reason=warm_reason,
            warm_done=warm_done,
        )

    def _set_warm_state(
        self,
        *,
        reason: str | None,
        done: bool,
    ) -> None:
        with self._sandbox_warm_lock:
            self._sandbox_warm_reason = reason
            self._sandbox_warm_done = done

    def _start_sandbox_lifecycle(self) -> None:
        """Ensure host tree, register lifecycle, kick async warm ensure (KD23).

        Never blocks product start on multi-minute image pull / pip. Chat and
        API come up immediately; guest tools fail closed until mount_ready.
        """
        global _INSTALL_HINT_LOGGED

        # 1. Host FS (fast) — product FS tools need sandboxes/sandbox0 seed.
        try:
            ensure_host_tree(PRIMARY_NAME, self.paths)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("ensure_host_tree failed: %s", exc)

        # 2. Construct + register immediately (even when degraded / unusable).
        if self._sandbox is None:
            self._sandbox = SandboxLifecycleManager(paths=self.paths)
        set_sandbox_lifecycle(self._sandbox)

        iso = isolation_enabled()
        if not iso:
            self._set_warm_state(reason="isolation_disabled", done=True)
            _LOG.info("sandbox isolation disabled (ELYRA_SANDBOX=0); skip warm ensure")
            return

        if self._sandbox.client_unusable:
            self._set_warm_state(reason="client_unusable", done=True)
            if not _INSTALL_HINT_LOGGED:
                _LOG.warning("%s", _INSTALL_HINT)
                _INSTALL_HINT_LOGGED = True
            return

        # 3. Async warm — do not block elyra start.
        self._set_warm_state(reason="warming", done=False)
        life = self._sandbox
        stop = self._sandbox_stop

        def _warm() -> None:
            if stop.is_set():
                return
            try:
                result = life.ensure(PRIMARY_NAME)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("sandbox async warm ensure raised: %s", exc)
                self._set_warm_state(reason="ensure_raised", done=True)
                return
            if stop.is_set():
                return
            if not result.ready:
                reason = result.reason or "degraded"
                _LOG.warning("sandbox0 async warm degraded: %s", reason)
                self._set_warm_state(reason=reason, done=True)
                return
            # Mount ready — curated pyenv install is *outside* the 60s mount wall
            # (KD23 / H3b). May take minutes; status pyenv_ready stays false until
            # marker written. Guest tools that need third-party pkgs / verify
            # fail closed until then.
            _LOG.info("sandbox0 mount ready (async warm); starting pyenv install")
            self._set_warm_state(reason="pyenv_not_ready", done=True)
            try:
                from elyra.sandbox.pyenv import try_install_curated_pyenv

                pyenv_ok = try_install_curated_pyenv(life, paths=self.paths).ok
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("sandbox0 pyenv install raised: %s", exc)
                pyenv_ok = False
            if stop.is_set():
                return
            if pyenv_ok:
                _LOG.info("sandbox0 pyenv ready (async warm)")
                self._set_warm_state(reason=None, done=True)
            else:
                _LOG.warning(
                    "sandbox0 pyenv not ready after warm — verify_tool will fail "
                    "guest_pytest_unavailable until operator re-bootstrap"
                )
                self._set_warm_state(reason="pyenv_not_ready", done=True)

        self._sandbox_warm_thread = threading.Thread(
            target=_warm,
            name="elyra-sandbox-warm",
            daemon=True,
        )
        self._sandbox_warm_thread.start()

    def start(self) -> None:
        set_runtime_state(self.state)
        self.paths.ensure_data_dirs()
        # Sandbox host tree + lifecycle before worker (FS tools see seed layout).
        self._start_sandbox_lifecycle()

        cfg = self.config
        provider_name = cfg.provider_name
        data_dir = self.paths.data_dir

        # Always load meter (even when !credential_ok) so repair keeps windows.
        meter = UsageMeter.load(data_dir, cfg.usage)
        self._usage_meter = meter

        # One WakeQueue for the whole supervisor process (KD reaper / PR3).
        # PresenceWorker + InstrumentReaper MUST share this object — a private
        # second WakeQueue(paths) would drop completion wakes from the heap.
        self._wake_queue = WakeQueue(self.paths)

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
            # --stub-llm is the only stub trigger; never starts inference process.
            self.state.set_chat_posture(ready=False, error="stub_llm")
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
            # Product default: chat_ready reflects client usability (KD14).
            # Do not set chat_error="provider_xai" — absence of local process is normal.
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
                    reasoning_effort=cfg.reasoning_effort,
                )
                if cfg.usage.enabled:
                    chat_client = UsageGatedChatClient(http_client, meter)
                else:
                    chat_client = http_client
                credential_ok = True
                credential_detail = None
                self.state.set_chat_posture(ready=True, error=None)
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
                # Credential failures stay on credential_*; chat stack not ready.
                self.state.set_chat_posture(ready=False, error=None)
                models_available = models_for_picker(
                    None, fallback=CURATED_XAI_MODELS, current=cfg.model
                )
        else:
            # provider=local — no process launch; fail closed (KD2).
            _LOG.warning(
                "local provider not implemented — use --provider xai or --stub-llm"
            )
            self.state.set_chat_posture(ready=False, error="local_not_implemented")
            chat_client = FailingChatClient("local_not_implemented")
            credential_ok = True
            credential_detail = "local_not_implemented"
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
            local_config=cfg.local if provider_name == "local" else None,
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
            stub_llm=self._use_stub,
            reasoning_effort=cfg.reasoning_effort,
        )
        self.provider_runtime = pr

        self._worker = PresenceWorker(
            paths=self.paths,
            client=chat_client,
            stop_event=self._stop,
            model_available=pr.can_open_model_moment,
            queue=self._wake_queue,
        )
        pr.worker = self._worker

        # Cold-start oauth: wire 401 refresh_cb + keep-alive (rebuild path also
        # does this; supervisor builds the first client without those hooks).
        if (
            credential_ok
            and provider_name == "xai"
            and not self._use_stub
            and pr.credential_source == SOURCE_XAI_OAUTH
            and pr.http_client is not None
        ):
            pr.http_client.set_refresh_cb(pr._make_chat_refresh_cb())
            pr._start_oauth_keepalive()
            pr._refresh_auth_redaction_snapshot_unlocked()

        # SuperGrok credits poller (daemon): after meter + provider runtime.
        # No-op when usage.enabled=false or credits_poll_enabled=false.
        self._start_credits_poller(meter=meter, pr=pr)

        # Instrument reaper (async grok_build jobs): same shared WakeQueue + meter.
        self._start_instrument_reaper(meter=meter)

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
            supervisor=self,
        )

        # Best-effort remote models when credentials already ok (no network on fail).
        if credential_ok and provider_name == "xai" and not self._use_stub:
            try:
                pr.refresh_models()
            except Exception:  # noqa: BLE001
                _LOG.debug("initial refresh_models failed", exc_info=True)

    def _start_instrument_reaper(self, *, meter: UsageMeter) -> None:
        """Start supervisor-owned InstrumentReaper on the shared WakeQueue."""
        from elyra.instrument.jobs import ensure_grok_build_runtime
        from elyra.instrument.reaper import InstrumentReaper

        try:
            ensure_grok_build_runtime(self.paths)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("ensure_grok_build_runtime failed: %s", exc)
        if self._wake_queue is None:
            # Should not happen — start() always builds the queue first.
            self._wake_queue = WakeQueue(self.paths)
        reaper = InstrumentReaper(
            paths=self.paths,
            wake_queue=self._wake_queue,
            stop_event=self._stop,
            meter=meter,
        )
        reaper.start()
        self._instrument_reaper = reaper
        _LOG.info("instrument reaper started (shared WakeQueue)")

    def _start_credits_poller(
        self,
        *,
        meter: UsageMeter,
        pr: ProviderRuntime,
    ) -> None:
        """Start daemon credits poller when usage + poll flags allow."""
        cfg = self.config
        if not cfg.usage.enabled or not cfg.usage.credits_poll_enabled:
            self._credits_poller = None
            pr.credits_poller = None
            return

        def _get_source() -> str:
            return str(pr.credential_source or cfg.credential_source)

        def _get_settings():
            return pr.usage_settings

        def _on_access_refreshed(
            access: str | None,
            expires_at: str | None = None,
            email: str | None = None,
        ) -> None:
            pr.on_access_refreshed(access, expires_at, email)

        poller = CreditsPoller(
            meter=meter,
            usage_settings=cfg.usage,
            data_dir=self.paths.data_dir,
            credential_source=cfg.credential_source,
            grok_auth_path=pr.grok_auth_path,
            get_credential_source=_get_source,
            get_usage_settings=_get_settings,
            on_access_refreshed=_on_access_refreshed,
            enabled=True,
        )
        self._credits_poller = poller
        pr.credits_poller = poller
        poller.start()
        _LOG.debug(
            "credits poller started interval_s=%s base=%s",
            cfg.usage.credits_poll_interval_s,
            cfg.usage.credits_base_url,
        )

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
                pass
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """Ordered teardown: stop signal → worker join → browser → sandbox → registry.

        Worker must join **before** sandbox stop (avoid mid-exec races).
        Browser sessions closed after worker join (IK18) so in-flight tools finish.
        Sandbox shutdown is stop-only (no remove). Warm thread best-effort join.
        """
        self._stop.set()
        self._sandbox_stop.set()
        self._gate.shutdown()
        # 0. Credits poller stop (daemon; join covers HTTP timeout + margin).
        if self._credits_poller is not None:
            try:
                # Default stop join = max(2, http_timeout+1); no shorter override.
                self._credits_poller.stop()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("credits poller stop failed: %s", exc)
            self._credits_poller = None
            if self.provider_runtime is not None:
                self.provider_runtime.credits_poller = None
        # 0a. Instrument reaper stop/join (before worker; leaves running jobs for next GC).
        if self._instrument_reaper is not None:
            try:
                self._instrument_reaper.stop(join_timeout_s=5.0)
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("instrument reaper stop failed: %s", exc)
            self._instrument_reaper = None
        # 0b. OAuth keep-alive stop.
        if self.provider_runtime is not None:
            try:
                self.provider_runtime.stop_background_tasks()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("provider background stop failed: %s", exc)
        # 1. Presence worker join before sandbox stop.
        # When browser sessions exist, allow nav timeout (+margin) so the
        # worker thread can finish and close Playwright on the owner thread.
        if self._worker_thread is not None:
            join_timeout = 5.0
            try:
                from elyra.tools.browser_sessions import (
                    WORKER_JOIN_TIMEOUT_S,
                    WORKER_JOIN_TIMEOUT_WITH_BROWSER_S,
                    get_browser_session_manager,
                )

                if get_browser_session_manager().session_count > 0:
                    join_timeout = float(WORKER_JOIN_TIMEOUT_WITH_BROWSER_S)
                else:
                    join_timeout = float(WORKER_JOIN_TIMEOUT_S)
            except Exception:  # noqa: BLE001
                join_timeout = 5.0
            self._worker_thread.join(timeout=join_timeout)
        # 1b. Safety net: leftover Playwright sessions after worker exit (IK18).
        # Prefer worker-thread close_all (run finally); this may be cross-thread.
        try:
            from elyra.tools.browser_sessions import get_browser_session_manager

            mgr = get_browser_session_manager()
            if mgr.session_count > 0:
                _LOG.warning(
                    "browser sessions remain after worker join count=%s; "
                    "force close_all from supervisor thread",
                    mgr.session_count,
                )
            mgr.close_all(force=True)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("browser close_all on shutdown failed: %s", exc)
        # 2. Warm thread best-effort join (daemon; cancel via stop event).
        if self._sandbox_warm_thread is not None:
            self._sandbox_warm_thread.join(timeout=5)
            self._sandbox_warm_thread = None
        # 3. Sandbox stop-only + bridge; clear durable in-memory ensure state.
        if self._sandbox is not None:
            try:
                self._sandbox.shutdown()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("sandbox shutdown failed: %s", exc)
            self._sandbox = None
        clear_sandbox_lifecycle()
        # 4. API
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
