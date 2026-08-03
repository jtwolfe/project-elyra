"""Unit tests: isolated GROK_HOME seed + access-only auth.json (PR-A / KD-F2)."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path

import pytest

from elyra.instrument.auth_handoff import (
    AUTH_JSON_SCOPE_KEY,
    AUTH_PROVIDER_MODULE,
    SYNTHETIC_EMAIL,
    SYNTHETIC_USER_ID,
    build_auth_provider_command,
    seed_isolated_home,
    write_access_only_auth_json,
)
from elyra.instrument.discover import (
    GrokSkillsUnavailableError,
    assert_skills_resolvable,
)
from elyra.llm.xai_oauth import XAI_OAUTH_CLIENT_ID

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_GOLDEN_AUTH = _FIXTURES / "grok_build_auth_json_external.json"
_ISO_Z_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _fake_bundled(root: Path, *, with_optional: bool = True) -> Path:
    bundled = root / "bundled"
    skills = bundled / "skills"
    for name in ("design", "implement"):
        d = skills / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    if with_optional:
        for name in ("execute-plan", "review"):
            d = skills / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
    return bundled


def test_build_auth_provider_command_uses_absolute_sys_executable(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    cmd = build_auth_provider_command(data_dir)
    assert cmd.startswith(sys.executable)
    assert Path(sys.executable).is_absolute()
    assert "-m" in cmd
    assert AUTH_PROVIDER_MODULE in cmd
    assert "--data-dir" in cmd
    assert str(data_dir.resolve()) in cmd
    # Never bare interpreter basename only as the command prefix.
    assert not cmd.startswith("python ")
    assert not cmd.startswith("python3 ")


def test_seed_layout_bundled_symlink_and_config(tmp_path: Path) -> None:
    real_root = tmp_path / "real_install"
    real_bundled = _fake_bundled(real_root)
    run_dir = tmp_path / "run" / "r1"
    data_dir = tmp_path / "elyra_data"
    data_dir.mkdir()

    seeded = seed_isolated_home(
        run_dir,
        data_dir=data_dir,
        real_bundled=real_bundled,
        access_token="fake-seed-token",
        expires_at="2026-08-03T06:42:10Z",
    )

    grok_home = seeded.grok_home
    assert grok_home == run_dir / "grok_home"
    assert grok_home.is_dir()
    mode = stat.S_IMODE(grok_home.stat().st_mode)
    assert mode == 0o700

    bundled = grok_home / "bundled"
    assert bundled.exists()
    assert bundled.is_symlink()
    assert bundled.resolve() == real_bundled.resolve()

    # Skills resolvable via symlink.
    assert_skills_resolvable(grok_home)
    assert (bundled / "skills" / "design" / "SKILL.md").is_file()
    assert (bundled / "skills" / "implement" / "SKILL.md").is_file()

    config = seeded.config_path.read_text(encoding="utf-8")
    assert "auth_provider_command" in config
    assert sys.executable in config
    assert AUTH_PROVIDER_MODULE in config
    assert str(data_dir.resolve()) in config
    assert seeded.auth_provider_command.startswith(sys.executable)

    # No secrets material in config.toml.
    assert "refresh_token" not in config
    assert "access_token" not in config
    assert "fake-seed-token" not in config

    # Access-only auth.json present.
    assert seeded.auth_json_path is not None
    assert seeded.auth_json_path.is_file()


def test_write_access_only_auth_json_shape_and_mode(tmp_path: Path) -> None:
    """KD-F2/F3/F12/F18: 0600, external mode, no refresh_token, ISO-Z expiry."""
    home = tmp_path / "grok_home"
    home.mkdir()
    token = "fake-access-token-for-hermetic-tests-only"
    exp = "2026-08-03T06:42:10Z"
    created = "2026-08-03T01:42:10Z"

    path = write_access_only_auth_json(
        home,
        access_token=token,
        expires_at=exp,
        create_time=created,
    )
    assert path == home / "auth.json"
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600

    data = json.loads(path.read_text(encoding="utf-8"))
    assert AUTH_JSON_SCOPE_KEY in data
    assert AUTH_JSON_SCOPE_KEY == (
        f"https://auth.x.ai::{XAI_OAUTH_CLIENT_ID}"
    )
    entry = data[AUTH_JSON_SCOPE_KEY]
    assert entry["key"] == token
    assert entry["auth_mode"] == "external"
    assert entry["expires_at"] == exp
    assert entry["create_time"] == created
    assert _ISO_Z_RE.match(entry["expires_at"])
    assert _ISO_Z_RE.match(entry["create_time"])
    assert entry["user_id"] == SYNTHETIC_USER_ID
    assert entry["email"] == SYNTHETIC_EMAIL
    assert entry["oidc_client_id"] == XAI_OAUTH_CLIENT_ID
    assert "refresh_token" not in entry
    # Whole file must not contain the refresh_token key name as a JSON key.
    raw = path.read_text(encoding="utf-8")
    assert '"refresh_token"' not in raw


def test_write_access_only_matches_golden_fixture(tmp_path: Path) -> None:
    """Round-trip against hermetic golden fixture (fake token only)."""
    golden = json.loads(_GOLDEN_AUTH.read_text(encoding="utf-8"))
    scope = AUTH_JSON_SCOPE_KEY
    assert scope in golden
    g_entry = golden[scope]

    home = tmp_path / "gh"
    home.mkdir()
    path = write_access_only_auth_json(
        home,
        access_token=g_entry["key"],
        expires_at=g_entry["expires_at"],
        create_time=g_entry["create_time"],
    )
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written == golden
    assert "refresh_token" not in written[scope]


def test_write_access_only_rejects_empty_token(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="access_token"):
        write_access_only_auth_json(
            tmp_path,
            access_token="  ",
            expires_at="2026-08-03T06:42:10Z",
        )


def test_write_access_only_rejects_empty_expires(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="expires_at"):
        write_access_only_auth_json(
            tmp_path,
            access_token="tok",
            expires_at="",
        )


def test_write_access_only_created_with_0600_not_umask_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """auth.json must be 0600 even under a permissive umask (Issue 1 hardening)."""
    home = tmp_path / "gh"
    home.mkdir()
    old_umask = os.umask(0o000)  # would yield 0o666 for write_text
    try:
        path = write_access_only_auth_json(
            home,
            access_token="umask-test-token",
            expires_at="2026-08-03T06:42:10Z",
            create_time="2026-08-03T01:42:10Z",
        )
    finally:
        os.umask(old_umask)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    raw = path.read_text(encoding="utf-8")
    assert '"refresh_token"' not in raw


def test_seed_with_token_writes_auth_json(tmp_path: Path) -> None:
    real_bundled = _fake_bundled(tmp_path / "install")
    run_dir = tmp_path / "run"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    token = "seed-access-abc"
    exp = "2026-12-01T12:00:00Z"

    seeded = seed_isolated_home(
        run_dir,
        data_dir=data_dir,
        real_bundled=real_bundled,
        access_token=token,
        expires_at=exp,
    )
    auth_path = run_dir / "grok_home" / "auth.json"
    assert auth_path.is_file()
    assert seeded.auth_json_path == auth_path
    mode = stat.S_IMODE(auth_path.stat().st_mode)
    assert mode == 0o600
    data = json.loads(auth_path.read_text(encoding="utf-8"))
    entry = data[AUTH_JSON_SCOPE_KEY]
    assert entry["key"] == token
    assert entry["expires_at"] == exp
    assert entry["auth_mode"] == "external"
    assert "refresh_token" not in entry


def test_seed_without_token_skips_auth_json(tmp_path: Path) -> None:
    """Config-only seed (no mint) leaves auth.json absent — provider-only path."""
    real_bundled = _fake_bundled(tmp_path / "install")
    seeded = seed_isolated_home(
        tmp_path / "run",
        data_dir=tmp_path / "data",
        real_bundled=real_bundled,
    )
    assert seeded.auth_json_path is None
    assert not (seeded.grok_home / "auth.json").exists()


def test_seed_partial_token_raises(tmp_path: Path) -> None:
    real_bundled = _fake_bundled(tmp_path / "install")
    with pytest.raises(ValueError, match="both access_token and expires_at"):
        seed_isolated_home(
            tmp_path / "run",
            data_dir=tmp_path / "data",
            real_bundled=real_bundled,
            access_token="only-token",
        )


def test_seed_never_writes_operator_auth_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PE refresh must never land in operator ~/.grok/auth.json."""
    fake_home = tmp_path / "operator_home"
    fake_grok = fake_home / ".grok"
    fake_grok.mkdir(parents=True)
    operator_auth = fake_grok / "auth.json"
    operator_auth.write_text('{"preexisting": true}\n', encoding="utf-8")
    before = operator_auth.read_text(encoding="utf-8")

    monkeypatch.setenv("HOME", str(fake_home))
    # Path.home() follows HOME on Unix.
    assert Path.home() == fake_home

    real_bundled = _fake_bundled(tmp_path / "install")
    run_dir = tmp_path / "run"
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    seed_isolated_home(
        run_dir,
        data_dir=data_dir,
        real_bundled=real_bundled,
        access_token="tok",
        expires_at="2026-08-03T06:42:10Z",
    )

    assert operator_auth.read_text(encoding="utf-8") == before
    # Run-local auth.json is fine (access-only); operator file untouched.
    assert (run_dir / "grok_home" / "auth.json").is_file()
    run_auth = json.loads((run_dir / "grok_home" / "auth.json").read_text())
    assert "refresh_token" not in run_auth[AUTH_JSON_SCOPE_KEY]


def test_seed_missing_skills_fails(tmp_path: Path) -> None:
    empty_bundled = tmp_path / "empty_bundled"
    empty_bundled.mkdir()
    (empty_bundled / "skills").mkdir()
    with pytest.raises(GrokSkillsUnavailableError):
        seed_isolated_home(
            tmp_path / "run",
            data_dir=tmp_path / "data",
            real_bundled=empty_bundled,
            access_token="t",
            expires_at="2026-08-03T06:42:10Z",
        )


def test_seed_minimal_design_implement_only(tmp_path: Path) -> None:
    real_bundled = _fake_bundled(tmp_path / "install", with_optional=False)
    seeded = seed_isolated_home(
        tmp_path / "run",
        data_dir=tmp_path / "data",
        real_bundled=real_bundled,
        access_token="t",
        expires_at="2026-08-03T06:42:10Z",
    )
    assert_skills_resolvable(seeded.grok_home)
