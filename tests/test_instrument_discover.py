"""Unit tests: grok binary + skill discover (PR2)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from elyra.instrument.discover import (
    GrokNotFoundError,
    GrokSkillsUnavailableError,
    assert_skills_resolvable,
    find_grok_binary,
    find_real_bundled,
    list_resolvable_skills,
)


def test_find_grok_binary_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty_bin"))
    monkeypatch.delenv("GROK_BIN", raising=False)
    monkeypatch.delenv("GROK_HOME", raising=False)
    # Isolate from operator ~/.grok/bin/grok
    monkeypatch.setenv("HOME", str(tmp_path / "no_home"))
    (tmp_path / "no_home").mkdir()
    with pytest.raises(GrokNotFoundError) as ei:
        find_grok_binary(env={
            "PATH": str(tmp_path / "empty_bin"),
            "HOME": str(tmp_path / "no_home"),
        })
    assert ei.value.error_reason == "grok_not_found"


def test_find_grok_binary_from_grok_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    grok = bin_dir / "grok"
    grok.write_text("#!/bin/sh\n", encoding="utf-8")
    grok.chmod(0o755)
    found = find_grok_binary(env={"GROK_BIN": str(grok), "PATH": ""})
    assert found == grok.resolve()


def test_find_grok_binary_from_path(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    grok = bin_dir / "grok"
    grok.write_text("#!/bin/sh\n", encoding="utf-8")
    grok.chmod(0o755)
    found = find_grok_binary(env={"PATH": str(bin_dir)})
    assert found == grok.resolve()


def test_assert_skills_resolvable_ok(tmp_path: Path) -> None:
    skills = tmp_path / "bundled" / "skills"
    for name in ("design", "implement", "execute-plan", "review"):
        (skills / name).mkdir(parents=True)
    assert_skills_resolvable(tmp_path)
    names = list_resolvable_skills(tmp_path)
    assert "design" in names
    assert "implement" in names


def test_assert_skills_resolvable_missing_design(tmp_path: Path) -> None:
    skills = tmp_path / "bundled" / "skills"
    (skills / "implement").mkdir(parents=True)
    with pytest.raises(GrokSkillsUnavailableError) as ei:
        assert_skills_resolvable(tmp_path)
    assert ei.value.error_reason == "grok_skills_unavailable"


def test_assert_skills_minimal_design_implement(tmp_path: Path) -> None:
    skills = tmp_path / "bundled" / "skills"
    (skills / "design").mkdir(parents=True)
    (skills / "implement").mkdir(parents=True)
    assert_skills_resolvable(tmp_path)


def test_find_real_bundled_from_fake_home(tmp_path: Path) -> None:
    home = tmp_path / "op_home"
    bundled = home / ".grok" / "bundled"
    skills = bundled / "skills"
    (skills / "design").mkdir(parents=True)
    (skills / "implement").mkdir(parents=True)
    env = {"HOME": str(home), "PATH": "", "GROK_HOME": ""}
    found = find_real_bundled(env=env)
    assert found == bundled.resolve()
