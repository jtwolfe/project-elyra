"""Tests for tool registry, schema load, runner dispatch, and policy."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.sandbox import Sandbox
from elyra.tools import (
    BundledToolsRootError,
    ToolContext,
    ToolRegistry,
    ToolResult,
    normalize_tool_name,
    resolve_bundled_tools_root,
)
from elyra.tools.policy import is_valid_tool_name
from elyra.tools.registry import SOURCE_BUNDLED, SOURCE_LOCAL, drafts_dir
from elyra.tools.runner import load_runner_json, resolve_builtin_handler
from elyra.tools.schema import load_tool_meta, to_openai_tool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bundled_root() -> Path:
    """Repo sample packages (tools/bundled)."""
    return resolve_bundled_tools_root()


@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Isolated ELYRA_HOME with local/drafts dirs."""
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def registry(home: Path, bundled_root: Path) -> ToolRegistry:
    paths = resolve_paths(home)
    return ToolRegistry(paths, bundled_root=bundled_root)


def _write_package(
    root: Path,
    name: str,
    *,
    description: str = "test tool",
    kind: str = "read",
    entry: str = "elyra.tools.builtin.files:read_file",
    runner_kind: str = "builtin",
    extra_schema: dict | None = None,
) -> Path:
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "TOOL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nkind: {kind}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
        },
        "required": ["path"],
    }
    if extra_schema:
        schema.update(extra_schema)
    (pkg / "schema.json").write_text(
        json.dumps(schema),
        encoding="utf-8",
    )
    runner: dict = {"kind": runner_kind}
    if runner_kind == "builtin":
        runner["entry"] = entry
    (pkg / "runner.json").write_text(json.dumps(runner), encoding="utf-8")
    return pkg


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_resolve_bundled_tools_root_exists() -> None:
    root = resolve_bundled_tools_root()
    assert root.is_dir()
    assert (root / "read_file").is_dir()
    assert (root / "read_file" / "schema.json").is_file()
    assert (root / "read_file" / "runner.json").is_file()


def test_discover_bundled_sample(registry: ToolRegistry) -> None:
    assert registry.has("read_file")
    pkg = registry.get("read_file")
    assert pkg is not None
    assert pkg.source == SOURCE_BUNDLED
    assert pkg.meta.name == "read_file"
    assert pkg.meta.kind == "read"
    assert "path" in pkg.meta.parameters.get("properties", {})
    assert pkg.runner.kind == "builtin"
    assert pkg.handler is not None
    assert "read_file" in registry.names()


def test_drafts_not_callable(home: Path, bundled_root: Path) -> None:
    paths = resolve_paths(home)
    draft_root = drafts_dir(paths)
    draft_root.mkdir(parents=True, exist_ok=True)
    _write_package(draft_root, "sneaky_draft", description="must not be callable")

    reg = ToolRegistry(paths, bundled_root=bundled_root)
    assert not reg.has("sneaky_draft")
    result = reg.execute(
        "sneaky_draft",
        {"path": "x"},
        ToolContext(paths=paths),
    )
    assert result.ok is False
    assert result.error_reason == "unknown_tool"


def test_drafts_dir_not_scanned_even_if_passed_as_local(
    home: Path, bundled_root: Path
) -> None:
    """Defense in depth: a root named drafts is refused as local_root."""
    paths = resolve_paths(home)
    drafts = paths.tools_dir / "drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    _write_package(drafts, "evil")

    with pytest.raises(ValueError, match="drafts"):
        ToolRegistry(paths, bundled_root=bundled_root, local_root=drafts)


def test_local_overrides_bundled(
    home: Path, bundled_root: Path, caplog: pytest.LogCaptureFixture
) -> None:
    paths = resolve_paths(home)
    local = paths.tools_dir / "local"
    local.mkdir(parents=True, exist_ok=True)
    # Local read_file with same name — points at same handler but distinct package dir
    _write_package(
        local,
        "read_file",
        description="local override of read_file",
    )

    with caplog.at_level(logging.INFO, logger="elyra.tools.registry"):
        reg = ToolRegistry(paths, bundled_root=bundled_root)
        # reload again should not re-log (log once)
        reg.reload()

    pkg = reg.get("read_file")
    assert pkg is not None
    assert pkg.source == SOURCE_LOCAL
    assert "local override" in pkg.meta.description
    assert any("overrides bundled" in r.message for r in caplog.records)
    # Second reload: still only one log line for this name
    override_msgs = [r for r in caplog.records if "overrides bundled" in r.message]
    assert len(override_msgs) == 1


def test_missing_bundled_root_raises(home: Path, tmp_path: Path) -> None:
    paths = resolve_paths(home)
    missing = tmp_path / "no_such_bundled"
    with pytest.raises(BundledToolsRootError, match="bundled_tools_root|BUNDLED"):
        ToolRegistry(paths, bundled_root=missing)


def test_missing_default_bundled_root_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """When project_root has no tools/bundled, resolve fails with editable hint."""
    from elyra.tools import policy as policy_mod

    fake_root = Path("/tmp/elyra-not-a-real-project-root-xyz")
    monkeypatch.setattr(policy_mod, "project_root", lambda: fake_root)
    with pytest.raises(BundledToolsRootError, match="editable"):
        resolve_bundled_tools_root()


# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------


def test_execute_returns_tool_result(registry: ToolRegistry, home: Path) -> None:
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths)
    result = registry.execute("read_file", {"path": "notes.txt"}, ctx)
    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert result.payload["path"] == "notes.txt"
    assert result.payload.get("test_double") is True
    assert result.ends_moment is False
    assert result.counts_as_speak is False


