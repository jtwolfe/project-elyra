"""Unit tests: isolated GROK_HOME seed + auth_provider_command (PR2 / KD5)."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from elyra.instrument.auth_handoff import (
    AUTH_PROVIDER_MODULE,
    build_auth_provider_command,
    seed_isolated_home,
)
from elyra.instrument.discover import (
    GrokSkillsUnavailableError,
    assert_skills_resolvable,
)


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

    # No secrets material.
    assert "refresh_token" not in config
    assert "access_token" not in config


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

    seed_isolated_home(run_dir, data_dir=data_dir, real_bundled=real_bundled)

    assert operator_auth.read_text(encoding="utf-8") == before
    assert not (run_dir / "grok_home" / "auth.json").exists()


def test_seed_missing_skills_fails(tmp_path: Path) -> None:
    empty_bundled = tmp_path / "empty_bundled"
    empty_bundled.mkdir()
    (empty_bundled / "skills").mkdir()
    with pytest.raises(GrokSkillsUnavailableError):
        seed_isolated_home(
            tmp_path / "run",
            data_dir=tmp_path / "data",
            real_bundled=empty_bundled,
        )


def test_seed_minimal_design_implement_only(tmp_path: Path) -> None:
    real_bundled = _fake_bundled(tmp_path / "install", with_optional=False)
    seeded = seed_isolated_home(
        tmp_path / "run",
        data_dir=tmp_path / "data",
        real_bundled=real_bundled,
    )
    assert_skills_resolvable(seeded.grok_home)
