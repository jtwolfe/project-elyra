"""Frozen git_* tools — path jail + mocked subprocess (no network)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.settings import Settings, ToolsSettings
from elyra.tools.builtin import git_tools
from elyra.tools.builtin.gh_tools import FROZEN_GIT_TOOLS
from elyra.tools.policy import resolve_bundled_tools_root
from elyra.tools.registry import ToolRegistry
from elyra.tools.types import ToolContext, ToolResult


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def repo(home: Path) -> Path:
    r = home / "repo"
    r.mkdir()
    (r / ".git").mkdir()
    return r


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(tools=ToolsSettings(allowed_repo_roots=(str(home),)))


@pytest.fixture
def ctx(home: Path, settings: Settings) -> ToolContext:
    return ToolContext(paths=resolve_paths(home), settings=settings)


@pytest.fixture
def registry(home: Path) -> ToolRegistry:
    return ToolRegistry(
        resolve_paths(home),
        bundled_root=resolve_bundled_tools_root(),
    )


def _ok_proc(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_frozen_git_tools_discovered(registry: ToolRegistry) -> None:
    for name in FROZEN_GIT_TOOLS:
        assert registry.has(name), name
        pkg = registry.get(name)
        assert pkg is not None and pkg.handler is not None
    # Deferred names must not be required.
    assert not registry.has("git_stash")


def test_git_status_invokes_argv_with_cwd(
    ctx: ToolContext, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv, *, cwd, timeout=60.0, env=None):
        captured["argv"] = list(argv)
        captured["cwd"] = str(cwd)
        return _ok_proc("On branch main\n")

    monkeypatch.setattr(git_tools, "run_git", fake_run)
    result = git_tools.git_status({"repo": str(repo)}, ctx)
    assert result.ok
    assert captured["argv"] == ["git", "status"]
    assert captured["cwd"] == str(repo.resolve())
    assert "On branch main" in result.payload["stdout"]


def test_git_status_path_outside_jail(ctx: ToolContext, home: Path) -> None:
    outside = Path("/tmp/elyra-vcs-outside-test-not-allowed")
    result = git_tools.git_status({"repo": str(outside)}, ctx)
    assert not result.ok
    assert result.error_reason == "path_jail"


def test_git_worktree_lifecycle_mocks(
    ctx: ToolContext, repo: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, *, cwd, timeout=60.0, env=None):
        calls.append(list(argv))
        return _ok_proc("ok\n")

    monkeypatch.setattr(git_tools, "run_git", fake_run)
    wt = home / "wt-feature"

    r = git_tools.git_worktree_add(
        {"repo": str(repo), "path": str(wt), "new_branch": "feat/x"},
        ctx,
    )
    assert r.ok
    assert calls[-1][:3] == ["git", "worktree", "add"]
    assert "-b" in calls[-1]
    assert str(wt.resolve()) in calls[-1]

    r = git_tools.git_worktree_list({"repo": str(repo)}, ctx)
    assert r.ok
    assert calls[-1] == ["git", "worktree", "list"]

    r = git_tools.git_worktree_prune({"repo": str(repo)}, ctx)
    assert r.ok
    assert calls[-1] == ["git", "worktree", "prune"]

    # Clean remove (no .git at wt → not dirty)
    r = git_tools.git_worktree_remove(
        {"repo": str(repo), "path": str(wt)},
        ctx,
    )
    assert r.ok
    assert calls[-1] == ["git", "worktree", "remove", str(wt.resolve())]


def test_git_worktree_remove_dirty_requires_confirm(
    ctx: ToolContext, repo: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wt = home / "dirty-wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: ../repo/.git/worktrees/dirty\n", encoding="utf-8")

    def fake_run(argv, *, cwd, timeout=60.0, env=None):
        if argv[:3] == ["git", "status", "--porcelain"]:
            return _ok_proc(" M file.txt\n")
        return _ok_proc()

    monkeypatch.setattr(git_tools, "run_git", fake_run)

    denied = git_tools.git_worktree_remove(
        {"repo": str(repo), "path": str(wt)},
        ctx,
    )
    assert not denied.ok
    assert denied.error_reason == "confirm_required"
    assert denied.payload.get("dirty") is True

    calls: list[list[str]] = []

    def fake_run2(argv, *, cwd, timeout=60.0, env=None):
        calls.append(list(argv))
        if argv[:3] == ["git", "status", "--porcelain"]:
            return _ok_proc(" M file.txt\n")
        return _ok_proc()

    monkeypatch.setattr(git_tools, "run_git", fake_run2)
    ok = git_tools.git_worktree_remove(
        {"repo": str(repo), "path": str(wt), "confirm": True},
        ctx,
    )
    assert ok.ok
    assert "--force" in calls[-1]


def test_git_worktree_add_outside_jail(
    ctx: ToolContext, repo: Path
) -> None:
    result = git_tools.git_worktree_add(
        {"repo": str(repo), "path": "/etc/elyra-not-allowed-wt"},
        ctx,
    )
    assert not result.ok
    assert result.error_reason == "path_jail"


def test_git_commit_and_branch(
    ctx: ToolContext, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, *, cwd, timeout=60.0, env=None):
        calls.append(list(argv))
        return _ok_proc()

    monkeypatch.setattr(git_tools, "run_git", fake_run)
    r = git_tools.git_commit({"repo": str(repo), "message": "msg"}, ctx)
    assert r.ok
    assert calls[-1] == ["git", "commit", "-m", "msg"]

    r = git_tools.git_branch({"repo": str(repo), "name": "feature"}, ctx)
    assert r.ok
    assert calls[-1] == ["git", "branch", "feature"]

    r = git_tools.git_checkout(
        {"repo": str(repo), "ref": "feature", "create": True},
        ctx,
    )
    assert r.ok
    assert calls[-1] == ["git", "checkout", "-b", "feature"]


def test_git_add_missing_paths(ctx: ToolContext, repo: Path) -> None:
    r = git_tools.git_add({"repo": str(repo)}, ctx)
    assert not r.ok
    assert r.error_reason == "missing_paths"
