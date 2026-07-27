"""PR5a: provider-aware supervisor/CLI merge order + client stack selection.

Hermetic — fake auth.json / temp data dirs only; no live xAI calls.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from elyra.config import resolve_paths
from elyra.llm.auth import DETAIL_MISSING_AUTH_JSON, write_stored_api_key
from elyra.llm.client import (
    FailingChatClient,
    HttpChatClient,
    StubChatClient,
    UsageGatedChatClient,
)
from elyra.llm.provider_prefs import ProviderPrefs, save_provider_prefs
from elyra.llm.usage import TokenUsage, UsageMeter
from elyra.runtime.config import (
    RuntimeConfig,
    load_merged_settings,
    runtime_config_from_settings,
)
from elyra.runtime.provider_runtime import (
    ProviderRuntime,
    credential_detail_message,
    format_usage_posture,
)
from elyra.runtime.supervisor import ElyraSupervisor
from elyra.settings import UsageSettings, default_settings, load_settings, merge_cli_overrides


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


# ---------------------------------------------------------------------------
# Config merge order
# ---------------------------------------------------------------------------


def test_merge_defaults_then_toml_then_prefs_then_cli(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    data = home / "data"
    data.mkdir()
    (data / "runtime").mkdir()

    (home / "elyra.toml").write_text(
        """
[provider]
name = "local"
model = "toml-model"
credential_source = "api_key"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    save_provider_prefs(
        data,
        ProviderPrefs(model="prefs-model", credential_source="grok_build"),
    )

    # prefs win over toml for model + credential_source only; name stays local
    s1 = load_merged_settings(home, data)
    assert s1.provider.name == "local"
    assert s1.provider.model == "prefs-model"
    assert s1.provider.credential_source == "grok_build"

    # CLI wins over prefs
    s2 = load_merged_settings(
        home,
        data,
        provider="xai",
        model="cli-model",
        credential_source="api_key",
    )
    assert s2.provider.name == "xai"
    assert s2.provider.model == "cli-model"
    assert s2.provider.credential_source == "api_key"

    # None CLI flags do not clobber prefs
    s3 = load_merged_settings(
        home,
        data,
        provider=None,
        model=None,
        credential_source=None,
    )
    assert s3.provider.model == "prefs-model"
    assert s3.provider.credential_source == "grok_build"


