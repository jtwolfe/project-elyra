"""Unit tests for credential resolver + API key secret store + provider prefs.

Uses temp dirs and fake auth.json only — never real secrets.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.llm.auth import (
    DETAIL_MISSING_API_KEY,
    DETAIL_MISSING_AUTH_JSON,
    DETAIL_MISSING_TOKEN,
    DETAIL_TOKEN_EXPIRED,
    DETAIL_UNKNOWN_SOURCE,
    ENV_XAI_API_KEY,
    GrokAuthError,
    api_key_is_configured,
    api_key_path,
    default_grok_auth_path,
    delete_stored_api_key,
    load_grok_build_session,
    read_stored_api_key,
    resolve_bearer,
    secrets_dir,
    write_stored_api_key,
)
from elyra.llm.provider_prefs import (
    ProviderPrefs,
    load_provider_prefs,
    provider_prefs_path,
    save_provider_prefs,
)


def _nested_auth(
    token: str = "test-access-token-abc123",
    *,
    expires_at: str | None = None,
    email: str | None = "user@example.com",
) -> dict:
    entry: dict = {
        "key": token,
        "auth_mode": "login",
        "email": email,
    }
    if expires_at is not None:
        entry["expires_at"] = expires_at
    return {"https://auth.x.ai::client-id-xyz": entry}


def _future_expires() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(hours=2)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _past_expires() -> str:
    return (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- load_grok_build_session ---


def test_load_nested_auth_json(tmp_path: Path):
    path = tmp_path / "auth.json"
    token = "nested-token-value-xyz"
    path.write_text(json.dumps(_nested_auth(token, expires_at=_future_expires())), encoding="utf-8")
    got, meta = load_grok_build_session(path)
    assert got == token
    assert meta["email"] == "user@example.com"
    assert meta["expires_at"]
    assert meta["token_len"] == len(token)
    assert "nested-token" not in json.dumps(meta)  # prefix only, truncated
    assert meta["token_prefix"].endswith("…")


def test_load_flat_auth_json_access_token(tmp_path: Path):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps({"access_token": "flat-token-99", "email": "a@b.c"}),
        encoding="utf-8",
    )
    got, meta = load_grok_build_session(path)
    assert got == "flat-token-99"
    assert meta["email"] == "a@b.c"
    assert meta["entry"] == "(flat)"


def test_load_flat_auth_json_key_field(tmp_path: Path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"key": "flat-key-token"}), encoding="utf-8")
    got, _meta = load_grok_build_session(path)
    assert got == "flat-key-token"


def test_load_missing_auth_json(tmp_path: Path):
    with pytest.raises(GrokAuthError) as ei:
        load_grok_build_session(tmp_path / "nope.json")
    assert ei.value.detail == DETAIL_MISSING_AUTH_JSON


def test_load_invalid_json(tmp_path: Path):
    path = tmp_path / "auth.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(GrokAuthError) as ei:
        load_grok_build_session(path)
    assert ei.value.detail == "invalid_auth_json"


def test_load_missing_token_field(tmp_path: Path):
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps({"https://auth.x.ai::x": {"email": "x@y.z"}}),
        encoding="utf-8",
    )
    with pytest.raises(GrokAuthError) as ei:
        load_grok_build_session(path)
    assert ei.value.detail == DETAIL_MISSING_TOKEN


def test_default_grok_auth_path():
    assert default_grok_auth_path() == Path.home() / ".grok" / "auth.json"


# --- resolve_bearer: grok_build ---


def test_resolve_grok_build_ok(tmp_path: Path):
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(_nested_auth("bearer-ok", expires_at=_future_expires())),
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    r = resolve_bearer(
        source="grok_build",
        data_dir=data,
        grok_auth_path=auth,
        env={},
    )
    assert r.ok is True
    assert r.source == "grok_build"
    assert r.token == "bearer-ok"
    assert r.detail is None
    assert r.email == "user@example.com"
    assert r.expires_at
    assert r.api_key_configured is False


def test_resolve_grok_build_missing_file(tmp_path: Path):
    r = resolve_bearer(
        source="grok_build",
        data_dir=tmp_path / "data",
        grok_auth_path=tmp_path / "missing.json",
        env={},
    )
    assert r.ok is False
    assert r.token is None
    assert r.detail == DETAIL_MISSING_AUTH_JSON


def test_resolve_grok_build_token_expired(tmp_path: Path):
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(_nested_auth("old-token", expires_at=_past_expires())),
        encoding="utf-8",
    )
    r = resolve_bearer(
        source="grok_build",
        data_dir=tmp_path / "data",
        grok_auth_path=auth,
        env={},
    )
    assert r.ok is False
    assert r.token is None
    assert r.detail == DETAIL_TOKEN_EXPIRED
    assert r.expires_at  # still report expiry for glass
    assert r.email == "user@example.com"


def test_resolve_grok_build_does_not_use_api_key_or_env(tmp_path: Path):
    """Active source only: grok_build must not fall back to stored key / env."""
    data = tmp_path / "data"
    write_stored_api_key(data, "sk-should-not-be-used")
    r = resolve_bearer(
        source="grok_build",
        data_dir=data,
        grok_auth_path=tmp_path / "no-auth.json",
        env={ENV_XAI_API_KEY: "env-should-not-be-used"},
    )
    assert r.ok is False
    assert r.detail == DETAIL_MISSING_AUTH_JSON
    assert r.token is None
    # Configured flag still reflects presence of key material
    assert r.api_key_configured is True


# --- resolve_bearer: api_key ---


def test_resolve_api_key_from_file(tmp_path: Path):
    data = tmp_path / "data"
    write_stored_api_key(data, "sk-file-key")
    r = resolve_bearer(source="api_key", data_dir=data, env={})
    assert r.ok is True
    assert r.token == "sk-file-key"
    assert r.detail is None
    assert r.api_key_configured is True
    assert r.expires_at is None


def test_resolve_api_key_from_env_when_no_file(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    r = resolve_bearer(
        source="api_key",
        data_dir=data,
        env={ENV_XAI_API_KEY: "  sk-env-only  "},
    )
    assert r.ok is True
    assert r.token == "sk-env-only"
    assert r.api_key_configured is True


def test_resolve_api_key_prefers_file_over_env(tmp_path: Path):
    data = tmp_path / "data"
    write_stored_api_key(data, "sk-file")
    r = resolve_bearer(
        source="api_key",
        data_dir=data,
        env={ENV_XAI_API_KEY: "sk-env"},
    )
    assert r.ok is True
    assert r.token == "sk-file"


def test_resolve_api_key_missing(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    r = resolve_bearer(source="api_key", data_dir=data, env={})
    assert r.ok is False
    assert r.token is None
    assert r.detail == DETAIL_MISSING_API_KEY
    assert r.api_key_configured is False


def test_resolve_api_key_does_not_use_auth_json(tmp_path: Path):
    """Active source only: api_key must not fall back to grok_build session."""
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(_nested_auth("session-token", expires_at=_future_expires())),
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    r = resolve_bearer(
        source="api_key",
        data_dir=data,
        grok_auth_path=auth,
        env={},
    )
    assert r.ok is False
    assert r.detail == DETAIL_MISSING_API_KEY
    assert r.token is None


def test_resolve_unknown_source(tmp_path: Path):
    r = resolve_bearer(source="local", data_dir=tmp_path, env={})
    assert r.ok is False
    assert r.detail == DETAIL_UNKNOWN_SOURCE
    assert r.token is None


# --- secret store atomic write ---


def test_write_stored_api_key_atomic_and_mode(tmp_path: Path):
    data = tmp_path / "data"
    path = write_stored_api_key(data, "  sk-secret-value  ")
    assert path == api_key_path(data)
    assert path.is_file()
    assert read_stored_api_key(data) == "sk-secret-value"
    # trailing newline on disk
    assert path.read_text(encoding="utf-8") == "sk-secret-value\n"
    # secrets dir 0700, file 0600 on POSIX
    if os.name == "posix":
        dir_mode = stat.S_IMODE(secrets_dir(data).stat().st_mode)
        file_mode = stat.S_IMODE(path.stat().st_mode)
        assert dir_mode == 0o700
        assert file_mode == 0o600
    # no leftover tmp
    assert not (secrets_dir(data) / "xai_api_key.tmp").exists()


def test_write_stored_api_key_rejects_empty(tmp_path: Path):
    with pytest.raises(ValueError):
        write_stored_api_key(tmp_path / "data", "   ")
    assert read_stored_api_key(tmp_path / "data") is None


def test_write_stored_api_key_overwrites(tmp_path: Path):
    data = tmp_path / "data"
    write_stored_api_key(data, "first")
    write_stored_api_key(data, "second")
    assert read_stored_api_key(data) == "second"


def test_delete_stored_api_key(tmp_path: Path):
    data = tmp_path / "data"
    write_stored_api_key(data, "sk-del")
    assert delete_stored_api_key(data) is True
    assert read_stored_api_key(data) is None
    assert delete_stored_api_key(data) is False


def test_api_key_is_configured_file_or_env(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    assert api_key_is_configured(data, env={}) is False
    assert api_key_is_configured(data, env={ENV_XAI_API_KEY: "x"}) is True
    write_stored_api_key(data, "sk")
    assert api_key_is_configured(data, env={}) is True


# --- provider prefs ---


def test_load_provider_prefs_missing(tmp_path: Path):
    prefs = load_provider_prefs(tmp_path)
    assert prefs.model is None
    assert prefs.credential_source is None


def test_save_and_load_provider_prefs(tmp_path: Path):
    path = save_provider_prefs(
        tmp_path,
        ProviderPrefs(model="grok-4.5", credential_source="api_key"),
    )
    assert path == provider_prefs_path(tmp_path)
    assert path.is_file()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["model"] == "grok-4.5"
    assert raw["credential_source"] == "api_key"
    assert "updated_at" in raw
    # no secrets keys
    assert "api_key" not in raw
    assert "token" not in raw

    prefs = load_provider_prefs(tmp_path)
    assert prefs.model == "grok-4.5"
    assert prefs.credential_source == "api_key"


def test_load_provider_prefs_ignores_invalid_credential_source(tmp_path: Path):
    path = provider_prefs_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"model": "m", "credential_source": "oauth"}),
        encoding="utf-8",
    )
    prefs = load_provider_prefs(tmp_path)
    assert prefs.model == "m"
    assert prefs.credential_source is None


def test_save_provider_prefs_rejects_invalid_source(tmp_path: Path):
    with pytest.raises(ValueError):
        save_provider_prefs(
            tmp_path, ProviderPrefs(credential_source="not_a_source")
        )


def test_load_provider_prefs_corrupt_json(tmp_path: Path):
    path = provider_prefs_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{bad", encoding="utf-8")
    prefs = load_provider_prefs(tmp_path)
    assert prefs.model is None


# --- ensure_data_dirs creates secrets ---


def test_ensure_data_dirs_creates_secrets(tmp_path: Path):
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    secrets = paths.data_dir / "secrets"
    assert secrets.is_dir()
    if os.name == "posix":
        assert stat.S_IMODE(secrets.stat().st_mode) == 0o700
