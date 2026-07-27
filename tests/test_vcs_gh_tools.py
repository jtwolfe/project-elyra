"""Frozen gh_* tools — auth_unavailable soft-fail + mocked subprocess."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from elyra.config import resolve_paths
from elyra.secrets.policy import TOOL_SECRET_REQUIREMENTS
from elyra.secrets.store import SecretsStore
from elyra.tools.builtin import gh_tools
from elyra.tools.builtin.gh_tools import FROZEN_GH_TOOLS, FROZEN_GIT_TOOLS
from elyra.tools.policy import resolve_bundled_tools_root
from elyra.tools.registry import ToolRegistry
from elyra.tools.types import ToolContext


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def paths(home: Path):
    return resolve_paths(home)


@pytest.fixture
def ctx_no_token(paths) -> ToolContext:
    return ToolContext(paths=paths, extras={"secret_env": {}})


@pytest.fixture
def ctx_with_token(paths) -> ToolContext:
    return ToolContext(
        paths=paths,
        extras={"secret_env": {"GH_TOKEN": "ghs_test_token_value"}},
    )


@pytest.fixture
def registry(paths) -> ToolRegistry:
    return ToolRegistry(paths, bundled_root=resolve_bundled_tools_root())


def _ok_proc(stdout: str = "ok\n", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


def test_frozen_gh_tools_discovered(registry: ToolRegistry) -> None:
    for name in FROZEN_GH_TOOLS:
        assert registry.has(name), name
        assert name in TOOL_SECRET_REQUIREMENTS
        assert TOOL_SECRET_REQUIREMENTS[name] == ["gh_token"]
    # Deferred names not required.
    assert not registry.has("gh_repo_view")
    assert not registry.has("git_stash")
    # git frozen set present too
    for name in FROZEN_GIT_TOOLS:
        assert registry.has(name), name


def test_every_frozen_gh_without_token_auth_unavailable(
    ctx_no_token: ToolContext,
) -> None:
    handlers = {
        "gh_auth_status": (gh_tools.gh_auth_status, {}),
        "gh_pr_list": (gh_tools.gh_pr_list, {}),
        "gh_pr_create": (gh_tools.gh_pr_create, {"title": "t"}),
        "gh_pr_view": (gh_tools.gh_pr_view, {"number": 1}),
        "gh_issue_list": (gh_tools.gh_issue_list, {}),
        "gh_issue_create": (gh_tools.gh_issue_create, {"title": "t"}),
        "gh_api": (gh_tools.gh_api, {"endpoint": "user"}),
        "gh_project_list": (gh_tools.gh_project_list, {}),
        "gh_project_item_list": (gh_tools.gh_project_item_list, {"number": 1}),
        "gh_project_item_add": (
            gh_tools.gh_project_item_add,
            {"number": 1, "url": "https://github.com/o/r/issues/1"},
        ),
        "gh_project_item_edit": (
            gh_tools.gh_project_item_edit,
            {"id": "PVTI_x"},
        ),
        "gh_project_field_list": (
            gh_tools.gh_project_field_list,
            {"number": 1},
        ),
    }
    assert set(handlers) == set(FROZEN_GH_TOOLS)
    for name, (fn, args) in handlers.items():
        result = fn(args, ctx_no_token)
        assert result.ok is False, name
        assert result.error_reason == "auth_unavailable", name
        # Hint may name the env var; never a live token value.
        assert "ghs_" not in str(result.payload).lower()


def test_gh_with_token_subprocess_env_has_token_not_in_result(
    ctx_with_token: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(argv, *, cwd=None, timeout=60.0, env=None):
        captured["argv"] = list(argv)
        captured["env"] = dict(env or {})
        return _ok_proc("logged in as test\n")

    monkeypatch.setattr(gh_tools, "run_gh", fake_run)
    result = gh_tools.gh_auth_status({}, ctx_with_token)
    assert result.ok
    assert captured["env"].get("GH_TOKEN") == "ghs_test_token_value"
    # ToolResult must not echo the token.
    blob = str(result.payload)
    assert "ghs_test_token_value" not in blob
    assert captured["argv"] == ["gh", "auth", "status"]


def test_gh_pr_list_argv(
    ctx_with_token: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_run(argv, *, cwd=None, timeout=60.0, env=None):
        captured.extend(argv)
        return _ok_proc("[]")

    monkeypatch.setattr(gh_tools, "run_gh", fake_run)
    result = gh_tools.gh_pr_list(
        {"repo": "o/r", "state": "open", "limit": 5, "json": True},
        ctx_with_token,
    )
    assert result.ok
    assert captured[:3] == ["gh", "pr", "list"]
    assert "--repo" in captured and "o/r" in captured


def test_gh_project_list_argv(
    ctx_with_token: ToolContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    def fake_run(argv, *, cwd=None, timeout=60.0, env=None):
        captured.extend(argv)
        return _ok_proc("{}")

    monkeypatch.setattr(gh_tools, "run_gh", fake_run)
    result = gh_tools.gh_project_list(
        {"owner": "acme", "format_json": True},
        ctx_with_token,
    )
    assert result.ok
    assert captured[:3] == ["gh", "project", "list"]
    assert "--owner" in captured and "acme" in captured


def test_registry_dispatches_gh_without_token_tool_soft_fails(
    paths, registry: ToolRegistry
) -> None:
    """Registry still dispatches; tool returns auth_unavailable (not registry)."""
    store = SecretsStore(paths.data_dir)
    # No gh_token set.
    ctx = ToolContext(paths=paths, extras={"secrets": store})
    result = registry.execute("gh_auth_status", {}, ctx)
    assert result.ok is False
    assert result.error_reason == "auth_unavailable"


def test_registry_injects_gh_token_for_granted_tool(
    paths, registry: ToolRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SecretsStore(paths.data_dir)
    store.set_secret(
        "gh_token",
        "ghs_injected_xyz",
        grants=["gh_pr_list"],
    )
    captured: dict[str, Any] = {}

    def fake_run(argv, *, cwd=None, timeout=60.0, env=None):
        captured["env"] = dict(env or {})
        captured["argv"] = list(argv)
        return _ok_proc("[]")

    monkeypatch.setattr(gh_tools, "run_gh", fake_run)
    ctx = ToolContext(paths=paths, extras={"secrets": store})
    result = registry.execute("gh_pr_list", {"limit": 1}, ctx)
    assert result.ok
    assert captured["env"].get("GH_TOKEN") == "ghs_injected_xyz"
    assert "ghs_injected_xyz" not in str(result.payload)
