"""Hermetic unit tests for reserved xAI OAuth token store (PR1)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.llm.oauth_store import (
    OAUTH_FILENAME,
    OAuthBundle,
    OAuthPublicMeta,
    delete_oauth_bundle,
    load_oauth_bundle,
    load_oauth_bundle_optional,
    oauth_is_configured,
    oauth_path,
    persist_oauth_login,
    public_meta,
    save_oauth_bundle,
)
from elyra.llm.xai_oauth import XAI_OAUTH_CLIENT_ID, XAI_OAUTH_SCOPE
from elyra.secrets.policy import RESERVED_SECRET_NAMES, is_reserved_secret_name, validate_secret_name
from elyra.secrets.store import SecretsStore


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return paths.data_dir


def _bundle(
    *,
    access: str = "access-token-secret",
    refresh: str | None = "refresh-token-secret",
    reauth: bool = False,
    email: str | None = "op@example.com",
) -> OAuthBundle:
    return OAuthBundle(
        version=1,
        client_id=XAI_OAUTH_CLIENT_ID,
        access_token=access,
        refresh_token=refresh,
        token_type="Bearer",
        scope=XAI_OAUTH_SCOPE,
        expires_at="2026-07-30T18:00:00Z",
        email=email,
        subject="sub-1",
        obtained_at="2026-07-30T12:00:00Z",
        updated_at="2026-07-30T12:00:00Z",
        auth_method="device_code",
        reauth_required=reauth,
    )


def test_save_load_roundtrip(data_dir: Path) -> None:
    b = _bundle()
    path = save_oauth_bundle(data_dir, b)
    assert path == oauth_path(data_dir)
    assert path.is_file()
    loaded = load_oauth_bundle(data_dir)
    assert loaded.access_token == "access-token-secret"
    assert loaded.refresh_token == "refresh-token-secret"
    assert loaded.email == "op@example.com"
    assert loaded.reauth_required is False
    assert loaded.client_id == XAI_OAUTH_CLIENT_ID


def test_file_mode_0600(data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle())
    path = oauth_path(data_dir)
    mode = stat.S_IMODE(path.stat().st_mode)
    if os.name == "posix":
        assert mode == 0o600


def test_public_meta_never_tokens(data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle())
    meta = public_meta(data_dir)
    assert isinstance(meta, OAuthPublicMeta)
    assert meta.configured is True
    assert meta.email == "op@example.com"
    assert meta.expires_at == "2026-07-30T18:00:00Z"
    assert meta.auth_method == "device_code"
    assert meta.reauth_required is False
    blob = json.dumps(meta.__dict__)
    assert "access-token-secret" not in blob
    assert "refresh-token-secret" not in blob


def test_public_meta_missing(data_dir: Path) -> None:
    meta = public_meta(data_dir)
    assert meta.configured is False
    assert meta.email is None
    assert oauth_is_configured(data_dir) is False


def test_delete_bundle_and_tmp(data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle())
    tmp = data_dir / "secrets" / "xai_oauth.json.tmp"
    tmp.write_text("leftover", encoding="utf-8")
    assert delete_oauth_bundle(data_dir) is True
    assert not oauth_path(data_dir).exists()
    assert not tmp.exists()
    assert delete_oauth_bundle(data_dir) is False
    assert load_oauth_bundle_optional(data_dir) is None


def test_invalid_schema(data_dir: Path) -> None:
    path = oauth_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "access_token": ""}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_oauth_tokens"):
        load_oauth_bundle(data_dir)
    assert load_oauth_bundle_optional(data_dir) is None


def test_reauth_required_roundtrip(data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle(reauth=True))
    loaded = load_oauth_bundle(data_dir)
    assert loaded.reauth_required is True
    meta = public_meta(data_dir)
    assert meta.reauth_required is True


def test_persist_oauth_login_clears_reauth_default_no_prefs(data_dir: Path) -> None:
    """Default activate=False: bundle only; no provider.json footgun in PR1."""
    b = _bundle(reauth=True)
    meta = persist_oauth_login(data_dir, b)
    assert meta.configured is True
    assert meta.reauth_required is False
    loaded = load_oauth_bundle(data_dir)
    assert loaded.reauth_required is False
    assert loaded.access_token == "access-token-secret"
    prefs_path = data_dir / "runtime" / "provider.json"
    assert not prefs_path.exists()


def test_persist_oauth_login_activate_true_is_noop_pr1(data_dir: Path) -> None:
    """activate=True ignored until PR2 (VALID_SOURCES); must not raw-write prefs."""
    meta = persist_oauth_login(data_dir, _bundle(), activate=True)
    assert meta.configured is True
    prefs_path = data_dir / "runtime" / "provider.json"
    assert not prefs_path.exists()


def test_persist_oauth_login_from_dict(data_dir: Path) -> None:
    meta = persist_oauth_login(
        data_dir,
        {
            "access_token": "dict-access",
            "refresh_token": "dict-refresh",
            "client_id": XAI_OAUTH_CLIENT_ID,
            "token_type": "Bearer",
            "expires_at": "2026-08-01T00:00:00Z",
            "email": "d@e.f",
            "auth_method": "device_code",
            "reauth_required": True,  # forced false on login
        },
    )
    assert meta.email == "d@e.f"
    loaded = load_oauth_bundle(data_dir)
    assert loaded.access_token == "dict-access"
    assert loaded.reauth_required is False


def test_reserved_oauth_names_blocked(data_dir: Path) -> None:
    store = SecretsStore(data_dir)
    reserved_oauth = {
        "xai_oauth",
        "xai_oauth.json",
        "xai_oauth.json.tmp",
        "xai_access_token",
    }
    for name in reserved_oauth:
        assert name in RESERVED_SECRET_NAMES
        assert is_reserved_secret_name(name)
        assert is_reserved_secret_name(name.upper())
        with pytest.raises(ValueError, match="reserved"):
            validate_secret_name(name)
        with pytest.raises(ValueError, match="reserved|invalid"):
            store.set_secret(name, "nope")

    # legacy reserved still present
    assert "xai_api_key" in RESERVED_SECRET_NAMES
    assert "meta.json" in RESERVED_SECRET_NAMES


def test_oauth_file_not_listed_as_named_secret(data_dir: Path) -> None:
    save_oauth_bundle(data_dir, _bundle())
    store = SecretsStore(data_dir)
    store.set_secret("gh_token", "tok")
    names = {r["name"] for r in store.list_secrets()}
    assert names == {"gh_token"}
    assert OAUTH_FILENAME not in names
    assert "xai_oauth" not in names
