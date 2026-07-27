"""VCS path jail (IK11) — effective roots, escape refusal, symlink."""

from __future__ import annotations

from pathlib import Path

import pytest

from elyra.config import project_root, resolve_paths
from elyra.settings import Settings, ToolsSettings, default_settings
from elyra.tools.vcs_jail import (
    PathJailError,
    effective_allowed_roots,
    path_in_jail,
    resolve_repo_path,
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


def test_empty_allowed_roots_use_site_project_and_home(home: Path) -> None:
    paths = resolve_paths(home)
    settings = default_settings()
    assert settings.tools.allowed_repo_roots == ()
    roots = effective_allowed_roots(settings, paths)
    assert roots == [project_root().resolve(), paths.home.resolve()]
    # Only those two — not arbitrary ambient paths.
    assert len(roots) == 2


def test_configured_allowed_roots(home: Path) -> None:
    paths = resolve_paths(home)
    allowed = home / "code"
    allowed.mkdir()
    settings = Settings(tools=ToolsSettings(allowed_repo_roots=(str(allowed),)))
    roots = effective_allowed_roots(settings, paths)
    assert roots == [allowed.resolve()]


def test_path_outside_jail_refused(home: Path) -> None:
    jail = home / "jail"
    jail.mkdir()
    outside = home / "outside"
    outside.mkdir()
    (outside / ".git").mkdir()
    with pytest.raises(PathJailError) as ei:
        resolve_repo_path(str(outside), [jail])
    assert ei.value.reason == "path_jail"


def test_dotdot_escape_refused(home: Path) -> None:
    jail = home / "jail"
    (jail / "repo").mkdir(parents=True)
    (jail / "repo" / ".git").mkdir()
    # Path that resolves outside via ..
    raw = str(jail / "repo" / ".." / ".." / "etc")
    # May or may not exist; must fail jail if outside.
    target = Path(raw).resolve()
    if path_in_jail(target, [jail]):
        pytest.skip("resolved path unexpectedly inside jail")
    with pytest.raises(PathJailError) as ei:
        resolve_repo_path(raw, [jail], require_git=False)
    assert ei.value.reason == "path_jail"


def test_symlink_escape_refused(home: Path) -> None:
    jail = home / "jail"
    jail.mkdir()
    secret = home / "secret"
    secret.mkdir()
    (secret / "file.txt").write_text("x", encoding="utf-8")
    link = jail / "escape"
    link.symlink_to(secret)
    with pytest.raises(PathJailError) as ei:
        resolve_repo_path(str(link), [jail], require_git=False)
    assert ei.value.reason == "path_jail"


def test_repo_inside_jail_ok(home: Path) -> None:
    jail = home / "jail"
    repo = jail / "myrepo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    got = resolve_repo_path(str(repo), [jail])
    assert got == repo.resolve()


def test_missing_git_marker(home: Path) -> None:
    jail = home / "jail"
    repo = jail / "notgit"
    repo.mkdir(parents=True)
    with pytest.raises(PathJailError) as ei:
        resolve_repo_path(str(repo), [jail], require_git=True)
    assert ei.value.reason == "not_a_repo"


def test_empty_path_invalid(home: Path) -> None:
    with pytest.raises(PathJailError) as ei:
        resolve_repo_path("  ", [home])
    assert ei.value.reason == "invalid_path"


def test_relative_path_joins_base(home: Path) -> None:
    jail = home / "jail"
    repo = jail / "r"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    nested = repo / "sub"
    nested.mkdir()
    got = resolve_repo_path("sub", [jail], require_git=False, base=repo)
    assert got == nested.resolve()