def test_execute_with_sandbox(registry: ToolRegistry, home: Path) -> None:
    paths = resolve_paths(home)
    paths.ensure_data_dirs()
    sandbox = Sandbox(paths)
    sandbox.write_text("hello.txt", "world\n")
    ctx = ToolContext(paths=paths, sandbox=sandbox)
    result = registry.execute("read_file", {"path": "hello.txt"}, ctx)
    assert result.ok is True
    assert result.payload["content"] == "world\n"
    assert "test_double" not in result.payload


def test_invalid_name_error_result_not_exception(
    registry: ToolRegistry, home: Path
) -> None:
    paths = resolve_paths(home)
    ctx = ToolContext(paths=paths)
    result = registry.execute("does_not_exist", {"path": "x"}, ctx)
    assert result.ok is False
    assert result.error_reason == "unknown_tool"
    # empty / whitespace
    result2 = registry.execute("", {}, ctx)
    assert result2.ok is False
    assert result2.error_reason == "invalid_name"


def test_execute_missing_path_arg(registry: ToolRegistry, home: Path) -> None:
    paths = resolve_paths(home)
    result = registry.execute("read_file", {}, ToolContext(paths=paths))
    assert result.ok is False
    assert result.error_reason == "missing_path"


def test_name_isolation_casefold(home: Path, bundled_root: Path) -> None:
    """Case-normalized lookup: Read_File hits read_file package."""
    paths = resolve_paths(home)
    reg = ToolRegistry(paths, bundled_root=bundled_root)
    assert reg.has("READ_FILE")
    assert reg.get("Read_File") is not None
    result = reg.execute(
        "READ_FILE",
        {"path": "a"},
        ToolContext(paths=paths),
    )
    assert result.ok is True


# ---------------------------------------------------------------------------
# openai_tools / schema
# ---------------------------------------------------------------------------


def test_openai_tools_shape(registry: ToolRegistry) -> None:
    tools = registry.openai_tools()
    assert isinstance(tools, list)
    assert len(tools) >= 1
    entry = next(t for t in tools if t["function"]["name"] == "read_file")
    assert entry["type"] == "function"
    fn = entry["function"]
    assert "description" in fn
    assert fn["parameters"]["type"] == "object"
    assert "path" in fn["parameters"]["properties"]


def test_load_tool_meta_sample(bundled_root: Path) -> None:
    meta = load_tool_meta(bundled_root / "read_file")
    assert meta.name == "read_file"
    assert meta.kind == "read"
    assert "sandbox" in meta.description.lower() or "file" in meta.description.lower()
    oai = to_openai_tool(meta)
    assert oai["type"] == "function"
    assert oai["function"]["name"] == "read_file"


def test_runner_load_sample(bundled_root: Path) -> None:
    runner = load_runner_json(bundled_root / "read_file")
    assert runner.kind == "builtin"
    assert runner.entry == "elyra.tools.builtin.files:read_file"
    handler = resolve_builtin_handler(runner.entry)
    assert callable(handler)


# ---------------------------------------------------------------------------
# Policy helpers
# ---------------------------------------------------------------------------


def test_normalize_and_validate_names() -> None:
    assert normalize_tool_name("Read_File") == "read_file"
    assert is_valid_tool_name("read_file")
    assert is_valid_tool_name("my-tool")
    assert not is_valid_tool_name("")
    assert not is_valid_tool_name("../escape")
    assert not is_valid_tool_name("has space")


def test_ends_moment_stripped_for_non_control(
    home: Path, bundled_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registry strips ends_moment unless kind is control/speak."""
    paths = resolve_paths(home)
    reg = ToolRegistry(paths, bundled_root=bundled_root)
    pkg = reg.get("read_file")
    assert pkg is not None

    def _fake_handler(args: dict, ctx: ToolContext) -> ToolResult:
        return ToolResult(
            ok=True,
            payload={"forced": True},
            ends_moment=True,
            stop_reason="wait",
        )

    # Patch the cached handler on a reloaded registry by writing local package
    # that still has kind=read.
    from elyra.tools import runner as runner_mod

    monkeypatch.setattr(
        runner_mod,
        "dispatch",
        lambda runner, args, ctx, handler=None: _fake_handler(args, ctx),
    )
    result = reg.execute("read_file", {"path": "x"}, ToolContext(paths=paths))
    assert result.ok is True
    assert result.ends_moment is False
    assert result.stop_reason is None


def test_reload_picks_up_new_local(home: Path, bundled_root: Path) -> None:
    paths = resolve_paths(home)
    reg = ToolRegistry(paths, bundled_root=bundled_root)
    assert not reg.has("echo_tool")
    local = paths.tools_dir / "local"
    local.mkdir(parents=True, exist_ok=True)
    _write_package(local, "echo_tool", description="added later")
    reg.reload()
    assert reg.has("echo_tool")


def test_incomplete_package_skipped(home: Path, bundled_root: Path) -> None:
    paths = resolve_paths(home)
    local = paths.tools_dir / "local"
    local.mkdir(parents=True, exist_ok=True)
    bare = local / "incomplete"
    bare.mkdir()
    (bare / "TOOL.md").write_text("---\nname: incomplete\n---\n", encoding="utf-8")
    # no schema.json / runner.json
    reg = ToolRegistry(paths, bundled_root=bundled_root)
    assert not reg.has("incomplete")