def test_merge_no_usage_meter_flag(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    data = home / "data"
    data.mkdir()
    s = load_merged_settings(home, data, no_usage_meter=True)
    assert s.usage.enabled is False


def test_runtime_config_no_process_launch_fields():
    """RuntimeConfig no longer starts a local inference process (PR2)."""
    s = default_settings()
    cfg = runtime_config_from_settings(s)
    assert cfg.provider_name == "xai"
    assert not hasattr(cfg, "start_llama_server")
    assert hasattr(cfg, "local")

    s_local = merge_cli_overrides(load_settings(), {"provider": {"name": "local"}})
    cfg_local = runtime_config_from_settings(s_local)
    assert cfg_local.provider_name == "local"
    assert not hasattr(cfg_local, "start_llama_server")

    cfg_stub = runtime_config_from_settings(s_local, stub_llm=True)
    assert cfg_stub.provider_name == "local"


def test_cli_no_llama_flag_removed_stub_only_forces_stub():
    """--no-llama removed; only --stub-llm forces StubChatClient."""
    from elyra.cli import build_parser

    parser = build_parser()
    # Removed flag must error
    with pytest.raises(SystemExit):
        parser.parse_args(["start", "--no-llama"])
    with pytest.raises(SystemExit):
        parser.parse_args(["start", "--context-tokens", "4096"])

    args = parser.parse_args(["start", "--stub-llm"])
    assert bool(args.stub_llm) is True
    use_stub = bool(args.stub_llm)
    assert use_stub is True

    args_plain = parser.parse_args(["start"])
    assert bool(args_plain.stub_llm) is False


# ---------------------------------------------------------------------------
# Supervisor client stack selection
# ---------------------------------------------------------------------------


def _supervisor_xai(
    tmp_path: Path,
    *,
    auth: bool = False,
    api_key: str | None = None,
    credential_source: str = "grok_build",
    usage_enabled: bool = True,
    stub: bool = False,
) -> ElyraSupervisor:
    home = tmp_path / "elyra-home"
    home.mkdir(exist_ok=True)
    paths = resolve_paths(home)
    paths.ensure_data_dirs()

    auth_path = home / "fake-auth.json"
    if auth:
        _write_auth(auth_path)

    if api_key is not None:
        write_stored_api_key(paths.data_dir, api_key)

    cfg = RuntimeConfig(
        api_host="127.0.0.1",
        api_port=0,
        provider_name="xai",
        model="grok-4.5",
        model_label="Grok 4.5",
        credential_source=credential_source,
        grok_auth_path=str(auth_path) if auth else str(home / "missing-auth.json"),
        usage=UsageSettings(enabled=usage_enabled),
    )

    # Bind free port
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    cfg.api_port = sock.getsockname()[1]
    sock.close()

    sup = ElyraSupervisor(paths=paths, config=cfg, use_stub_llm=stub)
    return sup


def test_supervisor_xai_missing_creds_uses_failing_and_loads_meter(tmp_path: Path):
    sup = _supervisor_xai(tmp_path, auth=False)
    try:
        sup.start()
        assert isinstance(sup._worker.client, FailingChatClient)
        assert sup.state.credential_ok is False
        assert sup.state.credential_detail == DETAIL_MISSING_AUTH_JSON
        assert sup.state.chat_ready is False
        assert sup.state.chat_error is None
        pr = sup.provider_runtime
        assert pr is not None
        assert pr.meter is not None  # meter loaded even when !credential_ok
        assert isinstance(pr.chat_client, FailingChatClient)
        assert pr.http_client is None
        assert pr.can_open_model_moment() is False
        # Failing never echoes user content
        with pytest.raises(RuntimeError, match="llm unavailable"):
            pr.chat_client.chat_completion([{"role": "user", "content": "secret"}])
    finally:
        sup.shutdown()


def test_supervisor_xai_ok_uses_usage_gated_stack(tmp_path: Path):
    sup = _supervisor_xai(tmp_path, auth=True)
    try:
        # refresh_models hits network — stub it out for hermetic test
        with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
            sup.start()
        client = sup._worker.client
        assert isinstance(client, UsageGatedChatClient)
        pr = sup.provider_runtime
        assert pr is not None
        assert pr.credential_ok is True
        assert pr.http_client is not None
        assert pr.http_client.profile == "xai"
        assert pr.http_client.chat_url == "https://api.x.ai/v1/chat/completions"
        assert pr.can_open_model_moment() is True
        assert sup.state.credential_ok is True
        assert sup.state.credential_email == "op@example.com"
        assert sup.state.chat_ready is True
        assert sup.state.chat_error is None
    finally:
        sup.shutdown()


def test_supervisor_xai_api_key_source(tmp_path: Path):
    sup = _supervisor_xai(
        tmp_path,
        auth=False,
        api_key="sk-test-key-not-real",
        credential_source="api_key",
    )
    try:
        with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
            sup.start()
        assert sup.state.credential_ok is True
        assert isinstance(sup._worker.client, UsageGatedChatClient)
        assert sup.state.api_key_configured is True
    finally:
        sup.shutdown()


def test_supervisor_stub_llm_uses_stub_not_failing(tmp_path: Path):
    sup = _supervisor_xai(tmp_path, auth=False, stub=True)
    try:
        sup.start()
        assert isinstance(sup._worker.client, StubChatClient)
        assert not isinstance(sup._worker.client, FailingChatClient)
    finally:
        sup.shutdown()


def test_supervisor_local_uses_failing_not_stub(tmp_path: Path):
    """KD2: provider=local without --stub-llm → FailingChatClient."""
    home = tmp_path / "home"
    home.mkdir()
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cfg = RuntimeConfig(
        api_host="127.0.0.1",
        api_port=port,
        provider_name="local",
        model="local",
        model_label="local",
    )
    sup = ElyraSupervisor(paths=paths, config=cfg, use_stub_llm=False)
    try:
        sup.start()
        assert isinstance(sup._worker.client, FailingChatClient)
        assert not isinstance(sup._worker.client, StubChatClient)
        assert sup.provider_runtime is not None
        assert sup.provider_runtime.provider_name == "local"
        assert sup.provider_runtime.can_open_model_moment() is False
        assert sup.state.chat_ready is False
        assert sup.state.chat_error == "local_not_implemented"
        # rebuild must never dial for_local / open HTTP
        with patch(
            "elyra.runtime.provider_runtime.HttpChatClient.for_local"
        ) as mock_for_local:
            sup.provider_runtime.rebuild_chat_stack()
            mock_for_local.assert_not_called()
        assert isinstance(sup.provider_runtime.chat_client, FailingChatClient)
    finally:
        sup.shutdown()


def test_supervisor_local_stub_llm_uses_stub(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cfg = RuntimeConfig(
        api_host="127.0.0.1",
        api_port=port,
        provider_name="local",
        model="local",
        model_label="local",
    )
    sup = ElyraSupervisor(paths=paths, config=cfg, use_stub_llm=True)
    try:
        sup.start()
        assert isinstance(sup._worker.client, StubChatClient)
        assert not isinstance(sup._worker.client, FailingChatClient)
        assert sup.state.chat_error == "stub_llm"
        assert sup.state.chat_ready is False
        assert sup.provider_runtime is not None
        assert sup.provider_runtime.stub_llm is True
    finally:
        sup.shutdown()


def test_stub_llm_survives_apply_model_and_rebuild_local(tmp_path: Path):
    """--stub-llm must stay Stub across model picker apply + rebuild (local)."""
    home = tmp_path / "home"
    home.mkdir()
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cfg = RuntimeConfig(
        api_host="127.0.0.1",
        api_port=port,
        provider_name="local",
        model="local",
        model_label="local",
    )
    sup = ElyraSupervisor(paths=paths, config=cfg, use_stub_llm=True)
    try:
        sup.start()
        pr = sup.provider_runtime
        assert pr is not None
        assert isinstance(pr.chat_client, StubChatClient)
        can_open_before = pr.can_open_model_moment()

        with patch(
            "elyra.runtime.provider_runtime.HttpChatClient.for_local"
        ) as mock_for_local:
            with patch(
                "elyra.runtime.provider_runtime.HttpChatClient.for_xai"
            ) as mock_for_xai:
                pr.apply_model("local")
                pr.rebuild_chat_stack()
                mock_for_local.assert_not_called()
                mock_for_xai.assert_not_called()

        assert isinstance(pr.chat_client, StubChatClient)
        assert not isinstance(pr.chat_client, FailingChatClient)
        assert sup._worker.client is pr.chat_client
        assert isinstance(sup._worker.client, StubChatClient)
        assert sup.state.chat_error == "stub_llm"
        assert sup.state.chat_ready is False
        assert pr.can_open_model_moment() is can_open_before
        assert pr.stub_llm is True
        assert pr.http_client is None
    finally:
        sup.shutdown()


def test_stub_llm_survives_apply_model_and_rebuild_xai(tmp_path: Path):
    """--stub-llm + valid auth must not escape to live HttpChatClient on apply/rebuild."""
    home = tmp_path / "home"
    home.mkdir()
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    auth = home / "auth.json"
    _write_auth(auth, token="must-not-be-used-for-http")

    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cfg = RuntimeConfig(
        api_host="127.0.0.1",
        api_port=port,
        provider_name="xai",
        model="grok-4.5",
        model_label="Grok 4.5",
        credential_source="grok_build",
        grok_auth_path=str(auth),
    )
    sup = ElyraSupervisor(paths=paths, config=cfg, use_stub_llm=True)
    try:
        sup.start()
        pr = sup.provider_runtime
        assert pr is not None
        assert isinstance(pr.chat_client, StubChatClient)
        assert pr.stub_llm is True
        assert pr.http_client is None

        with patch(
            "elyra.runtime.provider_runtime.HttpChatClient.for_xai"
        ) as mock_for_xai:
            with patch(
                "elyra.runtime.provider_runtime.HttpChatClient.for_local"
            ) as mock_for_local:
                pr.apply_model("grok-4.3")
                assert pr.model == "grok-4.3"
                pr.rebuild_chat_stack()
                mock_for_xai.assert_not_called()
                mock_for_local.assert_not_called()

        assert isinstance(pr.chat_client, StubChatClient)
        assert not isinstance(pr.chat_client, HttpChatClient)
        assert not isinstance(pr.chat_client, FailingChatClient)
        assert isinstance(sup._worker.client, StubChatClient)
        assert sup.state.chat_error == "stub_llm"
        assert sup.state.chat_ready is False
        assert pr.http_client is None
    finally:
        sup.shutdown()


def test_supervisor_does_not_start_llama_for_xai(tmp_path: Path):
    """No llama process methods remain; xai start still works."""
    sup = _supervisor_xai(tmp_path, auth=False)
    assert not hasattr(ElyraSupervisor, "_start_llama_server")
    assert not hasattr(sup, "_llama_proc")
    try:
        sup.start()
        assert isinstance(sup._worker.client, FailingChatClient)
    finally:
        sup.shutdown()


# ---------------------------------------------------------------------------
# ProviderRuntime rebuild + can_open_model_moment
# ---------------------------------------------------------------------------


def test_rebuild_chat_stack_repairs_failing_to_gated(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    auth = home / "auth.json"
    _write_auth(auth, token="repair-token")

    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cfg = RuntimeConfig(
        api_host="127.0.0.1",
        api_port=port,
        provider_name="xai",
        grok_auth_path=str(home / "missing.json"),  # cold start fail
    )
    sup = ElyraSupervisor(paths=paths, config=cfg)
    try:
        sup.start()
        pr = sup.provider_runtime
        assert pr is not None
        assert isinstance(pr.chat_client, FailingChatClient)
        assert pr.can_open_model_moment() is False

        # Point at valid auth and rebuild (live repair, no restart)
        pr.grok_auth_path = auth
        with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
            pr.rebuild_chat_stack()

        assert pr.credential_ok is True
        assert isinstance(pr.chat_client, UsageGatedChatClient)
        assert pr.http_client is not None
        # Worker rebound
        assert sup._worker.client is pr.chat_client
        assert pr.can_open_model_moment() is True
        assert sup.state.credential_ok is True
    finally:
        sup.shutdown()


def test_can_open_model_moment_respects_budget(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    auth = home / "auth.json"
    _write_auth(auth)

    usage = UsageSettings(
        enabled=True,
        weekly_allowed_tokens=10,
        day_allowed_tokens=10,
        hour_allowed_tokens=10,
    )
    meter = UsageMeter.load(paths.data_dir, usage)
    # Exhaust budget
    meter.record(TokenUsage(total_tokens=10))
    assert meter.can_call() is False

    pr = ProviderRuntime(
        meter=meter,
        http_client=None,
        chat_client=FailingChatClient("x"),  # will replace
        worker=None,
        usage_settings=usage,
        xai_config=None,
        local_config=None,
        gate=None,
        prefs_path=paths.data_dir / "runtime" / "provider.json",
        data_dir=paths.data_dir,
        provider_name="xai",
        model="grok-4.5",
        model_label="Grok 4.5",
        credential_source="grok_build",
        credential_ok=True,
        credential_detail=None,
        credential_expires_at=None,
        credential_email=None,
        api_key_configured=False,
        grok_auth_path=auth,
    )
    # Still Failing client → False even if we flip credential_ok
    assert pr.can_open_model_moment() is False

    # After rebuild to real stack, budget still blocks
    with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
        pr.rebuild_chat_stack()
    assert pr.credential_ok is True
    assert isinstance(pr.chat_client, UsageGatedChatClient)
    assert pr.can_open_model_moment() is False  # over budget, override OFF

    # Override unlocks can_open (and can_call)
    pr.set_hard_stop_override(True)
    assert pr.can_open_model_moment() is True


def test_apply_credential_source_fail_leaves_previous(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    auth = home / "auth.json"
    _write_auth(auth)

    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cfg = RuntimeConfig(
        api_host="127.0.0.1",
        api_port=port,
        provider_name="xai",
        credential_source="grok_build",
        grok_auth_path=str(auth),
    )
    sup = ElyraSupervisor(paths=paths, config=cfg)
    try:
        with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
            sup.start()
        pr = sup.provider_runtime
        assert pr is not None
        assert pr.credential_ok is True
        prev_client = pr.chat_client

        # Switch to api_key with no key → leave previous
        resolution = pr.apply_credential_source("api_key")
        assert resolution.ok is False
        assert pr.credential_source == "grok_build"
        assert pr.chat_client is prev_client
        assert pr.credential_ok is True
    finally:
        sup.shutdown()


def test_status_fields_never_contain_token(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    auth = home / "auth.json"
    token = "super-secret-token-value-xyz"
    _write_auth(auth, token=token)

    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cfg = RuntimeConfig(
        api_host="127.0.0.1",
        api_port=port,
        provider_name="xai",
        grok_auth_path=str(auth),
    )
    sup = ElyraSupervisor(paths=paths, config=cfg)
    try:
        with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
            sup.start()
        pr = sup.provider_runtime
        assert pr is not None
        blob = json.dumps(pr.status_provider_fields()) + json.dumps(sup.state.snapshot())
        assert token not in blob
        assert "super-secret" not in blob
    finally:
        sup.shutdown()


def test_credential_detail_message_and_usage_posture():
    assert "auth.json" in (credential_detail_message("missing_auth_json") or "")
    assert credential_detail_message(None) is None
    assert format_usage_posture(None, enabled=False) == "disabled"


def test_usage_gated_on_stack_when_usage_disabled_still_http(tmp_path: Path):
    """When usage.enabled=false, stack is bare HttpChatClient (not UsageGated)."""
    sup = _supervisor_xai(tmp_path, auth=True, usage_enabled=False)
    try:
        with patch.object(ProviderRuntime, "refresh_models", return_value=["grok-4.5"]):
            sup.start()
        assert isinstance(sup._worker.client, HttpChatClient)
        assert not isinstance(sup._worker.client, UsageGatedChatClient)
        assert sup.provider_runtime is not None
        # meter still loaded
        assert sup.provider_runtime.meter is not None
        assert sup.provider_runtime.can_open_model_moment() is True
    finally:
        sup.shutdown()


def test_provider_runtime_worker_rebind_is_thread_safe_enough(tmp_path: Path):
    """rebuild under lock does not leave worker.client as Failing after success."""
    home = tmp_path / "home"
    home.mkdir()
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    auth = home / "auth.json"
    _write_auth(auth)

    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    cfg = RuntimeConfig(
        api_host="127.0.0.1",
        api_port=port,
        provider_name="xai",
        grok_auth_path=str(home / "nope.json"),
    )
    sup = ElyraSupervisor(paths=paths, config=cfg)
    try:
        sup.start()
        pr = sup.provider_runtime
        assert pr is not None
        pr.grok_auth_path = auth
        errors: list[BaseException] = []

        def _rebuild() -> None:
            try:
                with patch.object(
                    ProviderRuntime, "refresh_models", return_value=["grok-4.5"]
                ):
                    pr.rebuild_chat_stack()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=_rebuild) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        assert not errors
        assert pr.credential_ok is True
        assert isinstance(sup._worker.client, UsageGatedChatClient)
    finally:
        sup.shutdown()
