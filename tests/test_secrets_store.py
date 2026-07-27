"""PR5: named secrets file store (meta.json + values/, reserved xai_api_key)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.llm.auth import read_stored_api_key, write_stored_api_key
from elyra.secrets.policy import RESERVED_SECRET_NAMES, validate_secret_name
from elyra.secrets.store import SecretsStore


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return paths.data_dir


def test_set_list_get_value_redacted(data_dir: Path) -> None:
    store = SecretsStore(data_dir)
    secret = "super-secret-token-xyz"
    meta = store.set_secret("gh_token", secret, grants=["gh_api", "gh_pr_create"])
    assert meta["name"] == "gh_token"
    assert "value" not in meta
    assert secret not in json.dumps(meta)
    assert meta["grants"] == ["gh_api", "gh_pr_create"]

    rows = store.list_secrets()
    assert len(rows) == 1
    assert rows[0]["name"] == "gh_token"
    assert "value" not in rows[0]
    blob = json.dumps(rows)
    assert secret not in blob

    assert store.get_value("gh_token") == secret
    assert store.get_value("missing") is None


def test_value_file_mode_0600(data_dir: Path) -> None:
    store = SecretsStore(data_dir)
    store.set_secret("my_token", "abc123")
    path = store.values_dir / "my_token"
    assert path.is_file()
    mode = stat.S_IMODE(path.stat().st_mode)
    # Best-effort on filesystems that support chmod.
    if os.name == "posix":
        assert mode == 0o600


def test_reserved_xai_api_key_blocked(data_dir: Path) -> None:
    store = SecretsStore(data_dir)
    write_stored_api_key(data_dir, "sk-provider-key")
    assert read_stored_api_key(data_dir) == "sk-provider-key"

    for name in RESERVED_SECRET_NAMES:
        with pytest.raises(ValueError, match="reserved|invalid"):
            store.set_secret(name, "nope")

    with pytest.raises(ValueError, match="reserved"):
        validate_secret_name("xai_api_key")

    # Provider key file untouched.
    assert read_stored_api_key(data_dir) == "sk-provider-key"
    assert not (store.values_dir / "xai_api_key").exists()


def test_delete_secret(data_dir: Path) -> None:
    store = SecretsStore(data_dir)
    store.set_secret("tmp_tok", "v1")
    assert store.delete_secret("tmp_tok") is True
    assert store.get_value("tmp_tok") is None
    assert store.list_secrets() == []
    assert store.delete_secret("tmp_tok") is False


def test_set_grants(data_dir: Path) -> None:
    store = SecretsStore(data_dir)
    store.set_secret("gh_token", "tok", grants=[])
    meta = store.set_grants("gh_token", ["gh_api"])
    assert meta["grants"] == ["gh_api"]
    with pytest.raises(ValueError, match="secret_not_found"):
        store.set_grants("nope", ["gh_api"])


def test_empty_value_rejected(data_dir: Path) -> None:
    store = SecretsStore(data_dir)
    with pytest.raises(ValueError, match="empty"):
        store.set_secret("x", "   ")


def test_layout_coexists_with_meta_and_values(data_dir: Path) -> None:
    store = SecretsStore(data_dir)
    store.set_secret("a", "1")
    assert (data_dir / "secrets" / "meta.json").is_file()
    assert (data_dir / "secrets" / "values").is_dir()
    meta = json.loads((data_dir / "secrets" / "meta.json").read_text(encoding="utf-8"))
    assert "a" in meta["secrets"]
    assert "value" not in meta["secrets"]["a"]
