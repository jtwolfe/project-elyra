"""PR6: live usage status + provider / usage override HTTP endpoints.

Hermetic — fake auth.json / temp data dirs; no live xAI network.
Covers: meter.snapshot on every GET; no secret leak; PATCH/PUT/DELETE;
hard_stop_override default-off + ON unlocks can_call; Failing cold start → repair.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from elyra.config import resolve_paths
from elyra.llm.auth import api_key_path, write_stored_api_key
from elyra.llm.client import FailingChatClient, StubChatClient, UsageGatedChatClient
from elyra.llm.queue import ChatRequestGate
from elyra.llm.credits import CreditsSnapshot
from elyra.llm.usage import TokenUsage, UsageMeter
from elyra.loop.doloop import DoLoopResult
from elyra.moment import MomentStore
from elyra.presence.queue import WakeQueue
from elyra.presence.timers import TimerService
from elyra.presence.worker import PresenceWorker
from elyra.runtime.api import start_api_server
from elyra.runtime.config import RuntimeConfig
from elyra.runtime.provider_runtime import ProviderRuntime
from elyra.runtime.state import RuntimeState
from elyra.settings import UsageSettings, default_settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _future_expires() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_auth(path: Path, token: str = "test-bearer-token-xyz") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "https://auth.x.ai::client": {
            "key": token,
            "email": "op@example.com",
            "expires_at": _future_expires(),
        }
    }
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


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


def _make_provider(
    paths,
    *,
    auth_path: Path | None = None,
    credential_source: str = "grok_build",
    credential_ok: bool = False,
    model: str = "grok-4.5",
    models_available: list[str] | None = None,
    usage: UsageSettings | None = None,
    meter: UsageMeter | None = None,
    worker: PresenceWorker | None = None,
    state: RuntimeState | None = None,
) -> ProviderRuntime:
    usage = usage or UsageSettings(enabled=True)
    data = paths.data_dir
    if meter is None:
        meter = UsageMeter.load(data, usage)
    chat: Any
    if credential_ok:
        # Will be replaced by rebuild when tests need a real stack; stub is fine
        # for status-only cases.
        chat = StubChatClient()
    else:
        chat = FailingChatClient("missing_auth_json")
    return ProviderRuntime(
        meter=meter,
        http_client=None,
        chat_client=chat,
        worker=worker,
        usage_settings=usage,
        xai_config=None,
        local_config=None,
        gate=None,
        prefs_path=data / "runtime" / "provider.json",
        data_dir=data,
        provider_name="xai",
        model=model,
        model_label="Grok 4.5 Fast" if model == "grok-4.5" else model,
        credential_source=credential_source,
        credential_ok=credential_ok,
        credential_detail=None if credential_ok else "missing_auth_json",
        credential_expires_at=None,
        credential_email=None,
        api_key_configured=False,
        models_available=list(models_available or ["grok-4.5", "grok-4.3"]),
        grok_auth_path=auth_path,
        state=state,
    )


class _ApiHarness:
    """ThreadingHTTPServer with optional ProviderRuntime."""

    def __init__(
        self,
        paths,
        *,
        provider: ProviderRuntime | None = None,
        attach_worker_to_provider: bool = True,
    ) -> None:
        self.paths = paths
        self._stop = threading.Event()
        queue = WakeQueue(paths)
        timers = TimerService(paths, queue)
        moments = MomentStore(paths)
        self.worker = PresenceWorker(
            paths=paths,
            client=StubChatClient(),
            stop_event=self._stop,
            poll_seconds=0.05,
            settings=default_settings(),
            queue=queue,
            timers=timers,
            moments=moments,
            registry=_fake_registry(),
            run_do_loop_fn=_stub_loop,
        )
        self.state = RuntimeState()
        self.gate = ChatRequestGate()
        self.provider = provider
        if provider is not None and attach_worker_to_provider:
            provider.worker = self.worker
            provider.state = self.state
            # Align worker client with provider's outer client.
            self.worker.client = provider.chat_client
        config = RuntimeConfig(api_host="127.0.0.1", api_port=0)
        self.server, self._api_thread = start_api_server(
            config,
            paths=paths,
            gate=self.gate,
            state=self.state,
            worker=self.worker,
            provider=provider,
            tools=None,
            skills=None,
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

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        data = None
        headers: dict[str, str] = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            try:
                return exc.code, json.loads(body)
            except json.JSONDecodeError:
                return exc.code, body

    def get(self, path: str) -> tuple[int, Any]:
        return self.request("GET", path)

    def patch(self, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
        return self.request("PATCH", path, payload)

    def put(self, path: str, payload: dict[str, Any]) -> tuple[int, Any]:
        return self.request("PUT", path, payload)

    def delete(self, path: str) -> tuple[int, Any]:
        return self.request("DELETE", path)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


# ---------------------------------------------------------------------------
# GET /api/status — live meter + provider fields, no secrets
# ---------------------------------------------------------------------------


def test_status_live_usage_and_provider_fields(paths):
    auth = paths.home / "auth.json"
    token = "secret-token-must-not-leak-abc123"
    _write_auth(auth, token=token)
    pr = _make_provider(paths, auth_path=auth, credential_ok=False)
    # Force some usage so snapshot fractions are not all 1.0
    assert pr.meter is not None
    pr.meter.record(TokenUsage(total_tokens=1000))

    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.get("/api/status")
        assert code == 200
        assert body["provider"] == "xai"
        assert body["model"] == "grok-4.5"
        assert body["model_label"] == "Grok 4.5 Fast"
        assert body["credential_source"] == "grok_build"
        assert body["credential_ok"] is False
        assert "models_available" in body
        assert "usage" in body
        usage = body["usage"]
        assert usage["enabled"] is True
        assert usage["override_active"] is False  # default OFF
        assert usage["week_used_tokens"] == 1000
        assert 0.0 <= usage["week_remaining_fraction"] < 1.0
        # Expanded design fields (PR6 Glass status shape)
        assert "pace_band" in usage
        assert usage["pace_band"] in ("green", "yellow", "red", "hard")
        assert "pace_ratio" in usage
        assert isinstance(usage["pace_ratio"], (int, float))
        assert "burst_remaining_tokens" in usage
        assert "burst_max_tokens" in usage
        assert usage["burst_max_tokens"] >= 0
        assert usage["burst_remaining_tokens"] >= 0
        assert "day_hard_stop_enabled" in usage
        assert "hour_hard_stop_enabled" in usage
        assert usage["day_hard_stop_enabled"] is False  # soft default
        assert usage["hour_hard_stop_enabled"] is False
        assert "day_soft_exhausted" in usage
        assert "hour_soft_exhausted" in usage
        assert usage["elyra_week_budget_tokens"] == usage["week_limit_tokens"]
        assert "weekly_allowed_fraction" in usage
        assert "period_id" in usage
        assert "period_authority" in usage
        assert "week_stt_calls" in usage
        assert "week_tts_calls" in usage
        assert usage["week_stt_calls"] == 0
        assert usage["week_tts_calls"] == 0
        assert "throttle_advice" in usage
        ta = usage["throttle_advice"]
        assert ta["band"] == usage["pace_band"]
        assert ta["delay_factor"] == 1.0
        assert ta["suggest_economy_model"] is False  # auto_throttle off by default
        assert "supergrok" in usage  # may be None without poll
        # Continuous still present and OFF by default
        cont = body.get("continuous")
        assert cont is None or cont.get("enabled") is False or cont.get("enabled") is None
        # No secret leak
        blob = json.dumps(body)
        assert token not in blob
        assert "secret-token" not in blob
        assert "Authorization" not in blob
    finally:
        h.close()


def test_status_without_provider_omits_usage_block(paths):
    """Legacy tests / unbound provider: status still 200 without usage keys."""
    h = _ApiHarness(paths, provider=None)
    try:
        code, body = h.get("/api/status")
        assert code == 200
        assert "phase" in body
        # Provider fields may come from RuntimeState defaults or be absent —
        # usage live block requires provider binding.
        assert body.get("usage") is None or "override_active" not in (body.get("usage") or {})
    finally:
        h.close()


def test_status_usage_refreshes_live(paths):
    pr = _make_provider(paths, credential_ok=False)
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body1 = h.get("/api/status")
        assert code == 200
        used1 = body1["usage"]["week_used_tokens"]
        assert pr.meter is not None
        pr.meter.record(TokenUsage(total_tokens=50))
        code, body2 = h.get("/api/status")
        assert code == 200
        assert body2["usage"]["week_used_tokens"] == used1 + 50
    finally:
        h.close()


def test_status_soft_day_exhausted_does_not_set_hard_stop(paths):
    """Day over soft limit with day_hard_stop_enabled=false → hard_stop null.

    Glass badge must not show stop · day from soft exhaustion alone.
    """
    usage_settings = UsageSettings(
        enabled=True,
        weekly_allowed_tokens=100_000,
        day_allowed_tokens=100,
        hour_allowed_tokens=100_000,
        day_hard_stop_enabled=False,
        hour_hard_stop_enabled=False,
    )
    meter = UsageMeter.load(paths.data_dir, usage_settings)
    # Exhaust day soft budget; week still has headroom.
    meter.record(TokenUsage(total_tokens=100))
    snap = meter.snapshot()
    assert snap.day_soft_exhausted is True
    assert snap.hard_stop is None
    assert meter.can_call() is True

    pr = _make_provider(paths, usage=usage_settings, meter=meter, credential_ok=False)
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.get("/api/status")
        assert code == 200
        usage = body["usage"]
        assert usage["day_soft_exhausted"] is True
        assert usage["day_hard_stop_enabled"] is False
        assert usage["hard_stop"] is None
        assert usage["override_active"] is False
        # Pace band is status only — not hard.
        assert usage["pace_band"] in ("green", "yellow", "red")
        assert usage["pace_band"] != "hard"
    finally:
        h.close()


def test_status_supergrok_block_from_injected_snapshot(paths):
    """Injected CreditsSnapshot surfaces nested supergrok on status."""
    usage_settings = UsageSettings(enabled=True, weekly_allowed_tokens=1_000_000)
    meter = UsageMeter.load(paths.data_dir, usage_settings)
    meter.apply_credits_snapshot(
        CreditsSnapshot(
            credit_usage_percent=22.5,
            period_start="2026-07-21T00:00:00Z",
            period_end="2026-07-28T00:00:00Z",
            fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            status="ok",
            ok=True,
            product_usage={"GrokBuild": 10.0, "Api": 12.5},
        )
    )
    pr = _make_provider(paths, usage=usage_settings, meter=meter, credential_ok=False)
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.get("/api/status")
        assert code == 200
        usage = body["usage"]
        assert usage["credit_usage_percent"] == 22.5
        sg = usage["supergrok"]
        assert sg is not None
        assert sg["credit_usage_percent"] == 22.5
        assert sg["status"] == "ok"
        assert sg["stale"] is False
        assert sg["period_authority"] == "supergrok"
        assert sg.get("product_usage") is not None
        assert "period_start" in sg
        assert "period_end" in sg
    finally:
        h.close()


def test_status_usage_disabled_placeholder_shape(paths):
    """Disabled meter still returns stable expanded keys for Glass."""
    usage_settings = UsageSettings(enabled=False)
    pr = _make_provider(paths, usage=usage_settings, credential_ok=False)
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.get("/api/status")
        assert code == 200
        usage = body["usage"]
        assert usage["enabled"] is False
        assert usage["hard_stop"] is None
        assert usage["override_active"] is False
        assert "pace_band" in usage
        assert "burst_remaining_tokens" in usage
        assert "throttle_advice" in usage
        assert usage["supergrok"] is None
    finally:
        h.close()


# ---------------------------------------------------------------------------
# PATCH /api/provider
# ---------------------------------------------------------------------------


def test_patch_provider_model(paths):
    pr = _make_provider(
        paths,
        models_available=["grok-4.5", "grok-4.3"],
        credential_ok=False,
    )
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.patch("/api/provider", {"model": "grok-4.3"})
        assert code == 200
        assert body["ok"] is True
        assert body["model"] == "grok-4.3"
        assert pr.model == "grok-4.3"
        # Prefs persisted
        prefs = (paths.data_dir / "runtime" / "provider.json").read_text(encoding="utf-8")
        assert "grok-4.3" in prefs
    finally:
        h.close()


def test_patch_provider_unknown_model(paths):
    pr = _make_provider(paths, models_available=["grok-4.5", "grok-4.3"])
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.patch("/api/provider", {"model": "not-a-real-model"})
        assert code == 400
        assert body["error"] == "unknown_model"
        assert pr.model == "grok-4.5"
    finally:
        h.close()


def test_patch_provider_empty_body(paths):
    pr = _make_provider(paths)
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.patch("/api/provider", {})
        assert code == 400
        assert body["error"] == "model or credential_source required"
    finally:
        h.close()


def test_patch_provider_invalid_credential_source(paths):
    pr = _make_provider(paths)
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.patch("/api/provider", {"credential_source": "oauth"})
        assert code == 400
        assert body["error"] == "invalid_credential_source"
    finally:
        h.close()


def test_patch_provider_credential_unavailable_leaves_previous(paths):
    auth = paths.home / "auth.json"
    _write_auth(auth)
    pr = _make_provider(
        paths,
        auth_path=auth,
        credential_source="grok_build",
        credential_ok=False,
    )
    # Build a working stack first
    with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5", "grok-4.3"]):
        pr.rebuild_chat_stack()
    assert pr.credential_ok is True
    prev_client = pr.chat_client

    h = _ApiHarness(paths, provider=pr)
    try:
        # Switch to api_key with no key → 400, previous intact
        code, body = h.patch("/api/provider", {"credential_source": "api_key"})
        assert code == 400
        assert body["error"] == "credential_unavailable"
        assert pr.credential_source == "grok_build"
        assert pr.chat_client is prev_client
        assert pr.credential_ok is True
    finally:
        h.close()


def test_patch_provider_credential_source_ok_rebuilds(paths):
    auth = paths.home / "auth.json"
    _write_auth(auth)
    write_stored_api_key(paths.data_dir, "sk-test-paste-key-not-real")
    pr = _make_provider(
        paths,
        auth_path=auth,
        credential_source="grok_build",
        credential_ok=False,
    )
    with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
        pr.rebuild_chat_stack()
    assert pr.credential_ok is True

    h = _ApiHarness(paths, provider=pr)
    try:
        with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
            code, body = h.patch(
                "/api/provider",
                {"credential_source": "api_key"},
            )
        assert code == 200
        assert body["ok"] is True
        assert body["credential_source"] == "api_key"
        assert body["credential_ok"] is True
        assert pr.credential_source == "api_key"
        assert isinstance(pr.chat_client, UsageGatedChatClient)
        # Worker rebound
        assert h.worker.client is pr.chat_client
    finally:
        h.close()


# ---------------------------------------------------------------------------
# PUT / DELETE /api/provider/api-key
# ---------------------------------------------------------------------------


def test_put_api_key_write_only_never_echoes(paths):
    secret = "sk-super-secret-paste-key-xyz"
    pr = _make_provider(
        paths,
        credential_source="grok_build",  # active source stays grok_build
        credential_ok=False,
    )
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.put("/api/provider/api-key", {"api_key": secret})
        assert code == 200
        assert body["ok"] is True
        assert body["api_key_configured"] is True
        # Never echo
        blob = json.dumps(body)
        assert secret not in blob
        assert "sk-super" not in blob
        # File written
        assert api_key_path(paths.data_dir).is_file()
        # Source not auto-switched
        assert pr.credential_source == "grok_build"
        # Status also clean
        code, status = h.get("/api/status")
        assert code == 200
        assert secret not in json.dumps(status)
        assert status["api_key_configured"] is True
    finally:
        h.close()


def test_put_api_key_required(paths):
    pr = _make_provider(paths)
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.put("/api/provider/api-key", {})
        assert code == 400
        assert body["error"] == "api_key required"
        code, body = h.put("/api/provider/api-key", {"api_key": "   "})
        assert code == 400
        assert body["error"] == "api_key required"
    finally:
        h.close()


def test_put_api_key_rebuilds_when_active_source_is_api_key(paths):
    """Failing cold start + paste key with active api_key → live repair."""
    pr = _make_provider(
        paths,
        credential_source="api_key",
        credential_ok=False,
        auth_path=paths.home / "missing-auth.json",
    )
    assert isinstance(pr.chat_client, FailingChatClient)
    h = _ApiHarness(paths, provider=pr)
    try:
        with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
            code, body = h.put(
                "/api/provider/api-key",
                {"api_key": "sk-repair-key-for-cold-start"},
            )
        assert code == 200
        assert body["ok"] is True
        assert body["api_key_configured"] is True
        assert body["credential_ok"] is True
        assert pr.credential_ok is True
        assert isinstance(pr.chat_client, UsageGatedChatClient)
        assert h.worker.client is pr.chat_client
        assert pr.can_open_model_moment() is True
    finally:
        h.close()


def test_delete_api_key_rebuilds_to_failing_when_source_api_key(paths):
    write_stored_api_key(paths.data_dir, "sk-will-delete")
    pr = _make_provider(
        paths,
        credential_source="api_key",
        credential_ok=False,
    )
    with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
        pr.rebuild_chat_stack()
    assert pr.credential_ok is True
    assert isinstance(pr.chat_client, UsageGatedChatClient)

    h = _ApiHarness(paths, provider=pr)
    try:
        with patch.dict("os.environ", {}, clear=False):
            # Ensure env does not keep api_key source alive
            import os

            env_backup = os.environ.pop("XAI_API_KEY", None)
            try:
                code, body = h.delete("/api/provider/api-key")
            finally:
                if env_backup is not None:
                    os.environ["XAI_API_KEY"] = env_backup
        assert code == 200
        assert body["ok"] is True
        assert body["api_key_configured"] is False
        assert body["credential_ok"] is False
        # No silent switch to grok_build
        assert pr.credential_source == "api_key"
        assert isinstance(pr.chat_client, FailingChatClient)
        assert not api_key_path(paths.data_dir).is_file()
    finally:
        h.close()


# ---------------------------------------------------------------------------
# PATCH /api/usage — hard_stop_override
# ---------------------------------------------------------------------------


def test_patch_usage_override_default_off_and_toggle(paths):
    usage_settings = UsageSettings(
        enabled=True,
        weekly_allowed_tokens=10,
        day_allowed_tokens=10,
        hour_allowed_tokens=10,
    )
    meter = UsageMeter.load(paths.data_dir, usage_settings)
    # Over budget
    meter.record(TokenUsage(total_tokens=10))
    assert meter.can_call() is False
    assert meter.snapshot().override_active is False

    pr = _make_provider(
        paths,
        usage=usage_settings,
        meter=meter,
        credential_ok=True,
        auth_path=None,
    )
    # Use Failing client but credential_ok True is inconsistent; rebuild not needed
    # for override tests — can_call is on the meter. For can_open we need non-Failing.
    # Swap chat client to Stub so can_open can pass when override ON.
    pr.chat_client = StubChatClient()
    pr.credential_ok = True

    h = _ApiHarness(paths, provider=pr)
    try:
        # Status shows override_active false by default
        code, status = h.get("/api/status")
        assert code == 200
        assert status["usage"]["override_active"] is False
        assert status["usage"]["hard_stop"] is not None or status["usage"][
            "week_remaining_fraction"
        ] == 0.0

        # Missing field
        code, body = h.patch("/api/usage", {})
        assert code == 400
        assert body["error"] == "hard_stop_override required"

        # Bad type
        code, body = h.patch("/api/usage", {"hard_stop_override": "yes"})
        assert code == 400
        assert body["error"] == "hard_stop_override must be a boolean"

        # Turn ON
        code, body = h.patch("/api/usage", {"hard_stop_override": True})
        assert code == 200
        assert body["ok"] is True
        assert body["usage"]["override_active"] is True
        assert meter.can_call() is True  # unlocks despite over budget
        assert pr.can_open_model_moment() is True
        # PATCH response usage is expanded status block (still only mutates override)
        assert "pace_band" in body["usage"]
        assert "burst_remaining_tokens" in body["usage"]
        assert "hard_stop" in body["usage"]  # would-be level still reported

        # Extra keys in body must not be accepted as mutators — only override.
        code, body = h.patch(
            "/api/usage",
            {"hard_stop_override": True, "pace_band": "green", "week_used_tokens": 0},
        )
        assert code == 200
        assert body["usage"]["override_active"] is True
        # Counters not reset by PATCH
        assert body["usage"]["week_used_tokens"] == 10

        # Status reflects ON
        code, status = h.get("/api/status")
        assert code == 200
        assert status["usage"]["override_active"] is True

        # Turn OFF re-enforces stop
        code, body = h.patch("/api/usage", {"hard_stop_override": False})
        assert code == 200
        assert body["usage"]["override_active"] is False
        assert meter.can_call() is False
    finally:
        h.close()


def test_override_does_not_reset_counters(paths):
    usage_settings = UsageSettings(
        enabled=True,
        weekly_allowed_tokens=100,
        day_allowed_tokens=100,
        hour_allowed_tokens=100,
    )
    meter = UsageMeter.load(paths.data_dir, usage_settings)
    meter.record(TokenUsage(total_tokens=40))
    pr = _make_provider(paths, usage=usage_settings, meter=meter)
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.patch("/api/usage", {"hard_stop_override": True})
        assert code == 200
        assert body["usage"]["week_used_tokens"] == 40
        # Still records under override
        meter.record(TokenUsage(total_tokens=5))
        code, status = h.get("/api/status")
        assert status["usage"]["week_used_tokens"] == 45
        assert status["usage"]["override_active"] is True
    finally:
        h.close()


# ---------------------------------------------------------------------------
# Failing cold start → repair via API (no process restart)
# ---------------------------------------------------------------------------


def test_failing_cold_start_repair_via_put_and_source_switch(paths):
    """Start Failing → paste key → select api_key → live UsageGated stack."""
    pr = _make_provider(
        paths,
        credential_source="grok_build",
        credential_ok=False,
        auth_path=paths.home / "no-auth.json",
    )
    assert isinstance(pr.chat_client, FailingChatClient)
    assert pr.can_open_model_moment() is False

    h = _ApiHarness(paths, provider=pr)
    try:
        code, status = h.get("/api/status")
        assert code == 200
        assert status["credential_ok"] is False

        # Paste key does not auto-switch source; still Failing
        code, body = h.put(
            "/api/provider/api-key",
            {"api_key": "sk-operator-paste-repair"},
        )
        assert code == 200
        assert body["api_key_configured"] is True
        assert pr.credential_source == "grok_build"
        # Still failing because active source is grok_build with missing auth
        assert isinstance(pr.chat_client, FailingChatClient)

        # Select api_key → rebuild → live
        with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
            code, body = h.patch(
                "/api/provider",
                {"credential_source": "api_key"},
            )
        assert code == 200
        assert body["credential_ok"] is True
        assert body["credential_source"] == "api_key"
        assert isinstance(pr.chat_client, UsageGatedChatClient)
        assert h.worker.client is pr.chat_client
        assert pr.can_open_model_moment() is True

        code, status = h.get("/api/status")
        assert status["credential_ok"] is True
        assert status["api_key_configured"] is True
        assert "sk-operator" not in json.dumps(status)
    finally:
        h.close()


# ---------------------------------------------------------------------------
# Continuous remains OFF / routing still works with provider bound
# ---------------------------------------------------------------------------


def test_continuous_patch_still_works_with_provider(paths):
    pr = _make_provider(paths)
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.patch("/api/continuous", {"enabled": False})
        assert code == 200
        assert body.get("ok") is True or body.get("enabled") is False
    finally:
        h.close()


def test_provider_routes_404_unknown(paths):
    pr = _make_provider(paths)
    h = _ApiHarness(paths, provider=pr)
    try:
        code, body = h.patch("/api/provider/nope", {"model": "x"})
        assert code == 404
        code, body = h.put("/api/provider/wrong", {"api_key": "x"})
        assert code == 404
        code, body = h.delete("/api/provider/wrong")
        assert code == 404
    finally:
        h.close()


def test_put_delete_not_found_without_provider_binding_still_404_on_wrong_path(paths):
    h = _ApiHarness(paths, provider=None)
    try:
        code, body = h.put("/api/provider/api-key", {"api_key": "x"})
        assert code == 503
        assert body["error"] == "provider unavailable"
        code, body = h.patch("/api/usage", {"hard_stop_override": True})
        assert code == 503
    finally:
        h.close()
