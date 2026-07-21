"""Tests for sandbox FS + run builtin tools (PR7)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from elyra.config import resolve_paths
from elyra.sandbox import Sandbox
from elyra.tools import ToolContext, ToolRegistry
from elyra.tools.builtin import files, run_cmd
from elyra.tools.policy import resolve_bundled_tools_root


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    paths = resolve_paths(tmp_path)
    paths.ensure_data_dirs()
    return tmp_path


@pytest.fixture
def sandbox(home: Path) -> Sandbox:
    return Sandbox(resolve_paths(home))


@pytest.fixture
def ctx(home: Path, sandbox: Sandbox) -> ToolContext:
    return ToolContext(paths=resolve_paths(home), sandbox=sandbox)


@pytest.fixture
def ctx_no_sandbox(home: Path) -> ToolContext:
    return ToolContext(paths=resolve_paths(home), sandbox=None)


@pytest.fixture
def registry(home: Path) -> ToolRegistry:
    return ToolRegistry(
        resolve_paths(home),
        bundled_root=resolve_bundled_tools_root(),
    )


# ---------------------------------------------------------------------------
# Discovery — all five bundled packages
# ---------------------------------------------------------------------------


def test_bundled_sandbox_tools_discovered(registry: ToolRegistry) -> None:
    for name in ("read_file", "list_dir", "grep", "search_replace", "run"):
        assert registry.has(name), name
        pkg = registry.get(name)
        assert pkg is not None
        assert pkg.handler is not None, name
        assert pkg.runner.kind == "builtin"
    names = registry.names()
    assert names == sorted(names)
    assert set(names) >= {
        "read_file",
        "list_dir",
        "grep",
        "search_replace",
        "run",
    }


def test_openai_tools_include_sandbox_group(registry: ToolRegistry) -> None:
    tools = {t["function"]["name"]: t for t in registry.openai_tools()}
    assert "path" in tools["read_file"]["function"]["parameters"]["properties"]
    assert "pattern" in tools["grep"]["function"]["parameters"]["properties"]
    assert "command" in tools["run"]["function"]["parameters"]["properties"]
    assert tools["search_replace"]["function"]["parameters"]["required"] == [
        "path",
        "old",
        "new",
    ]


# ---------------------------------------------------------------------------
# no_sandbox fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler,args",
    [
        (files.read_file, {"path": "a.txt"}),
        (files.list_dir, {"path": "."}),
        (files.grep, {"pattern": "x"}),
        (files.search_replace, {"path": "a.txt", "old": "a", "new": "b"}),
        (run_cmd.run, {"command": ["echo", "hi"]}),
    ],
)
def test_no_sandbox_error(handler, args, ctx_no_sandbox: ToolContext) -> None:
    result = handler(args, ctx_no_sandbox)
    assert result.ok is False
    assert result.error_reason == "no_sandbox"


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


def test_read_file_happy(ctx: ToolContext, sandbox: Sandbox) -> None:
    sandbox.write_text("notes/hello.txt", "hello world\n")
    result = files.read_file({"path": "notes/hello.txt"}, ctx)
    assert result.ok is True
    assert result.payload["content"] == "hello world\n"
    assert result.payload["path"] == "notes/hello.txt"
    assert result.ends_moment is False
    assert result.counts_as_speak is False


def test_read_file_missing_path(ctx: ToolContext) -> None:
    assert files.read_file({}, ctx).error_reason == "missing_path"
    assert files.read_file({"path": ""}, ctx).error_reason == "missing_path"
    assert files.read_file({"path": "   "}, ctx).error_reason == "missing_path"
    assert files.read_file({"path": 12}, ctx).error_reason == "missing_path"


def test_read_file_not_found(ctx: ToolContext) -> None:
    result = files.read_file({"path": "nope.txt"}, ctx)
    assert result.ok is False
    assert result.error_reason == "not_found"


def test_read_file_path_escape(ctx: ToolContext) -> None:
    result = files.read_file({"path": "../secret.txt"}, ctx)
    assert result.ok is False
    assert result.error_reason == "path_escape"


def test_read_file_directory_is_directory(
    ctx: ToolContext, sandbox: Sandbox
) -> None:
    sandbox.write_text("sub/inside.txt", "x")
    result = files.read_file({"path": "sub"}, ctx)
    assert result.ok is False
    assert result.error_reason == "is_directory"


def test_read_file_decode_error(ctx: ToolContext, sandbox: Sandbox) -> None:
    """Non-UTF-8 binary content → decode_error (not invalid_path)."""
    bin_path = sandbox.root / "binary.bin"
    bin_path.write_bytes(b"\xff\xfe\x00\x01\x80")
    result = files.read_file({"path": "binary.bin"}, ctx)
    assert result.ok is False
    assert result.error_reason == "decode_error"


def test_read_file_via_registry(
    registry: ToolRegistry, home: Path, sandbox: Sandbox
) -> None:
    sandbox.write_text("via.txt", "reg\n")
    ctx = ToolContext(paths=resolve_paths(home), sandbox=sandbox)
    result = registry.execute("read_file", {"path": "via.txt"}, ctx)
    assert result.ok is True
    assert result.payload["content"] == "reg\n"
    assert "test_double" not in result.payload


# ---------------------------------------------------------------------------
# list_dir
# ---------------------------------------------------------------------------


def test_list_dir_happy(ctx: ToolContext, sandbox: Sandbox) -> None:
    sandbox.write_text("a.txt", "1")
    sandbox.write_text("sub/b.txt", "2")
    result = files.list_dir({"path": "."}, ctx)
    assert result.ok is True
    assert "a.txt" in result.payload["entries"]
    assert "sub" in result.payload["entries"]
    sub = files.list_dir({"path": "sub"}, ctx)
    assert sub.payload["entries"] == ["b.txt"]


def test_list_dir_default_path(ctx: ToolContext, sandbox: Sandbox) -> None:
    sandbox.write_text("root.txt", "x")
    result = files.list_dir({}, ctx)
    assert result.ok is True
    assert "root.txt" in result.payload["entries"]
    assert result.payload["path"] == "."


def test_list_dir_not_a_directory(ctx: ToolContext, sandbox: Sandbox) -> None:
    sandbox.write_text("file.txt", "x")
    result = files.list_dir({"path": "file.txt"}, ctx)
    assert result.ok is False
    assert result.error_reason == "not_a_directory"


def test_list_dir_missing_not_found(ctx: ToolContext) -> None:
    """Missing path is not_found (not collapsed into not_a_directory)."""
    result = files.list_dir({"path": "does_not_exist"}, ctx)
    assert result.ok is False
    assert result.error_reason == "not_found"


def test_list_dir_path_escape(ctx: ToolContext) -> None:
    result = files.list_dir({"path": "../"}, ctx)
    assert result.ok is False
    assert result.error_reason == "path_escape"


def test_list_dir_via_registry(
    registry: ToolRegistry, home: Path, sandbox: Sandbox
) -> None:
    sandbox.write_text("listed.txt", "y")
    ctx = ToolContext(paths=resolve_paths(home), sandbox=sandbox)
    result = registry.execute("list_dir", {"path": "."}, ctx)
    assert result.ok is True
    assert "listed.txt" in result.payload["entries"]


# ---------------------------------------------------------------------------
# grep
# ---------------------------------------------------------------------------


def test_grep_substring(ctx: ToolContext, sandbox: Sandbox) -> None:
    sandbox.write_text("a.txt", "alpha\nbeta\n")
    sandbox.write_text("b/c.txt", "alphabet\n")
    result = files.grep({"pattern": "alpha"}, ctx)
    assert result.ok is True
    paths = {m["path"] for m in result.payload["matches"]}
    assert "a.txt" in paths
    assert "b/c.txt" in paths
    assert result.payload["truncated"] is False


def test_grep_regex(ctx: ToolContext, sandbox: Sandbox) -> None:
    sandbox.write_text("n.txt", "n1\nn22\n")
    result = files.grep({"pattern": r"n\d{2}", "regex": True}, ctx)
    assert result.ok is True
    assert len(result.payload["matches"]) == 1
    assert result.payload["matches"][0]["line"] == 2


def test_grep_max_matches_truncated(ctx: ToolContext, sandbox: Sandbox) -> None:
    sandbox.write_text("m.txt", "x\nx\nx\nx\n")
    result = files.grep({"pattern": "x", "max_matches": 2}, ctx)
    assert result.ok is True
    assert len(result.payload["matches"]) == 2
    assert result.payload["truncated"] is True


def test_grep_missing_pattern(ctx: ToolContext) -> None:
    assert files.grep({}, ctx).error_reason == "missing_pattern"
    assert files.grep({"pattern": ""}, ctx).error_reason == "missing_pattern"


def test_grep_path_escape(ctx: ToolContext) -> None:
    result = files.grep({"pattern": "x", "path": "../outside"}, ctx)
    assert result.ok is False
    assert result.error_reason == "path_escape"


def test_grep_via_registry(
    registry: ToolRegistry, home: Path, sandbox: Sandbox
) -> None:
    sandbox.write_text("g.txt", "needle here\n")
    ctx = ToolContext(paths=resolve_paths(home), sandbox=sandbox)
    result = registry.execute("grep", {"pattern": "needle"}, ctx)
    assert result.ok is True
    assert any("needle" in m["text"] for m in result.payload["matches"])


# ---------------------------------------------------------------------------
# search_replace
# ---------------------------------------------------------------------------


def test_search_replace_happy(ctx: ToolContext, sandbox: Sandbox) -> None:
    sandbox.write_text("edit.txt", "foo bar foo\n")
    result = files.search_replace(
        {"path": "edit.txt", "old": "foo", "new": "baz"},
        ctx,
    )
    assert result.ok is True
    assert result.payload["replacements"] == 2
    assert sandbox.read_text("edit.txt") == "baz bar baz\n"


def test_search_replace_count(ctx: ToolContext, sandbox: Sandbox) -> None:
    sandbox.write_text("edit.txt", "aa aa aa\n")
    result = files.search_replace(
        {"path": "edit.txt", "old": "aa", "new": "b", "count": 1},
        ctx,
    )
    assert result.ok is True
    assert result.payload["replacements"] == 1
    assert sandbox.read_text("edit.txt") == "b aa aa\n"


def test_search_replace_empty_old(ctx: ToolContext, sandbox: Sandbox) -> None:
    sandbox.write_text("e.txt", "x")
    result = files.search_replace(
        {"path": "e.txt", "old": "", "new": "y"},
        ctx,
    )
    assert result.ok is False
    assert result.error_reason == "empty_old"


def test_search_replace_missing_args(ctx: ToolContext) -> None:
    assert files.search_replace({}, ctx).error_reason == "missing_path"
    assert (
        files.search_replace({"path": "a", "new": "b"}, ctx).error_reason
        == "missing_old"
    )
    assert (
        files.search_replace({"path": "a", "old": "b"}, ctx).error_reason
        == "missing_new"
    )


def test_search_replace_path_escape(ctx: ToolContext) -> None:
    result = files.search_replace(
        {"path": "../x", "old": "a", "new": "b"},
        ctx,
    )
    assert result.ok is False
    assert result.error_reason == "path_escape"


def test_search_replace_directory_is_directory(
    ctx: ToolContext, sandbox: Sandbox
) -> None:
    sandbox.write_text("d/f.txt", "x")
    result = files.search_replace(
        {"path": "d", "old": "a", "new": "b"},
        ctx,
    )
    assert result.ok is False
    assert result.error_reason == "is_directory"


def test_search_replace_via_registry(
    registry: ToolRegistry, home: Path, sandbox: Sandbox
) -> None:
    sandbox.write_text("sr.txt", "one two one\n")
    ctx = ToolContext(paths=resolve_paths(home), sandbox=sandbox)
    result = registry.execute(
        "search_replace",
        {"path": "sr.txt", "old": "one", "new": "1"},
        ctx,
    )
    assert result.ok is True
    assert result.payload["replacements"] == 2
    assert sandbox.read_text("sr.txt") == "1 two 1\n"


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def test_run_argv_list(ctx: ToolContext) -> None:
    result = run_cmd.run(
        {"command": [sys.executable, "-c", "print('hi')"]},
        ctx,
    )
    assert result.ok is True
    assert result.payload["returncode"] == 0
    assert "hi" in result.payload["stdout"]
    assert result.payload["timed_out"] is False
    assert result.payload["argv"][0] == sys.executable


def test_run_string_shlex_not_shell(ctx: ToolContext, sandbox: Sandbox) -> None:
    """shell=False: ';' must not inject a second command."""
    marker = sandbox.root / "injected.flag"
    result = run_cmd.run({"command": f"echo safe; touch {marker.name}"}, ctx)
    assert result.ok is True
    assert not marker.exists()
    assert result.payload["argv"][0] == "echo"


def test_run_nonzero_exit_still_ok(ctx: ToolContext) -> None:
    """Non-zero exit is payload data, not infrastructure failure."""
    result = run_cmd.run(
        {"command": [sys.executable, "-c", "import sys; sys.exit(3)"]},
        ctx,
    )
    assert result.ok is True
    assert result.payload["returncode"] == 3


def test_run_timeout(ctx: ToolContext) -> None:
    result = run_cmd.run(
        {
            "command": [sys.executable, "-c", "import time; time.sleep(30)"],
            "timeout": 0.3,
        },
        ctx,
    )
    assert result.ok is True
    assert result.payload["timed_out"] is True
    assert result.payload["returncode"] != 0


def test_run_missing_command(ctx: ToolContext) -> None:
    assert run_cmd.run({}, ctx).error_reason == "missing_command"
    assert run_cmd.run({"command": ""}, ctx).error_reason == "empty_command"
    assert run_cmd.run({"command": []}, ctx).error_reason == "empty_command"
    assert run_cmd.run({"command": 1}, ctx).error_reason == "invalid_command"
    assert (
        run_cmd.run({"command": [1, 2]}, ctx).error_reason == "invalid_command"
    )


def test_run_invalid_timeout(ctx: ToolContext) -> None:
    assert (
        run_cmd.run(
            {"command": ["echo", "x"], "timeout": 0},
            ctx,
        ).error_reason
        == "invalid_timeout"
    )
    assert (
        run_cmd.run(
            {"command": ["echo", "x"], "timeout": -1},
            ctx,
        ).error_reason
        == "invalid_timeout"
    )
    assert (
        run_cmd.run(
            {"command": ["echo", "x"], "timeout": "fast"},
            ctx,
        ).error_reason
        == "invalid_timeout"
    )


def test_run_cwd_is_sandbox(ctx: ToolContext, sandbox: Sandbox) -> None:
    sandbox.write_text("here.txt", "yes\n")
    result = run_cmd.run(
        {"command": [sys.executable, "-c", "print(open('here.txt').read())"]},
        ctx,
    )
    assert result.ok is True
    assert "yes" in result.payload["stdout"]


def test_run_via_registry(
    registry: ToolRegistry, home: Path, sandbox: Sandbox
) -> None:
    ctx = ToolContext(paths=resolve_paths(home), sandbox=sandbox)
    result = registry.execute(
        "run",
        {"command": [sys.executable, "-c", "print(42)"]},
        ctx,
    )
    assert result.ok is True
    assert "42" in result.payload["stdout"]


def test_run_control_flags_stripped(
    registry: ToolRegistry, home: Path, sandbox: Sandbox
) -> None:
    """kind=mutate run must never end moment / count as speak."""
    ctx = ToolContext(paths=resolve_paths(home), sandbox=sandbox)
    result = registry.execute(
        "run",
        {"command": [sys.executable, "-c", "print(1)"]},
        ctx,
    )
    assert result.ends_moment is False
    assert result.counts_as_speak is False
    assert result.stop_reason is None


# ---------------------------------------------------------------------------
# Symlink escape via tool layer
# ---------------------------------------------------------------------------


def test_read_file_symlink_escape_denied(ctx: ToolContext, sandbox: Sandbox) -> None:
    outside = sandbox.root.parent / "host_secret.txt"
    outside.write_text("SECRET\n", encoding="utf-8")
    link = sandbox.root / "leak"
    link.symlink_to(outside)
    result = files.read_file({"path": "leak"}, ctx)
    assert result.ok is False
    assert result.error_reason == "path_escape"
